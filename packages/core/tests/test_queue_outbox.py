from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from edecan_core.queue import QueueTransport, dispatch_outbox, enqueue_outbox
from edecan_schemas import JobEnvelope


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, *, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self) -> _Mappings:
        return _Mappings(self._rows)


class _Session:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commit_calls = 0

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        sql = str(statement)
        self.calls.append((sql, params))
        if sql.lstrip().startswith("SELECT"):
            return _Result(self.rows)
        return _Result(rowcount=1)

    async def commit(self) -> None:
        self.commit_calls += 1


def _session_factory(session: _Session):
    @asynccontextmanager
    async def _factory(_tenant_id: UUID | None):
        yield session

    return _factory


class _Transport:
    def __init__(self, *, fail_ids: set[UUID] | None = None) -> None:
        self.fail_ids = fail_ids or set()
        self.sent = []

    async def send(self, envelope: Any) -> None:
        if envelope.job_id in self.fail_ids:
            raise RuntimeError(f"transport unavailable for {envelope.job_id}")
        self.sent.append(envelope)


async def test_enqueue_outbox_uses_callers_session_and_exact_table_contract() -> None:
    session = _Session()
    tenant_id = uuid4()

    outbox_id = await enqueue_outbox(
        session,
        tenant_id=tenant_id,
        job_type="run_persistent_agent",
        payload={"worker_id": "worker-1"},
    )

    assert isinstance(outbox_id, UUID)
    assert session.commit_calls == 0
    assert len(session.calls) == 1
    sql, params = session.calls[0]
    assert "INSERT INTO job_outbox" in sql
    for column in (
        "id",
        "tenant_id",
        "job_type",
        "payload",
        "status",
        "attempts",
        "available_at",
        "sent_at",
        "last_error",
        "created_at",
        "updated_at",
    ):
        assert column in sql
    assert params["id"] == outbox_id
    assert params["tenant_id"] == tenant_id
    assert params["job_type"] == "run_persistent_agent"
    assert json.loads(params["payload"]) == {"worker_id": "worker-1"}


async def test_dispatch_outbox_claims_bounded_batch_sends_and_marks_sent() -> None:
    outbox_id = uuid4()
    tenant_id = uuid4()
    session = _Session(
        [
            {
                "id": outbox_id,
                "tenant_id": tenant_id,
                "job_type": "run_persistent_agent",
                "payload": json.dumps({"worker_id": "worker-1"}),
                "attempts": 0,
            }
        ]
    )
    transport = _Transport()

    sent = await dispatch_outbox(
        session_factory=_session_factory(session),
        transport=transport,
    )

    assert sent == 1
    assert len(transport.sent) == 1
    assert transport.sent[0].job_id == outbox_id
    assert transport.sent[0].tenant_id == tenant_id
    assert transport.sent[0].payload == {"worker_id": "worker-1"}
    select_sql, select_params = session.calls[0]
    assert "FOR UPDATE SKIP LOCKED" in select_sql
    assert select_params["limit"] <= 50
    update_sql, update_params = session.calls[1]
    assert "UPDATE job_outbox" in update_sql
    assert update_params["status"] == "sent"
    assert update_params["attempts"] == 1
    assert update_params["sent_at"] is not None


async def test_dispatch_outbox_retries_then_marks_dead_without_stopping_batch() -> None:
    retry_id = uuid4()
    dead_id = uuid4()
    successful_id = uuid4()
    now = datetime.now(UTC)
    rows = [
        {
            "id": retry_id,
            "tenant_id": uuid4(),
            "job_type": "run_persistent_agent",
            "payload": {},
            "attempts": 0,
        },
        {
            "id": dead_id,
            "tenant_id": uuid4(),
            "job_type": "run_persistent_agent",
            "payload": {},
            "attempts": 4,
        },
        {
            "id": successful_id,
            "tenant_id": uuid4(),
            "job_type": "run_persistent_agent",
            "payload": {},
            "attempts": 0,
        },
    ]
    session = _Session(rows)
    transport = _Transport(fail_ids={retry_id, dead_id})

    sent = await dispatch_outbox(
        session_factory=_session_factory(session),
        transport=transport,
    )

    assert sent == 1
    updates = {params["id"]: params for _, params in session.calls[1:]}
    assert updates[retry_id]["status"] == "queued"
    assert updates[retry_id]["attempts"] == 1
    assert updates[retry_id]["available_at"] > now
    assert "transport unavailable" in updates[retry_id]["last_error"]
    assert updates[dead_id]["status"] == "dead"
    assert updates[dead_id]["attempts"] == 5
    assert updates[successful_id]["status"] == "sent"


async def test_enqueue_outbox_rejects_unknown_job_type_before_insert() -> None:
    session = _Session()

    with pytest.raises(ValueError):
        await enqueue_outbox(
            session,
            tenant_id=uuid4(),
            job_type="unknown_job",
            payload={},
        )

    assert session.calls == []


async def test_db_transport_reuses_outbox_id_and_deduplicates_publish(monkeypatch) -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []

    class _Connection:
        closed = False

        async def execute(self, statement: str, *args: Any) -> None:
            calls.append((statement, args))

        async def close(self) -> None:
            self.closed = True

    connection = _Connection()

    async def connect(dsn: str) -> _Connection:
        assert dsn == "postgresql://u:p@localhost/db"
        return connection

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(connect=connect))
    envelope = JobEnvelope(
        job_id=uuid4(),
        tenant_id=uuid4(),
        type="run_persistent_agent",
        payload={"worker_id": "worker-1"},
    )
    transport = QueueTransport(
        SimpleNamespace(
            QUEUE_PROVIDER="db",
            DATABASE_URL="postgresql+asyncpg://u:p@localhost/db",
        )
    )

    await transport.send(envelope)

    assert len(calls) == 1
    sql, args = calls[0]
    assert "ON CONFLICT (id) DO NOTHING" in sql
    assert args[0] == envelope.job_id
    assert args[1] == envelope.tenant_id
    assert args[2] == envelope.type
    assert json.loads(args[3]) == envelope.payload
    assert connection.closed is True
