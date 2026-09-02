"""Tests de `companion_wake_scan`: solo encola turnos reales, nunca escribe chat."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import edecan_worker.handlers.companion_wake_scan as scan_module
import pytest
from edecan_schemas import JobEnvelope
from fakes import install_fake_edecan_core_queue, make_deps


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]


class FakeSession:
    def __init__(
        self,
        pending: list[dict[str, Any]] | None = None,
        owners: list[dict[str, Any]] | None = None,
    ) -> None:
        self.pending = pending or []
        self.owners = owners or []

    async def execute(self, clause: Any, _params: dict[str, Any] | None = None) -> _FakeResult:
        sql = str(clause)
        if "pending_approvals" in sql:
            return _FakeResult(self.pending)
        if "memberships" in sql:
            return _FakeResult(self.owners)
        return _FakeResult()


async def test_scan_enqueues_run_companion_turn_for_pending_approvals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    approval_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    enqueued: list[tuple[str, dict[str, Any], uuid.UUID]] = []

    async def fake_enqueue(_settings, job_type, payload, tid):
        enqueued.append((job_type, payload, tid))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)

    session = FakeSession(
        [
            {
                "id": approval_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
            }
        ]
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def session_factory(_tenant_id: uuid.UUID | None):
        yield session

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=None,
        type="companion_wake_scan",
        payload={},
    )

    await scan_module.handle(env, make_deps(session_factory=session_factory))

    assert len(enqueued) == 1
    job_type, payload, tid = enqueued[0]
    assert job_type == "run_companion_turn"
    assert tid == tenant_id
    assert payload["user_id"] == str(user_id)
    assert payload["wake_key"] == f"approval:{approval_id}"
    assert payload["urgent"] is True
    assert payload["source"] == "pending_approval"


async def test_scan_rejects_tenant_scoped_job() -> None:
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        type="companion_wake_scan",
        payload={},
    )
    with pytest.raises(ValueError, match="global"):
        await scan_module.handle(env, make_deps())


async def test_scan_enqueues_hourly_pulse_during_waking_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    enqueued: list[tuple[str, dict[str, Any], uuid.UUID]] = []

    async def fake_enqueue(_settings, job_type, payload, tid):
        enqueued.append((job_type, payload, tid))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)
    monkeypatch.setattr(
        scan_module,
        "datetime",
        type(
            "DT",
            (),
            {"now": staticmethod(lambda tz=None: datetime(2026, 8, 27, 15, 0, tzinfo=UTC))},
        ),
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def session_factory(_tenant_id: uuid.UUID | None):
        yield FakeSession(owners=[{"tenant_id": tenant_id, "user_id": user_id}])

    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=None, type="companion_wake_scan", payload={})
    await scan_module.handle(env, make_deps(session_factory=session_factory))

    assert len(enqueued) == 1
    job_type, payload, tid = enqueued[0]
    assert job_type == "run_companion_turn"
    assert tid == tenant_id
    assert payload["wake_key"] == "pulse:2026-08-27-10"
    assert payload["source"] == "companion_pulse"
