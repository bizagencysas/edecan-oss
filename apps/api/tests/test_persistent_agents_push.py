"""Push del turno de un bot: aviso temprano al primer delta, sin duplicar el final.

`POST /v1/agents/workers/{worker_id}/message` emite el turno como SSE. El
notify temprano se encola al PRIMER delta de texto (`message.delta`/
`text_delta`) y el notify de fin de turno comparte el MISMO event_id: la
deduplicación durable de `record_notification_event` (terna
tenant+user+kind:event_id) garantiza un solo push por turno.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import auth_headers

import edecan_api.bot_turn_service as bot_turn_service
import edecan_api.deps as edecan_deps
import edecan_api.routers.persistent_agents as persistent_agents
from edecan_api.routers.conversations import _format_sse


class _FakeFreshSession:
    """Doble de `edecan_db.session.get_session`: nunca lee fila alguna."""

    async def __aenter__(self) -> _FakeFreshSession:
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def execute(self, _clause: Any, _params: dict | None = None) -> Any:
        return _FakeResult()


class _FakeResult:
    def mappings(self) -> _FakeMappings:
        return _FakeMappings()


class _FakeMappings:
    def first(self) -> None:
        return None


@pytest.fixture
def bot_turn_env(monkeypatch: pytest.MonkeyPatch, fake_redis) -> dict[str, Any]:
    """Fakea el turno completo y captura los jobs encolados por el endpoint."""
    worker_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    enqueued: list[tuple[str, dict[str, Any], Any]] = []

    async def fake_load_worker(_session, _user, _worker_id):
        return {
            "id": str(worker_id),
            "name": "BotX",
            "display_name": "BotX",
            "avatar": {"shape": "circle", "fill": "#6366f1", "accent": "#22c55e"},
        }

    async def fake_ensure_conversation(_session, _user, _worker):
        return conversation_id

    def fake_display_name(_worker) -> str:
        return "BotX"

    async def fake_enqueue(_settings, job_type, payload, tenant):
        enqueued.append((job_type, payload, tenant))

    async def fake_enqueue_outbox(session, *, tenant_id, job_type, payload):
        enqueued.append((job_type, payload, tenant_id))

    monkeypatch.setattr(bot_turn_service, "load_worker", fake_load_worker)
    monkeypatch.setattr(bot_turn_service, "ensure_worker_conversation", fake_ensure_conversation)
    monkeypatch.setattr(bot_turn_service, "worker_display_name", fake_display_name)
    monkeypatch.setattr(persistent_agents, "enqueue", fake_enqueue)
    monkeypatch.setattr("edecan_core.queue.enqueue_outbox", fake_enqueue_outbox)
    # `get_redis` y `get_session` se invocan DIRECTOS (no por Depends) dentro
    # del endpoint, así que el `dependency_overrides` del app fixture no los
    # cubre: se pisan en el módulo para que el turno no abra Redis/Postgres.
    monkeypatch.setattr(edecan_deps, "get_redis", lambda settings: fake_redis)
    monkeypatch.setattr("edecan_db.session.get_session", lambda tenant_id: _FakeFreshSession())
    return {"worker_id": worker_id, "conversation_id": conversation_id, "enqueued": enqueued}


def _notifies(enqueued: list[tuple[str, dict[str, Any], Any]]) -> list[dict[str, Any]]:
    return [payload for job_type, payload, _tenant in enqueued if job_type == "notify_important_event"]


async def test_push_temprano_al_primer_delta_y_final_comparte_event_id(
    client, bot_turn_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    deltas = ["Voy a revisar eso…", " y aquí va el resto."]

    async def fake_turn(**_kwargs):
        yield _format_sse("message.started", {"type": "started"})
        yield _format_sse("tool.start", {"type": "tool_start", "name": "buscar"})
        yield _format_sse("message.delta", {"type": "text_delta", "text": deltas[0]})
        yield _format_sse("message.delta", {"type": "text_delta", "text": deltas[1]})
        yield _format_sse("message.done", {"type": "done", "usage": {}, "message_id": str(uuid.uuid4())})

    monkeypatch.setattr(bot_turn_service, "stream_worker_turn", fake_turn)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    resp = await client.post(
        f"/v1/agents/workers/{bot_turn_env['worker_id']}/message",
        json={"text": "hola"},
        headers=headers,
    )

    assert resp.status_code == 200
    assert deltas[0] in resp.text and deltas[1] in resp.text  # el stream sigue intacto

    notifies = _notifies(bot_turn_env["enqueued"])
    assert len(notifies) == 2  # un temprano + el final; jamás uno por delta

    temprano, final = notifies
    assert temprano["kind"] == "agent_bot_message"
    assert final["kind"] == "agent_bot_message"
    assert temprano["event_id"] == final["event_id"]  # dedup durable garantizado
    assert temprano["chat_id"] == str(bot_turn_env["conversation_id"])
    assert temprano["user_id"] == str(user_id)
    assert temprano["apns_title"] == "BotX"
    assert "Voy a revisar eso" in temprano["apns_body"]
    assert temprano["worker_id"] == str(bot_turn_env["worker_id"])
    assert temprano["sender_display_name"] == "BotX"
    assert temprano["avatar_shape"] == "circle"
    assert temprano["avatar_fill"] == "#6366f1"
    assert temprano["avatar_accent"] == "#22c55e"
    # B-16: el push final lleva el texto REAL del turno.
    assert "Voy a revisar eso" in final["apns_body"]


async def test_sin_delta_de_texto_no_encola_push_temprano(
    client, bot_turn_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_turn(**_kwargs):
        yield _format_sse("message.started", {"type": "started"})
        yield _format_sse("tool.start", {"type": "tool_start", "name": "buscar"})
        yield _format_sse("tool.end", {"type": "tool_end", "name": "buscar"})
        yield _format_sse("message.done", {"type": "done", "usage": {}, "message_id": str(uuid.uuid4())})

    monkeypatch.setattr(bot_turn_service, "stream_worker_turn", fake_turn)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    resp = await client.post(
        f"/v1/agents/workers/{bot_turn_env['worker_id']}/message",
        json={"text": "hola"},
        headers=headers,
    )

    assert resp.status_code == 200
    notifies = _notifies(bot_turn_env["enqueued"])
    assert len(notifies) == 1
    assert notifies[0]["apns_body"] == "Terminé. Abre el chat para ver la respuesta."