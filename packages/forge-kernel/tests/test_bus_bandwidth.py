"""Presupuesto de ancho de banda (MiB/s agregado, no cuenta de suscriptores) y drop-oldest con
`StreamGap` en suscriptores `live_lossy` — `bus.py`, §1.4 líneas 1498-1500."""

from __future__ import annotations

import pytest
from edecan_forge_kernel.bus import (
    SUBSCRIBER_BANDWIDTH_BYTES_PER_S,
    BandwidthMeter,
    EventBus,
    StreamFrame,
)

from tests.conftest import FakeJournal


class _RelojFalso:
    """Reloj determinista e inyectable — evita que un test de "1 MiB/s" dependa de que la
    máquina de CI ejecute 1 MiB de operaciones en menos de un segundo real."""

    def __init__(self, inicio: float = 0.0) -> None:
        self.ahora = inicio

    def __call__(self) -> float:
        return self.ahora

    def avanzar(self, segundos: float) -> None:
        self.ahora += segundos


def test_bandwidth_meter_dentro_del_presupuesto() -> None:
    reloj = _RelojFalso()
    medidor = BandwidthMeter(1000, clock=reloj)
    assert medidor.charge(400) is True
    assert medidor.charge(400) is True
    assert medidor.current_rate() == 800


def test_bandwidth_meter_excede_presupuesto() -> None:
    reloj = _RelojFalso()
    medidor = BandwidthMeter(1000, clock=reloj)
    medidor.charge(700)
    assert medidor.charge(700) is False  # 1400 > 1000 en la misma ventana de 1s
    assert medidor.current_rate() == 1400


def test_bandwidth_meter_ventana_deslizante_purga_lo_viejo() -> None:
    reloj = _RelojFalso()
    medidor = BandwidthMeter(1000, clock=reloj)
    medidor.charge(900)
    reloj.avanzar(1.5)  # fuera de la ventana de 1s
    assert medidor.charge(900) is True  # la carga vieja ya no cuenta
    assert medidor.current_rate() == 900


@pytest.mark.asyncio
async def test_suscriptor_que_excede_su_propio_presupuesto_pasa_a_catchup() -> None:
    bus = EventBus(FakeJournal(), session_id="s1")
    bus.open_ephemeral_stream("evt-1", "proc.stdout")
    sub = bus.subscribe("proc.*", mode="live_lossy")

    # Un único frame más grande que el presupuesto POR SUSCRIPTOR (1 MiB/s) fuerza catch-up.
    frame_grande = b"x" * (SUBSCRIBER_BANDWIDTH_BYTES_PER_S + 1)
    # StreamFrame limita a 64 KiB por frame — se manda en trozos hasta superar el presupuesto.
    trozo = b"x" * (64 * 1024)
    ordinal = 0
    for _ in range(20):  # 20 * 64 KiB = 1.25 MiB > 1 MiB/s
        bus.emit(StreamFrame(anchor="evt-1", channel="proc.stdout", ordinal=ordinal, bytes=trozo))
        ordinal += 1
        if sub.mode != "live_lossy":
            break

    assert sub.mode == "durable_replay"
    del frame_grande


@pytest.mark.asyncio
async def test_suscriptor_mas_caro_se_degrada_primero_al_exceder_presupuesto_de_sesion() -> None:
    bus = EventBus(FakeJournal(), session_id="s1")
    bus.open_ephemeral_stream("evt-1", "proc.stdout")
    barato = bus.subscribe("proc.stdout", mode="live_lossy")
    caro = bus.subscribe("proc.stdout", mode="live_lossy")

    # Ambos reciben cada frame (mismo canal exacto), pero el meter de sesión es compartido:
    # basta con exceder 5 MiB/s agregados para que el bus degrade a UNO de los dos.
    trozo = b"x" * (64 * 1024)
    ordinal = 0
    for _ in range(90):  # 90 * 64 KiB ≈ 5.6 MiB > 5 MiB/s de sesión
        bus.emit(StreamFrame(anchor="evt-1", channel="proc.stdout", ordinal=ordinal, bytes=trozo))
        ordinal += 1

    modos = {barato.mode, caro.mode}
    assert "durable_replay" in modos  # al menos uno se degradó
    assert modos != {"live_lossy"}


def test_deliver_bajo_cola_llena_hace_drop_oldest_y_registra_gap() -> None:
    from edecan_forge_kernel.bus import Subscription

    sub = Subscription("sub-1", "proc.*", mode="live_lossy", queue=2)
    sub.deliver(StreamFrame(anchor="evt-1", channel="proc.stdout", ordinal=0, bytes=b"a"))
    sub.deliver(StreamFrame(anchor="evt-1", channel="proc.stdout", ordinal=1, bytes=b"b"))
    assert sub.gaps == []

    sub.deliver(StreamFrame(anchor="evt-1", channel="proc.stdout", ordinal=2, bytes=b"c"))
    assert len(sub.gaps) == 1
    assert sub.gaps[0].from_ordinal == 0  # se descartó el frame ordinal=0
    assert sub.gaps[0].to_ordinal == 2

    restantes = sub.drain()
    assert [f.ordinal for f in restantes] == [1, 2]
    assert sub.drain() == []  # el buffer quedó vacío tras drain()
