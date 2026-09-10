"""`/v1/agents/messages` — protocolo inter-agente (product design).

Humo end-to-end contra un doble de sesión que entiende el SQL del router. No
abre conexión real: cubre montaje, contratos, aislamiento tenant y — desde
BOTS-03/BOTS-08 — el outbox transaccional y el envelope del job. El router se
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
    """Doble de `AsyncSession` con un modelo transaccional mínimo.

    Los `execute` de escritura van a un búfer pendiente; `commit()` lo promueve
    al estado durable y `rollback()` lo descarta — para poder probar la
    atomicidad de BOTS-08 (mensaje + job outbox se comprometen o ruedan atrás
    juntos). El fixture `client` invoca commit/rollback como el `get_session`
    real (commit al salir, rollback ante excepción).
    """

    def __init__(self) -> None:
        self.messages: dict[uuid.UUID, dict[str, Any]] = {}
        self.agents: dict[uuid.UUID, uuid.UUID | None] = {}
        self.outbox: list[dict[str, Any]] = []
        self.executed: list[str] = []
        self._pending_messages: dict[uuid.UUID, dict[str, Any]] = {}
        self._pending_outbox: list[dict[str, Any]] = []

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
        return row

    def _all_messages(self) -> dict[uuid.UUID, dict[str, Any]]:
        merged = dict(self.messages)
        merged.update(self._pending_messages)
        return merged

    def _visible_to(self, row: dict[str, Any], user_id: uuid.UUID) -> bool:
        """Modela el EXISTS del SQL real: el mensaje es visible para `user_id`
        solo si es dueño del emisor o del receptor (BOTS-04). Un emisor/receptor
        NULL no autoriza por sí solo."""
        sender = row.get("sender_agent_id")
        receiver = row.get("receiver_agent_id")
        sender_owned = sender is not None and self.agents.get(sender) == user_id
        receiver_owned = receiver is not None and self.agents.get(receiver) == user_id
        return sender_owned or receiver_owned

    async def execute(self, clause: Any, params: dict | None = None) -> _Rows:
        sql = str(clause)
        self.executed.append(sql)
        p = params or {}

        if "SELECT id FROM persistent_agents" in sql:
            agent_id = uuid.UUID(p["id"])
            # H1: el contrato real filtra por dueño (`user_id = :user_id`); el
            # fake lo honra para no esconder una suplantación de sender.
            owner = self.agents.get(agent_id)
            return _Rows(
                [{"id": agent_id}] if owner is not None and owner == uuid.UUID(p["user_id"]) else []
            )

        if "SELECT user_id FROM persistent_agents" in sql:
            agent_id = uuid.UUID(p["id"])
            owner = self.agents.get(agent_id)
            return _Rows([{"user_id": owner}] if owner is not None else [])

        if "INSERT INTO agent_messages" in sql:
            row = self._row_from_insert(p)
            self._pending_messages[row["id"]] = row
            return _Rows([row])

        if "INSERT INTO job_outbox" in sql:
            self._pending_outbox.append(
                {
                    "id": p["id"],
                    "tenant_id": p["tenant_id"],
                    "job_type": p["job_type"],
                    "payload": json.loads(p["payload"]),
                }
            )
            return _Rows([])

        if "UPDATE agent_messages" in sql:
            mid = uuid.UUID(p["id"])
            row = self._all_messages().get(mid)
            if row is not None and row["status"] in ("pending", "delivered"):
                row["status"] = "acknowledged"
                row["updated_at"] = _now()
            return _Rows([])

        if "FROM agent_messages" in sql and "tenant_id" in (p or {}):
            tenant_id = uuid.UUID(p["tenant_id"])
            user_id = uuid.UUID(p["user_id"])
            rows = [
                r for r in self._all_messages().values() if r.get("tenant_id") == tenant_id
            ]
            rows = [r for r in rows if self._visible_to(r, user_id)]
            if "id" in p and "status" not in p:
                # _get_one (lectura por id, filtro de dueño).
                row = next((r for r in rows if r["id"] == uuid.UUID(p["id"])), None)
                return _Rows([row] if row is not None else [])
            if p.get("status"):
                rows = [r for r in rows if r["status"] == p["status"]]
            if p.get("receiver"):
                receiver = uuid.UUID(p["receiver"])
                rows = [r for r in rows if r["receiver_agent_id"] == receiver]
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return _Rows(rows)

        return _Rows([])

    async def commit(self) -> None:
        self.messages.update(self._pending_messages)
        self.outbox.extend(self._pending_outbox)
        self._pending_messages = {}
        self._pending_outbox = []

    async def rollback(self) -> None:
        self._pending_messages = {}
        self._pending_outbox = []


@pytest.fixture
def fake_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def client(app, fake_session: _FakeSession) -> AsyncClient:
    app.include_router(agent_messages.router)

    async def _tenant_session():
        try:
            yield fake_session
            await fake_session.commit()
        except BaseException:
            await fake_session.rollback()
            raise

    app.dependency_overrides[edecan_deps.get_tenant_session] = _tenant_session
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_send_creates_message(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    receiver = uuid.uuid4()
    sender = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=user_id)
    fake_session.seed_agent(sender, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

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
    user_id = uuid.uuid4()
    receiver_a = uuid.uuid4()
    receiver_b = uuid.uuid4()
    fake_session.seed_agent(receiver_a, user_id=user_id)
    fake_session.seed_agent(receiver_b, user_id=user_id)
    fake_session.seed_message(
        message_id=uuid.uuid4(), tenant_id=tenant_id, receiver_agent_id=receiver_a, status="pending"
    )
    fake_session.seed_message(
        message_id=uuid.uuid4(), tenant_id=tenant_id, receiver_agent_id=receiver_a, status="done"
    )
    fake_session.seed_message(
        message_id=uuid.uuid4(), tenant_id=tenant_id, receiver_agent_id=receiver_b, status="pending"
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

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
    user_id = uuid.uuid4()
    receiver = uuid.uuid4()
    message_id = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=user_id)
    fake_session.seed_message(
        message_id=message_id, tenant_id=tenant_id,
        receiver_agent_id=receiver, status="pending",
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

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
    user_id = uuid.uuid4()
    receiver = uuid.uuid4()
    message_id = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=user_id)
    fake_session.seed_message(
        message_id=message_id, tenant_id=tenant_id,
        receiver_agent_id=receiver, status="done",
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    resp = await client.post(f"/v1/agents/messages/{message_id}/acknowledge", headers=headers)
    assert resp.status_code == 409


async def test_sin_autenticacion_401(client) -> None:
    resp = await client.get("/v1/agents/messages")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# BOTS-04: aislamiento entre usuarios del mismo tenant. Un mensaje con
# `sender_agent_id IS NULL` (asistente principal) ya NO autoriza por sí solo;
# manda el ownership del emisor/receptor. A/B mismo tenant, C otro tenant.
# ---------------------------------------------------------------------------


async def test_otro_usuario_mismo_tenant_no_lista_mensajes_ajenos(
    client, fake_session: _FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    bot_a = uuid.uuid4()
    fake_session.seed_agent(bot_a, user_id=user_a)
    fake_session.seed_message(
        message_id=uuid.uuid4(), tenant_id=tenant_id,
        receiver_agent_id=bot_a, status="pending",
    )
    headers_b = auth_headers(user_id=user_b, tenant_id=tenant_id)

    resp = await client.get("/v1/agents/messages", headers=headers_b)

    assert resp.status_code == 200
    assert resp.json() == []


async def test_dueno_si_lista_sus_mensajes_sender_null(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    user_a = uuid.uuid4()
    bot_a = uuid.uuid4()
    fake_session.seed_agent(bot_a, user_id=user_a)
    fake_session.seed_message(
        message_id=uuid.uuid4(), tenant_id=tenant_id,
        receiver_agent_id=bot_a, status="pending",
    )
    headers_a = auth_headers(user_id=user_a, tenant_id=tenant_id)

    resp = await client.get("/v1/agents/messages", headers=headers_a)

    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_otro_usuario_mismo_tenant_no_lee_mensaje_ajeno(
    client, fake_session: _FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    bot_a = uuid.uuid4()
    message_id = uuid.uuid4()
    fake_session.seed_agent(bot_a, user_id=user_a)
    fake_session.seed_message(
        message_id=message_id, tenant_id=tenant_id,
        receiver_agent_id=bot_a, status="pending",
    )
    headers_b = auth_headers(user_id=user_b, tenant_id=tenant_id)

    resp = await client.get(f"/v1/agents/messages/{message_id}", headers=headers_b)

    assert resp.status_code == 404


async def test_otro_usuario_mismo_tenant_no_acknowledge_mensaje_ajeno(
    client, fake_session: _FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    bot_a = uuid.uuid4()
    message_id = uuid.uuid4()
    fake_session.seed_agent(bot_a, user_id=user_a)
    fake_session.seed_message(
        message_id=message_id, tenant_id=tenant_id,
        receiver_agent_id=bot_a, status="pending",
    )
    headers_b = auth_headers(user_id=user_b, tenant_id=tenant_id)

    resp = await client.post(f"/v1/agents/messages/{message_id}/acknowledge", headers=headers_b)

    assert resp.status_code == 404


async def test_otro_tenant_no_lista_mensajes(client, fake_session: _FakeSession) -> None:
    tenant_a = uuid.uuid4()
    tenant_c = uuid.uuid4()
    user_a = uuid.uuid4()
    bot_a = uuid.uuid4()
    fake_session.seed_agent(bot_a, user_id=user_a)
    fake_session.seed_message(
        message_id=uuid.uuid4(), tenant_id=tenant_a,
        receiver_agent_id=bot_a, status="pending",
    )
    headers_c = auth_headers(user_id=user_a, tenant_id=tenant_c)

    resp = await client.get("/v1/agents/messages", headers=headers_c)

    assert resp.status_code == 200
    assert resp.json() == []


async def test_ciclo_completo_dueno_crea_lista_lee_y_acknowledge(
    client, fake_session: _FakeSession
) -> None:
    """Flujo positivo de A: crear → listar → leer → acknowledge, sin que B vea nada."""
    tenant_id = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    bot_a = uuid.uuid4()
    fake_session.seed_agent(bot_a, user_id=user_a)
    headers_a = auth_headers(user_id=user_a, tenant_id=tenant_id)
    headers_b = auth_headers(user_id=user_b, tenant_id=tenant_id)

    created = await client.post(
        "/v1/agents/messages",
        headers=headers_a,
        json={"message_type": "task", "receiver_agent_id": str(bot_a), "goal": "tarea de A"},
    )
    assert created.status_code == 201
    message_id = created.json()["id"]

    listed_a = await client.get("/v1/agents/messages", headers=headers_a)
    assert [m["id"] for m in listed_a.json()] == [message_id]

    read_a = await client.get(f"/v1/agents/messages/{message_id}", headers=headers_a)
    assert read_a.status_code == 200

    ack_a = await client.post(f"/v1/agents/messages/{message_id}/acknowledge", headers=headers_a)
    assert ack_a.status_code == 200
    assert ack_a.json()["status"] == "acknowledged"

    # B no ve el mensaje de A ni siquiera conociendo el id.
    listed_b = await client.get("/v1/agents/messages", headers=headers_b)
    assert listed_b.json() == []
    read_b = await client.get(f"/v1/agents/messages/{message_id}", headers=headers_b)
    assert read_b.status_code == 404


# ---------------------------------------------------------------------------
# H1 (auditoría ronda 1): el fake honra `user_id` en `_ensure_agent_exists`.
# Un usuario B no puede crear un mensaje firmando con el bot de A.
# ---------------------------------------------------------------------------


async def test_suplantacion_de_sender_negada(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    bot_a = uuid.uuid4()
    fake_session.seed_agent(bot_a, user_id=user_a)
    headers_b = auth_headers(user_id=user_b, tenant_id=tenant_id)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers_b,
        json={
            "message_type": "task",
            "sender_agent_id": str(bot_a),
            "goal": "me hago pasar por el bot de A",
        },
    )

    # El sender no es del usuario B → 404 (mismo gate que el receptor).
    assert resp.status_code == 404
    assert fake_session.messages == {}
    assert fake_session.outbox == []


# ---------------------------------------------------------------------------
# BOTS-08: el job (y la notificación) se escriben en el outbox EN LA MISMA
# sesión que el INSERT del mensaje — nada se despacha directo durante el
# request. BOTS-03: el payload del job lleva el envelope completo.
# ---------------------------------------------------------------------------


async def test_send_escribe_outbox_run_y_push_para_el_receptor(
    client, fake_session: _FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    receiver = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={
            "message_type": "task",
            "receiver_agent_id": str(receiver),
            "goal": "Revisa el informe",
            "allowed_tools": ["leer_archivos"],
        },
    )

    assert resp.status_code == 201
    assert len(fake_session.outbox) == 2
    job_types = {event["job_type"] for event in fake_session.outbox}
    assert job_types == {"run_persistent_agent", "notify_important_event"}

    run_job = next(e for e in fake_session.outbox if e["job_type"] == "run_persistent_agent")
    assert run_job["payload"]["worker_id"] == str(receiver)
    assert run_job["payload"]["instruction"] == "Revisa el informe"
    assert run_job["payload"]["task_id"] == resp.json()["id"]
    # BOTS-03: el envelope viaja dentro del payload del job.
    assert run_job["payload"]["envelope"]["allowed_tools"] == ["leer_archivos"]

    push_job = next(e for e in fake_session.outbox if e["job_type"] == "notify_important_event")
    assert push_job["payload"]["kind"] == "agent_bot_message"
    assert push_job["payload"]["event_id"] == resp.json()["id"]


async def test_send_escribe_outbox_en_la_misma_sesion(
    client, fake_session: _FakeSession, monkeypatch
) -> None:
    """El outbox se escribe con la MISMA sesión que el INSERT (transaccional)."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    receiver = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    seen: list[tuple[Any, str]] = []

    async def spy(session, *, tenant_id, job_type, payload):
        seen.append((session, job_type))
        return uuid.uuid4()

    monkeypatch.setattr(agent_messages, "enqueue_outbox", spy)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={"message_type": "task", "receiver_agent_id": str(receiver), "goal": "algo"},
    )

    assert resp.status_code == 201
    assert len(seen) == 2
    assert all(session is fake_session for session, _ in seen)


async def test_send_no_encola_run_para_tipos_informativos(
    client, fake_session: _FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    receiver = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={"message_type": "status", "receiver_agent_id": str(receiver), "goal": "todo bien"},
    )

    assert resp.status_code == 201
    assert [e["job_type"] for e in fake_session.outbox] == ["notify_important_event"]


async def test_send_encola_push_para_dueno_del_receptor(
    client, fake_session: _FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    receiver = uuid.uuid4()
    # BOTS-04: el emisor debe ser dueño del receptor; el push va a ese dueño.
    fake_session.seed_agent(receiver, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

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
    push_jobs = [e for e in fake_session.outbox if e["job_type"] == "notify_important_event"]
    assert len(push_jobs) == 1
    assert push_jobs[0]["payload"]["kind"] == "agent_bot_message"
    assert push_jobs[0]["payload"]["user_id"] == str(user_id)


async def test_send_no_encola_sin_receptor(client, fake_session: _FakeSession) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    resp = await client.post(
        "/v1/agents/messages", headers=headers, json={"message_type": "task", "goal": "algo"}
    )

    assert resp.status_code == 201
    assert fake_session.outbox == []


async def test_envelope_completo_en_el_job(client, fake_session: _FakeSession) -> None:
    """BOTS-03: allowed_tools/deadline/dependencies llegan al job para el runner."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    receiver = uuid.uuid4()
    dep_id = str(uuid.uuid4())
    fake_session.seed_agent(receiver, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={
            "message_type": "task",
            "receiver_agent_id": str(receiver),
            "goal": "Revisa",
            "allowed_tools": ["leer_archivos", "navegar_web"],
            "deadline": "2026-09-09T00:00:00Z",
            "dependencies": [dep_id],
        },
    )

    assert resp.status_code == 201
    run_job = next(e for e in fake_session.outbox if e["job_type"] == "run_persistent_agent")
    envelope = run_job["payload"]["envelope"]
    assert envelope["allowed_tools"] == ["leer_archivos", "navegar_web"]
    assert datetime.fromisoformat(envelope["deadline"]) == datetime(2026, 9, 9, tzinfo=UTC)
    assert envelope["dependencies"] == [dep_id]


async def test_rollback_no_deja_job_en_cola(client, fake_session: _FakeSession, monkeypatch) -> None:
    """BOTS-08: si un outbox posterior falla, el mensaje Y el job ruedan atrás."""
    from edecan_core.queue import enqueue_outbox as real_enqueue_outbox
    from sqlalchemy.exc import SQLAlchemyError

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    receiver = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    order = {"n": 0}

    async def flaky(session, *, tenant_id, job_type, payload):
        order["n"] += 1
        if order["n"] >= 2:
            raise SQLAlchemyError("notify outbox insert failed")
        return await real_enqueue_outbox(
            session, tenant_id=tenant_id, job_type=job_type, payload=payload
        )

    monkeypatch.setattr(agent_messages, "enqueue_outbox", flaky)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={"message_type": "task", "receiver_agent_id": str(receiver), "goal": "haz algo"},
    )

    assert resp.status_code == 500
    # Atomicidad: ni el mensaje ni el job run quedaron comprometidos.
    assert fake_session.messages == {}
    assert fake_session.outbox == []


async def test_fallo_de_enqueue_no_deja_mensaje_huerfano(
    client, fake_session: _FakeSession, monkeypatch
) -> None:
    """BOTS-08: un fallo al escribir el job outbox no deja 'pending' sin trabajo."""
    from sqlalchemy.exc import SQLAlchemyError

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    receiver = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    async def falla(session, *, tenant_id, job_type, payload):
        raise SQLAlchemyError("job_outbox insert failed")

    monkeypatch.setattr(agent_messages, "enqueue_outbox", falla)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={"message_type": "task", "receiver_agent_id": str(receiver), "goal": "haz algo"},
    )

    assert resp.status_code == 500
    # Sin mensaje huerfano: el fallo fue visible (500) y nada quedó a medias.
    assert fake_session.messages == {}
    assert fake_session.outbox == []


# ---------------------------------------------------------------------------
# F4/F5: contrato de `dependencies` — lista de UUID en texto, 422 si no calza.
# ---------------------------------------------------------------------------


async def test_send_acepta_dependencies_uuid_strings(
    client, fake_session: _FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    receiver = uuid.uuid4()
    dep_a = uuid.uuid4()
    dep_b = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={
            "message_type": "task",
            "receiver_agent_id": str(receiver),
            "goal": "Revisa",
            "dependencies": [str(dep_a), str(dep_b)],
        },
    )

    assert resp.status_code == 201
    run_job = next(e for e in fake_session.outbox if e["job_type"] == "run_persistent_agent")
    assert run_job["payload"]["envelope"]["dependencies"] == [str(dep_a), str(dep_b)]


@pytest.mark.parametrize(
    "dependencies",
    [
        [{"task_id": "dep-1", "state": "done"}],
        "dep-1",
        ["dep-1"],
        [str(uuid.uuid4()), "no-es-uuid"],
        42,
    ],
)
async def test_send_rechaza_dependencies_shape_invalido(
    client, fake_session: _FakeSession, dependencies: Any
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    receiver = uuid.uuid4()
    fake_session.seed_agent(receiver, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    resp = await client.post(
        "/v1/agents/messages",
        headers=headers,
        json={
            "message_type": "task",
            "receiver_agent_id": str(receiver),
            "goal": "Revisa",
            "dependencies": dependencies,
        },
    )

    assert resp.status_code == 422
    # Nada persistido: el contrato se rechaza ANTES de escribir.
    assert fake_session.messages == {}
    assert fake_session.outbox == []