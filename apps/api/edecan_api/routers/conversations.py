"""`/v1/conversations/*` — CRUD + turno del agente en SSE (ARCHITECTURE.md §10.12, §10.7, §9).

`POST /{id}/messages` arma el `ToolContext`, corre `Agent.run_turn` y re-emite
cada `AgentEvent` como Server-Sent Event con los nombres pinned en §10.7. Si
el turno se detiene en `confirmation_required`, se guarda en Redis un
`PendingAgentTurn`: mensajes, lote de tool calls, nombres ofrecidos, uso,
iteración y salida acumulada. `POST /{id}/confirm` lo consume con GETDEL y
llama a `Agent.resume_turn`, que re-resuelve registry + MCP, revalida flags,
ejecuta el lote original y continúa el mismo loop LLM sin relanzar la orden.

Los payloads históricos que solo contienen tool/args siguen soportados con
el camino directo `_stream_approved_confirmation`. Ambas ramas fallan cerrado
si la confirmación expiró, ya fue consumida, perdió un flag o la tool dejó de
existir. Un rechazo también consume el pendiente y no ejecuta nada.

Al cerrar el turno (evento `done`), tras persistir `messages` + `usage_events`,
se encola el job `memory_consolidate` (ARCHITECTURE.md §9) — best-effort: un
fallo al encolar se registra en logs pero no interrumpe la respuesta ya
persistida (ver `_stream_agent_events`).
"""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import json
import logging
import re
import unicodedata
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

import aioboto3
import redis.asyncio as redis_asyncio
from edecan_core.agent import (
    Agent,
    SeleccionDeModelo,
    artifact_refs_from_tool_data,
    mission_ref_from_tool_data,
    question_tool_names_from_tool_log,
    rich_blocks_from_tool_data,
)
from edecan_core.memory import HashEmbedder, OpenAICompatEmbedder, PgMemoryStore
from edecan_core.queue import enqueue
from edecan_core.safety import public_error_message, redact
from edecan_core.tools import Tool, ToolContext, ToolResult
from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_llm.router import LLMRouter
from edecan_llm.task_router import (
    modelo_chat_con_vision_por_defecto,
    modelo_chat_info,
    modelo_chat_permitido,
)
from edecan_schemas import (
    UNLIMITED,
    ChatMessageIn,
    PendingAgentTurn,
    PendingConfirmationOut,
    PersonaConfig,
)
from edecan_schemas.plans import LIMIT_MESSAGES_PER_DAY
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from edecan_api.chat_context import ChatContextLimits, build_contextual_history
from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    CurrentUser,
    TenantCtx,
    get_current_user,
    get_llm_router,
    get_mcp_tools_for_tenant,
    get_redis,
    get_repo,
    get_streaming_repo,
    get_streaming_vault,
    get_tenant_session,
    get_tool_registry,
    rate_limit,
)
from edecan_api.persona_tools import conversation_persona_tools
from edecan_api.repo import Repo
from edecan_api.routers.perfil import profile_context_for
from edecan_api.routers.persona import persona_from_row
from edecan_api.routers.phone import phone_tool_dispatcher_for
from edecan_api.secret_intents import (
    InlineCredentialIntent,
    detect_inline_credential_intent,
    redact_values,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/conversations", tags=["conversations"], dependencies=[Depends(rate_limit)]
)

# Mapea `AgentEvent.type` (interno, edecan_core) -> nombre de evento SSE (§10.7).
EVENT_NAME_MAP: dict[str, str] = {
    "text_delta": "message.delta",
    "tool_start": "tool.start",
    "tool_progress": "tool.progress",
    "tool_end": "tool.end",
    "confirmation_required": "confirmation.required",
    "done": "message.done",
    "error": "error",
}

_RESULT_PREVIEW_LEN = 400
"""Mismo tope que `edecan_core.agent._RESULT_PREVIEW_LEN`: el `result_preview`
de un `tool_end` (acá o el que arma `Agent.run_turn`) siempre se trunca igual
de cara al cliente."""

PENDING_CONFIRMATION_TTL_SECONDS = 900
"""TTL en Redis de una confirmación `dangerous` pendiente (ARCHITECTURE.md
§10.12): ventana para que el usuario apruebe/rechace antes de que expire la
acción que el modelo propuso."""

_IMPORTANT_TOOL_NOTIFICATIONS: dict[str, str] = {
    # `crear_post_linkedin` viene con la misma promesa que `crear_contenido_social`
    # (borrador listo, con imagen), así que dispara el mismo push. Sin esta
    # entrada, el atajo directo terminaba en silencio: la persona veía la
    # tarjeta en el chat solo si la app ya estaba abierta y mirando.
    "crear_post_linkedin": "content_created",
    "crear_contenido_social": "content_created",
    "generar_contenido": "content_created",
    "publicar_social": "content_published",
    "crear_diseno_visual": "design_ready",
    "refinar_diseno_visual": "design_ready",
    "exportar_diseno_visual": "design_export_ready",
    "gestionar_autorreparacion_local": "self_repair_completed",
}


def _notification_kind_for_tool(name: str) -> str | None:
    """Clasifica herramientas terminadas sin mantener 36 entradas duplicadas.

    El motor completo de Studio conserva el prefijo ``fydesign_`` como
    contrato estable. Cualquier capacidad que produzca o transforme un
    entregable visual debe avisar al teléfono igual que el editor nativo.
    ``fydesign_health`` es diagnóstico y no representa trabajo terminado.
    """

    explicit = _IMPORTANT_TOOL_NOTIFICATIONS.get(name)
    if explicit is not None:
        return explicit
    if name.startswith("fydesign_") and name != "fydesign_health":
        return "design_ready"
    return None


class ConversationIn(BaseModel):
    title: str | None = None
    channel: Literal["web", "voice", "phone", "api"] = "web"


class ConversationTitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ConversationModelIn(BaseModel):
    """Cuerpo de `PUT /{conversation_id}/model`.

    Las dos claves son opcionales en el JSON pero se escriben SIEMPRE las dos:
    `null` es "volver a automático", un valor legítimo, no "no cambiar". Así el
    endpoint es idempotente y no existe un tercer estado ambiguo.
    """

    model: str | None = Field(default=None, max_length=200)
    effort: Literal["bajo", "medio", "alto"] | None = None


class ConfirmIn(BaseModel):
    tool_call_id: str
    approved: bool


def _conversation_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": redact(str(row.get("title") or "")),
        "channel": row.get("channel", "web"),
        # Frente 5 (paridad Aria): marca la conversación "principal" -- la
        # que recibe los eventos automáticos que el dueño no pidió. iOS la
        # fija arriba del historial y la distingue visualmente con esta
        # bandera (ver `Repo.resolve_main_conversation`,
        # `GET /v1/conversations/main`).
        "is_main": bool(row.get("is_main") or False),
        # Selector de modelos del chat (migración 0027): `null` = automático.
        # Van en la conversación y no en la credencial del tenant para que la
        # pastilla del composer se restaure al reabrir el chat en cualquier
        # dispositivo. Clientes viejos ignoran campos extra.
        "model": row.get("chat_model") or None,
        "effort": row.get("chat_effort") or None,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _redact_payload(value: Any) -> Any:
    """Redacta strings anidados al leer filas creadas por versiones viejas."""

    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_payload(item) for key, item in value.items()}
    return value


def _message_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "role": row["role"],
        "content": _redact_payload(row.get("content")),
        "tool_calls": _redact_payload(row.get("tool_calls")),
        "tokens_in": row.get("tokens_in", 0),
        "tokens_out": row.get("tokens_out", 0),
        "created_at": row.get("created_at"),
    }


@router.get("")
async def list_conversations(
    current_user: CurrentUser = Depends(get_current_user), repo: Repo = Depends(get_repo)
) -> list[dict[str, Any]]:
    await _refresh_conversation_titles(current_user=current_user, repo=repo)
    rows = await repo.list_conversations(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    return [_conversation_out(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    row = await repo.create_conversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        title=redact(body.title or ""),
        channel=body.channel,
    )
    return _conversation_out(row)


@router.get("/main")
async def get_main_conversation(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Resuelve (o crea si no existe) la conversación "principal" de este
    tenant+usuario -- frente 5, paridad Aria: ahí aterrizan los eventos
    automáticos que el dueño no pidió (llamada recibida, automatización
    ejecutada, recordatorio disparado), igual que el hilo de avisos de
    Aria. Es el "helper reutilizable" que exponen los frentes 2 (worker,
    escribe los eventos ahí) y 3 (deeplink del push: `conversation_id` en el
    payload apunta a esta misma fila) -- ver `Repo.resolve_main_conversation`
    para la receta exacta que cualquier otro servicio con acceso directo a
    Postgres debe replicar.

    Declarada ANTES de `GET /{conversation_id}` a propósito: si quedara
    después, FastAPI intentaría parsear el literal "main" como
    `conversation_id: uuid.UUID` y devolvería 422 en vez de resolver esta
    ruta -- Starlette empareja por orden de registro, no por especificidad.
    """
    row = await repo.resolve_main_conversation(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    return _conversation_out(row)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> dict[str, Any]:
    row = await repo.get_conversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    messages = await repo.list_messages(
        tenant_id=current_user.tenant_id, conversation_id=conversation_id
    )
    out = _conversation_out(row)
    out["messages"] = [_message_out(m) for m in messages]
    pending = await _get_pending_confirmation(
        redis_client,
        tenant_id=current_user.tenant_id,
        conversation_id=conversation_id,
    )
    out["pending_confirmation"] = pending.model_dump(mode="json") if pending else None
    return out


@router.patch("/{conversation_id}")
async def rename_conversation(
    conversation_id: uuid.UUID,
    body: ConversationTitleIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    title = redact(" ".join(body.title.split()))
    if not title:
        raise HTTPException(status_code=422, detail="Escribe un nombre para la conversación.")
    row = await repo.update_conversation_title(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
        title=title,
        source="manual",
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    return _conversation_out(row)


@router.put("/{conversation_id}/model")
async def set_conversation_model(
    conversation_id: uuid.UUID,
    body: ConversationModelIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fija el modelo (y el Esfuerzo) del selector para esta conversación.

    Es la vía normal de los clientes: permite elegir SIN mandar un mensaje, y
    al vivir en la fila hace que la pastilla del composer sobreviva reinicios y
    que el flujo de confirmación (`POST /confirm`, otro request HTTP) corra con
    el mismo modelo releyendo estas columnas.

    El Esfuerzo se guarda aunque el modelo activo no lo soporte: cambiar de
    Copla a Oda debe recordar el nivel previo. El gate es al APLICAR el turno
    (ver `_seleccion_efectiva`), no al guardar.
    """

    if not modelo_chat_permitido(body.model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Ese modelo no está en el catálogo del chat.",
        )
    row = await repo.update_conversation_model(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
        model=body.model.strip() if body.model else None,
        effort=body.effort,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    return {"model": row.get("chat_model") or None, "effort": row.get("chat_effort") or None}


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    deleted = await repo.delete_conversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")


# ---------------------------------------------------------------------------
# Helpers de armado del turno
# ---------------------------------------------------------------------------


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = str(content.get("text", ""))
        attachments = content.get("attachments")
        if isinstance(attachments, list) and attachments:
            refs = [
                _attachment_context_line(item)
                for item in attachments[:10]
                if isinstance(item, dict)
            ]
            if refs:
                return (text + "\n\nArchivos adjuntos privados:\n" + "\n".join(refs)).strip()
        return text
    return ""


_TITLE_LEADING_FILLER_RE = re.compile(
    r"^(?:(?:hola|buenas(?:\s+(?:tardes|noches|d[ií]as))?)[,.!\s]+)?"
    r"(?:(?:por\s+favor|necesito\s+que|quiero\s+que|puedes|podr[ií]as)\s+)?",
    re.IGNORECASE,
)
_TITLE_ACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:configura(?:me)?|configurar)\b", re.IGNORECASE), "Configurar"),
    (re.compile(r"^(?:planifica(?:me)?|planea(?:me)?|planificar)\b", re.IGNORECASE), "Planificar"),
    (re.compile(r"^(?:crea(?:me)?|crear|genera(?:me)?|generar)\b", re.IGNORECASE), "Crear"),
    (
        re.compile(r"^(?:escribe(?:me)?|redacta(?:me)?|escribir|redactar)\b", re.IGNORECASE),
        "Escribir",
    ),
    (re.compile(r"^(?:busca(?:me)?|encuentra(?:me)?|buscar)\b", re.IGNORECASE), "Buscar"),
    (
        re.compile(r"^(?:revisa(?:me)?|analiza(?:me)?|audita(?:me)?|revisar)\b", re.IGNORECASE),
        "Revisar",
    ),
    (re.compile(r"^(?:edita(?:me)?|corrige(?:me)?|editar|corregir)\b", re.IGNORECASE), "Editar"),
    (re.compile(r"^(?:organiza(?:me)?|ordenar|organizar)\b", re.IGNORECASE), "Organizar"),
)
_TITLE_API_PROVIDER_RE = re.compile(
    r"\b(?:api[\s_-]*key|clave(?:\s+api)?|token|credencial)"
    r"(?:\s+(?:de|para))?\s+"
    r"(?P<provider>[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9._-]*"
    r"(?:\s+[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9._-]*){0,2})"
    r"(?=\s+(?:es|vale|con)\b|[:=,.!?]|$)",
    re.IGNORECASE,
)
_TITLE_DAILY_EXERCISE_RE = re.compile(
    r"^(?:qu[eé]\s+pasar[ií]a\s+si\s+)?(?:yo\s+)?"
    r"(?:empezara?\s+a\s+)?(?:hacer\s+)?"
    r"(?P<count>\d+)\s+(?P<exercise>flexiones|sentadillas|abdominales)"
    r"(?:\s+(?P<frequency>diarias?|cada\s+d[ií]a))?\b",
    re.IGNORECASE,
)
_TITLE_PUSH_RE = re.compile(
    r"\b(?:notificaci[oó]n|notificaciones|push|apns|fcm)\b",
    re.IGNORECASE,
)
_TITLE_PUSH_TEST_RE = re.compile(
    r"\b(?:prueba|probar|test|pru[eé]bala|pru[eé]balo)\b",
    re.IGNORECASE,
)
_TITLE_FLIGHTS_RE = re.compile(
    r"\b(?:vuelo|vuelos|volar)\b(?:\s+(?:de|desde))?\s+"
    r"(?P<origin>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]{1,32}?)\s+"
    r"(?:a|hasta)\s+"
    r"(?P<destination>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]{1,32}?)"
    r"(?=\s+(?:el|del|para|entre|desde|con)\b|[,.;!?]|$)",
    re.IGNORECASE,
)
_TITLE_MAX_CHARS = 46


def _credential_conversation_title(intent: InlineCredentialIntent) -> str:
    """Nombra el objetivo, nunca la frase que contenía la credencial."""

    return f"Configurar API Key - {intent.display_name}"[:72]


def _automatic_conversation_title(text: str, *, fallback: str = "Conversación") -> str:
    """Resume la primera intención como una tarea breve, sin otra llamada al LLM."""

    clean = redact(" ".join(text.replace("\n", " ").split())).strip(" \t\r\n#*-")
    if not clean:
        return fallback
    provider_match = _TITLE_API_PROVIDER_RE.search(clean)
    if provider_match:
        provider = provider_match.group("provider").strip(" ,;:-")
        return f"Configurar API Key - {provider}"[:72]
    if re.search(r"\b(?:api[\s_-]*key|clave(?:\s+api)?|credencial)\b", clean, re.IGNORECASE):
        return "Configurar API Key"

    if re.search(r"\bwho\s+you\s+are\b", clean, re.IGNORECASE):
        return "Explicar quién es Edecán"

    if _TITLE_PUSH_RE.search(clean):
        if _TITLE_PUSH_TEST_RE.search(clean):
            return "Prueba de notificación push"
        if re.search(r"\b(?:configura|activar|activa|habilita)\w*\b", clean, re.IGNORECASE):
            return "Configurar notificaciones push"
        return "Enviar notificación push"

    flight_match = _TITLE_FLIGHTS_RE.search(clean)
    if flight_match:
        origin = " ".join(flight_match.group("origin").split()).strip(" ,;:-")
        destination = " ".join(flight_match.group("destination").split()).strip(" ,;:-")
        if origin and destination:
            return f"Vuelos {origin} a {destination}"[:72]

    exercise_match = _TITLE_DAILY_EXERCISE_RE.match(clean)
    if exercise_match:
        frequency = exercise_match.group("frequency") or "diarias"
        if frequency.casefold() == "cada día":
            frequency = "diarias"
        return (
            f"Hacer {exercise_match.group('count')} "
            f"{exercise_match.group('exercise').lower()} {frequency.lower()}"
        )

    clean = _TITLE_LEADING_FILLER_RE.sub("", clean).strip()
    first_sentence = clean
    for separator in ("?", "!", "."):
        candidate = first_sentence.split(separator, 1)[0].strip()
        if candidate:
            first_sentence = candidate
    for action_re, infinitive in _TITLE_ACTIONS:
        match = action_re.match(first_sentence)
        if match is None:
            continue
        subject = first_sentence[match.end() :].strip(" ,;:-")
        subject = re.sub(r"^(?:me|mi|un|una|el|la)\s+", "", subject, count=1, flags=re.I)
        first_sentence = f"{infinitive} {subject}".strip()
        break
    shortened = first_sentence[:_TITLE_MAX_CHARS]
    if len(first_sentence) > _TITLE_MAX_CHARS:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip(" ,;:") or fallback


def _title_is_message_copy(title: str, first_user_text: str) -> bool:
    """Detecta un recorte del mensaje, incluso si la interfaz lo elipsa."""

    normalized_title = " ".join(title.split()).strip()
    normalized_message = " ".join(first_user_text.split()).strip()
    if len(normalized_title) < 20 or not normalized_message:
        return False
    return normalized_message.casefold().startswith(normalized_title.casefold())


def _sanitize_generated_title(value: str, *, fallback: str) -> str:
    """Normaliza la salida breve del LLM sin permitir markdown ni explicaciones."""

    title = value.strip().splitlines()[0] if value.strip() else ""
    title = re.sub(r"^(?:t[ií]tulo|title)\s*:\s*", "", title, flags=re.IGNORECASE)
    title = title.strip(" \"'`#*—–-.,:;")
    title = redact(" ".join(title.split()))
    if not title or len(title.split()) > 10:
        return fallback
    shortened = title[:_TITLE_MAX_CHARS]
    if len(title) > _TITLE_MAX_CHARS:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip(" ,;:.!?") or fallback


async def _semantic_conversation_title(
    llm_router: LLMRouter,
    text: str,
    *,
    fallback: str,
) -> str:
    """Genera un nombre semántico con el modelo rápido; nunca bloquea el chat si falla."""

    safe_text = redact(" ".join(text.replace("\n", " ").split()))[:600]
    if not safe_text:
        return fallback
    if not _title_is_message_copy(fallback, safe_text):
        # Las intenciones conocidas ya producen un título estable y más
        # confiable que una segunda inferencia (además de ahorrar latencia).
        return fallback
    try:
        response = await llm_router.complete(
            "rapido",
            {},
            CompletionRequest(
                model="",
                system=(
                    "Escribe solo un título de 3 a 7 palabras que resuma el objetivo. "
                    "Usa el idioma del usuario. No copies la frase. Sin comillas, "
                    "markdown, punto final ni explicación."
                ),
                messages=[
                    ChatMessage(
                        role="user",
                        content=safe_text,
                    )
                ],
                # GLM puede usar cómputo interno antes de emitir el texto. El
                # presupuesto debe dejar margen para ese razonamiento además
                # del título visible; con 96 todavía hubo respuestas vacías en
                # el runtime empaquetado. 256 sigue siendo un límite pequeño.
                max_tokens=256,
                temperature=0.1,
                metadata={"task": "conversation_title"},
            ),
        )
    except Exception:
        logger.warning("No se pudo generar el título semántico; se conserva el fallback.")
        return fallback
    generated = _sanitize_generated_title(response.text, fallback=fallback)
    return fallback if _title_is_message_copy(generated, safe_text) else generated


async def _refresh_conversation_titles(*, current_user: CurrentUser, repo: Repo) -> None:
    """Resume títulos automáticos y clasifica los heredados sin tocar los manuales."""

    candidates = await repo.list_conversation_title_refresh_candidates(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
    )
    for row in candidates:
        old_title = str(row.get("title") or "").strip()
        first_user_text = _extract_text(row.get("first_user_content"))
        old_source = str(row.get("title_source") or "legacy")
        copied_message = _title_is_message_copy(old_title, first_user_text)
        if copied_message:
            title = _automatic_conversation_title(first_user_text)
            source = "auto"
        elif old_source == "auto":
            # Un título automático ya semántico puede haber sido creado por el
            # LLM. No lo degradamos de nuevo al fallback determinista en cada GET.
            title = old_title
            source = "auto"
        else:
            title = old_title
            source = "manual"
        if not title or (title == old_title and source == old_source):
            continue
        await repo.update_conversation_title(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            conversation_id=row["id"],
            title=title,
            source=source,
        )


def _attachment_context_line(item: dict[str, Any]) -> str:
    file_id = item.get("file_id")
    mime = str(item.get("mime") or "application/octet-stream")
    instruction = (
        "si puedes verla en este turno, respóndela directamente sin llamar una herramienta; "
        f"si no aparece, usa leer_archivo(file_id={file_id})"
        if mime.split(";", 1)[0].lower() in _DIRECT_VISION_MIMES
        else f"usa leer_archivo(file_id={file_id}) para ver su contenido antes de responder"
    )
    return f"- file_id={file_id} · {item.get('filename') or 'archivo'} · {mime} · {instruction}"


async def _resolve_message_attachments(
    *, repo: Repo, tenant_id: uuid.UUID, file_ids: list[uuid.UUID]
) -> list[dict[str, str | None]]:
    """Resuelve adjuntos por tenant y solo expone metadata necesaria al agente."""

    attachments: list[dict[str, str | None]] = []
    for file_id in file_ids:
        row = await repo.get_file(tenant_id=tenant_id, file_id=file_id)
        if row is None:
            # 404 uniforme: no revela si el UUID existe bajo otro tenant.
            raise HTTPException(status_code=404, detail="Archivo adjunto no encontrado.")
        attachments.append(
            {
                "file_id": str(file_id),
                "filename": str(row.get("filename") or "archivo")[:255],
                "mime": str(row.get("mime") or "application/octet-stream")[:255],
            }
        )
    return attachments


_DIRECT_VISION_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_DIRECT_VISION_MAX_BYTES = 10 * 1024 * 1024
_DIRECT_VISION_MAX_TOTAL_BYTES = 25 * 1024 * 1024
_DIRECT_VISION_MAX_IMAGES = 10


def _turno_trae_imagen(attachments: list[dict[str, str | None]]) -> bool:
    """¿Este turno lleva una imagen que el modelo va a ver directo?

    Mismo criterio exacto que `_direct_multimodal_content` (los mimes de
    `_DIRECT_VISION_MIMES`): si divergen, el gate de ceguera decidiría sobre un
    turno distinto del que realmente lleva la imagen.
    """

    return any(
        str(item.get("mime") or "").split(";", 1)[0].lower() in _DIRECT_VISION_MIMES
        for item in attachments
    )


def _seleccion_efectiva(
    *,
    modelo: str | None,
    esfuerzo: str | None,
    trae_imagen: bool,
) -> SeleccionDeModelo:
    """Traduce la selección persistida (o el override del body) al turno.

    Precedencia completa, de mayor a menor: (1) `model`/`effort` del body del
    POST, que el llamador ya fusionó con (2) `conversations.chat_model` /
    `chat_effort`; si las dos están vacías queda `None` = automático y decide la
    cadena de siempre: (3) `WORKERS_AI_CHAT_MODEL` del `platform-config.json`
    de la máquina (vía `os.environ.setdefault` en `edecan_local.runtime`, que
    HOY es quien gana en la máquina del dueño cuando no hay selección), (4) el
    default de `edecan_api.config`, (5) `MODELO_POR_DEFECTO` de
    `edecan_llm.workers_ai`. Ese orden importa saberlo: si el
    `platform-config.json` apunta a un modelo raro, "Automático" no es Copla.

    Dos reglas se aplican acá, y son las dos DEL TURNO — nunca tocan lo
    persistido:

    - Ceguera: si el turno trae imagen y el modelo elegido no la ve, corre con
      el default con visión del catálogo. Es degradación determinista y escrita
      en el contrato, sin evento SSE nuevo (agregar un tipo de `AgentEvent`
      rompería los decoders de las tres UIs); los clientes además avisan antes
      de enviar porque tienen el mismo catálogo. El próximo turno sin imagen
      vuelve al modelo elegido.
    - Esfuerzo: solo se aplica si el modelo efectivo tiene `soporta_esfuerzo`.
      Un nivel que no cambia nada sería un control decorativo.
    """

    if modelo is not None and trae_imagen:
        ficha = modelo_chat_info(modelo)
        if ficha is not None and not ficha["ve_imagenes"]:
            modelo = modelo_chat_con_vision_por_defecto()

    ficha_efectiva = modelo_chat_info(modelo)
    if ficha_efectiva is None or not ficha_efectiva["soporta_esfuerzo"]:
        esfuerzo = None
    return SeleccionDeModelo(modelo=modelo, esfuerzo=esfuerzo)


async def _direct_multimodal_content(
    *,
    settings: Settings,
    user_text: str,
    attachments: list[dict[str, str | None]],
    repo: Repo,
    tenant_id: uuid.UUID,
) -> str | list[dict[str, Any]]:
    """Construye el primer turno multimodal sin una llamada intermedia a tools.

    Las imágenes siguen privadas: se descargan desde el bucket del dueño,
    viajan como bloques base64 únicamente hacia el proveedor seleccionado y
    nunca se convierten en URL pública. Si storage falla o el adjunto no es
    una imagen compatible, el contrato anterior de ``file_id`` permanece
    disponible como texto y ``leer_archivo`` puede resolverlo.

    PISO DE 10px (medido el 29-07-2026): la imagen se manda TAL CUAL, sin
    re-escalar ni miniaturizar, y así debe quedarse. Soneto
    (``gemma-4-26b-a4b-it``, uno de los cuatro principales del selector)
    responde HTTP 400 "image dimensions must be at least 10px" con imágenes de
    menos de 10px de lado. Si algún día se agrega re-escalado o miniaturas
    aquí, ese piso es el primero que se rompe y Soneto es el primero que falla.
    """

    image_rows: list[tuple[str, str]] = []
    for item in attachments[:_DIRECT_VISION_MAX_IMAGES]:
        mime = str(item.get("mime") or "").split(";", 1)[0].lower()
        file_id = item.get("file_id")
        if mime not in _DIRECT_VISION_MIMES or not file_id:
            continue
        try:
            row = await repo.get_file(tenant_id=tenant_id, file_id=uuid.UUID(str(file_id)))
        except (TypeError, ValueError):
            continue
        if row is None or int(row.get("size_bytes") or 0) > _DIRECT_VISION_MAX_BYTES:
            continue
        key = str(row.get("s3_key") or "")
        if key:
            image_rows.append((mime, key))

    if not image_rows:
        return user_text

    blocks: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    session = aioboto3.Session()
    total_bytes = 0
    try:
        async with session.client(
            "s3",
            region_name=settings.AWS_REGION,
            endpoint_url=settings.AWS_ENDPOINT_URL,
        ) as s3:
            for mime, key in image_rows:
                try:
                    response = await s3.get_object(Bucket=settings.S3_BUCKET, Key=key)
                    raw = await response["Body"].read(_DIRECT_VISION_MAX_BYTES + 1)
                except Exception:  # noqa: BLE001 - una imagen no invalida las demás
                    logger.warning(
                        "No se pudo insertar una imagen directamente en el turno",
                        extra={"s3_key_hash": hashlib.sha256(key.encode()).hexdigest()[:12]},
                        exc_info=True,
                    )
                    continue
                if (
                    not raw
                    or len(raw) > _DIRECT_VISION_MAX_BYTES
                    or total_bytes + len(raw) > _DIRECT_VISION_MAX_TOTAL_BYTES
                ):
                    continue
                total_bytes += len(raw)
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": base64.b64encode(raw).decode("ascii"),
                        },
                    }
                )
    except Exception:  # noqa: BLE001 - storage degradable; leer_archivo sigue disponible
        logger.warning("No se pudo abrir storage para visión directa", exc_info=True)
        return user_text

    return blocks if len(blocks) > 1 else user_text


def _tools_con_pregunta_pendiente(history_rows: list[dict[str, Any]]) -> list[str]:
    """Tools que dejaron una tarjeta de pregunta abierta en el ÚLTIMO turno del asistente.

    El nombre de la tool que preguntó vive en `messages.tool_calls` (la bitácora de
    `tool_start`/`tool_end` con sus bloques ricos). El historial que recibe el agente se
    aplana a puro texto (`build_contextual_history`), así que ese nombre se pierde por el
    camino: sin este puente, el turno siguiente decide qué herramientas ofrecer solo con las
    palabras del usuario, y "Personal" no se parece en nada a "post de LinkedIn". Ver la
    invariante "quien pregunta tiene que poder oír la respuesta" en
    `edecan_core.capability_routing`.

    Se mira SOLO el último turno del asistente a propósito: contestar una pregunta es la
    continuación del turno inmediatamente anterior. Así la pregunta pendiente se consume
    sola en cuanto el asistente vuelve a hablar sin preguntar, y una petición nueva y sin
    relación no arrastra para siempre la tool de una pregunta vieja.
    """

    for row in reversed(history_rows):
        if row.get("role") == "assistant":
            return question_tool_names_from_tool_log(row.get("tool_calls"))
    return []


def _rows_to_chat_messages(rows: list[dict[str, Any]]) -> list[ChatMessage]:
    return [
        ChatMessage(role=row["role"], content=redact(_extract_text(row.get("content"))))
        for row in rows
        if row.get("role") in ("system", "user", "assistant", "tool")
    ]


async def _check_message_quota(repo: Repo, tenant: TenantCtx) -> None:
    # Default `0` (fail-closed), NUNCA `UNLIMITED` (barrido v7, WP-V7-08 lo
    # encontró y corrigió en `files.py`/`voice.py`; este archivo quedó fuera
    # del alcance de ese WP y se aplica acá, WP-V7-12, con el mismo criterio):
    # `edecan_api.deps.flags_for_plan` devuelve `{}` para un `plan_key`
    # huérfano (catálogo de planes desactualizado, ver su docstring) -- con el
    # default anterior (`UNLIMITED`) ese caso dejaba mandar mensajes SIN
    # NINGÚN límite en el endpoint más usado de toda la API, en vez de sin
    # cupo. `0` es seguro para los 4 planes reales: `LIMIT_MESSAGES_PER_DAY`
    # SIEMPRE viene explícito en `edecan_schemas.plans.PLANES` (nunca ausente
    # salvo plan huérfano), así que este default nunca se alcanza en
    # operación normal -- mismo criterio que `missions.py::_check_missions_quota`
    # y el fix de `files.py`/`voice.py` citado arriba.
    limit = tenant.flags.get(LIMIT_MESSAGES_PER_DAY, 0)
    if limit is None or limit == UNLIMITED:
        return
    since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    used = await repo.sum_usage_since(tenant_id=tenant.tenant_id, kind="messages", since=since)
    if used >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Alcanzaste tu límite de {int(limit)} mensajes por día de tu plan "
                f"'{tenant.plan_key}'. Vuelve a intentarlo mañana o mejora tu plan."
            ),
        )


@functools.lru_cache(maxsize=8)
def _cached_openai_embedder(base_url: str, api_key: str, model: str) -> OpenAICompatEmbedder:
    """Reutiliza un único `OpenAICompatEmbedder` (y su `httpx.AsyncClient`) por
    combinación de settings, en vez de abrir un cliente HTTP nuevo -y nunca
    cerrarlo, `PgMemoryStore` solo llama `.embed()`- en cada turno de chat con
    memoria activada. Mismo patrón que `_redis_client` en `edecan_api.deps`;
    seguro de compartir entre tenants porque las credenciales son de proceso
    (`Settings`, no del vault por tenant)."""
    return OpenAICompatEmbedder(base_url=base_url, api_key=api_key, model=model)


# Placeholders públicos de `.env.example` para el proveedor de embeddings
# OpenAI-compatible (no son secretos: compararlos aquí no filtra nada). Un
# `.env` recién copiado de `.env.example` sin tocar estas dos variables trae
# EXACTAMENTE estos valores — strings no vacíos, por tanto truthy — así que
# un chequeo `if settings.OPENAI_COMPAT_API_KEY and settings.EMBEDDINGS_MODEL`
# por sí solo NO detecta que siguen sin configurar. Sin este chequeo extra,
# el setup mínimo de `docs/self-hosting.md` §2.1 (que no pide reemplazar
# estas dos variables) dispara una llamada HTTP real a
# `https://api.openai.com/v1/embeddings` con una API key falsa en cada turno
# de chat con memoria activada (`persona.memoria_activada=True` por defecto),
# rompiendo el chat en vez de caer al `HashEmbedder` offline que promete
# `docs/self-hosting.md` §4. Mismo patrón que `JWT_SECRET_PLACEHOLDER`/
# `LOCAL_MASTER_KEY_PLACEHOLDER` en `edecan_api.config`.
_OPENAI_COMPAT_API_KEY_PLACEHOLDER = "TU_OPENAI_COMPAT_API_KEY_AQUI"
_EMBEDDINGS_MODEL_PLACEHOLDER = "TU_EMBEDDINGS_MODEL_AQUI"


def _has_real_embeddings_provider(settings: Settings) -> bool:
    """`True` solo si hay un proveedor de embeddings OpenAI-compatible
    configurado de verdad: `OPENAI_COMPAT_BASE_URL`/`OPENAI_COMPAT_API_KEY`/
    `EMBEDDINGS_MODEL` no vacíos y, además, `OPENAI_COMPAT_API_KEY`/
    `EMBEDDINGS_MODEL` distintos de los placeholders públicos de
    `.env.example` (ver comentario arriba). Usada tanto por `_build_embedder`
    como por `_build_document_embedder` para que ambas decisiones nunca
    queden desincronizadas.
    """
    # ``OPENAI_COMPAT_*`` belonged to the retired provider configuration.
    # Older hosted deployments may still expose the attributes, but a clean
    # Workers AI installation intentionally does not. Treat their absence as
    # "no external embedder" so memory falls back to the local HashEmbedder
    # instead of aborting every chat turn before inference starts.
    base_url = getattr(settings, "OPENAI_COMPAT_BASE_URL", None)
    api_key = getattr(settings, "OPENAI_COMPAT_API_KEY", None)
    embeddings_model = getattr(settings, "EMBEDDINGS_MODEL", None)
    return bool(
        base_url
        and api_key
        and api_key != _OPENAI_COMPAT_API_KEY_PLACEHOLDER
        and embeddings_model
        and embeddings_model != _EMBEDDINGS_MODEL_PLACEHOLDER
    )


def _build_embedder(settings: Settings) -> Any:
    if _has_real_embeddings_provider(settings):
        return _cached_openai_embedder(
            settings.OPENAI_COMPAT_BASE_URL,
            settings.OPENAI_COMPAT_API_KEY,
            settings.EMBEDDINGS_MODEL,
        )
    return HashEmbedder(dim=settings.EMBEDDINGS_DIM)


def _build_memory_store(session: Any, settings: Settings, persona: PersonaConfig) -> Any:
    if not persona.memoria_activada:
        return None
    return PgMemoryStore(session=session, embedder=_build_embedder(settings))


def _build_document_embedder(settings: Settings) -> Any:
    """`ctx.extras["memory_embedder"]` que lee `ConsultarDocumentosTool`
    (`edecan_toolkit.documentos`): solo se expone cuando hay un proveedor de
    embeddings real configurado. Si no (self-host sin `EMBEDDINGS_MODEL`), se
    devuelve `None` para que esa tool caiga a su propio fallback por texto
    plano (`ILIKE`) — tal como documenta su docstring — en vez de embeber con
    el `HashEmbedder` determinista que usa `_build_memory_store` para la
    memoria de largo plazo.
    """
    if _has_real_embeddings_provider(settings):
        return _build_embedder(settings)
    return None


def _companion_caller(request: Request, tenant_id: uuid.UUID) -> Any:
    manager = request.app.state.companion_manager
    if not manager.is_connected(tenant_id):
        return None
    return functools.partial(manager.send_command, tenant_id)


async def _enqueue_push_test(
    *,
    settings: Settings,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> dict[str, Any]:
    """Encola una prueba push opaca para el usuario actual.

    El modelo no controla título, cuerpo, token ni destinatario. El worker
    reutiliza el pipeline universal idempotente que respeta preferencias,
    dispositivos activos y credenciales APNs/FCM del tenant.
    """

    event_id = uuid.uuid4()
    job_id = await enqueue(
        settings,
        "notify_important_event",
        {
            "user_id": str(user_id),
            "kind": "push_test",
            "event_id": str(event_id),
            "chat_id": str(conversation_id),
        },
        tenant_id,
    )
    return {
        "queued": True,
        "event_id": str(event_id),
        "job_id": str(job_id),
    }


def _build_ctx(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    session: Any,
    settings: Settings,
    llm_router: LLMRouter,
    vault: Any,
    persona: PersonaConfig,
    request: Request,
    repo: Repo,
    approved_tool_calls: set[str],
    flags: dict[str, Any],
    conversation_id: uuid.UUID,
    phone_call_dispatcher: Any | None = None,
    profile_context: str = "",
) -> ToolContext:
    return ToolContext(
        tenant_id=tenant_id,
        user_id=user_id,
        session=session,
        settings=settings,
        llm=llm_router,
        vault=vault,
        extras={
            "companion": _companion_caller(request, tenant_id),
            "memory_store": _build_memory_store(session, settings, persona),
            "profile_context": profile_context,
            "memory_embedder": _build_document_embedder(settings),
            "approved_tool_calls": approved_tool_calls,
            # Callable tenant/user-scoped usado exclusivamente por las tools
            # de estilo de conversación. No se expone a misiones ni a
            # automatizaciones y nunca recibe credenciales.
            "persona_updater": functools.partial(
                repo.upsert_persona, tenant_id=tenant_id, user_id=user_id
            ),
            # `tenant.flags` (ARCHITECTURE.md §10.7): mismo dict que ya recibe
            # `Agent.run_turn(flags=...)` para resolver el modelo del turno
            # principal. Lo repetimos acá porque una `Tool` solo recibe `ctx`
            # (nunca el `flags` explícito de `run_turn`), y herramientas como
            # `GenerarContenidoTool` (edecan_toolkit.contenido) necesitan estos
            # flags para su propio `ctx.llm.complete("principal", flags, ...)`
            # — sin esta clave, `_tenant_flags(ctx)` siempre ve `{}` y el
            # downgrade a modelo "rapido" por plan nunca se aplica.
            "flags": flags,
            # La tool de llamada delega en una transacción independiente que
            # se cierra/committea antes de tocar Twilio (evita carrera webhook).
            "phone_call_dispatcher": phone_call_dispatcher,
            # Prueba push real limitada al usuario/tenant/conversación del
            # request. La tool nunca recibe tokens ni puede elegir otro
            # destinatario o introducir texto libre en la notificación.
            "push_test_dispatcher": functools.partial(
                _enqueue_push_test,
                settings=settings,
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
            ),
        },
    )


# ---------------------------------------------------------------------------
# Confirmaciones pendientes (Redis) — ver docstring del módulo.
#
# El `tool_call_id` de una `ConfirmationRequiredEvent` lo acuña el proveedor
# LLM en esa respuesta puntual (Anthropic/OpenAI-compatible generan un id
# opaco nuevo por completion); no hay manera de pedirle al modelo que lo
# reproduzca en una llamada posterior con el mismo prompt. Por eso se guarda
# un `PendingAgentTurn` keyed por `(tenant_id, conversation_id, tool_call_id)`;
# el payload mínimo `{name,args}` se conserva para compatibilidad histórica.
# ---------------------------------------------------------------------------


def _pending_confirmation_key(
    *, tenant_id: uuid.UUID, conversation_id: uuid.UUID, tool_call_id: str
) -> str:
    return f"pending_confirm:{tenant_id}:{conversation_id}:{tool_call_id}"


def _current_pending_confirmation_key(*, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    return f"pending_confirm_current:{tenant_id}:{conversation_id}"


async def _store_pending_confirmation(
    redis_client: redis_asyncio.Redis,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    tool_call_id: str,
    name: str,
    args: dict[str, Any],
    pending_turn: PendingAgentTurn | dict[str, Any] | None = None,
) -> None:
    key = _pending_confirmation_key(
        tenant_id=tenant_id, conversation_id=conversation_id, tool_call_id=tool_call_id
    )
    payload_data: dict[str, Any] = {"name": name, "args": args}
    if pending_turn is not None:
        payload_data["pending_turn"] = PendingAgentTurn.model_validate(pending_turn).model_dump()
    payload = json.dumps(payload_data, ensure_ascii=False, default=str)
    await redis_client.set(key, payload, ex=PENDING_CONFIRMATION_TTL_SECONDS)
    public_pending = PendingConfirmationOut(
        tool_call_id=tool_call_id,
        name=name,
        args=args,
    )
    await redis_client.set(
        _current_pending_confirmation_key(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
        ),
        public_pending.model_dump_json(),
        ex=PENDING_CONFIRMATION_TTL_SECONDS,
    )


async def _get_pending_confirmation(
    redis_client: redis_asyncio.Redis,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> PendingConfirmationOut | None:
    """Devuelve solo la vista pública de la confirmación vigente del chat.

    La referencia y el payload operativo se resuelven con claves que incluyen
    tenant + conversación. Se comprueba además que el payload de un solo uso
    siga existiendo, de modo que una referencia vencida nunca reconstruya una
    tarjeta que ya no puede confirmarse.
    """

    current_key = _current_pending_confirmation_key(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
    )
    raw_current = await redis_client.get(current_key)
    if raw_current is None:
        return None
    try:
        current = PendingConfirmationOut.model_validate_json(raw_current)
    except Exception:  # noqa: BLE001 - Redis puede conservar datos de una versión anterior
        await redis_client.delete(current_key)
        return None

    raw_pending = await redis_client.get(
        _pending_confirmation_key(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            tool_call_id=current.tool_call_id,
        )
    )
    if raw_pending is None:
        await redis_client.delete(current_key)
        return None
    try:
        pending = json.loads(raw_pending)
        # Nombre y argumentos salen del mismo registro de un solo uso que
        # consume /confirm. `pending_turn` nunca se copia a la respuesta.
        return PendingConfirmationOut(
            tool_call_id=current.tool_call_id,
            name=pending["name"],
            args=pending.get("args") or {},
        )
    except Exception:  # noqa: BLE001 - dato corrupto: falla cerrado
        await redis_client.delete(current_key)
        return None


async def _pop_pending_confirmation(
    redis_client: redis_asyncio.Redis,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    tool_call_id: str,
) -> dict[str, Any] | None:
    """Consume atómicamente una confirmación pendiente mediante Redis GETDEL."""
    key = _pending_confirmation_key(
        tenant_id=tenant_id, conversation_id=conversation_id, tool_call_id=tool_call_id
    )
    raw = await redis_client.getdel(key)
    if raw is None:
        return None
    current_key = _current_pending_confirmation_key(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
    )
    raw_current = await redis_client.getdel(current_key)
    if raw_current is not None:
        try:
            current = PendingConfirmationOut.model_validate_json(raw_current)
        except Exception:  # noqa: BLE001 - referencia corrupta, ya consumida
            current = None
        # Solo podría diferir si una continuación más nueva reemplazó la
        # tarjeta visible. En ese caso se restaura esa referencia en vez de
        # borrar una confirmación distinta.
        if current is not None and current.tool_call_id != tool_call_id:
            await redis_client.set(
                current_key,
                current.model_dump_json(),
                ex=PENDING_CONFIRMATION_TTL_SECONDS,
            )
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Idempotencia opcional del turno de chat (Redis / fakeredis).
# ---------------------------------------------------------------------------


def _message_idempotency_key(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    idempotency_key: uuid.UUID,
) -> str:
    return f"chat_idempotency:{tenant_id}:{user_id}:{conversation_id}:{idempotency_key}"


def _message_request_hash(body: ChatMessageIn) -> str:
    canonical = json.dumps(
        body.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _load_idempotency_record(
    redis_client: redis_asyncio.Redis,
    *,
    redis_key: str,
) -> dict[str, Any] | None:
    raw = await redis_client.get(redis_key)
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El estado de este reintento no se puede recuperar con seguridad.",
        ) from exc
    if not isinstance(record, dict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El estado de este reintento no se puede recuperar con seguridad.",
        )
    return record


async def _replay_sse(chunks: list[str]) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


def _resume_response_for_idempotency_record(
    *,
    record: dict[str, Any],
    idempotency_key: uuid.UUID,
) -> JSONResponse | StreamingResponse:
    """Expone el estado de un turno sin volver a enviar su texto original.

    Los clientes móviles solo necesitan persistir la UUID del intento y la
    conversación. Así pueden quedar suspendidos por iOS/Android, regresar
    horas después y recuperar el replay exacto sin guardar prompts ni
    credenciales en disco y sin ejecutar dos veces una herramienta.
    """

    common_headers = {
        "Cache-Control": "no-store",
        "Idempotency-Key": str(idempotency_key),
    }
    if record.get("status") == "in_flight":
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"status": "in_flight"},
            headers={**common_headers, "Retry-After": "1"},
        )
    if record.get("status") != "completed" or not isinstance(record.get("events"), list):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El estado de este turno no se puede recuperar con seguridad.",
        )
    chunks = record["events"]
    if not all(isinstance(chunk, str) for chunk in chunks):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El flujo guardado de este turno está dañado.",
        )
    return StreamingResponse(
        _replay_sse(chunks),
        media_type="text/event-stream",
        headers={
            **common_headers,
            "Idempotency-Replayed": "true",
        },
    )


async def _stream_and_complete_idempotency(
    *,
    stream: AsyncIterator[str],
    redis_client: redis_asyncio.Redis,
    redis_key: str,
    request_hash: str,
    owner_token: str,
    ttl_seconds: int,
    on_disconnected_complete: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[str]:
    """Entrega SSE en vivo y deja un replay completo aunque el cliente se vaya.

    El productor vive en una tarea separada del socket. Si Starlette cancela
    el consumidor porque web/iOS/Android perdió conexión, el ``finally``
    espera al productor bajo ``shield``: así el turno conserva sus
    dependencias request-scoped hasta persistir mensajes, uso y replay. Una
    nueva petición con la misma UUID recibe 409 mientras sigue en vuelo y el
    replay exacto en cuanto termina.
    """

    queue: asyncio.Queue[tuple[str, str | BaseException | None]] = asyncio.Queue()
    chunks: list[str] = []

    async def produce() -> None:
        try:
            async for chunk in stream:
                chunks.append(chunk)
                await queue.put(("chunk", chunk))
            await _complete_message_idempotency(
                redis_client,
                redis_key=redis_key,
                request_hash=request_hash,
                owner_token=owner_token,
                chunks=chunks,
                ttl_seconds=ttl_seconds,
            )
        except BaseException as exc:
            await queue.put(("error", exc))
            raise
        else:
            await queue.put(("done", None))

    producer = asyncio.create_task(produce(), name=f"chat-idempotency:{owner_token}")
    consumer_finished = False
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "chunk":
                assert isinstance(payload, str)
                yield payload
            elif kind == "done":
                consumer_finished = True
                break
            else:
                assert isinstance(payload, BaseException)
                raise payload
    finally:
        # Una cancelación del transporte no debe cancelar el turno que ya fue
        # reclamado. Suprimimos cancelaciones repetidas solo hasta que el
        # productor termine; el CancelledError original del consumidor se
        # propaga después de salir del finally.
        while not producer.done():
            try:
                await asyncio.shield(producer)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if producer.done() and not producer.cancelled():
            producer.exception()  # recupera la excepción y evita warnings de tareas huérfanas
        if (
            not consumer_finished
            and producer.done()
            and not producer.cancelled()
            and producer.exception() is None
            and on_disconnected_complete is not None
        ):
            try:
                await on_disconnected_complete()
            except Exception:
                logger.warning(
                    "No se pudo encolar el aviso de turno terminado tras desconexión.",
                    exc_info=True,
                )


def _response_for_idempotency_record(
    *,
    record: dict[str, Any],
    request_hash: str,
    idempotency_key: uuid.UUID,
) -> StreamingResponse:
    if record.get("request_hash") != request_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key ya fue usado con un mensaje diferente.",
        )
    if record.get("status") == "in_flight":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ese mensaje todavía se está procesando; reintenta con la misma clave.",
            headers={"Retry-After": "1", "Idempotency-Key": str(idempotency_key)},
        )
    if record.get("status") != "completed" or not isinstance(record.get("events"), list):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El estado de este reintento no se puede recuperar con seguridad.",
        )
    chunks = record["events"]
    if not all(isinstance(chunk, str) for chunk in chunks):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El flujo guardado de este reintento está dañado.",
        )
    return StreamingResponse(
        _replay_sse(chunks),
        media_type="text/event-stream",
        headers={
            "Idempotency-Key": str(idempotency_key),
            "Idempotency-Replayed": "true",
        },
    )


async def _claim_message_idempotency(
    redis_client: redis_asyncio.Redis,
    *,
    redis_key: str,
    request_hash: str,
    ttl_seconds: int,
) -> tuple[str | None, dict[str, Any] | None]:
    owner_token = str(uuid.uuid4())
    claimed = await redis_client.set(
        redis_key,
        json.dumps(
            {
                "status": "in_flight",
                "request_hash": request_hash,
                "owner_token": owner_token,
                "created_at": datetime.now(UTC).isoformat(),
            }
        ),
        ex=ttl_seconds,
        nx=True,
    )
    if claimed:
        return owner_token, None
    return None, await _load_idempotency_record(redis_client, redis_key=redis_key)


async def _complete_message_idempotency(
    redis_client: redis_asyncio.Redis,
    *,
    redis_key: str,
    request_hash: str,
    owner_token: str,
    chunks: list[str],
    ttl_seconds: int,
) -> None:
    current = await _load_idempotency_record(redis_client, redis_key=redis_key)
    if current is None or current.get("owner_token") != owner_token:
        raise RuntimeError("Se perdió la propiedad del turno idempotente antes de completarlo.")
    await redis_client.set(
        redis_key,
        json.dumps(
            {
                "status": "completed",
                "request_hash": request_hash,
                "events": chunks,
                "completed_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        ),
        ex=ttl_seconds,
    )


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


def _format_sse(event_name: str, data: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _event_to_dict(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    if hasattr(event, "model_dump"):
        # `mode="json"` convierte UUID/datetime de contratos Pydantic a sus
        # representaciones JSON antes de guardar `tool_calls` en JSONB. El SSE
        # ya toleraba esos tipos con `default=str`; la persistencia no.
        return event.model_dump(mode="json")
    if hasattr(event, "__dict__"):
        return dict(vars(event))
    raise TypeError(f"Evento de agente con forma inesperada: {event!r}")


def _uuid_from_tool_event(
    *, tenant_id: uuid.UUID, conversation_id: uuid.UUID, event: dict[str, Any], kind: str
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Devuelve un UUID opaco estable y, si existe, el artefacto navegable.

    Nunca se encola texto libre: un id de proveedor no UUID solo participa en
    un hash UUIDv5 y no cruza el proceso de API.
    """

    artifacts = event.get("artifacts")
    if isinstance(artifacts, list):
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            try:
                artifact_id = uuid.UUID(str(item.get("file_id")))
            except (TypeError, ValueError):
                continue
            return artifact_id, artifact_id
    opaque_source = ":".join(
        (
            str(tenant_id),
            str(conversation_id),
            str(event.get("tool_call_id") or "tool"),
            kind,
        )
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, f"edecan:{opaque_source}"), None


async def _enqueue_tool_notification(
    *,
    settings: Settings,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    event: dict[str, Any],
) -> None:
    name = str(event.get("name") or "")
    kind = _notification_kind_for_tool(name)
    preview = str(event.get("result_preview") or "").lstrip().lower()
    if kind is None or preview.startswith(("error:", "falló", "no se pudo")):
        return
    event_id, artifact_id = _uuid_from_tool_event(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        event=event,
        kind=kind,
    )
    payload: dict[str, str] = {
        "user_id": str(user_id),
        "kind": kind,
        "event_id": str(event_id),
    }
    if artifact_id is not None:
        payload["artifact_id"] = str(artifact_id)
    elif kind in {"content_created", "design_ready", "design_export_ready"}:
        payload["chat_id"] = str(conversation_id)
    else:
        payload["resource_id"] = str(event_id)
    await enqueue(settings, "notify_important_event", payload, tenant_id)


async def _stream_agent_events(
    *,
    events: AsyncIterator[Any],
    repo: Repo,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    settings: Settings,
    redis_client: redis_asyncio.Redis,
    llm_router: LLMRouter | None = None,
    title_user_text: str | None = None,
    initial_text: str = "",
    initial_tool_log: list[dict[str, Any]] | None = None,
) -> AsyncIterator[str]:
    text_parts: list[str] = [initial_text] if initial_text else []
    tool_log: list[dict[str, Any]] = list(initial_tool_log or [])
    try:
        async for raw_event in events:
            event = _event_to_dict(raw_event)
            event_type = event.get("type", "")
            sse_name = EVENT_NAME_MAP.get(event_type, event_type or "message")
            # ``pending_turn`` contiene historial y estado operativo interno;
            # se persiste en Redis pero nunca cruza el contrato SSE público.
            public_event = dict(event)
            public_event.pop("pending_turn", None)

            if event_type == "text_delta":
                text_parts.append(str(event.get("text", "")))
                yield _format_sse(sse_name, public_event)
            elif event_type in ("tool_start", "tool_end"):
                tool_log.append(event)
                if event_type == "tool_end":
                    try:
                        await _enqueue_tool_notification(
                            settings=settings,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            event=event,
                        )
                    except Exception:
                        logger.warning(
                            "No se pudo encolar la notificación de tool_end "
                            "(tenant_id=%s conversation_id=%s tool=%s)",
                            tenant_id,
                            conversation_id,
                            event.get("name"),
                            exc_info=True,
                        )
                yield _format_sse(sse_name, public_event)
            elif event_type == "tool_progress":
                # Es telemetría efímera para el turno vivo. No se persiste en
                # cada latido para evitar inflar el historial de conversaciones.
                yield _format_sse(sse_name, public_event)
            elif event_type == "done":
                usage = event.get("usage") or {}
                input_tokens = int(usage.get("input_tokens", 0) or 0)
                output_tokens = int(usage.get("output_tokens", 0) or 0)
                await repo.add_message(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content={"text": "".join(text_parts)},
                    tool_calls=tool_log or None,
                    tokens_in=input_tokens,
                    tokens_out=output_tokens,
                )
                total_tokens = input_tokens + output_tokens
                if total_tokens > 0:
                    await repo.add_usage_event(
                        tenant_id=tenant_id,
                        kind="llm_tokens",
                        quantity=float(total_tokens),
                        meta={
                            "conversation_id": str(conversation_id),
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        },
                    )
                await repo.add_usage_event(
                    tenant_id=tenant_id,
                    kind="messages",
                    quantity=1.0,
                    meta={"conversation_id": str(conversation_id)},
                )
                if llm_router is not None and title_user_text:
                    fallback_title = _automatic_conversation_title(title_user_text)
                    semantic_title = await _semantic_conversation_title(
                        llm_router,
                        title_user_text,
                        fallback=fallback_title,
                    )
                    await repo.update_conversation_title(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        title=semantic_title,
                        source="auto",
                    )
                # ARCHITECTURE.md §9: tras persistir el turno, el worker consolida
                # memoria. Best-effort — el turno ya quedó persistido, así que un
                # fallo al encolar (p. ej. cola no configurada en self-host) se
                # registra en logs y no debe convertirse en un error visible para
                # el cliente.
                try:
                    await enqueue(
                        settings, "memory_consolidate", {"user_id": str(user_id)}, tenant_id
                    )
                except Exception:
                    logger.warning(
                        "No se pudo encolar memory_consolidate (tenant_id=%s user_id=%s)",
                        tenant_id,
                        user_id,
                        exc_info=True,
                    )
                # El cliente solo ve `done` cuando mensaje y uso ya existen.
                # Así recargar inmediatamente nunca pierde el turno recién cerrado.
                yield _format_sse(sse_name, public_event)
            elif event_type == "confirmation_required":
                # El agente detiene el turno aquí (ARCHITECTURE.md §10.7): se
                # guarda en Redis el estado completo del loop para este
                # `tool_call_id`; `POST /confirm` reanuda desde esa foto exacta.
                tool_call_id = str(event.get("tool_call_id") or "")
                if tool_call_id:
                    await _store_pending_confirmation(
                        redis_client,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        tool_call_id=tool_call_id,
                        name=str(event.get("name") or ""),
                        args=event.get("args") or {},
                        pending_turn=event.get("pending_turn"),
                    )
                # Persistir antes de publicar evita que un tap inmediato a
                # "Confirmar" compita contra el SET de Redis.
                yield _format_sse(sse_name, public_event)
                break
            else:
                yield _format_sse(sse_name, public_event)
    except Exception as exc:  # pragma: no cover - defensivo, no debería ocurrir en flujo normal
        logger.exception("Error inesperado corriendo el turno del agente")
        yield _format_sse("error", {"type": "error", "message": public_error_message(exc)})


async def _stream_declined_confirmation(
    *, repo: Repo, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> AsyncIterator[str]:
    """El usuario rechazó la herramienta: no se vuelve a invocar al agente."""
    text = "De acuerdo, no realizo esa acción."
    await repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        role="assistant",
        content={"text": text},
    )
    await repo.add_usage_event(
        tenant_id=tenant_id,
        kind="messages",
        quantity=1.0,
        meta={"conversation_id": str(conversation_id)},
    )
    yield _format_sse("message.delta", {"type": "text_delta", "text": text})
    yield _format_sse("message.done", {"type": "done", "usage": {}})


def _is_bare_fix_command(text: str) -> bool:
    """``/fix`` sin contexto no debe abrir un turno LLM potencialmente largo.

    El comando desnudo aparece mucho después de que algo falló. En ese caso el
    servidor puede hacer el preflight local determinista usando el intercambio
    anterior; pedirle primero al modelo que decida qué herramienta invocar
    añade hasta varias rondas de CLI sin producir un solo evento SSE visible.
    """

    return re.fullmatch(r"\s*/fix\s*", text, flags=re.IGNORECASE) is not None


# Palabras clave que dicen "crea un post de LinkedIn" sin ambigüedad. Idénticas
# a las que Aria usa en `app.py:4560` (`features/linkedin_content._route_crear_post`),
# incluyendo la explicación que lo motivó allá: pedirle al modelo que DECIDA qué
# herramienta invocar añade hasta varias rondas donde la persona no ve nada, y
# el modelo -- con `tools` cargadas y un system largo -- se cae a escribir el
# tool_call como texto (`[crear_post_linkedin](tema="...")`) que nadie ejecuta.
# Aria lo evita porque NO le pregunta al modelo cuando ya sabe: detecta la
# intención en el texto, llama al motor y responde al instante "te preparo el
# post". Este atajo copia esa decisión.
# La lista literal de frases no aguantaba cómo se pide esto de verdad. Con
# "post de linkedin" como cadena exacta, "créame un post PERSONAL de LinkedIn"
# NO disparaba —una palabra en medio y el atajo se caía al agente, que escribe el
# tool_call como texto y deja a la persona sin nada—. Se pasa a un patrón que
# tolera calificativos entre la pieza y la plataforma.
_RE_PEDIDO_LINKEDIN = re.compile(
    r"\b(post|publicaci[oó]n|contenido|encuesta)\b[\w\s'\".,-]{0,40}?\blinkedin\b"
    r"|\blinkedin\b[\w\s'\".,-]{0,40}?\b(post|publicaci[oó]n|contenido|encuesta)\b"
    r"|\bpublic(?:a|ar)\s+en\s+linkedin\b",
    re.IGNORECASE,
)

# Una cuenta configurada nombrada junto a "post" también es un pedido inequívoco:
# "un post de Acme sobre X" no dice "LinkedIn" en ninguna parte y aun así no
# hay nada que preguntar. Es como funciona Aria, donde Acme tiene su propio
# motor y su propia ruta.
_RE_PEDIDO_POR_CUENTA = re.compile(
    r"\b(post|publicaci[oó]n|contenido|encuesta)\b[\w\s'\".,-]{0,40}?\b(data\s?cred)\b"
    r"|\b(data\s?cred)\b[\w\s'\".,-]{0,40}?\b(post|publicaci[oó]n|contenido|encuesta)\b",
    re.IGNORECASE,
)


def _es_pedido_directo_de_post_linkedin(text: str) -> bool:
    """`True` si el mensaje es inequívocamente "crea un post de LinkedIn".

    Sigue siendo ESTRICTO en lo que importa: hace falta la pieza ("post",
    "publicación", "contenido", "encuesta") junto a la plataforma o a una cuenta
    conocida. Un pedido ambiguo como "escríbeme algo para publicar" sigue yendo
    al agente normal, que ahí sí tiene que preguntar.
    """
    plano = text or ""
    return bool(_RE_PEDIDO_LINKEDIN.search(plano) or _RE_PEDIDO_POR_CUENTA.search(plano))


# Cuando la card de destino (`_decidir_destino`) le pregunta al usuario "con la
# voz de cuál de tus cuentas lo escribo", el botón que él toca manda un mensaje
# con este formato exacto (`social.py:884`, `value=f"Escríbelo con la voz de
# '{destino['id']}'."`). Ese segundo mensaje también es un caso donde ya
# sabemos qué hacer -- no hay que preguntarle al modelo qué tool llamar --,
# así que también entra por el atajo y no por el agente.
_RE_RESPUESTA_DESTINO_LINKEDIN = re.compile(
    r"escr[ií]belo con la voz de\s*['\"]?([\w.\-]+)['\"]?",
    re.IGNORECASE,
)


def _es_respuesta_a_card_de_destino(text: str) -> str | None:
    """Devuelve el `destino` elegido si el mensaje contesta a la card, o `None`."""
    match = _RE_RESPUESTA_DESTINO_LINKEDIN.search(text or "")
    if not match:
        return None
    destino = match.group(1).strip().lower()
    return destino or None


def _destino_desde_el_texto(text: str) -> str | None:
    """La cuenta que la persona nombró en su propio mensaje, o `None` si no nombró ninguna.

    Así es como Aria resuelve esto en UN mensaje: la cuenta va metida en la
    ruta. Su `linkedin_content.py` ES el motor personal (se dispara con "post de
    linkedin" y nunca pregunta), y Acme vive en un archivo aparte,
    `acme_linkedin_content.py`, con su propia ruta. No hay ambigüedad que
    resolver, así que no hay nada que preguntar.

    Edecán hizo UNA tool para las dos cuentas y por eso terminaba preguntando
    siempre, incluso cuando la respuesta era obvia. Con esto vuelve al
    comportamiento de Aria: si la persona nombró la cuenta, se usa; y si pidió
    un post "de LinkedIn" sin más, es el personal, igual que allá. La tarjeta
    queda para la duda real, no para el caso de todos los días.
    """
    descompuesto = unicodedata.normalize("NFKD", (text or "").casefold())
    plano = "".join(c for c in descompuesto if not unicodedata.combining(c))
    # Cualquier nombre de organización configurado por el tenant se detecta en
    # `_decidir_destino` con su config real; acá solo se resuelve lo que se puede
    # decidir sin consultarla, que es el caso frecuente.
    if re.search(r"\bdata\s?cred\b", plano):
        return "acme"
    if re.search(r"\b(personal|mi perfil|mi cuenta personal)\b", plano):
        return "personal"
    return None


def _extraer_tema_del_historial_linkedin(history: list[ChatMessage]) -> str | None:
    """Recupera el `tema` del pedido original al leer hacia atrás en el hilo.

    La card de destino corta el turno pidiéndole a la persona que elija; cuando
    contesta, el mensaje NO trae el tema otra vez -- vive en el turno anterior
    ("Créame un post de LinkedIn sobre X"). Sin esto, el motor se activa sin
    tema y cae a su rotación editorial, escribiendo sobre algo que la persona
    no pidió. Se busca el ÚLTIMO mensaje de usuario que traía el pedido
    original, no cualquier mención pasada."""
    for message in reversed(history):
        if message.role != "user":
            continue
        contenido = str(message.content or "")
        if not _es_pedido_directo_de_post_linkedin(contenido):
            continue
        tema = _extraer_tema_de_post_linkedin(contenido)
        if tema:
            return tema
        return None
    return None


# Reglas para sacar el tema del propio mensaje, en el mismo orden que Aria
# (`features/linkedin_content._extraer_tema_pedido`). "Sobre X", "de X",
# "acerca de X", "sobre el tema X". Sin match, el motor rota su calendario
# editorial como hace Aria con los pilares.
_TEMA_LINKEDIN_RE = re.compile(
    r"(?:sobre|acerca de|de|del|acerca del|sobre el tema)\s+(.{3,140}?)(?:[.?!]|$)",
    re.IGNORECASE,
)


def _extraer_tema_de_post_linkedin(text: str) -> str | None:
    """Tema explícito ("sobre tecnología") o `None` si el mensaje no lo trae.

    El motor sabe qué hacer sin tema (rota su calendario editorial). Pero
    inventar uno cuando la persona no lo dio arruina el post -- el motor
    escribiría sobre algo que la persona no pidió y la conversación no sabe
    ni cómo corregirlo.
    """
    limpio = re.sub(r"\s+", " ", (text or "").strip())
    # Se busca el tema DESPUÉS de la parte que identifica el pedido, para no
    # confundir "un post de LinkedIn" con el tema en sí. Se prueban los dos
    # patrones (plataforma y cuenta) porque cualquiera de los dos pudo disparar
    # el atajo, y gana el que aparezca primero en la frase.
    fin = None
    for patron in (_RE_PEDIDO_LINKEDIN, _RE_PEDIDO_POR_CUENTA):
        match_pedido = patron.search(limpio)
        if match_pedido and (fin is None or match_pedido.end() < fin):
            fin = match_pedido.end()
    if fin is None:
        return None
    match = _TEMA_LINKEDIN_RE.search(limpio[fin:])
    if not match:
        return None
    tema = match.group(1).strip().rstrip(",;: ")
    return tema if 3 <= len(tema) <= 140 else None


# Marcas de que una frase del `ToolResult.content` está dirigida al MODELO y no a
# la persona: nombres de tools entre comillas, órdenes sobre llamar o terminar el
# turno, y referencias al usuario en tercera persona. `content` es el canal
# modelo-facing, así que normalmente lo lee el modelo y traduce; en el ATAJO no
# hay modelo en medio y se le volcaba crudo a la persona. Ada vio en su chat
# "vuelve a llamar 'crear_post_linkedin' con ese 'tema'. No repitas la llamada",
# instrucciones para una máquina, escritas como si él invocara herramientas.
_FRASES_PARA_EL_MODELO = re.compile(
    r"""(
        '[a-z_]+_[a-z_]+'          # un nombre de tool entre comillas simples
      | \bvuelve\s+a\s+llamar\b
      | \bno\s+repitas\s+la\s+llamada\b
      | \btermina\s+tu\s+turno\b
      | \bhaz\s+una\s+de\s+estas\b
      | \beste\s+usuario\b
      | \bqué\s+hacer:
      | \bsin\s+texto\s+adicional\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)


# Las correcciones que el motor le pasa al ESCRITOR viajan en el mismo `content`,
# como viñetas en imperativo ("Quita la primera persona...", "Reescribe la idea
# como..."). Son órdenes para quien redacta, no información para la persona que
# pidió el post: verlas es como recibir las notas del editor en vez del artículo.
_CORRECCION_AL_ESCRITOR = re.compile(
    r"^\s*[-*•]?\s*(quita|reescribe|elimina|cambia|corrige|añade|agrega|evita|usa)\b",
    re.IGNORECASE,
)


def _texto_para_la_persona(contenido: str, *, respaldo: str) -> str:
    """Deja del `ToolResult.content` solo lo que tiene sentido leerle a la persona.

    Dos filtros: se descartan los párrafos dirigidos al modelo (ver
    `_FRASES_PARA_EL_MODELO`) y, dentro de los que sobreviven, las viñetas de
    corrección al escritor (ver `_CORRECCION_AL_ESCRITOR`). Si no queda nada
    legible se devuelve `respaldo`: una frase corta y honesta es mejor que un
    informe de control de calidad.
    """
    utiles: list[str] = []
    for parrafo in re.split(r"\n\s*\n", contenido):
        parrafo = parrafo.strip()
        if not parrafo or _FRASES_PARA_EL_MODELO.search(parrafo):
            continue
        lineas = [
            linea
            for linea in parrafo.splitlines()
            if linea.strip() and not _CORRECCION_AL_ESCRITOR.match(linea)
        ]
        if lineas:
            utiles.append(_sin_anuncio_huerfano("\n".join(lineas).strip()))
    utiles = [p for p in utiles if p]
    return "\n\n".join(utiles) if utiles else respaldo


def _sin_anuncio_huerfano(parrafo: str) -> str:
    """Quita el "Lo que falló la última vez:" que quedó anunciando una lista que
    el filtro de correcciones ya se llevó. Un párrafo que termina en dos puntos
    promete algo que no viene detrás."""
    if not parrafo.endswith(":"):
        return parrafo
    corte = max(parrafo.rfind(". "), parrafo.rfind("? "), parrafo.rfind("! "))
    return parrafo[: corte + 1].strip() if corte != -1 else ""


async def _stream_direct_linkedin_post(
    *,
    tool: Tool | None,
    ctx: ToolContext,
    text: str,
    tema_override: str | None = None,
    destino_override: str | None = None,
    repo: Repo,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    settings: Settings,
    conversation_id: uuid.UUID,
) -> AsyncIterator[str]:
    """Genera el post en el mismo turno, sin pasar por el modelo para decidir.

    Copia el patrón de `features/linkedin_content._route_crear_post` de Aria.
    La diferencia con "encolar en background y responder después" es
    deliberada: el motor nuevo (`crear_post_linkedin`) hace el ciclo entero en
    ~pocos segundos porque investiga, redacta y audita en código; puede correr
    dentro del propio request sin colgarlo. Si algún día pasa a tardar
    minutos, este es el sitio donde hay que meter `asyncio.create_task` y un
    push posterior.

    `tema_override` y `destino_override` se usan cuando la persona contesta la
    card de destino: el tema viene del turno original (que la card cortó) y el
    destino de la opción que tocó. En el primer mensaje ambos son `None` y se
    sacan del texto o los propone la tool.

    Si la tool no existe (paquete `edecan_creative` no cargado), se cierra el
    stream con un mensaje persistido -- misma disciplina que `_stream_bare_fix
    _diagnosis`: nunca dejar el cliente en "Edecán está pensando".
    """
    tema = tema_override or _extraer_tema_de_post_linkedin(text)
    tool_call_id = f"linkedin-directo-{uuid.uuid4().hex[:12]}"
    tool_name = "crear_post_linkedin"
    args: dict[str, Any] = {"plataforma": "linkedin"}
    if tema:
        args["tema"] = tema
    if destino_override:
        args["destino"] = destino_override
    args_publicos = {k: v for k, v in (("tema", tema), ("destino", destino_override)) if v}

    yield _format_sse(
        "tool.start",
        {
            "type": "tool_start",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "args": args_publicos,
        },
    )

    presentation: list[dict[str, Any]] = []
    if tool is None:
        detail = (
            "El motor de LinkedIn no está disponible en esta instalación. "
            "No preparé nada."
        )
    else:
        try:
            # 120 s cubre el ciclo entero (investigar + redactar + auditar +
            # imagen). Si vence, el usuario recibe un mensaje claro en vez del
            # cliente colgado. `_stream_bare_fix_diagnosis` usa 20 s porque su
            # tool es local; esta habla con proveedores externos.
            result = await asyncio.wait_for(tool.run(ctx, args), timeout=120.0)
            presentation = list(result.presentation or [])
            # `content` está escrito para el modelo; acá no hay modelo que lo
            # traduzca, así que se filtra antes de mostrarlo (ver
            # `_texto_para_la_persona`). El respaldo depende del tipo de bloque
            # que trajo el motor:
            #  - `social_draft` = borrador listo: la tarjeta habla, sobra texto.
            #  - `question`     = tarjeta de destino: ella misma tiene la
            #                     pregunta con sus botones; no hace falta un
            #                     mensaje "no logré armar el post" que confunda.
            #  - nada           = el motor no pudo hacer nada, respaldo real.
            hubo_borrador = any(
                isinstance(b, dict) and b.get("type") == "social_draft" for b in presentation
            )
            hubo_pregunta = any(
                isinstance(b, dict) and b.get("type") == "question" for b in presentation
            )
            if hubo_borrador:
                respaldo = "Aquí tienes el borrador."
            elif hubo_pregunta:
                # La tarjeta ya lleva el texto de la pregunta; un mensaje extra
                # duplica y confunde. Vacío es lo correcto aquí.
                respaldo = ""
            else:
                respaldo = (
                    "No logré armar el post esta vez. Dame un ángulo más "
                    "concreto — una decisión, un hecho, a quién le cambia "
                    "algo — y lo intento de nuevo."
                )
            detail = _texto_para_la_persona(result.content.strip(), respaldo=respaldo)
        except TimeoutError:
            detail = (
                "La preparación del post tardó más de dos minutos y fue "
                "detenida. No preparé nada."
            )
        except Exception as exc:  # noqa: BLE001 - cierre visible y seguro
            logger.warning("Falló el atajo directo de post de LinkedIn", exc_info=True)
            detail = (
                "No pude preparar el post de LinkedIn: "
                f"{public_error_message(exc)}"
            )

    # El `tool_end` — vivo Y persistido — lleva los bloques (la tarjeta) DENTRO,
    # con `blocks_version`. Ese es el ÚNICO canal que los clientes leen: iOS en
    # su handler `.toolEnd` (ChatViewModel: `if blocksVersion == 1 { ...bloques }`)
    # y web con `messageBlocks(message.tool_calls)` sobre el toolLog acumulado.
    # NINGUNO maneja un evento `message.block` suelto. Mandarlos aparte (como se
    # hacía antes) hacía que la tarjeta se guardara bien pero en VIVO no se
    # pintara: el iPhone mostraba "Trabajo completado" y nada más, porque el
    # tool_end vivo iba vacío. El mismo dict se emite y se persiste, así live y
    # recarga muestran exactamente lo mismo.
    evento_tool_end: dict[str, Any] = {
        "type": "tool_end",
        "tool_call_id": tool_call_id,
        "name": tool_name,
        "result_preview": detail[:_RESULT_PREVIEW_LEN],
    }
    if presentation:
        evento_tool_end["blocks_version"] = 1
        evento_tool_end["blocks"] = presentation
    yield _format_sse("tool.end", evento_tool_end)

    # El mensaje del asistente conserva EXACTAMENTE lo que devolvió el motor: el
    # modelo NO lo reformula. La tarjeta (SocialDraftBlock o QuestionBlock) trae
    # sus propios botones según el destino.
    contenido: dict[str, Any] = {"text": detail}
    if presentation:
        contenido["presentation"] = presentation
    await repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        role="assistant",
        content=contenido,
        tool_calls=[evento_tool_end],
    )
    await repo.add_usage_event(
        tenant_id=tenant_id,
        kind="messages",
        quantity=1.0,
        meta={"conversation_id": str(conversation_id)},
    )
    if detail:
        yield _format_sse("message.delta", {"type": "text_delta", "text": detail})
    yield _format_sse("message.done", {"type": "done", "usage": {}})
    # Push al teléfono cuando el post quedó listo -- así la persona no depende
    # de tener la app abierta mirando el chat. Solo dispara si SÍ hay una
    # tarjeta de borrador (`presentation` no vacía y no es solo una pregunta):
    # una respuesta de error o la card de destino no son "content_created".
    if presentation and any(
        isinstance(b, dict) and b.get("type") == "social_draft" for b in presentation
    ):
        try:
            await _enqueue_tool_notification(
                settings=settings,
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
                event={
                    "type": "tool_end",
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "result_preview": detail[:_RESULT_PREVIEW_LEN],
                },
            )
        except Exception:  # noqa: BLE001 - la notificación nunca tumba el turno
            logger.warning("no se pudo encolar el push del post", exc_info=True)


async def _stream_bare_fix_diagnosis(
    *,
    tool: Tool | None,
    ctx: ToolContext,
    history: list[ChatMessage],
    repo: Repo,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> AsyncIterator[str]:
    """Diagnóstico terminal y visible para ``/fix``.

    Nunca modifica código. Si existe el diagnóstico local lo ejecuta con un
    límite corto; cualquier configuración ausente o excepción se convierte en
    una respuesta persistida y en ``message.done``. Así ningún cliente queda
    indefinidamente en estado "Edecán está pensando".
    """

    previous_user = next(
        (
            str(message.content)
            for message in reversed(history)
            if message.role == "user" and str(message.content).strip()
        ),
        "La acción anterior",
    )
    previous_assistant = next(
        (
            str(message.content)
            for message in reversed(history)
            if message.role == "assistant" and str(message.content).strip()
        ),
        "No se recibió un detalle del fallo.",
    )
    tool_call_id = f"fix-preflight-{uuid.uuid4().hex[:12]}"
    tool_name = "diagnosticar_autorreparacion_local"
    args = {
        "intencion_original": previous_user[:2000],
        "fallo_reportado": previous_assistant[:4000],
        "categoria": "incierta",
    }
    yield _format_sse(
        "tool.start",
        {
            "type": "tool_start",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "args": {"categoria": "incierta"},
        },
    )

    if tool is None:
        detail = (
            "El diagnóstico local no está disponible en esta instalación. No cambié ningún archivo."
        )
        data: dict[str, Any] = {}
    else:
        try:
            result = await asyncio.wait_for(tool.run(ctx, args), timeout=20.0)
            detail = result.content.strip() or "El diagnóstico terminó sin observaciones."
            data = result.data or {}
        except TimeoutError:
            detail = (
                "El diagnóstico local tardó más de 20 segundos y fue detenido. "
                "No cambié ningún archivo."
            )
            data = {}
        except Exception as exc:  # noqa: BLE001 - cierre visible y seguro del comando
            logger.warning("Falló el preflight determinista de /fix", exc_info=True)
            detail = f"No pude completar el diagnóstico local: {public_error_message(exc)}"
            data = {}

    yield _format_sse(
        "tool.end",
        {
            "type": "tool_end",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "result_preview": detail[:_RESULT_PREVIEW_LEN],
        },
    )
    if data.get("source_repair_ready"):
        response_text = (
            f"{detail}\n\nEl código local está listo para una reparación aislada. "
            "Escribe qué comportamiento falló después de `/fix`, por ejemplo: "
            "`/fix al adjuntar una foto no se abre el selector`."
        )
    else:
        response_text = (
            f"{detail}\n\nPara continuar sin adivinar, escribe el fallo junto al comando, "
            "por ejemplo: `/fix al adjuntar una foto no se abre el selector`."
        )
    await repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        role="assistant",
        content={"text": response_text},
        tool_calls=[
            {
                "type": "tool_end",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "result_preview": detail[:_RESULT_PREVIEW_LEN],
            }
        ],
    )
    await repo.add_usage_event(
        tenant_id=tenant_id,
        kind="messages",
        quantity=1.0,
        meta={"conversation_id": str(conversation_id)},
    )
    yield _format_sse("message.delta", {"type": "text_delta", "text": response_text})
    yield _format_sse("message.done", {"type": "done", "usage": {}})


async def _stream_inline_credential_configuration(
    *,
    intent: InlineCredentialIntent,
    tool: Tool | None,
    ctx: ToolContext,
    repo: Repo,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> AsyncIterator[str]:
    """Configura una key explícita sin entregársela al LLM ni al SSE.

    El usuario ya dio una instrucción inequívoca al pegar su propia clave.
    Este camino usa la misma tool y el mismo TokenVault que Ajustes, pero los
    argumentos públicos contienen solo el nombre del proveedor. El texto
    original ya fue persistido redactado por ``post_message``.
    """

    call_id = f"credential-{uuid.uuid4()}"
    public_args = {"provider": intent.provider, "secret": "[credencial protegida]"}
    yield _format_sse(
        "tool.start",
        {
            "type": "tool_start",
            "tool_call_id": call_id,
            "name": "configurar_credencial",
            "args": public_args,
        },
    )

    if tool is None:
        response_text = (
            "No pude abrir el almacén seguro de credenciales. No guardé la clave y la "
            "oculté del historial."
        )
        succeeded = False
    else:
        try:
            result = await tool.run(ctx, intent.tool_args)
            response_text = redact_values(result.content, intent.secret_values)
            succeeded = not response_text.casefold().startswith(
                ("error:", "no pude", "no tengo", "falta ")
            )
        except Exception:  # noqa: BLE001 - el secreto nunca debe reflejarse al cliente
            logger.exception(
                "Falló la configuración segura de una credencial provider=%s",
                intent.provider,
            )
            response_text = (
                f"No pude configurar {intent.display_name}. No guardé la clave y la oculté "
                "del historial."
            )
            succeeded = False

    await repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        role="assistant",
        content={"text": response_text},
    )
    await repo.add_usage_event(
        tenant_id=tenant_id,
        kind="messages",
        quantity=1.0,
        meta={"conversation_id": str(conversation_id), "mode": "credential_setup"},
    )
    try:
        await repo.add_audit_log(
            tenant_id=tenant_id,
            actor_user_id=user_id,
            action=("credentials.chat.configured" if succeeded else "credentials.chat.failed"),
            target=intent.provider,
        )
    except Exception:  # pragma: no cover - auditoría best-effort en fakes antiguos
        logger.warning("No se pudo registrar la auditoría de credencial", exc_info=True)

    yield _format_sse(
        "tool.end",
        {
            "type": "tool_end",
            "tool_call_id": call_id,
            "name": "configurar_credencial",
            "result_preview": response_text[:_RESULT_PREVIEW_LEN],
            "artifacts": [],
            "blocks_version": 1,
            "blocks": [],
            "mission_id": None,
        },
    )
    yield _format_sse("message.delta", {"type": "text_delta", "text": response_text})
    yield _format_sse("message.done", {"type": "done", "usage": {}})


async def _stream_approved_confirmation(
    *,
    tool_call_id: str,
    tool: Tool,
    tool_name: str,
    tool_args: dict[str, Any],
    ctx: ToolContext,
    repo: Repo,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    settings: Settings,
) -> AsyncIterator[str]:
    """El usuario aprobó la tool `dangerous` pendiente: se ejecuta DIRECTO con
    la tool/args que el modelo propuso originalmente (recuperados de Redis por
    `confirm_tool_call`) en vez de volver a llamar al LLM — una llamada nueva
    acuñaría un `tool_call_id` distinto que el gate de confirmación jamás
    reconocería como aprobado (ver el docstring del módulo)."""
    try:
        yield _format_sse(
            "tool.start",
            {
                "type": "tool_start",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "args": tool_args,
            },
        )
        task = asyncio.create_task(tool.run(ctx, tool_args))
        started_at = asyncio.get_running_loop().time()
        try:
            while True:
                try:
                    result = await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
                    break
                except TimeoutError:
                    elapsed = max(0, int(asyncio.get_running_loop().time() - started_at))
                    yield _format_sse(
                        "tool.progress",
                        {
                            "type": "tool_progress",
                            "tool_call_id": tool_call_id,
                            "name": tool_name,
                            "elapsed_seconds": elapsed,
                            "message": "Edecán sigue trabajando",
                        },
                    )
        except Exception as exc:  # noqa: BLE001 - una tool nunca debe tumbar el turno
            logger.warning(
                "La herramienta aprobada %r lanzó una excepción", tool_name, exc_info=True
            )
            result = ToolResult(content=f"Error: {exc}")
        finally:
            if not task.done():
                task.cancel()

        preview = result.content[:_RESULT_PREVIEW_LEN]
        artifacts = artifact_refs_from_tool_data(result.data)
        visible_artifacts = (
            []
            if isinstance(result.data, dict) and bool(result.data.get("suppress_chat_artifacts"))
            else artifacts
        )
        blocks = rich_blocks_from_tool_data(
            result.data,
            presentation=result.presentation,
            artifacts=artifacts,
            tool_name=tool_name,
        )
        tool_end = {
            "type": "tool_end",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "result_preview": preview,
            "artifacts": [item.model_dump(mode="json") for item in visible_artifacts],
            "blocks_version": 1,
            "blocks": [item.model_dump(mode="json") for item in blocks],
            "mission_id": (
                str(mission_id) if (mission_id := mission_ref_from_tool_data(result.data)) else None
            ),
        }
        try:
            await _enqueue_tool_notification(
                settings=settings,
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
                event=tool_end,
            )
        except Exception:
            logger.warning(
                "No se pudo encolar la notificación de la herramienta aprobada "
                "(tenant_id=%s conversation_id=%s tool=%s)",
                tenant_id,
                conversation_id,
                tool_name,
                exc_info=True,
            )
        yield _format_sse("tool.end", tool_end)

        text = f"Listo, ejecuté «{tool_name}». {preview}".strip()
        tool_log = [
            {
                "type": "tool_start",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "args": tool_args,
            },
            tool_end,
        ]
        await repo.add_message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role="assistant",
            content={"text": text},
            tool_calls=tool_log,
        )
        await repo.add_usage_event(
            tenant_id=tenant_id,
            kind="messages",
            quantity=1.0,
            meta={"conversation_id": str(conversation_id)},
        )
        yield _format_sse("message.delta", {"type": "text_delta", "text": text})
        yield _format_sse("message.done", {"type": "done", "usage": {}})
    except Exception as exc:  # pragma: no cover - defensivo, no debería ocurrir en flujo normal
        logger.exception("Error inesperado ejecutando la tool aprobada")
        yield _format_sse("error", {"type": "error", "message": public_error_message(exc)})


async def _extra_mcp_tools_or_empty(request: Request, current_user: CurrentUser) -> list[Any]:
    """`get_mcp_tools_for_tenant` (`edecan_api.deps`) YA falla abierto
    internamente ante cualquier error (flag apagado, `edecan_mcp` no
    instalado, servidor MCP caído, vault/sesión rotos) — este wrapper es una
    SEGUNDA capa de defensa, redundante a propósito: si esa función de todos
    modos llegara a lanzar (p. ej. un bug futuro que rompa su propio
    `try/except`), el turno de chat sigue funcionando sin las tools MCP de
    esta vuelta en vez de devolver un `500` — un servidor MCP mal configurado
    NUNCA debe poder tumbar el chat completo del tenant.
    """
    try:
        return await get_mcp_tools_for_tenant(request, current_user)
    except Exception:  # noqa: BLE001 - fail-open explícito, ver docstring
        logger.warning(
            "get_mcp_tools_for_tenant lanzó una excepción inesperada (debería fallar "
            "abierto por su cuenta); el turno sigue sin tools MCP.",
            exc_info=True,
        )
        return []


async def _extra_conversation_tools(request: Request, current_user: CurrentUser) -> list[Any]:
    """Tools locales de preferencias + MCP efímeras de este tenant.

    Las primeras siempre están disponibles: no dependen de conectores ni de
    red. Las MCP conservan su aislamiento y su comportamiento fail-open.
    """
    return [*conversation_persona_tools(), *await _extra_mcp_tools_or_empty(request, current_user)]


def _tool_requires_flags_satisfechos(tool: Any, flags: dict[str, Any]) -> bool:
    """`True` solo si TODOS los `requires_flags` de `tool` están presentes en
    `flags` con un valor verdadero — mismo criterio que
    `edecan_core.tools.registry.ToolRegistry._flags_satisfechos` (el filtro
    que aplica `specs()`) y que `edecan_core.agent._extra_tools_disponibles`.

    Existe PORQUE `confirm_tool_call` nunca vuelve a invocar `Agent.run_turn`
    (ver el docstring del módulo): resuelve la tool pendiente con
    `ToolRegistry.get(name)`, que busca por nombre contra el registro
    COMPLETO sin filtrar por flags (el filtro de `specs(flags)` solo decide
    qué se OFRECE al modelo, nunca qué se puede ejecutar por nombre) — sin
    este chequeo aparte, un tenant cuyo plan no incluye una tool `dangerous`
    (p. ej. `commerce.orders=False` y una `preparar_pago` que de todos modos
    quedó pendiente de confirmar) podría ejecutarla igual con solo aprobar la
    tarjeta de confirmación. `getattr(tool, "requires_flags", frozenset())`
    en vez de `tool.requires_flags` directo porque `tool` puede llegar acá
    como una `Tool` real (siempre lo declara), una tool MCP bring-your-own
    (`edecan_mcp.tool_adapter`, también lo declara) o, en tests, un doble que
    no necesariamente lo hace — mismo criterio defensivo que
    `RestrictedRegistry.get()` (`edecan_agents.registry_view`) usa para
    `getattr(tool, "dangerous", False)`.
    """
    requires_flags = getattr(tool, "requires_flags", frozenset())
    return all(bool(flags.get(flag_name)) for flag_name in requires_flags)


def _preflight_pending_turn(
    *,
    pending: PendingAgentTurn,
    registry: Any,
    extra_tools: list[Any],
    flags: dict[str, Any],
    plan_key: str,
) -> None:
    """Falla antes de abrir SSE si el lote ya no conserva sus permisos.

    ``Agent.resume_turn`` repite esta validación justo antes de ejecutar; esta
    capa HTTP mantiene además los códigos 409/403 del endpoint histórico.
    """
    operational_names = set(pending.operational_tool_names)
    extra_by_name = {tool.name: tool for tool in extra_tools}
    for call in pending.tool_calls:
        if call.name not in operational_names:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"La herramienta «{call.name}» no fue ofrecida en el turno original.",
            )
        tool = registry.get(call.name) or extra_by_name.get(call.name)
        if tool is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"La herramienta «{call.name}» ya no está disponible.",
            )
        if not _tool_requires_flags_satisfechos(tool, flags):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"La herramienta «{call.name}» no está disponible en tu plan '{plan_key}'."
                ),
            )


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


@router.get(
    "/{conversation_id}/message-attempts/{idempotency_key}",
    response_model=None,
)
async def resume_message_attempt(
    conversation_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_streaming_repo),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> JSONResponse | StreamingResponse:
    """Recupera un turno después de suspender o cerrar el cliente móvil.

    No acepta el body original a propósito. La identidad autenticada, el
    tenant, la conversación y la UUID idempotente forman la única clave
    necesaria para consultar un intento ya reclamado.
    """

    conversation = await repo.get_conversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    redis_key = _message_idempotency_key(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
        idempotency_key=idempotency_key,
    )
    record = await _load_idempotency_record(redis_client, redis_key=redis_key)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Ese turno ya no está disponible para reanudación.",
        )
    return _resume_response_for_idempotency_record(
        record=record,
        idempotency_key=idempotency_key,
    )


@router.post("/{conversation_id}/messages")
async def post_message(
    conversation_id: uuid.UUID,
    body: ChatMessageIn,
    request: Request,
    idempotency_key: uuid.UUID | None = Header(default=None, alias="Idempotency-Key"),
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_streaming_repo),
    session: Any = Depends(get_tenant_session, scope="request"),
    llm_router: LLMRouter = Depends(get_llm_router),
    vault: Any = Depends(get_streaming_vault),
    settings: Settings = Depends(get_settings),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> StreamingResponse:
    tenant = current_user.tenant
    conversation = await repo.get_conversation(
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")

    # El override por turno se valida ANTES de cualquier efecto: un id fuera de
    # catálogo no debe llegar a consumir cuota ni a insertar el mensaje.
    if not modelo_chat_permitido(body.model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Ese modelo no está en el catálogo del chat.",
        )

    request_hash: str | None = None
    redis_idempotency_key: str | None = None
    idempotency_ttl = max(60, int(settings.CHAT_IDEMPOTENCY_TTL_SECONDS))
    if idempotency_key is not None:
        request_hash = _message_request_hash(body)
        redis_idempotency_key = _message_idempotency_key(
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )
        existing = await _load_idempotency_record(
            redis_client,
            redis_key=redis_idempotency_key,
        )
        if existing is not None:
            return _response_for_idempotency_record(
                record=existing,
                request_hash=request_hash,
                idempotency_key=idempotency_key,
            )

    await _check_message_quota(repo, tenant)

    history_rows = await repo.list_messages(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        limit=max(50, int(settings.CHAT_CONTEXT_MAX_MESSAGES)),
    )
    cross_chat_rows: list[dict[str, Any]] = []
    if settings.CHAT_CONTEXT_ENABLED and settings.CHAT_CONTEXT_CROSS_CHAT_ENABLED:
        cross_chat_rows = await repo.list_cross_chat_message_snippets(
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            exclude_conversation_id=conversation_id,
            conversations_limit=settings.CHAT_CONTEXT_CROSS_CHAT_CONVERSATIONS,
            messages_per_conversation=settings.CHAT_CONTEXT_CROSS_CHAT_MESSAGES_PER_CONVERSATION,
        )
    history = build_contextual_history(
        current_rows=history_rows,
        cross_chat_rows=cross_chat_rows,
        limits=ChatContextLimits(
            enabled=settings.CHAT_CONTEXT_ENABLED,
            recent_messages=settings.CHAT_CONTEXT_RECENT_MESSAGES,
            max_messages=settings.CHAT_CONTEXT_MAX_MESSAGES,
            max_chars=settings.CHAT_CONTEXT_MAX_CHARS,
            cross_chat_enabled=settings.CHAT_CONTEXT_CROSS_CHAT_ENABLED,
            cross_chat_conversations=settings.CHAT_CONTEXT_CROSS_CHAT_CONVERSATIONS,
            cross_chat_messages_per_conversation=(
                settings.CHAT_CONTEXT_CROSS_CHAT_MESSAGES_PER_CONVERSATION
            ),
            cross_chat_max_chars=settings.CHAT_CONTEXT_CROSS_CHAT_MAX_CHARS,
        ),
    )

    attachments = await _resolve_message_attachments(
        repo=repo,
        tenant_id=tenant.tenant_id,
        file_ids=body.attachments,
    )
    inline_credential = detect_inline_credential_intent(body.text) if not attachments else None
    # Toda credencial reconocible se redacta ANTES de título, base de datos,
    # historial LLM y SSE. La detección de intención conserva el valor crudo
    # solo en memoria para el vault; un segundo mensaje que únicamente discuta
    # una clave también debe quedar protegido aunque no configure nada.
    safe_user_text = (
        inline_credential.redacted_text if inline_credential is not None else redact(body.text)
    )
    stored_user_content: dict[str, Any] = {"text": safe_user_text}
    if attachments:
        stored_user_content["attachments"] = attachments
    user_text = _extract_text(stored_user_content)
    direct_user_content = await _direct_multimodal_content(
        settings=settings,
        user_text=user_text,
        attachments=attachments,
        repo=repo,
        tenant_id=tenant.tenant_id,
    )

    persona_row = await repo.get_persona(tenant_id=tenant.tenant_id, user_id=current_user.user_id)
    persona = persona_from_row(persona_row)
    # El proceso real siempre recibe una AsyncSession tenant-scoped. El
    # repositorio de pruebas de conversaciones usa históricamente ``None``
    # porque no toca SQL; conservar ese doble evita acoplar todo el contrato
    # SSE a una base falsa solo por el perfil opcional.
    profile_context = (
        await profile_context_for(session, tenant.tenant_id, current_user.user_id)
        if session is not None
        else ""
    )

    registry = get_tool_registry(request)
    agent = Agent(llm_router, registry)
    ctx = _build_ctx(
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        session=session,
        settings=settings,
        llm_router=llm_router,
        vault=vault,
        persona=persona,
        request=request,
        repo=repo,
        approved_tool_calls=set(),
        flags=tenant.flags,
        conversation_id=conversation_id,
        phone_call_dispatcher=phone_tool_dispatcher_for(
            request=request,
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            repo=repo,
            vault=vault,
        ),
        profile_context=profile_context,
    )
    if not isinstance(direct_user_content, str):
        ctx.extras["direct_user_content"] = direct_user_content
    # Quien preguntó el turno pasado tiene que poder oír la respuesta de este:
    # el selector de capacidades vuelve a ofrecer esa tool sin depender de que
    # el usuario repita las palabras clave al contestar (ver el helper).
    ctx.extras["tools_con_pregunta_pendiente"] = _tools_con_pregunta_pendiente(history_rows)
    # MCP bring-your-own (ARCHITECTURE.md §15): tools de los servidores MCP
    # que el tenant conectó, fusionadas SOLO para este turno — nunca tocan el
    # `registry` compartido (ver `Agent.run_turn`/`get_mcp_tools_for_tenant`).
    # `_extra_mcp_tools_or_empty` es fail-open con dos capas (ver su docstring).
    extra_tools = await _extra_conversation_tools(request, current_user)

    owner_token: str | None = None
    if idempotency_key is not None:
        assert request_hash is not None and redis_idempotency_key is not None
        owner_token, raced_record = await _claim_message_idempotency(
            redis_client,
            redis_key=redis_idempotency_key,
            request_hash=request_hash,
            ttl_seconds=idempotency_ttl,
        )
        if raced_record is not None:
            return _response_for_idempotency_record(
                record=raced_record,
                request_hash=request_hash,
                idempotency_key=idempotency_key,
            )
        if owner_token is None:  # pragma: no cover - defensa ante un Redis incompatible
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No se pudo reclamar este turno idempotente.",
            )

    # La reclamación atómica ocurre inmediatamente antes del primer efecto
    # persistente. Dos requests concurrentes con la misma clave nunca insertan
    # dos mensajes de usuario ni arrancan dos turnos del agente.
    needs_semantic_title = not str(conversation.get("title") or "").strip()
    if needs_semantic_title:
        automatic_title = (
            _credential_conversation_title(inline_credential)
            if inline_credential is not None
            else _automatic_conversation_title(safe_user_text, fallback="Archivo adjunto")
        )
        await repo.update_conversation_title(
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            conversation_id=conversation_id,
            title=automatic_title,
            only_if_empty=True,
            source="auto",
        )
    # Selector de modelos: el body del turno gana sobre lo persistido y TAMBIÉN
    # se persiste, para que elegir-y-enviar en un solo gesto no dependa de una
    # carrera entre el PUT `/model` y este POST. Un campo ausente conserva lo
    # que la conversación ya tenía (volver a automático es el PUT con `null`).
    modelo_elegido = conversation.get("chat_model") or None
    esfuerzo_elegido = conversation.get("chat_effort") or None
    if body.model is not None or body.effort is not None:
        modelo_elegido = body.model.strip() if body.model else modelo_elegido
        esfuerzo_elegido = body.effort or esfuerzo_elegido
        await repo.update_conversation_model(
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            conversation_id=conversation_id,
            model=modelo_elegido,
            effort=esfuerzo_elegido,
        )
    # El gate de ceguera y el de Esfuerzo son SOLO de este turno: la selección
    # persistida arriba no se toca (ver `_seleccion_efectiva`).
    seleccion = _seleccion_efectiva(
        modelo=modelo_elegido,
        esfuerzo=esfuerzo_elegido,
        trae_imagen=_turno_trae_imagen(attachments),
    )
    await repo.add_message(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        role="user",
        content=stored_user_content,
    )

    if inline_credential is not None:
        stream = _stream_inline_credential_configuration(
            intent=inline_credential,
            tool=registry.get("configurar_credencial"),
            ctx=ctx,
            repo=repo,
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            conversation_id=conversation_id,
        )
    elif _is_bare_fix_command(body.text):
        stream = _stream_bare_fix_diagnosis(
            tool=registry.get("diagnosticar_autorreparacion_local"),
            ctx=ctx,
            history=history,
            repo=repo,
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
        )
    elif _es_pedido_directo_de_post_linkedin(body.text):
        # Atajo estilo Aria: cuando el mensaje ya dice "post de linkedin", el
        # servidor llama al motor directo sin pedirle al modelo que decida qué
        # herramienta invocar. Ese "pedirle al modelo" es donde se cae hoy: con
        # `tools` cargadas y system largo, llama-4-scout escribe el tool_call
        # como texto (`[crear_post_linkedin](tema="...")`) que nadie ejecuta, y
        # la persona ve el chat en blanco tras 10 segundos. Aria lleva
        # meses sin ese problema porque nunca pregunta cuando ya sabe (ver
        # `app.py:4560` en el repo de Aria). Copiamos esa decisión.
        stream = _stream_direct_linkedin_post(
            tool=registry.get("crear_post_linkedin"),
            ctx=ctx,
            text=body.text,
            # Si la persona NOMBRÓ la cuenta en su mensaje ("un post de Acme
            # sobre X"), se usa y el post sale en UN turno, como en las ROUTES de
            # Aria. Si no la nombró, se deja en None a propósito: entonces sale
            # la tarjeta de destino, que el dueño eligió tener porque le parece
            # más elegante que adivinar. Rápido cuando hay certeza, tarjeta
            # cuando de verdad hay duda.
            destino_override=_destino_desde_el_texto(body.text),
            repo=repo,
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            settings=settings,
            conversation_id=conversation_id,
        )
    elif (destino_elegido := _es_respuesta_a_card_de_destino(body.text)) is not None:
        # La otra mitad del atajo: cuando el pedido tuvo card de destino
        # (más de una cuenta configurada) y la persona toca una opción, ese
        # segundo mensaje también entra directo al motor. Sin esto, la
        # respuesta a la card volvía al modelo y era donde escribía el
        # tool_call como texto -- la persona veía la card marcada y después
        # nada más. El tema se recupera del pedido original en el historial.
        stream = _stream_direct_linkedin_post(
            tool=registry.get("crear_post_linkedin"),
            ctx=ctx,
            text=body.text,
            tema_override=_extraer_tema_del_historial_linkedin(history),
            destino_override=destino_elegido,
            repo=repo,
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            settings=settings,
            conversation_id=conversation_id,
        )
    else:
        events = agent.run_turn(
            ctx=ctx,
            persona=persona,
            history=history,
            user_text=user_text,
            flags=tenant.flags,
            extra_tools=extra_tools,
            seleccion=seleccion,
        )
        stream = _stream_agent_events(
            events=events,
            repo=repo,
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            user_id=current_user.user_id,
            settings=settings,
            redis_client=redis_client,
            llm_router=llm_router,
            title_user_text=(
                safe_user_text if needs_semantic_title and inline_credential is None else None
            ),
        )
    if idempotency_key is None:
        # Compatibilidad total: clientes existentes conservan streaming en vivo.
        return StreamingResponse(stream, media_type="text/event-stream")

    # Los eventos salen en vivo. El productor queda desacoplado del socket y
    # completa el replay aun si el cliente pierde la conexión a mitad del turno.
    assert request_hash is not None
    assert redis_idempotency_key is not None
    assert owner_token is not None

    async def notify_when_mobile_left() -> None:
        # iOS y Android no pueden mantener un socket arbitrario mientras el
        # sistema los suspende. Si el transporte desapareció antes del final,
        # el host termina igual y emite un evento/push opaco e idempotente.
        await enqueue(
            settings,
            "notify_important_event",
            {
                "user_id": str(current_user.user_id),
                "kind": "work_completed",
                "event_id": str(idempotency_key),
                "chat_id": str(conversation_id),
            },
            tenant.tenant_id,
        )

    live_stream = _stream_and_complete_idempotency(
        stream=stream,
        redis_client=redis_client,
        redis_key=redis_idempotency_key,
        request_hash=request_hash,
        owner_token=owner_token,
        ttl_seconds=idempotency_ttl,
        on_disconnected_complete=notify_when_mobile_left,
    )
    return StreamingResponse(
        live_stream,
        media_type="text/event-stream",
        headers={
            "Idempotency-Key": str(idempotency_key),
            "Idempotency-Replayed": "false",
        },
    )


@router.post("/{conversation_id}/confirm")
async def confirm_tool_call(
    conversation_id: uuid.UUID,
    body: ConfirmIn,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_streaming_repo),
    session: Any = Depends(get_tenant_session, scope="request"),
    llm_router: LLMRouter = Depends(get_llm_router),
    vault: Any = Depends(get_streaming_vault),
    settings: Settings = Depends(get_settings),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> StreamingResponse:
    tenant = current_user.tenant
    conversation = await repo.get_conversation(
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")

    # Se consume tanto al aprobar como al rechazar. GETDEL garantiza que dos
    # clientes concurrentes no puedan ejecutar el mismo lote y que un rechazo
    # sea definitivo (no deja una aprobación reutilizable detrás).
    pending = await _pop_pending_confirmation(
        redis_client,
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        tool_call_id=body.tool_call_id,
    )
    if pending is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Esa confirmación ya no está disponible (expiró o ya se procesó). "
                "Pídele la acción de nuevo al asistente."
            ),
        )

    if not body.approved:
        return StreamingResponse(
            _stream_declined_confirmation(
                repo=repo, tenant_id=tenant.tenant_id, conversation_id=conversation_id
            ),
            media_type="text/event-stream",
        )

    serialized_turn = pending.get("pending_turn")
    if serialized_turn is not None:
        try:
            pending_turn = PendingAgentTurn.model_validate(serialized_turn)
        except Exception as exc:  # noqa: BLE001 - payload Redis inválido, fail closed
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El turno pendiente está dañado y no puede reanudarse con seguridad.",
            ) from exc
        call_ids = {call.id for call in pending_turn.tool_calls}
        if (
            body.tool_call_id not in call_ids
            or body.tool_call_id in pending_turn.approved_tool_call_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="La confirmación no corresponde a una acción pendiente de este lote.",
            )

        registry = get_tool_registry(request)
        extra_tools = await _extra_conversation_tools(request, current_user)
        _preflight_pending_turn(
            pending=pending_turn,
            registry=registry,
            extra_tools=extra_tools,
            flags=tenant.flags,
            plan_key=tenant.plan_key,
        )

        persona_row = await repo.get_persona(
            tenant_id=tenant.tenant_id, user_id=current_user.user_id
        )
        persona = persona_from_row(persona_row)
        ctx = _build_ctx(
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            session=session,
            settings=settings,
            llm_router=llm_router,
            vault=vault,
            persona=persona,
            request=request,
            repo=repo,
            approved_tool_calls={body.tool_call_id},
            flags=tenant.flags,
            conversation_id=conversation_id,
            phone_call_dispatcher=phone_tool_dispatcher_for(
                request=request,
                tenant_id=tenant.tenant_id,
                user_id=current_user.user_id,
                repo=repo,
                vault=vault,
            ),
        )
        agent = Agent(llm_router, registry)
        events = agent.resume_turn(
            ctx=ctx,
            pending=pending_turn,
            approved_tool_call_id=body.tool_call_id,
            flags=tenant.flags,
            extra_tools=extra_tools,
            # La confirmación corre en un request HTTP DISTINTO al del turno
            # original: hay que RELEER la selección de la fila. Sin esto el
            # lote confirmado correría con el modelo automático en silencio,
            # exactamente el bug fantasma que ya pasó una vez. `trae_imagen`
            # es False porque este request no adjunta nada: la imagen del
            # turno original ya viajó y vive en `pending_turn.messages`.
            seleccion=_seleccion_efectiva(
                modelo=conversation.get("chat_model") or None,
                esfuerzo=conversation.get("chat_effort") or None,
                trae_imagen=False,
            ),
        )
        return StreamingResponse(
            _stream_agent_events(
                events=events,
                repo=repo,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=current_user.user_id,
                settings=settings,
                redis_client=redis_client,
                initial_text=pending_turn.accumulated_text,
                initial_tool_log=pending_turn.tool_log,
            ),
            media_type="text/event-stream",
        )

    # Compatibilidad con confirmaciones creadas antes de que existiera la
    # continuación serializada (o por tests/dobles que solo emiten name/args):
    # se conserva el camino directo histórico.
    tool = get_tool_registry(request).get(pending["name"])
    if tool is None:
        # No está en el registry compartido: puede ser una tool MCP
        # bring-your-own (`mcp_*`, ARCHITECTURE.md §15) — esas nunca se
        # registran ahí (ver `Agent.run_turn`/`get_mcp_tools_for_tenant`), así
        # que se resuelven recalculando las `extra_tools` de este tenant y
        # buscando por nombre, mismo criterio "el registry base gana" que
        # aplica `Agent.run_turn` (acá no hay colisión posible: si el
        # registry ya la tenía, ni siquiera se llega a este bloque).
        extra_tools = await _extra_conversation_tools(request, current_user)
        tool = next((t for t in extra_tools if t.name == pending["name"]), None)
    if tool is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"La herramienta «{pending['name']}» ya no está disponible.",
        )

    # Único punto de este camino que revisa el flag de plan de la tool
    # resuelta (ver `_tool_requires_flags_satisfechos` y el docstring del
    # módulo): `confirm_tool_call` nunca vuelve a invocar `Agent.run_turn`,
    # así que el filtro de `ToolRegistry.specs(flags)` que decide qué se
    # OFRECE al modelo nunca corre en esta rama. Cubre tanto un tenant que
    # jamás debió ver esta tool (llegó pendiente por otra vía, p. ej. el
    # hallazgo pinneado en `test_v6_sweep_flags.py` sobre `Agent._run_turn`)
    # como el caso más mundano de que el flag se apagó (downgrade de plan)
    # DESPUÉS de proponerse la acción y ANTES de que el humano confirmara —
    # la ventana de `PENDING_CONFIRMATION_TTL_SECONDS` (15 min) es tiempo de
    # sobra para que eso ocurra.
    if not _tool_requires_flags_satisfechos(tool, tenant.flags):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"La herramienta «{pending['name']}» no está disponible en tu plan "
                f"'{tenant.plan_key}'."
            ),
        )

    persona_row = await repo.get_persona(tenant_id=tenant.tenant_id, user_id=current_user.user_id)
    persona = persona_from_row(persona_row)
    ctx = _build_ctx(
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        session=session,
        settings=settings,
        llm_router=llm_router,
        vault=vault,
        persona=persona,
        request=request,
        repo=repo,
        approved_tool_calls={body.tool_call_id},
        flags=tenant.flags,
        conversation_id=conversation_id,
        phone_call_dispatcher=phone_tool_dispatcher_for(
            request=request,
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            repo=repo,
            vault=vault,
        ),
    )

    return StreamingResponse(
        _stream_approved_confirmation(
            tool_call_id=body.tool_call_id,
            tool=tool,
            tool_name=pending["name"],
            tool_args=pending.get("args") or {},
            ctx=ctx,
            repo=repo,
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            conversation_id=conversation_id,
            settings=settings,
        ),
        media_type="text/event-stream",
    )
