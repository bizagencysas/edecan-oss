"""Metadata segura de uso LLM y estimación no facturable de costo."""

from __future__ import annotations

import logging
from typing import Any

from edecan_llm.base import Usage
from edecan_llm.costs import COSTOS, estimate

logger = logging.getLogger(__name__)

_ATTRIBUTION_KEYS = frozenset(
    {
        "provider",
        "model",
        "model_alias",
        "router",
        "router_alias",
        "task_kind",
        "routing_reason",
    }
)


def build_llm_usage_meta(
    *,
    attribution: dict[str, Any] | None,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> dict[str, Any]:
    """Devuelve metadata allowlisted; el precio desconocido nunca se presenta como cero."""
    safe = {
        key: str(value)[:200]
        for key, value in (attribution or {}).items()
        if key in _ATTRIBUTION_KEYS and value
    }
    model = safe.get("model")
    if not model or model not in COSTOS:
        # Honesto y ruidoso a propósito (C9a): un modelo real sin precio no
        # debe pasar desapercibido — se loguea para que el operador complete
        # `COSTOS`/`precio_referencia` en vez de que el gasto se estime en 0
        # en silencio (lo que dejaba muerta la alerta `USAGE_ALERT_USD_PER_DAY`).
        if model:
            logger.warning(
                "Uso de LLM sin precio conocido (cost_status=unknown): model=%s",
                model,
            )
        safe.update(
            {"cost_status": "unknown", "estimated_cost_usd": None, "cost_usd": None}
        )
        return safe
    usage = Usage(
        input_tokens=max(0, int(input_tokens)),
        output_tokens=max(0, int(output_tokens)),
        cached_input_tokens=max(0, int(cached_input_tokens)),
    )
    costo = round(estimate(model, usage), 12)
    safe.update(
        {
            "cost_status": "known",
            "estimated_cost_usd": costo,
            "cost_usd": costo,
        }
    )
    return safe
