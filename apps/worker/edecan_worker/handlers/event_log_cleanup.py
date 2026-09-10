"""Job diario `event_log_cleanup`: auto-elimina de `event_log` las filas con más de 7 días.

Se encola una vez al día desde el scheduler local (`edecan_local.worker_loop`,
`JOBS_PERIODICOS_DIARIOS`) con `tenant_id=None` (barrido global, mismo
criterio que `refresh_skills` — es una excepción deliberada a "SIEMPRE filtrar
por el tenant_id del job", ARCHITECTURE.md §2: la retención del log de eventos
es global, no por tenant).

El DELETE corta en `created_at < now() - interval '7 days'` y lo sirve el
índice `ix_event_log_created_at` (migración 0067): una pasada al día mantiene
la tabla acotada a ~8 días máximo, sin que la plataforma de logging crezca
infinita.

Fail-open: si el DELETE falla (BD caída, tabla sin migrar), el handler NO
lanza — devuelve `{"borradas": 0, "error": ...}` y las filas viejas quedan
para la próxima pasada diaria. Un fallo de limpieza jamás debe tumbar la cola
local ni marcar el job como error que reintente en bucle; peor caso, la
retención se estira un día.
"""

from __future__ import annotations

import logging

from edecan_schemas import JobEnvelope
from sqlalchemy import text

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)

_DELETE_SQL = text("DELETE FROM event_log WHERE created_at < now() - interval '7 days'")


async def handle(env: JobEnvelope, deps: Deps) -> dict[str, object] | None:
    try:
        async with deps.session_factory(None) as session:
            resultado = await session.execute(_DELETE_SQL)
    except Exception as exc:
        logger.exception(
            "event_log_cleanup: fallo en el DELETE (job_id=%s); se reintenta mañana.",
            env.job_id,
        )
        return {"borradas": 0, "error": f"{type(exc).__name__}: {exc}"}

    borradas = int(getattr(resultado, "rowcount", 0) or 0)
    logger.info("event_log_cleanup: %d filas de event_log borradas (más de 7 días).", borradas)
    return {"borradas": borradas}


__all__ = ["handle"]