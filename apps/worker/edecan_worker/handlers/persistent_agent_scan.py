"""Barrido justo de schedules de workers persistentes.

Solo encola tareas que el usuario configuró explícitamente en ``schedule``.
El job de ejecución vuelve a validar enabled/status/tools/presupuesto.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from edecan_schemas import JobEnvelope
from sqlalchemy import text

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)
MAX_CANDIDATES = 1000
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 86_400
DEFAULT_LEASE_SECONDS = 120.0

# La expresión usa solo números JSON; así un presupuesto malformado cae al
# default sin convertir el scan entero en un error SQL. Debe coincidir con los
# límites de `run_persistent_agent._lease_seconds`.
_LEASE_SECONDS_SQL = """
CASE
  WHEN jsonb_typeof(COALESCE(budget, '{}'::jsonb)->'lease_seconds') = 'number'
  THEN GREATEST(30.0, LEAST((budget->>'lease_seconds')::double precision, 3600.0))
  ELSE 120.0
END
"""


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is not None:
        raise ValueError("persistent_agent_scan es un job global")
    from edecan_core.queue import enqueue_outbox

    now = datetime.now(UTC)
    async with deps.session_factory(None) as session:
        result = await session.execute(
            text(
                "SELECT id, tenant_id, schedule FROM persistent_agents "
                "WHERE enabled = true AND ("
                "status = 'idle' OR (status = 'running' AND updated_at < "
                "now() - make_interval(secs => "
                + _LEASE_SECONDS_SQL
                + ")) ) "
                "AND jsonb_typeof(schedule) = 'object' "
                "AND NULLIF(btrim(schedule->>'instruction'), '') IS NOT NULL "
                "AND jsonb_typeof(schedule->'next_run_at') = 'string' "
                "AND (schedule->'every_seconds' IS NULL OR "
                "jsonb_typeof(schedule->'every_seconds') = 'number') "
                "ORDER BY updated_at ASC, id ASC LIMIT :limit"
            ),
            {"limit": MAX_CANDIDATES},
        )
        candidates = [dict(row) for row in result.mappings().all()]

    enqueued = 0
    for worker in candidates:
        try:
            schedule = _json(worker.get("schedule"))
            instruction = str(schedule.get("instruction") or "").strip()
            next_run_raw = schedule.get("next_run_at")
            if not instruction or not isinstance(next_run_raw, str):
                continue
            next_run = datetime.fromisoformat(next_run_raw.replace("Z", "+00:00"))
            # ISO-8601 without an offset is interpreted as UTC. Schedules are
            # persisted by this handler with an explicit offset from here on.
            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=UTC)
            else:
                next_run = next_run.astimezone(UTC)
            if next_run > now:
                continue
            every = schedule.get("every_seconds", 0)
            if isinstance(every, bool) or not isinstance(every, (int, float)):
                continue
            if not math.isfinite(float(every)):
                continue
            every_seconds = max(MIN_INTERVAL_SECONDS, min(int(every), MAX_INTERVAL_SECONDS))
            schedule["next_run_at"] = (now + timedelta(seconds=every_seconds)).isoformat()
            worker_id = UUID(str(worker["id"]))
            tenant_id = UUID(str(worker["tenant_id"]))
            async with deps.session_factory(None) as session:
                result = await session.execute(
                    text(
                        # No tocar `updated_at` aquí: para un `running` vencido es
                        # precisamente la prueba de que el runner murió. Solo el
                        # heartbeat o el runner que reclama pueden renovar el lease.
                        "UPDATE persistent_agents SET schedule = :schedule ::jsonb "
                        "WHERE tenant_id = :tenant_id AND id = :id "
                        "AND schedule->>'next_run_at' = :expected_next_run_at AND ("
                        "status = 'idle' OR (status = 'running' AND updated_at < "
                        "now() - make_interval(secs => "
                        + _LEASE_SECONDS_SQL
                        + ")) )"
                    ),
                    {
                        "schedule": json.dumps(schedule),
                        "expected_next_run_at": next_run_raw,
                        "tenant_id": str(tenant_id),
                        "id": str(worker_id),
                    },
                )
                if getattr(result, "rowcount", 0) <= 0:
                    logger.info(
                        "persistent_agent_scan: worker=%s ya reclamado; se salta.",
                        worker_id,
                    )
                    continue
                await enqueue_outbox(
                    session,
                    tenant_id=tenant_id,
                    job_type="run_persistent_agent",
                    payload={
                        "worker_id": str(worker_id),
                        "task_id": f"schedule:{worker_id}:{schedule['next_run_at']}",
                        "instruction": instruction,
                    },
                )
        except (TypeError, ValueError, OverflowError):
            logger.warning(
                "schedule inválido para worker=%s",
                worker.get("id"),
                exc_info=True,
            )
            continue
        except Exception:
            # The exception leaves the session context first, so its transaction
            # rolls back both the CAS and the outbox insert before this scan moves on.
            logger.exception(
                "persistent_agent_scan: fallo procesando worker=%s",
                worker.get("id"),
            )
            continue
        enqueued += 1
    logger.info("persistent_agent_scan: %d worker(s) encolado(s)", enqueued)
