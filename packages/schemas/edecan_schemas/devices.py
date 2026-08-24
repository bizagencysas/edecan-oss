"""`DeviceOut`/`RemoteSessionOut` — filas públicas de `devices`/`remote_sessions`
(ROADMAP_V2.md §7.4, dueño WP-V2-01; consumido por WP-V2-08/WP-V2-09).

`devices` generaliza el emparejamiento que ya existía para el companion
(`POST /v1/companion/pair-code`, ARCHITECTURE.md §10.12) a cualquier
dispositivo emparejado (companion de escritorio o, a futuro, apps móviles
P2, ver ROADMAP_V2.md §6.1). `remote_sessions` es el prototipo SOLO-VISTA de
WP-V2-09 (§5, guardrail §8.2: "control remoto = emparejamiento + aprobación +
cifrado, nunca backdoor"): cada sesión exige aprobación explícita en el
companion antes de pasar a `status="active"`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

DEVICE_KINDS: tuple[str, ...] = ("companion", "mobile")
DEVICE_STATUSES: tuple[str, ...] = ("active", "revoked")
REMOTE_SESSION_STATUSES: tuple[str, ...] = ("pending", "active", "ended", "denied")

DeviceKind = Literal["companion", "mobile"]
DeviceStatus = Literal["active", "revoked"]
RemoteSessionStatus = Literal["pending", "active", "ended", "denied"]


class DeviceOut(BaseModel):
    id: UUID
    tenant_id: UUID
    user_id: UUID
    nombre: str
    plataforma: str
    kind: DeviceKind
    status: DeviceStatus = "active"
    last_seen_at: datetime | None = None
    fingerprint: str | None = None
    created_at: datetime
    updated_at: datetime


class RemoteSessionOut(BaseModel):
    """`kind` no tiene vocabulario pinned (default `"view"`, texto abierto a
    propósito — mismo criterio que columnas de estado sin enum pinned en
    `edecan_db.models`, ver su docstring): P1 solo entrega vista de pantalla
    (ROADMAP_V2.md §5), pero el campo no se restringe para no bloquear un
    `"control"` futuro (P2, §6.2) con una migración de esquema."""

    id: UUID
    tenant_id: UUID
    user_id: UUID
    device_id: UUID | None = None
    kind: str = "view"
    status: RemoteSessionStatus = "pending"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    frames_count: int = 0
    created_at: datetime
    updated_at: datetime
