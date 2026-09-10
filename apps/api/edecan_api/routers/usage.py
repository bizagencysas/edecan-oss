"""`GET /v1/usage` — uso del mes vs límites del plan (ARCHITECTURE.md §10.12, §10.13)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from edecan_schemas.plans import (
    BOOL_FLAGS,
    LIMIT_MESSAGES_PER_DAY,
    LIMIT_PHONE_NUMBERS,
    LIMIT_SEATS,
    LIMIT_STORAGE_MB,
    LIMIT_VOICE_MINUTES_MONTH,
    UNLIMITED,
)
from fastapi import APIRouter, Depends, HTTPException, status

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_repo, rate_limit
from edecan_api.repo import Repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/usage", tags=["usage"], dependencies=[Depends(rate_limit)])

_PERIODOS_VALIDOS = {"7", "30", "todo"}


def _since_para_periodo(periodo: str) -> datetime | None:
    """`None` para "todo"; `now - N días` para "7"/"30" (mismo cálculo que
    `get_usage_por_modelo`)."""
    if periodo == "todo":
        return None
    return datetime.now(UTC) - timedelta(days=int(periodo))


def _alertar_presupuesto_diario(filas: list[dict[str, Any]], umbral_usd: float) -> None:
    """WARNING de log si el ÚLTIMO DÍA COMPLETO supera `USAGE_ALERT_USD_PER_DAY`.

    "Completo" lo decide la BASE: `dia_completo` viene del mismo `created_at::date
    < CURRENT_DATE` con el que se agrupa (ver `SqlRepo.usage_llm_diario_desde`),
    así que el criterio usa la zona horaria del servidor y no hay un reloj
    Python que pueda discrepar. Deliberadamente SIN estado (este router no
    guarda nada entre requests): el warning se emite en cada request de
    `/diario` que encuentra el último día completo sobre el umbral.
    Aceptable: es un log de servidor, no una notificación al dueño — la
    frecuencia la marca su propio polling del panel.
    """
    for fila in sorted(filas, key=lambda f: f["dia"], reverse=True):
        if not fila.get("dia_completo"):
            continue
        costo = float(fila.get("costo_usd") or 0)
        if costo > umbral_usd:
            dia = fila["dia"]
            logger.warning(
                "Alerta de presupuesto LLM: el costo diario supera el umbral "
                "(dia=%s, costo_usd=%.4f, umbral_usd=%.4f).",
                dia.isoformat() if hasattr(dia, "isoformat") else str(dia),
                costo,
                umbral_usd,
            )
        return


@router.get("/modelos")
async def get_usage_por_modelo(
    periodo: str = "30",
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """`GET /v1/usage/modelos?periodo=7|30|todo` — tokens de entrada/salida,
    llamadas y costo por MODELO (Sol, Terra, Luna, Astra, Workers AI…).

    El dueño ve el gasto REAL de cada LLM desde Perfil (iOS)."""
    if periodo not in _PERIODOS_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"periodo inválido: {periodo!r} (usa 7, 30 o todo).",
        )
    since: datetime | None = None
    if periodo != "todo":
        since = datetime.now(UTC) - timedelta(days=int(periodo))
    filas = await repo.usage_llm_por_modelo_desde(
        tenant_id=current_user.tenant_id, since=since
    )
    modelos = [
        {
            "model": str(fila["model"]),
            "llamadas": int(fila["llamadas"]),
            "tokens_entrada": int(fila["tokens_entrada"]),
            "tokens_salida": int(fila["tokens_salida"]),
            "costo_usd": float(fila["costo_usd"] or 0),
        }
        for fila in filas
    ]
    return {"periodo": periodo, "modelos": modelos}


@router.get("/diario")
async def get_usage_diario(
    periodo: str = "30",
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """`GET /v1/usage/diario?periodo=7|30|todo` — tokens de entrada/salida,
    llamadas y costo POR DÍA (panel de costos server-side).

    Además de la serie, si el ÚLTIMO DÍA COMPLETO supera
    `USAGE_ALERT_USD_PER_DAY` se emite un WARNING de log con día + costo +
    umbral (`_alertar_presupuesto_diario`): solo log, sin envíos."""
    if periodo not in _PERIODOS_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"periodo inválido: {periodo!r} (usa 7, 30 o todo).",
        )
    filas = await repo.usage_llm_diario_desde(
        tenant_id=current_user.tenant_id, since=_since_para_periodo(periodo)
    )
    dias: list[dict[str, Any]] = []
    for fila in filas:
        dia = fila["dia"]
        if isinstance(dia, datetime):
            dia = dia.date()
        dias.append(
            {
                "dia": dia.isoformat(),
                "llamadas": int(fila["llamadas"]),
                "tokens_entrada": int(fila["tokens_entrada"]),
                "tokens_salida": int(fila["tokens_salida"]),
                "costo_usd": float(fila["costo_usd"] or 0),
            }
        )
    _alertar_presupuesto_diario(filas, settings.USAGE_ALERT_USD_PER_DAY)
    return {"periodo": periodo, "dias": dias}


@router.get("/por_job")
async def get_usage_por_job(
    periodo: str = "30",
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """`GET /v1/usage/por_job?periodo=7|30|todo` — tokens de entrada/salida,
    llamadas y costo POR JOB (`meta->>'job'` del evento `llm_tokens`).

    Eventos sin job (telemetría vieja o rutas que no lo setean) se agrupan
    bajo "sin_job". Filas ordenadas por tokens totales DESC, misma forma de
    respuesta que `/modelos`."""
    if periodo not in _PERIODOS_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"periodo inválido: {periodo!r} (usa 7, 30 o todo).",
        )
    filas = await repo.usage_llm_por_job_desde(
        tenant_id=current_user.tenant_id, since=_since_para_periodo(periodo)
    )
    jobs = [
        {
            "job": str(fila["job"]),
            "llamadas": int(fila["llamadas"]),
            "tokens_entrada": int(fila["tokens_entrada"]),
            "tokens_salida": int(fila["tokens_salida"]),
            "costo_usd": float(fila["costo_usd"] or 0),
        }
        for fila in filas
    ]
    return {"periodo": periodo, "jobs": jobs}


@router.get("")
async def get_usage(
    current_user: CurrentUser = Depends(get_current_user), repo: Repo = Depends(get_repo)
) -> dict[str, Any]:
    tenant = current_user.tenant
    period_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used_by_kind = await repo.sum_usage_by_kind_since(
        tenant_id=tenant.tenant_id, since=period_start
    )
    cost_usd = await repo.sum_cost_usd_since(tenant_id=tenant.tenant_id, since=period_start)

    limits = {
        LIMIT_MESSAGES_PER_DAY: tenant.flags.get(LIMIT_MESSAGES_PER_DAY, UNLIMITED),
        LIMIT_VOICE_MINUTES_MONTH: tenant.flags.get(LIMIT_VOICE_MINUTES_MONTH, UNLIMITED),
        LIMIT_STORAGE_MB: tenant.flags.get(LIMIT_STORAGE_MB, UNLIMITED),
        LIMIT_PHONE_NUMBERS: tenant.flags.get(LIMIT_PHONE_NUMBERS, UNLIMITED),
        LIMIT_SEATS: tenant.flags.get(LIMIT_SEATS, UNLIMITED),
    }
    flags = {name: bool(tenant.flags.get(name, False)) for name in BOOL_FLAGS}

    return {
        "plan_key": tenant.plan_key,
        "period_start": period_start.date().isoformat(),
        "usage": used_by_kind,
        "cost_usd": cost_usd,
        "limits": limits,
        "flags": flags,
    }
