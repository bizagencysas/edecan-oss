"""Scheduler de DESARROLLO: encola los jobs de sistema periódicos
(`send_reminder_scan`, `sync_connector`, `automation_scan`) (ARCHITECTURE.md
§10.11; `automation_scan`: ROADMAP_V2.md §7.3/§7.6, dueño WP-V2-07).

Correr localmente: `python -m edecan_worker.scheduler` (junto con `make worker`
en otra terminal — ver `ARCHITECTURE.md` §8).

**En producción este módulo NO se despliega.** El reemplazo de prod
(EventBridge Scheduler, ARCHITECTURE.md §7) cubre `send_reminder_scan` y
`automation_scan` — dos `aws_scheduler_schedule` separados en
`infra/terraform/modules/scheduler/` (`.reminder_scan` / `.automation_scan`),
cada uno enviando su propio `{"type": ...}`. `sync_connector` sigue sin un
disparador periódico equivalente en producción: hoy solo se encola aquí, en
este loop de dev/self-host. Mientras ese gap siga abierto (falta un tercer
`aws_scheduler_schedule` en Terraform), los tokens OAuth
(Google/Microsoft/Meta/X/YouTube) no se refrescan proactivamente en un
despliegue real — las automatizaciones agendadas, en cambio, sí se disparan
solas en prod.

NOTA: `sync_connector` (refresca tokens OAuth por expirar, ver
`edecan_worker.handlers.sync_connector`) y `automation_scan` (barre
`automations` vencidas, ver `edecan_worker.handlers.automation_scan`) son,
igual que `send_reminder_scan`, jobs de sistema sin tenant propio — por eso
se encolan aquí también con `tenant_id=None` (barrido global).

## Dos cadencias, un solo loop

`_tick()` (jobs de v1, cada `INTERVALO_SEGUNDOS` = 30s por defecto) queda
INTACTO a propósito: `test_scheduler.py` (fuera de la lista de archivos que
este paquete de trabajo puede tocar) verifica su comportamiento exacto —
incluida la lista `[send_reminder_scan, sync_connector]` de una sola vez por
tick — con asserts de igualdad estricta. `automation_scan` (60s) se agrega
como un segundo chequeo DENTRO del mismo `while` de `run_forever`, NUNCA como
un `_tick()`/`asyncio.create_task` concurrente aparte: si `automation_scan`
se encolara en la MISMA pasada síncrona que el primer tick (antes de que
`run_forever` ceda el control en el primer `await asyncio.wait_for(...)`),
`test_run_forever_se_detiene_al_marcar_el_stop_event` -que cuenta
exactamente `len(JOBS_PERIODICOS)` llamadas a `enqueue` tras UN solo tick-
se rompería. Por eso `_ultimo_tick_automations` arranca en
`time.monotonic()` (no en `0`): el primer chequeo del loop siempre ve un
`elapsed` cercano a cero (`< INTERVALO_SEGUNDOS_AUTOMATIONS`) y se salta,
exactamente como "esperar antes del primer tick" — sin necesitar una segunda
tarea concurrente.
"""

from __future__ import annotations

import asyncio
import logging
import time

from edecan_worker.config import Settings, get_settings

logger = logging.getLogger(__name__)

INTERVALO_SEGUNDOS = 30
INTERVALO_SEGUNDOS_AUTOMATIONS = 60

# Jobs de sistema (sin tenant propio) que este loop encola periódicamente en
# dev/self-host. OJO: en prod, `send_reminder_scan` y `automation_scan` ya
# tienen equivalente vía EventBridge Scheduler (ARCHITECTURE.md §7,
# `infra/terraform/modules/scheduler/`); `sync_connector` todavía no está
# provisionado ahí — ver nota al tope del módulo.
JOBS_PERIODICOS: tuple[str, ...] = ("send_reminder_scan", "sync_connector")
JOBS_PERIODICOS_AUTOMATIONS: tuple[str, ...] = ("automation_scan",)


async def _enqueue_jobs(settings: Settings, job_types: tuple[str, ...]) -> None:
    # Import perezoso: edecan_core es un paquete hermano que puede aún no
    # existir en este workspace mientras se construye en paralelo
    # (ARCHITECTURE.md §10.1) — se importa aquí, no al tope del módulo, para
    # que `edecan_worker.scheduler` siga siendo importable sin él.
    from edecan_core.queue import enqueue

    for job_type in job_types:
        try:
            job_id = await enqueue(settings, job_type, {}, None)
            logger.info("%s encolado job_id=%s", job_type, job_id)
        except Exception:
            # Aislado por job: que falle el encolado de uno (p. ej. SQS
            # caído momentáneamente) no debe impedir que se intenten los
            # demás en este mismo tick; todos se reintentan en el próximo.
            logger.exception("fallo al encolar %s", job_type)


async def _tick(settings: Settings) -> None:
    await _enqueue_jobs(settings, JOBS_PERIODICOS)


async def _tick_automations(settings: Settings) -> None:
    await _enqueue_jobs(settings, JOBS_PERIODICOS_AUTOMATIONS)


async def run_forever(
    settings: Settings | None = None, *, stop_event: asyncio.Event | None = None
) -> None:
    settings = settings or get_settings()
    stop_event = stop_event or asyncio.Event()
    logger.info(
        "scheduler de desarrollo iniciado: encola %s cada %ss y %s cada %ss "
        "(en prod, EventBridge Scheduler cubre send_reminder_scan y "
        "automation_scan; sync_connector aun no tiene equivalente en prod, "
        "ver ARCHITECTURE.md §7 / docs/automatizaciones.md)",
        ", ".join(JOBS_PERIODICOS),
        INTERVALO_SEGUNDOS,
        ", ".join(JOBS_PERIODICOS_AUTOMATIONS),
        INTERVALO_SEGUNDOS_AUTOMATIONS,
    )
    # Arranca en "ahora" (no en 0): ver "Dos cadencias, un solo loop" en el
    # docstring del módulo — el primer chequeo de abajo debe salir `False`.
    ultimo_tick_automations = time.monotonic()
    while not stop_event.is_set():
        try:
            await _tick(settings)
        except Exception:
            logger.exception("fallo inesperado en el tick del scheduler")

        ahora = time.monotonic()
        if ahora - ultimo_tick_automations >= INTERVALO_SEGUNDOS_AUTOMATIONS:
            ultimo_tick_automations = ahora
            try:
                await _tick_automations(settings)
            except Exception:
                logger.exception("fallo inesperado en el tick de automatizaciones del scheduler")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=INTERVALO_SEGUNDOS)
        except TimeoutError:
            pass


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_forever(settings))


if __name__ == "__main__":
    main()
