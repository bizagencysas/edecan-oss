"""Job `proactive_scan`: minería proactiva de fondo (product design).

Barre `agent_missions` recientes de TODOS los tenants, detecta tareas
repetidas con `detect_routine_suggestions` y, para cada señal en el ladder
proactivo con etapa `action` o `suggestion` (`clasificar_proactividad`),
REGISTRA una sugerencia durable — reusando la tabla `automations` con una fila
`enabled=false` y `trigger.kind="suggestion"` (jamás se auto-crea una
automatización activa: `automation_scan` solo dispara filas con `enabled=true`
y `next_run_at IS NOT NULL`, y esta fila no cumple ninguna de las dos).

Job de sistema sin tenant propio — mismo criterio que `automation_scan.py`/
`send_reminder_scan.py`: se dispara sin `tenant_id` y barre todos los tenants.
En dev lo encola `edecan_worker.scheduler`; en prod puede encolarlo un
`aws_scheduler_schedule` equivalente. Determinista y barato: aritmética pura
(`edecan_automations.proactive`) + SQL parametrizado, sin LLM.

`scan_proactive` es la pieza testeable: recibe una sesión y devuelve la lista
de sugerencias registradas. `handle` solo la envuelve con la sesión "dueño".
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from edecan_schemas import JobEnvelope
from sqlalchemy import text

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)

SCAN_WINDOW_DAYS = 30
MAX_MISSIONS = 1000

# Etapas del ladder proactivo (product design) que merecen registrarse como
# sugerencia: `action`/`suggestion` proponen algo al dueño; `observation` se
# descarta (solo leer/analizar, no hay nada que proponer todavía).
_RECORD_STAGES = ("action", "suggestion")


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is not None:
        raise ValueError("proactive_scan es un job global")
    async with deps.session_factory(None) as session:
        registradas = await scan_proactive(session, now=datetime.now(UTC))
    logger.info("proactive_scan: %d sugerencia(s) registrada(s)", len(registradas))


async def scan_proactive(
    session: Any,
    *,
    now: datetime | None = None,
    window: timedelta = timedelta(days=SCAN_WINDOW_DAYS),
) -> list[dict[str, Any]]:
    """Minería proactiva determinista: detecta tareas repetidas recientes y
    registra una sugerencia durable por cada señal en etapa `action`/`suggestion`.

    Import perezoso de `edecan_automations` (paquete hermano, mismo criterio
    que `automation_scan.py`). Devuelve la lista de sugerencias registradas
    (cada una con `tenant_id`/`user_id`/`task`/`repetitions`) para que el
    llamador pueda loguear/auditar el efecto real.
    """
    from edecan_automations.proactive import clasificar_proactividad, detect_routine_suggestions

    ahora = now or datetime.now(UTC)
    misiones = await _list_recent_missions(session, ahora - window)

    registradas: list[dict[str, Any]] = []
    for (tenant_id, user_id), entries in _agrupar(misiones).items():
        for signal in detect_routine_suggestions(entries):
            if clasificar_proactividad(signal) not in _RECORD_STAGES:
                continue
            if await _record_suggestion(session, tenant_id, user_id, signal):
                registradas.append(
                    {
                        "tenant_id": str(tenant_id),
                        "user_id": str(user_id),
                        "task": signal.get("task"),
                        "repetitions": signal.get("repetitions"),
                    }
                )
    return registradas


async def _list_recent_missions(session: Any, since: datetime) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT tenant_id, user_id, objetivo, owner_agent_id, created_at "
            "FROM agent_missions WHERE created_at >= :since "
            "ORDER BY created_at DESC LIMIT :limit"
        ),
        {"since": since, "limit": MAX_MISSIONS},
    )
    return [dict(row) for row in result.mappings().all()]


def _agrupar(
    misiones: list[dict[str, Any]],
) -> dict[tuple[UUID, UUID], list[dict[str, Any]]]:
    """Agrupa misiones por `(tenant_id, user_id)` y las convierte al
    vocabulario que espera `detect_routine_suggestions` (`label`/`occurred_at`/
    `agent_id`). Determinista: el orden de salida sigue el de entrada."""
    grupos: dict[tuple[UUID, UUID], list[dict[str, Any]]] = defaultdict(list)
    for mision in misiones:
        tenant_id = UUID(str(mision["tenant_id"]))
        user_id = UUID(str(mision["user_id"]))
        grupos[(tenant_id, user_id)].append(
            {
                "label": str(mision.get("objetivo") or ""),
                "occurred_at": mision.get("created_at"),
                "agent_id": (
                    str(mision["owner_agent_id"])
                    if mision.get("owner_agent_id") is not None
                    else None
                ),
            }
        )
    return dict(grupos)


async def _record_suggestion(
    session: Any, tenant_id: UUID, user_id: UUID, signal: dict[str, Any]
) -> bool:
    """Registra la sugerencia como una fila `automations` deshabilitada.

    Reusa `automations` (no se crea una tabla nueva) pero NUNCA auto-crea una
    automatización activa: `enabled=false`, `next_run_at=NULL` y
    `trigger.kind="suggestion"` (que `automation_scan` ignora). Dedup
    determinista por `nombre` (derivado de la tarea normalizada): la misma
    tarea repetida no genera filas duplicadas en barridos sucesivos.
    """
    task = str(signal.get("task") or "").strip()
    if not task:
        return False
    nombre = f"Sugerencia de rutina: {task[:120]}"

    existing = await session.execute(
        text(
            "SELECT 1 FROM automations WHERE tenant_id = :tenant_id AND user_id = :user_id "
            "AND enabled = false AND trigger->>'kind' = 'suggestion' AND nombre = :nombre"
        ),
        {"tenant_id": str(tenant_id), "user_id": str(user_id), "nombre": nombre},
    )
    if existing.mappings().first() is not None:
        return False

    await session.execute(
        text(
            "INSERT INTO automations "
            "(id, tenant_id, user_id, nombre, descripcion, trigger, accion, enabled, next_run_at) "
            "VALUES (:id, :tenant_id, :user_id, :nombre, :descripcion, "
            "CAST(:trigger AS jsonb), CAST(:accion AS jsonb), false, NULL)"
        ),
        {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "nombre": nombre,
            "descripcion": (
                "Sugerencia propuesta por el scan proactivo; revísala y actívala si la quieres."
            ),
            "trigger": json.dumps({"kind": "suggestion"}),
            "accion": json.dumps({"kind": "agent_instruction", "instruccion": task}),
        },
    )
    return True
