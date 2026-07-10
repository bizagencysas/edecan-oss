"""Contratos del ecosistema de agentes / misiones (ROADMAP_V2.md §7.4, §7.9).

`MissionOut`/`MissionStepOut` espejan las tablas `agent_missions`/`agent_steps`
(`edecan_db.models`, migración `0003_v2_expansion`). Los vocabularios de
`status` están PINNED tal cual en ROADMAP_V2.md §7.4 — igual que
`edecan_db.models._enum_check`, esos literales se duplican aquí como
`Literal[...]` en vez de importar `edecan_db` (paquete hermano, prohibido
desde un paquete de contratos puro, ARCHITECTURE.md §10.5/§10.1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

MISSION_STATUSES: tuple[str, ...] = (
    "planning",
    "running",
    "waiting_confirmation",
    "done",
    "error",
    "cancelled",
)

MISSION_STEP_STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "waiting_confirmation",
    "done",
    "error",
    "skipped",
)

MissionStatus = Literal[
    "planning", "running", "waiting_confirmation", "done", "error", "cancelled"
]
MissionStepStatus = Literal[
    "pending", "running", "waiting_confirmation", "done", "error", "skipped"
]


class MissionOut(BaseModel):
    """Fila pública de `agent_missions` — misión por objetivo (WP-V2-06).

    `plan` (lista de pasos propuestos) y `resultado` (síntesis final) quedan
    `None` hasta que el Orchestrator los produce. `presupuesto` refleja el
    límite de pasos vigente (p. ej. `{"max_steps": MISSIONS_MAX_STEPS}`,
    ARCHITECTURE.md/ROADMAP_V2.md §7.5) — nunca vacío de verdad, siempre trae
    al menos ese campo.
    """

    id: UUID
    tenant_id: UUID
    user_id: UUID
    objetivo: str
    status: MissionStatus = "planning"
    plan: list[dict[str, Any]] | None = None
    resultado: str | None = None
    presupuesto: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class MissionStepOut(BaseModel):
    """Fila pública de `agent_steps` — un paso de una misión, ejecutado por
    uno de los perfiles de `edecan_agents.profiles` (ROADMAP_V2.md §7.9)."""

    id: UUID
    tenant_id: UUID
    mission_id: UUID
    seq: int
    agente: str
    instruccion: str
    status: MissionStepStatus = "pending"
    resultado: str | None = None
    usage: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
