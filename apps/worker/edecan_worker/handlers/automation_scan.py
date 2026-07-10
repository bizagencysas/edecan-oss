"""Job `automation_scan`: barre `automations` con trigger de agenda vencidas
y encola `run_automation` por cada una, adelantando `next_run_at` para evitar
doble disparo (ROADMAP_V2.md §7.3, §7.4, §7.6; `ARCHITECTURE.md` §10.11;
dueño WP-V2-07).

Job de sistema sin tenant propio — igual que `send_reminder_scan.py`
(mismo criterio, ver su docstring): se dispara sin `tenant_id`, barre TODOS
los tenants de una vez (`ARCHITECTURE.md` §2). En dev,
`edecan_worker.scheduler` lo encola cada 60s; en prod lo encola
`aws_scheduler_schedule.automation_scan` (`infra/terraform/modules/scheduler/`,
`ARCHITECTURE.md` §7, `docs/automatizaciones.md`).

Solo las automatizaciones con `trigger.kind="schedule"` fijan `next_run_at`
(las de `kind="webhook"` lo dejan `NULL` siempre — se disparan vía
`POST /v1/hooks/{id}`, nunca por este barrido): filtrar
`next_run_at IS NOT NULL` ya las excluye sin necesitar tocar la columna
`trigger` en el `WHERE`.

`edecan_automations`/`edecan_core` se importan de forma perezosa dentro de
`handle()` — mismo criterio que `send_reminder_scan.py`/`edecan_worker.deps`
(ARCHITECTURE.md §10.1). Habla SQL parametrizado directo contra
`automations` (no un ORM de `edecan_db.models`, que todavía no la declara
mientras WP-V2-01 termina la migración `0003_v2_expansion`) — mismo criterio
que `run_automation.py`/`run_mission.py`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from edecan_schemas import JobEnvelope
from sqlalchemy import text

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)


async def handle(env: JobEnvelope, deps: Deps) -> None:
    # Import perezoso: edecan_core/edecan_automations son paquetes hermanos
    # que pueden aún no existir en este workspace mientras se construyen en
    # paralelo (ARCHITECTURE.md §10.1) — ver docstring del módulo.
    from edecan_automations.engine import compute_next_run
    from edecan_core.queue import enqueue

    now = datetime.now(UTC)
    async with deps.session_factory(None) as session:
        due = await _list_due_schedule_automations(session, now)

    logger.info("automation_scan: %d automatización(es) vencida(s)", len(due))
    for automation in due:
        # UUID(str(...)) defensivo: funciona tanto si el driver ya decodificó
        # la columna uuid a un `UUID` de Python (asyncpg nativo) como si la
        # entregó como texto — mismo criterio que `run_automation.py` con
        # `user_id`.
        automation_id = UUID(str(automation["id"]))
        tenant_id = UUID(str(automation["tenant_id"]))
        trigger = _parse_jsonb(automation.get("trigger"))
        rrule = trigger.get("rrule") if trigger.get("kind") == "schedule" else None

        next_run_at: datetime | None = None
        if rrule is None:
            logger.warning(
                "automation_scan: automatización %s tiene next_run_at pero trigger no es de "
                "agenda (%r); se detiene su próxima corrida automática.",
                automation_id,
                trigger,
            )
        else:
            try:
                # `anchor=automation["next_run_at"]`: la fase (minuto/segundo)
                # de la recurrencia debe fijarse UNA sola vez (al crear/editar
                # el trigger, ver `tools.py::_crear`/`routers/automations.py`)
                # y quedar estable en cada recomputo — si en su lugar se usa el
                # `now` volátil del sondeo como ancla, `dtstart` cambia en cada
                # ciclo y la fase deriva sin fin (ver docstring de
                # `compute_next_run`). El valor YA está en la fila que trajo
                # `_list_due_schedule_automations`, no hace falta otra query.
                next_run_at = compute_next_run(
                    rrule, after=now, anchor=automation.get("next_run_at")
                )
            except ValueError:
                logger.exception(
                    "automation_scan: rrule inválida (%r) para %s; se detiene su próxima "
                    "corrida automática.",
                    rrule,
                    automation_id,
                )

        async with deps.session_factory(None) as session:
            # Adelanta next_run_at ANTES de encolar `run_automation`: evita
            # doble disparo si este barrido (u otro) vuelve a correr antes de
            # que ese job termine, o si el worker muere justo después de
            # este punto — mismo espíritu que
            # `SqlRepo.mark_reminder_sent`/`send_reminder.py`, pero
            # proactivo (antes de correr) en vez de reactivo (después).
            await _advance_next_run(session, automation_id, next_run_at)

        job_id = await enqueue(
            deps.settings, "run_automation", {"automation_id": str(automation_id)}, tenant_id
        )
        logger.info(
            "run_automation encolado job_id=%s automation_id=%s tenant_id=%s",
            job_id,
            automation_id,
            tenant_id,
        )


def _parse_jsonb(value: Any) -> dict[str, Any]:
    """El driver puede devolver una columna `jsonb` como `str` crudo — mismo
    gotcha que `edecan_toolkit.contactos._desde_jsonb`/
    `edecan_worker.handlers.run_automation._parse_jsonb`."""
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value) if value else {}


async def _list_due_schedule_automations(session: Any, now: datetime) -> list[dict[str, Any]]:
    """Barrido GLOBAL (sin filtro de `tenant_id`) deliberado — es un job de
    sistema que por definición recorre TODOS los tenants (ver docstring del
    módulo), mismo criterio que `SqlRepo.list_due_reminders`."""
    result = await session.execute(
        text(
            "SELECT * FROM automations WHERE enabled = true AND next_run_at IS NOT NULL "
            "AND next_run_at <= :now ORDER BY next_run_at ASC"
        ),
        {"now": now},
    )
    return [dict(row) for row in result.mappings().all()]


async def _advance_next_run(
    session: Any, automation_id: UUID, next_run_at: datetime | None
) -> None:
    await session.execute(
        text(
            "UPDATE automations SET next_run_at = :next_run_at, updated_at = now() WHERE id = :id"
        ),
        {"next_run_at": next_run_at, "id": str(automation_id)},
    )
