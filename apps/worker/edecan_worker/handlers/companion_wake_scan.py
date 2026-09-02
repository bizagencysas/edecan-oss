"""Job `companion_wake_scan`: enqueue real companion turns for event signals.

Global scan job (no tenant_id). Enqueues `run_companion_turn` for pending
approvals and hourly companion pulses during waking hours; never generates chat
content.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from edecan_core.companion_wake import DEFAULT_WAKE_INSTRUCTION, is_pulse_window, pulse_wake_key
from edecan_schemas import JobEnvelope
from sqlalchemy import text

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is not None:
        raise ValueError("companion_wake_scan es un job global")

    from edecan_core.queue import enqueue

    now = datetime.now(UTC)
    encolados = 0
    async with deps.session_factory(None) as session:
        pending = await _list_pending_approvals(session)
        for row in pending:
            approval_id = UUID(str(row["id"]))
            tenant_id = UUID(str(row["tenant_id"]))
            user_id = UUID(str(row["user_id"]))
            wake_key = f"approval:{approval_id}"
            payload = {
                "user_id": str(user_id),
                "wake_key": wake_key,
                "source": "pending_approval",
                "urgent": True,
                "conversation_id": str(row["conversation_id"]),
                "instruction": (
                    "[Edecán — turno proactivo interno]\n"
                    "Hay una aprobación pendiente que requiere atención del dueño. "
                    "Revisa el contexto y, solo si hace falta un recordatorio concreto, "
                    "escríbele en el chat principal. Si ya está claro o no aporta, responde "
                    f"exactamente: [NO_MESSAGE]"
                ),
            }
            await enqueue(deps.settings, "run_companion_turn", payload, tenant_id)
            encolados += 1

        if is_pulse_window(now):
            wake_key = pulse_wake_key(now)
            owners = await _list_companion_owners(session)
            for row in owners:
                tenant_id = UUID(str(row["tenant_id"]))
                user_id = UUID(str(row["user_id"]))
                payload = {
                    "user_id": str(user_id),
                    "wake_key": wake_key,
                    "source": "companion_pulse",
                    "instruction": DEFAULT_WAKE_INSTRUCTION,
                }
                await enqueue(deps.settings, "run_companion_turn", payload, tenant_id)
                encolados += 1

    logger.info("companion_wake_scan: %d wake(s) encolado(s)", encolados)


async def _list_pending_approvals(session: Any) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT id, tenant_id, user_id, conversation_id
            FROM pending_approvals
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT 200
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


async def _list_companion_owners(session: Any) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT DISTINCT m.tenant_id, m.user_id
            FROM memberships m
            JOIN tenants t ON t.id = m.tenant_id
            WHERE m.role = 'owner' AND t.status = 'active'
            ORDER BY m.tenant_id, m.user_id
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


__all__ = ["handle"]
