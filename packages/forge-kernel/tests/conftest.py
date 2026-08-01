from __future__ import annotations

import hashlib

import pytest
from edecan_forge_kernel.contracts import (
    Actor,
    AppendResult,
    Event,
    EventDraft,
    Guard,
    KernelState,
    Stamp,
    derive_ulid,
)


@pytest.fixture
def actor_agente() -> Actor:
    return Actor(kind="agent", id="agent-1", capability_id="cap-1")


@pytest.fixture
def actor_kernel() -> Actor:
    return Actor(kind="kernel", id="kernel", capability_id=None)


@pytest.fixture
def stamp() -> Stamp:
    return Stamp(
        ts_physical=1_800_000_000_000_000,
        id_seed=b"0123456789abcdef",
        lease_epoch=1,
        observed_lamport=0,
    )


@pytest.fixture
def estado_vacio() -> KernelState:
    return KernelState()


GENESIS_HASH = "b2b:" + "0" * 64
"""`prev_hash` del primer evento de un stream — un `CasRef` de juguete, no el de ningún host
real. Ningún test de este paquete depende de que este valor coincida con el de un journal de
producción; solo de que sea estable DENTRO de la vida de `FakeJournal`."""


class FakeJournal:
    """Implementación mínima de `contracts.Journal` para las pruebas de `bus.py`/
    `projections.py` de ESTE paquete — no es la implementación durable real, que es "de otro
    bloque" (`contracts.py`, comentario de la sección 12). Solo hace lo mínimo para que un
    `Event` bien formado exista: asigna `seq` contiguo por stream, un `lamport` monotónico
    (`next_lamport`, calcado del que usaría un host real) y una cadena de hash de juguete
    (`blake2b` sobre los bytes canónicos del draft más el `prev_hash` anterior — NO el CBOR
    canónico que fija el documento, misma desviación que el resto de este paquete).

    Además de servir a `EventBus.publish`, `self.appended` es la aserción central del test de
    separación de canales: un `emit()` que "deja rastro en el journal" se detectaría aquí como
    una entrada inesperada.
    """

    def __init__(self) -> None:
        self.appended: list[Event] = []
        self._seq_by_stream: dict[str, int] = {}
        self._lamport_by_stream: dict[str, int] = {}
        self._head_hash_by_stream: dict[str, str] = {}
        self._ts_cursor = 1_800_000_000_000_000

    async def append(
        self, drafts: list[EventDraft], *, stream_id: str, expected_seq: int, lease_epoch: int
    ) -> AppendResult:
        actual = self._seq_by_stream.get(stream_id, 0)
        if actual != expected_seq:
            return AppendResult(accepted=False, reason="SEQ_MISMATCH")
        if not drafts:
            return AppendResult(accepted=True, from_seq=actual, to_seq=actual)
        desde = actual + 1
        eventos: list[Event] = []
        for draft in drafts:
            actual += 1
            self._ts_cursor += 1
            lamport = self._lamport_by_stream.get(stream_id, 0) + 1
            prev_hash = self._head_hash_by_stream.get(stream_id, GENESIS_HASH)
            digest = hashlib.blake2b(
                draft.canonical_bytes() + prev_hash.encode("utf-8"), digest_size=32
            ).hexdigest()
            hash_ = f"b2b:{digest}"
            evento = Event(
                v=draft.v,
                id=derive_ulid(
                    ts_physical_us=self._ts_cursor, id_seed=b"0123456789abcdef", seq=actual
                ),
                stream_id=stream_id,
                seq=actual,
                lamport=lamport,
                ts_physical=self._ts_cursor,
                type=draft.type,
                cls=draft.cls,
                actor=draft.actor,
                correlation_id=draft.correlation_id,
                causation_id=draft.causation_id,
                lease_epoch=lease_epoch,
                durability=draft.durability,
                payload_inline=draft.payload_inline,
                payload_ref=draft.payload_ref,
                prev_hash=prev_hash,
                hash=hash_,
            )
            eventos.append(evento)
            self._lamport_by_stream[stream_id] = lamport
            self._head_hash_by_stream[stream_id] = hash_
        self._seq_by_stream[stream_id] = actual
        self.appended.extend(eventos)
        return AppendResult(accepted=True, from_seq=desde, to_seq=actual)

    async def append_if(
        self,
        drafts: list[EventDraft],
        *,
        stream_id: str,
        expected_seq: int,
        lease_epoch: int,
        guard: Guard,
    ) -> AppendResult:
        del guard  # `FakeJournal` no evalúa proyecciones; los tests de `Guard` no viven aquí
        return await self.append(
            drafts, stream_id=stream_id, expected_seq=expected_seq, lease_epoch=lease_epoch
        )


@pytest.fixture
def fake_journal() -> FakeJournal:
    return FakeJournal()
