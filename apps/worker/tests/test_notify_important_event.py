from __future__ import annotations

import uuid

import edecan_worker.handlers.notify_important_event as handler
import pytest
from edecan_schemas import JobEnvelope
from fakes import make_deps


async def test_handler_builds_a_strict_uuid_only_event(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    event_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    seen = []

    async def notify(_deps, event):
        seen.append(event)

    monkeypatch.setattr(handler, "notify_important_event", notify)
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="notify_important_event",
        payload={
            "user_id": str(user_id),
            "kind": "design_ready",
            "event_id": str(event_id),
            "artifact_id": str(artifact_id),
        },
    )

    await handler.handle(env, make_deps())

    assert len(seen) == 1
    assert seen[0].tenant_id == tenant_id
    assert seen[0].user_id == user_id
    assert seen[0].event_id == event_id
    assert seen[0].artifact_id == artifact_id


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user_id": "not-a-uuid", "event_id": str(uuid.uuid4()), "kind": "design_ready"},
        {"user_id": str(uuid.uuid4()), "event_id": "free-text", "kind": "design_ready"},
        {"user_id": str(uuid.uuid4()), "event_id": str(uuid.uuid4()), "kind": "invented"},
    ],
)
async def test_handler_rejects_untrusted_payload(payload: dict[str, str]) -> None:
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        type="notify_important_event",
        payload=payload,
    )
    with pytest.raises(ValueError):
        await handler.handle(env, make_deps())


async def test_handler_accepts_apns_text_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los productores pueden titular el push (p. ej. «{bot} terminó: …»).

    El override viaja por la cola, se sanitiza (colapsa espacios) y se acota —
    la superficie del push nunca recibe texto libre sin límite.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    event_id = uuid.uuid4()
    seen = []

    async def notify(_deps, event):
        seen.append(event)

    monkeypatch.setattr(handler, "notify_important_event", notify)
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="notify_important_event",
        payload={
            "user_id": str(user_id),
            "kind": "agent_message",
            "event_id": str(event_id),
            "chat_id": str(uuid.uuid4()),
            "apns_title": "BotAlpha  terminó   ",
            "apns_body": "La respuesta   con espacios  dobles. " + "x" * 300,
        },
    )

    await handler.handle(env, make_deps())

    assert len(seen) == 1
    assert seen[0].apns_title == "BotAlpha terminó"
    # 200 chars máximo, sin saltos dobles.
    assert seen[0].apns_body is not None
    assert len(seen[0].apns_body) <= 200
    assert "  " not in seen[0].apns_body


class _FakePresencia:
    """Registro de presencia falso: `esta_activa` responde según un set."""

    def __init__(self, activos: set[uuid.UUID]) -> None:
        self.activos = activos
        self.consultas: list[uuid.UUID] = []

    def esta_activa(self, conversation_id: uuid.UUID) -> bool:
        self.consultas.append(conversation_id)
        return conversation_id in self.activos


async def test_handler_suppresses_push_when_owner_is_in_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regla del producto: no hay push si el dueño está DENTRO del chat."""
    chat_id = uuid.uuid4()
    seen = []

    async def notify(_deps, event):
        seen.append(event)

    fake_presencia = _FakePresencia({chat_id})
    monkeypatch.setattr(handler, "notify_important_event", notify)
    monkeypatch.setattr(handler, "_presencia", lambda: fake_presencia)
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        type="notify_important_event",
        payload={
            "user_id": str(uuid.uuid4()),
            "kind": "agent_message",
            "event_id": str(uuid.uuid4()),
            "chat_id": str(chat_id),
        },
    )

    resultado = await handler.handle(env, make_deps())

    assert seen == []
    assert fake_presencia.consultas == [chat_id]
    assert resultado is not None
    assert resultado["suppressed"] is True
    assert resultado["reason"] == "suppressed_in_chat"
    assert resultado["chat_id"] == str(chat_id)


async def test_handler_delivers_push_when_owner_is_not_in_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin presencia activa en el chat, la entrega sigue normal."""
    chat_id = uuid.uuid4()
    seen = []

    async def notify(_deps, event):
        seen.append(event)

    fake_presencia = _FakePresencia(set())
    monkeypatch.setattr(handler, "notify_important_event", notify)
    monkeypatch.setattr(handler, "_presencia", lambda: fake_presencia)
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        type="notify_important_event",
        payload={
            "user_id": str(uuid.uuid4()),
            "kind": "agent_message",
            "event_id": str(uuid.uuid4()),
            "chat_id": str(chat_id),
        },
    )

    resultado = await handler.handle(env, make_deps())

    assert len(seen) == 1
    assert seen[0].chat_id == chat_id
    assert fake_presencia.consultas == [chat_id]
    assert resultado is None


async def test_handler_falls_open_si_falta_el_modulo_de_presencia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker sin la API (sin `edecan_api.presencia`): entrega el push igual."""
    chat_id = uuid.uuid4()
    seen = []

    async def notify(_deps, event):
        seen.append(event)

    monkeypatch.setattr(handler, "notify_important_event", notify)
    monkeypatch.setattr(handler, "_presencia", lambda: None)
    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        type="notify_important_event",
        payload={
            "user_id": str(uuid.uuid4()),
            "kind": "agent_message",
            "event_id": str(uuid.uuid4()),
            "chat_id": str(chat_id),
        },
    )

    resultado = await handler.handle(env, make_deps())

    assert len(seen) == 1
    assert seen[0].chat_id == chat_id
    assert resultado is None
