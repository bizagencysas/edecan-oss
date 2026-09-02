"""`/v1/agents/messages` — protocolo inter-agente (product design).

Humo end-to-end contra un doble de sesión que entiende el SQL del router. No
abre conexión real: cubre montaje, contratos y aislamiento tenant. El router se
monta a mano sobre el `app` fixture porque `main.py` aún no lo registra (el
dueño lo monta aparte).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

import edecan_api.deps as edecan_deps
from edecan_api.routers import agent_messages


def _now() -> datetime:
    return datetime.now(UTC)


class _Rows(list):
    def mappings(self):
        return self

    def all(self):
        return list(self)

    def first(self):
        return self[0] if self else None


def _parse_uuid(value: Any) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


class _FakeSession:
    def __init__(self) -> None:
        self.messages: dict[uuid.UUID, dict[str, Any]] = {}
        self.agents: dict[uuid.UUID, uuid.UUID | None] = {}
        self.executed: list[str] = []

    def seed_agent(self, agent_id: uuid.UUID, *, user_id: uuid.UUID | None = None) -> None:
        self.agents[agent_id] = user_id

    def seed_message(
        self, *, message_id: uuid.UUID, status: str = "pending", **fields: Any
    ) -> None:
        row: dict[str, Any] = {
            "id": message_id,
            "tenant_id": fields.pop("tenant_id", uuid.uuid4()),
            "sender_agent_id": fields.pop("sender_agent_id", None),
            "receiver_agent_id": fields.pop("receiver_agent_id", None),
            "task_id": fields.pop("task_id", None),
            "parent_task_id": fields.pop("parent_task_id", None),
            "conversation_id": fields.pop("conversation_id", None),
            "message_type": fields.pop("message_type", "task"),
            "goal": fields.pop("goal", None),
            "expected_output": fields.pop("expected_output", None),
            "priority": fields.pop("priority", None),
            "deadline": fields.pop("deadline", None),
            "dependencies": fields.pop("dependencies", None),
            "allowed_tools": fields.pop("allowed_tools", None),
            "approval_boundary": fields.pop("approval_boundary", None),
            "artifact_refs": fields.pop("artifact_refs", None),
            "context_refs": fields.pop("context_refs", None),
            "status": status,
            "created_at": _now(),
            "updated_at": _now(),
        }
        row.update(fields)
        self.messages[message_id] = row

    def _row_from_insert(self, params: dict[str, Any]) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": uuid.uuid4(),
            "tenant_id": _parse_uuid(params.get("tenant_id")),
            "sender_agent_id": _parse_uuid(params.get("sender")),
            "receiver_agent_id": _parse_uuid(params.get("receiver")),
            "task_id": params.get("task_id"),
            "parent_task_id": params.get("parent_task_id"),
            "conversation_id": _parse_uuid(params.get("conversation_id")),
            "message_type": params.get("message_type"),
            "goal": params.get("goal"),
            "expected_output": params.get("expected_output"),
            "priority": params.get("priority"),
            "deadline": params.get("deadline"),
            "dependencies": (
                json.loads(params["dependencies"]) if params.get("dependencies") else None
            ),
            "allowed_tools": (
                json.loads(params["allowed_tools"]) if params.get("allowed_tools") else None
            ),
            "approval_boundary": (
                json.loads(params["approval_boundary"]) if params.get("approval_boundary") else None
            ),
            "artifact_refs": (
                json.loads(params["artifact_refs"]) if params.get("artifact_refs") else None
            ),
            "context_refs": (
                json.loads(params["context_refs"]) if params.get("context_refs") else None
            ),
            "status": "pending",
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.messages[row["id"]] = row
        return row

    async def execute(self, clause: Any, params: dict | None = None) -> _Rows:
        sql = str(clause)
        self.executed.append(sql)
        p = params or {}

        if "SELECT id FROM persistent_agents" in sql:
            agent_id = uuid.UUID(p["id"])
            # el nuevo contrato filtra por dueño: si el test sembró el agente,
            # pertenece al dueño de la sesión (los fakes no modelan multi-user)
            return _Rows([{"id": agent_id}] if agent_id in self.agents else [])

        if "SELECT user_id FROM persistent_agents" in sql:
            agent_id = uuid.UUID(p["id"])
            owner = self.agents.get(agent_id)
            return _Rows([{"user_id": owner}] if owner is not None else [])

        if "INSERT INTO agent_messages" in sql:
            return _Rows([self._row_from_insert(p)])

        if "UPDATE agent_messages" in sql:
            mid = uuid.UUID(p["id"])
            row = self.messages.get(mid)
            if row is not None and row["status"] in ("pending", "delivered"):
                row["status"] = "acknowledged"
                row["updated_at"] = _now()
            return _Rows([])

        if "FROM agent_messages" in sql and "tenant_id" in (p or {}):
            if "id" in p and "status" not in p and "user_id" in p:
                # _get_one (con filtro de dueño): el fake siembra agentes del
                # dueño, así que el EXISTS pasa.
                row = self.messages.get(uuid.UUID(p["id"]))
                return _Rows([row] if row else [])
            rows = list(self.messages.values())
            if p.get("status"):
                rows = [r for r in rows if r["status"] == p["status"]]
            if p.get("receiver"):
                receiver = uuid.UUID(p["receiver"])
                rows = [r for r in rows if r["receiver_agent_id"] == receiver]
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return _Rows(rows)

        return _Rows([])


@pytest.fixture
def fake_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def client(app, fake_session: _FakeSession) -> AsyncClient:
    app.include_router(agent_messages.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_send_creates_message(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    receiver = uuid.uuid4()
    sender = uuid.uuid4()
    fake_session.seed_agent(receiver)
    fake_session.seed_agent(sender)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={
            "message_type": "TASK",
            "sender_agent_id": str(sender),
            "receiver_agent_id": str(receiver),
            "task_id": "task-1",
            "goal": "Revisa el informe",
            "priority": "alta",
            "allowed_tools": ["leer_archivos"],
            "context_refs": [{"kind": "message", "id": str(uuid.uuid4())}],
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["message_type"] == "task"  # normalizado a minúsculas
    assert body["sender_agent_id"] == str(sender)
    assert body["receiver_agent_id"] == str(receiver)
    assert body["task_id"] == "task-1"
    assert body["goal"] == "Revisa el informe"
    assert body["priority"] == "alta"
    assert body["status"] == "pending"
    assert body["allowed_tools"] == ["leer_archivos"]
    assert body["context_refs"][0]["kind"] == "message"


async def test_send_rechaza_receiver_inexistente(client, fake_session: _FakeSession) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={"message_type": "task", "receiver_agent_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


async def test_send_rechaza_tipo_invalido(client, fake_session: _FakeSession) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    resp = await client.post(
        "/v1/agents/messages", headers=headers, json={"message_type": "bogus"}
    )
    assert resp.status_code == 422


async def test_list_messages_filtra_por_status_y_receiver(
    client, fake_session: _FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    receiver_a = uuid.uuid4()
    receiver_b = uuid.uuid4()
    fake_session.seed_message(
        message_id=uuid.uuid4(), tenant_id=tenant_id, receiver_agent_id=receiver_a, status="pending"
    )
    fake_session.seed_message(
        message_id=uuid.uuid4(), tenant_id=tenant_id, receiver_agent_id=receiver_a, status="done"
    )
    fake_session.seed_message(
        message_id=uuid.uuid4(), tenant_id=tenant_id, receiver_agent_id=receiver_b, status="pending"
    )
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)

    resp = await client.get("/v1/agents/messages", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 3

    resp = await client.get("/v1/agents/messages", headers=headers, params={"status": "pending"})
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp = await client.get(
        "/v1/agents/messages", headers=headers, params={"receiver": str(receiver_a)}
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_get_message_y_acknowledge(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    message_id = uuid.uuid4()
    fake_session.seed_message(message_id=message_id, tenant_id=tenant_id, status="pending")
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)

    resp = await client.get(f"/v1/agents/messages/{message_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    resp = await client.post(f"/v1/agents/messages/{message_id}/acknowledge", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "acknowledged"


async def test_get_message_no_encontrado_404(client, fake_session: _FakeSession) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    resp = await client.get(f"/v1/agents/messages/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


async def test_acknowledge_estado_final_409(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    message_id = uuid.uuid4()
    fake_session.seed_message(message_id=message_id, tenant_id=tenant_id, status="done")
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)

    resp = await client.post(f"/v1/agents/messages/{message_id}/acknowledge", headers=headers)
    assert resp.status_code == 409


async def test_sin_autenticacion_401(client) -> None:
    resp = await client.get("/v1/agents/messages")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Inter-agent runtime (product design): el envío a un worker ejecutable
# encola `run_persistent_agent` para el RECEPTOR.
# ---------------------------------------------------------------------------


async def test_send_encola_run_persistent_agent_para_el_receptor(
    client, fake_session: _FakeSession, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    receiver = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=uuid.uuid4())
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)

    enqueued: list[tuple[str, dict, Any]] = []

    async def fake_enqueue(_settings, job_type, payload, tenant):
        enqueued.append((job_type, payload, tenant))

    monkeypatch.setattr(agent_messages, "enqueue", fake_enqueue)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={
            "message_type": "task",
            "receiver_agent_id": str(receiver),
            "goal": "Revisa el informe",
        },
    )

    assert resp.status_code == 201
    assert len(enqueued) == 2
    job_types = {item[0] for item in enqueued}
    assert job_types == {"run_persistent_agent", "notify_important_event"}
    run_job = next(item for item in enqueued if item[0] == "run_persistent_agent")
    job_type, payload, tenant = run_job
    assert job_type == "run_persistent_agent"
    assert payload["worker_id"] == str(receiver)
    assert payload["instruction"] == "Revisa el informe"
    assert payload["task_id"] == resp.json()["id"]
    push_job = next(item for item in enqueued if item[0] == "notify_important_event")
    _, push_payload, _ = push_job
    assert push_payload["kind"] == "agent_message"
    assert push_payload["event_id"] == resp.json()["id"]


async def test_send_no_encola_run_persistent_agent_para_tipos_informativos(
    client, fake_session: _FakeSession, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    receiver = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=uuid.uuid4())
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)

    enqueued: list[str] = []

    async def fake_enqueue(_settings, job_type, _payload, _tenant):
        enqueued.append(job_type)

    monkeypatch.setattr(agent_messages, "enqueue", fake_enqueue)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={"message_type": "status", "receiver_agent_id": str(receiver), "goal": "todo bien"},
    )

    assert resp.status_code == 201
    assert enqueued == ["notify_important_event"]


async def test_send_encola_push_agent_message_para_dueno_del_receptor(
    client, fake_session: _FakeSession, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    receiver = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=owner_id)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)

    enqueued: list[tuple[str, dict, Any]] = []

    async def fake_enqueue(_settings, job_type, payload, tenant):
        enqueued.append((job_type, payload, tenant))

    monkeypatch.setattr(agent_messages, "enqueue", fake_enqueue)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={
            "message_type": "status",
            "receiver_agent_id": str(receiver),
            "goal": "Actualización",
        },
    )

    assert resp.status_code == 201
    push_jobs = [item for item in enqueued if item[0] == "notify_important_event"]
    assert len(push_jobs) == 1
    _, payload, _ = push_jobs[0]
    assert payload["kind"] == "agent_message"
    assert payload["user_id"] == str(owner_id)


async def test_send_no_encola_sin_receptor(client, fake_session: _FakeSession, monkeypatch) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    enqueued: list[str] = []

    async def fake_enqueue(_settings, job_type, _payload, _tenant):
        enqueued.append(job_type)

    monkeypatch.setattr(agent_messages, "enqueue", fake_enqueue)

    resp = await client.post(
        "/v1/agents/messages", headers=headers, json={"message_type": "task", "goal": "algo"}
    )

    assert resp.status_code == 201
    assert enqueued == []


async def test_send_no_falla_si_el_encolado_falla(
    client, fake_session: _FakeSession, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    receiver = uuid.uuid4()
    fake_session.seed_agent(receiver)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)

    async def fake_enqueue(_settings, _job_type, _payload, _tenant):
        raise RuntimeError("SQS caído")

    monkeypatch.setattr(agent_messages, "enqueue", fake_enqueue)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={"message_type": "task", "receiver_agent_id": str(receiver), "goal": "Revisa"},
    )

    # Best-effort: el encolado falla, pero el mensaje SÍ queda guardado.
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"
