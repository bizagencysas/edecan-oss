"""Tests de `edecan_core.companion_wake`: quiet hours, silencio e idempotencia."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from edecan_core.companion_wake import (
    SILENCE_SENTINEL,
    companion_always_on,
    companion_push_title,
    format_push_preview,
    is_pulse_window,
    is_quiet_hours,
    is_substantive_assistant_text,
    pulse_wake_key,
    record_companion_wake,
    should_run_wake,
    stable_event_id,
)

_BOGOTA = ZoneInfo("America/Bogota")


class _FakeResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self):
        self.claims: set[str] = set()
        self.preferences_meta: dict[str, Any] | None = None

    async def execute(self, statement, params):
        sql = str(statement)
        params = dict(params)
        if "pg_advisory_xact_lock" in sql:
            return _FakeResult()
        if params.get("action") == "notifications.preferences.updated":
            if self.preferences_meta is not None:
                return _FakeResult([{"meta": self.preferences_meta}])
            return _FakeResult()
        if "SELECT id" in sql and "audit_log" in sql:
            target = params.get("target", "")
            return _FakeResult([{"id": uuid.uuid4()}] if target in self.claims else [])
        if "INSERT INTO audit_log" in sql:
            if params.get("action") == "notifications.preferences.updated":
                import json

                self.preferences_meta = json.loads(params["meta"])
            else:
                self.claims.add(params["target"])
            return _FakeResult()
        return _FakeResult()


def test_is_quiet_hours_bogota_window():
    # 23:00 Bogotá = quiet
    instant = datetime(2026, 8, 27, 4, 0, tzinfo=UTC)  # 23:00 -05
    assert is_quiet_hours(instant) is True
    # 10:00 Bogotá = not quiet
    instant = datetime(2026, 8, 27, 15, 0, tzinfo=UTC)
    assert is_quiet_hours(instant) is False


def test_should_run_wake_respects_urgent_and_opt_out():
    quiet = datetime(2026, 8, 27, 4, 0, tzinfo=UTC)
    assert should_run_wake(now=quiet, urgent=False, companion_enabled=True) is False
    assert should_run_wake(now=quiet, urgent=True, companion_enabled=False) is True
    assert should_run_wake(now=quiet, urgent=False, companion_enabled=False) is False


def test_is_pulse_window_bogota_hours():
    # 10:00 Bogotá = in pulse window
    assert is_pulse_window(datetime(2026, 8, 27, 15, 0, tzinfo=UTC)) is True
    # 23:00 Bogotá = outside pulse window
    assert is_pulse_window(datetime(2026, 8, 27, 4, 0, tzinfo=UTC)) is False
    # 07:00 Bogotá = outside pulse window (before 8)
    assert is_pulse_window(datetime(2026, 8, 27, 12, 0, tzinfo=UTC)) is False


def test_pulse_wake_key_is_hourly():
    instant = datetime(2026, 8, 27, 15, 30, tzinfo=UTC)  # 10:30 Bogotá
    assert pulse_wake_key(instant) == "pulse:2026-08-27-10"


def test_format_push_preview_one_line_and_truncates():
    assert format_push_preview("  hola\nmundo  ") == "hola mundo"
    long = "a" * 200
    preview = format_push_preview(long, max_chars=160)
    assert len(preview) == 160
    assert preview.endswith("…")


def test_companion_push_title_for_phone_call():
    assert companion_push_title("phone_call_finished") == "Llamada"
    assert companion_push_title(None) == "Edecán"


async def test_companion_always_on_defaults_true_without_preferences():
    session = _Session()
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    assert await companion_always_on(session, tenant_id=tenant_id, user_id=user_id) is True


async def test_companion_always_on_opt_out_via_preferences():
    session = _Session()
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    session.preferences_meta = {"companion_24_7": False}
    assert await companion_always_on(session, tenant_id=tenant_id, user_id=user_id) is False


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", False),
        ("   ", False),
        (SILENCE_SENTINEL, False),
        ("[no_message]", False),
        ("Hay una aprobación pendiente.", True),
    ],
)
def test_is_substantive_assistant_text(text: str, expected: bool):
    assert is_substantive_assistant_text(text) is expected


async def test_record_companion_wake_is_idempotent():
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    session = _Session()
    wake_key = "approval:1"

    first = await record_companion_wake(
        session, tenant_id=tenant_id, user_id=user_id, wake_key=wake_key
    )
    second = await record_companion_wake(
        session, tenant_id=tenant_id, user_id=user_id, wake_key=wake_key
    )

    assert first is True
    assert second is False


def test_stable_event_id_is_deterministic():
    tenant_id = uuid.uuid4()
    a = stable_event_id(tenant_id=tenant_id, wake_key="x")
    b = stable_event_id(tenant_id=tenant_id, wake_key="x")
    c = stable_event_id(tenant_id=tenant_id, wake_key="y")
    assert a == b
    assert a != c
