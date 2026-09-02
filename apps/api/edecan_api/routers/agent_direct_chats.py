"""Chats 1:1 entre dos bots persistentes — turnos reales, sin stand-ins."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from edecan_core.queue import enqueue
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.bot_turn_service import (
    list_normalized_messages,
    load_worker,
    stream_worker_turn,
    worker_display_name,
)
from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_redis, get_tenant_session, rate_limit
from edecan_api.routers.conversations import _format_sse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/agents/direct-chats",
    tags=["agent-direct-chats"],
    dependencies=[Depends(rate_limit)],
)

# El turno vive en una tarea DESPRENDIDA de la petición SSE: si el dueño
# sale de la app, bloquea o cambia de red, el socket muere pero el turno
# SIGUE en la Mac hasta terminar y notificar. Regla del dueño (2-sep-2026):
# «trabajen al 100% aunque salga de la app, y mándenme un push siempre».
_TURNOS_VIVOS: set[asyncio.Task] = set()
_TURN_LOCKS: dict[str, asyncio.Lock] = {}
_ESTADO_TTL_S = 2 * 60 * 60
_VIGILANTE_TOPE_S = 15 * 60


def _turn_lock_for(conversation_id: uuid.UUID) -> asyncio.Lock:
    key = str(conversation_id)
    lock = _TURN_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _TURN_LOCKS[key] = lock
    return lock


class DirectChatCreateIn(BaseModel):
    agent_a_id: uuid.UUID
    agent_b_id: uuid.UUID


class DirectChatMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    """Quién habla: `user` (dueño) o el id del bot emisor."""
    speaker: str = Field(default="user", max_length=120)


def _current(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.tenant.flags.get("agents.missions", False):
        raise HTTPException(status_code=403, detail="Los bots no están disponibles en tu plan.")
    return user


def _canonical_pair(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    if a == b:
        raise HTTPException(status_code=422, detail="Un bot no puede chatear consigo mismo.")
    return (a, b) if str(a) < str(b) else (b, a)


async def _ensure_direct_conversation(
    session: AsyncSession,
    user: CurrentUser,
    chat_row: dict[str, Any],
    agent_a: dict[str, Any],
    agent_b: dict[str, Any],
) -> uuid.UUID:
    conversation_id = chat_row.get("conversation_id")
    if conversation_id is not None:
        return uuid.UUID(str(conversation_id))
    title = f"{worker_display_name(agent_a)} ↔ {worker_display_name(agent_b)}"
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
            "UPDATE agent_direct_chats SET conversation_id = :cid, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"cid": str(new_id), "tenant_id": str(user.tenant_id), "id": str(chat_row["id"])},
    )
    chat_row["conversation_id"] = new_id
    return uuid.UUID(str(new_id))


async def _load_direct_chat(
    session: AsyncSession, user: CurrentUser, chat_id: uuid.UUID
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "SELECT id, tenant_id, user_id, agent_a_id, agent_b_id, conversation_id, "
            "created_at, updated_at FROM agent_direct_chats "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "id": str(chat_id),
        },
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Chat directo no encontrado.")
    return dict(row)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_direct_chat(
    body: DirectChatCreateIn,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    agent_a_id, agent_b_id = _canonical_pair(body.agent_a_id, body.agent_b_id)
    await load_worker(session, user, agent_a_id)
    await load_worker(session, user, agent_b_id)
    result = await session.execute(
        text(
            "INSERT INTO agent_direct_chats "
            "(id, tenant_id, user_id, agent_a_id, agent_b_id) "
            "VALUES (gen_random_uuid(), :tenant_id, :user_id, :agent_a_id, :agent_b_id) "
            "ON CONFLICT (tenant_id, user_id, agent_a_id, agent_b_id) DO UPDATE "
            "SET updated_at = now() "
            "RETURNING id, tenant_id, user_id, agent_a_id, agent_b_id, conversation_id, "
            "created_at, updated_at"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "agent_a_id": str(agent_a_id),
            "agent_b_id": str(agent_b_id),
        },
    )
    row = result.mappings().first()
    assert row is not None
    return jsonable_encoder(dict(row))


@router.get("")
async def list_direct_chats(
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT d.id, d.agent_a_id, d.agent_b_id, d.conversation_id, "
            "d.created_at, d.updated_at, "
            "a.name AS agent_a_name, a.display_name AS agent_a_display, "
            "b.name AS agent_b_name, b.display_name AS agent_b_display "
            "FROM agent_direct_chats d "
            "JOIN persistent_agents a ON a.id = d.agent_a_id "
            "JOIN persistent_agents b ON b.id = d.agent_b_id "
            "WHERE d.tenant_id = :tenant_id AND d.user_id = :user_id "
            "ORDER BY d.updated_at DESC"
        ),
        {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id)},
    )
    return [jsonable_encoder(dict(r)) for r in result.mappings().all()]


@router.get("/{chat_id}/messages")
async def list_direct_messages(
    chat_id: uuid.UUID,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    chat = await _load_direct_chat(session, user, chat_id)
    if chat.get("conversation_id") is None:
        return []
    return await list_normalized_messages(
        session,
        tenant_id=user.tenant_id,
        conversation_id=uuid.UUID(str(chat["conversation_id"])),
    )


@router.post("/{chat_id}/message")
async def send_direct_message(
    chat_id: uuid.UUID,
    body: DirectChatMessageIn,
    request: Request,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
):
    chat = await _load_direct_chat(session, user, chat_id)
    agent_a = await load_worker(session, user, uuid.UUID(str(chat["agent_a_id"])))
    agent_b = await load_worker(session, user, uuid.UUID(str(chat["agent_b_id"])))
    conversation_id = await _ensure_direct_conversation(session, user, chat, agent_a, agent_b)

    speaker = body.speaker.strip()
    agent_a_id = str(chat["agent_a_id"])
    agent_b_id = str(chat["agent_b_id"])

    if speaker in ("user", "owner", "human"):
        author_role, author_id, author_name = "user", "user", "Tú"
        responder = agent_a
        prompt = body.text
    elif speaker == agent_a_id:
        author_role, author_id, author_name = (
            "assistant",
            agent_a_id,
            worker_display_name(agent_a),
        )
        responder = agent_b
        prompt = body.text
    elif speaker == agent_b_id:
        author_role, author_id, author_name = (
            "assistant",
            agent_b_id,
            worker_display_name(agent_b),
        )
        responder = agent_a
        prompt = body.text
    else:
        raise HTTPException(status_code=422, detail="speaker debe ser user o un agent_id del chat.")

    async def _stream():
        if speaker in ("user", "owner", "human"):
            async for chunk in stream_worker_turn(
                request=request,
                session=session,
                user=user,
                settings=settings,
                worker=responder,
                conversation_id=conversation_id,
                user_text=prompt,
            ):
                yield chunk
        else:
            async for chunk in stream_worker_turn(
                request=request,
                session=session,
                user=user,
                settings=settings,
                worker=responder,
                conversation_id=conversation_id,
                user_text=prompt,
                speaker_role=author_role,
                speaker_id=author_id,
                speaker_name=author_name,
                run_turn=True,
            ):
                yield chunk

    # El turno corre DESPRENDIDO del socket: si el teléfono sale de la app,
    # se bloquea o pierde red, el trabajo SIGUE en la Mac y el push del
    # resultado llega siempre. La respuesta SSE es un vigilante: emite los
    # deltas desde el estado en Redis y cierra con done/error.
    turn_id = uuid.uuid4()
    estado_key = f"bot_turn:{chat_id}:{turn_id}"

    task = asyncio.create_task(
        _run_turno_desprendido(
            request=request,
            user=user,
            settings=settings,
            responder=responder,
            conversation_id=conversation_id,
            prompt=prompt,
            speaker_role="user" if speaker in ("user", "owner", "human") else author_role,
            speaker_id="user" if speaker in ("user", "owner", "human") else author_id,
            speaker_name=author_name if speaker not in ("user", "owner", "human") else "Tú",
            estado_key=estado_key,
        ),
        name=f"bot-turn:{turn_id}",
    )
    _TURNOS_VIVOS.add(task)
    task.add_done_callback(_TURNOS_VIVOS.discard)

    async def _vigilante():
        yield _format_sse("message.started", {"turn_id": str(turn_id)})
        redis = get_redis(settings)
        leido = 0
        inicio = time.monotonic()
        while True:
            if time.monotonic() - inicio > _VIGILANTE_TOPE_S:
                logger.warning(
                    "vigilante de chat de bots agotó el tope (%.0fs) chat=%s",
                    _VIGILANTE_TOPE_S, chat_id,
                )
                yield _format_sse("message.done", {"type": "done", "usage": {}})
                return
            try:
                raw = await redis.get(estado_key)
            except Exception:
                raw = None
            estado: dict[str, Any] = {}
            if raw:
                try:
                    estado = json.loads(raw)
                except (TypeError, ValueError):
                    estado = {}
            texto = str(estado.get("text") or "")
            if len(texto) > leido:
                yield _format_sse(
                    "message.text_delta", {"type": "text_delta", "text": texto[leido:]}
                )
                leido = len(texto)
            estado_actual = str(estado.get("status") or "")
            if estado_actual == "done":
                yield _format_sse("message.done", {"type": "done", "usage": {}})
                return
            if estado_actual == "error":
                yield _format_sse(
                    "message.error", {"type": "error", "message": "El turno falló en la Mac."}
                )
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(_vigilante(), media_type="text/event-stream")


async def _run_turno_desprendido(
    *,
    request: Request,
    user: CurrentUser,
    settings: Settings,
    responder: dict[str, Any],
    conversation_id: uuid.UUID,
    prompt: str,
    speaker_role: str,
    speaker_id: str,
    speaker_name: str,
    estado_key: str,
) -> None:
    """El turno completo, desprendido de la petición: sesión propia, progreso
    en Redis y PUSH al dueño al terminar (siempre — aunque el teléfono esté
    cerrado). El candado por conversación serializa envíos encadenados."""
    redis = get_redis(settings)
    lock = _turn_lock_for(conversation_id)

    async def _estado(status: str, texto: str) -> None:
        try:
            await redis.set(
                estado_key,
                json.dumps({"status": status, "text": texto}, ensure_ascii=False),
                ex=_ESTADO_TTL_S,
            )
        except Exception:
            logger.warning("No pude escribir el estado del turno %s", estado_key, exc_info=True)

    texto_acumulado = ""
    async with lock:
        try:
            await _estado("running", "")
            # Sesión PROPIA del turno desprendido: la de la petición muere con
            # el socket, y este trabajo tiene que sobrevivir a eso.
            from edecan_db.session import get_session

            async with get_session(user.tenant_id) as session:
                ultimo = time.monotonic()
                async for chunk in stream_worker_turn(
                    request=request,
                    session=session,
                    user=user,
                    settings=settings,
                    worker=responder,
                    conversation_id=conversation_id,
                    user_text=prompt,
                    speaker_role=speaker_role,
                    speaker_id=speaker_id,
                    speaker_name=speaker_name,
                ):
                    if '"type": "text_delta"' in chunk or '"type":"text_delta"' in chunk:
                        try:
                            datos = chunk.split("data: ", 1)[1].split("\n", 1)[0]
                            evento = json.loads(datos)
                            texto_acumulado += str(evento.get("text") or "")
                        except (IndexError, ValueError, TypeError):
                            pass
                    ahora = time.monotonic()
                    if ahora - ultimo > 0.5:
                        await _estado("running", texto_acumulado)
                        ultimo = ahora
            await _estado("done", texto_acumulado)
        except Exception:
            logger.exception(
                "turno desprendido falló chat=%s estado=%s", conversation_id, estado_key
            )
            await _estado("error", texto_acumulado)
            return

    # PUSH SIEMPRE al terminar: la entrega cuando el teléfono está fuera.
    try:
        await enqueue(
            settings,
            "notify_important_event",
            {
                "user_id": str(user.user_id),
                "kind": "agent_message",
                "event_id": str(uuid.uuid4()),
                "chat_id": str(conversation_id),
            },
            user.tenant_id,
        )
    except Exception:
        logger.warning(
            "No pude encolar el push de fin de turno de bot "
            "(chat=%s estado=%s)", conversation_id, estado_key, exc_info=True,
        )
