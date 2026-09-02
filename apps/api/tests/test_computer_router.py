"""`edecan_api.routers.computer` — plano de control de "toma de control / pausa".

Cubre: crear sesión, listar, takeover (mode='user'), return (mode='agent'),
pause (mode/status='paused'), resume, end (status='ended'), y los guardas de
validación (kind/modo inválido, worker ajeno, sesión inexistente o ya
terminada). Mismo patrón de doble de sesión que `test_approvals.py`:
`get_tenant_session` apunta a un `FakeComputerSession` que entiende el SQL de
`computer.py`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

import edecan_api.deps as edecan_deps
from edecan_api.routers import computer


class _FakeResult:
    def __init__(self, rows: list[dict] | None = None, rowcount: int = 1) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> dict | None:
        return dict(self._rows[0]) if self._rows else None

    def all(self) -> list[dict]:
        return [dict(r) for r in self._rows]


class FakeComputerSession:
    """Entiende (por prefijo SQL + claves de `params`) las queries de `computer.py`."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.agents: set[str] = set()

    def seed_session(
        self,
        *,
        session_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        kind: str = "desktop",
        mode: str = "agent",
        ephemeral: bool = False,
        status: str = "active",
        workspace_scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": str(session_id),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "agent_id": str(agent_id) if agent_id else None,
            "kind": kind,
            "mode": mode,
            "ephemeral": ephemeral,
            "status": status,
            "workspace_scope": workspace_scope or {},
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        self.sessions[str(session_id)] = row
        return row

    def seed_agent(self, agent_id: uuid.UUID) -> None:
        self.agents.add(str(agent_id))

    async def execute(self, clause, params=None) -> _FakeResult:
        sql = str(clause)
        params = dict(params or {})
        primer = sql.strip().split(None, 1)[0].upper()

        if primer == "SELECT":
            if "FROM persistent_agents" in sql:
                ok = params.get("id") in self.agents
                return _FakeResult(rows=[{"id": params.get("id")}] if ok else [])
            if "ORDER BY updated_at DESC" in sql:
                rows = [
                    row
                    for row in self.sessions.values()
                    if row["tenant_id"] == params["tenant_id"]
                    and (params.get("agent_id") is None or row["agent_id"] == params["agent_id"])
                ]
                return _FakeResult(rows=rows)
            row = self.sessions.get(params.get("id"))
            if row is None or row["tenant_id"] != params["tenant_id"]:
                return _FakeResult(rows=[])
            return _FakeResult(rows=[row])

        if primer == "INSERT":
            sid = uuid.uuid4()
            row = {
                "id": str(sid),
                "tenant_id": params["tenant_id"],
                "user_id": params["user_id"],
                "agent_id": params.get("agent_id"),
                "kind": params["kind"],
                "mode": params["mode"],
                "ephemeral": bool(params["ephemeral"]),
                "status": "active",
                "workspace_scope": json.loads(params["workspace_scope"] or "{}"),
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
            self.sessions[str(sid)] = row
            return _FakeResult(rows=[row])

        if primer == "UPDATE":
            row = self.sessions.get(params.get("id"))
            if row is None or row["tenant_id"] != params["tenant_id"]:
                return _FakeResult(rowcount=0)
            if "status = 'ended'" in sql:
                row["status"] = "ended"
            else:
                if params.get("mode"):
                    row["mode"] = params["mode"]
                if params.get("status"):
                    row["status"] = params["status"]
            row["updated_at"] = datetime.now(UTC)
            return _FakeResult(rowcount=1)

        raise AssertionError(f"query inesperada en el fake: {sql} params={params}")


@pytest.fixture
def fake_session() -> FakeComputerSession:
    return FakeComputerSession()


@pytest.fixture
def _mounted_app(app, fake_session: FakeComputerSession):
    ya_montado = any(getattr(route, "path", "") == "/v1/computer/sessions" for route in app.routes)
    if not ya_montado:
        app.include_router(computer.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_create_session(client, fake_session: FakeComputerSession):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    agent_id = uuid.uuid4()
    fake_session.seed_agent(agent_id)

    resp = await client.post(
        "/v1/computer/sessions",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
        json={
            "kind": "desktop",
            "agent_id": str(agent_id),
            "ephemeral": False,
            "workspace_scope": {"root": "/tmp/agente"},
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["mode"] == "agent"
    assert body["status"] == "active"
    assert body["kind"] == "desktop"
    assert body["agent_id"] == str(agent_id)
    assert body["workspace_scope"] == {"root": "/tmp/agente"}


async def test_create_session_kind_invalido(client):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    resp = await client.post(
        "/v1/computer/sessions",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
        json={"kind": "cafetera"},
    )
    assert resp.status_code == 422


async def test_create_session_agent_de_otro_tenant_422(client):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    resp = await client.post(
        "/v1/computer/sessions",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
        json={"kind": "desktop", "agent_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 422


async def test_list_sessions_y_filtro_por_agente(client, fake_session: FakeComputerSession):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    fake_session.seed_session(session_id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id)
    fake_session.seed_session(
        session_id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, agent_id=agent_a
    )
    fake_session.seed_session(
        session_id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, agent_id=agent_b
    )

    resp = await client.get(
        "/v1/computer/sessions", headers=auth_headers(user_id=user_id, tenant_id=tenant_id)
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 3

    resp_b = await client.get(
        f"/v1/computer/sessions?agent_id={agent_b}",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert resp_b.status_code == 200
    body = resp_b.json()
    assert len(body) == 1
    assert body[0]["agent_id"] == str(agent_b)


async def test_takeover_y_return(client, fake_session: FakeComputerSession):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    session_id = uuid.uuid4()
    fake_session.seed_session(session_id=session_id, tenant_id=tenant_id, user_id=user_id)

    resp = await client.post(
        f"/v1/computer/sessions/{session_id}/takeover",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "user"
    assert resp.json()["status"] == "active"

    resp = await client.post(
        f"/v1/computer/sessions/{session_id}/return",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "agent"


async def test_pause_y_resume(client, fake_session: FakeComputerSession):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    session_id = uuid.uuid4()
    fake_session.seed_session(session_id=session_id, tenant_id=tenant_id, user_id=user_id)

    resp = await client.post(
        f"/v1/computer/sessions/{session_id}/pause",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "paused"
    assert resp.json()["status"] == "paused"

    resp = await client.post(
        f"/v1/computer/sessions/{session_id}/resume",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "agent"
    assert resp.json()["status"] == "active"


async def test_end(client, fake_session: FakeComputerSession):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    session_id = uuid.uuid4()
    fake_session.seed_session(session_id=session_id, tenant_id=tenant_id, user_id=user_id)

    resp = await client.post(
        f"/v1/computer/sessions/{session_id}/end",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ended"


async def test_takeover_sobre_sesion_terminada_409(client, fake_session: FakeComputerSession):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    session_id = uuid.uuid4()
    fake_session.seed_session(
        session_id=session_id, tenant_id=tenant_id, user_id=user_id, status="ended"
    )

    resp = await client.post(
        f"/v1/computer/sessions/{session_id}/takeover",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert resp.status_code == 409


async def test_sesion_inexistente_404(client):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    resp = await client.post(
        f"/v1/computer/sessions/{uuid.uuid4()}/takeover",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert resp.status_code == 404


async def test_list_sessions_no_filtra_tenant_ajeno(client, fake_session: FakeComputerSession):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    otro_tenant = uuid.uuid4()
    fake_session.seed_session(
        session_id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, kind="desktop"
    )
    fake_session.seed_session(
        session_id=uuid.uuid4(), tenant_id=otro_tenant, user_id=user_id, kind="desktop"
    )

    resp = await client.get(
        "/v1/computer/sessions", headers=auth_headers(user_id=user_id, tenant_id=tenant_id)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["tenant_id"] == str(tenant_id)