"""`delegar_mision` — herramienta de agente que crea una misión y la encola
para el `Orchestrator` (`ROADMAP_V2.md` §7.7, §7.9; `ARCHITECTURE.md` §10.7).

Igual que `edecan_toolkit.recordatorios.CrearRecordatorioTool`: inserta la
fila con SQL parametrizado contra el esquema pinned de `ROADMAP_V2.md` §7.4
(`edecan_schemas.missions.MissionOut` documenta la misma forma, y coincide
con el modelo `edecan_db.models.AgentMission` de la migración
`0003_v2_expansion`, dueño WP-V2-01, ya aterrizada) — deliberadamente NO
importa el ORM de `edecan_db.models`: esa forma interna no está fijada por
el contrato, mientras que los nombres de tabla/columna sí lo están (mismo
criterio que `recordatorios.py`, no una limitación temporal de este archivo).

La ejecución real de la misión (planificación + pasos + síntesis) ocurre
DESPUÉS, de forma asíncrona, en el worker
(`apps/worker/edecan_worker/handlers/run_mission.py`, job `"run_mission"` —
ya está en `edecan_schemas.JOB_TYPES`). Esta tool solo crea la fila en
`status="planning"` y encola el job: nunca importa ni llama al
`Orchestrator` directamente, para no bloquear el turno del agente principal
esperando una misión potencialmente larga.

## `limits.missions_per_day` (Hallazgo 2 de `docs/seguridad-modelo-amenazas.md`, RESUELTO)

`POST /v1/missions` (`apps/api/edecan_api/routers/missions.py::
_check_missions_quota`, WP-V6-10) y esta tool encolan el MISMO job
`run_mission` para la misma capacidad — cada uno dispara un turno completo de
agente headless en el worker (costo real de LLM), así que ambos caminos
deben respetar el mismo cupo diario, no solo el flag booleano
`agents.missions` (`requires_flags`, abajo). `_cupo_disponible` replica
exactamente el criterio de `_check_missions_quota`: lee `LIMIT_MISSIONS_PER_DAY`
de `ctx.extras["flags"]` (mismo dict de flags del tenant que
`conversations._build_ctx` ya deja ahí, ver `ToolContext.extras` en
`edecan_core.tools.base`), `-1` = ilimitado, `0` (o ausente) = sin cupo en
absoluto (fail closed, igual que el router), positivo = cuenta
`agent_missions` creadas hoy contra ese límite. A diferencia del router, que
levanta `HTTPException`, acá se devuelve un `ToolResult` explicando el cupo
agotado: esta tool nunca lanza por errores "de negocio" (ver
`Tool.run` en `edecan_core.tools.base`).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from edecan_core import Tool, ToolContext, ToolResult
from edecan_core.queue import enqueue
from edecan_schemas.plans import LIMIT_MISSIONS_PER_DAY, UNLIMITED
from sqlalchemy import text

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 8
"""Mismo default que `orchestrator.DEFAULT_MAX_STEPS`/`ROADMAP_V2.md` §7.5
(`MISSIONS_MAX_STEPS`) — se duplica aquí como literal porque esta tool solo
necesita el número para congelarlo en `presupuesto`, no el resto del módulo
del Orchestrator."""

FLAG_AGENTS_MISSIONS = "agents.missions"

_MSG_CUPO_AGOTADO = (
    "Alcanzaste tu límite de misiones por día de tu plan. Vuelve a intentarlo "
    "mañana o mejora tu plan."
)


def _tenant_flags(ctx: ToolContext) -> dict[str, Any]:
    """Mismo patrón que `edecan_toolkit.contenido._tenant_flags`/
    `edecan_automations.tools._tenant_flags` (duplicado a propósito: este
    paquete no depende de ninguno de esos dos) — lee los flags de plan del
    tenant desde `ctx.extras["flags"]`, donde
    `apps.api.edecan_api.routers.conversations._build_ctx` los deja
    (`ARCHITECTURE.md` §10.7). `{}` si no están (mismo default "fail closed"
    que el resto de estos helpers duplicados)."""
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    flags = extras.get("flags")
    return flags if isinstance(flags, dict) else {}


class DelegarMisionTool(Tool):
    name = "delegar_mision"
    description = (
        "Crea una misión autónoma para un objetivo que requiere varios pasos "
        "encadenados de investigación, análisis de datos o generación de "
        "contenido. Un orquestador la planifica y la ejecuta en segundo "
        "plano delegando en sub-agentes especializados; el resultado queda "
        "disponible en la página Misiones cuando termina. No uses esta "
        "herramienta para preguntas simples que puedas responder tú mismo en "
        "este turno."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "objetivo": {
                "type": "string",
                "description": (
                    "Objetivo de la misión, descrito con el detalle suficiente "
                    "para que un planificador lo divida en pasos."
                ),
            }
        },
        "required": ["objetivo"],
    }
    requires_flags = frozenset({FLAG_AGENTS_MISSIONS})
    dangerous = False

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        objetivo = str(args.get("objetivo", "")).strip()
        if not objetivo:
            return ToolResult(content="Falta 'objetivo': describe qué debe lograr la misión.")

        if not await self._cupo_disponible(ctx):
            return ToolResult(content=_MSG_CUPO_AGOTADO)

        max_steps = getattr(ctx.settings, "MISSIONS_MAX_STEPS", DEFAULT_MAX_STEPS)
        mission_id = uuid4()
        presupuesto = json.dumps({"max_steps": max_steps})

        await ctx.session.execute(
            text(
                "INSERT INTO agent_missions "
                "(id, tenant_id, user_id, objetivo, status, plan, resultado, "
                "presupuesto, error) "
                "VALUES (:id, :tenant_id, :user_id, :objetivo, 'planning', NULL, "
                "NULL, :presupuesto ::jsonb, NULL)"
            ),
            {
                "id": str(mission_id),
                "tenant_id": str(ctx.tenant_id),
                "user_id": str(ctx.user_id),
                "objetivo": objetivo,
                "presupuesto": presupuesto,
            },
        )

        await enqueue(ctx.settings, "run_mission", {"mission_id": str(mission_id)}, ctx.tenant_id)

        logger.info(
            "delegar_mision: misión %s creada y encolada (tenant=%s)", mission_id, ctx.tenant_id
        )

        return ToolResult(
            content="Misión creada; sigue el avance en la página Misiones.",
            data={"mission_id": str(mission_id)},
        )

    async def _cupo_disponible(self, ctx: ToolContext) -> bool:
        """Mismo criterio que `missions.py::_check_missions_quota` (ver
        docstring del módulo, sección `limits.missions_per_day`): `-1`
        ilimitado, `0` (o ausente) sin cupo en absoluto, positivo se compara
        contra las `agent_missions` de este tenant creadas desde la
        medianoche UTC de hoy."""
        limite = _tenant_flags(ctx).get(LIMIT_MISSIONS_PER_DAY, 0)
        if limite == UNLIMITED:
            return True
        if limite == 0:
            return False

        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        resultado = await ctx.session.execute(
            text(
                "SELECT COUNT(*) FROM agent_missions "
                "WHERE tenant_id = :tenant_id AND created_at >= :since"
            ),
            {"tenant_id": str(ctx.tenant_id), "since": since},
        )
        count = int(resultado.scalar() or 0)
        return count < limite


def get_all_tools() -> list[Tool]:
    """Entry point `edecan.tools` (ver `[project.entry-points]` en `pyproject.toml`)."""
    return [DelegarMisionTool()]
