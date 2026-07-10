"""`scan encola un reminder vencido` (job `send_reminder_scan`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import edecan_worker.handlers.send_reminder_scan as scan_module
import pytest
from edecan_schemas import JobEnvelope
from fakes import FakeRepo, install_fake_edecan_core_queue, make_deps


async def test_scan_encola_un_reminder_vencido(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(scan_module, "SqlRepo", lambda session: fake_repo)

    encolados = []

    async def fake_enqueue(settings, job_type, payload, tenant_id):
        encolados.append((settings, job_type, payload, tenant_id))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    reminder_id = uuid.uuid4()
    ahora = datetime.now(UTC)
    fake_repo.reminders[reminder_id] = {
        "id": reminder_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "due_at": ahora - timedelta(minutes=5),  # vencido
        "message": "Llamar al proveedor",
        "status": "pending",
    }
    # Un recordatorio futuro NO debe encolarse.
    futuro_id = uuid.uuid4()
    fake_repo.reminders[futuro_id] = {
        "id": futuro_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "due_at": ahora + timedelta(hours=1),
        "message": "Todavía no",
        "status": "pending",
    }
    # Un recordatorio ya enviado tampoco.
    enviado_id = uuid.uuid4()
    fake_repo.reminders[enviado_id] = {
        "id": enviado_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "due_at": ahora - timedelta(days=1),
        "message": "Ya se mandó",
        "status": "sent",
    }

    deps = make_deps()
    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=None, type="send_reminder_scan", payload={})
    await scan_module.handle(env, deps)

    assert len(encolados) == 1
    _, job_type, payload, sent_tenant_id = encolados[0]
    assert job_type == "send_reminder"
    assert payload == {"reminder_id": str(reminder_id)}
    assert sent_tenant_id == tenant_id


async def test_scan_sin_reminders_vencidos_no_encola_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(scan_module, "SqlRepo", lambda session: fake_repo)

    encolados = []

    async def fake_enqueue(settings, job_type, payload, tenant_id):
        encolados.append((job_type, payload, tenant_id))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)

    deps = make_deps()
    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=None, type="send_reminder_scan", payload={})
    await scan_module.handle(env, deps)

    assert encolados == []
