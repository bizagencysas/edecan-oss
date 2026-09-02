"""`/v1/approvals` — aprobaciones durables de acciones peligrosas del chat.

`conversations.py` persiste cada `ConfirmationRequiredEvent` en
`pending_approvals` (además del caché Redis) para que una aprobación
sobreviva un reload (directiva §30-32: "las aprobaciones no pueden expirar y
morir"). Este router es la superficie para listarlas y decidirlas después de
reiniciar API/desktop, reanudando el turno con el mismo camino
`Agent.resume_turn` que usa `POST /conversations/{id}/confirm`.

Redis sigue siendo el caché rápido (TTL 900 s); la base es la fuente durable
de verdad. Decidir acá marca la fila (`approved`/`denied` + `decided_at`/
`decided_by`) y, al aprobar, reanuda el turno desde `agent_snapshot`.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import redis.asyncio as redis_asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    CurrentUser,
    get_current_user,
    get_llm_router,
    get_redis,
    get_streaming_repo,
    get_streaming_vault,
    get_tenant_session,
    rate_limit,
)
from edecan_api.repo import Repo
from edecan_api.routers.conversations import _resume_approved_turn

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/approvals", tags=["approvals"], dependencies=[Depends(rate_limit)]
)

_COLUMNS = (
    "id, tenant_id, user_id, conversation_id, tool_call_id, agent_snapshot, "
    "status, created_at, updated_at, decided_at, decided_by"
)


def _as_str(value: Any) -> Any:
    return str(value) if isinstance(value, uuid.UUID) else value


def _as_iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = row.get("agent_snapshot")
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except Exception:  # noqa: BLE001 - snapshot corrupto: se degrada a {}
            snapshot = {}
    return snapshot if isinstance(snapshot, dict) else {}


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Vista pública sin `pending_turn` (contiene mensajes del usuario)."""
    snapshot = _snapshot(row)
    return jsonable_encoder(
        {
            "id": _as_str(row["id"]),
            "conversation_id": _as_str(row["conversation_id"]),
            "tool_call_id": row["tool_call_id"],
            "name": snapshot.get("name"),
            "args": snapshot.get("args") or {},
            "status": row["status"],
            "created_at": _as_iso(row["created_at"]),
            "decided_at": _as_iso(row["decided_at"]),
        }
    )


async def _load_pending(
    session: AsyncSession, user: CurrentUser, approval_id: uuid.UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            f"SELECT {_COLUMNS} FROM pending_approvals "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "id": str(approval_id),
        },
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


@router.get("")
async def list_approvals(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            f"SELECT {_COLUMNS} FROM pending_approvals "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND status = 'pending' "
            "ORDER BY created_at DESC"
        ),
        {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id)},
    )
    return [_public_row(row) for row in result.mappings().all()]


@router.post("/{approval_id}/approve")
async def approve_approval(
    approval_id: uuid.UUID,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_streaming_repo),
    session: AsyncSession = Depends(get_tenant_session, scope="request"),
    llm_router: Any = Depends(get_llm_router),
    vault: Any = Depends(get_streaming_vault),
    settings: Settings = Depends(get_settings),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> StreamingResponse:
    row = await _load_pending(session, user, approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Aprobación no encontrada.")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="Esta aprobación ya fue resuelta.")

    tool_call_id = str(row["tool_call_id"])
    snapshot = _snapshot(row)
    pending = {
        "name": snapshot.get("name"),
        "args": snapshot.get("args") or {},
        "pending_turn": snapshot.get("pending_turn"),
    }

    conversation = await repo.get_conversation(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        conversation_id=row["conversation_id"],
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")

    decided = await session.execute(
        text(
            "UPDATE pending_approvals SET status = 'approved', decided_at = now(), "
            "decided_by = :decided_by, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :id AND status = 'pending'"
        ),
        {
            "decided_by": str(user.user_id),
            "tenant_id": str(user.tenant_id),
            "id": str(approval_id),
        },
    )
    if getattr(decided, "rowcount", 1) == 0:
        raise HTTPException(status_code=409, detail="Esta aprobación ya fue resuelta.")

    try:
        return await _resume_approved_turn(
            request=request,
            current_user=user,
            tenant=user.tenant,
            conversation_id=row["conversation_id"],
            conversation=conversation,
            tool_call_id=tool_call_id,
            pending=pending,
            repo=repo,
            session=session,
            llm_router=llm_router,
            vault=vault,
            settings=settings,
            redis_client=redis_client,
        )
    except HTTPException:
        # Si la validación previa al streaming falló (tool caída, flag apagado,
        # snapshot dañado), se devuelve la fila a `pending` para poder reintentar
        # en vez de dejar una aprobación marcada que nunca se ejecutó.
        await session.execute(
            text(
                "UPDATE pending_approvals SET status = 'pending', decided_at = NULL, "
                "decided_by = NULL, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :id AND status = 'approved'"
            ),
            {"tenant_id": str(user.tenant_id), "id": str(approval_id)},
        )
        raise


@router.post("/{approval_id}/deny")
async def deny_approval(
    approval_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    row = await _load_pending(session, user, approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Aprobación no encontrada.")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="Esta aprobación ya fue resuelta.")

    denied = await session.execute(
        text(
            "UPDATE pending_approvals SET status = 'denied', decided_at = now(), "
            "decided_by = :decided_by, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :id AND status = 'pending'"
        ),
        {
            "decided_by": str(user.user_id),
            "tenant_id": str(user.tenant_id),
            "id": str(approval_id),
        },
    )
    if getattr(denied, "rowcount", 1) == 0:
        raise HTTPException(status_code=409, detail="Esta aprobación ya fue resuelta.")

    return {"approval_id": str(approval_id), "status": "denied"}