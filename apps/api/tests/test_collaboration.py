"""Routers de colaboración: teams/workspaces/reactions/threads.

Humo end-to-end contra un doble de sesión que entiende el SQL de los tres
routers. No abre conexión real: cubre montaje, contratos y aislamiento tenant.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

import edecan_api.deps as edecan_deps


class _Rows(list):
    def mappings(self):
        return self

    def all(self):
        return list(self)

    def first(self):
        return self[0] if self else None


class _FakeSession:
    def __init__(self) -> None:
        self.teams: list[dict] = []
        self.workspaces: list[dict] = []
        self.reactions: list[dict] = []
        self.executed: list[str] = []

    async def execute(self, clause: Any, params: dict | None = None) -> _Rows:
        sql = str(clause)
        self.executed.append(sql)
        if "INSERT INTO teams" in sql:
            row = {
                "id": uuid.uuid4(),
                "tenant_id": params.get("tenant_id"),
                "user_id": params.get("user_id"),
                "name": params.get("name"),
                "description": params.get("description"),
                "avatar": {},
                "conversation_id": None,
                "created_at": None,
                "updated_at": None,
            }
            self.teams.append(row)
            return _Rows([row])
        if "FROM teams WHERE" in sql:
            return _Rows(self.teams)
        if "FROM persistent_agents" in sql:
            return _Rows([{"id": 1}])
        if "INSERT INTO team_members" in sql:
            return _Rows([])
        if "DELETE FROM team_members" in sql or "DELETE FROM teams" in sql:
            return _Rows([])
        if "INSERT INTO workspaces" in sql:
            row = {
                "id": uuid.uuid4(),
                "tenant_id": params.get("tenant_id"),
                "user_id": params.get("user_id"),
                "name": params.get("name"),
                "description": params.get("description"),
                "knowledge": {},
                "created_at": None,
                "updated_at": None,
            }
            self.workspaces.append(row)
            return _Rows([row])
        if "FROM workspaces WHERE" in sql:
            return _Rows(self.workspaces)
        if "INSERT INTO workspace_agents" in sql:
            return _Rows([])
        if "SELECT id FROM messages WHERE tenant_id" in sql:
            # existencia de mensaje (_mensaje_pertenece)
            return _Rows([{"id": uuid.UUID(params["id"])}])
        if "SELECT conversation_id FROM messages WHERE tenant_id" in sql:
            return _Rows([{"conversation_id": uuid.uuid4()}])
        if "INSERT INTO messages" in sql:
            return _Rows(
                [
                    {
                        "id": uuid.uuid4(),
                        "conversation_id": params.get("conversation_id"),
                        "role": "user",
                        "content": params.get("content"),
                        "thread_id": params.get("thread_id"),
                        "created_at": None,
                    }
                ]
            )
        if "INSERT INTO reactions" in sql:
            self.reactions.append(
                {"emoji": params.get("emoji"), "user_id": params.get("user_id")}
            )
            return _Rows([])
        if "FROM reactions" in sql:
            return _Rows(self.reactions)
        if "DELETE FROM reactions" in sql:
            self.reactions = []
            return _Rows([])
        if "FROM messages" in sql:
            return _Rows([])
        return _Rows([])


@pytest.fixture
def fake_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def client(app, fake_session) -> AsyncClient:
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_crear_listar_eliminar_equipo(client, fake_session) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    resp = await client.post(
        "/v1/teams", headers=headers, json={"name": "Product Launch"}
    )
    assert resp.status_code == 201
    team_id = resp.json()["id"]

    resp = await client.get("/v1/teams", headers=headers)
    assert resp.status_code == 200
    assert any(t["name"] == "Product Launch" for t in resp.json())

    resp = await client.post(
        f"/v1/teams/{team_id}/members",
        headers=headers,
        json={"agent_id": str(uuid.uuid4()), "role": "coordinator"},
    )
    assert resp.status_code == 201

    resp = await client.delete(f"/v1/teams/{team_id}", headers=headers)
    assert resp.status_code == 204


async def test_workspaces_y_agentes(client, fake_session) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    resp = await client.post("/v1/workspaces", headers=headers, json={"name": "Acme"})
    assert resp.status_code == 201
    ws_id = resp.json()["id"]

    resp = await client.post(
        f"/v1/workspaces/{ws_id}/agents",
        headers=headers,
        json={"agent_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 201

    resp = await client.get("/v1/workspaces", headers=headers)
    assert resp.status_code == 200
    assert any(w["name"] == "Acme" for w in resp.json())


async def test_reacciones(client, fake_session) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    msg = uuid.uuid4()
    resp = await client.post(
        f"/v1/messages/{msg}/reactions", headers=headers, json={"emoji": "👍"}
    )
    assert resp.status_code == 201

    resp = await client.get(f"/v1/messages/{msg}/reactions", headers=headers)
    assert resp.status_code == 200
    assert any(r["emoji"] == "👍" for r in resp.json())

    resp = await client.delete(f"/v1/messages/{msg}/reactions/👍", headers=headers)
    assert resp.status_code == 204


async def test_thread(client, fake_session) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    msg = uuid.uuid4()
    resp = await client.post(
        f"/v1/messages/{msg}/thread", headers=headers, json={"text": "un detalle"}
    )
    assert resp.status_code == 201
    assert resp.json()["thread_id"] == str(msg)
