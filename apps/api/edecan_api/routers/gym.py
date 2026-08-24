"""`/v1/gym` — gimnasio inteligente: check-in diario, plan de entrenamiento
generado por LLM, sesión con máquina de estados y historial.

**Sin flag de plan nuevo**: igual que `edecan_api.routers.negocios`, este
router está disponible en TODOS los planes — el check-in no consume recursos
caros por sí solo (solo Postgres); la generación del plan usa el LLM
administrado de siempre (`get_llm_router`), no un proveedor premium.

**Patrón** (mismo que `routers/negocios.py`/`routers/automations.py`): habla
SQL parametrizado directo contra `workout_plans`/`workout_sessions`/
`gym_checkins` (`packages/db/alembic/versions/0034_gym_tables.py`) sobre
`Depends(get_tenant_session)` (RLS activa, `ARCHITECTURE.md` §2) — no toca
`edecan_api/repo.py`. La lógica de dominio (generación del plan, máquina de
estados de la sesión, check-in) vive en `edecan_gym` (`packages/gym`), un
paquete puro; este módulo solo hace el mapeo HTTP + persistencia.

**Collage best-effort**: `_generar_collage` genera UNA imagen con
`edecan_gym.prompt_collage` usando el proveedor bring-your-own del tenant
(`edecan_creative.get_tenant_image_provider`). Si el tenant no tiene una
credencial real (solo `StubImageProvider`), o falla CUALQUIER paso (generar,
subir, firmar la URL), `imagen_url` queda `None` y el plan funciona igual
(texto + series/reps). Un fallo de imagen NUNCA tumba el check-in.

**Guardrail de salud (ROADMAP_V2 §8.3)**: el plan es guía de ejercicio, no
consejo médico. El `mensaje` del check-in "si" incluye la línea corta
no-médica "Ajusta el peso a tu nivel y calienta antes de empezar." y nunca se
inventa un diagnóstico.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

from edecan_creative import StubImageProvider, get_tenant_image_provider, subir_archivo
from edecan_gym import (
    Ejercicio,
    WorkoutPlan,
    WorkoutSession,
    decidir,
    generar_plan,
    prompt_collage,
)
from edecan_llm.base import ChatMessage, CompletionRequest
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    CurrentUser,
    get_current_user,
    get_llm_router,
    get_tenant_session,
    get_vault,
    rate_limit,
)

router = APIRouter(prefix="/v1/gym", tags=["gym"], dependencies=[Depends(rate_limit)])

logger = logging.getLogger(__name__)

# Estados de sesión que cuentan como "en curso" para `GET /v1/gym/session`.
_ESTADOS_EN_CURSO = ("planned", "active", "paused")

_ALIAS_LLM = "principal"
_MAX_TOKENS_PLAN = 2048

_GUARDRAIL_SALUD = "Ajusta el peso a tu nivel y calienta antes de empezar."

# ---------------------------------------------------------------------------
# Esquemas de entrada
# ---------------------------------------------------------------------------


class CheckinIn(BaseModel):
    """`{"respuesta": "si"|"no"}` — se valida contra `edecan_gym.decidir`."""

    # Se deja como `str` abierto (no `Literal`) para que `decidir` sea la única
    # fuente de verdad del vocabulario (acepta "si"/"sí"/"yes"/"no"/"nope" y
    # lanza `ValueError` en otro caso, que este router mapea a 422).
    respuesta: str = Field(min_length=1, max_length=16)


class SerieIn(BaseModel):
    """Body de `POST /v1/gym/sessions/{id}/sets`."""

    ejercicio_idx: int = Field(ge=0)
    repeticiones: int = Field(gt=0)
    peso_kg: float | None = None


# ---------------------------------------------------------------------------
# Serialización (contracto exacto que consume iOS — ver encargo)
# ---------------------------------------------------------------------------


def _from_jsonb(value: Any) -> Any:
    """Columna `jsonb` que el driver puede devolver como `str` crudo."""
    if isinstance(value, str):
        return json.loads(value) if value else []
    return value if value is not None else []


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _plan_to_dict(plan: WorkoutPlan) -> dict[str, Any]:
    return plan.to_dict()


def _session_to_dict(session: WorkoutSession, session_id: uuid.UUID) -> dict[str, Any]:
    data = session.to_dict()
    resumen = session.resumen()
    return {
        "id": str(session_id),
        "estado": data["estado"],
        "plan": data["plan"],
        "started_at": data["started_at"],
        "series": data["series"],
        "progreso": {"ejercicios": resumen["progreso"]},
    }


def _plan_from_row(row: dict[str, Any]) -> WorkoutPlan:
    raw_ejercicios = _from_jsonb(row.get("plan_ejercicios", row.get("ejercicios")))
    ejercicios = [Ejercicio.from_dict(e) for e in raw_ejercicios]
    return WorkoutPlan(
        titulo=row["titulo"],
        objetivo=row["objetivo"],
        duracion_min=row["duracion_min"],
        ejercicios=ejercicios,
        imagen_url=row.get("imagen_url"),
        imagen_file_id=row.get("imagen_file_id"),
    )


def _session_from_row(row: dict[str, Any]) -> WorkoutSession:
    plan = _plan_from_row(row)
    return WorkoutSession.from_dict(
        {
            "estado": row["estado"],
            "started_at": _iso(row.get("started_at")),
            "series": _from_jsonb(row.get("series")),
        },
        plan,
    )


def _historial_entry(session: WorkoutSession) -> dict[str, Any]:
    data = session.to_dict()
    data.update(session.resumen())
    return data


# ---------------------------------------------------------------------------
# Acceso a datos (SQL parametrizado directo, tenant-scoped)
# ---------------------------------------------------------------------------

_SELECT_SESSION_CON_PLAN = text(
    """
    SELECT ws.id, ws.tenant_id, ws.user_id, ws.plan_id, ws.estado,
           ws.started_at, ws.ended_at, ws.series,
           wp.fecha, wp.titulo, wp.objetivo, wp.duracion_min,
           wp.ejercicios AS plan_ejercicios, wp.imagen_url, wp.imagen_file_id
    FROM workout_sessions ws
    JOIN workout_plans wp ON wp.id = ws.plan_id
    WHERE ws.tenant_id = :tenant_id AND ws.id = :id
    """
)


async def _load_session(
    session: AsyncSession, *, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        _SELECT_SESSION_CON_PLAN, {"tenant_id": tenant_id, "id": session_id}
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _load_session_or_404(
    session: AsyncSession, *, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> dict[str, Any]:
    row = await _load_session(session, tenant_id=tenant_id, session_id=session_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sesión de gym no encontrada."
        )
    return row


async def _insert_plan(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    fecha: date,
    plan: WorkoutPlan,
) -> uuid.UUID:
    plan_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO workout_plans (
                id, tenant_id, user_id, fecha, titulo, objetivo, duracion_min,
                ejercicios, imagen_url, imagen_file_id
            ) VALUES (
                :id, :tenant_id, :user_id, :fecha, :titulo, :objetivo, :duracion_min,
                CAST(:ejercicios AS jsonb), :imagen_url, :imagen_file_id
            )
            """
        ),
        {
            "id": plan_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "fecha": fecha,
            "titulo": plan.titulo,
            "objetivo": plan.objetivo,
            "duracion_min": plan.duracion_min,
            "ejercicios": json.dumps(plan.to_dict()["ejercicios"]),
            "imagen_url": plan.imagen_url,
            "imagen_file_id": plan.imagen_file_id,
        },
    )
    return plan_id


async def _insert_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
    workout: WorkoutSession,
) -> uuid.UUID:
    session_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO workout_sessions (
                id, tenant_id, user_id, plan_id, estado, started_at, ended_at, series
            ) VALUES (
                :id, :tenant_id, :user_id, :plan_id, :estado, :started_at, :ended_at,
                CAST(:series AS jsonb)
            )
            """
        ),
        {
            "id": session_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "plan_id": plan_id,
            "estado": workout.estado,
            "started_at": _parse_dt(workout.started_at),
            "ended_at": None,
            "series": json.dumps(workout.to_dict()["series"]),
        },
    )
    return session_id


async def _update_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    workout: WorkoutSession,
    ended_at: datetime | None,
) -> None:
    await session.execute(
        text(
            """
            UPDATE workout_sessions
            SET estado = :estado, started_at = :started_at, ended_at = :ended_at,
                series = CAST(:series AS jsonb), updated_at = now()
            WHERE tenant_id = :tenant_id AND id = :id
            """
        ),
        {
            "estado": workout.estado,
            "started_at": _parse_dt(workout.started_at),
            "ended_at": ended_at,
            "series": json.dumps(workout.to_dict()["series"]),
            "tenant_id": tenant_id,
            "id": session_id,
        },
    )


async def _insert_checkin(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    fecha: date,
    respuesta: str,
    session_id: uuid.UUID | None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO gym_checkins (id, tenant_id, user_id, fecha, respuesta, session_id)
            VALUES (:id, :tenant_id, :user_id, :fecha, :respuesta, :session_id)
            """
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "fecha": fecha,
            "respuesta": respuesta,
            "session_id": session_id,
        },
    )


async def _load_plan_today(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fecha: date
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT * FROM workout_plans
            WHERE tenant_id = :tenant_id AND user_id = :user_id AND fecha = :fecha
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"tenant_id": tenant_id, "user_id": user_id, "fecha": fecha},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _load_session_en_curso(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT ws.id, ws.tenant_id, ws.user_id, ws.plan_id, ws.estado,
                   ws.started_at, ws.ended_at, ws.series,
                   wp.fecha, wp.titulo, wp.objetivo, wp.duracion_min,
wp.ejercicios AS plan_ejercicios, wp.imagen_url, wp.imagen_file_id
    FROM workout_sessions ws
    JOIN workout_plans wp ON wp.id = ws.plan_id
    WHERE ws.tenant_id = :tenant_id AND ws.user_id = :user_id
      AND ws.estado IN ('planned', 'active', 'paused')
            ORDER BY ws.created_at DESC LIMIT 1
            """
        ),
        {"tenant_id": tenant_id, "user_id": user_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _load_historial(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, limite: int
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT ws.id, ws.tenant_id, ws.user_id, ws.plan_id, ws.estado,
                   ws.started_at, ws.ended_at, ws.series,
                   wp.fecha, wp.titulo, wp.objetivo, wp.duracion_min,
wp.ejercicios AS plan_ejercicios, wp.imagen_url, wp.imagen_file_id
    FROM workout_sessions ws
    JOIN workout_plans wp ON wp.id = ws.plan_id
    WHERE ws.tenant_id = :tenant_id AND ws.user_id = :user_id
      AND ws.estado = 'completed'
            ORDER BY ws.created_at DESC LIMIT :limite
            """
        ),
        {"tenant_id": tenant_id, "user_id": user_id, "limite": limite},
    )
    return [dict(row) for row in result.mappings().all()]


async def _historial_para_plan(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = await _load_historial(session, tenant_id=tenant_id, user_id=user_id, limite=5)
    return [_historial_entry(_session_from_row(row)) for row in rows]


# ---------------------------------------------------------------------------
# Generación de plan + collage (best-effort)
# ---------------------------------------------------------------------------


async def _generar_plan_gym(
    llm_router: Any,
    flags: dict[str, Any],
    historial: list[dict[str, Any]],
    objetivo: str | None,
) -> WorkoutPlan:
    async def completar(system: str, user: str) -> str:
        response = await llm_router.complete(
            _ALIAS_LLM,
            flags,
            CompletionRequest(
                model="",
                system=system,
                messages=[ChatMessage(role="user", content=user)],
                max_tokens=_MAX_TOKENS_PLAN,
            ),
        )
        return response.text

    return await generar_plan(completar, persona=None, historial=historial, objetivo=objetivo)


async def _generar_collage(ctx: Any, plan: WorkoutPlan) -> str | None:
    """Collage best-effort: devuelve el `file_id` del collage o `None`.

    El collage se descarga por el camino autenticado de siempre
    (`GET /v1/files/{id}/download` con el Bearer del tenant), no por una URL
    pública firmada: la URL pública depende de `PUBLIC_BASE_URL` y no
    funciona a través del edge `e.organization.org`. Si el tenant no tiene una
    credencial de imágenes REAL (solo `StubImageProvider`), o falla CUALQUIER
    paso, se devuelve `None` — nunca un placeholder presentado como collage
    real, y nunca una excepción que tumbe el check-in (ver docstring del
    módulo).
    """
    try:
        provider = await get_tenant_image_provider(ctx)
        if isinstance(provider, StubImageProvider):
            return None
        png_bytes = await provider.generate(prompt_collage(plan), size="1024x1024")
        file_id, _filename = await subir_archivo(
            ctx, data=png_bytes, filename="gym-collage.png", mime="image/png"
        )
        return str(file_id)
    except Exception:
        # best-effort por diseño: el plan funciona sin imagen (ver docstring).
        return None


async def _collage_en_segundo_plano(
    *,
    plan: WorkoutPlan,
    plan_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    settings: Settings,
    vault: Any,
    flags: dict[str, Any],
) -> None:
    """Genera el collage SIN bloquear la respuesta del check-in y actualiza
    `workout_plans.imagen_file_id` cuando el archivo queda listo.

    Usa una sesión de base de datos FRESCA (`edecan_db.session.get_session`)
    porque la del request ya se cerró cuando la respuesta volvió al cliente.
    Si falla cualquier paso, solo se loguea: la próxima vez que la app pida
    la sesión y no haya imagen, sigue sin ella (best-effort, nunca rompe).
    """
    try:
        from edecan_core.tools import ToolContext
        from edecan_db.session import get_session

        async with get_session(tenant_id) as s:
            ctx = ToolContext(
                tenant_id=tenant_id,
                user_id=user_id,
                session=s,
                settings=settings,
                llm=None,
                vault=vault,
                extras={"flags": flags},
            )
            file_id = await _generar_collage(ctx, plan)
            if not file_id:
                return
            await s.execute(
                text(
                    "UPDATE workout_plans SET imagen_file_id = :fid, updated_at = now() "
                    "WHERE id = :pid AND tenant_id = :tid"
                ),
                {"fid": file_id, "pid": str(plan_id), "tid": str(tenant_id)},
            )
    except Exception:
        logger.warning("gym: collage en segundo plano no se pudo generar.", exc_info=True)


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


@router.post("/checkin")
async def checkin(
    body: CheckinIn,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
    llm_router: Any = Depends(get_llm_router),
    vault: Any = Depends(get_vault),
) -> dict[str, Any]:
    try:
        va = decidir(body.respuesta)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    tenant_id = current_user.tenant_id
    user_id = current_user.user_id
    hoy = date.today()

    if not va:
        await _insert_checkin(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            fecha=hoy,
            respuesta="no",
            session_id=None,
        )
        return {
            "ok": True,
            "plan": None,
            "session": None,
            "mensaje": "Entendido, hoy toca descansar. Te pregunto de nuevo mañana.",
        }

    historial = await _historial_para_plan(session, tenant_id=tenant_id, user_id=user_id)
    plan = await _generar_plan_gym(
        llm_router, current_user.tenant.flags, historial=historial, objetivo=None
    )

    plan_id = await _insert_plan(
        session, tenant_id=tenant_id, user_id=user_id, fecha=hoy, plan=plan
    )
    # La sesión nace "planned" (el señor la inicia cuando toque en Entrenamiento),
    # NO "active": darle "Sí" a la card no debe arrancar el cronómetro.
    workout = WorkoutSession(plan)
    session_id = await _insert_session(
        session, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id, workout=workout
    )
    await _insert_checkin(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        fecha=hoy,
        respuesta="si",
        session_id=session_id,
    )

    # El collage se genera en segundo plano: puede tardar 30-60s con un
    # proveedor de imágenes real, y bloquear la respuesta hacía que el cliente
    # cortara con "request timed out" (y el plan pareciera no tener imagen).
    asyncio.create_task(
        _collage_en_segundo_plano(
            plan=plan,
            plan_id=plan_id,
            tenant_id=tenant_id,
            user_id=user_id,
            settings=settings,
            vault=vault,
            flags=current_user.tenant.flags,
        )
    )

    return {
        "ok": True,
        "plan": _plan_to_dict(plan),
        "session": _session_to_dict(workout, session_id),
        "mensaje": (
            f"¡Listo! Ya tienes tu plan de hoy. Cuando quieras empezar, toca "
            f"'Iniciar' en Entrenamiento. {_GUARDRAIL_SALUD}"
        ),
    }


@router.get("/plan/today")
async def plan_today(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    row = await _load_plan_today(
        session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        fecha=date.today(),
    )
    if row is None:
        return {"plan": None}
    plan = _plan_from_row(row)
    return {"plan": _plan_to_dict(plan)}


@router.get("/session")
async def session_activa(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    row = await _load_session_en_curso(
        session, tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    if row is None:
        return {"session": None}
    return {"session": _session_to_dict(_session_from_row(row), uuid.UUID(str(row["id"])))}


@router.post("/sessions/{session_id}/sets")
async def registrar_serie(
    session_id: uuid.UUID,
    body: SerieIn,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    row = await _load_session_or_404(
        session, tenant_id=current_user.tenant_id, session_id=session_id
    )
    workout = _session_from_row(row)
    try:
        workout.registrar_serie(body.ejercicio_idx, body.repeticiones, body.peso_kg)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    await _update_session(
        session,
        tenant_id=current_user.tenant_id,
        session_id=session_id,
        workout=workout,
        ended_at=row.get("ended_at"),
    )

    ejercicio = workout.plan.ejercicios[body.ejercicio_idx]
    series_hechas = workout.series_completadas(body.ejercicio_idx)
    restantes = ejercicio.series - series_hechas
    mensaje = (
        f"Serie {series_hechas} de {ejercicio.nombre} anotada. "
        f"Te quedan {restantes}. Descansa {ejercicio.descanso_seg}s."
    )
    return {"session": _session_to_dict(workout, session_id), "mensaje": mensaje}


@router.post("/sessions/{session_id}/complete")
async def completar_sesion(
    session_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    row = await _load_session_or_404(
        session, tenant_id=current_user.tenant_id, session_id=session_id
    )
    workout = _session_from_row(row)
    try:
        workout.terminar()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    ended_at = datetime.now(UTC)
    await _update_session(
        session,
        tenant_id=current_user.tenant_id,
        session_id=session_id,
        workout=workout,
        ended_at=ended_at,
    )
    return {
        "session": _session_to_dict(workout, session_id),
        "mensaje": "Entrenamiento completado. ¡Buen trabajo!",
    }


@router.post("/sessions/{session_id}/pause")
async def pausar_sesion(
    session_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    row = await _load_session_or_404(
        session, tenant_id=current_user.tenant_id, session_id=session_id
    )
    workout = _session_from_row(row)
    try:
        workout.pausar()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    await _update_session(
        session,
        tenant_id=current_user.tenant_id,
        session_id=session_id,
        workout=workout,
        ended_at=row.get("ended_at"),
    )
    return {"session": _session_to_dict(workout, session_id)}


@router.post("/sessions/{session_id}/resume")
async def reanudar_sesion(
    session_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    row = await _load_session_or_404(
        session, tenant_id=current_user.tenant_id, session_id=session_id
    )
    workout = _session_from_row(row)
    try:
        workout.reanudar()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    await _update_session(
        session,
        tenant_id=current_user.tenant_id,
        session_id=session_id,
        workout=workout,
        ended_at=row.get("ended_at"),
    )
    return {"session": _session_to_dict(workout, session_id)}


@router.post("/sessions/{session_id}/start")
async def iniciar_sesion(
    session_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """`planned → active`: arranca el cronómetro y fija `started_at`.

    La sesión nace `planned` al darle "Sí" a la card; el señor la inicia
    explícitamente desde Entrenamiento con el botón "Iniciar".
    """
    row = await _load_session_or_404(
        session, tenant_id=current_user.tenant_id, session_id=session_id
    )
    workout = _session_from_row(row)
    try:
        workout.iniciar()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    await _update_session(
        session,
        tenant_id=current_user.tenant_id,
        session_id=session_id,
        workout=workout,
        ended_at=row.get("ended_at"),
    )
    return {"session": _session_to_dict(workout, session_id)}


@router.get("/history")
async def historial(
    limit: int = Query(default=30, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    rows = await _load_historial(
        session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        limite=limit,
    )
    sessions = [_session_to_dict(_session_from_row(row), uuid.UUID(str(row["id"]))) for row in rows]
    return {"sessions": sessions}


# ---------------------------------------------------------------------------
# ToolContext mínimo para el proveedor de imágenes (mismo patrón que
# `content_studio._tool_context`).
# ---------------------------------------------------------------------------


def _tool_context(
    *, current_user: CurrentUser, session: AsyncSession, settings: Settings, vault: Any
) -> Any:
    from edecan_core.tools import ToolContext

    return ToolContext(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        session=session,
        settings=settings,
        llm=None,
        vault=vault,
        extras={"flags": current_user.tenant.flags},
    )
