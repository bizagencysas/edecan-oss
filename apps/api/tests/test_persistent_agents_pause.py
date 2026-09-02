"""`POST /v1/agents/workers/pause-all` — freno de emergencia (§178)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

import edecan_api.deps as edecan_deps


class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self) -> None:
        self.updated: list[dict[str, Any]] = []
        self.rowcount = 0

    async def execute(self, clause: Any, params: dict | None = None) -> _Result:
        self.updated.append({"sql": str(clause), "params": params})
        return _Result(self.rowcount)


@pytest.fixture
def fake_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def client(app, fake_session: _FakeSession) -> AsyncClient:
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_pause_all_pausa_solo_idle_y_running(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    fake_session.rowcount = 2
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)

    resp = await client.post("/v1/agents/workers/pause-all", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"paused": 2}

    sql = fake_session.updated[0]["sql"]
    assert "UPDATE persistent_agents" in sql
    assert "status = 'paused'" in sql
    assert "status IN ('idle', 'running')" in sql
    assert fake_session.updated[0]["params"]["tenant_id"] == str(tenant_id)


async def test_pause_all_devuelve_cero_cuando_no_hay_nada(
    client, fake_session: _FakeSession
) -> None:
    fake_session.rowcount = 0
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    resp = await client.post("/v1/agents/workers/pause-all", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"paused": 0}


async def test_pause_all_sin_autenticacion_401(client) -> None:
    resp = await client.post("/v1/agents/workers/pause-all")
    assert resp.status_code == 401