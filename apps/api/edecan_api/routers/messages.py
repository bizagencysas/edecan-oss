"""`/v1/messages` — reacciones y threads de mensajes (product design)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

router = APIRouter(prefix="/v1/messages", tags=["messages"], dependencies=[Depends(rate_limit)])

_EMOJIS = {"👍", "👎", "✅", "👀", "❤️", "🔥"}


def _as_public(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row(row: Mapping[str, Any]) -> dict[str, Any]:
    return jsonable_encoder({k: _as_public(v) for k, v in dict(row).items()})


class ReactionIn(BaseModel):
    emoji: str = Field(min_length=1, max_length=8)


class ThreadIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


async def _mensaje_pertenece(
    session: AsyncSession, user: CurrentUser, message_id: uuid.UUID
) -> None:
    result = await session.execute(
        text("SELECT id FROM messages WHERE tenant_id = :tenant_id AND id = :id"),
        {"tenant_id": str(user.tenant_id), "id": str(message_id)},
    )
    if result.mappings().first() is None:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")


@router.get("/{message_id}/reactions")
async def list_reactions(
    message_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    await _mensaje_pertenece(session, user, message_id)
    result = await session.execute(
        text(
            "SELECT emoji, user_id FROM reactions "
            "WHERE tenant_id = :tenant_id AND message_id = :message_id ORDER BY created_at ASC"
        ),
        {"tenant_id": str(user.tenant_id), "message_id": str(message_id)},
    )
    return [_row(r) for r in result.mappings().all()]


@router.post("/{message_id}/reactions", status_code=status.HTTP_201_CREATED)
async def add_reaction(
    message_id: uuid.UUID,
    body: ReactionIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    if body.emoji not in _EMOJIS:
        raise HTTPException(status_code=422, detail="Reacción no permitida.")
    await _mensaje_pertenece(session, user, message_id)
    await session.execute(
        text(
            "INSERT INTO reactions (id, tenant_id, user_id, message_id, emoji) "
            "VALUES (gen_random_uuid(), :tenant_id, :user_id, :message_id, :emoji) "
            "ON CONFLICT (user_id, message_id, emoji) DO NOTHING"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "message_id": str(message_id),
            "emoji": body.emoji,
        },
    )
    return {"message_id": str(message_id), "emoji": body.emoji}


@router.delete("/{message_id}/reactions/{emoji}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_reaction(
    message_id: uuid.UUID,
    emoji: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    await session.execute(
        text(
            "DELETE FROM reactions WHERE tenant_id = :tenant_id AND message_id = :message_id "
            "AND user_id = :user_id AND emoji = :emoji"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "message_id": str(message_id),
            "user_id": str(user.user_id),
            "emoji": emoji,
        },
    )


@router.get("/{message_id}/thread")
async def list_thread(
    message_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    await _mensaje_pertenece(session, user, message_id)
    result = await session.execute(
        text(
            "SELECT id, conversation_id, role, content, tool_calls, thread_id, created_at "
            "FROM messages WHERE tenant_id = :tenant_id AND thread_id = :message_id "
            "ORDER BY created_at ASC"
        ),
        {"tenant_id": str(user.tenant_id), "message_id": str(message_id)},
    )
    return [_row(r) for r in result.mappings().all()]


@router.post("/{message_id}/thread", status_code=status.HTTP_201_CREATED)
async def post_thread(
    message_id: uuid.UUID,
    body: ThreadIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    await _mensaje_pertenece(session, user, message_id)
    conv = await session.execute(
        text("SELECT conversation_id FROM messages WHERE tenant_id = :tenant_id AND id = :id"),
        {"tenant_id": str(user.tenant_id), "id": str(message_id)},
    )
    row = conv.mappings().first()
    result = await session.execute(
        text(
            "INSERT INTO messages (id, tenant_id, conversation_id, role, content, thread_id) "
            "VALUES (gen_random_uuid(), :tenant_id, :conversation_id, 'user', :content ::jsonb, "
            ":thread_id) RETURNING id, tenant_id, conversation_id, role, content, thread_id, "
            "created_at"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "conversation_id": str(row["conversation_id"]),
            "content": json.dumps({"text": body.text.strip()}),
            "thread_id": str(message_id),
        },
    )
    return _row(result.mappings().first())