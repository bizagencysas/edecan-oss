"""`/v1/workspaces` — workspaces y sus agentes (product design)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"], dependencies=[Depends(rate_limit)])


def _as_public(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row(row: Mapping[str, Any]) -> dict[str, Any]:
    return jsonable_encoder({k: _as_public(v) for k, v in dict(row).items()})


class WorkspaceCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    knowledge: dict[str, Any] = Field(default_factory=dict)


class WorkspaceAgentIn(BaseModel):
    agent_id: uuid.UUID


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreateIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "INSERT INTO workspaces (id, tenant_id, user_id, name, description, knowledge) "
            "VALUES (gen_random_uuid(), :tenant_id, :user_id, :name, :description, "
            ":knowledge ::jsonb) "
            "RETURNING id, tenant_id, user_id, name, description, knowledge, created_at, updated_at"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "name": body.name.strip(),
            "description": body.description,
            "knowledge": json.dumps(body.knowledge),
        },
    )
    row = result.mappings().first()
    assert row is not None
    return _row(row)


@router.get("")
async def list_workspaces(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT id, tenant_id, user_id, name, description, knowledge, created_at, updated_at "
            "FROM workspaces WHERE tenant_id = :tenant_id AND user_id = :user_id "
            "ORDER BY created_at ASC"
        ),
        {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id)},
    )
    workspaces = [_row(r) for r in result.mappings().all()]
    agents = await session.execute(
        text("SELECT workspace_id, agent_id FROM workspace_agents WHERE tenant_id = :tenant_id"),
        {"tenant_id": str(user.tenant_id)},
    )
    by_ws: dict[str, list[str]] = {}
    for a in agents.mappings().all():
        by_ws.setdefault(str(a["workspace_id"]), []).append(str(a["agent_id"]))
    for ws in workspaces:
        ws["agents"] = by_ws.get(str(ws["id"]), [])
    return workspaces


@router.post("/{workspace_id}/agents", status_code=status.HTTP_201_CREATED)
async def add_workspace_agent(
    workspace_id: uuid.UUID,
    body: WorkspaceAgentIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    try:
        await session.execute(
            text(
                "INSERT INTO workspace_agents (id, tenant_id, workspace_id, agent_id) "
                "VALUES (gen_random_uuid(), :tenant_id, :workspace_id, :agent_id) "
                "ON CONFLICT (workspace_id, agent_id) DO NOTHING"
            ),
            {
                "tenant_id": str(user.tenant_id),
                "workspace_id": str(workspace_id),
                "agent_id": str(body.agent_id),
            },
        )
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=422, detail="No existe el workspace o el agente.") from exc
    return {"workspace_id": str(workspace_id), "agent_id": str(body.agent_id)}


@router.delete("/{workspace_id}/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_workspace_agent(
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    await session.execute(
        text(
            "DELETE FROM workspace_agents WHERE tenant_id = :tenant_id "
            "AND workspace_id = :workspace_id AND agent_id = :agent_id"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "workspace_id": str(workspace_id),
            "agent_id": str(agent_id),
        },
    )


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    await session.execute(
        text("DELETE FROM workspaces WHERE tenant_id = :tenant_id AND id = :id"),
        {"tenant_id": str(user.tenant_id), "id": str(workspace_id)},
    )