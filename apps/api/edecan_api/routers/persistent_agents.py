"""Identidad durable de workers always-on.

Este router administra configuración y checkpoints; no encola ni ejecuta
trabajo autónomo. La autorización de ejecución queda para una ola posterior.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from edecan_agents.persistent_policy import (
    validate_handoff,
    validate_worker_budget,
    validate_worker_tools,
)
from edecan_core.queue import enqueue
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/agents/workers", tags=["persistent-agents"], dependencies=[Depends(rate_limit)]
)

_COLUMNS = (
    "id, tenant_id, user_id, name, purpose, workspace, tools, permissions, memory, schedule, "
    "budget, status, enabled, last_checkpoint, created_at, updated_at"
)
_STATUSES = ("idle", "running", "paused", "disabled")


class PersistentAgentCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=2000)
    workspace: str | None = Field(default=None, max_length=500)
    tools: list[str] = Field(default_factory=list, max_length=64)
    permissions: dict[str, Any] = Field(default_factory=dict)
    schedule: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)


class PersistentAgentPatchIn(BaseModel):
    enabled: bool | None = None
    status: str | None = None
    last_checkpoint: dict[str, Any] | None = None


class PersistentAgentTaskIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=20_000)
    task_id: str | None = Field(default=None, max_length=120)


class PersistentAgentHandoffIn(BaseModel):
    destination_worker_id: uuid.UUID
    task_id: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=1, max_length=20_000)
    depth: int = Field(default=0, ge=0, le=4)
    visited_worker_ids: list[str] = Field(default_factory=list, max_length=4)


def _current(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.tenant.flags.get("agents.missions", False):
        raise HTTPException(status_code=403, detail="Los workers no están disponibles en tu plan.")
    return user


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """UUID/datetime/jsonb → JSON que iOS y la web ya saben leer.

    Devolver `dict(row)` crudo hace que FastAPI arme un ResponseValidationError
    (detail en lista, no en string) y el teléfono muestre «500: sin detalle».
    """
    payload: dict[str, Any] = {}
    for key, value in dict(row).items():
        if isinstance(value, uuid.UUID):
            payload[key] = str(value)
        elif isinstance(value, datetime):
            payload[key] = value.isoformat()
        else:
            payload[key] = value
    return jsonable_encoder(payload)


async def _get_one(
    session: AsyncSession, user: CurrentUser, worker_id: uuid.UUID
) -> dict[str, Any]:
    result = await session.execute(
        text(
            f"SELECT {_COLUMNS} FROM persistent_agents WHERE tenant_id = :tenant_id "
            "AND user_id = :user_id AND id = :id"
        ),
        {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id), "id": str(worker_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Worker persistente no encontrado.")
    return _public_row(row)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_worker(
    body: PersistentAgentCreateIn,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    try:
        tools = validate_worker_tools(body.tools)
        budget = validate_worker_budget(body.budget)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = await session.execute(
        text(
            "INSERT INTO persistent_agents "
            "(id, tenant_id, user_id, name, purpose, workspace, tools, permissions, "
            "memory, schedule, budget) "
            "VALUES (gen_random_uuid(), :tenant_id, :user_id, :name, :purpose, :workspace, "
            ":tools ::jsonb, :permissions ::jsonb, '{}'::jsonb, :schedule ::jsonb, "
            ":budget ::jsonb) "
            f"RETURNING {_COLUMNS}"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "name": body.name.strip(),
            "purpose": body.purpose.strip(),
            "workspace": body.workspace,
            "tools": json.dumps(tools),
            "permissions": json.dumps(body.permissions),
            "schedule": json.dumps(body.schedule),
            "budget": json.dumps(budget),
        },
    )
    row = result.mappings().first()
    assert row is not None
    return _public_row(row)


@router.get("")
async def list_workers(
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    include_disabled: bool = Query(default=False),
) -> list[dict[str, Any]]:
    try:
        result = await session.execute(
            text(
                f"SELECT {_COLUMNS} FROM persistent_agents WHERE tenant_id = :tenant_id "
                "AND user_id = :user_id AND (:include_disabled OR status <> 'disabled') "
                "ORDER BY updated_at DESC"
            ),
            {
                "tenant_id": str(user.tenant_id),
                "user_id": str(user.user_id),
                "include_disabled": include_disabled,
            },
        )
        return [_public_row(row) for row in result.mappings().all()]
    except ProgrammingError:
        logger.exception("list_workers: esquema de persistent_agents no disponible")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Los workers no están listos en esta instalación.",
        ) from None
    except SQLAlchemyError:
        logger.exception("list_workers: error de base")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No pude leer los workers.",
        ) from None


@router.patch("/{worker_id}")
async def patch_worker(
    worker_id: uuid.UUID,
    body: PersistentAgentPatchIn,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    if body.status is not None and body.status not in _STATUSES:
        raise HTTPException(status_code=422, detail="Estado de worker inválido.")
    await _get_one(session, user, worker_id)
    sets = ["updated_at = now()"]
    params: dict[str, Any] = {
        "tenant_id": str(user.tenant_id),
        "user_id": str(user.user_id),
        "id": str(worker_id),
    }
    if body.enabled is not None:
        sets.append("enabled = :enabled")
        params["enabled"] = body.enabled
    if body.status is not None:
        sets.append("status = :status")
        params["status"] = body.status
    if body.last_checkpoint is not None:
        sets.append("last_checkpoint = :last_checkpoint ::jsonb")
        params["last_checkpoint"] = json.dumps(body.last_checkpoint)
    await session.execute(
        text(
            f"UPDATE persistent_agents SET {', '.join(sets)} WHERE tenant_id = :tenant_id "
            "AND user_id = :user_id AND id = :id"
        ),
        params,
    )
    return await _get_one(session, user, worker_id)


@router.post("/{worker_id}/tasks", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_worker_task(
    worker_id: uuid.UUID,
    body: PersistentAgentTaskIn,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Encola una tarea explícita; el worker valida estado y presupuesto otra vez."""
    worker = await _get_one(session, user, worker_id)
    if not worker["enabled"] or worker["status"] in ("paused", "disabled", "running"):
        raise HTTPException(status_code=409, detail="El worker no está disponible.")
    task_id = body.task_id or str(uuid.uuid4())
    await enqueue(
        settings,
        "run_persistent_agent",
        {"worker_id": str(worker_id), "task_id": task_id, "instruction": body.instruction.strip()},
        user.tenant_id,
    )
    return {"worker_id": str(worker_id), "task_id": task_id, "status": "queued"}


@router.get("/handoffs")
async def list_worker_handoffs(
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    status_filter: str | None = Query(default="pending"),
) -> list[dict[str, Any]]:
    try:
        result = await session.execute(
            text(
                "SELECT h.id, h.destination_worker_id, h.task_id, h.envelope, h.status, "
                "h.created_at, h.updated_at, destination.name AS destination_name "
                "FROM persistent_agent_handoffs h "
                "JOIN persistent_agents destination ON destination.id = h.destination_worker_id "
                "WHERE h.tenant_id = :tenant_id AND destination.user_id = :user_id "
                "AND (:status IS NULL OR h.status = :status) "
                "ORDER BY h.created_at DESC"
            ),
            {
                "tenant_id": str(user.tenant_id),
                "user_id": str(user.user_id),
                "status": status_filter,
            },
        )
        return [_public_row(row) for row in result.mappings().all()]
    except ProgrammingError:
        logger.exception("list_worker_handoffs: esquema de handoffs no disponible")
        return []
    except SQLAlchemyError:
        logger.exception("list_worker_handoffs: error de base")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No pude leer los handoffs.",
        ) from None


@router.post("/{worker_id}/handoffs", status_code=status.HTTP_201_CREATED)
async def create_worker_handoff(
    worker_id: uuid.UUID,
    body: PersistentAgentHandoffIn,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Persiste un handoff pendiente; no lo ejecuta ni auto-aprueba."""
    await _get_one(session, user, worker_id)
    await _get_one(session, user, body.destination_worker_id)
    try:
        envelope = validate_handoff(
            source_worker_id=str(worker_id),
            destination_worker_id=str(body.destination_worker_id),
            task_id=body.task_id,
            depth=body.depth,
            visited_worker_ids=body.visited_worker_ids,
        )
        envelope["instruction"] = body.instruction.strip()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = await session.execute(
        text(
            "INSERT INTO persistent_agent_handoffs "
            "(id, tenant_id, source_worker_id, destination_worker_id, task_id, envelope) "
            "VALUES (gen_random_uuid(), :tenant_id, :source, :destination, :task_id, "
            ":envelope ::jsonb) "
            "RETURNING id, tenant_id, source_worker_id, destination_worker_id, task_id, "
            "envelope, status, result, created_at, updated_at"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "source": str(worker_id),
            "destination": str(body.destination_worker_id),
            "task_id": body.task_id,
            "envelope": json.dumps(envelope),
        },
    )
    row = result.mappings().first()
    assert row is not None
    return _public_row(row)


@router.post("/handoffs/{handoff_id}/approve")
async def approve_worker_handoff(
    handoff_id: uuid.UUID,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Aprueba un handoff destinado al usuario y encola exactamente una tarea."""
    result = await session.execute(
        text(
            "SELECT h.id, h.destination_worker_id, h.task_id, h.envelope, h.status "
            "FROM persistent_agent_handoffs h "
            "JOIN persistent_agents destination ON destination.id = h.destination_worker_id "
            "WHERE h.tenant_id = :tenant_id AND h.id = :id AND destination.user_id = :user_id"
        ),
        {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id), "id": str(handoff_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Handoff no encontrado.")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="Este handoff ya fue resuelto.")
    envelope = row["envelope"]
    if isinstance(envelope, str):
        envelope = json.loads(envelope)
    instruction = str((envelope or {}).get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="El handoff no tiene instrucción ejecutable.")
    await session.execute(
        text(
            "UPDATE persistent_agent_handoffs SET status = 'approved', updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :id AND status = 'pending'"
        ),
        {"tenant_id": str(user.tenant_id), "id": str(handoff_id)},
    )
    await enqueue(
        settings,
        "run_persistent_agent",
        {
            "worker_id": str(row["destination_worker_id"]),
            "task_id": str(row["task_id"]),
            "instruction": instruction,
            "handoff_id": str(handoff_id),
        },
        user.tenant_id,
    )
    return {"handoff_id": str(handoff_id), "status": "approved", "task_id": row["task_id"]}
