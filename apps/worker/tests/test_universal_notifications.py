from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import edecan_worker.universal_notifications as notifications
import pytest
from edecan_core.notifications import DurableNotificationEvent, ImportantNotificationEvent
from edecan_worker.push import ResultadoEnvioPush
from fakes import make_deps


def _event() -> ImportantNotificationEvent:
    return ImportantNotificationEvent(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        kind="work_completed",
        event_id=uuid.uuid4(),
        resource_id=uuid.uuid4(),
    )


def _deps_with_session(session: object):
    @asynccontextmanager
    async def factory(_tenant_id):
        yield session

    return make_deps(session_factory=factory)


async def test_commits_durable_scope_before_attempting_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope_open = False

    @asynccontextmanager
    async def factory(_tenant_id):
        nonlocal scope_open
        scope_open = True
        yield object()
        scope_open = False

    deps = make_deps(session_factory=factory)

    async def record(_session, _event):
        assert scope_open is True
        return DurableNotificationEvent(uuid.uuid4(), True, True)

    async def send(*_args, **_kwargs):
        assert scope_open is False
        return ResultadoEnvioPush(2, 0)

    monkeypatch.setattr(notifications, "record_notification_event", record)
    monkeypatch.setattr(notifications.push, "enviar_push_a_usuario", send)

    result = await notifications.notify_important_event(deps, _event())

    assert result.durable is True
    assert result.pushed == 2


@pytest.mark.parametrize(
    "durable",
    [
        DurableNotificationEvent(uuid.uuid4(), False, True),
        DurableNotificationEvent(uuid.uuid4(), True, False),
    ],
)
async def test_duplicate_or_disabled_never_pushes(
    monkeypatch: pytest.MonkeyPatch, durable: DurableNotificationEvent
) -> None:
    deps = _deps_with_session(object())

    async def record(_session, _event):
        return durable

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("no debe intentar push")

    monkeypatch.setattr(notifications, "record_notification_event", record)
    monkeypatch.setattr(notifications.push, "enviar_push_a_usuario", forbidden)

    result = await notifications.notify_important_event(deps, _event())

    assert result.pushed == 0
    assert result.duplicate is (not durable.created)


async def test_apns_fcm_failure_does_not_erase_durable_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _deps_with_session(object())

    async def record(_session, _event):
        return DurableNotificationEvent(uuid.uuid4(), True, True)

    async def provider_failure(*_args, **_kwargs):
        # ``fallidos=2`` representa APNs + FCM fallando en el mismo lote.
        return ResultadoEnvioPush(0, 2)

    monkeypatch.setattr(notifications, "record_notification_event", record)
    monkeypatch.setattr(notifications.push, "enviar_push_a_usuario", provider_failure)

    result = await notifications.notify_important_event(deps, _event())

    assert result == notifications.UniversalNotificationResult(True, False, True, 0, 2)


async def test_database_failure_prevents_push(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = _deps_with_session(SimpleNamespace())

    async def record(_session, _event):
        raise RuntimeError("db down")

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("no debe intentar push sin evento durable")

    monkeypatch.setattr(notifications, "record_notification_event", record)
    monkeypatch.setattr(notifications.push, "enviar_push_a_usuario", forbidden)

    result = await notifications.notify_important_event(deps, _event())

    assert result.durable is False


# Frente 3 (deeplink): tocar el push debe abrir la conversación correcta.
# Estas dos pruebas fijan el contrato de lo único que le compete a este
# módulo — que el `data` que le pasa a `push.enviar_push_a_usuario` sea
# exactamente `event.push_data()`, sin recortarlo ni reconstruirlo. La forma
# de `push_data()` (cuándo trae `chat_id`/`deeplink`) es responsabilidad de
# `ImportantNotificationEvent` en `edecan_core.notifications`, no de aquí.


async def test_payload_de_push_lleva_conversacion_cuando_el_evento_la_trae(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _deps_with_session(object())
    chat_id = uuid.uuid4()
    event = ImportantNotificationEvent(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        kind="content_created",
        event_id=uuid.uuid4(),
        chat_id=chat_id,
    )

    async def record(_session, _event):
        return DurableNotificationEvent(uuid.uuid4(), True, True)

    payloads_enviados: list[dict[str, str]] = []

    async def send(_deps, **kwargs):
        payloads_enviados.append(kwargs["data"])
        return ResultadoEnvioPush(1, 0)

    monkeypatch.setattr(notifications, "record_notification_event", record)
    monkeypatch.setattr(notifications.push, "enviar_push_a_usuario", send)

    await notifications.notify_important_event(deps, event)

    assert len(payloads_enviados) == 1
    payload = payloads_enviados[0]
    assert payload["chat_id"] == str(chat_id)
    assert payload["deeplink"] == f"edecan://chat/{chat_id}"
    assert payload["route"] == "assistant"


async def test_payload_de_push_sin_conversacion_no_inventa_una(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin `chat_id` en el evento, el payload tampoco lo trae — la app debe
    conservar el comportamiento actual (abrir la app en la pestaña de
    siempre) en vez de deep-linkear a una conversación inexistente. Se usa
    un evento sin `chat_id` NI `artifact_id`/`resource_id` (a diferencia de
    `_event()`, que sí trae `resource_id` y por tanto sí lleva `deeplink`
    hacia Actividad) para aislar específicamente el caso "sin conversación".
    """
    deps = _deps_with_session(object())
    event = ImportantNotificationEvent(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        kind="push_test",
        event_id=uuid.uuid4(),
    )

    async def record(_session, _event):
        return DurableNotificationEvent(uuid.uuid4(), True, True)

    payloads_enviados: list[dict[str, str]] = []

    async def send(_deps, **kwargs):
        payloads_enviados.append(kwargs["data"])
        return ResultadoEnvioPush(1, 0)

    monkeypatch.setattr(notifications, "record_notification_event", record)
    monkeypatch.setattr(notifications.push, "enviar_push_a_usuario", send)

    await notifications.notify_important_event(deps, event)

    assert len(payloads_enviados) == 1
    assert "chat_id" not in payloads_enviados[0]
    assert "deeplink" not in payloads_enviados[0]
