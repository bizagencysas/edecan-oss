"""Job `send_reminder_scan`: barre `reminders` vencidos y encola `send_reminder`
por cada uno, con el `tenant_id` correspondiente (ARCHITECTURE.md §10.11).

Job de sistema: se dispara sin `tenant_id` propio — en dev, cada
`INTERVALO_SEGUNDOS` vía `edecan_worker.scheduler`; en prod, cada minuto vía
EventBridge Scheduler (ARCHITECTURE.md §7). Por eso es la única excepción
documentada a "SIEMPRE filtrar por tenant_id del job" (ARCHITECTURE.md §2): no
hay un tenant propio del job, es un barrido de TODOS los tenants — ver
`edecan_worker.repo.SqlRepo.list_due_reminders`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from edecan_schemas import JobEnvelope

from edecan_worker.deps import Deps
from edecan_worker.repo import SqlRepo

logger = logging.getLogger(__name__)


async def handle(env: JobEnvelope, deps: Deps) -> None:
    # Import perezoso: edecan_core es un paquete hermano que puede aún no
    # existir en este workspace mientras se construye en paralelo
    # (ARCHITECTURE.md §10.1) — se importa aquí, no al tope del módulo, para
    # que este handler siga siendo importable (y testeable con fakes) sin él.
    from edecan_core.queue import enqueue

    now = datetime.now(UTC)
    async with deps.session_factory(None) as session:
        repo = SqlRepo(session)
        due = await repo.list_due_reminders(now=now)

    logger.info("send_reminder_scan: %d recordatorio(s) vencido(s)", len(due))
    for reminder in due:
        job_id = await enqueue(
            deps.settings,
            "send_reminder",
            {"reminder_id": str(reminder["id"])},
            reminder["tenant_id"],
        )
        logger.info(
            "send_reminder encolado job_id=%s reminder_id=%s tenant_id=%s",
            job_id,
            reminder["id"],
            reminder["tenant_id"],
        )
