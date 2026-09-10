"""Regression tests for durable persistent-agent execution and delivery."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from edecan_schemas import JobEnvelope
from edecan_worker.handlers.run_persistent_agent import (
    MAX_BUSY_DEFERRALS,
    _BudgetedProvider,
    _BudgetRejectedBeforeLLMCall,
    _claim_bot_run,
    _claim_worker_and_handoff,
    _defer_busy_run,
    _finalizar_team_mission,
    _publicar_en_chat_del_worker,
    _rollback_and_defer_busy_run,
    _run_origin,
    _RunBudgetGuard,
    _save_checkpoint,
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
    def __init__(self, session: Any, events: list[str] | None = None) -> None:
        self.session = session
        self.events = events

    async def __aenter__(self) -> Any:
        if self.events is not None:
            self.events.append("second-session-enter")
        self.session.active = True
        return self.session

    async def __aexit__(self, *_args: Any) -> bool:
        self.session.active = False
        if self.events is not None:
            self.events.append("second-session-exit")
        return False


class _Deps:
    settings = SimpleNamespace()

    def __init__(self, sessions: list[Any], events: list[str] | None = None) -> None:
        self.sessions = sessions
        self.events = events

    def session_factory(self, _tenant: Any) -> _Context:
        return _Context(self.sessions.pop(0), self.events)


class _MissingTableError(RuntimeError):
    sqlstate = "42P01"


@pytest.mark.asyncio
async def test_b03_relay_and_merge_publish_with_durable_delivery_id() -> None:
    tenant_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    message_id = uuid.uuid4()
    calls: list[tuple[str, dict[str, Any]]] = []

    class PublishSession:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            sql = str(statement)
            calls.append((sql, dict(params)))
            if "SELECT delivery_message_id FROM bot_runs" in sql:
                return _Result({"delivery_message_id": None})
            if "SELECT conversation_id" in sql:
                return _Result({"conversation_id": uuid.uuid4(), "nombre": "Coordinator"})
            if "INSERT INTO messages" in sql:
                return _Result({"id": message_id})
            return _Result(rowcount=1)

    class FinishSession:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            calls.append((str(statement), dict(params)))
            return _Result(rowcount=1)

    assert (
        _run_origin(source="delegacion_resultado", task_id="relay:abc", handoff_id=None)
        == "relay"
    )
    assert (
        _run_origin(source="team_merge", task_id="team-merge:abc", handoff_id=None)
        == "team_merge"
    )

    deps = _Deps([PublishSession(), FinishSession()])
    persisted_id = await _publicar_en_chat_del_worker(
        deps,
        tenant_id=tenant_id,
        worker_id=worker_id,
        texto="Final delivery",
        run_key="run:merge",
    )
    await _finalizar_team_mission(
        deps,
        tenant_id,
        str(uuid.uuid4()),
        "done",
        run_key="run:merge",
        delivery_message_id=persisted_id,
    )

    persist_index = next(i for i, (sql, _) in enumerate(calls) if "delivery_message_id =" in sql)
    delivered_index = next(i for i, (sql, _) in enumerate(calls) if "UPDATE team_missions" in sql)
    assert persisted_id == message_id
    assert persist_index < delivered_index
    delivered_sql, delivered_params = calls[delivered_index]
    assert "EXISTS" in delivered_sql
    assert delivered_params["delivery_message_id"] == str(message_id)


@pytest.mark.asyncio
async def test_b04_busy_claim_is_recoverable_through_bounded_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_core.queue as queue

    tenant_id = uuid.uuid4()
    events: list[str] = []

    class Session:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            events.append(str(statement))
            return _Result(rowcount=1)

    async def enqueue_outbox(
        session: Any, *, tenant_id: uuid.UUID, job_type: str, payload: dict[str, Any]
    ) -> uuid.UUID:
        assert session.active
        events.append("outbox")
        assert job_type == "run_persistent_agent"
        assert payload["busy_deferrals"] == 1
        assert payload["run_key"] == "run:busy"
        assert payload["_not_before"]
        return uuid.uuid4()

    monkeypatch.setattr(queue, "enqueue_outbox", enqueue_outbox, raising=False)
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_persistent_agent",
        payload={"worker_id": str(uuid.uuid4()), "instruction": "work"},
    )
    assert await _defer_busy_run(
        _Deps([Session()]), env=env, tenant_id=tenant_id, run_key="run:busy"
    )
    assert any("status = 'queued'" in event for event in events)
    assert "outbox" in events
    assert any("UPDATE job_outbox SET available_at" in event for event in events)

    exhausted = env.model_copy(
        update={"payload": {**env.payload, "busy_deferrals": MAX_BUSY_DEFERRALS}}
    )
    events.clear()
    assert not await _defer_busy_run(
        _Deps([Session()]), env=exhausted, tenant_id=tenant_id, run_key="run:busy"
    )
    assert any("status = 'failed'" in event for event in events)
    assert "outbox" not in events


@pytest.mark.asyncio
async def test_b05_rolls_back_claim_session_before_opening_defer_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_core.queue as queue

    tenant_id = uuid.uuid4()
    events: list[str] = []

    class ClaimSession:
        async def rollback(self) -> None:
            events.append("claim-rollback")

    class DeferSession:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            events.append("defer-update")
            return _Result(rowcount=1)

    async def enqueue_outbox(
        session: Any, *, tenant_id: uuid.UUID, job_type: str, payload: dict[str, Any]
    ) -> uuid.UUID:
        assert session.active
        events.append("defer-outbox")
        return uuid.uuid4()

    monkeypatch.setattr(queue, "enqueue_outbox", enqueue_outbox, raising=False)
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_persistent_agent",
        payload={"worker_id": str(uuid.uuid4()), "instruction": "work"},
    )
    await _rollback_and_defer_busy_run(
        _Deps([DeferSession()], events),
        ClaimSession(),
        env=env,
        tenant_id=tenant_id,
        run_key="run:ordered",
    )
    assert events.index("claim-rollback") < events.index("second-session-enter")
    assert events.index("second-session-enter") < events.index("defer-outbox")


@pytest.mark.asyncio
async def test_b05_claims_worker_then_handoff_in_the_same_transaction() -> None:
    tenant_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    handoff_id = uuid.uuid4()
    calls: list[tuple[int, str]] = []

    class ClaimSession:
        async def execute(self, statement: Any, _params: dict[str, Any]) -> _Result:
            calls.append((id(self), str(statement)))
            return _Result(rowcount=1)

    session = ClaimSession()
    assert await _claim_worker_and_handoff(
        session,
        tenant_id=tenant_id,
        worker_id=worker_id,
        handoff_id=handoff_id,
        task_id="task",
        lease_seconds=120,
    )

    assert len({session_id for session_id, _sql in calls}) == 1
    assert "UPDATE persistent_agents SET status = 'running'" in calls[0][1]
    assert "UPDATE persistent_agent_handoffs SET status = 'running'" in calls[1][1]


@pytest.mark.asyncio
async def test_b06_terminal_checkpoint_preserves_a_concurrent_pause() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class Session:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            calls.append((str(statement), dict(params)))
            return _Result(rowcount=1)

    await _save_checkpoint(
        _Deps([Session()]),
        uuid.uuid4(),
        uuid.uuid4(),
        task_id="task",
        status="idle",
        detail={"task_id": "task", "status": "done"},
    )
    sql, params = calls[0]
    assert "persistent_agents.status = 'paused' AND :status = 'idle'" in sql
    assert "THEN 'paused' ELSE :status END" in sql
    assert params["status"] == "idle"


@pytest.mark.asyncio
async def test_b07_parent_lock_precedes_child_update_and_outbox_is_transactional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_core.queue as queue

    tenant_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    events: list[str] = []

    class Session:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
            sql = str(statement)
            events.append(sql)
            if "SELECT r.team_mission_id" in sql:
                return _Result(
                    {
                        "team_mission_id": mission_id,
                        "agent_id": agent_id,
                        "coordinator_agent_id": uuid.uuid4(),
                        "pedido": "Build it",
                        "esperados": 2,
                        "user_id": uuid.uuid4(),
                        "status": "collecting",
                    }
                )
            if "UPDATE team_mission_results" in sql:
                return _Result(rowcount=1)
            if "COUNT(*)" in sql:
                return _Result({"fin": 2})
            if "UPDATE team_missions SET status = 'merging'" in sql:
                return _Result({"id": mission_id})
            if "SELECT 1 FROM persistent_agents" in sql:
                return _Result({"one": 1})
            if "COALESCE(a.display_name" in sql:
                return _Result(
                    [
                        {"nombre": "A", "estado": "done", "resumen": "one"},
                        {"nombre": "B", "estado": "done", "resumen": "two"},
                    ]
                )
            return _Result()

    session = Session()

    async def enqueue_outbox(
        current: Any, *, tenant_id: uuid.UUID, job_type: str, payload: dict[str, Any]
    ) -> uuid.UUID:
        assert current is session and current.active
        events.append("outbox-inside-transaction")
        return uuid.uuid4()

    monkeypatch.setattr(queue, "enqueue_outbox", enqueue_outbox, raising=False)
    await _notificar_team_mission_for_test(
        _Deps([session]), tenant_id=tenant_id, handoff_id=uuid.uuid4()
    )
    lock_index = next(i for i, event in enumerate(events) if "FOR UPDATE" in event)
    child_index = next(
        i for i, event in enumerate(events) if "UPDATE team_mission_results" in event
    )
    assert lock_index == 0
    assert lock_index < child_index
    assert events[-1] == "outbox-inside-transaction"


async def _notificar_team_mission_for_test(
    deps: Any, *, tenant_id: uuid.UUID, handoff_id: uuid.UUID
) -> None:
    from edecan_worker.handlers.run_persistent_agent import _notificar_team_mission

    await _notificar_team_mission(
        deps,
        tenant_id=tenant_id,
        handoff_id=handoff_id,
        estado="done",
        resumen="complete",
    )


@pytest.mark.asyncio
async def test_b20_rejects_next_llm_call_before_provider_spend() -> None:
    provider_calls: list[str] = []

    class BudgetSession:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            sql = str(statement)
            if "SELECT id FROM bot_runs" in sql:
                return _Result({"id": uuid.uuid4()})
            if "actual_compute" in sql:
                return _Result(
                    {
                        "actual_compute": 90,
                        "actual_money": 0,
                        "reserved_compute": 0,
                        "released_compute": 0,
                        "reserved_money": 0,
                        "released_money": 0,
                        "tools": 0,
                    }
                )
            if "MAX(seq)" in sql:
                return _Result({"seq": 4})
            return _Result(rowcount=1)

    class Provider:
        name = "must-not-run"

        async def stream(self, _request: Any):
            provider_calls.append("spent")
            if False:
                yield None

    guard = _RunBudgetGuard(
        _Deps([BudgetSession()]),
        tenant_id=uuid.uuid4(),
        worker_id=uuid.uuid4(),
        run_key="run:budget",
        budget={"compute": 100},
        started_monotonic=0.0,
    )
    guarded = _BudgetedProvider(Provider(), guard)

    with pytest.raises(_BudgetRejectedBeforeLLMCall):
        async for _chunk in guarded.stream(SimpleNamespace(max_tokens=20)):
            pass
    assert provider_calls == []
    assert guard.exceeded == ("compute",)


@pytest.mark.asyncio
async def test_b20_counts_an_outstanding_reservation_before_the_next_call() -> None:
    class BudgetSession:
        active = False

        async def execute(self, statement: Any, _params: dict[str, Any]) -> _Result:
            sql = str(statement)
            if "SELECT id FROM bot_runs" in sql:
                return _Result({"id": uuid.uuid4()})
            if "actual_compute" in sql:
                return _Result(
                    {
                        "actual_compute": 80,
                        "actual_money": 0,
                        "reserved_compute": 15,
                        "released_compute": 0,
                        "reserved_money": 0,
                        "released_money": 0,
                        "tools": 0,
                    }
                )
            if "MAX(seq)" in sql:
                return _Result({"seq": 2})
            return _Result(rowcount=1)

    guard = _RunBudgetGuard(
        _Deps([BudgetSession()]),
        tenant_id=uuid.uuid4(),
        worker_id=uuid.uuid4(),
        run_key="run:concurrent-budget",
        budget={"compute": 100},
        started_monotonic=0.0,
    )
    with pytest.raises(_BudgetRejectedBeforeLLMCall):
        await guard.before_call(SimpleNamespace(max_tokens=10, messages=[], metadata={}))
    assert guard.exceeded == ("compute",)


@pytest.mark.asyncio
async def test_b20_hard_money_cap_uses_conservative_estimate() -> None:
    """R2-F1: el costo desconocido ya no rechaza SIEMPRE (feature muerto):
    se estima conservadoramente (max_tokens x precio de salida máximo de la
    tabla). Un cap por debajo del estimado rechaza ANTES de gastar; un cap
    holgado deja pasar."""

    class BudgetSession:
        active = False

        async def execute(self, statement: Any, _params: dict[str, Any]) -> _Result:
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
                return _Result({"seq": 1})
            return _Result(rowcount=1)

    # Cap minúsculo (< estimado de 1 token al precio máximo): rechazo previo.
    guard = _RunBudgetGuard(
        _Deps([BudgetSession()]),
        tenant_id=uuid.uuid4(),
        worker_id=uuid.uuid4(),
        run_key="run:money-tight",
        budget={"money": 0.000001},
        started_monotonic=0.0,
    )
    with pytest.raises(_BudgetRejectedBeforeLLMCall):
        await guard.before_call(SimpleNamespace(max_tokens=1, messages=[], metadata={}))
    assert guard.exceeded == ("money",)

    # Cap holgado: la llamada pasa (el feature vuelve a funcionar).
    guard2 = _RunBudgetGuard(
        _Deps([BudgetSession()]),
        tenant_id=uuid.uuid4(),
        worker_id=uuid.uuid4(),
        run_key="run:money-loose",
        budget={"money": 10.0},
        started_monotonic=0.0,
    )
    await guard2.before_call(SimpleNamespace(max_tokens=1, messages=[], metadata={}))
    assert guard2.exceeded == ()


@pytest.mark.asyncio
async def test_missing_0070_tables_degrade_without_stopping_the_runner() -> None:
    tenant_id = uuid.uuid4()
    worker_id = uuid.uuid4()

    class MissingBotRunsSession:
        active = False

        async def execute(self, _statement: Any, _params: dict[str, Any]) -> _Result:
            raise _MissingTableError('relation "bot_runs" does not exist')

    legacy_claim = await _claim_bot_run(
        _Deps([MissingBotRunsSession()]),
        tenant_id=tenant_id,
        worker={"id": worker_id, "conversation_id": None},
        run_key="run:legacy-schema",
        origin="agent_message",
        lease_seconds=120.0,
    )
    assert legacy_claim.claimed
    assert not legacy_claim.terminal
    assert not legacy_claim.durability_enabled

    provider_calls: list[str] = []

    class Provider:
        async def stream(self, _request: Any):
            provider_calls.append("called")
            if False:
                yield None

    guard = _RunBudgetGuard(
        _Deps([MissingBotRunsSession()]),
        tenant_id=tenant_id,
        worker_id=worker_id,
        run_key="run:legacy-schema",
        budget={"compute": 1},
        started_monotonic=0.0,
    )
    async for _chunk in _BudgetedProvider(Provider(), guard).stream(
        SimpleNamespace(max_tokens=100)
    ):
        pass
    assert provider_calls == ["called"]


@pytest.mark.asyncio
async def test_missing_bot_runs_still_publishes_the_generated_delivery() -> None:
    tenant_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    message_id = uuid.uuid4()

    class MissingBotRunsSession:
        active = False

        async def execute(self, _statement: Any, _params: dict[str, Any]) -> _Result:
            raise _MissingTableError('relation "bot_runs" does not exist')

    class LegacyDeliverySession:
        active = False

        async def execute(self, statement: Any, _params: dict[str, Any]) -> _Result:
            sql = str(statement)
            if "SELECT conversation_id" in sql:
                return _Result({"conversation_id": uuid.uuid4(), "nombre": "Coordinator"})
            if "INSERT INTO messages" in sql:
                return _Result({"id": message_id})
            raise AssertionError(f"unexpected SQL: {sql}")

    assert (
        await _publicar_en_chat_del_worker(
            _Deps([MissingBotRunsSession(), LegacyDeliverySession()]),
            tenant_id=tenant_id,
            worker_id=worker_id,
            texto="Visible on the legacy schema",
            run_key="run:legacy-delivery",
        )
        == message_id
    )
