"""Diagnóstico seguro de salud de proveedores (`/v1/health/providers`).

Expone únicamente contadores, latencia agregada y estado del circuit breaker.
Nunca devuelve excepciones, prompts, argumentos, tokens ni credenciales.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from edecan_api.deps import CurrentUser, get_current_user, rate_limit

router = APIRouter(
    prefix="/v1/health/providers",
    tags=["health"],
    dependencies=[Depends(rate_limit)],
)


@router.get("")
async def provider_health(
    request: Request,
    _current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Devuelve el estado agregado del registro de proveedores de este proceso."""

    health = getattr(request.app.state, "provider_health", None)
    report = health.health_report() if health is not None else {}
    recent_events = health.recent_events() if health is not None else []
    durable_events: list[dict[str, Any]] = []
    history_status = "disabled"
    store = getattr(request.app.state, "provider_health_store", None)
    if store is not None:
        history_status = "ok"
        try:
            durable_events = await store.recent_events()
        except Exception:  # noqa: BLE001 - diagnóstico no debe tumbar el health endpoint
            history_status = "unavailable"
    degraded = any(not bool(item.get("available", False)) for item in report.values())
    return {
        "format": "edecan-provider-health.v1",
        "status": "degraded" if degraded else "ok",
        "providers": report,
        "recent_events": recent_events,
        "durable_history": {
            "status": history_status,
            "events": durable_events,
        },
    }
