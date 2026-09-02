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
import inspect
import json
import logging
import re
import time
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
from edecan_core.session import UnifiedSessionState
from edecan_core.session_store import load_unified_session, save_unified_session
from edecan_core.speech_tags import enriquecer_speech_tags
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
from sqlalchemy import text

from edecan_api.chat_context import ChatContextLimits, build_contextual_history
from edecan_api.chat_delegation import prepare_chat_delegation
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
from edecan_api.llm_attribution import build_llm_usage_meta
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


def _agent_for_request(request: Request, llm_router: Any, registry: Any) -> Agent:
    """Construye Agent sin romper integraciones/test doubles antiguos."""
    kwargs: dict[str, Any] = {}
    try:
        supports_health = "provider_health" in inspect.signature(Agent).parameters
    except (TypeError, ValueError):
        supports_health = False
    if supports_health:
        kwargs["provider_health"] = getattr(request.app.state, "provider_health", None)
    return Agent(llm_router, registry, **kwargs)


# Mapea `AgentEvent.type` (interno, edecan_core) -> nombre de evento SSE (§10.7).
EVENT_NAME_MAP: dict[str, str] = {
    "text_delta": "message.delta",
    "tool_start": "tool.start",
    "tool_progress": "tool.progress",
    "tool_end": "tool.end",
    "confirmation_required": "confirmation.required",
    "done": "message.done",
    "error": "error",
    "follow_up_turn": "follow_up_turn",
}

MAX_QUEUED_CHAT_FOLLOWUPS = 5

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
        # Marca la conversación principal para los eventos asíncronos.
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
        # Comando local `/clear` (migración 0031): `null` = nunca se limpió.
        # Clientes viejos ignoran el campo extra igual que ya hacen con
        # `is_main`/`model`/`effort`.
        "context_cleared_at": row.get("context_cleared_at"),
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


@router.get("/search")
async def search_conversations(
    q: str,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """Búsqueda semántica en conversaciones anteriores (§142).

    Busca en títulos y contenido de mensajes de conversaciones del usuario.
    Usa ILIKE como fallback cuando no hay embeddings configurados.
    """
    query = q.strip()
    if not query:
        return []
    rows = await repo.search_conversations(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        query=query,
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
    tenant+usuario: ahí aterrizan los eventos
    automáticos que el dueño no pidió (llamada recibida, automatización
    ejecutada, recordatorio disparado), igual que el hilo de avisos de
    Es el helper reutilizable que exponen los workers,
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
    # `context_cleared_at` (comando local `/clear`, migración 0031): NO borra
    # nada de `messages`, solo deja de listarse por defecto desde acá en
    # adelante -- mismo límite que usa `post_message` para el contexto del
    # LLM (ver `chat_context.build_contextual_history`), así la pantalla y lo
    # que el modelo recuerda cuentan la misma historia.
    messages = await repo.list_messages(
        tenant_id=current_user.tenant_id,
        conversation_id=conversation_id,
        after=row.get("context_cleared_at"),
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


@router.post("/{conversation_id}/clear")
async def clear_conversation_context(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Comando local `/clear`: reinicia el contexto SIN borrar el historial.

    El cliente intercepta el texto `/clear` ANTES de mandarlo como mensaje
    normal (ver `ChatViewModel.enviar` en iOS) y llama a este endpoint en su
    lugar -- por eso NO hay tool ni paso por el modelo: si el modelo lo
    "interpretara" a su manera, sería exactamente el bug que este comando
    existe para arreglar (ver docstring del módulo, y el diagnóstico del
    dueño: "si escribo /clear se limpia es el contexto más no el chat").

    Mueve `conversations.context_cleared_at` a AHORA. Ningún `Message` se
    borra: siguen íntegros en la base, solo dejan de listarse por defecto en
    `GET /{id}` y de mandarse al LLM en el siguiente turno (ambos filtran por
    `after=context_cleared_at`, ver `Repo.list_messages`). Repetirlo sobre una
    conversación ya limpia es seguro -- vuelve a mover el límite un poco más
    adelante, nunca resucita nada.
    """

    row = await repo.clear_conversation_context(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    return _conversation_out(row)


class ConversationBranchIn(BaseModel):
    after_message_id: uuid.UUID | None = None
    until_message_id: uuid.UUID | None = None


class MessageFlagsIn(BaseModel):
    pinned: bool | None = None
    bookmark: bool | None = None


@router.post("/{conversation_id}/branch")
async def branch_conversation(
    conversation_id: uuid.UUID,
    body: ConversationBranchIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Copia el hilo a una conversación nueva sin borrar la original."""
    source = await repo.get_conversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    messages = await repo.list_messages(
        tenant_id=current_user.tenant_id,
        conversation_id=conversation_id,
        limit=200,
    )
    selected: list[Any] = []
    started = body.after_message_id is None
    for message in messages:
        message_id = uuid.UUID(str(message["id"]))
        if body.after_message_id is not None and message_id == body.after_message_id:
            started = True
        if started:
            selected.append(message)
        if body.until_message_id is not None and message_id == body.until_message_id:
            break
    title = f"Rama de {str(source.get('title') or 'chat').strip() or 'chat'}"[:120]
    created = await repo.create_conversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        title=title,
        channel=str(source.get("channel") or "web"),
    )
    for message in selected:
        await repo.add_message(
            tenant_id=current_user.tenant_id,
            conversation_id=uuid.UUID(str(created["id"])),
            role=str(message["role"]),
            content=message.get("content") or {"text": ""},
            tool_calls=message.get("tool_calls"),
            tokens_in=int(message.get("tokens_in") or 0),
            tokens_out=int(message.get("tokens_out") or 0),
        )
    return _conversation_out(created)


@router.post("/{conversation_id}/rewind")
async def rewind_conversation(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Crea una rama sin el último turno, para reescribir desde ahí."""
    source = await repo.get_conversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    messages = await repo.list_messages(
        tenant_id=current_user.tenant_id,
        conversation_id=conversation_id,
        limit=200,
    )
    if len(messages) < 2:
        raise HTTPException(
            status_code=400,
            detail="No hay un turno que rebobinar.",
        )
    if len(messages) < 3:
        title = f"Rama de {str(source.get('title') or 'chat').strip() or 'chat'}"[:120]
        created = await repo.create_conversation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            title=title,
            channel=str(source.get("channel") or "web"),
        )
        return _conversation_out(created)
    return await branch_conversation(
        conversation_id,
        ConversationBranchIn(until_message_id=uuid.UUID(str(messages[-3]["id"]))),
        current_user,
        repo,
    )


@router.post("/{conversation_id}/messages/{message_id}/flags")
async def set_message_flags(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    body: MessageFlagsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    conversation = await repo.get_conversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    messages = await repo.list_messages(
        tenant_id=current_user.tenant_id,
        conversation_id=conversation_id,
        limit=200,
    )
    row = next((item for item in messages if str(item["id"]) == str(message_id)), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")
    content = row["content"] if isinstance(row["content"], dict) else {"text": row["content"]}
    if body.pinned is not None:
        content["pinned"] = body.pinned
    if body.bookmark is not None:
        content["bookmark"] = body.bookmark
    updated = await repo.update_message_content(
        tenant_id=current_user.tenant_id,
        conversation_id=conversation_id,
        message_id=message_id,
        content=content,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")
    return _message_out(updated)


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

    if modelo is not None and modelo_chat_info(modelo) is None:
        modelo = None
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

    # Cuando la imagen va inline como base64, el bloque de texto NO debe
    # incluir el contexto de adjuntos ("Archivos adjuntos privados: … si no
    # aparece, usa leer_archivo"). Ese texto es un fallback para modelos que
    # no reciben la imagen; dejarlo cuando la imagen SÍ va inline confunde a
    # Llama 4 Scout, que lee "si no aparece, usa leer_archivo" y responde
    # "No puedo ver la imagen" a pesar de tenerla en el turno como base64.
    clean_text = user_text.split("\n\nArchivos adjuntos privados:")[0].strip()
    blocks: list[dict[str, Any]] = [{"type": "text", "text": clean_text}]
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
                    raw = await response["Body"].read()
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


# ---------------------------------------------------------------------------
# Sesión multimodal persistente (PHASE2.md §49-§50).
#
# `Agent._run_turn` instancia `VisualMemory` PER-TURNO salvo que el llamador
# inyecte una instancia longeva en `ctx.extras["visual_memory"]` — sin eso el
# contexto visual se pierde entre mensajes. Este dict de módulo guarda un
# `MultimodalSessionState` por conversación (clave `(tenant_id,
# conversation_id)`) con TTL: el contexto visual sobrevive entre turnos dentro
# de la vida de la conversación, sin mezclar conversaciones distintas (el
# `context_key` refuerza esa frontera). Es memoria de proceso, no de base de
# datos: basta y sobra para el requisito del frente y no toca `models.py` ni
# `alembic/`.
# ---------------------------------------------------------------------------

_MULTIMODAL_SESSION_TTL_SECONDS = 6 * 60 * 60  # 6 h sin actividad = se rearma
_unified_sessions: dict[tuple[uuid.UUID, uuid.UUID], tuple[float, UnifiedSessionState]] = {}


def _unified_session_for(
    *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> UnifiedSessionState:
    """Devuelve (creando si hace falta) el estado multimodal de la conversación.

    La evicción es perezosa por TTL: una entrada sin uso durante más de
    ``_MULTIMODAL_SESSION_TTL_SECONDS`` se descarta y se arma de nuevo. En el
    modelo de despliegue actual (un solo proceso asyncio por worker) no hay
    contención por este dict; si se moviera a multiproceso, este store pasaría
    a Redis igual que las confirmaciones pendientes.
    """

    key = (tenant_id, conversation_id)
    now = time.monotonic()
    entry = _unified_sessions.get(key)
    if entry is not None:
        created_at, state = entry
        if now - created_at <= _MULTIMODAL_SESSION_TTL_SECONDS:
            return state
    state = UnifiedSessionState(
        session_id=str(conversation_id),
        tenant_id=str(tenant_id),
        user_id="",
        conversation_id=str(conversation_id),
    )
    state.multimodal.visual_memory.context_key = str(conversation_id)
    _unified_sessions[key] = (now, state)
    return state


# Compatibilidad para tests y callers internos de fase 2.
_multimodal_session_for = _unified_session_for


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
    unified_session: UnifiedSessionState | None = None,
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
            "unified_session": unified_session
            or _unified_session_for(tenant_id=tenant_id, conversation_id=conversation_id),
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


async def _persist_pending_approval(
    session: Any,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    tool_call_id: str,
    name: str,
    args: dict[str, Any],
    pending_turn: PendingAgentTurn | dict[str, Any] | None = None,
) -> None:
    """Persiste el respaldo durable de una confirmación (directiva §30-32).

    Guarda en `pending_approvals` el MISMO payload que Redis
    (`{name, args, pending_turn}`) para que `POST /v1/approvals/{id}/approve`
    reanude el turno después de un reload. Es best-effort y nunca debe romper
    el camino efímero actual: si no hay sesión (dobles de prueba) o la tabla
    aún no existe, se registra y se continúa.
    """

    if session is None:
        return
    snapshot: dict[str, Any] = {"name": name, "args": args}
    if pending_turn is not None:
        snapshot["pending_turn"] = PendingAgentTurn.model_validate(pending_turn).model_dump()
    try:
        await session.execute(
            text(
                "INSERT INTO pending_approvals "
                "(tenant_id, user_id, conversation_id, tool_call_id, agent_snapshot) "
                "VALUES (:tenant_id, :user_id, :conversation_id, :tool_call_id, "
                ":snapshot ::jsonb) "
                "ON CONFLICT (tenant_id, conversation_id, tool_call_id) DO UPDATE SET "
                "agent_snapshot = EXCLUDED.agent_snapshot, status = 'pending', "
                "decided_at = NULL, decided_by = NULL, updated_at = now()"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "conversation_id": str(conversation_id),
                "tool_call_id": tool_call_id,
                "snapshot": json.dumps(snapshot, ensure_ascii=False, default=str),
            },
        )
    except Exception:  # noqa: BLE001 - respaldo best-effort; el camino Redis sigue vigente
        logger.warning(
            "No se pudo persistir pending_approvals (tenant_id=%s conversation_id=%s "
            "tool_call_id=%s)",
            tenant_id,
            conversation_id,
            tool_call_id,
            exc_info=True,
        )


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
) -> StreamingResponse | JSONResponse:
    if record.get("request_hash") != request_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key ya fue usado con un mensaje diferente.",
        )
    if record.get("status") == "queued":
        return _queued_message_response(
            idempotency_key=idempotency_key,
            position=int(record.get("position") or 0),
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


def _chat_turn_active_key(
    *, tenant_id: uuid.UUID, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> str:
    return f"chat_turn_active:{tenant_id}:{user_id}:{conversation_id}"


def _chat_followup_count_key(
    *, tenant_id: uuid.UUID, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> str:
    return f"chat_followup_pending:{tenant_id}:{user_id}:{conversation_id}"


def _queued_message_response(
    *,
    idempotency_key: uuid.UUID | None,
    position: int,
) -> JSONResponse:
    payload = {
        "status": "queued",
        "position": position,
        "pending": position,
        "max_pending": MAX_QUEUED_CHAT_FOLLOWUPS,
    }
    headers: dict[str, str] = {"Cache-Control": "no-store"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = str(idempotency_key)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=payload, headers=headers)


async def _claim_queued_message_idempotency(
    redis_client: redis_asyncio.Redis,
    *,
    redis_key: str,
    request_hash: str,
    position: int,
    ttl_seconds: int,
) -> dict[str, Any] | None:
    record = {
        "status": "queued",
        "request_hash": request_hash,
        "position": position,
        "queued_at": datetime.now(UTC).isoformat(),
    }
    claimed = await redis_client.set(
        redis_key,
        json.dumps(record, ensure_ascii=False),
        ex=ttl_seconds,
        nx=True,
    )
    if claimed:
        return None
    return await _load_idempotency_record(redis_client, redis_key=redis_key)


async def _enqueue_chat_followup_message(
    *,
    redis_client: redis_asyncio.Redis,
    followup_key: str,
    ttl_seconds: int,
    idempotency_key: uuid.UUID | None,
    redis_idempotency_key: str | None,
    request_hash: str | None,
) -> tuple[int, dict[str, Any] | None]:
    position = int(await redis_client.incr(followup_key))
    await redis_client.expire(followup_key, ttl_seconds)
    if position > MAX_QUEUED_CHAT_FOLLOWUPS:
        await redis_client.decr(followup_key)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="La cola de mensajes mientras Edecán trabaja está llena.",
        )
    if idempotency_key is not None:
        assert redis_idempotency_key is not None and request_hash is not None
        raced = await _claim_queued_message_idempotency(
            redis_client,
            redis_key=redis_idempotency_key,
            request_hash=request_hash,
            position=position,
            ttl_seconds=ttl_seconds,
        )
        if raced is not None:
            if raced.get("request_hash") != request_hash:
                await redis_client.decr(followup_key)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency-Key ya fue usado con un mensaje diferente.",
                )
            if raced.get("status") != "queued":
                await redis_client.decr(followup_key)
                return int(raced.get("position") or position), raced
            return int(raced.get("position") or position), raced
    return position, None


def _followup_user_text_and_prefix_rows(
    history_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    last_assistant = -1
    for index, row in enumerate(history_rows):
        if row.get("role") == "assistant":
            last_assistant = index
    prefix_rows = history_rows[: last_assistant + 1] if last_assistant >= 0 else []
    new_user_rows = [row for row in history_rows[last_assistant + 1 :] if row.get("role") == "user"]
    parts = [_extract_text(row.get("content") or {}) for row in new_user_rows]
    user_text = "\n\n".join(part for part in parts if part.strip())
    return prefix_rows, user_text


async def _stream_with_followup_chain(
    *,
    initial_stream: AsyncIterator[str],
    redis_client: redis_asyncio.Redis,
    active_key: str,
    followup_key: str,
    build_followup_stream: Callable[[], Awaitable[AsyncIterator[str]]],
) -> AsyncIterator[str]:
    try:
        async for chunk in initial_stream:
            yield chunk
        while True:
            pending = int(await redis_client.get(followup_key) or 0)
            if pending <= 0:
                break
            await redis_client.set(followup_key, 0, ex=3600)
            yield _format_sse(
                "follow_up_turn",
                {"type": "follow_up_turn", "pending": pending},
            )
            followup_stream = await build_followup_stream()
            async for chunk in followup_stream:
                yield chunk
    finally:
        await redis_client.delete(active_key)


async def _persist_chat_user_message(
    *,
    repo: Repo,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    conversation: dict[str, Any],
    body: ChatMessageIn,
    stored_user_content: dict[str, Any],
    safe_user_text: str,
    inline_credential: Any,
) -> tuple[str | None, str | None]:
    needs_semantic_title = not str(conversation.get("title") or "").strip()
    if needs_semantic_title:
        automatic_title = (
            _credential_conversation_title(inline_credential)
            if inline_credential is not None
            else _automatic_conversation_title(safe_user_text, fallback="Archivo adjunto")
        )
        await repo.update_conversation_title(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            title=automatic_title,
            only_if_empty=True,
            source="auto",
        )
    modelo_elegido = conversation.get("chat_model") or None
    esfuerzo_elegido = conversation.get("chat_effort") or None
    if body.model is not None or body.effort is not None:
        modelo_elegido = body.model.strip() if body.model else modelo_elegido
        esfuerzo_elegido = body.effort or esfuerzo_elegido
        await repo.update_conversation_model(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            model=modelo_elegido,
            effort=esfuerzo_elegido,
        )
    await repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        role="user",
        content=stored_user_content,
    )
    return modelo_elegido, esfuerzo_elegido


async def _enqueue_chat_message_while_busy(
    *,
    repo: Repo,
    redis_client: redis_asyncio.Redis,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    conversation: dict[str, Any],
    body: ChatMessageIn,
    stored_user_content: dict[str, Any],
    safe_user_text: str,
    inline_credential: Any,
    idempotency_key: uuid.UUID | None,
    redis_idempotency_key: str | None,
    request_hash: str | None,
    idempotency_ttl: int,
    followup_key: str,
) -> JSONResponse:
    await _persist_chat_user_message(
        repo=repo,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        conversation=conversation,
        body=body,
        stored_user_content=stored_user_content,
        safe_user_text=safe_user_text,
        inline_credential=inline_credential,
    )
    position, raced = await _enqueue_chat_followup_message(
        redis_client=redis_client,
        followup_key=followup_key,
        ttl_seconds=idempotency_ttl,
        idempotency_key=idempotency_key,
        redis_idempotency_key=redis_idempotency_key,
        request_hash=request_hash,
    )
    if raced is not None and raced.get("status") == "queued":
        position = int(raced.get("position") or position)
    return _queued_message_response(idempotency_key=idempotency_key, position=position)


async def _build_followup_chat_stream(
    *,
    request: Request,
    current_user: CurrentUser,
    tenant: Any,
    conversation_id: uuid.UUID,
    conversation: dict[str, Any],
    repo: Repo,
    session: Any,
    llm_router: LLMRouter,
    vault: Any,
    settings: Settings,
    redis_client: redis_asyncio.Redis,
) -> AsyncIterator[str]:
    history_rows = await repo.list_messages(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        limit=max(50, int(settings.CHAT_CONTEXT_MAX_MESSAGES)),
        after=conversation.get("context_cleared_at"),
    )
    _prefix_rows, user_text = _followup_user_text_and_prefix_rows(history_rows)
    if not user_text.strip():
        yield _format_sse("message.done", {"type": "done", "usage": {}})
        return

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

    persona_row = await repo.get_persona(tenant_id=tenant.tenant_id, user_id=current_user.user_id)
    persona = persona_from_row(persona_row)
    profile_context = (
        await profile_context_for(session, tenant.tenant_id, current_user.user_id)
        if session is not None
        else ""
    )
    registry = get_tool_registry(request)
    agent = _agent_for_request(request, llm_router, registry)
    unified_session = await load_unified_session(
        session,
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if unified_session is None:
        unified_session = _unified_session_for(
            tenant_id=tenant.tenant_id, conversation_id=conversation_id
        )
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
        unified_session=unified_session,
    )
    ctx.extras["tools_con_pregunta_pendiente"] = _tools_con_pregunta_pendiente(history_rows)
    ctx.extras["lo_pidio_una_persona"] = True
    unified_session = ctx.extras["unified_session"]
    unified_session.user_id = str(current_user.user_id)
    unified_session.touch(modality="text")
    ctx.extras["visual_memory"] = unified_session.visual_memory
    extra_tools = await _extra_conversation_tools(request, current_user)
    seleccion = _seleccion_efectiva(
        modelo=conversation.get("chat_model") or None,
        esfuerzo=conversation.get("chat_effort") or None,
        trae_imagen=False,
    )
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
        session=session,
        title_user_text=None,
    )
    stream = _persist_session_after_stream(
        stream,
        db_session=session,
        unified_session=unified_session,
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    async for chunk in stream:
        yield chunk


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
    session: Any = None,
    assistant_content_extra: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    text_parts: list[str] = [initial_text] if initial_text else []
    tool_log: list[dict[str, Any]] = list(initial_tool_log or [])
    # Archivos que el modelo GENERÓ en este turno (crear_pdf, crear_documento,
    # generar_imagen, generar_grafico…): se adjuntan al mensaje del asistente
    # para que el teléfono los muestre — así los bots entregan documentos.
    artefactos_turno: list[dict[str, str | None]] = []
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
                    # Archivos generados por la tool (contrato ArtifactRef,
                    # que llega como objeto pydantic, no dict — se lee por
                    # atributo y se tolera el dict por robustez).
                    for ref in event.get("artifacts") or []:
                        es_dict = isinstance(ref, dict)
                        file_id = ref.get("file_id") if es_dict else getattr(ref, "file_id", None)
                        filename = (
                            ref.get("filename") if es_dict else getattr(ref, "filename", None)
                        )
                        mime = ref.get("mime") if es_dict else getattr(ref, "mime", None)
                        if file_id and filename:
                            artefactos_turno.append(
                                {
                                    "file_id": str(file_id),
                                    "filename": str(filename),
                                    "mime": str(mime or ""),
                                }
                            )
                    artefactos_turno = artefactos_turno[:10]
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
                attribution = build_llm_usage_meta(
                    attribution=event.get("attribution"),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=int(usage.get("cached_input_tokens", 0) or 0),
                )
                assistant_content: dict[str, Any] = {
                    "text": enriquecer_speech_tags("".join(text_parts)),
                    **({"explanation": event["explanation"]} if event.get("explanation") else {}),
                    **({"attachments": artefactos_turno} if artefactos_turno else {}),
                }
                if assistant_content_extra:
                    assistant_content.update(assistant_content_extra)
                await repo.add_message(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=assistant_content,
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
                            **attribution,
                        },
                        cost_usd=attribution.get("cost_usd"),
                    )
                await repo.add_usage_event(
                    tenant_id=tenant_id,
                    kind="messages",
                    quantity=1.0,
                    meta={"conversation_id": str(conversation_id), **attribution},
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
                    # Respaldo durable (directiva §30-32): además del caché de
                    # Redis, se persiste el snapshot en `pending_approvals` para
                    # que la aprobación sobreviva un reload. Best-effort: si la
                    # base no está lista (p. ej. dobles de prueba con `session=None`)
                    # se registra y se sigue, sin romper el camino Redis actual.
                    await _persist_pending_approval(
                        session,
                        tenant_id=tenant_id,
                        user_id=user_id,
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


async def _persist_session_after_stream(
    stream: AsyncIterator[str],
    *,
    db_session: Any,
    unified_session: UnifiedSessionState,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> AsyncIterator[str]:
    """Guarda el último snapshot conocido aunque el cliente abandone el stream."""

    try:
        async for item in stream:
            yield item
    finally:
        try:
            await save_unified_session(
                db_session,
                unified_session,
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        except Exception:  # noqa: BLE001 - continuidad auxiliar best-effort
            logger.warning(
                "No se pudo persistir la sesión unificada tenant=%s conversation=%s",
                tenant_id,
                conversation_id,
                exc_info=True,
            )


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


async def _stream_delegation_confirmation(
    *,
    prefix: str,
    repo: Repo,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> AsyncIterator[str]:
    """Confirma delegaciones encoladas sin invocar al agente principal."""
    text = enriquecer_speech_tags(prefix)
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
        meta={"conversation_id": str(conversation_id), "delegation_only": True},
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
# a las que usa el enrutador directo de contenido,
# incluyendo la explicación que lo motivó allá: pedirle al modelo que DECIDA qué
# herramienta invocar añade hasta varias rondas donde la persona no ve nada, y
# el modelo -- con `tools` cargadas y un system largo -- se cae a escribir el
# tool_call como texto (`[crear_post_linkedin](tema="...")`) que nadie ejecuta.
# El atajo lo evita porque NO le pregunta al modelo cuando ya sabe: detecta la
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

def _es_pedido_directo_de_post_linkedin(text: str) -> bool:
    """`True` si el mensaje es inequívocamente "crea un post de LinkedIn".

    Sigue siendo ESTRICTO en lo que importa: hace falta la pieza ("post",
    "publicación", "contenido", "encuesta") junto a la plataforma o a una cuenta
    conocida. Un pedido ambiguo como "escríbeme algo para publicar" sigue yendo
    al agente normal, que ahí sí tiene que preguntar.
    """
    plano = text or ""
    return bool(_RE_PEDIDO_LINKEDIN.search(plano))


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


# Hasta cuántos caracteres puede medir un mensaje para tratarlo como una respuesta
# escrita a la card de destino. Una respuesta de verdad nombra la cuenta y poco
# más ("acme", "para mi página", "en la personal"); un mensaje largo es otra
# conversación aunque mencione la cuenta de pasada.
_MAX_RESPUESTA_DESTINO_LIBRE_CHARS = 80


async def _destino_de_respuesta_libre(
    ctx: ToolContext, text: str, history_rows: list[dict[str, Any]]
) -> str | None:
    """El destino, cuando la persona CONTESTÓ la card de destino escribiendo.

    La card permite texto libre (`allow_free_text=True`), pero el atajo solo
    entendía la frase exacta del botón ("Escríbelo con la voz de '...'"). Quien
    escribía "acme" o "en mi perfil personal" caía al agente genérico, que
    es el camino donde el modelo decide libre qué tool llamar -- y donde nacía
    el post con otro motor y otra imagen. Mismo criterio que el resto del atajo:
    si ya se sabe qué quiso decir, no se le pregunta a un LLM.

    Solo aplica cuando la card de destino quedó abierta en el último turno del
    asistente (`_tools_con_pregunta_pendiente`): sin esa condición, cualquier
    mensaje corto que nombrara una cuenta se volvería un pedido de post.
    """
    if "crear_post_linkedin" not in _tools_con_pregunta_pendiente(history_rows):
        return None
    limpio = re.sub(r"\s+", " ", (text or "").strip())
    if not limpio or len(limpio) > _MAX_RESPUESTA_DESTINO_LIBRE_CHARS:
        return None
    directo = _destino_desde_el_texto(limpio)
    if directo:
        return directo
    if ctx.session is None:
        return None
    try:
        from edecan_creative.social import destinos_configurados

        destinos = await destinos_configurados(ctx, "linkedin")
    except Exception:  # noqa: BLE001 - sin catálogo no hay nada que reconocer
        return None
    plano = _texto_plano_sin_acentos(limpio)
    for destino in destinos:
        for candidato in (str(destino.get("id") or ""), str(destino.get("label") or "")):
            candidato_plano = _texto_plano_sin_acentos(candidato)
            # Palabra completa, nunca substring: el panel de verificación demostró que
            # con substring un destino de nombre corto ("ana") matcheaba dentro de
            # "mañana" y el post salía por la cuenta equivocada.
            if candidato_plano and re.search(rf"(?<!\w){re.escape(candidato_plano)}(?!\w)", plano):
                return str(destino["id"])
    return None


def _texto_plano_sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", (texto or "").casefold())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _destino_desde_el_texto(text: str) -> str | None:
    """La cuenta que la persona nombró en su propio mensaje, o `None` si no nombró ninguna.

    Una cuenta explícita puede resolverse en el mismo mensaje, sin pedirle a un
    modelo que vuelva a interpretar una selección ya inequívoca.

    Edecán hizo UNA tool para las dos cuentas y por eso terminaba preguntando
    siempre, incluso cuando la respuesta era obvia. Con esto vuelve al
    comportamiento directo: si la persona nombró la cuenta, se usa; y si pidió
    un post "de LinkedIn" sin más, es el personal, igual que allá. La tarjeta
    queda para la duda real, no para el caso de todos los días.
    """
    descompuesto = unicodedata.normalize("NFKD", (text or "").casefold())
    plano = "".join(c for c in descompuesto if not unicodedata.combining(c))
    # Cualquier nombre de organización configurado por el tenant se detecta en
    # `_decidir_destino` con su config real; acá solo se resuelve lo que se puede
    # decidir sin consultarla, que es el caso frecuente.
    if re.search(r"\b(personal|mi perfil|mi cuenta personal)\b", plano):
        return "personal"
    return None


# Un mensaje que SOLO trae el tema ("sobre: X", "acerca de X", "tema: X"), mandado
# como turno propio. Es la manera natural de completar "créame un post de LinkedIn"
# cuando el tema no cupo en el primer mensaje -- y era exactamente el mensaje que
# se caía al agente genérico, donde un LLM libre decidía qué tool llamar y con qué
# texto: ahí nacía el "segundo pipeline" (copy corto escrito por el modelo del chat
# + imagen del proveedor BYO) que no se parecía en nada al motor real.
_RE_SEGUIMIENTO_TEMA_LINKEDIN = re.compile(
    r"^(?:sobre el tema|acerca del|acerca de|sobre|tema)(?:\s*:\s*|\s+)(.{3,140}?)\s*[.!]?$",
    re.IGNORECASE,
)

# Cuántos mensajes recientes del usuario se revisan buscando el pedido de post que
# le da contexto a un seguimiento "sobre: X". DOS, no más, por un fallo que encontró
# el panel de verificación: con una ventana de 4, una frase conversacional que arranca
# en "sobre" ("Sobre el tema de mañana, no voy a poder ir a la reunión") mandada hasta
# tres mensajes después de un pedido ya resuelto se convertía en un post espurio. Dos
# alcanza exactamente para los flujos reales -- (pedido → "sobre: X") y (pedido → botón
# de la card → "sobre: X"), porque las respuestas de botón no cuentan para la ventana
# (ver `_mensajes_de_usuario_sin_botones`) -- y para nada más.
_VENTANA_SEGUIMIENTO_MENSAJES = 2


# Arranques de captura que NO son un tema, aunque el mensaje empiece con "sobre":
#   - "sobre todo ..." es la muletilla adverbial más común del español;
#   - "sobre eso/esto/ese asunto..." es anáfora conversacional ("Sobre eso, ponlo en mi
#     personal") -- se refiere a algo ya hablado, no encarga un tema nuevo. El panel de
#     verificación demostró que sin esta exclusión ese mensaje se robaba el turno y el
#     tema real del pedido se perdía.
# Se descarta por el ARRANQUE de la captura, no prohibiendo la palabra en el tema entero
# ("sobre la venta de todo el portafolio" es normal y no debe caerse).
_RE_ARRANQUE_NO_TEMA = re.compile(
    r"^(?:tod[oa]s?|eso|esto|es[ea]|aquell[oa]s?|aquel|lo\s+(?:de|que))\b",
    re.IGNORECASE,
)


def _tema_de_seguimiento(text: str) -> str | None:
    """El tema, si el mensaje ENTERO es un "sobre: X" y nada más; `None` si no.

    Una pregunta no es un tema: "¿sobre qué escribiste?" conversa, no encarga.
    Por eso cualquier signo de interrogación descarta el match completo.
    """
    limpio = re.sub(r"\s+", " ", (text or "").strip())
    if "?" in limpio or "¿" in limpio:
        return None
    match = _RE_SEGUIMIENTO_TEMA_LINKEDIN.match(limpio)
    if not match:
        return None
    if _RE_ARRANQUE_NO_TEMA.match(match.group(1).strip()):
        return None
    return _limpiar_tema(match.group(1))


def _mensajes_de_usuario_sin_botones(history: list[ChatMessage]) -> list[str]:
    """Los textos del usuario, sin los mensajes sintéticos de los botones de la card.

    "Escríbelo con la voz de '...'" lo escribe el botón, no la persona: contarlo en la
    ventana de recencia dejaba el pedido original fuera de alcance justo en el flujo más
    normal (pedido → botón → "sobre: X")."""
    return [
        str(m.content or "")
        for m in history
        if m.role == "user" and _es_respuesta_a_card_de_destino(str(m.content or "")) is None
    ]


def _es_seguimiento_de_tema_linkedin(text: str, history: list[ChatMessage]) -> str | None:
    """El tema si este mensaje completa un pedido de post RECIENTE, o `None`.

    La firma del seguimiento son las dos mitades juntas: el mensaje tiene forma de
    tema puro (`_tema_de_seguimiento`) Y hay un pedido directo de post en los
    últimos mensajes del usuario. Sin la segunda condición, cualquier "sobre la
    reunión de mañana" entraría al motor de posts; sin la primera, cualquier
    mensaje después de un pedido lo haría. El camino directo no necesita este puente porque
    su ruta se resuelve en UN mensaje; Edecán corta el turno con la card de
    destino, así que el tema llega partido en dos y alguien tiene que volver a
    juntarlo -- determinista, no un LLM adivinando."""
    tema = _tema_de_seguimiento(text)
    if not tema:
        return None
    recientes = _mensajes_de_usuario_sin_botones(history)[-_VENTANA_SEGUIMIENTO_MENSAJES:]
    for contenido in recientes:
        if _es_pedido_directo_de_post_linkedin(contenido):
            return tema
    return None


def _destino_del_pedido_en_historial(history: list[ChatMessage]) -> str | None:
    """La cuenta que la persona nombró en su pedido original, leyendo hacia atrás."""
    for message in reversed(history):
        if message.role != "user":
            continue
        contenido = str(message.content or "")
        if _es_pedido_directo_de_post_linkedin(contenido):
            return _destino_desde_el_texto(contenido)
    return None


def _extraer_tema_del_historial_linkedin(history: list[ChatMessage]) -> str | None:
    """Recupera el `tema` del pedido original al leer hacia atrás en el hilo.

    La card de destino corta el turno pidiéndole a la persona que elija; cuando
    contesta, el mensaje NO trae el tema otra vez -- vive en el turno anterior
    ("Créame un post de LinkedIn sobre X") o en un seguimiento suelto ("sobre:
    X") que la persona mandó después del pedido. Sin esto, el motor se activa
    sin tema y cae a su rotación editorial, escribiendo sobre algo que la
    persona no pidió. Se busca el ÚLTIMO mensaje de usuario que traía el pedido
    original (o su seguimiento de tema), no cualquier mención pasada.

    Un mensaje con forma de seguimiento solo cuenta si en su momento fue un
    seguimiento DE VERDAD -- con un pedido directo dentro de la ventana de
    mensajes anteriores a ÉL, la misma condición que exige el despacho
    (`_es_seguimiento_de_tema_linkedin`). Sin ese requisito, el panel de
    verificación demostró que un "sobre: mi cita con el dentista" perdido veinte
    mensajes atrás, sin ningún pedido de post cerca, resucitaba como tema del
    post de hoy."""
    usuarios = _mensajes_de_usuario_sin_botones(history)
    for indice in range(len(usuarios) - 1, -1, -1):
        contenido = usuarios[indice]
        tema_suelto = _tema_de_seguimiento(contenido)
        if tema_suelto:
            previos = usuarios[max(0, indice - _VENTANA_SEGUIMIENTO_MENSAJES) : indice]
            if any(_es_pedido_directo_de_post_linkedin(p) for p in previos):
                return tema_suelto
            continue
        if not _es_pedido_directo_de_post_linkedin(contenido):
            continue
        tema = _extraer_tema_de_post_linkedin(contenido)
        if tema:
            return tema
        return None
    return None


# Reglas para sacar el tema del propio mensaje
# (`features/linkedin_content._extraer_tema_pedido`). "Sobre X", "de X",
# "acerca de X", "sobre el tema: X". Sin match, el motor rota su calendario
# editorial según los pilares configurados.
#
# Son DOS patrones con prioridad, no uno, por dos fallos reales del regex único:
#   - "sobre: X" (con dos puntos pegados, que es como lo escribe el dueño) no
#     matcheaba el `\s+` obligatorio y el tema se PERDÍA entero -> el motor
#     rotaba su calendario y escribía de otra cosa, con imagen de otra cosa.
#   - Con "sobre" y "de" en la misma alternación gana el conector que aparezca
#     primero en la frase, no el más específico: "un post de LinkedIn sobre la
#     multa de la SIC a Rappi" extraía "la SIC a Rappi" (el "de" de en medio se
#     comía el conector real). El conector débil ("de X") solo puede decidir
#     cuando no hay ninguno fuerte en todo el resto de la frase.
_TEMA_LINKEDIN_FUERTE_RE = re.compile(
    r"(?:sobre el tema|acerca del|acerca de|sobre)(?:\s*:\s*|\s+)(.{3,140}?)(?:[.?!]|$)",
    re.IGNORECASE,
)
_TEMA_LINKEDIN_DEBIL_RE = re.compile(
    r"\bdel?(?:\s*:\s*|\s+)(.{3,140}?)(?:[.?!]|$)",
    re.IGNORECASE,
)

# Comillas que la gente pone alrededor del tema ("sobre 'la multa de la SIC'").
_COMILLAS_TEMA = "'\"“”«»"


def _limpiar_tema(crudo: str) -> str | None:
    tema = crudo.strip().strip(_COMILLAS_TEMA).rstrip(",;: ").strip()
    return tema if 3 <= len(tema) <= 140 else None


def _extraer_tema_de_post_linkedin(text: str) -> str | None:
    """Tema explícito ("sobre Venezuela") o `None` si el mensaje no lo trae.

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
    resto = limpio[fin:]
    match = _TEMA_LINKEDIN_FUERTE_RE.search(resto) or _TEMA_LINKEDIN_DEBIL_RE.search(resto)
    if not match:
        return None
    return _limpiar_tema(match.group(1))


# Marcas de que una frase del `ToolResult.content` está dirigida al MODELO y no a
# la persona: nombres de tools entre comillas, órdenes sobre llamar o terminar el
# turno, y referencias al usuario en tercera persona. `content` es el canal
# modelo-facing, así que normalmente lo lee el modelo y traduce; en el ATAJO no
# hay modelo en medio y se le volcaba crudo a la persona. Se observó en el chat
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

    Usa un patrón de enrutamiento directo para pedidos inequívocos.
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

    # ¿Hay que preguntar el destino? Solo si no vino resuelto Y el usuario tiene
    # 2+ cuentas configuradas. Si se sabe (o hay 0/1), se ENCOLA la generación
    # pesada (escritor "profundo"/nemotron + imagen) en segundo plano y el
    # resultado llega como card + push cuando termina — así el turno del chat no
    # queda colgado esperando a un modelo lento. Import perezoso para no crear
    # una dependencia dura de `edecan_creative` en este router.
    destinos: list[dict[str, str]] = []
    if destino_override is None and ctx.session is not None:
        try:
            from edecan_creative.social import destinos_configurados

            destinos = await destinos_configurados(ctx, "linkedin")
        except Exception:  # noqa: BLE001 - sin destinos => sin gate, se encola
            destinos = []
    gate_de_destino = destino_override is None and len(destinos) >= 2

    presentation: list[dict[str, Any]] = []
    if tool is None:
        detail = "El motor de LinkedIn no está disponible en esta instalación. No preparé nada."
    elif not gate_de_destino:
        # Destino resuelto (o único/ninguno): encolar y avisar. El handler
        # `create_linkedin_post` corre el motor completo con tiempo de sobra y
        # entrega el borrador como card + push en el chat principal.
        job_type = "create_linkedin_post"
        try:
            await enqueue(
                settings,
                job_type,
                {
                    "conversation_id": str(conversation_id),
                    "user_id": str(user_id),
                    "tema": tema,
                    "destino": destino_override,
                    "con_imagen": True,
                },
                tenant_id,
            )
            detail = "Listo, me pongo a escribir tu post de LinkedIn — te llega aquí mismo en un momento."
        except Exception as exc:  # noqa: BLE001 - cierre visible y seguro
            logger.warning("no se pudo encolar el post de LinkedIn", exc_info=True)
            detail = f"No pude preparar el post de LinkedIn: {public_error_message(exc)}"
    else:
        # Gate de destino INLINE: la tool corta en la pregunta (rápido, no
        # escribe el post todavía). El siguiente turno trae `destino_override`.
        try:
            result = await asyncio.wait_for(tool.run(ctx, args), timeout=30.0)
            presentation = list(result.presentation or [])
            hubo_pregunta = any(
                isinstance(b, dict) and b.get("type") == "question" for b in presentation
            )
            respaldo = "" if hubo_pregunta else "Dime con cuál cuenta lo escribo y lo preparo."
            detail = _texto_para_la_persona(result.content.strip(), respaldo=respaldo)
        except TimeoutError:
            detail = "La preparación tardó demasiado y fue detenida. No preparé nada."
        except Exception as exc:  # noqa: BLE001 - cierre visible y seguro
            logger.warning("Falló el gate de destino del post de LinkedIn", exc_info=True)
            detail = f"No pude preparar el post de LinkedIn: {public_error_message(exc)}"

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


@router.post(
    "/{conversation_id}/messages",
    response_model=None,
)
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
) -> StreamingResponse | JSONResponse:
    tenant = current_user.tenant
    conversation = await repo.get_conversation(
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")

    if (
        await _get_pending_confirmation(
            redis_client,
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
        )
        is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Hay una confirmación pendiente. Aprueba o rechaza antes de enviar otro mensaje.",
        )

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
        # Comando local `/clear` (migración 0031): el turno nunca vuelve a ver
        # lo que quedó antes del límite que fijó `POST /{id}/clear`. Mismo
        # filtro que usa `GET /{id}` para lo que pinta la pantalla -- ver su
        # docstring para por qué es un límite y no un borrado.
        after=conversation.get("context_cleared_at"),
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

    active_key = _chat_turn_active_key(
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    followup_key = _chat_followup_count_key(
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if await redis_client.exists(active_key):
        return await _enqueue_chat_message_while_busy(
            repo=repo,
            redis_client=redis_client,
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            conversation_id=conversation_id,
            conversation=conversation,
            body=body,
            stored_user_content=stored_user_content,
            safe_user_text=safe_user_text,
            inline_credential=inline_credential,
            idempotency_key=idempotency_key,
            redis_idempotency_key=redis_idempotency_key,
            request_hash=request_hash,
            idempotency_ttl=idempotency_ttl,
            followup_key=followup_key,
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
    agent = _agent_for_request(request, llm_router, registry)
    unified_session = await load_unified_session(
        session,
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if unified_session is None:
        unified_session = _unified_session_for(
            tenant_id=tenant.tenant_id, conversation_id=conversation_id
        )
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
        unified_session=unified_session,
    )
    if not isinstance(direct_user_content, str):
        ctx.extras["direct_user_content"] = direct_user_content
    # Quien preguntó el turno pasado tiene que poder oír la respuesta de este:
    # el selector de capacidades vuelve a ofrecer esa tool sin depender de que
    # el usuario repita las palabras clave al contestar (ver el helper).
    ctx.extras["tools_con_pregunta_pendiente"] = _tools_con_pregunta_pendiente(history_rows)
    # Todo turno de ESTE endpoint lo escribió una persona en un chat -- por
    # definición. Las tools que distinguen "alguien está esperando" de "esto lo
    # disparó un reloj" (p. ej. el motor de posts de LinkedIn: su rescate de
    # borradores sin auditar es para personas, nunca para el cron) lo leen de
    # acá cuando el agente las invoca inline, donde nadie arma los args a mano.
    # El worker no pone esta marca: su señal viaja en el payload del job.
    ctx.extras["lo_pidio_una_persona"] = True
    # Memoria visual persistente (PHASE2.md §49): inyecta la instancia longeva
    # de ESTA conversación para que `Agent._run_turn` no la reinstancie cada
    # turno y el contexto visual (entidades, escena, texto) sobreviva entre
    # mensajes sin reenviar los píxeles.
    unified_session = ctx.extras["unified_session"]
    unified_session.user_id = str(current_user.user_id)
    unified_session.touch(modality="image" if not isinstance(direct_user_content, str) else "text")
    ctx.extras["visual_memory"] = unified_session.visual_memory
    # MCP bring-your-own (ARCHITECTURE.md §15): tools de los servidores MCP
    # que el tenant conectó, fusionadas SOLO para este turno — nunca tocan el
    # `registry` compartido (ver `Agent.run_turn`/`get_mcp_tools_for_tenant`).
    # `_extra_mcp_tools_or_empty` es fail-open con dos capas (ver su docstring).
    extra_tools = await _extra_conversation_tools(request, current_user)

    turn_owner = str(uuid.uuid4())
    active_claimed = await redis_client.set(
        active_key,
        turn_owner,
        ex=idempotency_ttl,
        nx=True,
    )
    if not active_claimed:
        return await _enqueue_chat_message_while_busy(
            repo=repo,
            redis_client=redis_client,
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            conversation_id=conversation_id,
            conversation=conversation,
            body=body,
            stored_user_content=stored_user_content,
            safe_user_text=safe_user_text,
            inline_credential=inline_credential,
            idempotency_key=idempotency_key,
            redis_idempotency_key=redis_idempotency_key,
            request_hash=request_hash,
            idempotency_ttl=idempotency_ttl,
            followup_key=followup_key,
        )

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
            await redis_client.delete(active_key)
            return _response_for_idempotency_record(
                record=raced_record,
                request_hash=request_hash,
                idempotency_key=idempotency_key,
            )
        if owner_token is None:  # pragma: no cover - defensa ante un Redis incompatible
            await redis_client.delete(active_key)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No se pudo reclamar este turno idempotente.",
            )

    # La reclamación atómica ocurre inmediatamente antes del primer efecto
    # persistente. Dos requests concurrentes con la misma clave nunca insertan
    # dos mensajes de usuario ni arrancan dos turnos del agente.
    needs_semantic_title = not str(conversation.get("title") or "").strip()
    modelo_elegido, esfuerzo_elegido = await _persist_chat_user_message(
        repo=repo,
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
        conversation=conversation,
        body=body,
        stored_user_content=stored_user_content,
        safe_user_text=safe_user_text,
        inline_credential=inline_credential,
    )
    # El gate de ceguera y el de Esfuerzo son SOLO de este turno: la selección
    # persistida arriba no se toca (ver `_seleccion_efectiva`).
    seleccion = _seleccion_efectiva(
        modelo=modelo_elegido,
        esfuerzo=esfuerzo_elegido,
        trae_imagen=_turno_trae_imagen(attachments),
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
        # Atajo directo: cuando el mensaje ya dice "post de linkedin", el
        # servidor llama al motor directo sin pedirle al modelo que decida qué
        # herramienta invocar. Ese "pedirle al modelo" es donde se cae hoy: con
        # `tools` cargadas y system largo, llama-4-scout escribe el tool_call
        # como texto (`[crear_post_linkedin](tema="...")`) que nadie ejecuta, y
        # la persona ve el chat en blanco. Aquí no se vuelve a preguntar cuando
        # la intención ya está resuelta.
        stream = _stream_direct_linkedin_post(
            tool=registry.get("crear_post_linkedin"),
            ctx=ctx,
            text=body.text,
            # Si la persona nombró una cuenta configurada en su mensaje, se usa y
            # el post sale en un turno. Si no la nombró, se deja en None: entonces sale
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
    elif (
        destino_libre := await _destino_de_respuesta_libre(ctx, body.text, history_rows)
    ) is not None:
        # La card de destino contestada ESCRIBIENDO ("acme", "en mi perfil
        # personal") en vez de tocar el botón. La card siempre permitió texto
        # libre; el atajo no lo entendía y la respuesta se iba al agente.
        #
        # Esta rama va ANTES que la de seguimiento a propósito (hallazgo del
        # panel de verificación): "Sobre eso, ponlo en mi personal" es una
        # respuesta de destino que EMPIEZA como un tema -- si el seguimiento se
        # evaluara primero, se robaba el turno, convertía la frase entera en
        # "tema" y el tema real del pedido se perdía.
        stream = _stream_direct_linkedin_post(
            tool=registry.get("crear_post_linkedin"),
            ctx=ctx,
            text=body.text,
            tema_override=_extraer_tema_del_historial_linkedin(history),
            destino_override=destino_libre,
            repo=repo,
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            settings=settings,
            conversation_id=conversation_id,
        )
    elif (tema_seguimiento := _es_seguimiento_de_tema_linkedin(body.text, history)) is not None:
        # El seguimiento "sobre: X" de un pedido reciente. Sin esta rama, ese
        # mensaje caía al agente genérico -- el único camino donde un LLM libre
        # decide qué tool llamar y con qué texto -- y de ahí salían los posts
        # con otro redactor y otra imagen que no se parecían en nada a los del
        # motor. Mismo principio que el resto del atajo: cuando el
        # texto ya dice qué hacer, ningún modelo tiene que adivinarlo.
        stream = _stream_direct_linkedin_post(
            tool=registry.get("crear_post_linkedin"),
            ctx=ctx,
            text=body.text,
            tema_override=tema_seguimiento,
            destino_override=(
                _destino_desde_el_texto(body.text) or _destino_del_pedido_en_historial(history)
            ),
            repo=repo,
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            settings=settings,
            conversation_id=conversation_id,
        )
    else:
        delegation = await prepare_chat_delegation(ctx, body.text)
        turn_user_text = delegation.user_text if delegation.delegated else user_text
        if delegation.delegated and not turn_user_text.strip() and delegation.initial_prefix:
            stream = _stream_delegation_confirmation(
                prefix=delegation.initial_prefix,
                repo=repo,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
            )
        else:
            events = agent.run_turn(
                ctx=ctx,
                persona=persona,
                history=history,
                user_text=turn_user_text,
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
                session=session,
                title_user_text=(
                    safe_user_text if needs_semantic_title and inline_credential is None else None
                ),
                initial_text=delegation.initial_prefix if delegation.delegated else "",
            )
    stream = _persist_session_after_stream(
        stream,
        db_session=session,
        unified_session=unified_session,
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )

    async def build_followup_stream() -> AsyncIterator[str]:
        return _build_followup_chat_stream(
            request=request,
            current_user=current_user,
            tenant=tenant,
            conversation_id=conversation_id,
            conversation=conversation,
            repo=repo,
            session=session,
            llm_router=llm_router,
            vault=vault,
            settings=settings,
            redis_client=redis_client,
        )

    stream = _stream_with_followup_chain(
        initial_stream=stream,
        redis_client=redis_client,
        active_key=active_key,
        followup_key=followup_key,
        build_followup_stream=build_followup_stream,
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


async def _resume_approved_turn(
    *,
    request: Request,
    current_user: CurrentUser,
    tenant: Any,
    conversation_id: uuid.UUID,
    conversation: dict[str, Any],
    tool_call_id: str,
    pending: dict[str, Any],
    repo: Repo,
    session: Any,
    llm_router: LLMRouter,
    vault: Any,
    settings: Settings,
    redis_client: redis_asyncio.Redis,
) -> StreamingResponse:
    """Reanuda un turno aprobado desde su foto serializada (`pending`).

    `pending` es el payload de una confirmación (`{name, args, pending_turn?}`)
    tal como lo produce `_pop_pending_confirmation` desde Redis o como lo
    conserva `pending_approvals.agent_snapshot` en la base — el mismo
    consumidor sirve a `POST /conversations/{id}/confirm` (caché rápido) y a
    `POST /v1/approvals/{id}/approve` (durable tras reload).
    """

    serialized_turn = pending.get("pending_turn")
    if serialized_turn is not None:
        try:
            pending_turn = PendingAgentTurn.model_validate(serialized_turn)
        except Exception as exc:  # noqa: BLE001 - payload inválido, fail closed
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El turno pendiente está dañado y no puede reanudarse con seguridad.",
            ) from exc
        call_ids = {call.id for call in pending_turn.tool_calls}
        if tool_call_id not in call_ids or tool_call_id in pending_turn.approved_tool_call_ids:
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
            approved_tool_calls={tool_call_id},
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
        agent = _agent_for_request(request, llm_router, registry)
        events = agent.resume_turn(
            ctx=ctx,
            pending=pending_turn,
            approved_tool_call_id=tool_call_id,
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
                session=session,
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
        approved_tool_calls={tool_call_id},
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
            tool_call_id=tool_call_id,
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

    return await _resume_approved_turn(
        request=request,
        current_user=current_user,
        tenant=tenant,
        conversation_id=conversation_id,
        conversation=conversation,
        tool_call_id=body.tool_call_id,
        pending=pending,
        repo=repo,
        session=session,
        llm_router=llm_router,
        vault=vault,
        settings=settings,
        redis_client=redis_client,
    )
