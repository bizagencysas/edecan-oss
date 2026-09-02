"""`edecan_api.routers.approvals` — aprobaciones durables de acciones peligrosas.

Cubre: listar pendientes, aprobar (marca `approved` + reanuda con
`_resume_approved_turn`), denegar (marca `denied`), y el respaldo durable que
escribe `_persist_pending_approval` desde `conversations.py`. Mismo patrón de
doble de sesión que `test_missions_router.py`: `get_tenant_session` apunta a un
`FakeApprovalsSession` que entiende el SQL de `approvals.py`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import auth_headers
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

import edecan_api.deps as edecan_deps
from edecan_api.routers import approvals
from edecan_api.routers.conversations import _persist_pending_approval


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


class FakeApprovalsSession:
    """Entiende (por prefijo SQL + claves de `params`) las queries de
    `approvals.py` — mismo espíritu que el doble de `test_missions_router.py`."""

    def __init__(self) -> None:
        self.approvals: dict[str, dict] = {}
        self.executed: list[tuple[str, dict]] = []

    def seed(
        self,
        *,
        approval_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        tool_call_id: str = "call_1",
        snapshot: dict[str, Any] | None = None,
        status: str = "pending",
        decided_at: datetime | None = None,
        decided_by: uuid.UUID | None = None,
    ) -> dict:
        row = {
            "id": str(approval_id),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "conversation_id": conversation_id,
            "tool_call_id": tool_call_id,
            "agent_snapshot": snapshot or {"name": "publicar_social", "args": {}},
            "status": status,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "decided_at": decided_at,
            "decided_by": str(decided_by) if decided_by else None,
        }
        self.approvals[str(approval_id)] = row
        return row

    async def execute(self, clause, params=None) -> _FakeResult:
        sql = str(clause)
        params = dict(params or {})
        self.executed.append((sql, params))
        primer = sql.strip().split(None, 1)[0].upper()

        if primer == "SELECT":
            rows = [
                row
                for row in self.approvals.values()
                if row["tenant_id"] == params["tenant_id"]
                and row["user_id"] == params["user_id"]
                and (params.get("id") is None or row["id"] == params["id"])
                and ("status = 'pending'" not in sql or row["status"] == "pending")
            ]
            return _FakeResult(rows=rows)

        if primer == "INSERT":
            return _FakeResult(rowcount=1)

        if primer == "UPDATE":
            row = self.approvals.get(params["id"])
            if row is None or row["tenant_id"] != params["tenant_id"]:
                return _FakeResult(rowcount=0)
            cond_ok = ("status = 'pending'" in sql and row["status"] == "pending") or (
                "status = 'approved'" in sql and row["status"] == "approved"
            )
            if not cond_ok:
                return _FakeResult(rowcount=0)
            if "status = 'denied'" in sql:
                row["status"] = "denied"
            elif "status = 'pending', decided_at = NULL" in sql:
                row["status"] = "pending"
                row["decided_at"] = None
                row["decided_by"] = None
            elif "status = 'approved'" in sql:
                row["status"] = "approved"
            if params.get("decided_by"):
                row["decided_by"] = params["decided_by"]
                row["decided_at"] = datetime.now(UTC)
            return _FakeResult(rowcount=1)

        raise AssertionError(f"query inesperada en el fake: {sql} params={params}")


@pytest.fixture
def fake_session() -> FakeApprovalsSession:
    return FakeApprovalsSession()


@pytest.fixture
def _mounted_app(app, fake_session: FakeApprovalsSession, fake_repo):
    ya_montado = any(getattr(route, "path", "") == "/v1/approvals" for route in app.routes)
    if not ya_montado:
        app.include_router(approvals.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    app.dependency_overrides[edecan_deps.get_streaming_repo] = lambda: fake_repo
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _empty_events():
    if False:  # pragma: no cover
        yield ""


def _install_fake_resume(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_resume(**kwargs: Any) -> StreamingResponse:
        captured.update(kwargs)
        return StreamingResponse(_empty_events(), media_type="text/event-stream")

    monkeypatch.setattr(approvals, "_resume_approved_turn", fake_resume)
    return captured


def _seed_conversation(fake_repo, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    cid = uuid.uuid4()
    fake_repo.conversations[cid] = {
        "id": cid,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "title": "",
        "channel": "web",
        "chat_model": None,
        "chat_effort": None,
        "context_cleared_at": None,
    }
    return cid


async def test_list_pending_solo_devuelve_pendientes_y_no_filtra_snapshot(
    client, fake_session: FakeApprovalsSession
):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    cid = uuid.uuid4()
    fake_session.seed(
        approval_id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=cid,
        tool_call_id="call_pending",
        snapshot={"name": "publicar_social", "args": {"x": 1}},
    )
    fake_session.seed(
        approval_id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=cid,
        tool_call_id="call_done",
        status="approved",
    )

    resp = await client.get(
        "/v1/approvals", headers=auth_headers(user_id=user_id, tenant_id=tenant_id)
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    item = body[0]
    assert item["tool_call_id"] == "call_pending"
    assert item["name"] == "publicar_social"
    assert item["args"] == {"x": 1}
    assert item["status"] == "pending"
    assert "agent_snapshot" not in item


async def test_approve_marca_approved_y_reanuda(
    client, fake_session: FakeApprovalsSession, fake_repo, monkeypatch: pytest.MonkeyPatch
):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    cid = _seed_conversation(fake_repo, tenant_id=tenant_id, user_id=user_id)
    approval_id = uuid.uuid4()
    snapshot = {
        "name": "publicar_social",
        "args": {"texto": "hola"},
        "pending_turn": {
            "version": 1,
            "messages": [],
            "tool_calls": [],
            "operational_tool_names": [],
        },
    }
    fake_session.seed(
        approval_id=approval_id,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=cid,
        tool_call_id="call_1",
        snapshot=snapshot,
    )
    captured = _install_fake_resume(monkeypatch)

    resp = await client.post(
        f"/v1/approvals/{approval_id}/approve",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert resp.status_code == 200
    row = fake_session.approvals[str(approval_id)]
    assert row["status"] == "approved"
    assert row["decided_by"] == str(user_id)
    assert captured["tool_call_id"] == "call_1"
    assert captured["pending"]["name"] == "publicar_social"
    assert captured["pending"]["args"] == {"texto": "hola"}
    assert captured["conversation_id"] == cid


async def test_approve_no_encontrada_404(client):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    resp = await client.post(
        f"/v1/approvals/{uuid.uuid4()}/approve",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert resp.status_code == 404


async def test_approve_ya_resuelta_409(client, fake_session: FakeApprovalsSession):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    approval_id = uuid.uuid4()
    fake_session.seed(
        approval_id=approval_id,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=uuid.uuid4(),
        status="denied",
    )
    resp = await client.post(
        f"/v1/approvals/{approval_id}/approve",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert resp.status_code == 409


async def test_deny_marca_denied(client, fake_session: FakeApprovalsSession):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    approval_id = uuid.uuid4()
    fake_session.seed(
        approval_id=approval_id,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=uuid.uuid4(),
    )
    resp = await client.post(
        f"/v1/approvals/{approval_id}/deny",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert resp.status_code == 200
    assert resp.json() == {"approval_id": str(approval_id), "status": "denied"}
    assert fake_session.approvals[str(approval_id)]["status"] == "denied"
    assert fake_session.approvals[str(approval_id)]["decided_by"] == str(user_id)


async def test_approve_revierte_a_pending_si_la_reanudacion_falla(
    client, fake_session: FakeApprovalsSession, fake_repo, monkeypatch: pytest.MonkeyPatch
):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    cid = _seed_conversation(fake_repo, tenant_id=tenant_id, user_id=user_id)
    approval_id = uuid.uuid4()
    fake_session.seed(
        approval_id=approval_id,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=cid,
        tool_call_id="call_1",
    )

    from fastapi import HTTPException

    async def fake_resume(**kwargs: Any) -> StreamingResponse:
        raise HTTPException(status_code=409, detail="Herramienta caída.")

    monkeypatch.setattr(approvals, "_resume_approved_turn", fake_resume)

    resp = await client.post(
        f"/v1/approvals/{approval_id}/approve",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert resp.status_code == 409
    row = fake_session.approvals[str(approval_id)]
    assert row["status"] == "pending"
    assert row["decided_by"] is None


async def test_persist_pending_approval_escribe_el_snapshot_durable(fake_session):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    conversation_id = uuid.uuid4()

    await _persist_pending_approval(
        fake_session,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        tool_call_id="call_1",
        name="publicar_social",
        args={"texto": "hola"},
        pending_turn=None,
    )

    sql, params = fake_session.executed[0]
    assert "INSERT INTO pending_approvals" in sql
    assert params["tenant_id"] == str(tenant_id)
    assert params["user_id"] == str(user_id)
    assert params["conversation_id"] == str(conversation_id)
    assert params["tool_call_id"] == "call_1"
    snapshot = json.loads(params["snapshot"])
    assert snapshot == {"name": "publicar_social", "args": {"texto": "hola"}}


async def test_persist_pending_approval_sin_sesion_es_noop():
    await _persist_pending_approval(
        None,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        tool_call_id="call_1",
        name="x",
        args={},
    )