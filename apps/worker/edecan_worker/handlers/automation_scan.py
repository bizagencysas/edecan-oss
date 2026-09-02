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
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from edecan_schemas import JobEnvelope
from sqlalchemy import text

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)

# Arriendo durable de una automatización reclamada por el barrido (directiva
# §67-68, migración `0050_automation_lease`): si el worker muere después de
# reclamar y antes de encolar/correr, el arriendo vence solo y el siguiente
# barrido puede reclamar de nuevo. `run_automation` lo limpia al persistir el
# estado terminal.
_LEASE_SECONDS = 900


async def handle(env: JobEnvelope, deps: Deps) -> None:
    # Import perezoso: edecan_core/edecan_automations son paquetes hermanos
    # que pueden aún no existir en este workspace mientras se construyen en
    # paralelo (ARCHITECTURE.md §10.1) — ver docstring del módulo.
    from edecan_automations.engine import compute_next_run
    try:
        from edecan_automations.engine import evaluate_condition
    except ImportError:
        # Versiones antiguas del paquete y dobles de tests pueden no traer aún
        # condiciones. Una condición ausente conserva compatibilidad; una
        # condición presente se bloquea, nunca se ejecuta a ciegas.
        def evaluate_condition(condition: Any, context: dict[str, Any]) -> bool:
            del context
            if condition in (None, [], "", {}):
                return True
            logger.error(
                "automation_scan: motor sin evaluate_condition; se bloquea una "
                "automatización condicionada"
            )
            return False
    from edecan_core.queue import enqueue

    now = datetime.now(UTC)
    scan_id = uuid4()  # dueño del arriendo: un id por invocación del barrido
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
                #
                # `timezone=automation.get("timezone")`: el huso en el que se
                # LEE la rrule (columna de la fila, migración
                # `0029_social_drafts_tz`). Sin esto, `BYHOUR=9` significaba
                # las 9 UTC para todo el mundo -- las 4:00 a.m. en Bogotá. Va
                # con `.get()` y no `["timezone"]` a propósito: este handler
                # habla SQL crudo contra la tabla (ver docstring del módulo) y
                # debe seguir corriendo contra una base que todavía no aplicó
                # la migración; ahí la clave no existe, llega `None` y el motor
                # cae a UTC = el comportamiento de siempre. Una zona inválida
                # tampoco revienta el barrido (ver `engine._zona_horaria`): un
                # typo de un tenant no puede dejar sin reprogramar a los demás.
                next_run_at = compute_next_run(
                    rrule,
                    after=now,
                    anchor=automation.get("next_run_at"),
                    timezone=automation.get("timezone"),
                )
            except ValueError:
                logger.exception(
                    "automation_scan: rrule inválida (%r) para %s; se detiene su próxima "
                    "corrida automática.",
                    rrule,
                    automation_id,
                )

        async with deps.session_factory(None) as session:
            # Claim + adelanto de next_run_at ATÓMICOS en un solo UPDATE: evita
            # doble disparo si este barrido (u otro) vuelve a correr antes de
            # que ese job termine — el `WHERE (lease_until IS NULL OR
            # lease_until < now())` hace que solo UN barrido gane la fila
            # (mismo espíritu que el claim de `run_persistent_agent.py`). Si el
            # UPDATE no tocó fila, otro barrido ya la reclamó y se salta.
            claimed = await _claim_and_advance(session, automation_id, next_run_at, scan_id, now)
        if not claimed:
            logger.info(
                "automation_scan: automatización %s ya reclamada por otro barrido; se salta.",
                automation_id,
            )
            continue

        # Condición opcional (PHASE2 §60): si la automatización trae una
        # `condition` que NO se cumple contra el contexto de runtime, se salta
        # la ejecución SIN encolar `run_automation`. `next_run_at` YA quedó
        # adelantado arriba (a propósito): una condición falsa persistente no
        # debe reintentar el mismo slot para siempre; el salto se registra en
        # el log, que es la evidencia observable disponible (no existe un
        # estado "skipped" en `automation_runs`).
        if not evaluate_condition(automation.get("condition"), _condition_context(automation, now)):
            logger.info(
                "automation_scan: condición falsa para %s; se salta esta corrida.",
                automation_id,
            )
            continue

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
    módulo), mismo criterio que `SqlRepo.list_due_reminders`.

    Excluye las automatizaciones con arriendo activo (`lease_until >= now`):
    una reclamada por otro barrido/concurrente no debe volver a encolarse
    mientras su arriendo esté vivo — primera defensa antes del claim atómico
    de `_claim_and_advance`."""
    result = await session.execute(
        text(
            "SELECT * FROM automations WHERE enabled = true AND next_run_at IS NOT NULL "
            "AND next_run_at <= :now "
            "AND (lease_until IS NULL OR lease_until < :now) "
            "ORDER BY next_run_at ASC"
        ),
        {"now": now},
    )
    return [dict(row) for row in result.mappings().all()]


async def _claim_and_advance(
    session: Any,
    automation_id: UUID,
    next_run_at: datetime | None,
    lease_owner: UUID,
    now: datetime,
) -> bool:
    """Claim atómico del arriendo + adelanto de `next_run_at` en UN solo
    `UPDATE` (directiva §67-68, migración `0050_automation_lease`).

    El `WHERE (lease_until IS NULL OR lease_until < now())` hace que solo un
    barrido gane la fila aunque dos la hayan leído vencida a la vez; devuelve
    `True` solo si el UPDATE tocó la fila (reclamó). `rowcount` se lee con
    `getattr(..., 1)` a propósito: los dobles de sesión de tests no exponen
    `rowcount`, y en ese caso se asume éxito (mismo criterio que
    `run_persistent_agent.py` con `getattr(claim, "rowcount", 1)`)."""
    result = await session.execute(
        text(
            "UPDATE automations SET next_run_at = :next_run_at, "
            "lease_owner = :lease_owner, lease_until = :lease_until, updated_at = now() "
            "WHERE id = :id AND (lease_until IS NULL OR lease_until < now())"
        ),
        {
            "next_run_at": next_run_at,
            "lease_owner": str(lease_owner),
            "lease_until": now + timedelta(seconds=_LEASE_SECONDS),
            "id": str(automation_id),
        },
    )
    return getattr(result, "rowcount", 1) > 0


def _condition_context(automation: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Contexto mínimo para `evaluate_condition` (PHASE2 §60).

    Solo expone lo que el `SELECT *` de este barrido ya trae más el reloj del
    sondeo: `last_run`, `failure_count`, `next_run`, `hour`, `minute` y
    `weekday` (ver el docstring de `evaluate_condition`). `last_result` exige
    cargar `automation_runs` (no lo hace este job) y queda a cargo de
    `compute_automation_state` en callers más ricos. Se lee con `.get()` y no
    con `[...]` a propósito: este handler sigue corriendo contra una base que
    todavía no aplicó las migraciones `0036`/`0039`; ahí las claves no existen,
    llegan `None`/`0` y la condición se evalúa como siempre (sin bloquear).
    """
    return {
        "last_run": automation.get("last_run_at"),
        "failure_count": int(automation.get("consecutive_failures") or 0),
        "next_run": automation.get("next_run_at"),
        "hour": now.hour,
        "minute": now.minute,
        "weekday": now.weekday(),
    }
