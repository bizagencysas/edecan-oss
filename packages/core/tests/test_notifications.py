from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from edecan_core.notifications import (
    ImportantNotificationEvent,
    get_notification_preferences,
    record_notification_event,
    save_notification_preferences,
)


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


class _Session:
    def __init__(self) -> None:
        self.audit: list[dict[str, Any]] = []
        self.sql: list[str] = []

    async def execute(self, clause: Any, params: dict[str, Any]) -> _Result:
        statement = str(clause)
        self.sql.append(statement)
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


async def test_event_is_idempotent_and_contains_only_safe_metadata() -> None:
    session = _Session()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    chat_id = uuid.uuid4()
    event = ImportantNotificationEvent(
        tenant_id=tenant_id,
        user_id=user_id,
        kind="content_created",
        event_id=uuid.UUID("00000000-0000-0000-0000-000000000123"),
        chat_id=chat_id,
    )

    first = await record_notification_event(session, event)
    duplicate = await record_notification_event(session, event)

    assert first.created is True
    assert duplicate.created is False
    events = [row for row in session.audit if row["action"] == "notifications.event"]
    assert len(events) == 1
    assert events[0]["meta"] == {
        "version": 1,
        "category": "content",
        "kind": "content_created",
        "event_key": "content_created:00000000-0000-0000-0000-000000000123",
        "route": "assistant",
        "chat_id": str(chat_id),
    }
    serialized = json.dumps(events[0], default=str)
    assert "prompt" not in serialized
    assert "filename" not in serialized
    assert "error" not in serialized


async def test_preferences_default_and_partial_update_preserve_other_categories() -> None:
    session = _Session()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    defaults = await get_notification_preferences(
        session, tenant_id=tenant_id, user_id=user_id
    )
    assert defaults == {
        "work": True,
        "content": True,
        "design": True,
        "files": True,
        "self_repair": True,
    }

    changed = await save_notification_preferences(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        categories={"content": False},
    )
    changed_again = await save_notification_preferences(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        categories={"files": False},
    )

    assert changed["content"] is False
    assert changed["files"] is True
    assert changed_again["content"] is False
    assert changed_again["files"] is False


async def test_disabled_category_is_returned_with_durable_event() -> None:
    session = _Session()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await save_notification_preferences(
        session, tenant_id=tenant_id, user_id=user_id, categories={"design": False}
    )

    durable = await record_notification_event(
        session,
        ImportantNotificationEvent(
            tenant_id=tenant_id,
            user_id=user_id,
            kind="design_export_ready",
            event_id=uuid.uuid4(),
            artifact_id=uuid.uuid4(),
        ),
    )

    assert durable.created is True
    assert durable.push_enabled is False


def test_event_rejects_non_uuid_occurrence_and_ambiguous_destination() -> None:
    with pytest.raises(ValueError):
        ImportantNotificationEvent(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            kind="work_completed",
            event_id="esto contiene información privada",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        ImportantNotificationEvent(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            kind="design_ready",
            event_id=uuid.uuid4(),
            chat_id=uuid.uuid4(),
            artifact_id=uuid.uuid4(),
        )
