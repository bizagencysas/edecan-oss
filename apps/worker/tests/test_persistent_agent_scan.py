from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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
    def __init__(self, rows=()):
        self._rows = rows

    def mappings(self):
        return _Mappings(self._rows)


class _Session:
    def __init__(self, row):
        self.row = row
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params):
        sql = str(statement)
        self.calls.append((sql, params))
        if sql.startswith("SELECT"):
            return _Result([self.row])
        return _Result()


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

    async def enqueue(_settings, job_type, payload, queued_tenant):
        enqueued.append((job_type, payload, queued_tenant))

    monkeypatch.setattr("edecan_core.queue.enqueue", enqueue)

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
