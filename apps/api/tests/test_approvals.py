"""`edecan_api.routers.approvals` — aprobaciones durables de acciones peligrosas.

Cubre: listar pendientes, aprobar (marca `approved` + reanuda con
`_resume_approved_turn`), denegar (marca `denied`), y el respaldo durable que
escribe `_persist_pending_approval` desde `conversations.py`. Mismo patrón de
doble de sesión que `test_missions_router.py`: `get_tenant_session` apunta a un
`FakeApprovalsSession` que entiende el SQL de `approvals.py`.
"""

from __future__ import annotations

import json
import logging
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
from edecan_api.routers.conversations import _args_digest, _persist_pending_approval


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


class _NoopNested:
    """Doble del SAVEPOINT (`session.begin_nested`) que usa
    `_persist_pending_approval`: no hace nada y no suprime excepciones."""

    async def __aenter__(self) -> _NoopNested:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeApprovalsSession:
    """Entiende (por prefijo SQL + claves de `params`) las queries de
    `approvals.py` — mismo espíritu que el doble de `test_missions_router.py`."""

    def __init__(self) -> None:
        self.approvals: dict[str, dict] = {}
        self.executed: list[tuple[str, dict]] = []

    def begin_nested(self) -> _NoopNested:
        return _NoopNested()

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
                and (
                    params.get("conversation_id") is None
                    or str(row["conversation_id"]) == params["conversation_id"]
                )
                and (
                    params.get("worker_id") is None
                    or str((row.get("agent_snapshot") or {}).get("worker_id") or "")
                    == params["worker_id"]
                )
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

    persisted = await _persist_pending_approval(
        fake_session,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        tool_call_id="call_1",
        name="publicar_social",
        args={"texto": "hola"},
        pending_turn=None,
    )

    assert persisted is True
    sql, params = fake_session.executed[0]
    assert "INSERT INTO pending_approvals" in sql
    assert params["tenant_id"] == str(tenant_id)
    assert params["user_id"] == str(user_id)
    assert params["conversation_id"] == str(conversation_id)
    assert params["tool_call_id"] == "call_1"
    snapshot = json.loads(params["snapshot"])
    assert snapshot == {
        "name": "publicar_social",
        "args": {"texto": "hola"},
        "args_digest": _args_digest({"texto": "hola"}),
    }


async def test_persist_pending_approval_incluye_snapshot_extra(fake_session):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    conversation_id = uuid.uuid4()
    worker_id = uuid.uuid4()

    await _persist_pending_approval(
        fake_session,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        tool_call_id="call_bot",
        name="publicar_social",
        args={"texto": "hola"},
        snapshot_extra={"worker_id": str(worker_id)},
    )

    snapshot = json.loads(fake_session.executed[0][1]["snapshot"])
    assert snapshot["worker_id"] == str(worker_id)


async def test_list_approvals_filtra_por_conversation_y_worker(
    client, fake_session: FakeApprovalsSession, fake_repo
):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    cid_bot = _seed_conversation(fake_repo, tenant_id=tenant_id, user_id=user_id)
    cid_otro = _seed_conversation(fake_repo, tenant_id=tenant_id, user_id=user_id)
    worker_id = uuid.uuid4()
    aprob_bot = uuid.uuid4()
    aprob_otro = uuid.uuid4()
    fake_session.seed(
        approval_id=aprob_bot,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=cid_bot,
        snapshot={"name": "x", "args": {}, "worker_id": str(worker_id)},
    )
    fake_session.seed(
        approval_id=aprob_otro,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=cid_otro,
        snapshot={"name": "y", "args": {}},
    )

    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    resp = await client.get(
        f"/v1/approvals?conversation_id={cid_bot}&worker_id={worker_id}",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == str(aprob_bot)
    assert body[0]["worker_id"] == str(worker_id)


async def test_persist_pending_approval_sin_sesion_devuelve_false():
    persisted = await _persist_pending_approval(
        None,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        tool_call_id="call_1",
        name="x",
        args={},
    )
    assert persisted is False


# --------------------------------------------------------------------------
# BOTS-18: durabilidad honesta — fallo de INSERT no aborta la transacción
# principal y la aprobación se reporta como SOLO efímera (Redis).
# --------------------------------------------------------------------------


class _FailingInsertSession(FakeApprovalsSession):
    """Simula la tabla `pending_approvals` ausente: el INSERT explota dentro del
    savepoint y `_persist_pending_approval` debe devolver `False` sin propagar
    la excepción (la transacción principal del turno sigue viva)."""

    async def execute(self, clause, params=None):
        sql = str(clause)
        if sql.strip().split(None, 1)[0].upper() == "INSERT":
            raise RuntimeError('relation "pending_approvals" does not exist')
        return await super().execute(clause, params)


async def test_persist_pending_approval_insert_falla_devuelve_false_y_loguea(caplog):
    caplog.set_level(logging.ERROR, logger="edecan_api.routers.conversations")
    session = _FailingInsertSession()

    persisted = await _persist_pending_approval(
        session,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        tool_call_id="call_fail",
        name="publicar_social",
        args={"texto": "hola"},
    )

    assert persisted is False
    assert "durable write FAILED" in caplog.text
    assert "SOLO" in caplog.text


# --------------------------------------------------------------------------
# BOTS-19: proyección pública enmascarada + integridad de args al reanudar.
# --------------------------------------------------------------------------


async def test_list_enmascara_campos_sensibles_y_no_filtra_el_secreto(
    client, fake_session: FakeApprovalsSession
):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    cid = uuid.uuid4()
    args = {
        "to": "ana@example.com",
        "subject": "hola",
        "body": "Cuerpo del correo visible para autorizar",
        "token": "sk-abcdef1234567890",
        "password": "supersecreto123",
        "headers": {
            "Authorization": "Bearer tok_1234567890",
            "X-Custom": "valor-no-secreto",
        },
        "nested": {"api_key": "AKIA1234567890", "label": "visible"},
    }
    fake_session.seed(
        approval_id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=cid,
        tool_call_id="call_secrets",
        snapshot={"name": "enviar_correo", "args": args},
    )

    resp = await client.get(
        "/v1/approvals", headers=auth_headers(user_id=user_id, tenant_id=tenant_id)
    )

    assert resp.status_code == 200
    item = resp.json()[0]
    out = item["args"]
    # Campos no sensibles quedan visibles para que el usuario sepa qué autoriza.
    assert out["to"] == "ana@example.com"
    assert out["body"] == "Cuerpo del correo visible para autorizar"
    # Sensibles: solo los 4 primeros caracteres + sufijo.
    assert out["token"] == "sk-a…"
    assert out["password"] == "supe…"
    assert out["headers"]["Authorization"] == "Bear…"
    assert out["headers"]["X-Custom"] == "valor-no-secreto"
    assert out["nested"]["api_key"] == "AKIA…"
    assert out["nested"]["label"] == "visible"
    # El secreto íntegro NO viaja al cliente.
    for secret in (
        "sk-abcdef1234567890",
        "supersecreto123",
        "tok_1234567890",
        "AKIA1234567890",
    ):
        assert secret not in resp.text


async def test_approve_reanuda_con_args_originales_y_digest_correcto(
    client, fake_session: FakeApprovalsSession, fake_repo, monkeypatch: pytest.MonkeyPatch
):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    cid = _seed_conversation(fake_repo, tenant_id=tenant_id, user_id=user_id)
    approval_id = uuid.uuid4()
    args = {
        "to": "ana@example.com",
        "body": "hola",
        "api_key": "AKIA-original-secreto",
    }
    fake_session.seed(
        approval_id=approval_id,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=cid,
        tool_call_id="call_1",
        snapshot={
            "name": "publicar_social",
            "args": args,
            "args_digest": _args_digest(args),
        },
    )
    captured = _install_fake_resume(monkeypatch)

    resp = await client.post(
        f"/v1/approvals/{approval_id}/approve",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert resp.status_code == 200
    assert captured["pending"]["args"] == args  # payload ORIGINAL, sin redactar
    assert captured["pending"]["args"]["api_key"] == "AKIA-original-secreto"


async def test_approve_rechaza_si_el_args_digest_no_coincide(
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
        tool_call_id="call_tampered",
        snapshot={
            "name": "publicar_social",
            "args": {"texto": "hola"},
            # Digest que no corresponde a los args: integridad rota.
            "args_digest": _args_digest({"texto": "ALTERADO"}),
        },
    )
    captured = _install_fake_resume(monkeypatch)

    resp = await client.post(
        f"/v1/approvals/{approval_id}/approve",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert resp.status_code == 409
    # Fail closed: la fila queda pendiente y la reanudación nunca se invoca.
    assert fake_session.approvals[str(approval_id)]["status"] == "pending"
    assert captured == {}
