"""Bus de eventos de Forge: dos canales estrictamente separados — `docs/arquitectura-forge.md`
§1.4 (líneas 1478-1512).

Esta separación es la decisión que hace viable el sistema entero, así que se repite aquí sin
disculpas: `publish` es DURABLE (entra en la cadena de hashes del journal, invariante 2)
y `emit` es EFÍMERO (nunca toca el journal, ni lo conoce en su firma). Un `pnpm install`
verboso son ~200.000 líneas de stdout; journalizarlas cuesta cientos de MB por sesión y
destruye el replay (§1.4, línea 1512: "3 eventos en el journal, no 12.000"). Todo stream
efímero se ancla a un evento durable de apertura (`open_ephemeral_stream`) y termina en un
evento durable de cierre que el LLAMADOR construye con un `CasRef` al volcado íntegro
(`close_ephemeral_stream` solo calcula ese `CasRef`; journalizarlo es responsabilidad del
llamador, con el tipo de dominio activo que corresponda — ver `EphemeralStreamSeal`).

Regla dura heredada de `edecan_forge_probe.modelcard` y de `contracts.py`: nada se rellena a
mano ni se inventa para "que quede bonito". Donde el documento fija solo dos frases de prosa en
vez de una máquina de estados completa (la histéresis de suscriptores, líneas 1496-1500), este
módulo lo dice en su propio docstring como SÍNTESIS propia y no como cita, para que quede
auditable — ver el docstring de `Subscription`.

Este módulo NO implementa un `Journal` real: la implementación durable es "de otro bloque"
(`contracts.py`, comentario de la sección 12: "solo el tipo; la implementación durable es de
otro bloque"). `EventBus.publish` recibe un `Journal` (el `Protocol` de `contracts.py`) ya
construido por el host y se limita a delegar en él — es lo que mantiene la separación de
canales auditable con una lectura del código: `emit` no tiene el journal en su cierre.
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, field_validator

from edecan_forge_kernel.contracts import AppendResult, CasRef, EventDraft, Journal

# --------------------------------------------------------------------------------------- #
# Constantes de presupuesto — §1.4, líneas 1496-1500 y tabla de la línea 1657
# --------------------------------------------------------------------------------------- #

MAX_FRAME_BYTES = 64 * 1024
""""frame ≤ 64 KiB" — §1.4, línea 1500."""

SESSION_BANDWIDTH_BYTES_PER_S = 5 * 1024 * 1024
""""5 MiB/s por sesión" — §1.4, línea 1500 y tabla de la línea 1657."""

SUBSCRIBER_BANDWIDTH_BYTES_PER_S = 1 * 1024 * 1024
""""1 MiB/s por suscriptor" — misma cita."""

COALESCE_MS = 50
""""coalescing de 50 ms" — §1.4, línea 1500. Documental en este módulo: el propio bus no
implementa el temporizador de coalescing (eso es del transporte, WebSocket/HTTP2 concreto que
usa el bus); esta constante existe para que un implementador de transporte no tenga que
adivinar el número."""

LAG_LIVE_THRESHOLD = 64
""""lag < 64 eventos" para volver a `live` — §1.4, línea 1496."""

LAG_CONSECUTIVE_WINDOWS_TO_LIVE = 2
""""durante dos ventanas de 1 s consecutivas" — misma cita."""

LAG_WINDOW_SECONDS = 1.0

MAX_CATCHUP_ATTEMPTS = 3
""""si no converge tras 3 intentos, se degrada automáticamente a `digest`" — §1.4, línea 1496."""

DIGEST_ALLOWED_PATTERNS: frozenset[str] = frozenset({"*.failed", "approval.*", "turn.*"})
"""Lo que SÍ recibe un suscriptor en `digest`, además de `cls == "control"` (que no es un patrón
de canal y se filtra aparte) y un heartbeat de 1 Hz con `head.seq` — §1.4, línea 1496. El
heartbeat y el filtro por `cls` son responsabilidad del host que sirve el transporte; este
módulo solo fija el conjunto de patrones."""

DeliveryMode = Literal["durable_replay", "live_lossy", "digest"]
"""Los tres modos de entrega de §1.4. `durable_replay` es el modo de CATCH-UP: el suscriptor
lee el journal desde `last_applied_seq` en vez de recibir frames en vivo. No hay un cuarto modo
"live" con nombre propio en el documento: `live_lossy` ES el modo en vivo, y el sufijo
"lossy" documenta la semántica real (drop-oldest bajo presión, `StreamGap`), no un modo
distinto."""


class BusError(RuntimeError):
    """Raíz de los errores de este módulo — nunca se usa para control de flujo de negocio (eso
    son `Rejection`/`Decision` en `contracts.py`); esto es programación defensiva contra un
    llamador que viola el protocolo del bus (emitir sin abrir stream, cerrar dos veces...)."""


class EphemeralStreamError(BusError):
    """Uso incorrecto del ciclo de vida ancla→emit*→cierre de un stream efímero."""


# --------------------------------------------------------------------------------------- #
# El canal efímero — `StreamFrame`, el ancla y el sello de cierre
# --------------------------------------------------------------------------------------- #


class StreamFrame(BaseModel, frozen=True):
    """Frame del canal `emit` — §1.4, líneas 1484-1489. `anchor` es el `Event.id` (ULID) del
    evento durable que abrió el stream (ver `EventBus.open_ephemeral_stream`): sin ancla, un
    frame efímero no se puede atribuir a nada durable y la invariante 4 (contenido direccionado
    por hash, siempre referenciado desde el journal) queda rota silenciosamente."""

    anchor: str
    channel: str
    ordinal: int
    bytes: bytes

    @field_validator("bytes")
    @classmethod
    def _validar_tamano(cls, v: bytes) -> bytes:
        if len(v) > MAX_FRAME_BYTES:
            raise ValueError(
                f"frame de {len(v)} B excede el máximo de {MAX_FRAME_BYTES} B (§1.4, línea 1500)"
            )
        return v


class EphemeralStreamSeal(BaseModel, frozen=True):
    """Lo que `close_ephemeral_stream` devuelve — §1.4, línea 1502: "termina en un evento durable
    de cierre [...] que lleva el contenido íntegro al CAS". Este tipo NO es ese evento durable:
    es la materia prima (`content_ref` y las estadísticas) que el llamador cita al construir su
    propio `EventDraft` activo y pasarlo a `EventBus.publish`. Journalizar el cierre es decisión
    del dominio (qué tipo de evento usar: `proc.output_sealed`, `tool.call_suspended`...), no de
    este bus genérico."""

    anchor: str
    channel: str
    content_ref: CasRef
    bytes_total: int
    lines_total: int
    frames_total: int


class _EphemeralStreamHandle:
    """Estado en memoria de un stream efímero abierto. Deliberadamente NO es un modelo Pydantic:
    vive solo en memoria del proceso host mientras el stream está abierto, nunca se serializa ni
    cruza el bus — es la contraparte interna de `StreamFrame`, que SÍ es Pydantic porque ese es
    el que viaja.

    Guarda un hash INCREMENTAL (`hashlib.blake2b.update` por frame), no el contenido acumulado:
    igual que la corrección de §2.3 (línea 1941) para las transacciones de workspace ("escribir
    el contenido al CAS inmediatamente [...] la txn retiene solo el mapa, no gigabytes"), un
    stream de 40 MB de stdout no debe significar 40 MB residentes en el bus. El coste de emitir
    un frame es O(len(frame)), nunca O(bytes acumulados hasta ahora).
    """

    __slots__ = (
        "anchor",
        "channel",
        "_hasher",
        "next_ordinal",
        "total_bytes",
        "total_lines",
        "closed",
    )

    def __init__(self, anchor: str, channel: str) -> None:
        self.anchor = anchor
        self.channel = channel
        self._hasher = hashlib.blake2b(digest_size=32)
        self.next_ordinal = 0
        self.total_bytes = 0
        self.total_lines = 0
        self.closed = False

    def absorb(self, frame: StreamFrame) -> None:
        self._hasher.update(frame.bytes)
        self.total_bytes += len(frame.bytes)
        self.total_lines += frame.bytes.count(b"\n")
        self.next_ordinal += 1

    def seal(self) -> EphemeralStreamSeal:
        self.closed = True
        return EphemeralStreamSeal(
            anchor=self.anchor,
            channel=self.channel,
            content_ref=CasRef(algorithm="b2b", digest=self._hasher.hexdigest()),
            bytes_total=self.total_bytes,
            lines_total=self.total_lines,
            frames_total=self.next_ordinal,
        )


# --------------------------------------------------------------------------------------- #
# Suscripción por patrón — trie de dos niveles, §1.4 líneas 1512-1513
# --------------------------------------------------------------------------------------- #


class PatternTrie:
    """Compilación de patrones glob por segmento (`tool.*`, `*.failed`, `agent.<id>.*`) a un
    trie — §1.4, línea 1513: "compilado a trie, matching O(1) amortizado [...] sin regex, para
    que el coste del matching no dependa de lo que escriba un cliente". Cada segmento de un
    `type` de evento (`"tool.call_completed"` → `["tool", "call_completed"]`) recorre el trie una
    vez; el coste de matchear un evento es O(número de segmentos del tipo), no O(número de
    patrones registrados) — es lo que da el "amortizado" del documento.

    Solo `*` es comodín, y coincide con EXACTAMENTE un segmento (no con una subcadena ni con
    varios segmentos): un patrón y un tipo de igual longitud de segmentos pueden matchear;
    longitudes distintas nunca matchean. No hay `**`: el documento no lo pide y añadirlo sin que
    lo pida el documento sería inventar semántica de namespacing que no está pinneada.
    """

    _WILDCARD = "*"
    _TERMINAL_KEY = "$"

    def __init__(self) -> None:
        self._root: dict[str, Any] = {}

    def add(self, pattern: str, subscription_id: str) -> None:
        nodo = self._root
        for segmento in pattern.split("."):
            nodo = nodo.setdefault(segmento, {})
        nodo.setdefault(self._TERMINAL_KEY, set()).add(subscription_id)

    def remove(self, pattern: str, subscription_id: str) -> None:
        nodo: dict[str, Any] | None = self._root
        for segmento in pattern.split("."):
            if nodo is None:
                return
            nodo = nodo.get(segmento)
        if nodo is not None:
            nodo.get(self._TERMINAL_KEY, set()).discard(subscription_id)

    def match(self, type_: str) -> frozenset[str]:
        segmentos = type_.split(".")
        encontrados: set[str] = set()
        self._walk(self._root, segmentos, 0, encontrados)
        return frozenset(encontrados)

    def _walk(
        self, nodo: dict[str, Any], segmentos: list[str], i: int, encontrados: set[str]
    ) -> None:
        if i == len(segmentos):
            encontrados.update(nodo.get(self._TERMINAL_KEY, set()))
            return
        segmento = segmentos[i]
        for clave in {segmento, self._WILDCARD}:
            siguiente = nodo.get(clave)
            if siguiente is not None:
                self._walk(siguiente, segmentos, i + 1, encontrados)


# --------------------------------------------------------------------------------------- #
# Backpressure con histéresis — §1.4, líneas 1496-1500. Ver el docstring de `Subscription`
# para la síntesis de la máquina de estados completa que el documento no tabula.
# --------------------------------------------------------------------------------------- #


class StreamGap(BaseModel, frozen=True):
    """Lo que recibe un suscriptor `live_lossy` cuando su cola se desborda y se descarta el
    frame más antiguo — §1.4, línea 1498: "sufren drop-oldest y reciben `StreamGap` [...] para
    que la UI muestre '…' honestamente en vez de un stdout con un agujero invisible"."""

    from_ordinal: int
    to_ordinal: int


class Subscription:
    """Un suscriptor con presupuesto e histéresis propios — §1.4.

    El documento fija la regla de PROMOCIÓN en dos frases de prosa (líneas 1496-1500): un
    suscriptor en catch-up (`durable_replay`) vuelve a `live_lossy` solo tras dos ventanas de 1 s
    SEGUIDAS con lag < 64, y si no converge tras 3 intentos se degrada a `digest`. No tabula la
    máquina de estados completa (qué cuenta exactamente como "intento", qué pasa si nunca hay ni
    una ventana buena). Lo de abajo es la SÍNTESIS de esta implementación — no una cita — y se
    documenta aquí para que un desacuerdo se dirima sobre este docstring:

    - Cada llamada a `record_lag_window(lag)` mientras el modo es `durable_replay` evalúa UNA
      ventana. Si `lag < 64`, extiende la racha buena; al llegar a 2 sube a `live_lossy` y pone
      a cero tanto la racha como los intentos fallidos.
    - Si `lag >= 64`, la racha buena se rompe a cero Y se cuenta como un intento fallido —
      INCLUSO si es la primerísima ventana, sin racha previa que romper. Esto es deliberado: el
      caso que motiva la regla (línea 1726, "móvil en red mala, catch-up más lento que la
      producción") nunca llega a tener una racha que romper, y aun así tiene que converger a
      `digest` en vez de quedarse en `durable_replay` para siempre — que es exactamente el ciclo
      infinito que la histéresis existe para evitar.
    - Al tercer intento fallido pasa a `digest`, TERMINAL en esta clase salvo `re_subscribe()`
      explícito. `digest` no se abandona por una mejora espontánea del lag: el documento dice
      (línea 1496) que "un cliente en `digest` sabe que está en `digest` y lo muestra"; una
      recuperación automática y silenciosa violaría esa promesa de honestidad — la salida de
      `digest` tiene que ser una reconexión que el cliente inicia a sabiendas.
    - `live_lossy` no se re-evalúa por ventana de lag: el documento solo tabula la transición DE
      `durable_replay` A `live_lossy` por convergencia. La salida de `live_lossy` es
      `force_catchup()`, disparada por presión externa (cola de `queue` frames desbordada,
      presupuesto de ancho de banda excedido — ver `EventBus`), no por esta máquina.
    """

    def __init__(
        self,
        subscription_id: str,
        pattern: str,
        *,
        mode: DeliveryMode = "durable_replay",
        queue: int = 256,
    ) -> None:
        self.subscription_id = subscription_id
        self.pattern = pattern
        self.queue = queue
        self.mode: DeliveryMode = mode
        self.gaps: list[StreamGap] = []
        self._buffer: deque[StreamFrame] = deque(maxlen=queue)
        self._racha_buena = 0
        self._intentos_fallidos = 0

    def record_lag_window(self, lag: int) -> DeliveryMode:
        """Alimenta una ventana de 1 s de lag medido — ver el docstring de la clase para la
        máquina completa. No hace nada si el modo actual no es `durable_replay`: la histéresis
        de promoción solo aplica mientras se está en catch-up."""
        if self.mode != "durable_replay":
            return self.mode
        if lag < LAG_LIVE_THRESHOLD:
            self._racha_buena += 1
            if self._racha_buena >= LAG_CONSECUTIVE_WINDOWS_TO_LIVE:
                self.mode = "live_lossy"
                self._racha_buena = 0
                self._intentos_fallidos = 0
        else:
            self._racha_buena = 0
            self._intentos_fallidos += 1
            if self._intentos_fallidos >= MAX_CATCHUP_ATTEMPTS:
                self.mode = "digest"
        return self.mode

    def force_catchup(self) -> DeliveryMode:
        """Desconexión externa (cola desbordada, presupuesto de ancho de banda excedido, socket
        caído): vuelve a `durable_replay` e inicia un ciclo nuevo de promoción. `digest` es
        terminal — ni siquiera una desconexión lo saca (ver docstring de la clase)."""
        if self.mode != "digest":
            self.mode = "durable_replay"
            self._racha_buena = 0
        return self.mode

    def re_subscribe(self) -> None:
        """Única vía documentada para salir de `digest` — una reconexión explícita del cliente
        (línea 1496: "lo muestra", nunca se le miente con una recuperación automática)."""
        self.mode = "durable_replay"
        self._racha_buena = 0
        self._intentos_fallidos = 0

    def deliver(self, frame: StreamFrame) -> None:
        """Entrega un frame en vivo. Bajo presión de cola llena, descarta el más antiguo y
        registra un `StreamGap` — línea 1498: nunca un agujero invisible."""
        if len(self._buffer) == self._buffer.maxlen and self._buffer.maxlen:
            descartado = self._buffer.popleft()
            self.gaps.append(StreamGap(from_ordinal=descartado.ordinal, to_ordinal=frame.ordinal))
        self._buffer.append(frame)

    def drain(self) -> list[StreamFrame]:
        """Vacía y devuelve el buffer acumulado — lo que un transporte real consumiría para
        mandarlo por el socket."""
        elementos = list(self._buffer)
        self._buffer.clear()
        return elementos


# --------------------------------------------------------------------------------------- #
# Presupuesto de ancho de banda — §1.4, línea 1500: "MiB/s agregados, no cuenta de suscriptores"
# --------------------------------------------------------------------------------------- #


class BandwidthMeter:
    """Ventana deslizante de 1 s de bytes cargados — mide "bytes en el último segundo", que es
    la métrica que el documento nombra literalmente (línea 1500). Deliberadamente NO es un token
    bucket con burst: un bucket permitiría ráfagas por encima del límite instantáneo que el
    documento no pide ni necesita para el caso que motiva la regla (un stdout sostenido a
    2.000 ev/s, no una ráfaga corta)."""

    def __init__(
        self, limit_bytes_per_s: int, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit_bytes_per_s
        self._clock = clock
        self._eventos: list[tuple[float, int]] = []

    def charge(self, nbytes: int) -> bool:
        """Registra `nbytes` cargados AHORA y devuelve si el total de la última ventana de 1 s
        sigue dentro del presupuesto. El cargo no se deshace si `False`: este medidor solo mide;
        decidir qué hacer con la sobrecarga (degradar al suscriptor más caro, línea 1500) es de
        `EventBus`."""
        ahora = self._clock()
        self._eventos.append((ahora, nbytes))
        self._purgar(ahora)
        return self.current_rate() <= self._limit

    def current_rate(self) -> int:
        ahora = self._clock()
        self._purgar(ahora)
        return sum(b for _, b in self._eventos)

    def _purgar(self, ahora: float) -> None:
        limite_inferior = ahora - LAG_WINDOW_SECONDS
        self._eventos = [(t, b) for t, b in self._eventos if t >= limite_inferior]


# --------------------------------------------------------------------------------------- #
# `EventBus` — el punto de entrada único de los dos canales
# --------------------------------------------------------------------------------------- #


class EventBus:
    """Bus de eventos con dos canales estrictamente separados — §1.4.

    - `publish`: DURABLE. Delega íntegro en el `Journal` inyectado (`contracts.Journal`,
      protocolo cuya implementación real vive fuera de este paquete). Es la ÚNICA vía por la
      que un evento entra en la cadena de hashes.
    - `emit`: EFÍMERO. Su firma no recibe ni conoce el `Journal` — un auditor puede verificar la
      separación de canales leyendo el código de este método sin ejecutar nada: no hay ninguna
      referencia a `self._journal` en su cuerpo, ni transitivamente (`_fan_out` tampoco la toca).

    Backpressure: cada suscriptor tiene su propio `Subscription` (histéresis) y su propio
    `BandwidthMeter` (1 MiB/s); la sesión entera comparte un `BandwidthMeter` de 5 MiB/s. Al
    superar cualquiera de los dos, se degrada al suscriptor más caro primero — línea 1500: "en
    ese orden".
    """

    def __init__(self, journal: Journal, *, session_id: str) -> None:
        self._journal = journal
        self.session_id = session_id
        self._streams: dict[tuple[str, str], _EphemeralStreamHandle] = {}
        self._subs: dict[str, Subscription] = {}
        self._meters: dict[str, BandwidthMeter] = {}
        self._trie = PatternTrie()
        self._session_meter = BandwidthMeter(SESSION_BANDWIDTH_BYTES_PER_S)
        self._next_sub_seq = 0

    # -- Canal durable ------------------------------------------------------------------- #

    async def publish(
        self, drafts: Sequence[EventDraft], *, stream_id: str, expected_seq: int, lease_epoch: int
    ) -> AppendResult:
        """Único camino durable — invariante 2. No decide nada de la cadena de hashes (`seq`
        final, `hash`, `prev_hash` son del `Journal`, §1.1/§1.2); este método solo es el punto
        de entrada nombrado que hace la separación de canales auditable y valida la precondición
        estructural mínima (todos los drafts de un `publish()` comparten `stream_id`, porque un
        `AppendResult` describe un rango CONTIGUO en un único stream)."""
        ajenos = [d.stream_id for d in drafts if d.stream_id != stream_id]
        if ajenos:
            raise BusError(
                f"todos los drafts de un publish() deben compartir stream_id={stream_id!r}; "
                f"se encontraron {ajenos!r}"
            )
        return await self._journal.append(
            list(drafts), stream_id=stream_id, expected_seq=expected_seq, lease_epoch=lease_epoch
        )

    # -- Canal efímero --------------------------------------------------------------------- #

    def open_ephemeral_stream(self, anchor_event_id: str, channel: str) -> None:
        """Ancla un stream efímero nuevo a un evento durable ya publicado — §1.4, línea 1502.
        Sin esta llamada, `emit` rechaza cualquier frame para `(anchor_event_id, channel)`: todo
        stream efímero tiene que nacer anclado, nunca huérfano."""
        clave = (anchor_event_id, channel)
        if clave in self._streams:
            raise EphemeralStreamError(f"stream ya abierto para {clave!r}")
        self._streams[clave] = _EphemeralStreamHandle(anchor_event_id, channel)

    def emit(self, frame: StreamFrame) -> None:
        """Único camino efímero — JAMÁS toca `self._journal`. Absorbe el frame en el hash
        incremental del stream (si está abierto y anclado) y lo reenvía a los suscriptores vivos
        cuyo patrón matchea `frame.channel`, con presupuesto de ancho de banda."""
        clave = (frame.anchor, frame.channel)
        handle = self._streams.get(clave)
        if handle is None:
            raise EphemeralStreamError(
                f"emit sin stream abierto para {clave!r}: todo frame efímero exige "
                "open_ephemeral_stream() primero (§1.4)"
            )
        if handle.closed:
            raise EphemeralStreamError(f"stream ya cerrado: {clave!r}")
        if frame.ordinal != handle.next_ordinal:
            raise EphemeralStreamError(
                f"ordinal fuera de secuencia en {clave!r}: se esperaba {handle.next_ordinal}, "
                f"llegó {frame.ordinal}"
            )
        handle.absorb(frame)
        self._fan_out(frame)

    def close_ephemeral_stream(self, anchor_event_id: str, channel: str) -> EphemeralStreamSeal:
        """Cierra el stream y devuelve su sello (`CasRef` + estadísticas) — NO journaliza nada
        por sí mismo. Construir y `publish()` el evento durable de cierre que cita este
        `content_ref` es responsabilidad del llamador, con el tipo de dominio activo que
        corresponda (ver el test de anclaje/cierre)."""
        clave = (anchor_event_id, channel)
        handle = self._streams.get(clave)
        if handle is None:
            raise EphemeralStreamError(f"no hay stream abierto para {clave!r}")
        if handle.closed:
            raise EphemeralStreamError(f"stream ya cerrado: {clave!r}")
        return handle.seal()

    # -- Suscripción ------------------------------------------------------------------------ #

    def subscribe(
        self, pattern: str, *, mode: DeliveryMode = "live_lossy", queue: int = 256
    ) -> Subscription:
        """Suscribe un patrón — §1.4, línea 1513. `mode="live_lossy"` asume que el suscriptor ya
        está al día (un cliente nuevo desde `head`); `mode="durable_replay"` es para un cliente
        que arranca haciendo catch-up desde un `last_applied_seq` antiguo."""
        self._next_sub_seq += 1
        sub_id = f"sub-{self._next_sub_seq}"
        sub = Subscription(sub_id, pattern, mode=mode, queue=queue)
        self._subs[sub_id] = sub
        self._meters[sub_id] = BandwidthMeter(SUBSCRIBER_BANDWIDTH_BYTES_PER_S)
        self._trie.add(pattern, sub_id)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._trie.remove(sub.pattern, sub.subscription_id)
        self._subs.pop(sub.subscription_id, None)
        self._meters.pop(sub.subscription_id, None)

    def _fan_out(self, frame: StreamFrame) -> None:
        nbytes = len(frame.bytes)
        self._session_meter.charge(nbytes)
        for sub_id in self._trie.match(frame.channel):
            sub = self._subs.get(sub_id)
            if sub is None or sub.mode != "live_lossy":
                continue  # digest y durable_replay no reciben frames en vivo (§1.4)
            medidor = self._meters[sub_id]
            dentro_de_presupuesto = medidor.charge(nbytes)
            if not dentro_de_presupuesto:
                sub.force_catchup()
                continue
            sub.deliver(frame)
        self._degradar_si_excede_presupuesto_de_sesion()

    def _degradar_si_excede_presupuesto_de_sesion(self) -> None:
        """ "Superarlo degrada al suscriptor más caro a `digest`, en ese orden" — §1.4, línea
        1500. "En ese orden" se lee como: primero se agota el presupuesto POR SUSCRIPTOR (ya
        resuelto arriba, en `_fan_out`, cada uno con su propio `force_catchup`); solo si el
        AGREGADO de sesión también se excede se interviene aquí, sobre el más caro entre los que
        siguen `live_lossy`."""
        if self._session_meter.current_rate() <= SESSION_BANDWIDTH_BYTES_PER_S:
            return
        candidatos = [
            (self._meters[sid].current_rate(), sid)
            for sid, sub in self._subs.items()
            if sub.mode == "live_lossy"
        ]
        if not candidatos:
            return
        candidatos.sort(reverse=True)
        _, mas_caro = candidatos[0]
        self._subs[mas_caro].force_catchup()
