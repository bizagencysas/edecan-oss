"""Estado seguro de la continuidad híbrida de esta instalación."""

from __future__ import annotations

import uuid
from typing import Any

from edecan_core.safety import redact
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from edecan_api.deps import CurrentUser, get_current_user, get_repo, rate_limit
from edecan_api.repo import Repo

router = APIRouter(
    prefix="/v1/continuity",
    tags=["continuity"],
    dependencies=[Depends(rate_limit)],
)


class ContinuitySyncIn(BaseModel):
    event_id: uuid.UUID
    conversation_id: uuid.UUID
    user_message: str = Field(min_length=1, max_length=20_000)
    assistant_message: str = Field(min_length=1, max_length=50_000)


@router.get("/status")
async def continuity_status(
    request: Request,
    _current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Informa disponibilidad sin revelar infraestructura o credenciales."""

    state = getattr(request.app.state, "edge_continuity_state", None)
    snapshot = getattr(state, "snapshot", None)
    if callable(snapshot):
        payload = snapshot()
        if isinstance(payload, dict):
            return payload
    return {
        "configured": False,
        "enabled": False,
        "connected": False,
        "last_heartbeat_at": None,
        "last_claim_at": None,
        "last_job_at": None,
        "last_error": None,
        "completed_jobs": 0,
        "failed_jobs": 0,
    }


@router.post("/sync")
async def sync_continuity_turn(
    body: ContinuitySyncIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Reconcilia un turno Bedrock creado mientras la computadora estaba fuera."""

    conversation = await repo.get_conversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=body.conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")

    event_id = str(body.event_id)
    history = await repo.list_messages(
        tenant_id=current_user.tenant_id,
        conversation_id=body.conversation_id,
    )
    if any(
        isinstance(row.get("content"), dict)
        and row["content"].get("continuity_event_id") == event_id
        for row in history
    ):
        return {
            "ok": True,
            "deduplicated": True,
            "conversation_id": str(body.conversation_id),
        }

    marker = {"continuity_event_id": event_id, "source": "aws_continuity"}
    await repo.add_message(
        tenant_id=current_user.tenant_id,
        conversation_id=body.conversation_id,
        role="user",
        content={"text": redact(body.user_message), **marker},
    )
    await repo.add_message(
        tenant_id=current_user.tenant_id,
        conversation_id=body.conversation_id,
        role="assistant",
        content={"text": redact(body.assistant_message), **marker},
    )
    return {
        "ok": True,
        "deduplicated": False,
        "conversation_id": str(body.conversation_id),
    }
