"""Enforcement determinista del presupuesto de un run (PHASE2 §63).

El presupuesto vive en `persistent_agents.budget` con las claves `money`,
`compute`, `time` y `tools` (esquema exacto en
`edecan_agents.persistent_policy.validate_worker_budget`). Cada clave es un
tope numérico no negativo; ausente o `<= 0` significa "sin tope" para esa
dimensión.

Este módulo es PURO (sin SQL, sin LLM, sin red): los handlers
(`run_persistent_agent.py`/`run_automation.py`) calculan el uso observado de un
turno y llaman :func:`presupuesto_excedido` antes de persistir el estado
terminal. Si un tope se excede, el run se detiene y el handler marca el estado
"needs attention" en vez de seguir en silencio o reventar — ver el docstring de
esos handlers.

`money` solo se hace cumplir cuando hay un costo MEDIDO: este módulo no fabrica
precios (Método Fable §5). Si el caller no le pasa `uso["money"]`, el tope de
dinero no dispara; queda documentado, no inventado.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

BUDGET_KEYS: tuple[str, ...] = ("money", "compute", "time", "tools")


def cap_presupuesto(budget: Mapping[str, Any], clave: str) -> float | None:
    """Devuelve el tope numérico de `clave`, o `None` si no hay tope.

    `None` significa "sin límite": clave ausente, valor no numérico, o `<= 0`.
    (Un tope de 0 no permite correr nada, y tratarlo como "sin límite" sería
    mentir; por eso `<= 0` se normaliza a "sin tope", igual que
    `_lease_seconds` en `run_persistent_agent.py`.)
    """
    valor = (budget or {}).get(clave)
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    numero = float(valor)
    return numero if numero > 0 else None


def presupuesto_excedido(budget: Mapping[str, Any], uso: Mapping[str, Any]) -> tuple[str, ...]:
    """Claves cuyo tope se excedió, en orden estable de `BUDGET_KEYS`.

    Solo compara dimensiones con tope declarado Y uso observado numérico; el
    resto se ignora (nunca un falso "excedido" por un dato que falta).
    """
    excedidas: list[str] = []
    for clave in BUDGET_KEYS:
        tope = cap_presupuesto(budget, clave)
        if tope is None:
            continue
        consumido = (uso or {}).get(clave)
        if isinstance(consumido, bool) or not isinstance(consumido, (int, float)):
            continue
        if float(consumido) > tope:
            excedidas.append(clave)
    return tuple(excedidas)


def uso_desde_detalle(detalle: Mapping[str, Any], *, elapsed_seconds: float) -> dict[str, float]:
    """Uso observado de un turno a partir del `detalle` terminal del runner.

    - `compute`: tokens (``usage.input_tokens + output_tokens``, con fallback a
      ``total_tokens``).
    - `tools`: cantidad de invocaciones (eventos ``tool_start`` en `tool_log`).
    - `time`: segundos de pared pasados por el caller.
    - `money`: solo si `detalle` trae un ``cost_usd`` ya medido; si no, se omite
      (ver docstring del módulo: no se fabrica precio).
    """
    usage = detalle.get("usage") or {}
    if not isinstance(usage, Mapping):
        usage = {}

    def _entero(valor: Any) -> int:
        try:
            return int(valor or 0)
        except (TypeError, ValueError):
            return 0

    compute = _entero(usage.get("input_tokens")) + _entero(usage.get("output_tokens"))
    if compute == 0:
        compute = _entero(usage.get("total_tokens"))

    tool_log = detalle.get("tool_log") or []
    tools = (
        sum(
            1
            for evento in tool_log
            if isinstance(evento, Mapping) and evento.get("type") == "tool_start"
        )
        if isinstance(tool_log, (list, tuple))
        else 0
    )

    uso: dict[str, float] = {
        "compute": float(compute),
        "tools": float(tools),
        "time": float(elapsed_seconds),
    }
    cost_usd = detalle.get("cost_usd")
    if isinstance(cost_usd, (int, float)) and not isinstance(cost_usd, bool):
        uso["money"] = float(cost_usd)
    return uso


def motivo_excedido(excedidas: tuple[str, ...]) -> str:
    """Mensaje legible (español) para el estado "needs attention"."""
    if not excedidas:
        return ""
    return "Presupuesto excedido: " + ", ".join(excedidas) + "."


__all__ = [
    "BUDGET_KEYS",
    "cap_presupuesto",
    "motivo_excedido",
    "presupuesto_excedido",
    "uso_desde_detalle",
]