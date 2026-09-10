from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import edecan_agents.tools as tools_module
import edecan_core.queue as queue_module
import pytest
from edecan_agents.persistent_policy import MAX_HANDOFF_DEPTH
from edecan_agents.tools import EnviarMensajeBotTool


class _Result:
    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        scalar_value: Any = None,
    ) -> None:
        self._rows = rows or []
        self._scalar_value = scalar_value

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar(self) -> Any:
        return self._scalar_value


@dataclass
class _SenderChainSession:
    destination_id: str
    source_name: str = "Sender"
    destination_name: str = "Receiver"
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        bound = dict(params or {})
        self.calls.append((sql, bound))

        if "name ILIKE :q" in sql:
            return _Result(
                rows=[
                    {
                        "id": self.destination_id,
                        "name": self.destination_name,
                        "display_name": self.destination_name,
                    }
                ]
            )
        if "SELECT 1 FROM persistent_agents" in sql:
            return _Result(rows=[{"1": 1}])
        if "SELECT id, COALESCE(display_name, name) AS nombre" in sql:
            ids = bound["ids"]
            return _Result(
                rows=[
                    {"id": ids[0], "nombre": self.source_name},
                    {"id": ids[1], "nombre": self.destination_name},
                ]
            )
        if "SELECT display_name, name FROM persistent_agents" in sql:
            return _Result(rows=[{"display_name": self.source_name, "name": self.source_name}])
        if "INSERT INTO persistent_agent_handoffs" in sql:
            return _Result(rows=[{"id": str(uuid4())}])
        if "INSERT INTO agent_direct_chats" in sql:
            return _Result(rows=[{"id": str(uuid4()), "conversation_id": str(uuid4())}])
        if "SELECT avatar FROM persistent_agents" in sql:
            return _Result(scalar_value={"shape": "orb"})
        return _Result()


def _install_fake_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Monkeypatchea `enqueue_outbox` (camino principal, transaccional) y
    `enqueue` (fallback SQS inmediato) sobre `edecan_core.queue`, registrando
    cada uno por separado para poder afirmar cuál se usó (C8a)."""
    outbox_payloads: list[dict[str, Any]] = []
    enqueue_payloads: list[dict[str, Any]] = []

    async def fake_enqueue_outbox(
        session: Any, *, tenant_id: Any, job_type: str, payload: dict[str, Any]
    ) -> Any:
        assert job_type == "run_persistent_agent"
        outbox_payloads.append(dict(payload))
        return uuid4()

    async def fake_enqueue(
        settings: Any,
        job_type: str,
        payload: dict[str, Any],
        tenant_id: Any,
        **kwargs: Any,
    ) -> Any:
        assert job_type == "run_persistent_agent"
        enqueue_payloads.append(dict(payload))
        return uuid4()

    monkeypatch.setattr(queue_module, "enqueue_outbox", fake_enqueue_outbox)
    monkeypatch.setattr(queue_module, "enqueue", fake_enqueue)
    return outbox_payloads, enqueue_payloads


@pytest.mark.parametrize(
    "extras",
    [
        {},
        {"worker_id": None},
        {"worker_id": "not-a-uuid"},
    ],
)
async def test_sender_invalido_falla_contrato_antes_de_sql(
    extras: dict[str, Any], make_ctx
) -> None:
    session = _SenderChainSession(destination_id=str(uuid4()))
    result = await EnviarMensajeBotTool().run(
        make_ctx(session=session, extras=extras),
        {"bot": "Receiver", "mensaje": "Hello"},
    )

    assert "contexto de bot válido" in result.content
    assert "None" not in result.content
    assert session.calls == []


async def test_sender_uuid_valido_pasa_y_encola_cadena_canonica(
    make_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    outbox_payloads, enqueue_payloads = _install_fake_queue(monkeypatch)
    sender = str(uuid4())
    receiver = str(uuid4())
    session = _SenderChainSession(destination_id=receiver)

    result = await EnviarMensajeBotTool().run(
        make_ctx(session=session, extras={"worker_id": sender}),
        {"bot": "Receiver", "mensaje": "Please review this"},
    )

    assert result.data["receiver"] == receiver
    # C8a: el trabajo va por outbox transaccional, no por enqueue inmediato.
    assert enqueue_payloads == []
    assert len(outbox_payloads) == 1
    payload = outbox_payloads[0]
    assert payload["depth"] == 1
    assert payload["visited_worker_ids"] == [sender]
    assert payload["chain_depth"] == payload["depth"]
    assert json.loads(payload["chain_visited"]) == payload["visited_worker_ids"]


async def test_cadena_a_b_a_se_frena_antes_de_escribir(
    make_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    outbox_payloads, _enqueue_payloads = _install_fake_queue(monkeypatch)
    worker_a = str(uuid4())
    worker_b = str(uuid4())

    first_session = _SenderChainSession(destination_id=worker_b)
    await EnviarMensajeBotTool().run(
        make_ctx(session=first_session, extras={"worker_id": worker_a}),
        {"bot": "B", "mensaje": "First hop"},
    )
    parent = outbox_payloads[0]

    return_session = _SenderChainSession(destination_id=worker_a)
    result = await EnviarMensajeBotTool().run(
        make_ctx(
            session=return_session,
            extras={
                "worker_id": worker_b,
                "handoff_depth": parent["depth"],
                "handoff_visited": parent["visited_worker_ids"],
            },
        ),
        {"bot": "A", "mensaje": "Return hop"},
    )

    assert "ciclo" in result.content.lower()
    assert len(outbox_payloads) == 1
    assert not any("INSERT INTO" in sql for sql, _params in return_session.calls)


async def test_depth_maximo_se_conserva_y_luego_bloquea(
    make_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    outbox_payloads, _enqueue_payloads = _install_fake_queue(monkeypatch)
    sender = str(uuid4())
    receiver = str(uuid4())
    visited = [str(uuid4()) for _ in range(MAX_HANDOFF_DEPTH - 1)]
    session = _SenderChainSession(destination_id=receiver)

    result = await EnviarMensajeBotTool().run(
        make_ctx(
            session=session,
            extras={
                "worker_id": sender,
                "handoff_depth": MAX_HANDOFF_DEPTH - 1,
                "handoff_visited": visited,
            },
        ),
        {"bot": "Receiver", "mensaje": "Last allowed hop"},
    )

    assert result.data["receiver"] == receiver
    assert outbox_payloads[0]["depth"] == MAX_HANDOFF_DEPTH
    assert outbox_payloads[0]["visited_worker_ids"] == [*visited, sender]

    blocked_session = _SenderChainSession(destination_id=str(uuid4()))
    blocked = await EnviarMensajeBotTool().run(
        make_ctx(
            session=blocked_session,
            extras={
                "worker_id": receiver,
                "handoff_depth": outbox_payloads[0]["depth"],
                "handoff_visited": outbox_payloads[0]["visited_worker_ids"],
            },
        ),
        {"bot": "Another", "mensaje": "Too deep"},
    )

    assert "profundidad máxima" in blocked.content
    assert blocked_session.calls == []


async def test_handoff_tool_persiste_columnas_y_envelope_canonicos(
    make_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_send_agent_message(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(tools_module, "enviar_mensaje_agente", fake_send_agent_message)
    source = str(uuid4())
    destination = str(uuid4())
    visited = [str(uuid4()), str(uuid4())]
    session = _SenderChainSession(destination_id=destination)

    result = await tools_module._crear_handoff(
        make_ctx(
            session=session,
            extras={
                "worker_id": source,
                "handoff_depth": 2,
                "handoff_visited": visited,
            },
        ),
        mission_id=uuid4(),
        destino_worker_id=destination,
        args={"objetivo": "Prepare the report"},
    )

    assert isinstance(result, dict)
    _sql, params = next(
        call for call in session.calls if "INSERT INTO persistent_agent_handoffs" in call[0]
    )
    envelope = json.loads(params["envelope"])
    assert envelope["depth"] == params["depth"] == 3
    assert envelope["visited_worker_ids"] == json.loads(params["visitados"])
    assert envelope["visited_worker_ids"] == [*visited, source]


# ---------------------------------------------------------------------------
# C8a: `EnviarMensajeBotTool` escribe el job del receptor en el outbox
# TRANSACCIONAL (misma sesión que el INSERT del mensaje), no con `enqueue`
# inmediato — así el receptor no corre antes del commit y no queda una fila
# `pending` huérfana.
# ---------------------------------------------------------------------------


@dataclass
class _TransactionalSession(_SenderChainSession):
    """`_SenderChainSession` + un búfer `job_outbox` con commit/rollback, para
    probar que el job no es visible ANTES del commit (C8a)."""

    outbox: list[dict[str, Any]] = field(default_factory=list)
    _pending_outbox: list[dict[str, Any]] = field(default_factory=list)

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        if "INSERT INTO job_outbox" in str(statement):
            self._pending_outbox.append(dict(params or {}))
            return _Result()
        return await super().execute(statement, params)

    async def commit(self) -> None:
        self.outbox.extend(self._pending_outbox)
        self._pending_outbox = []

    async def rollback(self) -> None:
        self._pending_outbox = []


async def test_job_va_por_outbox_en_la_misma_sesion_y_no_por_enqueue_inmediato(
    make_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C8a: el trabajo se escribe con `enqueue_outbox` usando la MISMA sesión
    que el INSERT del mensaje; `enqueue` (SQS inmediato) no se llama."""
    seen: list[tuple[Any, str]] = []

    async def spy_outbox(session: Any, *, tenant_id: Any, job_type: str, payload: Any) -> Any:
        seen.append((session, job_type))
        return uuid4()

    async def spy_enqueue(settings: Any, job_type: str, payload: Any, tenant_id: Any, **kwargs: Any) -> Any:
        raise AssertionError("enqueue inmediato no debe usarse en EnviarMensajeBotTool")

    monkeypatch.setattr(queue_module, "enqueue_outbox", spy_outbox)
    monkeypatch.setattr(queue_module, "enqueue", spy_enqueue)

    sender = str(uuid4())
    receiver = str(uuid4())
    session = _SenderChainSession(destination_id=receiver)
    ctx = make_ctx(session=session, extras={"worker_id": sender})

    result = await EnviarMensajeBotTool().run(ctx, {"bot": "Receiver", "mensaje": "Hello"})

    assert result.data["receiver"] == receiver
    assert len(seen) == 1
    outbox_session, job_type = seen[0]
    assert job_type == "run_persistent_agent"
    assert outbox_session is session


async def test_job_no_es_visible_antes_del_commit_y_rollback_no_deja_job(
    make_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C8a: el job del receptor se escribe en el outbox de la MISMA sesión —
    antes del commit solo existe en el búfer pendiente, y un rollback lo
    descarta (no queda job huérfano)."""
    from edecan_core.queue import enqueue_outbox as real_enqueue_outbox

    monkeypatch.setattr(queue_module, "enqueue_outbox", real_enqueue_outbox)

    sender = str(uuid4())
    receiver = str(uuid4())
    session = _TransactionalSession(destination_id=receiver)
    ctx = make_ctx(session=session, extras={"worker_id": sender})

    result = await EnviarMensajeBotTool().run(ctx, {"bot": "Receiver", "mensaje": "Hello"})

    assert result.data["receiver"] == receiver
    # Antes del commit: el job está pendiente, NO durable.
    assert session.outbox == []
    assert len(session._pending_outbox) == 1

    # Rollback: el job pendiente se descarta — nada queda encolado.
    await session.rollback()
    assert session.outbox == []
    assert session._pending_outbox == []


async def test_fallback_a_enqueue_si_el_outbox_no_esta_disponible(
    make_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C8a: si el outbox no está disponible (migración no aplicada), se degrada
    al camino viejo `enqueue` — mismo criterio que `DelegarMisionTool`."""
    enqueue_payloads: list[dict[str, Any]] = []

    async def falla_outbox(session: Any, *, tenant_id: Any, job_type: str, payload: Any) -> Any:
        raise RuntimeError("job_outbox table missing")

    async def spy_enqueue(settings: Any, job_type: str, payload: Any, tenant_id: Any, **kwargs: Any) -> Any:
        enqueue_payloads.append(dict(payload))
        return uuid4()

    monkeypatch.setattr(queue_module, "enqueue_outbox", falla_outbox)
    monkeypatch.setattr(queue_module, "enqueue", spy_enqueue)

    sender = str(uuid4())
    receiver = str(uuid4())
    session = _SenderChainSession(destination_id=receiver)
    ctx = make_ctx(session=session, extras={"worker_id": sender})

    result = await EnviarMensajeBotTool().run(ctx, {"bot": "Receiver", "mensaje": "Hello"})

    assert result.data["receiver"] == receiver
    assert len(enqueue_payloads) == 1
    assert enqueue_payloads[0]["depth"] == 1
