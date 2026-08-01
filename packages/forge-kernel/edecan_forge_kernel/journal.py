"""`SqliteJournal` — la única fuente de verdad del sistema (invariante 2).

Todo lo demás en Forge es proyección de lo que hay aquí: el estado del kernel (`KernelState`),
los presupuestos, los índices de búsqueda, incluso la UI. Si un dato no se puede reconstruir
leyendo este journal desde el principio, ese dato no existe de verdad — solo parece existir
hasta el próximo reinicio.

Este módulo implementa el **almacenamiento durable**, no el reducer puro. `contracts.py` ya fija
los tipos (`EventDraft`, `Event`, `Guard`, `AppendResult`, y el `Journal` Protocol) — este módulo
los pone en un archivo SQLite real, con las garantías que un `Protocol` no puede expresar:
contigüidad de `seq` bajo contención, fencing de `lease_epoch`, una cadena de hashes verificable
y un punto único de redacción legal. No se modifica `contracts.py` (regla dura del encargo);
donde este módulo necesita algo que el contrato no fija literalmente, la decisión se documenta
aquí como síntesis propia, igual que hace `contracts.py` consigo mismo.

Cuatro decisiones que no son una cita literal del encargo, y por qué:

1. **Canonicalización propia.** `contracts.py` ya define `_canonical_json` (privado) para sus
   propios hashes (`CasRef`, `CacheKey`, `IdempotencyKey`). Este módulo necesita la MISMA forma
   canónica (JSON con claves ordenadas, sin espacios, UTF-8, sin `NaN`/`Infinity`) para su propia
   cadena de hashes, pero sobre una composición de campos distinta (el "evento sin hash" del
   journal, no un payload de dominio) — así que se reimplementa aquí, no se importa un símbolo
   privado de otro módulo. Es la misma regla, aplicada a otro conjunto de bytes.

2. **La cadena de hashes es POR STREAM, no por journal entero.** `seq` ya es contiguo por
   stream (dato del encargo); encadenar el hash por la misma unidad que ya tiene orden total
   propio es lo único que permite verificar y detectar manipulación sin tener que serializar
   entre streams no relacionados. El primer evento de cada stream encadena contra
   `GENESIS_HASH` (`"b2b:" + "0"*64`, el mismo centinela que ya usa `test_roundtrip.py` como
   `expected_state_hash` de ejemplo).

3. **`append_if` — la guardia de `Guard` se evalúa en dos partes, no una.** El encargo pide
   "una proyección nombrada cuyo `last_applied_seq == head`, en la misma transacción" además de
   lo que `Guard` ya expresa literalmente (`expected_state_hash`). `Guard` (contracts.py) no
   lleva un campo `last_applied_seq`, así que esa proyección vive en una tabla propia de este
   módulo (`projections`), indexada por `(projection_name, key)`, con dos condiciones que deben
   cumplirse juntas dentro de la misma transacción SQLite (`BEGIN IMMEDIATE`):
     a) `last_applied_seq == head_seq` del stream que se está escribiendo — la proyección tiene
        que haber visto TODO lo que ya pasó en ese stream, o la condición que evalúa está mirando
        un pasado que ya no es cierto (la mitad de una condición de carrera clásica: "leí el
        saldo, hice el cargo" con un lector desactualizado en medio).
     b) `state_hash == guard.expected_state_hash` — la condición de negocio real que el llamador
        quiere comprobar (línea de `Guard`, cita literal).
   Es la lectura más estricta de "dependencia dura del libro de efectos" que sostiene ambas
   frases del encargo a la vez sin inventar un campo nuevo en `Guard`.

4. **`redact()` no encadena un nuevo `Event`.** El namespace `kernel.event_redacted` existe en
   `contracts.py` pero está `reserved`, no `active` — activarlo exigiría tocar el `SCHEMA_REGISTRY`
   sellado de `contracts.py`, que la regla dura de este encargo prohíbe. La lectura literal de
   "el hash sobrevive y la cadena sigue verificando" además apunta al mismo sitio: si redactar
   escribiera un evento nuevo, tendría que journalizarse igual que cualquier otro hecho — pero lo
   que se pide es que la cadena YA ESCRITA siga verificando exactamente igual después de destruir
   el blob. La solución que sostiene ambas cosas es: la cadena de `events` nunca se toca; el
   blob y su lápida viven en tablas de auditoría propias (`cas_blobs`, `redactions`) que ningún
   hash de evento referencia por contenido, solo por `CasRef` (que sigue siendo el mismo digest
   aunque el blob detrás ya no exista — el hash de un `CasRef` es del CONTENIDO, no una promesa
   de que el contenido siga vivo). Queda en `pendiente` para cuando `kernel.event_redacted` se
   active de verdad.

Una quinta desviación, de forma (no de fondo): el `Journal` Protocol de `contracts.py` declara
`append`/`append_if` como `async def`. `sqlite3` de la stdlib es síncrono y este paquete no puede
sumar una dependencia nueva (`aiosqlite` u otra) para fingir asincronía sobre I/O local que ya es
rápida. `SqliteJournal` expone métodos síncronos con los mismos nombres; envolverlos en
`asyncio.to_thread` para calzar el `Protocol` es responsabilidad del host que los use desde un
runtime async, no de este módulo.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from edecan_forge_kernel.contracts import (
    MAX_EVENT_SERIALIZED_BYTES,
    Actor,
    AppendResult,
    CasRef,
    Event,
    EventDraft,
    Guard,
    PayloadInlineError,
    derive_ulid,
    next_lamport,
    validate_payload_inline,
)

# --------------------------------------------------------------------------------------- #
# Errores propios de este módulo
# --------------------------------------------------------------------------------------- #


class FencedOut(RuntimeError):
    """Un `append`/`append_if` con `lease_epoch` obsoleto frente al máximo que este journal ya
    vio. Es fencing PREVENTIVO (impide que un host que perdió el lease gane la carrera de
    escritura), no forense — por eso es una excepción y no un `AppendResult.accepted=False`: un
    conflicto de `seq` es un desacuerdo de datos normal que el llamador puede reintentar sin más;
    un epoch obsoleto es la señal de que el propio llamador ya no tiene autoridad para reintentar
    nada en este stream sin renovar su lease primero."""


class ChainVerificationError(RuntimeError):
    """La cadena de hashes de un stream no verifica: hueco de `seq`, `prev_hash` que no enlaza,
    o un evento cuyo `hash` recalculado no coincide con el guardado (manipulación)."""

    def __init__(self, stream_id: str, seq: int, motivo: str) -> None:
        self.stream_id = stream_id
        self.seq = seq
        self.motivo = motivo
        super().__init__(f"cadena rota en stream {stream_id!r}, seq={seq}: {motivo}")


class BlobNotFoundError(KeyError):
    """No existe ningún blob con ese `CasRef` en este journal."""


class BlobRedactedError(RuntimeError):
    """El blob existió pero fue redactado — la lápida sobrevive, el contenido no (§ `redact`)."""


# --------------------------------------------------------------------------------------- #
# Constantes y utilidades de hash — ver desviación #1 del docstring del módulo
# --------------------------------------------------------------------------------------- #

GENESIS_HASH = "b2b:" + "0" * 64
"""Centinela de `prev_hash` para el primer evento de cada stream. Mismo formato de wire que
`CasRef.__str__()` (`"b2b:<64 hex>"`) por consistencia visual, aunque no sea un `CasRef` real —
es indistinguible en la práctica de un hash real porque `blake2b` nunca produce todo-ceros sobre
una entrada no vacía con probabilidad que importe."""


def _canonical_json(value: object) -> bytes:
    """Forma canónica JSON para el hash-chain de este journal — ver desviación #1: misma regla
    que `contracts._canonical_json` (claves ordenadas, sin espacios, UTF-8, sin `NaN`/`Infinity`),
    reimplementada aquí porque opera sobre una composición de campos propia del journal, no sobre
    un payload de dominio."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _blake2b_hex(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=32).hexdigest()


# --------------------------------------------------------------------------------------- #
# Esquema SQLite
# --------------------------------------------------------------------------------------- #

_ESQUEMA_SQL = """
CREATE TABLE IF NOT EXISTS journal_meta (
    id INTEGER PRIMARY KEY CHECK (id = 0),
    lamport INTEGER NOT NULL DEFAULT 0,
    max_lease_epoch INTEGER NOT NULL DEFAULT 0,
    id_seed BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS stream_heads (
    stream_id TEXT PRIMARY KEY,
    head_seq INTEGER NOT NULL DEFAULT 0,
    last_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    stream_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    v INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    lamport INTEGER NOT NULL,
    ts_physical INTEGER NOT NULL,
    type TEXT NOT NULL,
    cls TEXT NOT NULL,
    actor_json TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    lease_epoch INTEGER NOT NULL,
    durability TEXT NOT NULL,
    payload_inline_json TEXT,
    payload_ref TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    PRIMARY KEY (stream_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_lamport ON events (lamport);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_id ON events (event_id);

CREATE TABLE IF NOT EXISTS projections (
    projection_name TEXT NOT NULL,
    key TEXT NOT NULL,
    last_applied_seq INTEGER NOT NULL,
    state_hash TEXT NOT NULL,
    PRIMARY KEY (projection_name, key)
);

CREATE TABLE IF NOT EXISTS cas_blobs (
    digest TEXT PRIMARY KEY,
    data BLOB,
    redacted INTEGER NOT NULL DEFAULT 0,
    redacted_at_us INTEGER,
    redacted_reason TEXT,
    redacted_by TEXT
);

CREATE TABLE IF NOT EXISTS redactions (
    digest TEXT NOT NULL,
    redacted_at_us INTEGER NOT NULL,
    actor_id TEXT NOT NULL,
    reason TEXT NOT NULL
);
"""

_COLUMNAS_EVENTO = (
    "stream_id, seq, v, event_id, lamport, ts_physical, type, cls, actor_json, "
    "correlation_id, causation_id, lease_epoch, durability, payload_inline_json, payload_ref, "
    "prev_hash, hash"
)


# --------------------------------------------------------------------------------------- #
# `SqliteJournal`
# --------------------------------------------------------------------------------------- #


class SqliteJournal:
    """Journal durable de una sesión — un archivo SQLite en modo WAL, stdlib `sqlite3`.

    Instancia una sola vez por archivo (proceso). El `threading.Lock` interno serializa las
    transacciones de escritura a nivel de objeto Python; SQLite en modo WAL ya permite lectores
    concurrentes sin bloquearse contra el escritor, así que el lock solo protege la sección
    "leer cabeza -> decidir -> escribir" de una carrera entre dos hilos del mismo proceso —
    entre PROCESOS distintos, la garantía real es `BEGIN IMMEDIATE` (adquiere el lock de
    escritura de SQLite de inmediato, no de forma perezosa en el primer `INSERT`).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._crear_esquema()

    def _crear_esquema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(_ESQUEMA_SQL)
        cur.execute("SELECT COUNT(*) FROM journal_meta WHERE id = 0")
        (existe,) = cur.fetchone()
        if not existe:
            # `id_seed` se genera UNA vez por archivo y sobrevive a reaperturas — es la semilla
            # de entropía de `derive_ulid` para todo evento que este journal escriba jamás.
            cur.execute(
                "INSERT INTO journal_meta (id, lamport, max_lease_epoch, id_seed) "
                "VALUES (0, 0, 0, ?)",
                (os.urandom(16),),
            )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SqliteJournal:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----------------------------------------------------------------------------------- #
    # Lecturas auxiliares dentro de una transacción ya abierta
    # ----------------------------------------------------------------------------------- #

    def _leer_meta(self, cur: sqlite3.Cursor) -> tuple[int, int, bytes]:
        cur.execute("SELECT lamport, max_lease_epoch, id_seed FROM journal_meta WHERE id = 0")
        lamport, max_epoch, id_seed = cur.fetchone()
        return lamport, max_epoch, id_seed

    def _leer_head(self, cur: sqlite3.Cursor, stream_id: str) -> tuple[int, str]:
        cur.execute(
            "SELECT head_seq, last_hash FROM stream_heads WHERE stream_id = ?", (stream_id,)
        )
        fila = cur.fetchone()
        if fila is None:
            return 0, GENESIS_HASH
        return fila[0], fila[1]

    def _id_seed_para_stream(self, base_seed: bytes, stream_id: str) -> bytes:
        """Deriva 16 bytes de semilla específicos del stream a partir de la semilla base del
        journal. `derive_ulid` (contracts.py) toma `(ts_physical_us, id_seed, seq)` — sin
        `stream_id` — así que dos streams distintos que coincidan en `seq` y en el milisegundo
        de escritura producirían el mismo ULID si se usara la semilla base tal cual. Mezclar el
        `stream_id` en la semilla antes de llamar a `derive_ulid` evita esa colisión sin tocar
        la firma fijada por el contrato."""
        return hashlib.blake2b(base_seed + stream_id.encode("utf-8"), digest_size=16).digest()

    def _evaluar_guardia(
        self, cur: sqlite3.Cursor, guard: Guard, head_seq: int
    ) -> tuple[bool, str]:
        """Ver decisión #3 del docstring del módulo: la guardia exige `last_applied_seq ==
        head_seq` (la proyección está al día con este stream) Y `state_hash ==
        expected_state_hash` (la condición de negocio), ambas contra la MISMA fila leída dentro
        de la transacción de escritura — nunca una lectura previa fuera de ella."""
        cur.execute(
            "SELECT last_applied_seq, state_hash FROM projections "
            "WHERE projection_name = ? AND key = ?",
            (guard.projection_name, guard.key),
        )
        fila = cur.fetchone()
        if fila is None:
            return False, (
                f"guard_failed: no existe proyección {guard.projection_name!r}/{guard.key!r}"
            )
        last_applied_seq, state_hash = fila
        if last_applied_seq != head_seq:
            return False, (
                f"guard_failed: proyección desactualizada (last_applied_seq={last_applied_seq}, "
                f"head real={head_seq}) — hay que recalcularla antes de reintentar"
            )
        if state_hash != guard.expected_state_hash:
            return False, "guard_failed: expected_state_hash no coincide con el estado real"
        return True, ""

    # ----------------------------------------------------------------------------------- #
    # `append` / `append_if`
    # ----------------------------------------------------------------------------------- #

    def append(
        self,
        drafts: Sequence[EventDraft],
        *,
        stream_id: str,
        expected_seq: int,
        lease_epoch: int,
    ) -> AppendResult:
        """CAS optimista: `expected_seq` debe ser la `seq` del último evento que el llamador vio
        en `stream_id` (0 si el stream está vacío). Si la cabeza real ya avanzó, se rechaza con
        `AppendResult(accepted=False, ...)` — nunca una excepción, porque perder una carrera de
        escritura es un resultado de negocio normal, no un bug ni una condición irrecuperable.

        La firma (`drafts` posicional, `stream_id` solo por palabra clave) es EXACTAMENTE la del
        `Journal` Protocol de `contracts.py` — coherencia entre partes (encargo, paso 4): `bus.py`
        (`EventBus.publish`) llama a `self._journal.append(list(drafts), stream_id=..., ...)`
        contra ese Protocol, y esta clase tiene que aceptar la misma llamada estructuralmente,
        no solo "parecerse" al Protocol. Ver desviación #5 del docstring del módulo sobre por
        qué esto es síncrono en vez de `async def` (única diferencia real con el Protocol)."""
        return self._append_interno(stream_id, list(drafts), expected_seq, lease_epoch, guard=None)

    def append_if(
        self,
        drafts: Sequence[EventDraft],
        *,
        stream_id: str,
        expected_seq: int,
        lease_epoch: int,
        guard: Guard,
    ) -> AppendResult:
        """Como `append`, con una precondición adicional evaluada en la misma transacción — ver
        decisión #3 del docstring del módulo. Mismo orden de parámetros que el `Journal`
        Protocol, por la misma razón que `append`."""
        return self._append_interno(stream_id, list(drafts), expected_seq, lease_epoch, guard=guard)

    def _append_interno(
        self,
        stream_id: str,
        drafts: list[EventDraft],
        expected_seq: int,
        lease_epoch: int,
        *,
        guard: Guard | None,
    ) -> AppendResult:
        if not drafts:
            raise ValueError("append necesita al menos un EventDraft")
        for draft in drafts:
            if draft.stream_id != stream_id:
                raise ValueError(
                    f"el draft declara stream_id={draft.stream_id!r}, no coincide con el "
                    f"append a {stream_id!r}"
                )
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                resultado = self._append_bajo_transaccion(
                    cur, stream_id, drafts, expected_seq, lease_epoch, guard
                )
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            else:
                # Un rechazo estructurado (seq_conflict / guard_failed / payload inválido) no
                # escribió nada: hacer COMMIT sobre una transacción sin cambios es un no-op que
                # igual libera el lock de escritura, así que no hace falta distinguir la rama.
                cur.execute("COMMIT")
                return resultado

    def _append_bajo_transaccion(
        self,
        cur: sqlite3.Cursor,
        stream_id: str,
        drafts: list[EventDraft],
        expected_seq: int,
        lease_epoch: int,
        guard: Guard | None,
    ) -> AppendResult:
        lamport_actual, max_epoch_actual, id_seed = self._leer_meta(cur)
        if lease_epoch < max_epoch_actual:
            # Fencing preventivo: un host que leyó la cabeza justo antes de perder el lease no
            # puede ganar la carrera — el journal recuerda el epoch más alto que vio jamás.
            raise FencedOut(
                f"lease_epoch {lease_epoch} es obsoleto: el máximo visto por este journal es "
                f"{max_epoch_actual}"
            )
        head_seq, head_hash = self._leer_head(cur, stream_id)
        if expected_seq != head_seq:
            return AppendResult(
                accepted=False,
                reason=f"seq_conflict: esperado {expected_seq}, cabeza real {head_seq}",
            )
        if guard is not None:
            ok, motivo = self._evaluar_guardia(cur, guard, head_seq)
            if not ok:
                return AppendResult(accepted=False, reason=motivo)
        try:
            eventos, lamport_final, hash_final = self._construir_y_escribir(
                cur,
                stream_id=stream_id,
                drafts=drafts,
                lease_epoch=lease_epoch,
                head_seq=head_seq,
                head_hash=head_hash,
                lamport_actual=lamport_actual,
                id_seed=id_seed,
            )
        except PayloadInlineError as exc:
            # Defensa en profundidad (invariante 7: el sandbox impone, no se confía en el
            # modelo): `EventDraft` ya garantiza esto en su propio constructor, pero el journal
            # lo vuelve a comprobar en el borde de la escritura durable, no solo en memoria.
            return AppendResult(
                accepted=False, reason=f"{exc} — el journal lo rechaza antes de escribir"
            )
        cur.execute(
            "UPDATE journal_meta SET lamport = ?, max_lease_epoch = ? WHERE id = 0",
            (lamport_final, max(max_epoch_actual, lease_epoch)),
        )
        cur.execute(
            "INSERT INTO stream_heads (stream_id, head_seq, last_hash) VALUES (?, ?, ?) "
            "ON CONFLICT (stream_id) DO UPDATE SET "
            "head_seq = excluded.head_seq, last_hash = excluded.last_hash",
            (stream_id, eventos[-1].seq, hash_final),
        )
        return AppendResult(accepted=True, from_seq=eventos[0].seq, to_seq=eventos[-1].seq)

    def _construir_y_escribir(
        self,
        cur: sqlite3.Cursor,
        *,
        stream_id: str,
        drafts: list[EventDraft],
        lease_epoch: int,
        head_seq: int,
        head_hash: str,
        lamport_actual: int,
        id_seed: bytes,
    ) -> tuple[list[Event], int, str]:
        """Asigna `seq`/`lamport`/`id`/`hash` a cada draft, en orden, y los inserta. Todos los
        eventos de un mismo `append` comparten `ts_physical` (una sola lectura de reloj para el
        lote entero: son un solo hecho durable atómico, no N hechos independientes que
        casualmente llegaron juntos) y encadenan `prev_hash` entre sí antes de tocar
        `stream_heads` — así que un `append` de 3 drafts que falla a mitad de lote nunca dejó
        huella (todo vive dentro de la misma transacción SQL del llamador)."""
        id_seed_stream = self._id_seed_para_stream(id_seed, stream_id)
        ts_physical = time.time_ns() // 1000
        eventos: list[Event] = []
        seq = head_seq
        prev_hash = head_hash
        lamport = lamport_actual
        for draft in drafts:
            validate_payload_inline(draft.payload_inline)
            seq += 1
            # `observed_lamport` siempre 0 aquí: no hay propagación entre journals en esta fase
            # (§ nota del encargo — sin eso el parámetro sería puramente decorativo), así que
            # `next_lamport` se reduce a "avanza el reloj local en uno", pero se llama a la
            # función real del contrato para dejar el punto de extensión ya cableado.
            lamport = next_lamport(lamport, 0)
            event_id = derive_ulid(ts_physical_us=ts_physical, id_seed=id_seed_stream, seq=seq)
            payload_ref_wire = str(draft.payload_ref) if draft.payload_ref is not None else None
            nucleo = {
                "v": draft.v,
                "id": event_id,
                "stream_id": stream_id,
                "seq": seq,
                "lamport": lamport,
                "ts_physical": ts_physical,
                "type": draft.type,
                "cls": draft.cls,
                "actor": draft.actor.model_dump(mode="json"),
                "correlation_id": draft.correlation_id,
                "causation_id": draft.causation_id,
                "lease_epoch": lease_epoch,
                "durability": draft.durability,
                "payload_inline": draft.payload_inline,
                "payload_ref": payload_ref_wire,
            }
            cuerpo = _canonical_json(nucleo)
            if len(cuerpo) > MAX_EVENT_SERIALIZED_BYTES:
                raise PayloadInlineError(
                    f"evento serializado ocupa {len(cuerpo)} B, excede el máximo de "
                    f"{MAX_EVENT_SERIALIZED_BYTES} B (§1.2)"
                )
            # hash_evento = blake2b(canónico(evento_sin_hash) || hash_anterior) — ver
            # desviación #1 (JSON canónico en vez de CBOR) del docstring del módulo.
            digest = _blake2b_hex(cuerpo + prev_hash.encode("utf-8"))
            hash_evento = f"b2b:{digest}"
            evento = Event(
                v=draft.v,
                id=event_id,
                stream_id=stream_id,
                seq=seq,
                lamport=lamport,
                ts_physical=ts_physical,
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
                hash=hash_evento,
            )
            cur.execute(
                f"INSERT INTO events ({_COLUMNAS_EVENTO}) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    stream_id,
                    seq,
                    draft.v,
                    event_id,
                    lamport,
                    ts_physical,
                    draft.type,
                    draft.cls,
                    draft.actor.model_dump_json(),
                    draft.correlation_id,
                    draft.causation_id,
                    lease_epoch,
                    draft.durability,
                    (
                        json.dumps(draft.payload_inline, ensure_ascii=False)
                        if draft.payload_inline is not None
                        else None
                    ),
                    payload_ref_wire,
                    prev_hash,
                    hash_evento,
                ),
            )
            eventos.append(evento)
            prev_hash = hash_evento
        return eventos, lamport, prev_hash

    # ----------------------------------------------------------------------------------- #
    # Lectura por cursor
    # ----------------------------------------------------------------------------------- #

    def _fila_a_evento(self, fila: tuple[object, ...]) -> Event:
        (
            stream_id,
            seq,
            v,
            event_id,
            lamport,
            ts_physical,
            type_,
            cls,
            actor_json,
            correlation_id,
            causation_id,
            lease_epoch,
            durability,
            payload_inline_json,
            payload_ref,
            prev_hash,
            hash_,
        ) = fila
        return Event(
            v=v,
            id=event_id,
            stream_id=stream_id,
            seq=seq,
            lamport=lamport,
            ts_physical=ts_physical,
            type=type_,
            cls=cls,
            actor=Actor.model_validate_json(actor_json),
            correlation_id=correlation_id,
            causation_id=causation_id,
            lease_epoch=lease_epoch,
            durability=durability,
            payload_inline=json.loads(payload_inline_json)
            if payload_inline_json is not None
            else None,
            payload_ref=CasRef.parse(payload_ref) if payload_ref is not None else None,
            prev_hash=prev_hash,
            hash=hash_,
        )

    def leer(self, stream_id: str, *, desde_seq: int = 0, limite: int = 100) -> list[Event]:
        """Eventos de `stream_id` con `seq > desde_seq`, en orden, hasta `limite`. Pensado para
        que un cliente se suscriba con `desde_seq = último que ya procesó` y rehidrate en
        páginas, sin descargar el stream entero de una vez."""
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT {_COLUMNAS_EVENTO} FROM events WHERE stream_id = ? AND seq > ? "
            "ORDER BY seq ASC LIMIT ?",
            (stream_id, desde_seq, limite),
        )
        return [self._fila_a_evento(fila) for fila in cur.fetchall()]

    def leer_todos(self, *, desde_cursor: int = 0, limite: int = 100) -> tuple[list[Event], int]:
        """Igual que `leer`, pero a través de TODOS los streams del journal, ordenado por
        `lamport` (el reloj compartido entre streams de este journal — ver docstring del módulo
        sobre por qué no hay propagación entre journals todavía). Devuelve `(eventos,
        siguiente_cursor)`: `siguiente_cursor` es el `lamport` del último evento devuelto, listo
        para pasarse como `desde_cursor` en la siguiente llamada; si no hay eventos nuevos,
        devuelve el mismo cursor de entrada."""
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT {_COLUMNAS_EVENTO} FROM events WHERE lamport > ? ORDER BY lamport ASC LIMIT ?",
            (desde_cursor, limite),
        )
        filas = cur.fetchall()
        eventos = [self._fila_a_evento(fila) for fila in filas]
        siguiente_cursor = eventos[-1].lamport if eventos else desde_cursor
        return eventos, siguiente_cursor

    def head(self, stream_id: str) -> int:
        """`seq` del último evento de `stream_id`, o 0 si el stream no existe todavía."""
        cur = self._conn.cursor()
        cur.execute("SELECT head_seq FROM stream_heads WHERE stream_id = ?", (stream_id,))
        fila = cur.fetchone()
        return fila[0] if fila is not None else 0

    # ----------------------------------------------------------------------------------- #
    # Verificación de la cadena
    # ----------------------------------------------------------------------------------- #

    def verify_chain(self, stream_id: str) -> bool:
        """Recorre `stream_id` entero desde `seq=1`, recalculando cada `hash` y comprobando que
        `prev_hash` enlaza con el hash del evento anterior (o con `GENESIS_HASH` para el
        primero). Levanta `ChainVerificationError` en el primer evento que no cuadre — hueco de
        `seq`, enlace roto o hash manipulado — y devuelve `True` si el stream entero (vacío
        incluido) verifica limpio. Coste O(n) en el tamaño del stream: pensado para auditoría y
        arranque/recuperación, no para el camino caliente de cada escritura."""
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT {_COLUMNAS_EVENTO} FROM events WHERE stream_id = ? ORDER BY seq ASC",
            (stream_id,),
        )
        prev_hash_esperado = GENESIS_HASH
        seq_esperado = 1
        for fila in cur.fetchall():
            (
                _stream_id,
                seq,
                v,
                event_id,
                lamport,
                ts_physical,
                type_,
                cls,
                actor_json,
                correlation_id,
                causation_id,
                lease_epoch,
                durability,
                payload_inline_json,
                payload_ref,
                prev_hash,
                hash_,
            ) = fila
            if seq != seq_esperado:
                raise ChainVerificationError(
                    stream_id, seq, f"hueco de seq: se esperaba {seq_esperado}"
                )
            if prev_hash != prev_hash_esperado:
                raise ChainVerificationError(
                    stream_id, seq, "prev_hash no enlaza con el evento anterior"
                )
            nucleo = {
                "v": v,
                "id": event_id,
                "stream_id": stream_id,
                "seq": seq,
                "lamport": lamport,
                "ts_physical": ts_physical,
                "type": type_,
                "cls": cls,
                "actor": json.loads(actor_json),
                "correlation_id": correlation_id,
                "causation_id": causation_id,
                "lease_epoch": lease_epoch,
                "durability": durability,
                "payload_inline": (
                    json.loads(payload_inline_json) if payload_inline_json is not None else None
                ),
                "payload_ref": payload_ref,
            }
            cuerpo = _canonical_json(nucleo)
            digest = _blake2b_hex(cuerpo + prev_hash.encode("utf-8"))
            hash_recalculado = f"b2b:{digest}"
            if hash_recalculado != hash_:
                raise ChainVerificationError(
                    stream_id, seq, "hash no coincide: el evento fue manipulado"
                )
            prev_hash_esperado = hash_
            seq_esperado += 1
        return True

    # ----------------------------------------------------------------------------------- #
    # Proyecciones (para `append_if`)
    # ----------------------------------------------------------------------------------- #

    def set_projection(
        self, projection_name: str, key: str, *, last_applied_seq: int, state_hash: str
    ) -> None:
        """Actualiza (o crea) el estado conocido de una proyección — lo llama el proceso que
        mantiene esa proyección (por ejemplo, un `EffectLedger`) cada vez que aplica un evento
        nuevo. `append_if` lee esta tabla para decidir si la proyección está lo bastante al día
        como para confiar en su `state_hash` (ver decisión #3 del docstring del módulo)."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                cur.execute(
                    "INSERT INTO projections (projection_name, key, last_applied_seq, state_hash) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (projection_name, key) DO UPDATE SET "
                    "last_applied_seq = excluded.last_applied_seq, "
                    "state_hash = excluded.state_hash",
                    (projection_name, key, last_applied_seq, state_hash),
                )
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            else:
                cur.execute("COMMIT")

    # ----------------------------------------------------------------------------------- #
    # CAS de blobs y redacción — ver decisión #4 del docstring del módulo
    # ----------------------------------------------------------------------------------- #

    def put_blob(self, data: bytes) -> CasRef:
        """Guarda un blob y devuelve su `CasRef`. Idempotente por contenido: escribir el mismo
        blob dos veces es un no-op la segunda vez (`INSERT OR IGNORE` sobre la clave del
        digest) — es justamente lo que "direccionado por hash" garantiza (invariante 4)."""
        ref = CasRef.from_bytes(data)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                cur.execute(
                    "INSERT OR IGNORE INTO cas_blobs (digest, data, redacted) VALUES (?, ?, 0)",
                    (str(ref), data),
                )
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            else:
                cur.execute("COMMIT")
        return ref

    def get_blob(self, cas_ref: CasRef) -> bytes:
        cur = self._conn.cursor()
        cur.execute("SELECT data, redacted FROM cas_blobs WHERE digest = ?", (str(cas_ref),))
        fila = cur.fetchone()
        if fila is None:
            raise BlobNotFoundError(str(cas_ref))
        data, redacted = fila
        if redacted:
            raise BlobRedactedError(f"el blob {cas_ref} fue redactado y ya no está disponible")
        return data

    def redact(self, cas_ref: CasRef, *, reason: str, actor_id: str) -> None:
        """Destruye el contenido de un blob y deja una lápida (`redactions`) — la vía por la que
        el sistema es legalmente operable (borrar un dato personal sin mentir sobre que existió).
        El `CasRef` sigue siendo válido como identificador (es el hash del contenido, no una
        promesa de que el contenido sobreviva) y la cadena de `events` de cualquier stream que lo
        referencie por `payload_ref` sigue verificando exactamente igual: `redact` nunca toca la
        tabla `events`. Ver decisión #4 del docstring del módulo sobre por qué esto no encadena
        un `Event` nuevo en esta fase."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                cur.execute("SELECT redacted FROM cas_blobs WHERE digest = ?", (str(cas_ref),))
                fila = cur.fetchone()
                if fila is None:
                    raise BlobNotFoundError(str(cas_ref))
                ahora_us = time.time_ns() // 1000
                cur.execute(
                    "UPDATE cas_blobs SET data = NULL, redacted = 1, redacted_at_us = ?, "
                    "redacted_reason = ?, redacted_by = ? WHERE digest = ?",
                    (ahora_us, reason, actor_id, str(cas_ref)),
                )
                cur.execute(
                    "INSERT INTO redactions (digest, redacted_at_us, actor_id, reason) "
                    "VALUES (?, ?, ?, ?)",
                    (str(cas_ref), ahora_us, actor_id, reason),
                )
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            else:
                cur.execute("COMMIT")
