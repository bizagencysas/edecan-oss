"""`edecan_api.routers.activity` — feed reciente de acciones observables.

Humo end-to-end contra un doble de sesión que entiende las 4 consultas del
router. No abre conexión real: cubre montaje, shape del ítem, merge/orden y
recorte de resumen.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

import edecan_api.deps as edecan_deps
from edecan_api.routers import activity as activity_router


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


def _dt(minute: int) -> datetime:
    return datetime(2026, 8, 25, 12, minute, 0, tzinfo=UTC)


class _FakeSession:
    def __init__(self, *, tool_rows=None, step_rows=None, approval_rows=None, session_rows=None):
        self.tool_rows = tool_rows or []
        self.step_rows = step_rows or []
        self.approval_rows = approval_rows or []
        self.session_rows = session_rows or []

    async def execute(self, clause: Any, params: dict | None = None) -> _Rows:
        sql = str(clause)
        if "FROM messages" in sql:
            return _Rows(self.tool_rows)
        if "FROM agent_steps" in sql:
            return _Rows(self.step_rows)
        if "FROM pending_approvals" in sql:
            return _Rows(self.approval_rows)
        if "FROM computer_sessions" in sql:
            return _Rows(self.session_rows)
        return _Rows([])


@pytest.fixture
def fake_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def client(app, fake_session: _FakeSession) -> AsyncClient:
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    app.include_router(activity_router.router)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_activity_vacio(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    resp = await client.get("/v1/activity", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_activity_shape_y_tipos(client, fake_session: _FakeSession) -> None:
    fake_session.tool_rows = [
        {
            "tool_calls": [{"name": "buscar_web"}],
            "created_at": _dt(10),
        }
    ]
    fake_session.step_rows = [
        {
            "agente": "research",
            "instruccion": "investiga el mercado",
            "resultado": "encontré tres proveedores relevantes",
            "status": "done",
            "updated_at": _dt(20),
            "agent": "Research Analyst",
        }
    ]
    fake_session.approval_rows = [
        {
            "agent_snapshot": {"name": "enviar_correo"},
            "status": "pending",
            "updated_at": _dt(15),
        }
    ]
    fake_session.session_rows = [
        {
            "kind": "desktop",
            "mode": "user",
            "status": "active",
            "updated_at": _dt(5),
            "agent": "Research Analyst",
        }
    ]
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    resp = await client.get("/v1/activity", headers=headers)
    assert resp.status_code == 200

    items = resp.json()
    tipos = [item["type"] for item in items]
    assert tipos == ["mission_step", "approval", "tool_call", "computer_session"]

    paso = items[0]
    assert set(paso) == {"type", "agent", "summary", "at", "status"}
    assert paso["agent"] == "Research Analyst"
    assert paso["status"] == "done"
    assert paso["summary"] == "encontré tres proveedores relevantes"

    tool = items[2]
    assert tool["agent"] is None
    assert tool["summary"] == "Usó la herramienta «buscar_web»"
    assert tool["at"] == _dt(10).isoformat()

    sesion = items[3]
    assert sesion["summary"] == "Superficie desktop: user"


async def test_activity_recorta_resumen_largo(client, fake_session: _FakeSession) -> None:
    fake_session.step_rows = [
        {
            "agente": "research",
            "instruccion": None,
            "resultado": "palabra " * 100,
            "status": "error",
            "updated_at": _dt(1),
            "agent": None,
        }
    ]
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    resp = await client.get("/v1/activity", headers=headers)
    item = resp.json()[0]
    assert len(item["summary"]) <= 181
    assert item["summary"].endswith("…")


async def test_activity_ignora_tool_calls_sin_nombre(client, fake_session: _FakeSession) -> None:
    fake_session.tool_rows = [
        {"tool_calls": [{"no_name": True}], "created_at": _dt(1)},
        {"tool_calls": None, "created_at": _dt(2)},
    ]
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    resp = await client.get("/v1/activity", headers=headers)
    assert resp.json() == []


async def test_activity_respeta_limit(client, fake_session: _FakeSession) -> None:
    fake_session.tool_rows = [
        {"tool_calls": [{"name": f"tool_{i}"}], "created_at": _dt(i)} for i in range(60)
    ]
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    resp = await client.get("/v1/activity", headers=headers, params={"limit": 3})
    assert len(resp.json()) == 3


async def test_activity_sin_autenticacion_401(client) -> None:
    resp = await client.get("/v1/activity")
    assert resp.status_code == 401
