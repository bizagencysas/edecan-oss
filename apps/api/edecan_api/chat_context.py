"""Context packing for long and cross-chat conversations.

This module keeps the LLM-facing context useful without pretending the model
has infinite context. It builds one compact system message with:

- older turns from the same conversation when the chat grows beyond the recent
  tail sent verbatim — the older part is condensed into a summary (with the
  cheap LLM alias when available, otherwise a deterministic first-sentences
  fallback) instead of shipping raw messages;
- small snippets from other chats owned by the same user.

Secrets are redacted before reaching the model. Tool logs and reasoning never
enter this pack.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from edecan_core.safety import redact
from edecan_llm.base import ChatMessage, CompletionRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatContextLimits:
    enabled: bool
    recent_messages: int
    max_messages: int
    max_chars: int
    cross_chat_enabled: bool
    cross_chat_conversations: int
    cross_chat_messages_per_conversation: int
    cross_chat_max_chars: int


# ---------------------------------------------------------------------------
# Compactación del hilo (`compactar_historial`): el contrato y sus constantes.
# ---------------------------------------------------------------------------

_ALIAS_RAPIDO = "rapido"
_TOPE_CARACTERES_DEFECTO = 8_000
_MAX_CHARS_RESUMEN = 4_000
_MAX_CHARS_TEXTO_MENSAJE = 2_000
_MAX_CHARS_LINEA_RESUMEN = 800
_MAX_FRASES_POR_MENSAJE = 2
_MENSAJE_CORTO_MAX_CHARS = 160
_RESUMEN_LLM_MAX_TOKENS = 700
_RESUMEN_LLM_TIMEOUT_SEGUNDOS = 12.0
_CACHE_RESUMEN_MAX_ENTRADAS = 128
_ROLES_VALIDOS = {"system", "user", "assistant", "tool"}
_LABELS_ROLES = {
    "user": "Usuario",
    "assistant": "Edecán",
    "system": "Sistema",
    "tool": "Herramienta",
}
_PUNTOS_DE_CORTE = re.compile(r"(?<=[.!?])\s+")
# Caché por contenido (digest del transcripto viejo → resumen ya generado): una
# sola llamada al modelo barato por compactación; si el hilo no cambió entre
# turnos, no se vuelve a pagar. Solo se cachean resúmenes LLM EXITOSOS — el
# recorte determinista es barato y no hay por qué congelarlo.
_cache_resumen: OrderedDict[str, str] = OrderedDict()


async def compactar_historial(
    mensajes: list[ChatMessage],
    tope_caracteres: int,
    *,
    llm_router: Any | None = None,
) -> tuple[str, list[ChatMessage]]:
    """Comprime un hilo largo para que el turno cueste menos tokens.

    Devuelve ``(resumen, recientes)``:

    - ``recientes``: los ÚLTIMOS mensajes cuya longitud total cabe en
      ``tope_caracteres`` — el final del hilo viaja textual, sin tocarlo.
      El último mensaje se conserva SIEMPRE, aunque él solo exceda el tope.
    - ``resumen``: condensación de todo lo anterior (con nombres de roles y
      el tema/decisiones/pendientes). Se genera con el alias barato
      ``"rapido"`` si hay ``llm_router`` (p. ej. ``deps.get_llm_router``), con
      caché por contenido; si no hay router o la llamada falla, degrada a un
      recorte determinista (primeras frases) — nunca lanza.
    """
    normalizados = [_a_chat_message(m) for m in mensajes]
    mensajes = [m for m in normalizados if m is not None]
    if not mensajes:
        return "", []
    try:
        tope = max(1, int(tope_caracteres))
    except (TypeError, ValueError):
        tope = _TOPE_CARACTERES_DEFECTO
    recientes = _ultimos_dentro_del_tope(mensajes, tope)
    viejos = mensajes[: len(mensajes) - len(recientes)]
    if not viejos:
        return "", list(mensajes)
    resumen = ""
    if llm_router is not None:
        resumen = await _resumen_con_llm(viejos, llm_router)
    if not resumen:
        resumen = _resumen_determinista(viejos, _MAX_CHARS_RESUMEN)
    return resumen, recientes


def _a_chat_message(item: Any) -> ChatMessage | None:
    """Tolera ``ChatMessage`` o filas ``{role, content}`` del repo."""
    if isinstance(item, ChatMessage):
        return item
    if isinstance(item, dict):
        role = item.get("role")
        if role not in _ROLES_VALIDOS:
            return None
        try:
            return ChatMessage(role=role, content=item.get("content") or "")
        except Exception:
            return None
    return None


def _label_rol(role: str) -> str:
    return _LABELS_ROLES.get(str(role), str(role or "mensaje"))


def _ultimos_dentro_del_tope(mensajes: list[ChatMessage], tope: int) -> list[ChatMessage]:
    if not mensajes:
        return []
    largos = [
        len(extract_message_text(m.content, max_chars=_MAX_CHARS_TEXTO_MENSAJE)) for m in mensajes
    ]
    inicio = len(largos) - 1
    total = largos[inicio]
    while inicio > 0 and total + largos[inicio - 1] <= tope:
        total += largos[inicio - 1]
        inicio -= 1
    return mensajes[inicio:]


def _frases_clave(texto: str, max_frases: int) -> str:
    """Primeras frases + la última: el inicio trae el contexto y el final
    suele traer el pedido concreto o la conclusión — lo que más importa
    conservar de un mensaje largo."""
    frases = [parte.strip() for parte in _PUNTOS_DE_CORTE.split(texto) if parte.strip()]
    if len(frases) <= max_frases:
        return texto
    clave = frases[:max_frases]
    ultima = frases[-1]
    if ultima not in clave:
        clave.append(ultima)
    return " ".join(clave) + "…"


def _resumen_determinista(mensajes: list[ChatMessage], max_chars: int) -> str:
    """Recorte determinista "inteligente": por mensaje largo, sus frases clave
    (inicio + final) con el rol; los mensajes cortos van enteros — ahí suelen
    vivir las decisiones y los pendientes explícitos ("ok", "quedó pendiente…")."""
    lineas: list[str] = []
    restante = max(1, int(max_chars))
    for msg in mensajes:
        texto = extract_message_text(msg.content, max_chars=_MAX_CHARS_TEXTO_MENSAJE)
        if not texto:
            continue
        if len(texto) > _MENSAJE_CORTO_MAX_CHARS:
            texto = _frases_clave(texto, _MAX_FRASES_POR_MENSAJE)
        if len(texto) > _MAX_CHARS_LINEA_RESUMEN:
            texto = texto[:_MAX_CHARS_LINEA_RESUMEN].rstrip() + "…"
        linea = f"{_label_rol(msg.role)}: {texto}"
        if lineas and len(linea) + 1 > restante:
            break
        if len(linea) + 1 > restante:
            linea = linea[: max(1, restante - 1)] + "…"
        lineas.append(linea)
        restante -= len(linea) + 1
    return "\n".join(lineas)


def _transcripto_para_llm(mensajes: list[ChatMessage]) -> str:
    lineas: list[str] = []
    for msg in mensajes:
        texto = extract_message_text(msg.content, max_chars=_MAX_CHARS_TEXTO_MENSAJE)
        if texto:
            lineas.append(f"{_label_rol(msg.role)}: {texto}")
    return "\n".join(lineas)


def _llave_resumen(mensajes: list[ChatMessage]) -> str:
    payload = "\x1f".join(
        f"{m.role}\x1e{extract_message_text(m.content, max_chars=_MAX_CHARS_TEXTO_MENSAJE)}"
        for m in mensajes
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_SYSTEM_RESUMEN = (
    "Eres el compresor de historial de Edecán. Recibes la parte vieja de una "
    "conversación entre un usuario y su asistente. Devuelve ÚNICAMENTE un "
    "resumen en español que conserve: el tema y el objetivo principal, las "
    "decisiones tomadas, los pedidos concretos del usuario y si se cumplieron, "
    "lo que quedó pendiente, y datos específicos importantes (nombres, fechas, "
    "cifras, referencias). No inventes nada que no esté en el texto. No "
    "saludes ni agregues comentarios: solo el resumen."
)


async def _resumen_con_llm(viejos: list[ChatMessage], llm_router: Any) -> str:
    llave = _llave_resumen(viejos)
    cacheado = _cache_resumen.get(llave)
    if cacheado is not None:
        return cacheado
    transcripto = redact(_transcripto_para_llm(viejos))
    try:
        respuesta = await asyncio.wait_for(
            llm_router.complete(
                _ALIAS_RAPIDO,
                {},
                CompletionRequest(
                    model=_ALIAS_RAPIDO,
                    system=_SYSTEM_RESUMEN,
                    messages=[ChatMessage(role="user", content=transcripto)],
                    max_tokens=_RESUMEN_LLM_MAX_TOKENS,
                    temperature=0.2,
                ),
            ),
            timeout=_RESUMEN_LLM_TIMEOUT_SEGUNDOS,
        )
        texto = redact(str(getattr(respuesta, "text", "") or "")).strip()
    except Exception:
        logger.warning(
            "compactar_historial: falló el resumen con el modelo barato; "
            "se usa el recorte determinista.",
            exc_info=True,
        )
        return ""
    if not texto:
        return ""
    if len(texto) > _MAX_CHARS_RESUMEN:
        texto = texto[: _MAX_CHARS_RESUMEN - 1].rstrip() + "…"
    _cache_resumen[llave] = texto
    if len(_cache_resumen) > _CACHE_RESUMEN_MAX_ENTRADAS:
        _cache_resumen.popitem(last=False)
    return texto


async def resumen_llm_hilo_anterior(
    current_rows: list[dict[str, Any]],
    limits: ChatContextLimits,
    *,
    llm_router: Any | None = None,
) -> str:
    """Resumen LLM (alias ``"rapido"``) de la parte vieja que
    ``build_contextual_history`` condensa como "[Resumen del hilo anterior]".

    Es el brazo async que el empaquetado sync no puede correr por sí mismo: los
    call sites lo esperan ANTES de armar el contexto y le pasan el resultado
    como ``current_summary``. Devuelve ``""`` (para que el empaquetado degrade
    a su recorte determinista) cuando: no hay router, el contexto está
    deshabilitado, no hay mensajes viejos, la parte vieja cabe en el
    presupuesto del resumen (historial corto → no se paga una llamada al
    modelo), o el modelo falla. Nunca lanza.
    """
    if llm_router is None or not limits.enabled:
        return ""
    bounded_rows = current_rows[-max(1, limits.max_messages) :]
    recent_count = max(1, min(limits.recent_messages, len(bounded_rows)))
    older_rows = bounded_rows[:-recent_count]
    if not older_rows:
        return ""
    mensajes = _rows_to_messages(older_rows)
    if not mensajes:
        return ""
    # La regla de compactación es la de `compactar_historial`: solo se resume si
    # el hilo viejo NO cabe en el tope. Acá el tope es el presupuesto del
    # resumen (lo mismo que el recorte determinista puede conservar), así que el
    # modelo barato solo paga cuando el fallback perdería contenido.
    tope = min(max(1, limits.max_chars), _MAX_CHARS_RESUMEN)
    if len(_ultimos_dentro_del_tope(mensajes, tope)) == len(mensajes):
        return ""
    return await _resumen_con_llm(mensajes, llm_router)


def build_contextual_history(
    *,
    current_rows: list[dict[str, Any]],
    cross_chat_rows: list[dict[str, Any]],
    limits: ChatContextLimits,
    current_summary: str | None = None,
) -> list[ChatMessage]:
    """``current_summary`` es opcional: el resumen LLM ya precalculado de la
    parte vieja del hilo (ver ``resumen_llm_hilo_anterior``). Si no se pasa o
    viene vacío, se usa el recorte determinista de siempre — el contrato sync
    no cambia para el resto de los llamadores."""
    if not limits.enabled:
        return _rows_to_messages(current_rows[-limits.recent_messages :])

    bounded_rows = current_rows[-max(1, limits.max_messages) :]
    recent_count = max(1, min(limits.recent_messages, len(bounded_rows)))
    older_rows = bounded_rows[:-recent_count]
    recent_rows = bounded_rows[-recent_count:]

    context_sections: list[str] = []
    if older_rows:
        resumen = (current_summary or "").strip()
        if not resumen:
            resumen = _resumen_determinista(
                _rows_to_messages(older_rows), min(limits.max_chars, _MAX_CHARS_RESUMEN)
            )
        if resumen:
            context_sections.append(
                "[Resumen del hilo anterior] " + resumen
                + "\nÚsalo como continuidad, pero responde al mensaje nuevo."
            )

    if limits.cross_chat_enabled and cross_chat_rows:
        cross_summary = _format_cross_chat_summary(cross_chat_rows, limits)
        if cross_summary:
            context_sections.append(
                "Contexto de otros chats del mismo usuario. Úsalo solo si es "
                "relevante y no lo cites como fuente externa:\n" + cross_summary
            )

    history = _rows_to_messages(recent_rows)
    if not context_sections:
        return history
    return [
        ChatMessage(role="system", content=redact("\n\n".join(context_sections))),
        *history,
    ]


def extract_message_text(content: Any, *, max_chars: int = 2_000) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, dict):
        if isinstance(content.get("text"), str):
            text = content["text"]
        elif isinstance(content.get("blocks"), list):
            text = " ".join(_block_text(block) for block in content["blocks"])
        else:
            text = json.dumps(content, ensure_ascii=False, default=str)
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(_block_text(item))
            else:
                parts.append(str(item))
        text = " ".join(part for part in parts if part)
    else:
        text = str(content or "")
    return redact(_compact_ws(text))[:max_chars]


def _rows_to_messages(rows: list[dict[str, Any]]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for row in rows:
        role = row.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            continue
        text = extract_message_text(row.get("content"), max_chars=8_000)
        if role == "assistant":
            # Un turno que terminó mostrando una tarjeta no deja texto, así que
            # sin esto la fila se descartaba entera y el modelo veía dos
            # mensajes del usuario seguidos: perdía QUÉ preguntó y QUÉ ya hizo.
            # Respondiendo "Auditoría de cuenta" a una tarjeta sobre la skill
            # «ads», salía a buscar una skill llamada "auditoria" y volvía a
            # preguntar. El resumen es corto a propósito — reconstruir el
            # `tool_log` completo reventaría el contexto sin hacer falta.
            resumen = _resumen_de_tools(row.get("tool_calls"))
            if resumen:
                text = f"{text}\n\n{resumen}" if text else resumen
        if text:
            messages.append(ChatMessage(role=role, content=text))
    return messages


_MAX_RESUMEN_TOOLS = 8
_MAX_CHARS_POR_TOOL = 220


def _resumen_de_tools(tool_calls: Any) -> str:
    """Qué hizo el asistente en ese turno, en una línea por tool.

    Se queda con los `tool_end` (lo que DEVOLVIÓ cada tool, no lo que se pidió)
    porque ahí está el hecho que el modelo necesita recordar: que la skill quedó
    instalada, qué opciones ofreció la tarjeta, qué falló.
    """
    if not isinstance(tool_calls, list):
        return ""
    lineas: list[str] = []
    for evento in tool_calls:
        if not isinstance(evento, dict) or evento.get("type") != "tool_end":
            continue
        nombre = str(evento.get("name") or "").strip()
        if not nombre:
            continue
        detalle = " ".join(str(evento.get("result_preview") or "").split())
        if len(detalle) > _MAX_CHARS_POR_TOOL:
            detalle = detalle[:_MAX_CHARS_POR_TOOL].rstrip() + "…"
        lineas.append(f"- {nombre}: {detalle}" if detalle else f"- {nombre}")
    if not lineas:
        return ""
    if len(lineas) > _MAX_RESUMEN_TOOLS:
        lineas = lineas[-_MAX_RESUMEN_TOOLS:]
    return "[Lo que hiciste en ese turno]\n" + "\n".join(lineas)


def _format_cross_chat_summary(rows: list[dict[str, Any]], limits: ChatContextLimits) -> str:
    grouped: OrderedDict[UUID, dict[str, Any]] = OrderedDict()
    for row in rows:
        conversation_id = row.get("conversation_id")
        if conversation_id is None:
            continue
        if conversation_id not in grouped and len(grouped) >= limits.cross_chat_conversations:
            continue
        group = grouped.setdefault(
            conversation_id,
            {
                "title": row.get("conversation_title") or "Conversación anterior",
                "updated_at": row.get("conversation_updated_at"),
                "messages": [],
            },
        )
        if len(group["messages"]) < limits.cross_chat_messages_per_conversation:
            group["messages"].append(row)

    lines: list[str] = []
    remaining = max(1, limits.cross_chat_max_chars)
    for group in list(grouped.values())[: limits.cross_chat_conversations]:
        header = _format_conversation_header(group["title"], group.get("updated_at"))
        if len(header) + 1 > remaining:
            break
        lines.append(header)
        remaining -= len(header) + 1
        for row in group["messages"]:
            line = "  " + _format_message_line(row)
            if len(line) + 1 > remaining:
                return "\n".join(lines)
            lines.append(line)
            remaining -= len(line) + 1
    return "\n".join(lines)


def _format_conversation_header(title: Any, updated_at: Any) -> str:
    suffix = ""
    if isinstance(updated_at, datetime):
        suffix = f" · {updated_at.date().isoformat()}"
    return f"- {str(title).strip()[:90] or 'Conversación anterior'}{suffix}"


def _format_message_line(row: dict[str, Any]) -> str:
    role = str(row.get("role") or "mensaje")
    text = extract_message_text(row.get("content"), max_chars=1_200)
    if not text:
        return ""
    return f"{_label_rol(role)}: {text}"


def _block_text(block: dict[str, Any]) -> str:
    if isinstance(block.get("text"), str):
        return block["text"]
    if isinstance(block.get("title"), str) or isinstance(block.get("url"), str):
        return " ".join(str(block.get(key) or "") for key in ("title", "url"))
    if isinstance(block.get("alt"), str):
        return block["alt"]
    return ""


def _compact_ws(text: str) -> str:
    return " ".join(text.replace("\x00", " ").split())
