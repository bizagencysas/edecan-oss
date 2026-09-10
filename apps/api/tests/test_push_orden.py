from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import edecan_core.queue as queue
import pytest

import edecan_api.bot_turn_service as bot_turn_service
import edecan_api.deps as deps
import edecan_api.routers.persistent_agents as persistent_agents
from edecan_api.deps import CurrentUser, TenantCtx
from edecan_api.routers.conversations import _format_sse
from edecan_api.routers.persistent_agents import PersistentAgentMessageIn


class _Session:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def commit(self) -> None:
        self.order.append("commit")


async def _consume(response: Any, order: list[str]) -> str:
    parts: list[str] = []
    async for part in response.body_iterator:
        text = part.decode() if isinstance(part, bytes) else part
        if "event: message.done" in text:
            order.append("terminal")
        parts.append(text)
    return "".join(parts)


@pytest.fixture
def push_env(monkeypatch: pytest.MonkeyPatch, fake_redis: Any) -> dict[str, Any]:
    worker_id = uuid.uuid4()
    conversation_id = uuid.uuid4()

    async def load_worker(_session: Any, _user: Any, _worker_id: uuid.UUID) -> dict[str, Any]:
        return {"id": str(worker_id), "name": "BotX", "display_name": "BotX"}

    async def ensure(_session: Any, _user: Any, _worker: Any) -> uuid.UUID:
        return conversation_id

    async def no_log(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def no_early_push(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(bot_turn_service, "load_worker", load_worker)
    monkeypatch.setattr(bot_turn_service, "ensure_worker_conversation", ensure)
    monkeypatch.setattr(deps, "get_redis", lambda _settings: fake_redis)
    monkeypatch.setattr(persistent_agents, "_log_evento", no_log)
    monkeypatch.setattr(persistent_agents, "enqueue", no_early_push)
    return {"worker_id": worker_id, "conversation_id": conversation_id}


def _user() -> CurrentUser:
    tenant_id = uuid.uuid4()
    return CurrentUser(
        user_id=uuid.uuid4(),
        tenant=TenantCtx(tenant_id=tenant_id, plan_key="hosted_basic", flags={}),
    )


async def test_result_and_outbox_encolados_antes_del_terminal_con_message_id_real(
    push_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    outbox_payloads: list[dict[str, Any]] = []
    message_id = uuid.uuid4()

    async def turn(**_kwargs: Any):
        order.append("persisted")
        yield _format_sse("message.delta", {"type": "text_delta", "text": "Resultado real"})
        yield _format_sse(
            "message.done",
            {"type": "done", "usage": {}, "message_id": str(message_id)},
        )

    async def enqueue_outbox(
        _session: Any, *, tenant_id: uuid.UUID, job_type: str, payload: dict[str, Any]
    ) -> uuid.UUID:
        order.append("outbox")
        assert job_type == "notify_important_event"
        outbox_payloads.append(payload)
        return uuid.uuid4()

    monkeypatch.setattr(bot_turn_service, "stream_worker_turn", turn)
    monkeypatch.setattr(queue, "enqueue_outbox", enqueue_outbox, raising=False)
    user = _user()
    response = await persistent_agents.send_worker_message(
        push_env["worker_id"],
        PersistentAgentMessageIn(text="hazlo"),
        SimpleNamespace(headers={"Idempotency-Key": str(uuid.uuid4())}),
        user,
        _Session(order),
        SimpleNamespace(CHAT_IDEMPOTENCY_TTL_SECONDS=3600),
    )

    body = await _consume(response, order)

    assert "Resultado real" in body
    # El commit ya NO se hace a mitad del stream (rompía la transacción de
    # `get_tenant_session` y dejaba mensajes sin persistir); lo hace
    # `get_session` al cerrar el request. El orden garantiza que el push se
    # encola con el message_id real ANTES de exponer el terminal.
    assert order == ["persisted", "outbox", "terminal"]
    assert outbox_payloads[0]["message_id"] == str(message_id)
    assert outbox_payloads[0]["chat_id"] == str(push_env["conversation_id"])


async def test_failed_turn_never_enqueues_success_notification(
    push_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    outbox_payloads: list[dict[str, Any]] = []

    async def failed_turn(**_kwargs: Any):
        yield _format_sse("error", {"type": "error", "message": "falló"})

    async def enqueue_outbox(_session: Any, **kwargs: Any) -> uuid.UUID:
        outbox_payloads.append(kwargs["payload"])
        return uuid.uuid4()

    monkeypatch.setattr(bot_turn_service, "stream_worker_turn", failed_turn)
    monkeypatch.setattr(queue, "enqueue_outbox", enqueue_outbox, raising=False)
    user = _user()
    response = await persistent_agents.send_worker_message(
        push_env["worker_id"],
        PersistentAgentMessageIn(text="hazlo"),
        SimpleNamespace(headers={"Idempotency-Key": str(uuid.uuid4())}),
        user,
        _Session([]),
        SimpleNamespace(CHAT_IDEMPOTENCY_TTL_SECONDS=3600),
    )

    body = await _consume(response, [])

    assert "event: error" in body
    assert outbox_payloads == []


async def test_terminal_event_carries_the_exact_persisted_assistant_message_id() -> None:
    from edecan_api.routers.conversations import _stream_agent_events

    message_id = uuid.uuid4()

    class _Repo:
        async def add_message(self, **_kwargs: Any) -> dict[str, Any]:
            return {"id": message_id}

        async def add_usage_event(self, **_kwargs: Any) -> None:
            return None

    async def events():
        yield {"type": "text_delta", "text": "respuesta"}
        yield {"type": "done", "usage": {}}

    chunks = [
        chunk
        async for chunk in _stream_agent_events(
            events=events(),
            repo=_Repo(),  # type: ignore[arg-type]
            tenant_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            settings=SimpleNamespace(),
            redis_client=object(),  # type: ignore[arg-type]
            split_messages=True,
        )
    ]

    terminal = next(chunk for chunk in chunks if "event: message.done" in chunk)
    terminal_payload = terminal.split("data: ", 1)[1].strip()
    import json

    assert json.loads(terminal_payload)["message_id"] == str(message_id)
