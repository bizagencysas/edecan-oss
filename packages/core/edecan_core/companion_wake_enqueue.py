"""Enqueue `run_companion_turn` with a stable wake_key (idempotent at delivery)."""

from __future__ import annotations

import logging
import uuid
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

RUN_COMPANION_TURN_JOB = "run_companion_turn"


async def enqueue_companion_wake(
    settings: Any,
    *,
    tenant_id: UUID,
    payload: dict[str, Any],
) -> bool:
    """Enqueue a proactive companion turn. Instruction carries facts, never chat copy.

    Returns True when the job was enqueued; False when enqueue failed (logged, not raised).
    """
    wake_key = str(payload.get("wake_key") or "").strip()
    user_id = str(payload.get("user_id") or "").strip()
    if not wake_key or not user_id:
        raise ValueError("enqueue_companion_wake requires wake_key and user_id")

    from edecan_core.queue import enqueue

    body = {
        "user_id": user_id,
        "wake_key": wake_key,
        "instruction": str(payload.get("instruction") or "").strip(),
        "source": payload.get("source"),
        "urgent": bool(payload.get("urgent")),
        "require_message": bool(payload.get("require_message")),
    }
    if payload.get("conversation_id"):
        body["conversation_id"] = str(payload["conversation_id"])
    if payload.get("message_presentation") is not None:
        body["message_presentation"] = payload["message_presentation"]
    if payload.get("message_tool_calls") is not None:
        body["message_tool_calls"] = payload["message_tool_calls"]
    if payload.get("push") is not None:
        body["push"] = payload["push"]
    if payload.get("notification") is not None:
        body["notification"] = payload["notification"]

    try:
        await enqueue(settings, RUN_COMPANION_TURN_JOB, body, tenant_id)
    except Exception:
        logger.warning(
            "companion_wake_enqueue_failed tenant_id=%s wake_key=%s source=%s",
            tenant_id,
            wake_key,
            payload.get("source"),
            exc_info=True,
        )
        return False
    return True


def notification_payload(
    *,
    kind: str,
    event_id: UUID | str,
) -> dict[str, str]:
    return {"kind": kind, "event_id": str(event_id)}


__all__ = [
    "RUN_COMPANION_TURN_JOB",
    "enqueue_companion_wake",
    "notification_payload",
]
