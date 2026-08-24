"""Feedback explícito de calidad, sin almacenar texto crudo del usuario."""

from __future__ import annotations

import hashlib
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from edecan_api.deps import CurrentUser, get_current_user, get_repo, rate_limit
from edecan_api.repo import Repo

router = APIRouter(prefix="/v1/feedback", tags=["quality"], dependencies=[Depends(rate_limit)])


class FeedbackIn(BaseModel):
    kind: Literal["thumb_up", "thumb_down", "correction", "report_problem"]
    category: str = Field(default="general", min_length=1, max_length=80)
    detail: str | None = Field(default=None, max_length=1000)
    conversation_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None


def _feedback_ref(body: FeedbackIn, user_id: uuid.UUID) -> str:
    raw = "|".join(
        (
            str(user_id),
            body.kind,
            body.category.strip().lower(),
            body.detail or "",
            str(body.conversation_id or ""),
            str(body.message_id or ""),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@router.post("", status_code=202)
async def submit_feedback(
    body: FeedbackIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, str | bool]:
    """Acepta feedback para el loop de calidad; no ejecuta ninguna acción."""
    if body.message_id is not None and body.conversation_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="message_id requiere conversation_id.",
        )
    if body.conversation_id is not None:
        conversation = await repo.get_conversation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            conversation_id=body.conversation_id,
        )
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversación no encontrada."
            )
    ref = _feedback_ref(body, current_user.user_id)
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="quality.feedback_received",
        target=str(body.message_id or body.conversation_id or "session"),
        meta={
            "kind": body.kind,
            "category": body.category.strip().lower(),
            "feedback_ref": ref,
            "conversation_id": str(body.conversation_id) if body.conversation_id else None,
            "message_id": str(body.message_id) if body.message_id else None,
            "detail_ref": hashlib.sha256((body.detail or "").encode("utf-8")).hexdigest(),
        },
    )
    return {"accepted": True, "feedback_ref": ref}
