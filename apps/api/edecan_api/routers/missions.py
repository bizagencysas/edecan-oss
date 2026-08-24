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
from edecan_core.safety import redact
from edecan_schemas import MissionOut, MissionStepOut
from edecan_schemas.missions import MISSION_STATUSES, MISSION_STEP_STATUSES, MissionStepStatus
from edecan_schemas.plans import FLAG_AGENTS_MISSIONS, LIMIT_MISSIONS_PER_DAY, UNLIMITED
from fastapi import APIRouter, Depends, HTTPException, Query, status
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
_PAUSABLE_MISSION_STATUSES = ("planning", "running", "waiting_confirmation")


class MissionCreateIn(BaseModel):
    objetivo: str


class MissionConfirmIn(BaseModel):
    approved: bool


class MissionSteerIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)


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
    trace: list[dict[str, Any]] = Field(default_factory=list)


class MissionReproductionStepOut(BaseModel):
    seq: int
    agente: str
    instruccion: str
    status: MissionStepStatus
    provenance: dict[str, Any] | None = None


class MissionReproductionOut(BaseModel):
    """Manifiesto seguro para staging/debugging; nunca ejecuta la misión."""

    mission_id: uuid.UUID
    objetivo: str
    workflow_version: str | None = None
    plan: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[MissionReproductionStepOut] = Field(default_factory=list)


class MissionInboxOut(BaseModel):
    """Resumen portable entre clientes: atención, actividad y recientes."""

    attention: list[MissionOut] = Field(default_factory=list)
    active: list[MissionOut] = Field(default_factory=list)
    recent: list[MissionOut] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


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
    "archived_at, created_at, updated_at"
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


def _redactar_json(value: Any) -> Any:
    """Redacta strings dentro de un manifiesto sin alterar su estructura."""

    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(key): _redactar_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redactar_json(item) for item in value]
    return value


def _usage_publico(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """Conserva métricas del paso, pero nunca entrega args de una tool."""
    if usage is None:
        return None
    public = dict(usage)
    pending = public.get("pending_tool_call")
    if isinstance(pending, dict):
        safe_pending = {
            key: pending[key] for key in ("id", "tool_call_id", "name") if key in pending
        }
        # Mantener `{}` conserva compatibilidad del contrato para una llamada
        # sin argumentos, sin permitir que un payload real se filtre.
        if pending.get("args") == {}:
            safe_pending["args"] = {}
        public["pending_tool_call"] = safe_pending
    return public


def _step_a_detalle(step: dict[str, Any]) -> dict[str, Any]:
    usage = step.get("usage")
    usage = usage if isinstance(usage, dict) else None
    return {
        "seq": step["seq"],
        "agente": step["agente"],
        "instruccion": redact(str(step["instruccion"])),
        "status": step["status"],
        "resultado_truncado": _truncar_resultado(
            redact(str(step["resultado"])) if step.get("resultado") is not None else None
        ),
        "usage": _usage_publico(usage),
        # `edecan_agents.orchestrator._timing_usage` (WP-V6-10) las guarda
        # ahí en cada guardado TERMINAL de un paso — `None` para pasos que
        # corrieron antes de ese WP o que todavía no terminaron.
        "started": usage.get("started_at") if usage else None,
        "finished": usage.get("finished_at") if usage else None,
    }


def _step_a_reproduccion(step: dict[str, Any]) -> dict[str, Any]:
    usage = step.get("usage")
    provenance = usage.get("provenance") if isinstance(usage, dict) else None
    return {
        "seq": step["seq"],
        "agente": step["agente"],
        "instruccion": redact(str(step["instruccion"])),
        "status": step["status"],
        "provenance": provenance if isinstance(provenance, dict) else None,
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


def _construir_trace(mission: dict[str, Any], steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruye un trace seguro desde checkpoints durables, sin prompts/args."""
    root_id = f"mission:{mission['id']}"
    trace: list[dict[str, Any]] = [
        {
            "id": root_id,
            "parent_id": None,
            "kind": "mission",
            "name": "agent_mission",
            "status": mission.get("status"),
        }
    ]
    for step in steps:
        usage = step.get("usage") if isinstance(step.get("usage"), dict) else {}
        provenance = usage.get("provenance") if isinstance(usage.get("provenance"), dict) else {}
        step_id = f"{root_id}:step:{step['seq']}"
        started = usage.get("started_at")
        finished = usage.get("finished_at")
        duration_ms: int | None = None
        if isinstance(started, str) and isinstance(finished, str):
            try:
                duration_ms = max(
                    0,
                    int(
                        (
                            datetime.fromisoformat(finished) - datetime.fromisoformat(started)
                        ).total_seconds()
                        * 1000
                    ),
                )
            except ValueError:
                duration_ms = None
        token_usage = {
            key: int(value)
            for key, value in usage.items()
            if key.endswith("_tokens")
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        }
        trace.append(
            {
                "id": step_id,
                "parent_id": root_id,
                "kind": "agent",
                "name": str(step.get("agente") or "agent"),
                "status": step.get("status"),
                "started": started,
                "finished": finished,
                "duration_ms": duration_ms,
                "token_usage": token_usage,
                "model_alias": provenance.get("model_alias"),
                "workflow_version": provenance.get("workflow_version"),
                "failure_category": usage.get("failure_category"),
            }
        )
        for index, tool_name in enumerate(provenance.get("tools_requested") or []):
            trace.append(
                {
                    "id": f"{step_id}:tool:{index}",
                    "parent_id": step_id,
                    "kind": "tool",
                    "name": str(tool_name),
                    "status": step.get("status"),
                }
            )
    return trace


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
    search: str | None = Query(default=None, max_length=200),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    include_archived: bool = Query(default=False),
) -> list[dict[str, Any]]:
    if status_filter is not None and status_filter not in MISSION_STATUSES:
        raise HTTPException(status_code=422, detail="Estado de misión inválido.")
    search_pattern = f"%{search.strip()}%" if search and search.strip() else None
    result = await session.execute(
        text(
            f"SELECT {_MISSION_COLUMNS} FROM agent_missions "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id "
            "AND (:include_archived OR archived_at IS NULL) "
            "AND (:search IS NULL OR objetivo ILIKE :search) "
            "AND (:status IS NULL OR status = :status) "
            "ORDER BY created_at DESC"
        ),
        {
            "tenant_id": str(current_user.tenant_id),
            "user_id": str(current_user.user_id),
            "search": search_pattern,
            "status": status_filter,
            "include_archived": include_archived,
        },
    )
    return [dict(row) for row in result.mappings().all()]


@router.get("/inbox", response_model=MissionInboxOut)
async def get_missions_inbox(
    current_user: CurrentUser = Depends(_require_agents_missions),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Inbox cross-device estable; no expone eventos internos por paso."""
    result = await session.execute(
        text(
            f"SELECT {_MISSION_COLUMNS} FROM agent_missions "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND archived_at IS NULL "
            "ORDER BY updated_at DESC"
        ),
        {"tenant_id": str(current_user.tenant_id), "user_id": str(current_user.user_id)},
    )
    rows = [dict(row) for row in result.mappings().all()]
    attention = [row for row in rows if row["status"] in ("waiting_confirmation", "error")]
    active = [row for row in rows if row["status"] in ("planning", "running", "paused")]
    counts: dict[str, int] = {}
    for row in rows:
        status_name = str(row["status"])
        counts[status_name] = counts.get(status_name, 0) + 1
    return {
        "attention": attention,
        "active": active,
        "recent": rows[:8],
        "counts": counts,
    }


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
        "trace": _construir_trace(mission, steps),
    }


@router.get("/{mission_id}/reproduction", response_model=MissionReproductionOut)
async def get_mission_reproduction(
    mission_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_agents_missions),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Devuelve un manifiesto sin efectos laterales para reproducir en staging.

    No incluye `pending_tool_call.args`, no copia aprobaciones humanas y no
    encola el worker: cualquier ejecución posterior debe crear una misión nueva
    y pasar por los mismos flags y confirmaciones actuales.
    """
    mission, steps = await _get_mission_and_steps(session, current_user, mission_id)
    versions = [
        (step.get("usage") or {}).get("provenance", {}).get("workflow_version")
        for step in steps
        if isinstance(step.get("usage"), dict)
    ]
    workflow_version = next((version for version in versions if isinstance(version, str)), None)
    return {
        "mission_id": mission["id"],
        "objetivo": redact(str(mission["objetivo"])),
        "workflow_version": workflow_version,
        "plan": _redactar_json(mission.get("plan") or []),
        "steps": [_step_a_reproduccion(step) for step in steps],
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


@router.post("/{mission_id}/pause", response_model=MissionOut)
async def pause_mission(
    mission_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_agents_missions),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Pausa en un checkpoint; no elimina plan, pasos ni resultados."""
    mission = await _require_mission(session, current_user, mission_id)
    if mission["status"] not in _PAUSABLE_MISSION_STATUSES:
        raise HTTPException(status_code=409, detail="Esta misión no se puede pausar ahora.")
    await _update_mission_status(session, current_user.tenant_id, mission_id, "paused")
    return await _require_mission(session, current_user, mission_id)


@router.post("/{mission_id}/resume", response_model=MissionOut)
async def resume_mission(
    mission_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_agents_missions),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Reanuda una misión pausada desde sus checkpoints persistidos."""
    mission = await _require_mission(session, current_user, mission_id)
    if mission["status"] != "paused":
        raise HTTPException(status_code=409, detail="Esta misión no está pausada.")
    await _update_mission_status(session, current_user.tenant_id, mission_id, "running")
    await enqueue(
        settings,
        "run_mission",
        {"mission_id": str(mission_id), "resume_paused": True},
        current_user.tenant_id,
    )
    return await _require_mission(session, current_user, mission_id)


@router.post("/{mission_id}/steer", response_model=MissionOut)
async def steer_mission(
    mission_id: uuid.UUID,
    body: MissionSteerIn,
    current_user: CurrentUser = Depends(_require_agents_missions),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Añade una instrucción al trabajo en curso sin reiniciar el plan."""
    mission = await _require_mission(session, current_user, mission_id)
    if mission["status"] in _TERMINAL_MISSION_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta misión ya terminó; no se puede redirigir.",
        )
    instruction = body.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="instruction es obligatorio.")
    presupuesto = mission.get("presupuesto") or {}
    if isinstance(presupuesto, str):
        presupuesto = json.loads(presupuesto)
    if not isinstance(presupuesto, dict):
        presupuesto = {}
    notes = [note for note in (presupuesto.get("steering") or []) if isinstance(note, dict)]
    notes.append({"instruction": instruction, "at": datetime.now(UTC).isoformat()})
    presupuesto = {**presupuesto, "steering": notes[-20:]}
    await session.execute(
        text(
            "UPDATE agent_missions SET presupuesto = :presupuesto ::jsonb, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {
            "presupuesto": json.dumps(presupuesto),
            "tenant_id": str(current_user.tenant_id),
            "id": str(mission_id),
        },
    )
    return await _require_mission(session, current_user, mission_id)


@router.post("/{mission_id}/archive", response_model=MissionOut)
async def archive_mission(
    mission_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_agents_missions),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Oculta una misión terminal sin borrar su resultado ni sus pasos."""
    mission = await _require_mission(session, current_user, mission_id)
    if mission["status"] not in _TERMINAL_MISSION_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Solo se pueden archivar misiones terminadas.",
        )
    await session.execute(
        text(
            "UPDATE agent_missions SET archived_at = now(), updated_at = now() "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id"
        ),
        {
            "tenant_id": str(current_user.tenant_id),
            "user_id": str(current_user.user_id),
            "id": str(mission_id),
        },
    )
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
