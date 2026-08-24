from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

import edecan_worker.handlers.notify_incoming_phone_call as handler_module
import edecan_worker.universal_notifications as universal_notifications
from edecan_core.notifications import save_notification_preferences
from edecan_schemas import JobEnvelope
from fakes import FakeRepo, make_deps


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


class _NotificationSession:
    def __init__(self) -> None:
        self.audit: list[dict[str, Any]] = []

    async def execute(self, clause: Any, params: dict[str, Any]) -> _Result:
        statement = str(clause)
        if "pg_advisory_xact_lock" in statement:
            return _Result()
        if statement.lstrip().startswith("SELECT meta"):
            rows = [
                row
                for row in reversed(self.audit)
                if row["tenant_id"] == params["tenant_id"]
                and row["user_id"] == params["user_id"]
                and row["action"] == params["action"]
            ]
            return _Result([{"meta": row["meta"]} for row in rows[:1]])
        if statement.lstrip().startswith("SELECT id"):
            rows = [
                row
                for row in self.audit
                if row["tenant_id"] == params["tenant_id"]
                and row["action"] == params["action"]
                and row["target"] == params["target"]
            ]
            return _Result([{"id": row["id"]} for row in rows[:1]])
        if statement.lstrip().startswith("INSERT INTO audit_log"):
            self.audit.append(
                {
                    "id": params["id"],
                    "tenant_id": params["tenant_id"],
                    "user_id": params["user_id"],
                    "action": params["action"],
                    "target": params["target"],
                    "meta": json.loads(params["meta"]),
                }
            )
            return _Result()
        raise AssertionError(statement)


def _envelope(tenant_id: uuid.UUID, call_id: uuid.UUID) -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="notify_incoming_phone_call",
        payload={"call_id": str(call_id)},
    )


def _arrange(monkeypatch):
    tenant_id, user_id, call_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    repo = FakeRepo()
    repo.phone_calls[call_id] = {
        "id": call_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "direction": "incoming",
        "from_e164": "+573009999999",
        "goal": "Texto privado de la llamada",
    }
    repo.phone_call_events.append(
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "call_id": call_id,
            "event_type": "incoming",
            "payload": {"status": "in_progress"},
        }
    )
    monkeypatch.setattr(handler_module, "SqlRepo", lambda _session: repo)
    session = _NotificationSession()

    @asynccontextmanager
    async def factory(_tenant_id):
        yield session

    return tenant_id, user_id, call_id, session, make_deps(session_factory=factory)


async def test_webhook_redelivery_produces_one_durable_event_and_one_safe_push(
    monkeypatch,
) -> None:
    tenant_id, user_id, call_id, session, deps = _arrange(monkeypatch)
    pushes: list[dict[str, Any]] = []

    async def send(_deps, **kwargs):
        pushes.append(kwargs)
        return universal_notifications.push.ResultadoEnvioPush(enviados=1, fallidos=0)

    monkeypatch.setattr(universal_notifications.push, "enviar_push_a_usuario", send)
    env = _envelope(tenant_id, call_id)
    await handler_module.handle(env, deps)
    await handler_module.handle(env, deps)

    events = [row for row in session.audit if row["action"] == "notifications.event"]
    assert len(events) == 1
    assert events[0]["meta"] == {
        "version": 1,
        "category": "work",
        "kind": "phone_call_incoming",
        "event_key": f"phone_call_incoming:{call_id}",
        "route": "activity",
        "resource_id": str(call_id),
    }
    assert len(pushes) == 1
    push = pushes[0]
    assert push["tenant_id"] == tenant_id
    assert push["user_id"] == user_id
    visible = f"{push['titulo']} {push['cuerpo']}"
    assert push["titulo"] == "Llamada entrante"
    assert "Actividad" in visible
    assert "+573009999999" not in visible
    assert "Texto privado" not in visible
    assert push["data"] == {
        "route": "activity",
        "kind": "mission",
        "event": "phone_call_incoming",
        "event_key": f"phone_call_incoming:{call_id}",
        "resource_id": str(call_id),
        "deeplink": f"edecan://activity/{call_id}",
    }


async def test_push_failure_keeps_event_and_does_not_retry_duplicate(monkeypatch) -> None:
    tenant_id, _user_id, call_id, session, deps = _arrange(monkeypatch)
    attempts = 0

    async def broken_push(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("proveedor push caído")

    monkeypatch.setattr(
        universal_notifications.push, "enviar_push_a_usuario", broken_push
    )
    env = _envelope(tenant_id, call_id)
    await handler_module.handle(env, deps)
    await handler_module.handle(env, deps)

    assert attempts == 1
    assert len(
        [row for row in session.audit if row["action"] == "notifications.event"]
    ) == 1


async def test_disabled_work_preference_records_activity_without_push(monkeypatch) -> None:
    tenant_id, user_id, call_id, session, deps = _arrange(monkeypatch)
    await save_notification_preferences(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        categories={"work": False},
    )

    async def forbidden_push(*_args, **_kwargs):
        raise AssertionError("No debe enviar push con la preferencia desactivada")

    monkeypatch.setattr(
        universal_notifications.push, "enviar_push_a_usuario", forbidden_push
    )
    await handler_module.handle(_envelope(tenant_id, call_id), deps)

    events = [row for row in session.audit if row["action"] == "notifications.event"]
    assert len(events) == 1
    assert events[0]["meta"]["kind"] == "phone_call_incoming"


async def test_missing_durable_incoming_event_never_notifies(monkeypatch) -> None:
    tenant_id, _user_id, call_id, session, deps = _arrange(monkeypatch)
    repo = handler_module.SqlRepo(None)
    repo.phone_call_events.clear()

    async def forbidden_notify(*_args, **_kwargs):
        raise AssertionError("No debe notificar antes del evento durable")

    monkeypatch.setattr(handler_module, "notify_important_event", forbidden_notify)
    await handler_module.handle(_envelope(tenant_id, call_id), deps)
    assert session.audit == []
