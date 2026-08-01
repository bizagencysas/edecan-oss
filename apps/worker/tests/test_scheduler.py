"""Tests de `edecan_worker.scheduler` (loop de desarrollo que encola los jobs
de sistema periódicos: `send_reminder_scan` y `sync_connector`)."""

from __future__ import annotations

import asyncio
import uuid

import edecan_worker.scheduler as scheduler_module
from edecan_worker.config import Settings
from fakes import install_fake_edecan_core_queue


async def test_tick_encola_los_jobs_de_sistema_sin_tenant(monkeypatch) -> None:
    llamadas = []

    async def fake_enqueue(settings, job_type, payload, tenant_id):
        llamadas.append((job_type, payload, tenant_id))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)

    await scheduler_module._tick(Settings())

    assert llamadas == [("send_reminder_scan", {}, None), ("sync_connector", {}, None)]


async def test_tick_encola_sync_connector_aunque_falle_send_reminder_scan(monkeypatch) -> None:
    llamadas = []

    async def fake_enqueue(settings, job_type, payload, tenant_id):
        if job_type == "send_reminder_scan":
            raise RuntimeError("SQS no disponible")
        llamadas.append((job_type, payload, tenant_id))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)

    await scheduler_module._tick(Settings())  # no debe propagar la excepción

    assert llamadas == [("sync_connector", {}, None)]


async def test_run_forever_se_detiene_al_marcar_el_stop_event(monkeypatch) -> None:
    conteo = 0

    async def fake_enqueue(settings, job_type, payload, tenant_id):
        nonlocal conteo
        conteo += 1
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)
    # 1h: no debe llegar a esperar tanto, stop_event se dispara antes.
    monkeypatch.setattr(scheduler_module, "INTERVALO_SEGUNDOS", 3600)

    stop_event = asyncio.Event()

    async def detener_tras_un_tick():
        while conteo < 1:
            await asyncio.sleep(0)
        stop_event.set()

    await asyncio.wait_for(
        asyncio.gather(
            scheduler_module.run_forever(Settings(), stop_event=stop_event), detener_tras_un_tick()
        ),
        timeout=5,
    )

    # Un solo tick encola todos los jobs de JOBS_PERIODICOS (send_reminder_scan
    # + sync_connector) antes de que el loop vuelva a ceder el control y
    # `detener_tras_un_tick` pueda observar el stop_event.
    assert conteo == len(scheduler_module.JOBS_PERIODICOS)


async def test_run_forever_sigue_si_enqueue_falla(monkeypatch) -> None:
    intentos = 0

    async def fake_enqueue_falla(settings, job_type, payload, tenant_id):
        nonlocal intentos
        intentos += 1
        raise RuntimeError("SQS no disponible")

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue_falla)
    monkeypatch.setattr(scheduler_module, "INTERVALO_SEGUNDOS", 0)

    stop_event = asyncio.Event()

    async def detener_tras_dos_intentos():
        while intentos < 2:
            await asyncio.sleep(0)
        stop_event.set()

    await asyncio.wait_for(
        asyncio.gather(
            scheduler_module.run_forever(Settings(), stop_event=stop_event),
            detener_tras_dos_intentos(),
        ),
        timeout=5,
    )

    assert intentos >= 2
