from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import edecan_worker.handlers.persistent_agent_scan as scan_module
import pytest
from edecan_schemas import JobEnvelope


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows=(), *, rowcount=0):
        self._rows = rows
        self.rowcount = rowcount

    def mappings(self):
        return _Mappings(self._rows)


class _Session:
    def __init__(self, rows):
        self.rows = rows if isinstance(rows, list) else [rows]
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params):
        sql = str(statement)
        self.calls.append((sql, params))
        if sql.startswith("SELECT"):
            return _Result(self.rows)
        return _Result(rowcount=1)


class _Context:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return None


class _Deps:
    def __init__(self, session):
        self.session = session
        self.settings = object()

    def session_factory(self, _tenant):
        return _Context(self.session)


@pytest.mark.asyncio
async def test_scan_reencola_worker_running_con_lease_vencido(monkeypatch):
    worker_id, tenant_id = uuid4(), uuid4()
    row = {
        "id": worker_id,
        "tenant_id": tenant_id,
        "schedule": json.dumps(
            {
                "instruction": "revisar pendientes",
                "next_run_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
                "every_seconds": 300,
            }
        ),
        "budget": {"lease_seconds": 120},
    }
    session = _Session(row)
    enqueued: list[tuple[str, dict, object]] = []

    async def enqueue_outbox(_session, *, tenant_id, job_type, payload):
        enqueued.append((job_type, payload, tenant_id))
        return uuid4()

    monkeypatch.setattr("edecan_core.queue.enqueue_outbox", enqueue_outbox)

    await scan_module.handle(
        JobEnvelope(
            job_id=uuid4(),
            tenant_id=None,
            type="persistent_agent_scan",
            payload={},
        ),
        _Deps(session),
    )

    assert len(enqueued) == 1
    assert enqueued[0][0] == "run_persistent_agent"
    assert enqueued[0][1]["worker_id"] == str(worker_id)
    assert enqueued[0][2] == tenant_id
    select_sql = session.calls[0][0]
    update_sql = session.calls[1][0]
    for sql in (select_sql, update_sql):
        assert "status = 'running'" in sql
        assert "jsonb_typeof" in sql
        assert "make_interval" in sql
    assert "updated_at = now()" not in update_sql
    assert "schedule->>'next_run_at' = :expected_next_run_at" in update_sql


@pytest.mark.asyncio
async def test_scan_normalizes_naive_utc_and_isolates_invalid_candidates(monkeypatch):
    now = datetime.now(UTC)
    rows = [
        {
            "id": uuid4(),
            "tenant_id": uuid4(),
            "schedule": {
                "instruction": "invalid date",
                "next_run_at": "not-a-date",
                "every_seconds": 300,
            },
        },
        {
            "id": uuid4(),
            "tenant_id": uuid4(),
            "schedule": {
                "instruction": "naive UTC",
                "next_run_at": (now - timedelta(minutes=2)).replace(tzinfo=None).isoformat(),
                "every_seconds": 300,
            },
        },
        {
            "id": uuid4(),
            "tenant_id": uuid4(),
            "schedule": {
                "instruction": "aware UTC",
                "next_run_at": (now - timedelta(minutes=1)).isoformat(),
                "every_seconds": 300,
            },
        },
    ]
    session = _Session(rows)
    enqueued: list[dict] = []

    async def enqueue_outbox(_session, *, tenant_id, job_type, payload):
        enqueued.append({"tenant_id": tenant_id, "job_type": job_type, "payload": payload})
        return uuid4()

    monkeypatch.setattr("edecan_core.queue.enqueue_outbox", enqueue_outbox)

    await scan_module.handle(
        JobEnvelope(job_id=uuid4(), tenant_id=None, type="persistent_agent_scan", payload={}),
        _Deps(session),
    )

    assert [call["payload"]["instruction"] for call in enqueued] == [
        "naive UTC",
        "aware UTC",
    ]


@pytest.mark.asyncio
async def test_scan_filters_executable_schedules_before_limit(monkeypatch):
    worker_id, tenant_id = uuid4(), uuid4()
    session = _Session(
        {
            "id": worker_id,
            "tenant_id": tenant_id,
            "schedule": {
                "instruction": "scheduled worker after 1001 unscheduled workers",
                "next_run_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                "every_seconds": 300,
            },
        }
    )
    enqueued: list[object] = []

    async def enqueue_outbox(*args, **kwargs):
        enqueued.append((args, kwargs))
        return uuid4()

    monkeypatch.setattr("edecan_core.queue.enqueue_outbox", enqueue_outbox)

    await scan_module.handle(
        JobEnvelope(job_id=uuid4(), tenant_id=None, type="persistent_agent_scan", payload={}),
        _Deps(session),
    )

    select_sql = session.calls[0][0]
    assert select_sql.index("schedule") < select_sql.index("LIMIT")
    assert "schedule->>'instruction'" in select_sql
    assert "schedule->'next_run_at'" in select_sql
    assert len(enqueued) == 1


class _ConcurrentSession(_Session):
    def __init__(self, row, state):
        super().__init__(row)
        self.state = state

    async def execute(self, statement, params):
        sql = str(statement)
        self.calls.append((sql, params))
        if sql.startswith("SELECT"):
            await asyncio.sleep(0)
            return _Result(self.rows)
        if sql.startswith("UPDATE persistent_agents"):
            if params["expected_next_run_at"] != self.state.next_run_at:
                return _Result(rowcount=0)
            self.state.next_run_at = json.loads(params["schedule"])["next_run_at"]
            await asyncio.sleep(0)
            return _Result(rowcount=1)
        return _Result(rowcount=1)


@pytest.mark.asyncio
async def test_two_concurrent_scans_enqueue_one_outbox_row_via_cas(monkeypatch):
    previous_next_run = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    row = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "schedule": {
            "instruction": "run exactly once",
            "next_run_at": previous_next_run,
            "every_seconds": 300,
        },
    }
    state = SimpleNamespace(next_run_at=previous_next_run)
    first_session = _ConcurrentSession(row, state)
    second_session = _ConcurrentSession(row, state)
    outbox_calls: list[dict] = []

    async def enqueue_outbox(_session, *, tenant_id, job_type, payload):
        outbox_calls.append({"tenant_id": tenant_id, "job_type": job_type, "payload": payload})
        return uuid4()

    monkeypatch.setattr("edecan_core.queue.enqueue_outbox", enqueue_outbox)
    env = JobEnvelope(
        job_id=uuid4(), tenant_id=None, type="persistent_agent_scan", payload={}
    )

    await asyncio.gather(
        scan_module.handle(env, _Deps(first_session)),
        scan_module.handle(env, _Deps(second_session)),
    )

    assert len(outbox_calls) == 1
    assert outbox_calls[0]["job_type"] == "run_persistent_agent"
