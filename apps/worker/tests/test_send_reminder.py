"""Tests del job `send_reminder`: marca enviado + mensaje en "Recordatorios"."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import edecan_worker.handlers.send_reminder as send_reminder_module
from edecan_schemas import JobEnvelope
from fakes import FakeRepo, make_deps


def _envelope(*, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="send_reminder",
        payload={"reminder_id": str(reminder_id)},
    )


async def test_send_reminder_crea_conversacion_y_manda_mensaje(monkeypatch) -> None:
    fake_repo = FakeRepo()
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

    assert len(fake_repo.messages) == 1
    message = fake_repo.messages[0]
    assert message["role"] == "assistant"
    assert "Renovar el dominio" in message["content"]["text"]
    assert message["conversation_id"] == conversation["id"]


async def test_send_reminder_reutiliza_conversacion_existente(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Primer recordatorio: crea la conversación.
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

    # Segundo recordatorio del mismo usuario: debe reusar la misma conversación.
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
    assert len(fake_repo.messages) == 2


async def test_send_reminder_recordatorio_inexistente_no_falla(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)

    deps = make_deps()
    await send_reminder_module.handle(
        _envelope(tenant_id=uuid.uuid4(), reminder_id=uuid.uuid4()), deps
    )  # no debe lanzar

    assert fake_repo.messages == []


async def test_send_reminder_ya_enviado_se_ignora(monkeypatch) -> None:
    fake_repo = FakeRepo()
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


# ---------------------------------------------------------------------------
# channel="mobile" (v5, ARCHITECTURE.md §14, WP-V5-13): push nativo ADEMÁS
# del mensaje de chat de siempre, nunca en su lugar.
# ---------------------------------------------------------------------------


async def test_send_reminder_channel_mobile_crea_mensaje_y_llama_al_push(monkeypatch) -> None:
    fake_repo = FakeRepo()
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

    # El mensaje de chat se crea igual que en cualquier otro canal.
    assert fake_repo.reminders[reminder_id]["status"] == "sent"
    assert len(fake_repo.messages) == 1
    assert "Recoger el paquete" in fake_repo.messages[0]["content"]["text"]

    # Y ADEMÁS se llamó al push, con el título/cuerpo/tenant/usuario correctos.
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
    """Si `push.enviar_push_a_usuario` lanza (defensa en profundidad — en la
    práctica nunca debería, ver su propio docstring), el job de todos modos
    termina limpio y el mensaje/estado del recordatorio ya quedaron
    guardados: el `try/except` de `handle` es la segunda red de seguridad."""
    fake_repo = FakeRepo()
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
    )  # no debe lanzar

    assert fake_repo.reminders[reminder_id]["status"] == "sent"
    assert len(fake_repo.messages) == 1


async def test_send_reminder_channel_mobile_sin_devices_ni_credenciales_no_revienta(
    monkeypatch,
) -> None:
    """Sin monkeypatch de `push`: ejercita `push.enviar_push_a_usuario` REAL
    contra el `Deps` de prueba (`make_deps()` — sesión/vault fakes vacíos,
    sin ninguna credencial push ni dispositivo real que contactar). El
    mensaje de la conversación se crea igual, y el job termina sin lanzar —
    el push es SIEMPRE best-effort (`ARCHITECTURE.md` §14)."""
    fake_repo = FakeRepo()
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
    )  # no debe lanzar

    assert fake_repo.reminders[reminder_id]["status"] == "sent"
    assert len(fake_repo.messages) == 1
    assert "Sin push configurado todavía" in fake_repo.messages[0]["content"]["text"]


async def test_send_reminder_channel_voice_sigue_sin_entrega_dedicada_y_no_llama_al_push(
    monkeypatch,
) -> None:
    """Regresión: un canal de `CANALES_SIN_ENTREGA_DEDICADA` (`voice`/`phone`)
    NO debe disparar `push.enviar_push_a_usuario` — solo `"mobile"` lo hace."""
    fake_repo = FakeRepo()
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

    assert len(fake_repo.messages) == 1
    assert llamado is False


# ---------------------------------------------------------------------------
# Cobertura universal para channel != "mobile" (frente 2, "Push para todo",
# ver docstring del módulo). Se monkeypatchea `notify_important_event` (mismo
# patrón que `test_notify_incoming_phone_call.py`/
# `test_automation_handlers.py`) en vez de darle a `FakeSession`/`make_deps()`
# soporte para el SQL de `audit_log` — la suite exhaustiva del contrato
# universal en sí ya vive en `test_universal_notifications.py`.
# ---------------------------------------------------------------------------


def _install_fake_notify(monkeypatch) -> list[dict]:
    llamadas: list[dict] = []

    async def _fake_notify(deps, event) -> None:
        llamadas.append(
            {
                "tenant_id": event.tenant_id,
                "user_id": event.user_id,
                "kind": event.kind,
                "event_id": event.event_id,
                "resource_id": event.resource_id,
            }
        )

    monkeypatch.setattr(send_reminder_module, "notify_important_event", _fake_notify)
    return llamadas


async def test_send_reminder_channel_web_avisa_reminder_triggered(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)
    llamadas = _install_fake_notify(monkeypatch)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    reminder_id = uuid.uuid4()
    fake_repo.reminders[reminder_id] = {
        "id": reminder_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "due_at": datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
        "message": "Pagar el arriendo",
        "status": "pending",
        "channel": "web",
    }

    deps = make_deps()
    await send_reminder_module.handle(_envelope(tenant_id=tenant_id, reminder_id=reminder_id), deps)

    assert len(llamadas) == 1
    assert llamadas[0]["tenant_id"] == tenant_id
    assert llamadas[0]["user_id"] == user_id
    assert llamadas[0]["kind"] == "reminder_triggered"
    assert llamadas[0]["resource_id"] == reminder_id
    # Ver docstring del módulo: no es `reminder_id` a secas.
    assert llamadas[0]["event_id"] != reminder_id


async def test_send_reminder_channel_mobile_no_duplica_con_notify_important_event(
    monkeypatch,
) -> None:
    """`channel="mobile"` ya recibe su push nativo con el texto real (arriba
    en este archivo) — NO debe además pasar por `notify_important_event`, o
    el dueño recibiría dos push por el mismo recordatorio."""
    fake_repo = FakeRepo()
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)
    llamadas = _install_fake_notify(monkeypatch)

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

    assert llamadas == []


async def test_send_reminder_ocurrencias_distintas_del_mismo_recordatorio_avisan_cada_vez(
    monkeypatch,
) -> None:
    """Regresión directa del caso `rrule` (ver docstring del módulo): dos
    disparos del MISMO `reminder_id` con `due_at` distinto (simulando lo que
    `repo.mark_reminder_sent` hace de verdad al reprogramar) deben producir
    `event_id` DISTINTOS -- si no, `record_notification_event` trataría el
    segundo disparo real como duplicado del primero y jamás avisaría."""
    fake_repo = FakeRepo()
    monkeypatch.setattr(send_reminder_module, "SqlRepo", lambda session: fake_repo)
    llamadas = _install_fake_notify(monkeypatch)

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

    # Simula la reprogramación que `repo.mark_reminder_sent` real hace para
    # un `rrule`: mismo id, `due_at` de la siguiente ocurrencia, 'pending' de
    # nuevo (`FakeRepo.mark_reminder_sent` no simula `rrule`, así que se pisa
    # el estado a mano acá).
    fake_repo.reminders[reminder_id]["due_at"] = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)
    fake_repo.reminders[reminder_id]["status"] = "pending"
    await send_reminder_module.handle(_envelope(tenant_id=tenant_id, reminder_id=reminder_id), deps)

    assert len(llamadas) == 2
    assert llamadas[0]["event_id"] != llamadas[1]["event_id"]
