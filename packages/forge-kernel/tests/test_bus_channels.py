"""Separación de canales (`publish` durable vs. `emit` efímero) y anclaje/cierre de streams
efímeros con `CasRef` — encargo de `bus.py`, invariante 2 y 4."""

from __future__ import annotations

import hashlib

import pytest
from edecan_forge_kernel.bus import EphemeralStreamError, EventBus, StreamFrame
from edecan_forge_kernel.contracts import (
    Actor,
    CasRef,
    SessionCreatedPayload,
    ToolCallSuspendedPayload,
    emit,
)

from tests.conftest import FakeJournal


@pytest.mark.asyncio
async def test_emit_nunca_deja_rastro_en_el_journal(
    actor_agente: Actor, fake_journal: FakeJournal
) -> None:
    bus = EventBus(fake_journal, session_id="s1")

    draft_apertura = emit(
        "session.created",
        actor=actor_agente,
        stream_id="s1",
        correlation_id="corr-1",
        causation_id=None,
        payload=SessionCreatedPayload(session_id="s1"),
    )
    resultado = await bus.publish([draft_apertura], stream_id="s1", expected_seq=0, lease_epoch=1)
    assert resultado.accepted
    assert len(fake_journal.appended) == 1
    ancla = fake_journal.appended[0].id

    bus.open_ephemeral_stream(ancla, "proc.stdout")
    for i in range(500):
        cuerpo = f"linea {i}\n".encode()
        bus.emit(StreamFrame(anchor=ancla, channel="proc.stdout", ordinal=i, bytes=cuerpo))

    # 500 `emit()` después: el journal sigue teniendo exactamente el evento que sí se publicó.
    assert len(fake_journal.appended) == 1


@pytest.mark.asyncio
async def test_emit_exige_stream_abierto_y_anclado(
    actor_agente: Actor, fake_journal: FakeJournal
) -> None:
    del actor_agente
    bus = EventBus(fake_journal, session_id="s1")
    frame = StreamFrame(anchor="evt-fantasma", channel="proc.stdout", ordinal=0, bytes=b"hola")
    with pytest.raises(EphemeralStreamError, match="emit sin stream abierto"):
        bus.emit(frame)


@pytest.mark.asyncio
async def test_frame_por_encima_de_64_kib_se_rechaza() -> None:
    with pytest.raises(ValueError, match="excede el máximo"):
        StreamFrame(anchor="evt-1", channel="proc.stdout", ordinal=0, bytes=b"x" * (64 * 1024 + 1))


@pytest.mark.asyncio
async def test_anclaje_y_cierre_con_cas_ref(actor_agente: Actor, fake_journal: FakeJournal) -> None:
    """El cierre de un stream efímero produce un `CasRef` sobre el contenido íntegro, y ese
    `CasRef` es citable desde un evento durable — aquí, `tool.call_suspended.checkpoint_ref`,
    el único tipo ACTIVO de este paquete que transporta un `CasRef` en su payload (ver el
    docstring de `EphemeralStreamSeal`: el tipo de cierre concreto es decisión del dominio)."""
    bus = EventBus(fake_journal, session_id="s1")

    draft_apertura = emit(
        "session.created",
        actor=actor_agente,
        stream_id="s1",
        correlation_id="corr-1",
        causation_id=None,
        payload=SessionCreatedPayload(session_id="s1"),
    )
    await bus.publish([draft_apertura], stream_id="s1", expected_seq=0, lease_epoch=1)
    ancla = fake_journal.appended[0].id

    bus.open_ephemeral_stream(ancla, "proc.stdout")
    fragmentos = [b"primera linea\n", b"segunda linea\n", b"tercera linea sin salto"]
    for i, fragmento in enumerate(fragmentos):
        bus.emit(StreamFrame(anchor=ancla, channel="proc.stdout", ordinal=i, bytes=fragmento))

    sello = bus.close_ephemeral_stream(ancla, "proc.stdout")

    esperado = hashlib.blake2b(b"".join(fragmentos), digest_size=32).hexdigest()
    assert sello.content_ref == CasRef(algorithm="b2b", digest=esperado)
    assert sello.bytes_total == sum(len(f) for f in fragmentos)
    assert sello.lines_total == 2  # solo dos '\n' en los tres fragmentos
    assert sello.frames_total == 3

    # El evento de cierre lo construye y publica el LLAMADOR, citando `sello.content_ref`.
    draft_cierre = emit(
        "tool.call_suspended",
        actor=actor_agente,
        stream_id="s1",
        correlation_id="corr-1",
        causation_id=fake_journal.appended[0].id,
        payload=ToolCallSuspendedPayload(call_id="call-1", checkpoint_ref=sello.content_ref),
    )
    resultado = await bus.publish([draft_cierre], stream_id="s1", expected_seq=1, lease_epoch=1)
    assert resultado.accepted
    assert len(fake_journal.appended) == 2
    evento_cierre = fake_journal.appended[1]
    assert evento_cierre.payload_inline is not None
    # `payload_inline` serializa `CasRef` por su estructura (Pydantic), no por `str(CasRef)`.
    assert evento_cierre.payload_inline["checkpoint_ref"] == sello.content_ref.model_dump(
        mode="json"
    )


@pytest.mark.asyncio
async def test_no_se_puede_abrir_dos_veces_el_mismo_stream() -> None:
    bus = EventBus(FakeJournal(), session_id="s1")
    bus.open_ephemeral_stream("evt-1", "proc.stdout")
    with pytest.raises(EphemeralStreamError, match="ya abierto"):
        bus.open_ephemeral_stream("evt-1", "proc.stdout")


@pytest.mark.asyncio
async def test_no_se_puede_emitir_tras_cerrar() -> None:
    bus = EventBus(FakeJournal(), session_id="s1")
    bus.open_ephemeral_stream("evt-1", "proc.stdout")
    bus.emit(StreamFrame(anchor="evt-1", channel="proc.stdout", ordinal=0, bytes=b"x"))
    bus.close_ephemeral_stream("evt-1", "proc.stdout")
    with pytest.raises(EphemeralStreamError, match="ya cerrado"):
        bus.emit(StreamFrame(anchor="evt-1", channel="proc.stdout", ordinal=1, bytes=b"y"))


@pytest.mark.asyncio
async def test_ordinal_fuera_de_secuencia_se_rechaza() -> None:
    bus = EventBus(FakeJournal(), session_id="s1")
    bus.open_ephemeral_stream("evt-1", "proc.stdout")
    with pytest.raises(EphemeralStreamError, match="ordinal fuera de secuencia"):
        bus.emit(StreamFrame(anchor="evt-1", channel="proc.stdout", ordinal=5, bytes=b"x"))


@pytest.mark.asyncio
async def test_publish_rechaza_drafts_de_streams_mezclados(actor_agente: Actor) -> None:
    bus = EventBus(FakeJournal(), session_id="s1")
    d1 = emit(
        "session.created",
        actor=actor_agente,
        stream_id="s1",
        correlation_id="corr-1",
        causation_id=None,
        payload=SessionCreatedPayload(session_id="s1"),
    )
    d2 = emit(
        "session.created",
        actor=actor_agente,
        stream_id="s2",
        correlation_id="corr-2",
        causation_id=None,
        payload=SessionCreatedPayload(session_id="s2"),
    )
    with pytest.raises(Exception, match="deben compartir stream_id"):
        await bus.publish([d1, d2], stream_id="s1", expected_seq=0, lease_epoch=1)
