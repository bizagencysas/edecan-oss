"""Regression tests for auditoría ola 2 fixes in run_persistent_agent + envelope.

Covers the six findings of the ola-2 audit against
`apps/worker/edecan_worker/handlers/run_persistent_agent.py` and
`packages/core/edecan_core/agent_envelope.py`:

- F2-HIGH  — the envelope only trimmed the registry; `extra_tools` (persona +
  MCP) passed unfiltered. Now both are trimmed by `allowed_tools`.
- AUD-07a — `_mark_bot_run_terminal` wrote the terminal state without fencing by
  `claim_generation`; a stale runner could stomp the live run.
- AUD-07b — `_defer_busy_run` reset a live run to `queued` (and a false `failed`
  on exhaustion); a re-delivery must never touch a run held by another runner.
- F4/F5   — `dependencies` had no contract (dict broke the UUID cast → dead
  letter) and an unmet dependency returned silently. Now: re-enqueue with backoff
  up to `MAX_DEPENDENCY_DEFERRALS`, then a visible `blocked_dependencies` state.
- F3      — worker-side envelope coverage: expired → no run; allowed_tools → both
  registry and extra_tools trimmed; pending dependency → retries then visible.
- F6      — a corrupt envelope is fail-closed (sentinel + visible error), not the
  legacy "no envelope" path.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from edecan_core.agent_envelope import (
    apply_envelope_extra_tools_filter,
    apply_envelope_restrictions,
    envelope_expired,
)
from edecan_core.tools.base import Tool, ToolContext, ToolResult
from edecan_core.tools.registry import ToolRegistry
from edecan_schemas import JobEnvelope
from edecan_worker.handlers.run_persistent_agent import (
    _CORRUPT_ENVELOPE,
    MAX_BUSY_DEFERRALS,
    MAX_DEPENDENCY_DEFERRALS,
    _defer_busy_run,
    _defer_pending_dependencies,
    _dependency_backoff_seconds,
    _dependency_deferral_count,
    _marcar_mensaje_estado,
    _mark_bot_run_terminal,
    _parse_message_envelope,
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
        self.session.active = True
        return self.session

    async def __aexit__(self, *_args: Any) -> bool:
        self.session.active = False
        return False


class _Deps:
    settings = SimpleNamespace()

    def __init__(self, session: Any) -> None:
        self.session = session

    def session_factory(self, _tenant: Any) -> _Context:
        return _Context(self.session)


class _ToolStub(Tool):
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = name
        self.input_schema = {"type": "object", "properties": {}}
        self.dangerous = False

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(content="")


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(_ToolStub(name))
    return registry


# ---------------------------------------------------------------------------
# F2-HIGH — extra_tools filter
# ---------------------------------------------------------------------------


def test_f2_extra_tools_filter_filtra_tool_excluida() -> None:
    tools = [_ToolStub("leer_archivo"), _ToolStub("navegar_web")]
    envelope = {"allowed_tools": ["leer_archivo"]}

    filtradas = apply_envelope_extra_tools_filter(tools, envelope)

    assert [t.name for t in filtradas] == ["leer_archivo"]


def test_f2_extra_tools_filter_sin_allowed_tools_no_filtra() -> None:
    tools = [_ToolStub("leer_archivo"), _ToolStub("navegar_web")]

    assert apply_envelope_extra_tools_filter(tools, {"deadline": None}) == tools
    assert apply_envelope_extra_tools_filter(tools, {"allowed_tools": []}) == tools


def test_f2_extra_tools_filter_no_amplia() -> None:
    tools = [_ToolStub("leer_archivo")]
    envelope = {"allowed_tools": ["leer_archivo", "usar_computadora"]}

    assert [t.name for t in apply_envelope_extra_tools_filter(tools, envelope)] == ["leer_archivo"]


# ---------------------------------------------------------------------------
# F3 — worker-side envelope coverage
# ---------------------------------------------------------------------------


def test_f3_allowed_tools_recorta_registry_y_extra_tools() -> None:
    registry = _registry("leer_archivo", "escribir_archivo", "navegar_web")
    extra = [_ToolStub("escribir_archivo"), _ToolStub("navegar_web")]
    envelope = {"allowed_tools": ["leer_archivo", "navegar_web"]}

    restringido = apply_envelope_restrictions(registry, envelope)
    filtradas = apply_envelope_extra_tools_filter(extra, envelope)

    assert {t.name for t in restringido.all()} == {"leer_archivo", "navegar_web"}
    assert restringido.get("escribir_archivo") is None
    assert [t.name for t in filtradas] == ["navegar_web"]


def test_f3_envelope_vencido_no_corre() -> None:
    now = datetime.now(UTC)
    envelope = {"deadline": (now - timedelta(minutes=5)).isoformat()}

    assert envelope_expired(envelope, now=now) is True


def test_f3_envelope_futuro_si_corre() -> None:
    now = datetime.now(UTC)
    envelope = {"deadline": (now + timedelta(minutes=5)).isoformat()}

    assert envelope_expired(envelope, now=now) is False


# ---------------------------------------------------------------------------
# AUD-07a — terminal write fenced by claim_generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aud07a_mark_terminal_fencea_por_generacion() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class Session:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            calls.append((str(statement), dict(params)))
            return _Result(rowcount=1)

    await _mark_bot_run_terminal(
        _Deps(Session()),
        tenant_id=uuid.uuid4(),
        run_key="run:x",
        status="done",
        generation=7,
    )

    sql, params = calls[0]
    assert "claim_generation = :generation" in sql
    assert "status = 'running'" in sql
    assert params["generation"] == 7
    assert params["status"] == "succeeded"


@pytest.mark.asyncio
async def test_aud07a_mark_terminal_obsoleto_no_pisa_y_avisa(caplog: pytest.LogCaptureFixture) -> None:
    class Session:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            return _Result(rowcount=0)

    with caplog.at_level(logging.WARNING, logger="edecan_worker.handlers.run_persistent_agent"):
        await _mark_bot_run_terminal(
            _Deps(Session()),
            tenant_id=uuid.uuid4(),
            run_key="run:x",
            status="done",
            generation=7,
        )

    assert any("obsoleta" in message for message in caplog.messages)


# ---------------------------------------------------------------------------
# AUD-07b — defer never stomps a live run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aud07b_defer_run_vivo_no_toca_la_fila(monkeypatch: pytest.MonkeyPatch) -> None:
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
        return uuid.uuid4()

    monkeypatch.setattr(queue, "enqueue_outbox", enqueue_outbox, raising=False)

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_persistent_agent",
        payload={"worker_id": str(uuid.uuid4()), "instruction": "work"},
    )

    assert await _defer_busy_run(
        _Deps(Session()), env=env, tenant_id=tenant_id, run_key="run:live", claimed=False
    )

    assert not any("status = 'queued'" in event for event in events)
    assert not any("status = 'failed'" in event for event in events)
    assert "outbox" in events
    assert any("UPDATE job_outbox SET available_at" in event for event in events)


@pytest.mark.asyncio
async def test_aud07b_defer_run_vivo_agotado_no_marca_failed(
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

    monkeypatch.setattr(
        queue, "enqueue_outbox", lambda *a, **k: uuid.uuid4(), raising=False
    )

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_persistent_agent",
        payload={
            "worker_id": str(uuid.uuid4()),
            "instruction": "work",
            "busy_deferrals": MAX_BUSY_DEFERRALS,
        },
    )

    assert not await _defer_busy_run(
        _Deps(Session()), env=env, tenant_id=tenant_id, run_key="run:live", claimed=False
    )

    assert events == []  # the run row is never touched, no retry is enqueued
    assert not any("status = 'failed'" in event for event in events)
    assert not any("status = 'queued'" in event for event in events)


# ---------------------------------------------------------------------------
# F4/F5 — dependencies contract and visible blocked state
# ---------------------------------------------------------------------------


def test_f4_dependency_backoff_crece() -> None:
    assert _dependency_backoff_seconds(1) == 30
    assert _dependency_backoff_seconds(2) == 60
    assert _dependency_backoff_seconds(3) == 120
    assert _dependency_backoff_seconds(99) == 120  # exponent clamp


def test_f4_dependency_deferral_count_incrementa() -> None:
    assert _dependency_deferral_count({}) == 1
    assert _dependency_deferral_count({"dependency_deferrals": 0}) == 1
    assert _dependency_deferral_count({"dependency_deferrals": 2}) == 3
    assert _dependency_deferral_count({"dependency_deferrals": "bogus"}) == 1


@pytest.mark.asyncio
async def test_f4_dependencia_pendiente_reencola_con_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_core.queue as queue

    tenant_id = uuid.uuid4()
    message_id = str(uuid.uuid4())
    seen: dict[str, Any] = {"payload": None, "job_type": None, "session": None}
    sqls: list[str] = []

    class Session:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            sqls.append(str(statement))
            return _Result(rowcount=1)

    the_session = Session()

    async def enqueue_outbox(
        session: Any, *, tenant_id: uuid.UUID, job_type: str, payload: dict[str, Any]
    ) -> uuid.UUID:
        seen["session"] = session
        seen["job_type"] = job_type
        seen["payload"] = payload
        return uuid.uuid4()

    monkeypatch.setattr(queue, "enqueue_outbox", enqueue_outbox, raising=False)

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_persistent_agent",
        payload={"worker_id": str(uuid.uuid4()), "instruction": "work"},
    )

    await _defer_pending_dependencies(
        the_session, env=env, tenant_id=tenant_id, message_id=message_id
    )

    assert seen["session"] is the_session
    assert seen["job_type"] == "run_persistent_agent"
    assert seen["payload"]["dependency_deferrals"] == 1
    assert not any("blocked_dependencies" in sql for sql in sqls)


@pytest.mark.asyncio
async def test_f4_dependencia_agotada_marca_blocked_dependencies() -> None:
    tenant_id = uuid.uuid4()
    message_id = str(uuid.uuid4())
    calls: list[tuple[str, dict[str, Any]]] = []

    class Session:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            calls.append((str(statement), dict(params)))
            return _Result(rowcount=1)

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_persistent_agent",
        payload={
            "worker_id": str(uuid.uuid4()),
            "instruction": "work",
            "dependency_deferrals": MAX_DEPENDENCY_DEFERRALS,
        },
    )

    await _defer_pending_dependencies(
        Session(), env=env, tenant_id=tenant_id, message_id=message_id
    )

    assert any("UPDATE agent_messages" in sql for sql, _params in calls)
    blocked = next(
        params for sql, params in calls if "UPDATE agent_messages" in sql
    )
    assert blocked["status"] == "blocked_dependencies"
    assert blocked["id"] == message_id
    assert not any("INSERT INTO job_outbox" in sql for sql, _params in calls)


# ---------------------------------------------------------------------------
# F6 — corrupt envelope is fail-closed
# ---------------------------------------------------------------------------


def test_f6_envelope_corrupto_es_sentinel() -> None:
    assert _parse_message_envelope("no-json{{{") is _CORRUPT_ENVELOPE
    assert _parse_message_envelope("[]") is _CORRUPT_ENVELOPE
    assert _parse_message_envelope("null") is _CORRUPT_ENVELOPE
    assert _parse_message_envelope(42) is _CORRUPT_ENVELOPE


def test_f6_envelope_ausente_es_legacy() -> None:
    assert _parse_message_envelope(None) is None


def test_f6_envelope_valido_se_decodifica() -> None:
    envelope = {"allowed_tools": ["leer_archivo"]}

    assert _parse_message_envelope(envelope) == envelope
    assert _parse_message_envelope(json.dumps(envelope)) == envelope


@pytest.mark.asyncio
async def test_f6_marcar_mensaje_error_visible() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class Session:
        active = False

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            calls.append((str(statement), dict(params)))
            return _Result(rowcount=1)

    await _marcar_mensaje_estado(
        Session(), tenant_id=uuid.uuid4(), message_id=str(uuid.uuid4()), status="error"
    )

    sql, params = calls[0]
    assert "UPDATE agent_messages" in sql
    assert "status = :status" in sql
    assert params["status"] == "error"