"""`HANDLERS`: despacho de tipo de job → función manejadora (ARCHITECTURE.md §10.11).

`Handler = Callable[[JobEnvelope, Deps], Awaitable[None]]`. `HANDLERS` cubre
los 7 `JOB_TYPES` de v1 SIEMPRE, más hasta 3 tipos nuevos de v2
(ROADMAP_V2.md §7.3/§7.6, dueño WP-V2-01: `run_mission`, `run_automation`,
`automation_scan`), hasta 1 tipo nuevo de v5 (`ARCHITECTURE.md` §14, dueño
WP-V5-01: `generate_podcast`) y hasta 1 tipo nuevo de v6 (`ARCHITECTURE.md`
§15, dueño WP-V6-01: `process_meeting`) si sus handlers ya aterrizaron — ver
las secciones "v2"/"v5"/"v6" más abajo. Cada handler abre sus propias transacciones vía
`deps.session_factory(None)` (conexión "dueño", bypassa Row-Level Security —
ARCHITECTURE.md §2) y SIEMPRE filtra manualmente por el `tenant_id` del job
en cada query (ver `edecan_worker.repo`).

Importa los SUBMÓDULOS (no `handle` directamente) y arma `HANDLERS` con
`<submodulo>.handle`: si en vez de esto se hiciera `from .ingest_file import
handle as ingest_file`, ese `import ... as ingest_file` SOBRESCRIBIRÍA el
atributo `ingest_file` que Python ya fija en este paquete al importar el
submódulo — dejando `edecan_worker.handlers.ingest_file` apuntando a la
función `handle` en vez de al módulo, y rompiendo cualquier código (p. ej.
`tests/`) que necesite `import edecan_worker.handlers.ingest_file` para
monkeypatchear algo dentro de ese módulo. `_register_defensive` (v2, más
abajo) respeta la misma regla: `importlib.import_module(...)` dentro de
`try/except` deja el submódulo importado como atributo normal del paquete
(mismo mecanismo de Python que el `from . import ingest_file, ...` de arriba)
en vez de aliasearlo.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Awaitable, Callable

from edecan_schemas import JobEnvelope

from edecan_worker.deps import Deps

from . import (
    generate_content,
    ingest_file,
    memory_consolidate,
    notify_important_event,
    notify_incoming_phone_call,
    notify_phone_call_summary,
    run_campaign_step,
    send_reminder,
    send_reminder_scan,
    sync_connector,
)

logger = logging.getLogger(__name__)

Handler = Callable[[JobEnvelope, Deps], Awaitable[None]]

HANDLERS: dict[str, Handler] = {
    "ingest_file": ingest_file.handle,
    "sync_connector": sync_connector.handle,
    "send_reminder": send_reminder.handle,
    "send_reminder_scan": send_reminder_scan.handle,
    "run_campaign_step": run_campaign_step.handle,
    "generate_content": generate_content.handle,
    "memory_consolidate": memory_consolidate.handle,
    "notify_phone_call_summary": notify_phone_call_summary.handle,
    "notify_incoming_phone_call": notify_incoming_phone_call.handle,
    "notify_important_event": notify_important_event.handle,
}


def _register_defensive(handlers: dict[str, Handler], job_type: str, module_name: str) -> None:
    """`handlers[job_type] = edecan_worker.handlers.<module_name>.handle`, solo
    si ese submódulo existe. Si no (el WP dueño todavía no lo aterrizó),
    registra un `logger.warning` y deja `handlers` intacto — nunca revienta
    el import de este paquete (ARCHITECTURE.md §10.1: aterrizajes parciales
    tolerados, misma filosofía que `edecan_api.main.create_app()` con los
    routers v2/v3/v4/v5, ver el docstring de ese módulo). Reutilizado tal
    cual por v2 y v5 (ver las dos secciones más abajo) — el mensaje de log NO
    fija ningún número de versión a propósito, porque a este helper genérico
    le es indistinto cuál WP registra qué.
    """
    try:
        mod = importlib.import_module(f".{module_name}", __name__)
    except ImportError:
        logger.warning(
            "handler 'edecan_worker.handlers.%s' no disponible todavía "
            "(WP no aterrizado) — job type %r queda sin handler por ahora.",
            module_name,
            job_type,
        )
        return
    handlers[job_type] = mod.handle
    logger.info(
        "handler 'edecan_worker.handlers.%s' registrado para job type %r.",
        module_name,
        job_type,
    )


# v2 (ROADMAP_V2.md §7.3/§7.6): `run_mission` → `handlers.run_mission`
# (WP-V2-06); `run_automation`/`automation_scan` → `handlers.run_automation`/
# `handlers.automation_scan` (WP-V2-07). Los 7 handlers de v1 arriba NUNCA
# pasan por esta ruta defensiva: son parte del contrato congelado de v1 y su
# ausencia SÍ debe romper el import (sería un error real, no un aterrizaje
# parcial esperado).
_register_defensive(HANDLERS, "run_mission", "run_mission")
_register_defensive(HANDLERS, "run_automation", "run_automation")
_register_defensive(HANDLERS, "automation_scan", "automation_scan")

# v5 (ARCHITECTURE.md §14, dueño WP-V5-01): `generate_podcast` →
# `handlers.generate_podcast` (dueño real WP-V5-11, en paralelo) — mismo
# criterio defensivo que v2 arriba.
_register_defensive(HANDLERS, "generate_podcast", "generate_podcast")

# v6 (ARCHITECTURE.md §15, dueño WP-V6-01): `process_meeting` →
# `handlers.process_meeting` (dueño real WP-V6-05, en paralelo) — mismo
# criterio defensivo que v2/v5 arriba.
_register_defensive(HANDLERS, "process_meeting", "process_meeting")

__all__ = ["HANDLERS", "Handler"]
