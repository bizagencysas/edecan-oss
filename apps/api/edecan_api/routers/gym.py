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
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

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
from edecan_llm.task_router import azure_activo
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
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

_ALIAS_LLM = "profundo"
# El entrenador es Edecán: usar el modelo fuerte (con Azure activo, Sol ULTRA)
# y presupuesto amplio, porque el razonamiento xhigh se come tokens antes de
# responder. Un plan de gym de calidad vale la latencia.
_MAX_TOKENS_PLAN = 6000
# Resumen de cierre: presupuesto corto (best-effort, 2-3 oraciones).
_MAX_TOKENS_RESUMEN = 600
# Si el LLM tarda más que esto, el resumen se descarta y se cierra igual.
_TIMEOUT_RESUMEN_SEG = 20
# Reporte semanal: 2-4 oraciones sobre la semana terminada (best-effort).
_MAX_TOKENS_REPORTE = 800
_TIMEOUT_REPORTE_SEG = 20
# Coach de técnica (visión): feedback de 3-5 oraciones sobre la foto (best-effort).
_MAX_TOKENS_FEEDBACK = 800
_TIMEOUT_FEEDBACK_SEG = 20
# Coach de voz: UNA línea corta en español (best-effort; el TTS lo hace el cliente iOS).
_MAX_TOKENS_COACH = 120
_TIMEOUT_COACH_SEG = 15
# Semanas/días del reporte semanal: sesiones terminadas de los últimos 7 días.
_REPORTE_VENTANA_DIAS = 7
# Sobrecarga progresiva mínima para la meta de la próxima sesión.
_PESO_OBJETIVO_INCREMENTO_KG = 2.5

_GUARDRAIL_SALUD = "Ajusta el peso a tu nivel y calienta antes de empezar."

# ---------------------------------------------------------------------------
# Esquemas de entrada
# ---------------------------------------------------------------------------


class CheckinIn(BaseModel):
    """`{"respuesta": "si"|"no", "readiness": str|null}` — se valida contra
    `edecan_gym.decidir`.
    """
    # Se deja como `str` abierto (no `Literal`) para que `decidir` sea la única
    # fuente de verdad del vocabulario (acepta "si"/"sí"/"yes"/"no"/"nope" y
    # lanza `ValueError` en otro caso, que este router mapea a 422).
    respuesta: str = Field(min_length=1, max_length=16)
    # Estado de recuperación auto-reportado (p. ej. "dormí mal", "estoy
    # recuperado"): ajusta el volumen/intensidad del plan de hoy.
    readiness: str | None = None


class FormAnalizarIn(BaseModel):
    """Body de `POST /v1/gym/form/analizar`: foto base64 del ejercicio + nombre,
    o frames base64 de un vídeo (máx. 6). Al menos uno de los dos es obligatorio.
    """
    imagen_b64: str | None = None
    frames_b64: list[str] | None = None
    ejercicio: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _validar_imagen_o_frames(self) -> FormAnalizarIn:
        if not self.imagen_b64 and not self.frames_b64:
            raise ValueError("Se requiere `imagen_b64` o `frames_b64`.")
        if self.frames_b64 is not None and len(self.frames_b64) > 6:
            raise ValueError("`frames_b64` admite como máximo 6 frames.")
        return self


class CoachVozIn(BaseModel):
    """Body de `POST /v1/gym/coach_voz`: tipo de línea + contexto opcional."""
    tipo: Literal["descanso_termino", "serie_completada", "sesion_inicio", "motivacion"]
    ejercicio: str | None = None
    contexto: str | None = None


class SerieIn(BaseModel):
    """Body de `POST /v1/gym/sessions/{id}/sets`."""
    ejercicio_idx: int = Field(ge=0)
    repeticiones: int = Field(gt=0)
    peso_kg: float | None = None


class SwapEjercicioIn(BaseModel):
    """Body de `POST /v1/gym/plan/{id}/swap-ejercicio`."""
    ejercicio_idx: int = Field(ge=0)
    # Nombre libre del dueño: «press banca», «pecho», «no sé, algo de espalda».
    nombre: str = Field(min_length=2, max_length=160)
    # Si True, devuelve SOLO la lista de candidatos (sin aplicar el swap).
    solo_opciones: bool = False


_SYSTEM_PLAN_GYM = (
    "Eres un instructor de gimnasio profesional. Diseñas y ajustas planes de "
    "fuerza e hipertrofia, en español. No emites diagnósticos médicos; ante "
    "cualquier molestia remite al usuario a su médico. Responde ÚNICAMENTE con "
    "el JSON solicitado, sin texto adicional."
)


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


def _meta_desde_previo(previo: list[dict] | None) -> list[dict]:
    """Meta de sobrecarga progresiva para la próxima sesión, por ejercicio.

    A partir de `previo` (mejor peso/reps de la última sesión terminada):
    peso_objetivo = peso + 2.5 kg; repeticiones_objetivo = reps + 2 para series
    de fuerza (reps ≤ 8) y reps + 1 para reps > 8. `meta` SIEMPRE está presente
    en el contrato (lista; vacía sin previo). Ejercicios sin peso ni reps salen
    con ambos objetivos `null`.
    """
    meta: list[dict] = []
    for entrada in previo or []:
        try:
            idx = int(entrada["idx"])
        except (KeyError, TypeError, ValueError):
            continue
        peso = entrada.get("peso_kg")
        peso_objetivo = (
            round(float(peso) + _PESO_OBJETIVO_INCREMENTO_KG, 1)
            if isinstance(peso, (int, float))
            else None
        )
        reps = entrada.get("repeticiones")
        if type(reps) is not int:
            repeticiones_objetivo = None
        else:
            repeticiones_objetivo = reps + (2 if reps <= 8 else 1)
        meta.append(
            {
                "idx": idx,
                "peso_objetivo": peso_objetivo,
                "repeticiones_objetivo": repeticiones_objetivo,
            }
        )
    return meta


def _session_to_dict(
    session: WorkoutSession,
    session_id: uuid.UUID,
    previo: list[dict] | None = None,
) -> dict[str, Any]:
    data = session.to_dict()
    resumen = session.resumen()
    return {
        "id": str(session_id),
        "estado": data["estado"],
        "plan": data["plan"],
        "started_at": data["started_at"],
        "series": data["series"],
        "progreso": {"ejercicios": resumen["progreso"]},
        # Mejor peso de la última sesión completada, por índice de ejercicio
        # (lista; vacía si no hay previa). Contrato fijo con iOS.
        "previo": previo or [],
        # Meta de sobrecarga progresiva derivada de `previo`. Lista, vacía sin
        # previa. Contrato fijo con iOS.
        "meta": _meta_desde_previo(previo),
    }


def _fecha_previo(entrada: dict[str, Any]) -> str | None:
    """`YYYY-MM-DD` de la sesión: `ended_at` si existe, si no `fecha`."""
    ended = entrada.get("ended_at")
    if isinstance(ended, str) and ended:
        try:
            return datetime.fromisoformat(ended).date().isoformat()
        except ValueError:
            pass
    if hasattr(ended, "date"):
        return ended.date().isoformat()
    fecha = entrada.get("fecha")
    if isinstance(fecha, str) and fecha:
        return fecha[:10]
    if hasattr(fecha, "isoformat"):
        return fecha.isoformat()[:10]
    return None


def _previo_desde_historial(historial: list[dict]) -> list[dict]:
    """Mejor peso (máximo) y sus repeticiones por índice de ejercicio.

    Usa la ÚLTIMA sesión terminada (`estado in {"completed", "ended"}`) del
    historial (que llega de la más reciente a la más antigua). Sin previa,
    devuelve lista vacía — `previo` SIEMPRE está presente en el contrato.
    """
    terminadas = [h for h in historial if h.get("estado") in ("completed", "ended")]
    if not terminadas:
        return []
    previa = terminadas[0]

    idxes: set[int] = set()
    plan = previa.get("plan")
    ejercicios = plan.get("ejercicios") if isinstance(plan, dict) else None
    if ejercicios is None:
        ejercicios = previa.get("ejercicios")
    if isinstance(ejercicios, list):
        idxes.update(range(len(ejercicios)))
    mejor: dict[int, tuple[float | None, int | None]] = {}
    for serie in previa.get("series") or []:
        if not isinstance(serie, dict):
            continue
        try:
            idx = int(serie.get("ejercicio_idx"))
        except (TypeError, ValueError):
            continue
        peso = serie.get("peso_kg")
        if not isinstance(peso, (int, float)):
            peso = None
        reps = serie.get("repeticiones")
        if type(reps) is not int:
            reps = None
        actual = mejor.get(idx)
        if actual is None or (peso is not None and (actual[0] is None or peso > actual[0])):
            mejor[idx] = (peso, reps)
        idxes.add(idx)

    fecha = _fecha_previo(previa)
    return [
        {
            "idx": idx,
            "peso_kg": (mejor.get(idx) or (None, None))[0],
            "repeticiones": (mejor.get(idx) or (None, None))[1],
            "fecha": fecha,
        }
        for idx in sorted(idxes)
    ]


def _inicio_semana(d: date) -> date:
    """Lunes de la semana (lunes a domingo) a la que pertenece `d`."""
    return d - timedelta(days=d.weekday())


def _streak_semanas(rows: list[dict[str, Any]]) -> int:
    """Semanas consecutivas (lunes a domingo) con al menos una sesión terminada.

    Cuenta hacia atrás desde la semana actual si tiene sesiones; si no, desde
    la semana más reciente con sesiones. Usa las `ended_at` de las filas.
    """
    semanas: set[date] = set()
    for row in rows:
        ended = row.get("ended_at")
        if hasattr(ended, "date"):
            d = ended.date()
        elif isinstance(ended, str) and ended:
            try:
                d = datetime.fromisoformat(ended).date()
            except ValueError:
                continue
        else:
            continue
        semanas.add(_inicio_semana(d))
    if not semanas:
        return 0

    referencia = _inicio_semana(date.today())
    if referencia not in semanas:
        pasadas = [s for s in semanas if s < referencia]
        if not pasadas:
            return 0
        referencia = max(pasadas)

    racha = 0
    semana = referencia
    while semana in semanas:
        racha += 1
        semana -= timedelta(days=7)
    return racha


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
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    limite: int,
    desde: date | None = None,
) -> list[dict[str, Any]]:
    """Sesiones terminadas del usuario, de la más reciente a la más antigua.

    `desde` opcional acota por `ws.ended_at >= :desde` (lo usa el reporte
    semanal); sin él, el comportamiento es el de siempre.
    """
    params: dict[str, Any] = {"tenant_id": tenant_id, "user_id": user_id, "limite": limite}
    filtro_fecha = ""
    if desde is not None:
        filtro_fecha = " AND ws.ended_at >= :desde"
        params["desde"] = desde
    result = await session.execute(
        text(
            f"""
            SELECT ws.id, ws.tenant_id, ws.user_id, ws.plan_id, ws.estado,
                   ws.started_at, ws.ended_at, ws.series,
                   wp.fecha, wp.titulo, wp.objetivo, wp.duracion_min,
wp.ejercicios AS plan_ejercicios, wp.imagen_url, wp.imagen_file_id
    FROM workout_sessions ws
    JOIN workout_plans wp ON wp.id = ws.plan_id
    WHERE ws.tenant_id = :tenant_id AND ws.user_id = :user_id
      AND ws.estado = 'completed'
      {filtro_fecha}
            ORDER BY ws.created_at DESC LIMIT :limite
            """
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()]


async def _historial_para_plan(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = await _load_historial(session, tenant_id=tenant_id, user_id=user_id, limite=5)
    entradas: list[dict[str, Any]] = []
    for row in rows:
        entrada = _historial_entry(_session_from_row(row))
        # El estado del dominio no serializa `ended_at`/`fecha`: los sumamos
        # aquí porque `previo` (mejor peso por ejercicio) los necesita.
        entrada["ended_at"] = _iso(row.get("ended_at"))
        entrada["fecha"] = _iso(row.get("fecha"))
        entradas.append(entrada)
    return entradas


async def _previo_actual(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[dict]:
    """`previo` (mejor peso por ejercicio de la última sesión terminada)."""
    historial = await _historial_para_plan(session, tenant_id=tenant_id, user_id=user_id)
    return _previo_desde_historial(historial)


# ---------------------------------------------------------------------------
# Generación de plan + collage (best-effort)
# ---------------------------------------------------------------------------


async def _generar_plan_gym(
    llm_router: Any,
    flags: dict[str, Any],
    historial: list[dict[str, Any]],
    objetivo: str | None,
    readiness: str | None = None,
) -> WorkoutPlan:
    async def completar(system: str, user: str) -> str:
        kwargs: dict[str, Any] = {"max_tokens": _MAX_TOKENS_PLAN}
        if azure_activo():
            # El entrenador corre en modo ULTRA cuando el proveedor es Azure.
            kwargs["reasoning_effort"] = "xhigh"
        response = await llm_router.complete(
            _ALIAS_LLM,
            flags,
            CompletionRequest(
                model="",
                system=system,
                messages=[ChatMessage(role="user", content=user)],
                **kwargs,
            ),
        )
        return response.text

    return await generar_plan(
        completar,
        persona=None,
        historial=historial,
        objetivo=objetivo,
        readiness=readiness,
    )


async def _resumen_sesion(
    llm_router: Any,
    flags: dict[str, Any],
    workout: WorkoutSession,
    *,
    ended_at: datetime,
) -> str | None:
    """Resumen en español (best-effort) de la sesión terminada.

    2-3 oraciones + recomendación para la próxima sesión. NUNCA rompe el cierre
    de la sesión: si el LLM falla, tarda o viene `None`, devuelve `None` (el
    endpoint responde `"resumen": null`). Corre bajo el mismo rate limit del
    endpoint y usa el mismo `llm_router`.
    """
    try:
        if llm_router is None or not workout.series:
            return None
        nombres = {i: e.nombre for i, e in enumerate(workout.plan.ejercicios)}
        lineas_series = []
        for serie in workout.series:
            nombre = nombres.get(serie.ejercicio_idx, f"ejercicio {serie.ejercicio_idx}")
            peso = f"{serie.peso_kg} kg" if serie.peso_kg is not None else "sin peso"
            lineas_series.append(f"- {nombre}: {serie.repeticiones} repeticiones @ {peso}")

        duracion: str | None = None
        if workout.started_at:
            try:
                inicio = datetime.fromisoformat(workout.started_at)
                if inicio.tzinfo is None:
                    inicio = inicio.replace(tzinfo=UTC)
                duracion = str(int((ended_at - inicio).total_seconds() // 60)) + " minutos"
            except ValueError:
                duracion = None

        sistema = (
            "Eres el entrenador personal de Edecán. Escribe un resumen breve en "
            "español de la sesión de entrenamiento terminada: 2-3 oraciones que "
            "describan el trabajo realizado y una recomendación concreta para la "
            "próxima sesión. No inventes datos que no estén en la información dada. "
            "Responde ÚNICAMENTE con el texto del resumen."
        )
        partes = [
            f"Título del plan: {workout.plan.titulo}",
            f"Duración: {duracion or 'no registrada'}",
            "Series registradas:",
            *lineas_series,
        ]
        kwargs: dict[str, Any] = {"max_tokens": _MAX_TOKENS_RESUMEN}
        if azure_activo():
            # Mismo criterio que el plan: modo ULTRA con el proveedor Azure.
            kwargs["reasoning_effort"] = "xhigh"
        response = await asyncio.wait_for(
            llm_router.complete(
                _ALIAS_LLM,
                flags,
                CompletionRequest(
                    model="",
                    system=sistema,
                    messages=[ChatMessage(role="user", content="\n".join(partes))],
                    **kwargs,
                ),
            ),
            timeout=_TIMEOUT_RESUMEN_SEG,
        )
        texto = (response.text or "").strip()
        return texto or None
    except Exception:
        # best-effort por diseño: el cierre de la sesión nunca depende del resumen.
        logger.warning("gym: resumen de sesión no se pudo generar.", exc_info=True)
        return None


def _sesion_a_lineas_reporte(row: dict[str, Any]) -> list[str]:
    """Líneas texto (ejercicio / series / reps / peso) de una sesión terminada.

    Alimenta el prompt del reporte semanal: por cada serie registrada, una
    línea con el nombre del ejercicio, sus repeticiones y su peso.
    """
    plan = _plan_from_row(row)
    nombres = {i: e.nombre for i, e in enumerate(plan.ejercicios)}
    lineas: list[str] = []
    for serie in _from_jsonb(row.get("series")):
        if not isinstance(serie, dict):
            continue
        try:
            idx = int(serie.get("ejercicio_idx"))
        except (TypeError, ValueError):
            continue
        nombre = nombres.get(idx, f"ejercicio {idx}")
        reps = serie.get("repeticiones")
        texto = f"- {nombre}: {reps} repeticiones"
        peso = serie.get("peso_kg")
        if isinstance(peso, (int, float)):
            texto += f" @ {peso} kg"
        lineas.append(texto)
    return lineas


async def _generar_collage(ctx: Any, plan: WorkoutPlan) -> str | None:
    """Collage best-effort: devuelve el `file_id` del collage o `None`.

    El collage se descarga por el camino autenticado de siempre
    (`GET /v1/files/{id}/download` con el Bearer del tenant), no por una URL
    pública firmada: la URL pública depende de `PUBLIC_BASE_URL` y no
    funciona a través del edge configurado. Si el tenant no tiene una
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

    # IDEMPOTENCIA del check-in: la push/card de "¿Vas a ir al gym?" NO se
    # borra del teléfono al tocarla — un segundo "Sí" (hoy mismo) NO debe
    # generar otro plan y otra sesión. Si ya hay check-in "si" de hoy con
    # sesión, se devuelve ESA sesión/plan y se acaba (mismo contrato que el
    # camino normal).
    previa_row = (
        await session.execute(
            text(
                "SELECT session_id FROM gym_checkins "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id "
                "AND fecha = :hoy AND respuesta = 'si' AND session_id IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"tenant_id": str(tenant_id), "user_id": str(user_id), "hoy": hoy},
        )
    ).mappings().first()
    if previa_row and previa_row["session_id"]:
        fila = await _load_session(
            session, tenant_id=tenant_id, session_id=previa_row["session_id"]
        )
        if fila is not None:
            workout_previo = _session_from_row(fila)
            return {
                "ok": True,
                "plan": _plan_to_dict(_plan_from_row(fila)),
                "session": _session_to_dict(
                    workout_previo,
                    uuid.UUID(str(fila["id"])),
                    previo=_previo_desde_historial(historial),
                ),
                "mensaje": (
                    "Ya te había registrado el check-in de hoy: tu plan sigue "
                    f"igual. Cuando quieras, toca 'Iniciar' en Entrenamiento. {_GUARDRAIL_SALUD}"
                ),
            }

    # Continuidad de objetivo: el siguiente plan persigue el MISMO objetivo que
    # el último (hipertrofia, fuerza, etc.) salvo que el check-in lo cambie. Sin
    # historial, el entrenador decide solo.
    objetivo_continuidad = None
    if historial:
        plan_previo = historial[-1].get("plan")
        if isinstance(plan_previo, dict):
            objetivo_continuidad = str(plan_previo.get("objetivo") or "").strip() or None
    plan = await _generar_plan_gym(
        llm_router,
        current_user.tenant.flags,
        historial=historial,
        objetivo=objetivo_continuidad,
        readiness=body.readiness,
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
        "session": _session_to_dict(
            workout, session_id, previo=_previo_desde_historial(historial)
        ),
        "mensaje": (
            f"¡Listo! Ya tienes tu plan de hoy. Cuando quieras empezar, toca "
            f"'Iniciar' en Entrenamiento. {_GUARDRAIL_SALUD}"
        ),
    }


@router.post("/plan/swap-ejercicio")
async def swap_ejercicio(
    body: SwapEjercicioIn,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    llm_router: Any = Depends(get_llm_router),
) -> dict[str, Any]:
    """Cambia UN ejercicio del plan de hoy por otro que pida el dueño.

    El dueño escribe el nombre como le salga («press banca», «pecho», «algo
    de espalda, no sé el nombre técnico») — la IA elige el ejercicio correcto
    y devuelve también alternativas para que la app ofrezca lista. Con
    `solo_opciones=true` solo devuelve candidatos (la UI los lista para que
    el dueño escoja).
    """
    from edecan_schemas import ChatMessage

    fila = await _load_plan_today(
        session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        fecha=date.today(),
    )
    if fila is None:
        raise HTTPException(status_code=404, detail="No hay plan de hoy.")
    plan = _plan_from_row(fila)
    if body.ejercicio_idx >= len(plan.ejercicios):
        raise HTTPException(status_code=422, detail="Ese número de ejercicio no existe en el plan.")
    actual = plan.ejercicios[body.ejercicio_idx]

    async def completar(system: str, user: str) -> str:
        kwargs: dict[str, Any] = {"max_tokens": 900}
        if azure_activo():
            kwargs["reasoning_effort"] = "high"
        response = await llm_router.complete(
            _ALIAS_LLM,
            current_user.tenant.flags,
            CompletionRequest(
                model=_ALIAS_LLM,
                system=system,
                messages=[ChatMessage(role="user", content=user)],
                **kwargs,
            ),
        )
        return response.text

    prompt = (
        f"Plan de hoy: «{plan.titulo}» (objetivo: {plan.objetivo}).\n"
        f"El ejercicio #{body.ejercicio_idx + 1} actual es: {actual.nombre} "
        f"({actual.musculo}, {actual.series} series x {actual.repeticiones} reps).\n"
        f"El dueño pide cambiarlo por: «{body.nombre}».\n\n"
        "El nombre puede ser impreciso o no técnico: interpreta qué ejercicio o "
        "músculo quiere y elige el MEJOR reemplazo (mismo patrón de movimiento o "
        "músculo objetivo, equipment similar si es posible). Devuelve ÚNICAMENTE "
        "JSON válido con esta forma:\n"
        '{"ejercicio": {"nombre": "...", "musculo": "...", "series": N, '
        '"repeticiones": "N-M o N", "descanso_seg": N, "notas": "cómo hacerlo o '
        'por qué encaja"}, "alternativas": [2-3 ejercicios con la misma forma], '
        '"interpreto": "lo que entendiste del pedido en una frase"}\n'
        "Las alternativas sirven para que el dueño escoja otra opción de la lista."
    )
    raw = await completar(_SYSTEM_PLAN_GYM, prompt)
    try:
        inicio, fin = raw.index("{"), raw.rindex("}") + 1
        datos = json.loads(raw[inicio:fin])
        nuevo = Ejercicio.from_dict(datos["ejercicio"])
        alternativas = [
            Ejercicio.from_dict(a) for a in (datos.get("alternativas") or [])[:3]
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=502,
            detail="El entrenador no pudo interpretar el cambio; prueba con otro nombre.",
        ) from exc

    if body.solo_opciones:
        return {
            "ok": True,
            "interpreto": str(datos.get("interpreto") or ""),
            "ejercicio_actual": actual.to_dict(),
            "ejercicio_propuesto": nuevo.to_dict(),
            "alternativas": [a.to_dict() for a in alternativas],
        }

    plan.ejercicios[body.ejercicio_idx] = nuevo
    session.execute(
        text(
            """
            UPDATE workout_plans
            SET ejercicios = CAST(:ejercicios AS jsonb), updated_at = now()
            WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tenant_id AS uuid)
            """
        ),
        {
            "ejercicios": json.dumps(
                [e.to_dict() for e in plan.ejercicios], ensure_ascii=False
            ),
            "id": str(fila["id"]),
            "tenant_id": str(current_user.tenant_id),
        },
    )
    await session.commit()
    return {
        "ok": True,
        "interpreto": str(datos.get("interpreto") or ""),
        "plan": _plan_to_dict(plan),
        "ejercicio_nuevo": nuevo.to_dict(),
        "alternativas": [a.to_dict() for a in alternativas],
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
    previo = await _previo_actual(
        session, tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    return {
        "session": _session_to_dict(
            _session_from_row(row), uuid.UUID(str(row["id"])), previo=previo
        )
    }


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
        session, tenant_id=current_user.tenant_id, session_id=session_id, workout=workout,
        ended_at=row.get("ended_at"),
    )

    previo = await _previo_actual(
        session, tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    ejercicio = workout.plan.ejercicios[body.ejercicio_idx]
    series_hechas = workout.series_completadas(body.ejercicio_idx)
    restantes = ejercicio.series - series_hechas
    mensaje = (
        f"Serie {series_hechas} de {ejercicio.nombre} anotada. "
        f"Te quedan {restantes}. Descansa {ejercicio.descanso_seg}s."
    )
    return {
        "session": _session_to_dict(workout, session_id, previo=previo),
        "mensaje": mensaje,
    }


@router.post("/sessions/{session_id}/complete")
async def completar_sesion(
    session_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    llm_router: Any = Depends(get_llm_router),
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
        session, tenant_id=current_user.tenant_id, session_id=session_id, workout=workout,
        ended_at=ended_at,
    )

    previo = await _previo_actual(
        session, tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    # Resumen con IA: best-effort. Si falla o tarda, `resumen` es `null` y el
    # cierre de la sesión ya quedó persistido arriba (nunca se rompe).
    resumen = await _resumen_sesion(
        llm_router,
        current_user.tenant.flags,
        workout,
        ended_at=ended_at,
    )
    return {
        "session": _session_to_dict(workout, session_id, previo=previo),
        "mensaje": "Entrenamiento completado. ¡Buen trabajo!",
        "resumen": resumen,
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
        session, tenant_id=current_user.tenant_id, session_id=session_id, workout=workout,
        ended_at=row.get("ended_at"),
    )
    previo = await _previo_actual(
        session, tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    return {"session": _session_to_dict(workout, session_id, previo=previo)}


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
        session, tenant_id=current_user.tenant_id, session_id=session_id, workout=workout,
        ended_at=row.get("ended_at"),
    )
    previo = await _previo_actual(
        session, tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    return {"session": _session_to_dict(workout, session_id, previo=previo)}


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
        session, tenant_id=current_user.tenant_id, session_id=session_id, workout=workout,
        ended_at=row.get("ended_at"),
    )
    previo = await _previo_actual(
        session, tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    return {"session": _session_to_dict(workout, session_id, previo=previo)}


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
    sessions = [
        _session_to_dict(_session_from_row(row), uuid.UUID(str(row["id"]))) for row in rows
    ]
    return {"sessions": sessions, "streak": _streak_semanas(rows)}


@router.get("/reporte_semanal")
async def reporte_semanal(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    llm_router: Any = Depends(get_llm_router),
) -> dict[str, Any]:
    """Resumen semanal con IA (best-effort): `{"reporte": str|null}`.

    Carga las sesiones terminadas de los últimos 7 días y le pide al LLM (mismo
    patrón que el plan: `_ALIAS_LLM`, `reasoning_effort="xhigh"` con Azure)
    un resumen en español de 2-4 oraciones: progreso (mencionando pesos/series
    de los ejercicios que más cambiaron), tendencia, si conviene una semana de
    deload o ajustar el objetivo, y una recomendación concreta para la próxima
    semana. Si el LLM falla o tarda, devuelve `{"reporte": null}`.
    """
    try:
        desde = date.today() - timedelta(days=_REPORTE_VENTANA_DIAS)
        rows = await _load_historial(
            session,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            limite=100,
            desde=desde,
        )
        if llm_router is None:
            return {"reporte": None}

        sistema = (
            "Eres el entrenador personal de Edecán. Escribe un resumen semanal "
            "en español de 2-4 oraciones a partir de las sesiones de "
            "entrenamiento de los últimos 7 días: el progreso (menciona pesos y "
            "series de los ejercicios que más cambiaron), la tendencia, si "
            "conviene una semana de deload o ajustar el objetivo, y una "
            "recomendación concreta para la próxima semana. Si no hay sesiones "
            "registradas, dilo y sugiere retomar de forma gradual. No inventes "
            "datos que no estén en la información dada. Responde ÚNICAMENTE con "
            "el texto del resumen."
        )
        bloques: list[str] = []
        for row in rows:
            titulo = row.get("titulo")
            ended = _iso(row.get("ended_at"))
            bloques.append(f"Sesión: {titulo} ({ended or 'fecha desconocida'})")
            bloques.extend(_sesion_a_lineas_reporte(row))
        if not bloques:
            bloques.append("No hubo sesiones terminadas en los últimos 7 días.")

        kwargs: dict[str, Any] = {"max_tokens": _MAX_TOKENS_REPORTE}
        if azure_activo():
            kwargs["reasoning_effort"] = "xhigh"
        response = await asyncio.wait_for(
            llm_router.complete(
                _ALIAS_LLM,
                current_user.tenant.flags,
                CompletionRequest(
                    model="",
                    system=sistema,
                    messages=[ChatMessage(role="user", content="\n".join(bloques))],
                    **kwargs,
                ),
            ),
            timeout=_TIMEOUT_REPORTE_SEG,
        )
        texto = (response.text or "").strip()
        return {"reporte": texto or None}
    except Exception:
        # best-effort por diseño: el reporte nunca debe tumbar el endpoint.
        logger.warning("gym: reporte semanal no se pudo generar.", exc_info=True)
        return {"reporte": None}


@router.post("/form/analizar")
async def analizar_forma(
    body: FormAnalizarIn,
    current_user: CurrentUser = Depends(get_current_user),
    llm_router: Any = Depends(get_llm_router),
) -> dict[str, Any]:
    """Coach de técnica con visión (best-effort): `{"feedback": str|null}`.

    Envía la foto del ejercicio (`imagen_b64`) o los frames de un vídeo
    (`frames_b64`, máx. 6) al LLM con visión (Sol en Azure) y devuelve 3-5
    oraciones en español con correcciones accionables de postura, rango de
    movimiento, alineación y tempo. Con frames, TODOS viajan en un único
    mensaje multimodal multi-imagen. Si el LLM falla o tarda, `{"feedback":
    null}`.
    """
    try:
        if llm_router is None:
            return {"feedback": None}

        sistema = (
            "Eres un coach de técnica de gimnasio profesional. Analizas fotos de "
            "ejercicios y das correcciones accionables, en español. No emites "
            "diagnósticos médicos; ante cualquier molestia remite al usuario a "
            "su médico."
        )
        if body.frames_b64:
            prompt = (
                f"Analiza la técnica del ejercicio '{body.ejercicio}' a lo largo de "
                "estos frames del video. Evalúa la postura, el rango de movimiento, "
                "la alineación y el tempo del movimiento, y da 1-3 correcciones "
                "accionables en español, en 3-5 oraciones. No inventes detalles que "
                "no se vean en las imágenes."
            )
            imagenes = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{frame}"},
                }
                for frame in body.frames_b64
            ]
        else:
            prompt = (
                f"Analiza la técnica del ejercicio '{body.ejercicio}' en esta foto. "
                "Evalúa la postura, el rango de movimiento y la alineación, y da 1-3 "
                "correcciones accionables en español, en 3-5 oraciones. No inventes "
                "detalles que no se vean en la imagen."
            )
            imagenes = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{body.imagen_b64}"},
                }
            ]

        request = CompletionRequest(
            model="",
            system=sistema,
            messages=[
                ChatMessage(
                    role="user",
                    content=[{"type": "text", "text": prompt}, *imagenes],
                )
            ],
            max_tokens=_MAX_TOKENS_FEEDBACK,
        )
        if azure_activo():
            request.reasoning_effort = "xhigh"
        response = await asyncio.wait_for(
            llm_router.complete(_ALIAS_LLM, current_user.tenant.flags, request),
            timeout=_TIMEOUT_FEEDBACK_SEG,
        )
        texto = (response.text or "").strip()
        return {"feedback": texto or None}
    except Exception:
        # best-effort por diseño: el feedback nunca debe tumbar el endpoint.
        logger.warning("gym: feedback de técnica no se pudo generar.", exc_info=True)
        return {"feedback": None}


@router.post("/coach_voz")
async def coach_voz(
    body: CoachVozIn,
    current_user: CurrentUser = Depends(get_current_user),
    llm_router: Any = Depends(get_llm_router),
) -> dict[str, Any]:
    """Coach de voz (best-effort): `{"linea": str|null}`.

    Genera UNA línea corta de coach en español (máx. ~25 palabras, directa, en
    segunda persona/tú) según el `tipo`. El TTS lo hace el cliente iOS, no este
    endpoint. Si el LLM falla o tarda, `{"linea": null}`.
    """
    try:
        if llm_router is None:
            return {"linea": None}

        ejercicio = body.ejercicio or ""
        contexto = body.contexto or ""

        sistema = (
            "Eres el coach de voz de Edecán, un entrenador personal cercano y "
            "directo. Escribe UNA sola línea corta en español, de máximo ~25 "
            "palabras, en segunda persona (tú), sin emojis ni símbolos que "
            "rompan un sintetizador de voz. Responde ÚNICAMENTE con esa línea."
        )

        prompt_por_tipo = {
            "descanso_termino": (
                "El descanso terminó. Motiva al usuario a seguir, "
                f"con la siguiente serie{(' de ' + ejercicio) if ejercicio else ''}."
            ),
            "serie_completada": (
                f"El usuario completó una serie{(' de ' + ejercicio) if ejercicio else ''}. "
                "Dale un elogio breve y recuérdale un punto de técnica."
            ),
            "sesion_inicio": "El usuario va a empezar su entrenamiento. Dale energía de arranque.",
            "motivacion": "Da una frase motivacional de entrenador, corta y directa.",
        }
        prompt = prompt_por_tipo[body.tipo]
        if contexto:
            prompt += f" Contexto: {contexto}."

        kwargs: dict[str, Any] = {"max_tokens": _MAX_TOKENS_COACH}
        if azure_activo():
            # Mismo criterio que el resto: modo ULTRA con el proveedor Azure.
            kwargs["reasoning_effort"] = "xhigh"
        response = await asyncio.wait_for(
            llm_router.complete(
                _ALIAS_LLM,
                current_user.tenant.flags,
                CompletionRequest(
                    model="",
                    system=sistema,
                    messages=[ChatMessage(role="user", content=prompt)],
                    **kwargs,
                ),
            ),
            timeout=_TIMEOUT_COACH_SEG,
        )
        linea = (response.text or "").strip()
        return {"linea": linea or None}
    except Exception:
        # best-effort por diseño: la línea nunca debe tumbar el endpoint.
        logger.warning("gym: línea de coach de voz no se pudo generar.", exc_info=True)
        return {"linea": None}


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
