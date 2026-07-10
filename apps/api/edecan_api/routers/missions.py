"""`/v1/missions` — crea y consulta misiones multi-agente (`ROADMAP_V2.md`
§7.4, §7.6, §7.9, §8; dueño WP-V2-06; `GET /{id}/detalle` observabilidad
enriquecida, WP-V6-10, ver más abajo y `docs/agentes.md` sección
"Observabilidad de misiones").

Router deliberadamente delgado: SOLO inserta/lee filas de `agent_missions`/
`agent_steps` (SQL parametrizado, ver más abajo) y encola el job
`"run_mission"` (`edecan_core.queue.enqueue`, ya en `edecan_schemas.JOB_TYPES`
— WP-V2-01 lo agregó). NUNCA importa `edecan_agents`: la planificación y
ejecución real de una misión ocurren de forma asíncrona en el worker
(`apps/worker/edecan_worker/handlers/run_mission.py`), no en el turno de esta
request — así una misión larga no bloquea el request/response de la API.
`GET /{id}/detalle` (WP-V6-10) respeta esta regla igual: solo lee lo que
`edecan_agents.orchestrator` ya dejó escrito en `agent_steps.usage`, nunca
importa ese paquete.

## `GET /{mission_id}/detalle` — observabilidad enriquecida (WP-V6-10)

`agent_steps.usage` (jsonb) ya guardaba, desde v2, el uso del LLM por paso, y
desde `WP-V6-10` (`edecan_agents.orchestrator._timing_usage`) también
`started_at`/`finished_at`; `agent_missions.presupuesto` ya guardaba
`replans_usados` desde `WP-V5-05` — pero `GET /{mission_id}` (arriba) nunca
le daba forma a nada de eso para la UI: devuelve las filas casi crudas. Este
endpoint AGREGA una vista enriquecida sin tocar ese contrato (`GET
/{mission_id}` sigue devolviendo exactamente lo mismo que antes de este WP,
ver `_get_mission_and_steps` — el helper que ahora comparten ambos, para no
duplicar el SELECT de `agent_steps`): `resultado` recortado
(`resultado_truncado`, cap `RESULTADO_TRUNCADO_LIMITE`), `usage` tal cual está
guardado más `started`/`finished` extraídos de ahí, y `agregados` (tokens
totales por tipo + conteo de pasos por status) calculados en Python sobre las
filas ya traídas — sin SQL de agregación nuevo. Mismo `Depends
(_require_agents_missions)`/aislamiento tenant+usuario que el resto del
router.

`edecan_api.main.create_app()` monta este router de forma defensiva
(`importlib.import_module` + `try/except ImportError` por cada router v2,
`ROADMAP_V2.md` §7.6, dueño WP-V2-01) — `apps/api/tests/
test_missions_router.py` de todos modos revisa si ya está montado antes de
incluirlo a mano (mismo patrón defensivo que `test_remote_router.py`), para
seguir funcionando aunque se ejecute contra una `app` armada sin pasar por
`create_app()`.

## SQL directo contra `agent_missions`/`agent_steps`

Igual que `edecan_api.routers.consents`/`edecan_toolkit.recordatorios`: SQL
parametrizado contra los nombres de tabla/columna pinned en `ROADMAP_V2.md`
§7.4 (`edecan_schemas.missions.MissionOut`/`MissionStepOut` documentan la
misma forma, y son las que usa este router como `response_model`; coinciden
con los modelos `edecan_db.models.AgentMission`/`AgentStep` de la migración
`0003_v2_expansion`, dueño WP-V2-01, ya aterrizada) — deliberadamente NO un
ORM de `edecan_db.models`: esa forma interna no está fijada por el contrato,
los nombres de tabla/columna sí (mismo criterio que `recordatorios.py`).
Tampoco se toca `edecan_api.repo` (fuera de la lista de rutas que le
corresponde escribir a este paquete de trabajo) — por eso las queries van
directo sobre la `AsyncSession` de `get_tenant_session` (RLS activo,
ARCHITECTURE.md §2) en vez de pasar por `Repo`/`get_repo`.

Todas las queries filtran también `tenant_id`/`user_id` explícitos aunque la
sesión ya tenga RLS activo (defensa en profundidad, mismo criterio que el
resto de `edecan_api`) — el aislamiento CROSS-TENANT real (404 si la misión
es de otro tenant) lo da la política `tenant_isolation` de Postgres; el
filtro por `user_id` es aplicativo (una misión es privada de quien la creó,
mismo criterio que `reminders`/`contacts`/`transactions`).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from edecan_core.queue import enqueue
from edecan_schemas import MissionOut, MissionStepOut
from edecan_schemas.missions import MISSION_STEP_STATUSES, MissionStepStatus
from edecan_schemas.plans import FLAG_AGENTS_MISSIONS, LIMIT_MISSIONS_PER_DAY, UNLIMITED
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, TenantCtx, get_current_user, get_tenant_session, rate_limit

router = APIRouter(prefix="/v1/missions", tags=["missions"], dependencies=[Depends(rate_limit)])

DEFAULT_MAX_STEPS = 8
"""Mismo default que `edecan_agents.orchestrator.DEFAULT_MAX_STEPS`/
`ROADMAP_V2.md` §7.5 (`MISSIONS_MAX_STEPS`) — se duplica aquí como literal
porque este router no depende de `edecan_agents` (ver docstring del módulo)."""

_ACTIVE_STEP_STATUS = "waiting_confirmation"
_TERMINAL_MISSION_STATUSES = ("done", "error", "cancelled")


class MissionCreateIn(BaseModel):
    objetivo: str


class MissionConfirmIn(BaseModel):
    approved: bool


class MissionDetailOut(BaseModel):
    mission: MissionOut
    steps: list[MissionStepOut]


class MissionStepDetalleOut(BaseModel):
    """Fila enriquecida de un paso para `GET /{mission_id}/detalle`
    (WP-V6-10) — mismos campos base que `MissionStepOut` salvo que
    `resultado` se recorta a `resultado_truncado` (ver `_truncar_resultado`,
    cap `RESULTADO_TRUNCADO_LIMITE`) y se agregan `started`/`finished`,
    extraídos de `usage["started_at"/"finished_at"]` cuando
    `edecan_agents.orchestrator` los persistió ahí (ver ese módulo, sección
    `started_at`/`finished_at` de su docstring) — `None` para pasos que
    corrieron antes de ese WP o que todavía no terminaron. `usage` viaja TAL
    CUAL está guardado (puede traer `input_tokens`/`output_tokens`,
    `pending_tool_call`, `started_at`/`finished_at`, o nada)."""

    seq: int
    agente: str
    instruccion: str
    status: MissionStepStatus = "pending"
    resultado_truncado: str | None = None
    usage: dict[str, Any] | None = None
    started: str | None = None
    finished: str | None = None


class MissionAgregadosOut(BaseModel):
    """Totales calculados en Python sobre las filas de `agent_steps` de la
    misión (WP-V6-10, `_calcular_agregados`) — sin SQL de agregación nuevo.
    `tokens_totales_por_tipo` suma, por cada clave de `usage` que termine en
    `_tokens` (p. ej. `input_tokens`/`output_tokens` de `edecan_llm.base.
    Usage`, y cualquier otra que se sume en el futuro sin tocar este código),
    el total across todos los pasos con ese dato. `pasos_por_status` cuenta
    los pasos por cada uno de los 6 valores de `MISSION_STEP_STATUSES`
    (`edecan_schemas.missions`), siempre con las 6 claves presentes (en 0 si
    ningún paso está en ese estado)."""

    tokens_totales_por_tipo: dict[str, int] = Field(default_factory=dict)
    pasos_por_status: dict[str, int] = Field(default_factory=dict)


class MissionDetalleOut(BaseModel):
    """`GET /{mission_id}/detalle` (WP-V6-10) — superset observabilidad de
    `MissionDetailOut`: el mismo `mission` (su `presupuesto` YA incluye
    `replans_usados` cuando `Orchestrator.run` replaneó al menos una vez —
    `edecan_agents.orchestrator`, sección "Replan acotado" — no se inventa
    ningún campo nuevo, se expone el jsonb real de `agent_missions.
    presupuesto` tal cual vive en la fila), pasos enriquecidos y agregados."""

    mission: MissionOut
    steps: list[MissionStepDetalleOut]
    agregados: MissionAgregadosOut


def _require_agents_missions(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current_user.tenant.flags.get(FLAG_AGENTS_MISSIONS, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Las misiones no están disponibles en tu plan.",
        )
    return current_user


async def _check_missions_quota(session: AsyncSession, tenant: TenantCtx) -> None:
    """`limits.missions_per_day`: `-1` ilimitado, `0` -> `403` (el plan no
    trae esta capacidad en absoluto), positivo -> `429` una vez alcanzado
    (mismo código que `conversations._check_message_quota`/
    `files._check_storage_quota` para "cupo agotado por hoy, vuelve mañana")."""
    limit = tenant.flags.get(LIMIT_MISSIONS_PER_DAY, 0)
    if limit == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Las misiones no están disponibles en tu plan '{tenant.plan_key}'.",
        )
    if limit == UNLIMITED:
        return

    since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await session.execute(
        text(
            "SELECT COUNT(*) FROM agent_missions "
            "WHERE tenant_id = :tenant_id AND created_at >= :since"
        ),
        {"tenant_id": str(tenant.tenant_id), "since": since},
    )
    count = int(result.scalar() or 0)
    if count >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Alcanzaste tu límite de {int(limit)} misiones por día de tu plan "
                f"'{tenant.plan_key}'. Vuelve a intentarlo mañana o mejora tu plan."
            ),
        )


_MISSION_COLUMNS = (
    "id, tenant_id, user_id, objetivo, status, plan, resultado, presupuesto, error, "
    "created_at, updated_at"
)
_STEP_COLUMNS = (
    "id, tenant_id, mission_id, seq, agente, instruccion, status, resultado, usage, "
    "created_at, updated_at"
)


async def _get_mission_row(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, mission_id: uuid.UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            f"SELECT {_MISSION_COLUMNS} FROM agent_missions "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id"
        ),
        {"tenant_id": str(tenant_id), "user_id": str(user_id), "id": str(mission_id)},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _require_mission(
    session: AsyncSession, current_user: CurrentUser, mission_id: uuid.UUID
) -> dict[str, Any]:
    row = await _get_mission_row(
        session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        mission_id=mission_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Misión no encontrada.")
    return row


async def _get_mission_and_steps(
    session: AsyncSession, current_user: CurrentUser, mission_id: uuid.UUID
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compartida por `GET /{mission_id}` y `GET /{mission_id}/detalle`
    (WP-V6-10) — misma misión + sus `agent_steps`, ordenados por `seq`; cada
    endpoint decide después cómo darle forma a la respuesta (`get_mission`
    los deja tal cual, `get_mission_detalle` los enriquece vía
    `_step_a_detalle`/`_calcular_agregados`). Evita duplicar el SELECT de
    `agent_steps` que ya tenía `get_mission` antes de este WP."""
    mission = await _require_mission(session, current_user, mission_id)
    result = await session.execute(
        text(
            f"SELECT {_STEP_COLUMNS} FROM agent_steps "
            "WHERE tenant_id = :tenant_id AND mission_id = :mission_id "
            "ORDER BY seq ASC"
        ),
        {"tenant_id": str(current_user.tenant_id), "mission_id": str(mission_id)},
    )
    steps = [dict(row) for row in result.mappings().all()]
    return mission, steps


RESULTADO_TRUNCADO_LIMITE = 2000
"""Cap de `resultado_truncado` (WP-V6-10, `GET /{mission_id}/detalle`) — un
paso puede producir un resultado arbitrariamente largo (p. ej. un reporte
completo), y esta ruta está pensada para un panel de UI, no para descargar el
resultado íntegro (eso lo sigue dando `GET /{mission_id}` sin recortar)."""
_RESULTADO_TRUNCADO_SUFIJO = "… (resultado truncado, ver el detalle completo en la misión)"


def _truncar_resultado(resultado: str | None) -> str | None:
    if resultado is None:
        return None
    if len(resultado) <= RESULTADO_TRUNCADO_LIMITE:
        return resultado
    return resultado[:RESULTADO_TRUNCADO_LIMITE] + _RESULTADO_TRUNCADO_SUFIJO


def _step_a_detalle(step: dict[str, Any]) -> dict[str, Any]:
    usage = step.get("usage")
    usage = usage if isinstance(usage, dict) else None
    return {
        "seq": step["seq"],
        "agente": step["agente"],
        "instruccion": step["instruccion"],
        "status": step["status"],
        "resultado_truncado": _truncar_resultado(step.get("resultado")),
        "usage": usage,
        # `edecan_agents.orchestrator._timing_usage` (WP-V6-10) las guarda
        # ahí en cada guardado TERMINAL de un paso — `None` para pasos que
        # corrieron antes de ese WP o que todavía no terminaron.
        "started": usage.get("started_at") if usage else None,
        "finished": usage.get("finished_at") if usage else None,
    }


_SUFIJO_CLAVES_TOKENS = "_tokens"


def _calcular_agregados(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Agregados de `GET /{mission_id}/detalle` (WP-V6-10) — calculados en
    Python sobre las filas ya traídas por `_get_mission_and_steps`, sin SQL
    de agregación nuevo (instrucción explícita del work package)."""
    tokens: dict[str, int] = {}
    pasos_por_status: dict[str, int] = {s: 0 for s in MISSION_STEP_STATUSES}
    for step in steps:
        estado = str(step.get("status") or "")
        pasos_por_status[estado] = pasos_por_status.get(estado, 0) + 1

        usage = step.get("usage")
        if not isinstance(usage, dict):
            continue
        for clave, valor in usage.items():
            if not clave.endswith(_SUFIJO_CLAVES_TOKENS):
                continue
            if isinstance(valor, bool) or not isinstance(valor, (int, float)):
                continue
            tokens[clave] = tokens.get(clave, 0) + int(valor)
    return {"tokens_totales_por_tipo": tokens, "pasos_por_status": pasos_por_status}


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MissionOut)
async def create_mission(
    body: MissionCreateIn,
    current_user: CurrentUser = Depends(_require_agents_missions),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    objetivo = body.objetivo.strip()
    if not objetivo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="objetivo es obligatorio."
        )

    await _check_missions_quota(session, current_user.tenant)

    max_steps = getattr(settings, "MISSIONS_MAX_STEPS", DEFAULT_MAX_STEPS)
    result = await session.execute(
        text(
            "INSERT INTO agent_missions "
            "(id, tenant_id, user_id, objetivo, status, plan, resultado, presupuesto, error) "
            "VALUES (gen_random_uuid(), :tenant_id, :user_id, :objetivo, 'planning', NULL, "
            "NULL, :presupuesto ::jsonb, NULL) "
            f"RETURNING {_MISSION_COLUMNS}"
        ),
        {
            "tenant_id": str(current_user.tenant_id),
            "user_id": str(current_user.user_id),
            "objetivo": objetivo,
            "presupuesto": json.dumps({"max_steps": max_steps}),
        },
    )
    row = result.mappings().first()
    assert row is not None
    mission = dict(row)

    payload = {"mission_id": str(mission["id"])}
    await enqueue(settings, "run_mission", payload, current_user.tenant_id)

    return mission


@router.get("", response_model=list[MissionOut])
async def list_missions(
    current_user: CurrentUser = Depends(_require_agents_missions),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            f"SELECT {_MISSION_COLUMNS} FROM agent_missions "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id "
            "ORDER BY created_at DESC"
        ),
        {"tenant_id": str(current_user.tenant_id), "user_id": str(current_user.user_id)},
    )
    return [dict(row) for row in result.mappings().all()]


@router.get("/{mission_id}", response_model=MissionDetailOut)
async def get_mission(
    mission_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_agents_missions),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    mission, steps = await _get_mission_and_steps(session, current_user, mission_id)
    return {"mission": mission, "steps": steps}


@router.get("/{mission_id}/detalle", response_model=MissionDetalleOut)
async def get_mission_detalle(
    mission_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_agents_missions),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Observabilidad enriquecida de una misión (WP-V6-10, `docs/agentes.md`
    sección "Observabilidad de misiones"): igual auth/flag/aislamiento que
    `GET /{mission_id}` (mismo `_get_mission_and_steps` — 404 si la misión no
    existe o es de otro tenant/usuario), pero con `resultado` recortado,
    `usage`/`started`/`finished` por paso y agregados de tokens/estado."""
    mission, steps = await _get_mission_and_steps(session, current_user, mission_id)
    return {
        "mission": mission,
        "steps": [_step_a_detalle(step) for step in steps],
        "agregados": _calcular_agregados(steps),
    }


@router.post("/{mission_id}/confirm", response_model=MissionOut)
async def confirm_mission(
    mission_id: uuid.UUID,
    body: MissionConfirmIn,
    current_user: CurrentUser = Depends(_require_agents_missions),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    mission = await _require_mission(session, current_user, mission_id)
    if mission["status"] != "waiting_confirmation":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta misión no tiene una confirmación pendiente.",
        )

    if not body.approved:
        await _update_mission_status(session, current_user.tenant_id, mission_id, "cancelled")
        await session.execute(
            text(
                "UPDATE agent_steps SET status = 'skipped', updated_at = now() "
                "WHERE tenant_id = :tenant_id AND mission_id = :mission_id AND status = :waiting"
            ),
            {
                "tenant_id": str(current_user.tenant_id),
                "mission_id": str(mission_id),
                "waiting": _ACTIVE_STEP_STATUS,
            },
        )
        return await _require_mission(session, current_user, mission_id)

    pending_seq = await _find_waiting_step_seq(session, current_user.tenant_id, mission_id)
    if pending_seq is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se encontró el paso pendiente de confirmación de esta misión.",
        )

    await _update_mission_status(session, current_user.tenant_id, mission_id, "running")
    await enqueue(
        settings,
        "run_mission",
        {"mission_id": str(mission_id), "resume": True, "approved_step_seq": pending_seq},
        current_user.tenant_id,
    )
    return await _require_mission(session, current_user, mission_id)


@router.post("/{mission_id}/cancel", response_model=MissionOut)
async def cancel_mission(
    mission_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_agents_missions),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    mission = await _require_mission(session, current_user, mission_id)
    if mission["status"] in _TERMINAL_MISSION_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Esta misión ya terminó (status={mission['status']}).",
        )

    await _update_mission_status(session, current_user.tenant_id, mission_id, "cancelled")
    return await _require_mission(session, current_user, mission_id)


async def _update_mission_status(
    session: AsyncSession, tenant_id: uuid.UUID, mission_id: uuid.UUID, new_status: str
) -> None:
    await session.execute(
        text(
            "UPDATE agent_missions SET status = :status, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"status": new_status, "tenant_id": str(tenant_id), "id": str(mission_id)},
    )


async def _find_waiting_step_seq(
    session: AsyncSession, tenant_id: uuid.UUID, mission_id: uuid.UUID
) -> int | None:
    result = await session.execute(
        text(
            "SELECT seq FROM agent_steps "
            "WHERE tenant_id = :tenant_id AND mission_id = :mission_id AND status = :waiting "
            "ORDER BY seq ASC LIMIT 1"
        ),
        {
            "tenant_id": str(tenant_id),
            "mission_id": str(mission_id),
            "waiting": _ACTIVE_STEP_STATUS,
        },
    )
    row = result.mappings().first()
    return int(row["seq"]) if row is not None else None
