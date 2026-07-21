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
import functools
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

import redis.asyncio as redis_asyncio
from edecan_core.agent import (
    Agent,
    artifact_refs_from_tool_data,
    mission_ref_from_tool_data,
    rich_blocks_from_tool_data,
)
from edecan_core.memory import HashEmbedder, OpenAICompatEmbedder, PgMemoryStore
from edecan_core.queue import enqueue
from edecan_core.tools import Tool, ToolContext, ToolResult
from edecan_llm.base import ChatMessage
from edecan_llm.router import LLMRouter
from edecan_schemas import (
    UNLIMITED,
    ChatMessageIn,
    PendingAgentTurn,
    PendingConfirmationOut,
    PersonaConfig,
)
from edecan_schemas.plans import LIMIT_MESSAGES_PER_DAY
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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


class ConversationIn(BaseModel):
    title: str | None = None
    channel: Literal["web", "voice", "phone", "api"] = "web"


class ConfirmIn(BaseModel):
    tool_call_id: str
    approved: bool


def _conversation_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row.get("title"),
        "channel": row.get("channel", "web"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _message_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row.get("content"),
        "tool_calls": row.get("tool_calls"),
        "tokens_in": row.get("tokens_in", 0),
        "tokens_out": row.get("tokens_out", 0),
        "created_at": row.get("created_at"),
    }


@router.get("")
async def list_conversations(
    current_user: CurrentUser = Depends(get_current_user), repo: Repo = Depends(get_repo)
) -> list[dict[str, Any]]:
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
        title=body.title,
        channel=body.channel,
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
        tenant_id=current_user.tenant_id, conversation_id=conversation_id
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


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    deleted = await repo.delete_conversation(
        tenant_id=current_user.tenant_id, conversation_id=conversation_id
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


def _attachment_context_line(item: dict[str, Any]) -> str:
    return (
        f"- file_id={item.get('file_id')} · {item.get('filename') or 'archivo'}"
        f" · {item.get('mime') or 'application/octet-stream'}"
    )


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


def _rows_to_chat_messages(rows: list[dict[str, Any]]) -> list[ChatMessage]:
    return [
        ChatMessage(role=row["role"], content=_extract_text(row.get("content")))
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
    return bool(
        settings.OPENAI_COMPAT_BASE_URL
        and settings.OPENAI_COMPAT_API_KEY
        and settings.OPENAI_COMPAT_API_KEY != _OPENAI_COMPAT_API_KEY_PLACEHOLDER
        and settings.EMBEDDINGS_MODEL
        and settings.EMBEDDINGS_MODEL != _EMBEDDINGS_MODEL_PLACEHOLDER
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


def _current_pending_confirmation_key(
    *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> str:
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
    *, tenant_id: uuid.UUID, conversation_id: uuid.UUID, idempotency_key: uuid.UUID
) -> str:
    return f"chat_idempotency:{tenant_id}:{conversation_id}:{idempotency_key}"


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


async def _stream_and_complete_idempotency(
    *,
    stream: AsyncIterator[str],
    redis_client: redis_asyncio.Redis,
    redis_key: str,
    request_hash: str,
    owner_token: str,
    ttl_seconds: int,
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
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "chunk":
                assert isinstance(payload, str)
                yield payload
            elif kind == "done":
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


async def _stream_agent_events(
    *,
    events: AsyncIterator[Any],
    repo: Repo,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    settings: Settings,
    redis_client: redis_asyncio.Redis,
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
        yield _format_sse("error", {"type": "error", "message": str(exc)})


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


async def _stream_approved_confirmation(
    *,
    tool_call_id: str,
    tool: Tool,
    tool_name: str,
    tool_args: dict[str, Any],
    ctx: ToolContext,
    repo: Repo,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
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
        blocks = rich_blocks_from_tool_data(
            result.data,
            presentation=result.presentation,
            artifacts=artifacts,
        )
        tool_end = {
            "type": "tool_end",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "result_preview": preview,
            "artifacts": [item.model_dump(mode="json") for item in artifacts],
            "blocks_version": 1,
            "blocks": [item.model_dump(mode="json") for item in blocks],
            "mission_id": (
                str(mission_id) if (mission_id := mission_ref_from_tool_data(result.data)) else None
            ),
        }
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
        yield _format_sse("error", {"type": "error", "message": str(exc)})


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
        tenant_id=tenant.tenant_id, conversation_id=conversation_id
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")

    request_hash: str | None = None
    redis_idempotency_key: str | None = None
    idempotency_ttl = max(60, int(settings.CHAT_IDEMPOTENCY_TTL_SECONDS))
    if idempotency_key is not None:
        request_hash = _message_request_hash(body)
        redis_idempotency_key = _message_idempotency_key(
            tenant_id=tenant.tenant_id,
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
        tenant_id=tenant.tenant_id, conversation_id=conversation_id
    )
    history = _rows_to_chat_messages(history_rows)

    attachments = await _resolve_message_attachments(
        repo=repo,
        tenant_id=tenant.tenant_id,
        file_ids=body.attachments,
    )
    stored_user_content: dict[str, Any] = {"text": body.text}
    if attachments:
        stored_user_content["attachments"] = attachments
    user_text = _extract_text(stored_user_content)

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
        phone_call_dispatcher=phone_tool_dispatcher_for(
            request=request,
            tenant_id=tenant.tenant_id,
            user_id=current_user.user_id,
            repo=repo,
            vault=vault,
        ),
        profile_context=profile_context,
    )
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
    await repo.add_message(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        role="user",
        content=stored_user_content,
    )

    events = agent.run_turn(
        ctx=ctx,
        persona=persona,
        history=history,
        user_text=user_text,
        flags=tenant.flags,
        extra_tools=extra_tools,
    )
    stream = _stream_agent_events(
        events=events,
        repo=repo,
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        user_id=current_user.user_id,
        settings=settings,
        redis_client=redis_client,
    )
    if idempotency_key is None:
        # Compatibilidad total: clientes existentes conservan streaming en vivo.
        return StreamingResponse(stream, media_type="text/event-stream")

    # Los eventos salen en vivo. El productor queda desacoplado del socket y
    # completa el replay aun si el cliente pierde la conexión a mitad del turno.
    assert request_hash is not None
    assert redis_idempotency_key is not None
    assert owner_token is not None
    live_stream = _stream_and_complete_idempotency(
        stream=stream,
        redis_client=redis_client,
        redis_key=redis_idempotency_key,
        request_hash=request_hash,
        owner_token=owner_token,
        ttl_seconds=idempotency_ttl,
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
        tenant_id=tenant.tenant_id, conversation_id=conversation_id
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
            conversation_id=conversation_id,
        ),
        media_type="text/event-stream",
    )
