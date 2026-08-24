"""Políticas puras para workers persistentes y handoffs entre agentes.

Este módulo no ejecuta tools ni encola jobs. Solo valida contratos antes de
que una capa con efectos laterales pueda persistirlos o ejecutarlos.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MAX_HANDOFF_DEPTH = 4
MAX_WORKER_TOOLS = 64
MAX_BUDGET_KEYS = frozenset({"compute", "money", "time", "tools"})


def validate_worker_budget(budget: Mapping[str, Any]) -> dict[str, Any]:
    """Normaliza un presupuesto y rechaza valores negativos o desconocidos."""
    unknown = set(budget) - MAX_BUDGET_KEYS
    if unknown:
        raise ValueError(f"Claves de presupuesto no permitidas: {sorted(unknown)}")
    result: dict[str, Any] = {}
    for key, value in budget.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"Presupuesto inválido para {key!r}")
        result[key] = value
    return result


def validate_worker_tools(tools: Sequence[str]) -> list[str]:
    """Deduplica tools y conserva un límite razonable de configuración."""
    normalized = sorted({str(tool).strip() for tool in tools if str(tool).strip()})
    if len(normalized) > MAX_WORKER_TOOLS:
        raise ValueError(f"Un worker no puede declarar más de {MAX_WORKER_TOOLS} tools")
    return normalized


def validate_handoff(
    *,
    source_worker_id: str,
    destination_worker_id: str,
    task_id: str,
    depth: int = 0,
    visited_worker_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Construye un envelope acotado y rechaza ciclos o conversaciones infinitas."""
    if not task_id.strip():
        raise ValueError("Un handoff requiere task_id")
    if source_worker_id == destination_worker_id:
        raise ValueError("Un worker no puede delegarse a sí mismo")
    if depth < 0 or depth >= MAX_HANDOFF_DEPTH:
        raise ValueError("Se alcanzó la profundidad máxima de handoff")
    visited = {str(item) for item in visited_worker_ids}
    if destination_worker_id in visited or source_worker_id in visited:
        raise ValueError("El handoff formaría un ciclo")
    return {
        "protocol": "edecan.worker-handoff.v1",
        "source_worker_id": source_worker_id,
        "destination_worker_id": destination_worker_id,
        "task_id": task_id.strip(),
        "depth": depth + 1,
        "visited_worker_ids": [*visited_worker_ids, source_worker_id],
        "requires_human_approval": True,
    }
