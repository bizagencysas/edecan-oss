"""Turnos reales de bots persistentes (modelo Grok Bot en Edecán.app).

Cada mensaje visible en chats de bot/equipo es un `Agent.run_turn` completo:
persona del worker, tools filtradas (IDE/companion en Mac), persistencia con
metadata de remitente. Prohibido ACK sintético o mensajes stand-in.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Any

from edecan_core.bot_harness import (
    AUTONOMY_LEVEL_ASK,
    AUTONOMY_LEVEL_FULL,
    AUTONOMY_LEVELS,
    append_skills_to_persona,
    autonomy_allows_operation,
    bot_preapproved_tool_calls,
    build_skills_context,
    mcp_preapproved_tokens,
    parse_mcp_grants,
    tool_local_operation,
    worker_chat_extras,
)
from edecan_core.bot_persona import persona_from_worker, worker_display_name
from edecan_core.bot_registry import build_worker_registry
from edecan_core.companion_access import companion_para
from edecan_core.safety import redact
from edecan_core.session_store import load_unified_session
from edecan_core.tools import ToolContext, ToolRegistry
from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.chat_context import (
    ChatContextLimits,
    build_contextual_history,
    resumen_llm_hilo_anterior,
)
from edecan_api.config import Settings
from edecan_api.deps import CurrentUser

logger = logging.getLogger(__name__)

DEFAULT_MESSAGE_LIST_LIMIT = 50
MAX_MESSAGE_LIST_LIMIT = 200


def clamp_message_limit(limit: int | None) -> int:
    """Tope del GET de historial visible. No recorta el contexto del modelo."""
    if limit is None:
        return DEFAULT_MESSAGE_LIST_LIMIT
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_MESSAGE_LIST_LIMIT
    return max(1, min(n, MAX_MESSAGE_LIST_LIMIT))


def _worker_autonomy_level(worker: Mapping[str, Any]) -> str:
    """Nivel de autonomía efectivo del worker para el turno de chat (BOTS-02).

    Mismo contrato que el runner headless (`run_persistent_agent`): vacío o
    desconocido cae a `ask` (solo lectura, fail-closed) — nunca a un nivel más
    permisivo por un campo ausente o un typo del dueño.
    """
    nivel = str(worker.get("autonomy_level") or "").strip()
    return nivel if nivel in AUTONOMY_LEVELS else AUTONOMY_LEVEL_ASK


def _filter_registry_by_autonomy(registry: ToolRegistry, autonomy_level: str) -> ToolRegistry:
    """Restringe el registry de tools del turno de chat por nivel de autonomía.

    `full` conserva todo lo que `build_worker_registry` ya autorizó. Los niveles
    restrictivos descartan, ANTES de ofrecerlas al modelo, toda tool cuya
    operación local no esté permitida (una tool no clasificable → `None` →
    rechazada, fail-closed). Espeja `edecan_automations.runner` sin importar el
    paquete de automatizaciones en el paquete API.
    """
    if autonomy_level == AUTONOMY_LEVEL_FULL:
        return registry
    # Dobles de prueba pueden inyectar un registry que NO es `ToolRegistry`
    # (p. ej. `object()` en `test_bot_turn_service`): no hay herramientas que
    # filtrar ahí, se conserva tal cual. En producción `build_worker_registry`
    # siempre devuelve un `ToolRegistry` real.
    if not hasattr(registry, "all"):
        return registry
    restringido = ToolRegistry()
    for tool in registry.all():
        operation = tool_local_operation(
            name=tool.name,
            dangerous=bool(
                getattr(tool, "intrinsically_dangerous", getattr(tool, "dangerous", False))
            ),
            category=getattr(tool, "category", None),
        )
        if autonomy_allows_operation(autonomy_level, operation):
            restringido.register(tool)
    return restringido


def _filter_extra_tools_by_autonomy(extra_tools: list[Any], autonomy_level: str) -> list[Any]:
    """Filtra las tools extra (MCP dinámicas + persona) por nivel de autonomía
    ANTES de ofrecerlas al modelo (BOTS-02). `full` no filtra nada; los niveles
    restrictivos descartan toda tool cuya operación local no esté permitida (una
    tool MCP no clasificable → `None` → rechazada, fail-closed)."""
    if autonomy_level == AUTONOMY_LEVEL_FULL:
        return list(extra_tools)
    filtradas: list[Any] = []
    for tool in extra_tools:
        name = str(getattr(tool, "name", "") or "")
        operation = tool_local_operation(
            name=name,
            input_schema=getattr(tool, "input_schema", None),
            dangerous=bool(
                getattr(tool, "intrinsically_dangerous", getattr(tool, "dangerous", False))
            ),
            category=getattr(tool, "category", None),
        )
        if autonomy_allows_operation(autonomy_level, operation):
            filtradas.append(tool)
    return filtradas


def _conversation_epoch_key(*, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    return f"conversation_epoch:{tenant_id}:{conversation_id}"


async def increment_conversation_epoch(
    redis_client: Any, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> int:
    """Incrementa el epoch de una conversación (BOTS-09) para invalidar
    productores y refreshes viejos tras `/clear` o DELETE del chat de un bot.

    Redis es el único almacén compartido entre procesos (no puede ser un dict
    módulo-nivel, ver la restricción de réplica única en `persistent_agents`).
    El epoch es un contador de invalidación de caché, no una fuente de verdad
    durable: si Redis se pierde, el contador reinicia — aceptable para su rol.
    """
    key = _conversation_epoch_key(tenant_id=tenant_id, conversation_id=conversation_id)
    return int(await redis_client.incr(key))


async def get_conversation_epoch(
    redis_client: Any, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> int:
    """Epoch actual de la conversación (0 si nunca se limpió/borró)."""
    key = _conversation_epoch_key(tenant_id=tenant_id, conversation_id=conversation_id)
    raw = await redis_client.get(key)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _encode_message_cursor(created_at: Any, message_id: Any) -> str:
    """Cursor opaco de paginación (BOTS-12): el par `(created_at, id)` del
    mensaje más viejo de la página, serializado para que el cliente lo trate
    como opaco y lo devuelva tal cual en el siguiente `before`."""
    iso = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at)
    return f"{iso},{message_id}"


def _decode_message_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Inverso de `_encode_message_cursor`; 422 si el cursor está malformado."""
    try:
        iso, id_part = cursor.split(",", 1)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Cursor de historial inválido.") from exc
    try:
        # El offset del timestamp (p. ej. `+00:00`) viaja dentro del query
        # string y el cliente/form lo decodifica a un espacio en el camino.
        # Restaurarlo antes de parsear: sin esto, el cursor de primera página
        # (`9999-12-31T23:59:59.999999+00:00,...`) llegaba como
        # `...999999 00:00,...` y `fromisoformat` lo rechazaba con 422.
        iso = iso.replace(" ", "+")
        return datetime.fromisoformat(iso), uuid.UUID(id_part)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Cursor de historial inválido.") from exc


async def _extra_bot_turn_tools(request: Request, user: CurrentUser) -> list[Any]:
    """Persona tools + MCP del tenant (mismo contrato que el chat principal)."""
    from edecan_api.deps import get_mcp_tools_for_tenant
    from edecan_api.persona_tools import conversation_persona_tools

    try:
        mcp_tools = await get_mcp_tools_for_tenant(request, user)
    except Exception:  # noqa: BLE001 - fail-open como `_extra_mcp_tools_or_empty`
        logger.warning(
            "get_mcp_tools_for_tenant lanzó en turno de bot; sigue sin MCP efímeras.",
            exc_info=True,
        )
        mcp_tools = []
    return [*conversation_persona_tools(), *mcp_tools]


_WORKER_COLUMNS = (
    "id, tenant_id, user_id, name, purpose, workspace, display_name, avatar, "
    "role_title, role_short, job_description, personality, communication_style, "
    "instructions, constraints, approval_policy, autonomy_level, model_policy, "
    "tools, permissions, memory, schedule, budget, status, enabled, relation, conversation_id"
)

def _redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_payload(item) for key, item in value.items()}
    return value


def _parse_tool_calls(raw: Any) -> list[Any] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, list):
        return raw
    return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return str(parsed.get("text") or "")
        except json.JSONDecodeError:
            return content
    return ""


def normalize_stored_message(row: Mapping[str, Any]) -> dict[str, Any]:
    """Contrato unificado para web/iOS: text, sender_id, sender_name."""
    content = row.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {"text": content}
    if not isinstance(content, dict):
        content = {}
    role = str(row.get("role") or "assistant")
    sender_id = content.get("sender_id")
    sender_name = content.get("sender_name")
    if not sender_id:
        if role == "user":
            sender_id = "user"
            sender_name = sender_name or "Tú"
        else:
            sender_id = content.get("agent_id") or "assistant"
            sender_name = sender_name or "Asistente"
    payload: dict[str, Any] = {
        "id": str(row.get("id")),
        "role": role,
        "text": _content_text(content),
        "sender_id": str(sender_id) if sender_id is not None else None,
        "sender_name": str(sender_name) if sender_name is not None else None,
        "created_at": row.get("created_at"),
        "conversation_id": str(row.get("conversation_id")) if row.get("conversation_id") else None,
        # Eventos de narración entre bots («Escribió a X», «X me escribió»):
        # viajan con `kind=evento` + los datos que la fila necesita para
        # pintarse (quién, y la cara del otro bot). Ausente = mensaje normal.
        **(
            {
                "kind": "evento",
                "evento": str(content.get("evento") or ""),
                "de": str(content.get("de") or ""),
                "goal": str(content.get("goal") or ""),
                "cara": content.get("cara"),
                **(
                    {
                        "assigned_worker_id": str(
                            content.get("assigned_worker_id")
                            or content.get("asignado_id")
                            or ""
                        ),
                        "assigned_worker_name": str(
                            content.get("assigned_worker_name")
                            or content.get("asignado_nombre")
                            or content.get("de")
                            or ""
                        ),
                        **(
                            {"motivo": str(content.get("motivo"))}
                            if content.get("motivo")
                            else {}
                        ),
                    }
                    if content.get("evento") == "asignacion"
                    else {}
                ),
            }
            if content.get("kind") == "evento"
            else {}
        ),
        **({"adjuntos": content.get("attachments")} if content.get("attachments") else {}),
        # Beats mid-turn (`avisar_avance`) persisten con kind=aviso para que iOS
        # los pinte como «En vivo» al recargar el historial.
        **({"kind": "aviso"} if content.get("kind") == "aviso" else {}),
    }
    tool_calls = _parse_tool_calls(row.get("tool_calls"))
    if tool_calls:
        payload["tool_calls"] = _redact_payload(tool_calls)
    return payload


async def load_worker(
    session: AsyncSession, user: CurrentUser, worker_id: uuid.UUID
) -> dict[str, Any]:
    result = await session.execute(
        text(
            f"SELECT {_WORKER_COLUMNS} FROM persistent_agents "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "id": str(worker_id),
        },
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Bot no encontrado.")
    return dict(row)


async def ensure_worker_conversation(
    session: AsyncSession,
    user: CurrentUser,
    worker: Mapping[str, Any],
) -> uuid.UUID:
    # The caller's worker mapping is only a snapshot. Lock and re-read the
    # parent row so two first requests (including requests in different API
    # processes) cannot each create and attach a different conversation.
    current = await session.execute(
        text(
            "SELECT conversation_id FROM persistent_agents "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id "
            "FOR UPDATE"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "id": str(worker["id"]),
        },
    )
    current_row = current.mappings().first()
    if current_row is None:
        raise HTTPException(status_code=404, detail="Bot no encontrado.")
    conversation_id = current_row["conversation_id"]
    if conversation_id is not None:
        return uuid.UUID(str(conversation_id))
    title = f"Bot: {worker_display_name(worker)}"
    created = await session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, user_id, title, channel) "
            "VALUES (gen_random_uuid(), :tenant_id, :user_id, :title, 'web') "
            "RETURNING id"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "title": title,
        },
    )
    new_id = created.mappings().first()["id"]
    await session.execute(
        text(
            "UPDATE persistent_agents SET conversation_id = :cid, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id "
            "AND conversation_id IS NULL"
        ),
        {
            "cid": str(new_id),
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "id": str(worker["id"]),
        },
    )
    return uuid.UUID(str(new_id))


async def persist_chat_message(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: str,
    texto: str,
    sender_id: str,
    sender_name: str,
    adjuntos: list[dict[str, str | None]] | None = None,
) -> None:
    """Persiste un mensaje del chat del bot.

    El parámetro se llama `texto` y NO `text` a propósito: un parámetro `text`
    taparía el import `sqlalchemy.text` DENTRO de este cuerpo y cada insert
    reventaría con `TypeError: 'str' object is not callable` — el fallo exacto
    que dejó los chats de bot en silencio (HTTP 200, stream vacío, y el
    teléfono pintando «Se perdió la conexión con Edecán»).
    """
    await session.execute(
        text(
            "INSERT INTO messages (id, tenant_id, conversation_id, role, content) "
            "VALUES (gen_random_uuid(), :tenant_id, :conversation_id, :role, :content ::jsonb)"
        ),
        {
            "tenant_id": str(tenant_id),
            "conversation_id": str(conversation_id),
            "role": role,
            "content": json.dumps(
                {
                    "text": texto.strip(),
                    "sender_id": sender_id,
                    "sender_name": sender_name,
                    **({"attachments": adjuntos} if adjuntos else {}),
                },
                ensure_ascii=False,
            ),
        },
    )


async def persist_team_assignment_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    assignee: Mapping[str, Any],
    reason: str,
) -> None:
    """Evento visible «Se lo pasé a X» antes del turno del bot elegido."""

    nombre = worker_display_name(assignee)
    motivo = (reason or "mejor coincidencia con tu pedido").strip()
    worker_id = str(assignee.get("id") or "")
    payload = {
        "kind": "evento",
        "evento": "asignacion",
        "text": f"Se lo pasé a {nombre}",
        "de": nombre,
        "goal": motivo,
        "assigned_worker_id": worker_id,
        "assigned_worker_name": nombre,
        "asignado_id": worker_id,
        "asignado_nombre": nombre,
        "motivo": motivo,
        "cara": assignee.get("avatar"),
    }
    await session.execute(
        text(
            "INSERT INTO messages (id, tenant_id, conversation_id, role, content) "
            "VALUES (gen_random_uuid(), :tenant_id, :cid, 'assistant', :content ::jsonb)"
        ),
        {
            "tenant_id": str(tenant_id),
            "cid": str(conversation_id),
            "content": json.dumps(payload, ensure_ascii=False),
        },
    )


async def list_normalized_messages(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    limit: int | None = None,
    before: str | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Historial normalizado del chat del bot, paginable hacia atrás (BOTS-12).

    Sin `before` (protocolo legacy) devuelve la lista plana de siempre: los
    últimos `limit` mensajes en orden ascendente. Con `before` devuelve una
    `HistoryPage` (`messages`, `next_cursor`, `has_more`) con los mensajes
    ANTERIORES al cursor, en el mismo orden ascendente. El cursor es opaco:
    el cliente lo devuelve tal cual en el siguiente `before`.
    """
    capped = clamp_message_limit(limit)
    paginar = before is not None
    # Se pide una fila extra solo al paginar, para saber si hay más sin una
    # segunda consulta. El camino legacy conserva el `LIMIT` exacto de siempre.
    fetch_limit = capped + 1 if paginar else capped
    before_clause = ""
    params: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "conversation_id": str(conversation_id),
        "limit": fetch_limit,
    }
    if paginar:
        before_created_at, before_id = _decode_message_cursor(before)
        before_clause = "AND (created_at, id) < (:before_created_at, :before_id)"
        params["before_created_at"] = before_created_at
        params["before_id"] = before_id
    result = await session.execute(
        text(
            "SELECT id, conversation_id, role, content, tool_calls, created_at FROM ("
            "SELECT id, conversation_id, role, content, tool_calls, created_at "
            "FROM messages WHERE tenant_id = :tenant_id AND conversation_id = :conversation_id "
            f"{before_clause} "
            "ORDER BY created_at DESC, id DESC "
            "LIMIT :limit"
            ") recientes ORDER BY created_at ASC, id ASC"
        ),
        params,
    )
    rows = [normalize_stored_message(row) for row in result.mappings().all()]
    if not paginar:
        return rows
    has_more = len(rows) > capped
    # ACT-02: al paginar hacia atrás, la página son los `capped` mensajes MÁS
    # NUEVOS de la ventana consultada (los pegados al cursor anterior):
    # `rows[-capped:]`, no `rows[:capped]` (que devolvía los más viejos y
    # perdía/duplicaba mensajes en el borde).
    rows = rows[-capped:] if has_more else rows
    next_cursor: str | None = None
    if has_more and rows:
        # El cursor es el mensaje MÁS VIEJO de la página (rows[0] en orden
        # ascendente): la siguiente página trae estrictamente lo anterior a él.
        oldest = rows[0]
        next_cursor = _encode_message_cursor(oldest.get("created_at"), oldest.get("id"))
    return {"messages": rows, "next_cursor": next_cursor, "has_more": has_more}


async def stream_worker_turn(
    *,
    request: Request,
    session: AsyncSession,
    user: CurrentUser,
    settings: Settings,
    worker: Mapping[str, Any],
    conversation_id: uuid.UUID,
    user_text: str,
    speaker_role: str = "user",
    speaker_id: str = "user",
    speaker_name: str = "Tú",
    run_turn: bool = True,
    persist_user_message: bool = True,
    attachments: list[str] | None = None,
    seleccion_modelo: str | None = None,
    seleccion_esfuerzo: str | None = None,
) -> AsyncIterator[str]:
    """Ejecuta un turno real del worker y emite SSE estándar de chat.

    `persist_user_message=False` existe para los avisos internos (p. ej. el
    ack de identidad tras un renombre): la instrucción llega al modelo pero NO
    aparece en el chat como mensaje del dueño — solo se persiste lo que el bot
    responda.
    """
    from edecan_api.deps import get_llm_router, get_redis, get_repo, get_vault
    from edecan_api.routers.conversations import (
        _agent_for_request,
        _build_ctx,
        _stream_agent_events,
        _tools_con_pregunta_pendiente,
        _unified_session_for,
        get_tool_registry,
    )
    from edecan_api.routers.perfil import profile_context_for

    clean = user_text.strip()

    # Adjuntos (imágenes/documentos que el dueño mandó): se resuelven por
    # tenant, quedan referenciados en el mensaje persistido (para que el chat
    # los pinte) y el modelo recibe los refs con el file_id para leerlos con
    # sus tools — el mismo contrato del chat principal.
    adjuntos_resueltos: list[dict[str, str | None]] = []
    ids_adjuntos: list[uuid.UUID] = []
    if attachments:
        from edecan_api.repo import SqlRepo
        from edecan_api.routers.conversations import _resolve_message_attachments

        try:
            ids_adjuntos = [uuid.UUID(a) for a in attachments if a.strip()]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="file_id adjunto inválido.") from exc
        adjuntos_resueltos = await _resolve_message_attachments(
            repo=SqlRepo(session), tenant_id=user.tenant_id, file_ids=ids_adjuntos
        )

    if not clean:
        if not adjuntos_resueltos:
            raise HTTPException(status_code=422, detail="El mensaje no puede estar vacío.")
        clean = "Revisa los archivos adjuntos."

    if persist_user_message:
        await persist_chat_message(
            session,
            tenant_id=user.tenant_id,
            conversation_id=conversation_id,
            role=speaker_role,
            texto=clean,
            sender_id=speaker_id,
            sender_name=speaker_name,
            adjuntos=adjuntos_resueltos,
        )
        if adjuntos_resueltos:
            refs = "\n\nArchivos adjuntos privados:\n" + "\n".join(
                f"- file_id={a['file_id']} · {a['filename'] or 'archivo'} · {a['mime'] or '?'}"
                f" — usa leer_archivo(file_id='{a['file_id']}') para verlos"
                for a in adjuntos_resueltos
                if isinstance(a, dict) and a.get("file_id")
            )
            clean = (clean + refs).strip()

    if not run_turn:
        from edecan_api.routers.conversations import _format_sse

        yield _format_sse("message.done", {"type": "done", "usage": {}})
        return

    # Llamadas DIRECTAS a los proveedores de `deps` con sus argumentos
    # resueltos (el `Depends(...)` de sus firmas solo aplica cuando FastAPI
    # las resuelve). OJO: get_repo/get_vault/get_llm_router son `async def` —
    # sin `await` entregan una coroutine y el turno muere con
    # `'coroutine' object has no attribute 'list_messages'` a mitad del stream.
    # `vault` comparte la MISMA sesión que `repo` a propósito (misma
    # transacción, ver el comentario de `deps.get_vault`).
    repo = await get_repo(session)
    vault = await get_vault(session, settings)
    llm_router = await get_llm_router(request)
    redis_client = get_redis(settings)

    history_rows = await repo.list_messages(
        tenant_id=user.tenant_id,
        conversation_id=conversation_id,
        limit=max(50, int(settings.BOT_CONTEXT_MAX_MESSAGES)),
        after=None,
    )
    # Contexto ESCALONADO (política de costos): solo los últimos
    # BOT_CONTEXT_RECENT_MESSAGES viajan crudos; el resto de la historia se
    # compacta con el resumen LLM cacheado de `resumen_llm_hilo_anterior`
    # (solo se paga cuando el hilo viejo no cabe en el presupuesto, y queda
    # en caché por hilo). La memoria del bot NO se inyecta completa: el
    # agente la busca a demanda con búsqueda semántica por turno.
    limits = ChatContextLimits(
        enabled=settings.BOT_CONTEXT_MAX_MESSAGES > 0,
        recent_messages=min(
            int(getattr(settings, "BOT_CONTEXT_RECENT_MESSAGES", 20) or 20),
            settings.BOT_CONTEXT_MAX_MESSAGES,
        ),
        max_messages=settings.BOT_CONTEXT_MAX_MESSAGES,
        max_chars=settings.BOT_CONTEXT_MAX_CHARS,
        cross_chat_enabled=False,
        cross_chat_conversations=0,
        cross_chat_messages_per_conversation=0,
        cross_chat_max_chars=0,
    )
    resumen_llm = await resumen_llm_hilo_anterior(history_rows, limits, llm_router=llm_router)
    history = build_contextual_history(
        current_rows=history_rows,
        cross_chat_rows=[],
        limits=limits,
        current_summary=resumen_llm or None,
    )

    persona = persona_from_worker(worker)
    skills_context = await build_skills_context(session, user.tenant_id, user.user_id)
    append_skills_to_persona(persona, skills_context)
    extra_tools = await _extra_bot_turn_tools(request, user)
    profile_context = await profile_context_for(session, user.tenant_id, user.user_id)
    full_registry = get_tool_registry(request)
    registry = build_worker_registry(
        full_registry,
        worker,
        local_mode=bool(getattr(settings, "EDECAN_LOCAL_MODE", False)),
    )
    # BOTS-02: el chat interactivo aplica la MISMA matriz de autonomía que el
    # runner headless. `read_only`/`ask` dejan solo lectura aunque el modelo pida
    # escritura; `full` conserva todo lo autorizado. Se filtra ANTES de entregar
    # registry y tools extra al Agent — igual que `run_persistent_agent`
    # restringe el dispatcher. La instrucción al modelo NO sustituye este control.
    autonomy_level = _worker_autonomy_level(worker)
    registry = _filter_registry_by_autonomy(registry, autonomy_level)
    extra_tools = _filter_extra_tools_by_autonomy(extra_tools, autonomy_level)
    # Política de costos (dueño, 6-sep): los chats de bot corren con el
    # modelo del CHAT (Luna en Azure, el de `perfiles.chat_rapido` en
    # Workers AI), NO con el perfil profundo — el profundo resolvía a Sol en
    # Azure y un solo día de charla con los bots gastó 2M de tokens de Sol
    # (~100 USD). El escritor de posts SÍ conserva el profundo (calidad
    # pedida a propósito). Si un bot necesita Sol/Astra para algo puntual,
    # el dueño lo pide en el chat principal.
    agent = _agent_for_request(request, llm_router, registry, model_alias="chat_rapido")

    unified_session = await load_unified_session(
        session,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        conversation_id=conversation_id,
    )
    if unified_session is None:
        unified_session = _unified_session_for(
            tenant_id=user.tenant_id, conversation_id=conversation_id
        )

    companion = companion_para(user.tenant_id)
    local_mode = bool(getattr(settings, "EDECAN_LOCAL_MODE", False))
    approved = bot_preapproved_tool_calls(
        companion_present=companion is not None,
        local_mode=local_mode,
    )
    # BOTS-06: las tools MCP no se pre-aprueban por prefijo. Solo un grant
    # explícito del dueño (`worker.approval_policy.mcp_grants`), atado a la
    # versión de definición ACTUAL de cada tool, produce un token de
    # pre-aprobación. Una tool MCP sin grant (o con definición cambiada) sigue
    # exigiendo tarjeta de confirmación.
    mcp_grants = parse_mcp_grants(worker.get("approval_policy"))
    approved |= mcp_preapproved_tokens(tools=extra_tools, grants=mcp_grants)
    ctx: ToolContext = _build_ctx(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        session=session,
        settings=settings,
        llm_router=llm_router,
        vault=vault,
        persona=persona,
        request=request,
        repo=repo,
        approved_tool_calls=approved,
        flags=user.tenant.flags,
        conversation_id=conversation_id,
        phone_call_dispatcher=None,
        profile_context=profile_context,
        unified_session=unified_session,
    )
    ctx.extras["worker_id"] = str(worker["id"])
    # delegar_al_ide SIN capability de escritorio (VPS) va por el companion:
    # la tool necesita el manager del app.state para hablar con la Mac.
    _app = getattr(request, "app", None)
    ctx.extras["companion_manager"] = getattr(
        getattr(_app, "state", None), "companion_manager", None
    )
    ctx.extras["lo_pidio_una_persona"] = speaker_id in ("user", "owner", "human")
    ctx.extras["tools_con_pregunta_pendiente"] = _tools_con_pregunta_pendiente(history_rows)
    unified_session.user_id = str(user.user_id)
    unified_session.touch(modality="text")
    ctx.extras["visual_memory"] = unified_session.visual_memory

    bot_name = worker_display_name(worker)
    bot_id = str(worker["id"])
    # Canal de narración en vivo: `avisar_avance` escribe avisos del bot en
    # SU chat — el dueño los ve al instante (regla «avisan todo, como los
    # LLM que narran cada paso»).
    ctx.extras["worker_chat"] = worker_chat_extras(worker, conversation_id)
    from edecan_api.routers.conversations import SeleccionDeModelo

    seleccion = (
        SeleccionDeModelo(modelo=seleccion_modelo, esfuerzo=seleccion_esfuerzo)
        if seleccion_modelo or seleccion_esfuerzo
        else None
    )
    events = agent.run_turn(
        ctx=ctx,
        persona=persona,
        history=history,
        user_text=clean,
        flags=user.tenant.flags,
        extra_tools=extra_tools,
        seleccion=seleccion,
    )
    stream = _stream_agent_events(
        events=events,
        repo=repo,
        tenant_id=user.tenant_id,
        conversation_id=conversation_id,
        user_id=user.user_id,
        settings=settings,
        redis_client=redis_client,
        llm_router=llm_router,
        session=session,
        assistant_content_extra={"sender_id": bot_id, "sender_name": bot_name},
        approval_snapshot_extra={"worker_id": bot_id},
        # Chats de bot/equipo: burbuja por mensaje, no un bloque pegado.
        split_messages=True,
    )
    async for chunk in stream:
        yield chunk


async def ack_cambio_identidad(
    request: Request,
    *,
    user: CurrentUser,
    settings: Settings,
    worker_id: uuid.UUID,
    resumen: str,
) -> None:
    """Turno real del bot confirmando su nueva identidad tras un renombre.

    Es el «OK, ese es mi nuevo nombre» estilo Grok Bot: el modelo escribe
    (cero copy de Python), la respuesta queda persistida en el chat del bot y
    el aviso que la dispara NO se persiste como mensaje visible
    (`persist_user_message=False`). Corre en background: el PATCH responde
    rápido y el ack llega al chat un momento después. Best-effort: un fallo
    aquí jamás revienta el PATCH ni deja estado sucio.
    """
    from edecan_db.session import get_session

    instruccion = (
        "(Aviso interno del sistema — no lo cites ni lo menciones como aviso): el dueño "
        f"acaba de actualizar tu identidad. {resumen} "
        "Reacciona en UNA o dos frases, en tu voz natural, confirmando quién eres ahora "
        "— como lo haría un compañero al que le ajustan su rol. No uses herramientas y "
        "no hagas preguntas."
    )
    try:
        async with get_session(user.tenant_id) as session:
            worker = await load_worker(session, user, worker_id)
            conversation_id = await ensure_worker_conversation(session, user, worker)
            async for _chunk in stream_worker_turn(
                request=request,
                session=session,
                user=user,
                settings=settings,
                worker=worker,
                conversation_id=conversation_id,
                user_text=instruccion,
                persist_user_message=False,
            ):
                pass  # los chunks SSE se descartan: el mensaje del bot ya se persiste adentro
    except Exception:  # noqa: BLE001 - best-effort: el PATCH ya respondió
        logger.warning("ack de identidad falló para worker=%s", worker_id, exc_info=True)
