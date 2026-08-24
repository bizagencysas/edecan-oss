"""Context packing for long and cross-chat conversations.

This module keeps the LLM-facing context useful without pretending the model
has infinite context. It builds one compact system message with:

- older turns from the same conversation when the chat grows beyond the recent
  tail sent verbatim;
- small snippets from other chats owned by the same user.

Secrets are redacted before reaching the model. Tool logs and reasoning never
enter this pack.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from edecan_core.safety import redact
from edecan_llm.base import ChatMessage


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


def build_contextual_history(
    *,
    current_rows: list[dict[str, Any]],
    cross_chat_rows: list[dict[str, Any]],
    limits: ChatContextLimits,
) -> list[ChatMessage]:
    if not limits.enabled:
        return _rows_to_messages(current_rows[-limits.recent_messages :])

    bounded_rows = current_rows[-max(1, limits.max_messages) :]
    recent_count = max(1, min(limits.recent_messages, len(bounded_rows)))
    older_rows = bounded_rows[:-recent_count]
    recent_rows = bounded_rows[-recent_count:]

    context_sections: list[str] = []
    if older_rows:
        current_summary = _format_current_summary(older_rows, limits.max_chars)
        if current_summary:
            context_sections.append(
                "Contexto anterior de esta conversación. Úsalo como continuidad, "
                "pero responde al mensaje nuevo:\n" + current_summary
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


def _format_current_summary(rows: list[dict[str, Any]], max_chars: int) -> str:
    lines: list[str] = []
    remaining = max(1, max_chars)
    for row in rows:
        line = _format_message_line(row)
        if not line:
            continue
        if len(line) + 1 > remaining:
            break
        lines.append(line)
        remaining -= len(line) + 1
    return "\n".join(lines)


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
    labels = {
        "user": "Usuario",
        "assistant": "Edecán",
        "system": "Sistema",
        "tool": "Herramienta",
    }
    label = labels.get(role, role)
    return f"{label}: {text}"


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
