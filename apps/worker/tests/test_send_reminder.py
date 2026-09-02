"""Tests del job `send_reminder`: marca enviado + wake companion (sin copy en Python)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import edecan_worker.handlers.send_reminder as send_reminder_module
from edecan_schemas import JobEnvelope
from edecan_core.companion_wake_enqueue import RUN_COMPANION_TURN_JOB
from fakes import FakeRepo, install_companion_wake_capture, make_deps


def _envelope(*, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="send_reminder",
        payload={"reminder_id": str(reminder_id)},
    )


async def test_send_reminder_crea_conversacion_y_encola_wake(monkeypatch) -> None:
    fake_repo = FakeRepo()
    capture = install_companion_wake_capture(monkeypatch)
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    reminder_id = uuid.uuid4()
    fake_repo.reminders[reminder_id] = {
        "id": reminder_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "due_at": datetime.now(UTC),
        "message": "Renovar el dominio",
        "status": "pending",
    }

    deps = make_deps()
    await send_reminder_module.handle(_envelope(tenant_id=tenant_id, reminder_id=reminder_id), deps)

    assert fake_repo.reminders[reminder_id]["status"] == "sent"
    assert len(fake_repo.conversations) == 1
    conversation = next(iter(fake_repo.conversations.values()))
    assert conversation["title"] == "Recordatorios"
    assert conversation["channel"] == "api"
    assert conversation["user_id"] == user_id

    assert fake_repo.messages == []
    assert len(capture.companion_wakes()) == 1
    wake = capture.companion_wakes()[0]
    assert wake["job_type"] == RUN_COMPANION_TURN_JOB
    payload = wake["payload"]
    assert payload["require_message"] is True
    assert payload["source"] == "reminder_triggered"
    assert payload["conversation_id"] == str(conversation["id"])
    assert str(reminder_id) in payload["instruction"]
    assert "Renovar el dominio" in payload["instruction"]
    assert payload["notification"] == {
        "kind": "reminder_triggered",
        "event_id": str(uuid.uuid5(reminder_id, str(fake_repo.reminders[reminder_id]["due_at"]))),
    }


async def test_send_reminder_reutiliza_conversacion_existente(monkeypatch) -> None:
    fake_repo = FakeRepo()
    install_companion_wake_capture(monkeypatch)
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    r1 = uuid.uuid4()
    fake_repo.reminders[r1] = {
        "id": r1,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "due_at": datetime.now(UTC),
        "message": "Primero",
        "status": "pending",
    }
    deps = make_deps()
    await send_reminder_module.handle(_envelope(tenant_id=tenant_id, reminder_id=r1), deps)

    r2 = uuid.uuid4()
    fake_repo.reminders[r2] = {
        "id": r2,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "due_at": datetime.now(UTC),
        "message": "Segundo",
        "status": "pending",
    }
    await send_reminder_module.handle(_envelope(tenant_id=tenant_id, reminder_id=r2), deps)

    assert len(fake_repo.conversations) == 1
    assert fake_repo.messages == []


async def test_send_reminder_recordatorio_inexistente_no_falla(monkeypatch) -> None:
    fake_repo = FakeRepo()
    install_companion_wake_capture(monkeypatch)
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    deps = make_deps()
    await send_reminder_module.handle(
        _envelope(tenant_id=uuid.uuid4(), reminder_id=uuid.uuid4()), deps
    )

    assert fake_repo.messages == []


async def test_send_reminder_ya_enviado_se_ignora(monkeypatch) -> None:
    fake_repo = FakeRepo()
    capture = install_companion_wake_capture(monkeypatch)
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    reminder_id = uuid.uuid4()
    fake_repo.reminders[reminder_id] = {
        "id": reminder_id,
        "tenant_id": tenant_id,
        "user_id": uuid.uuid4(),
        "due_at": datetime.now(UTC),
        "message": "Ya se mandó",
        "status": "sent",
    }

    deps = make_deps()
    await send_reminder_module.handle(_envelope(tenant_id=tenant_id, reminder_id=reminder_id), deps)

    assert fake_repo.messages == []
    assert fake_repo.conversations == {}
    assert capture.companion_wakes() == []


async def test_send_reminder_channel_mobile_encola_wake_y_llama_al_push(monkeypatch) -> None:
    fake_repo = FakeRepo()
    install_companion_wake_capture(monkeypatch)
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    llamadas: list[dict] = []

    async def _fake_enviar_push_a_usuario(deps, *, tenant_id, user_id, titulo, cuerpo, data):
        llamadas.append(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "titulo": titulo,
                "cuerpo": cuerpo,
                "data": data,
            }
        )
        return send_reminder_module.push.ResultadoEnvioPush(enviados=1, fallidos=0)

    monkeypatch.setattr(
        send_reminder_module.push, "enviar_push_a_usuario", _fake_enviar_push_a_usuario
    )

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    reminder_id = uuid.uuid4()
    fake_repo.reminders[reminder_id] = {
        "id": reminder_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "due_at": datetime.now(UTC),
        "message": "Recoger el paquete",
        "status": "pending",
        "channel": "mobile",
    }

    deps = make_deps()
    await send_reminder_module.handle(_envelope(tenant_id=tenant_id, reminder_id=reminder_id), deps)

    assert fake_repo.reminders[reminder_id]["status"] == "sent"
    assert fake_repo.messages == []

    assert len(llamadas) == 1
    assert llamadas[0]["tenant_id"] == tenant_id
    assert llamadas[0]["user_id"] == user_id
    assert llamadas[0]["titulo"] == send_reminder_module.TITULO_PUSH
    assert llamadas[0]["cuerpo"] == "Recoger el paquete"
    assert llamadas[0]["data"] == {
        "route": "activity",
        "kind": "reminder",
        "resource_id": str(reminder_id),
    }


async def test_send_reminder_channel_mobile_push_falla_no_revienta_el_job(monkeypatch) -> None:
    fake_repo = FakeRepo()
    install_companion_wake_capture(monkeypatch)
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    async def _push_que_revienta(deps, *, tenant_id, user_id, titulo, cuerpo, data):
        raise RuntimeError("bug hipotético en push.py")

    monkeypatch.setattr(send_reminder_module.push, "enviar_push_a_usuario", _push_que_revienta)

    tenant_id = uuid.uuid4()
    reminder_id = uuid.uuid4()
    fake_repo.reminders[reminder_id] = {
        "id": reminder_id,
        "tenant_id": tenant_id,
        "user_id": uuid.uuid4(),
        "due_at": datetime.now(UTC),
        "message": "No debe perderse",
        "status": "pending",
        "channel": "mobile",
    }

    deps = make_deps()
    await send_reminder_module.handle(
        _envelope(tenant_id=tenant_id, reminder_id=reminder_id), deps
    )

    assert fake_repo.reminders[reminder_id]["status"] == "sent"
    assert fake_repo.messages == []


async def test_send_reminder_channel_mobile_sin_devices_ni_credenciales_no_revienta(
    monkeypatch,
) -> None:
    fake_repo = FakeRepo()
    install_companion_wake_capture(monkeypatch)
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    reminder_id = uuid.uuid4()
    fake_repo.reminders[reminder_id] = {
        "id": reminder_id,
        "tenant_id": tenant_id,
        "user_id": uuid.uuid4(),
        "due_at": datetime.now(UTC),
        "message": "Sin push configurado todavía",
        "status": "pending",
        "channel": "mobile",
    }

    deps = make_deps()
    await send_reminder_module.handle(
        _envelope(tenant_id=tenant_id, reminder_id=reminder_id), deps
    )

    assert fake_repo.reminders[reminder_id]["status"] == "sent"
    assert fake_repo.messages == []


async def test_send_reminder_channel_voice_encola_wake_sin_push_directo(monkeypatch) -> None:
    fake_repo = FakeRepo()
    capture = install_companion_wake_capture(monkeypatch)
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    llamado = False

    async def _push_no_deberia_llamarse(*args, **kwargs):
        nonlocal llamado
        llamado = True
        return send_reminder_module.push.ResultadoEnvioPush(0, 0)

    monkeypatch.setattr(
        send_reminder_module.push, "enviar_push_a_usuario", _push_no_deberia_llamarse
    )

    tenant_id = uuid.uuid4()
    reminder_id = uuid.uuid4()
    fake_repo.reminders[reminder_id] = {
        "id": reminder_id,
        "tenant_id": tenant_id,
        "user_id": uuid.uuid4(),
        "due_at": datetime.now(UTC),
        "message": "Llamar al cliente",
        "status": "pending",
        "channel": "voice",
    }

    deps = make_deps()
    await send_reminder_module.handle(_envelope(tenant_id=tenant_id, reminder_id=reminder_id), deps)

    assert fake_repo.messages == []
    assert llamado is False
    assert len(capture.companion_wakes()) == 1


async def test_send_reminder_channel_web_lleva_notification_en_wake(monkeypatch) -> None:
    fake_repo = FakeRepo()
    capture = install_companion_wake_capture(monkeypatch)
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    reminder_id = uuid.uuid4()
    due_at = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    fake_repo.reminders[reminder_id] = {
        "id": reminder_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "due_at": due_at,
        "message": "Pagar el arriendo",
        "status": "pending",
        "channel": "web",
    }

    deps = make_deps()
    await send_reminder_module.handle(_envelope(tenant_id=tenant_id, reminder_id=reminder_id), deps)

    [wake] = capture.companion_wakes()
    occurrence_id = uuid.uuid5(reminder_id, str(due_at))
    assert wake["payload"]["notification"] == {
        "kind": "reminder_triggered",
        "event_id": str(occurrence_id),
    }


async def test_send_reminder_channel_mobile_no_duplica_con_notify_important_event(
    monkeypatch,
) -> None:
    fake_repo = FakeRepo()
    install_companion_wake_capture(monkeypatch)
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    llamadas_notify: list[dict] = []

    async def _fake_notify(deps, event) -> None:
        llamadas_notify.append({"kind": event.kind})

    monkeypatch.setattr(send_reminder_module, "notify_important_event", _fake_notify)

    async def _fake_push(deps, *, tenant_id, user_id, titulo, cuerpo, data):
        return send_reminder_module.push.ResultadoEnvioPush(enviados=1, fallidos=0)

    monkeypatch.setattr(send_reminder_module.push, "enviar_push_a_usuario", _fake_push)

    tenant_id = uuid.uuid4()
    reminder_id = uuid.uuid4()
    fake_repo.reminders[reminder_id] = {
        "id": reminder_id,
        "tenant_id": tenant_id,
        "user_id": uuid.uuid4(),
        "due_at": datetime.now(UTC),
        "message": "Recoger el paquete",
        "status": "pending",
        "channel": "mobile",
    }

    deps = make_deps()
    await send_reminder_module.handle(_envelope(tenant_id=tenant_id, reminder_id=reminder_id), deps)

    assert llamadas_notify == []


async def test_send_reminder_ocurrencias_distintas_producen_wake_keys_distintas(
    monkeypatch,
) -> None:
    fake_repo = FakeRepo()
    capture = install_companion_wake_capture(monkeypatch)
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    reminder_id = uuid.uuid4()

    fake_repo.reminders[reminder_id] = {
        "id": reminder_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "due_at": datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
        "message": "Regar las plantas",
        "status": "pending",
        "channel": "web",
    }
    deps = make_deps()
    await send_reminder_module.handle(_envelope(tenant_id=tenant_id, reminder_id=reminder_id), deps)

    fake_repo.reminders[reminder_id]["due_at"] = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)
    fake_repo.reminders[reminder_id]["status"] = "pending"
    await send_reminder_module.handle(_envelope(tenant_id=tenant_id, reminder_id=reminder_id), deps)

    wake_keys = [w["payload"]["wake_key"] for w in capture.companion_wakes()]
    assert len(wake_keys) == 2
    assert wake_keys[0] != wake_keys[1]
