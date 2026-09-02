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
from typing import Any

from edecan_core.bot_persona import persona_from_worker, worker_display_name
from edecan_core.companion_access import companion_para
from edecan_core.session_store import load_unified_session
from edecan_core.tools import ToolContext, ToolRegistry
from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.chat_context import ChatContextLimits, build_contextual_history
from edecan_api.config import Settings
from edecan_api.deps import CurrentUser

logger = logging.getLogger(__name__)

_WORKER_COLUMNS = (
    "id, tenant_id, user_id, name, purpose, workspace, display_name, avatar, "
    "role_title, role_short, job_description, personality, communication_style, "
    "instructions, constraints, approval_policy, autonomy_level, model_policy, "
    "tools, permissions, memory, schedule, budget, status, enabled, relation, conversation_id"
)


def build_worker_registry(
    full_registry: ToolRegistry, worker: Mapping[str, Any], *, local_mode: bool = False
) -> ToolRegistry:
    """Registro de tools del bot.

    En el Mac del dueño (companion presente) el bot recibe **TODO** el
    registro — es un amigo con acceso total: buscar, navegar, archivos,
    terminal, computadora, todo lo que Edecán sabe hacer. La idea del
    producto es «los bots hacen de verdad todo» (product design), no un
    workforce restringido que solo responde chat.

    Sin companion (deploy remoto sin Mac): se respeta la lista declarada
    por el bot y se excluyen las tools `dangerous` — sin humano presente no
    hay flujo de confirmación que las cubra.
    """
    tenant_id = worker.get("tenant_id")
    companion = companion_para(tenant_id) if tenant_id is not None else None
    registry = ToolRegistry()
    if companion is not None:
        for tool in full_registry.all():
            registry.register(tool)
        logger.info(
            "bot registry: %s COMPLETA con companion (%d tools)",
            str(worker.get("id") or "")[:8],
            len(registry.all()),
        )
        return registry
    # En un runtime local single-owner (`EDECAN_LOCAL_MODE`) la propia Mac es
    # el companion aunque el WebSocket/iOS Remoto esté desconectado — el
    # registro completo no debe depender del factory. Sin esto el turno del
    # bot caía a `tools=[]` y el bot decía "no tengo habilitado el canal".
    if local_mode:
        for tool in full_registry.all():
            registry.register(tool)
        logger.info(
            "bot registry: %s COMPLETA local-mode (%d tools)",
            str(worker.get("id") or "")[:8],
            len(registry.all()),
        )
        return registry
    # Sin companion (deploy remoto o Mac desconectada): se respeta la lista
    # declarada por el bot y se excluyen las dangerous — PERO las herramientas
    # de comunicación entre bots siempre son válidas: no necesitan la Mac.
    for tool_name in worker.get("tools") or []:
        tool = full_registry.get(str(tool_name))
        if tool is None:
            continue
        if tool.dangerous:
            continue
        registry.register(tool)
    for nombre_social in ("enviar_mensaje_bot", "listar_bots"):
        tool = full_registry.get(nombre_social)
        if tool is not None:
            registry.register(tool)
    return registry


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
    return {
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
            }
            if content.get("kind") == "evento"
            else {}
        ),
        **({"adjuntos": content.get("attachments")} if content.get("attachments") else {}),
    }


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
    conversation_id = worker.get("conversation_id")
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
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {
            "cid": str(new_id),
            "tenant_id": str(user.tenant_id),
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


async def list_normalized_messages(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT id, conversation_id, role, content, created_at "
            "FROM messages WHERE tenant_id = :tenant_id AND conversation_id = :conversation_id "
            "ORDER BY created_at ASC"
        ),
        {"tenant_id": str(tenant_id), "conversation_id": str(conversation_id)},
    )
    return [normalize_stored_message(row) for row in result.mappings().all()]


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
    if not clean:
        raise HTTPException(status_code=422, detail="El mensaje no puede estar vacío.")

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
            adjuntos_resueltos = await _resolve_message_attachments(
                repo=SqlRepo(session), tenant_id=user.tenant_id, file_ids=ids_adjuntos
            )
        except Exception:  # noqa: BLE001 - un adjunto inválido no tumba el turno
            adjuntos_resueltos = []

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
    history = build_contextual_history(
        current_rows=history_rows,
        cross_chat_rows=[],
        limits=ChatContextLimits(
            enabled=settings.BOT_CONTEXT_MAX_MESSAGES > 0,
            recent_messages=settings.BOT_CONTEXT_MAX_MESSAGES,
            max_messages=settings.BOT_CONTEXT_MAX_MESSAGES,
            max_chars=settings.BOT_CONTEXT_MAX_CHARS,
            cross_chat_enabled=False,
            cross_chat_conversations=0,
            cross_chat_messages_per_conversation=0,
            cross_chat_max_chars=0,
        ),
    )

    persona = persona_from_worker(worker)
    profile_context = await profile_context_for(session, user.tenant_id, user.user_id)
    full_registry = get_tool_registry(request)
    registry = build_worker_registry(
        full_registry,
        worker,
        local_mode=bool(getattr(settings, "EDECAN_LOCAL_MODE", False)),
    )
    agent = _agent_for_request(request, llm_router, registry)

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
    approved = {"usar_computadora", "delegar_al_ide"} if companion is not None else set()
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
    ctx.extras["lo_pidio_una_persona"] = speaker_id in ("user", "owner", "human")
    ctx.extras["tools_con_pregunta_pendiente"] = _tools_con_pregunta_pendiente(history_rows)
    unified_session.user_id = str(user.user_id)
    unified_session.touch(modality="text")
    ctx.extras["visual_memory"] = unified_session.visual_memory

    bot_name = worker_display_name(worker)
    bot_id = str(worker["id"])
    events = agent.run_turn(
        ctx=ctx,
        persona=persona,
        history=history,
        user_text=clean,
        flags=user.tenant.flags,
        extra_tools=[],
        seleccion=None,
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
