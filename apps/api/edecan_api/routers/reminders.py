"""CRUD `/v1/reminders` (ARCHITECTURE.md §10.12, §10.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from edecan_api.deps import CurrentUser, get_current_user, get_repo, rate_limit
from edecan_api.repo import Repo

router = APIRouter(prefix="/v1/reminders", tags=["reminders"], dependencies=[Depends(rate_limit)])

# Canales de entrega válidos. Antes de v5 (`ARCHITECTURE.md` §14, dueño
# WP-V5-01) este campo no tenía NINGUNA validación a nivel de este router
# (`channel: str` sin restricción, cualquier string pasaba) — solo la tool
# del agente (`edecan_toolkit.recordatorios.CrearRecordatorioTool`,
# `_CANALES_VALIDOS`) restringía el vocabulario, y solo a los 4 canales de
# v1 (`web`/`voice`/`phone`/`api`). v5 suma `"mobile"` (push a la app móvil,
# dueño real WP-V5-13, tabla `devices.push_token`/`push_platform`) y
# aprovecha para que la propia API REST valide el campo explícitamente en
# vez de aceptar cualquier string.
#
# NOTA para un WP de seguimiento: `packages/toolkit/edecan_toolkit/
# recordatorios.py::_CANALES_VALIDOS` es un segundo allowlist independiente
# (usado por la tool `crear_recordatorio`, no por este router HTTP) que
# sigue sin incluir `"mobile"` — queda fuera del alcance de este work
# package (no toca `packages/toolkit/`), así que un recordatorio creado por
# el AGENTE con `channel="mobile"` hoy cae en silencio a `"web"` (ver esa
# tool), mientras que uno creado por este endpoint HTTP con `channel="mobile"`
# sí lo respeta. Sincronizar ambos allowlists queda pendiente.
CANALES_VALIDOS = ("web", "voice", "phone", "api", "mobile")


class ReminderIn(BaseModel):
    due_at: datetime
    message: str
    rrule: str | None = None
    channel: Literal[*CANALES_VALIDOS] = "web"


class ReminderPatch(BaseModel):
    due_at: datetime | None = None
    message: str | None = None
    rrule: str | None = None
    channel: Literal[*CANALES_VALIDOS] | None = None
    status: Literal["pending", "sent", "cancelled"] | None = None


@router.get("")
async def list_reminders(
    current_user: CurrentUser = Depends(get_current_user), repo: Repo = Depends(get_repo)
) -> list[dict[str, Any]]:
    return await repo.list_reminders(tenant_id=current_user.tenant_id, user_id=current_user.user_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_reminder(
    body: ReminderIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    return await repo.create_reminder(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id, fields=body.model_dump()
    )


@router.get("/{reminder_id}")
async def get_reminder(
    reminder_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    row = await repo.get_reminder(tenant_id=current_user.tenant_id, reminder_id=reminder_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recordatorio no encontrado."
        )
    return row


@router.put("/{reminder_id}")
async def update_reminder(
    reminder_id: uuid.UUID,
    body: ReminderPatch,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    row = await repo.update_reminder(
        tenant_id=current_user.tenant_id, reminder_id=reminder_id, fields=fields
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recordatorio no encontrado."
        )
    return row


@router.delete("/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reminder(
    reminder_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    deleted = await repo.delete_reminder(tenant_id=current_user.tenant_id, reminder_id=reminder_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recordatorio no encontrado."
        )
