"""Regression tests for BOTS-01/07/16 runtime fixes in run_persistent_agent.

Covers three auditoría findings, each narrowed to the claim/budget/heartbeat
machinery of `run_persistent_agent.py`:

- BOTS-07: a durable run in a terminal state is never re-claimed.
- BOTS-16: `_BudgetedProvider.complete` reconciles its reservation on every
  terminal path (success without usage, error, CancelledError).
- BOTS-01: the heartbeat aborts the turn when the worker is paused mid-run.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from edecan_worker.handlers.run_persistent_agent import (
    _BudgetedProvider,
    _claim_bot_run,
    _heartbeat,
    _run_with_pause_abort,
    _RunBudgetGuard,
    _RunInterrupted,
)


class _Result:
    def __init__(self, rows: Any = None, *, rowcount: int = 1) -> None:
        if isinstance(rows, dict):
            rows = [rows]
        self.rows = list(rows or [])
        self.rowcount = rowcount

    def mappings(self) -> _Result:
        return self

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def all(self) -> list[Any]:
        return self.rows


class _Context:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *_args: Any) -> bool:
        return False


class _Deps:
    settings = SimpleNamespace()

    def __init__(self, session: Any) -> None:
        self.session = session

    def session_factory(self, _tenant: Any) -> _Context:
        return _Context(self.session)


def _budget_session(events: list[tuple[str, dict[str, Any]]]) -> Any:
    """A session fake that records run_events rows and serves before_call."""

    class BudgetSession:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            sql = str(statement)
            if "SELECT id FROM bot_runs" in sql:
                return _Result({"id": uuid.uuid4()})
            if "actual_compute" in sql:
                return _Result(
                    {
                        "actual_compute": 0,
                        "actual_money": 0,
                        "reserved_compute": 0,
                        "released_compute": 0,
                        "reserved_money": 0,
                        "released_money": 0,
                        "tools": 0,
                    }
                )
            if "MAX(seq)" in sql:
                return _Result({"seq": len(events)})
            if "INSERT INTO run_events" in sql:
                events.append((params["type"], json.loads(params["payload"])))
                return _Result(rowcount=1)
            return _Result(rowcount=1)

    return BudgetSession()


class _Provider:
    def __init__(
        self,
        *,
        response: Any = None,
        error: BaseException | None = None,
    ) -> None:
        self._response = response
        self._error = error

    async def complete(self, _request: Any) -> Any:
        if self._error is not None:
            raise self._error
        return self._response


# ---------------------------------------------------------------------------
# BOTS-07 — a terminal run is never re-claimed
# ---------------------------------------------------------------------------


class _TerminalRunSession:
    """Simulates a bot_runs row already in a terminal state."""

    active = False

    def __init__(self, status: str) -> None:
        self.status = status
        self.sql: list[str] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        sql = str(statement)
        self.sql.append(sql)
        if "INSERT INTO bot_runs" in sql:
            return _Result(rowcount=0)
        if "UPDATE bot_runs SET status = 'running'" in sql:
            # Terminal run: the conditional claim matches no row.
            return _Result(rowcount=0)
        if "SELECT status FROM bot_runs" in sql:
            return _Result({"status": self.status})
        return _Result(rowcount=1)


@pytest.mark.asyncio
async def test_bots07_terminal_run_is_not_reclaimed() -> None:
    tenant_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    session = _TerminalRunSession("succeeded")

    claim = await _claim_bot_run(
        _Deps(session),
        tenant_id=tenant_id,
        worker={"id": worker_id, "conversation_id": None},
        run_key="run:done",
        origin="agent_message",
        lease_seconds=120.0,
    )

    assert claim.terminal
    assert not claim.claimed
    assert claim.generation == 0
    # The atomic claim must be conditional on recoverable states only.
    claim_sql = next(sql for sql in session.sql if "UPDATE bot_runs" in sql)
    assert "status = 'queued'" in claim_sql
    assert "lease_expires_at < now()" in claim_sql
    assert "RETURNING claim_generation" in claim_sql


@pytest.mark.asyncio
async def test_bots07_fresh_run_is_claimed_with_generation() -> None:
    class FreshSession:
        active = False

        def __init__(self) -> None:
            self.sql: list[str] = []

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            sql = str(statement)
            self.sql.append(sql)
            if "INSERT INTO bot_runs" in sql:
                return _Result(rowcount=1)
            if "UPDATE bot_runs SET status = 'running'" in sql:
                return _Result({"claim_generation": 3})
            return _Result(rowcount=1)

    session = FreshSession()
    claim = await _claim_bot_run(
        _Deps(session),
        tenant_id=uuid.uuid4(),
        worker={"id": uuid.uuid4(), "conversation_id": None},
        run_key="run:fresh",
        origin="agent_message",
        lease_seconds=120.0,
    )

    assert claim.claimed
    assert not claim.terminal
    assert claim.durability_enabled
    assert claim.generation == 3


# ---------------------------------------------------------------------------
# BOTS-16 — complete() reconciles the reservation on every terminal path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bots16_complete_without_usage_releases_as_unknown_cost() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    guard = _RunBudgetGuard(
        _Deps(_budget_session(events)),
        tenant_id=uuid.uuid4(),
        worker_id=uuid.uuid4(),
        run_key="run:usage-none",
        budget={"compute": 100},
        started_monotonic=0.0,
    )
    provider = _Provider(response=SimpleNamespace(usage=None))
    guarded = _BudgetedProvider(provider, guard)

    await guarded.complete(SimpleNamespace(max_tokens=20, messages=[], metadata={}))

    event_types = [event_type for event_type, _payload in events]
    assert "llm_budget_reserved" in event_types
    # No usage recorded (we do not invent a zero cost), but the reservation is
    # released with an explicit unknown-cost marker instead of staying orphaned.
    assert "llm_usage" not in event_types
    released = next(payload for etype, payload in events if etype == "llm_budget_released")
    assert released["unknown_cost"] is True


@pytest.mark.asyncio
async def test_bots16_complete_releases_reservation_on_cancelled_error() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    guard = _RunBudgetGuard(
        _Deps(_budget_session(events)),
        tenant_id=uuid.uuid4(),
        worker_id=uuid.uuid4(),
        run_key="run:cancelled",
        budget={"compute": 100},
        started_monotonic=0.0,
    )
    provider = _Provider(error=asyncio.CancelledError())
    guarded = _BudgetedProvider(provider, guard)

    with pytest.raises(asyncio.CancelledError):
        await guarded.complete(SimpleNamespace(max_tokens=20, messages=[], metadata={}))

    event_types = [event_type for event_type, _payload in events]
    assert "llm_budget_released" in event_types
    assert "llm_usage" not in event_types


# ---------------------------------------------------------------------------
# BOTS-01 — the heartbeat aborts the turn when the worker is paused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bots01_run_with_pause_abort_cancels_the_runner() -> None:
    pause_detected = asyncio.Event()
    runner_started = asyncio.Event()
    runner_cancelled = asyncio.Event()

    async def runner() -> None:
        runner_started.set()
        try:
            await asyncio.Event().wait()  # never completes on its own
        finally:
            runner_cancelled.set()

    async def trigger_pause() -> None:
        await runner_started.wait()
        await asyncio.sleep(0)
        pause_detected.set()

    trigger = asyncio.create_task(trigger_pause())
    with pytest.raises(_RunInterrupted):
        await _run_with_pause_abort(
            runner(),
            pause_detected=pause_detected,
            timeout=10.0,
        )
    await trigger
    assert runner_cancelled.is_set()


@pytest.mark.asyncio
async def test_bots01_heartbeat_marks_cancelled_and_signals_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HeartbeatSession:
        active = False

        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            sql = str(statement)
            self.calls.append((sql, dict(params)))
            if "SELECT 1 FROM persistent_agents" in sql:
                return _Result({"one": 1})  # worker is paused
            return _Result(rowcount=1)

    real_sleep = asyncio.sleep

    async def no_sleep(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr("asyncio.sleep", no_sleep)

    session = HeartbeatSession()
    pause_detected = asyncio.Event()
    heartbeat = asyncio.create_task(
        _heartbeat(
            _Deps(session),
            uuid.uuid4(),
            uuid.uuid4(),
            "task-1",
            run_key="run:paused",
            generation=1,
            lease_seconds=120.0,
            pause_detected=pause_detected,
        )
    )
    try:
        await asyncio.wait_for(pause_detected.wait(), timeout=5.0)
    finally:
        heartbeat.cancel()
        with pytest.raises(asyncio.CancelledError):
            await heartbeat

    assert pause_detected.is_set()
    assert any("status = 'cancelled'" in sql for sql, _params in session.calls)
    # The cancel must be fenced so a stale runner cannot revive the run.
    cancel_sql = next(sql for sql, _params in session.calls if "status = 'cancelled'" in sql)
    assert "claim_generation = :generation" in cancel_sql
    assert "status = 'running'" in cancel_sql