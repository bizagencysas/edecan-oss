"""Proyecciones de Forge: fold determinista sobre el journal — el journal es la única fuente de
verdad (invariante 2), y una proyección es, por definición, una VISTA reconstruible de esa
verdad, nunca una segunda fuente. Este módulo fija el contrato `Projection` y dos proyecciones
reales de la fase 1: `session_timeline` y `budget_ledger`.

Regla dura heredada de `contracts.py`: nada se rellena a mano. Donde el documento no tabula algo
(aquí, casi todo el detalle operativo de una proyección concreta — el documento fija el
CONCEPTO, §1.4, línea 1493: "toda proyección persiste `last_applied_seq` y descarta `seq <=
last_applied_seq` en la misma transacción en que escribe su estado"), este módulo lo dice en su
propio docstring como síntesis propia, no como cita.

**Hueco documentado (para `pendiente` del encargo):** el espacio de nombres `budget.*`
(`allocated`/`charged`/`warned`/`exhausted`/`released`, `contracts.py` §7/§13.1 fila 3) está
`reserved`, no `active`, en `SCHEMA_REGISTRY` — ver `contracts._NAMESPACE_RESERVADO` y
`_ACTIVOS`. Ningún evento durable de ESTE paquete transporta `Budget`/`Hold`/`usd_micros`
todavía. `BudgetLedgerProjection`, tal como se pide en el encargo ("un ledger de presupuesto"),
no puede plegar sobre eventos que no existen como `active` sin inventar un tipo — y "ampliar
esto después es añadir `status='active'` a una entrada YA declarada, nunca inventar un tipo
nuevo" (`contracts.py`, línea 1301-1303) es una regla de ESTE paquete que este módulo no se
salta. La proyección de abajo es un SUSTITUTO explícito y acotado: cuenta llamadas a
herramienta por `EffectClass` y desenlace (`tool.call_requested/completed/failed/...`, que SÍ
son `active` y SÍ llevan `effect_class`), que es la señal de exposición a riesgo/coste
disponible hoy. Cuando `budget.*` se active, esta proyección debe reemplazarse o ampliarse para
plegar sobre `Budget` real en `usd_micros` — no antes.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any, ClassVar

from edecan_forge_kernel.contracts import SCHEMA_REGISTRY, CasRef, EffectClass, Event

# --------------------------------------------------------------------------------------- #
# Canonicalización local — deliberadamente NO importa `contracts._canonical_json` (privada de
# ese módulo): un `state_hash` de proyección es un contrato de ESTE módulo, y dos módulos que
# comparten una función "por casualidad" porque uno importó el `_privado` del otro es
# exactamente el acoplamiento oculto que la invariante 10 ("cero acoplamiento directo entre
# módulos") prohíbe. La forma canónica es la misma (claves ordenadas, sin espacios, UTF-8, sin
# `NaN`/`Infinity`) porque es la única forma determinista razonable en JSON puro — no porque
# haya una dependencia entre los dos módulos.
# --------------------------------------------------------------------------------------- #


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


class ProjectionError(RuntimeError):
    """Uso incorrecto de una proyección (evento de un tipo que la proyección no sabe plegar,
    payload que no valida contra su propio esquema registrado...). Nunca corrupción del
    journal: eso lo detecta la verificación de cadena de hashes, no una proyección."""


# --------------------------------------------------------------------------------------- #
# El contrato `Projection`
# --------------------------------------------------------------------------------------- #


class Projection(ABC):
    """Contrato de una proyección: fold sobre `Event`, `last_applied_seq` por stream,
    reconstruible desde cero — §1.4, línea 1493.

    **Idempotencia por `(stream_id, seq)`.** `apply()` es la única vía de entrada y es la
    ÚNICA responsable de la deduplicación: comprueba `event.seq` contra el `last_applied_seq`
    del stream de ese evento ANTES de invocar `_fold` (el método que cada subclase implementa),
    y solo si `event.seq` es estrictamente mayor pliega y avanza el cursor. Una subclase nunca
    necesita su propia lógica de deduplicación — centralizarla aquí es lo que hace que "entrega
    doble no corrompe el estado" sea una propiedad de la CLASE BASE, verificable una sola vez,
    en vez de una obligación repetida (y potencialmente olvidada) en cada proyección nueva.

    **"Misma transacción" en un solo proceso.** El documento pide que `last_applied_seq` se
    persista "en la misma transacción" en que se escribe el resto del estado (línea 1493). Este
    módulo corre en memoria de un proceso: la atomicidad real entre el `state` durable y
    `last_applied_seq` durable es responsabilidad del HOST que persiste ambos (una fila de
    SQLite, una transacción de Postgres...), no de esta clase. Lo que esta clase sí garantiza es
    el ORDEN: `_fold` se ejecuta antes de avanzar el cursor, así que si `_fold` lanza una
    excepción a mitad de camino, el cursor NO avanza — un reintento del mismo evento no se
    pierde por partirse en dos. Lo que esta clase NO garantiza es que un `_fold` que muta estado
    parcialmente y LUEGO lanza deje ese estado limpio: una subclase que muta y puede fallar tiene
    que hacerlo de forma atómica ella misma (construir el nuevo estado y solo entonces asignarlo,
    nunca mutar in situ paso a paso). Las dos proyecciones de este módulo lo hacen así.

    **Reconstrucción determinista.** `rebuild()` no asume ningún orden de iteración de `events`:
    ordena explícitamente por `(stream_id, seq)` antes de plegar. Sin este orden explícito, un
    `dict`/`set` desordenado aguas arriba (o `PYTHONHASHSEED` aleatorio reordenando algo que
    dependiera de iteración de hash) produciría un `state_hash` distinto entre dos ejecuciones
    del mismo journal — precisamente el bug que el test de reconstrucción en otro proceso existe
    para atrapar.
    """

    name: ClassVar[str]
    """Nombre estable de la proyección — es el `projection_name` que cita `contracts.Guard`."""

    def __init__(self) -> None:
        self._last_applied_seq: dict[str, int] = {}

    def last_applied_seq(self, stream_id: str) -> int:
        """`0` si el stream nunca se aplicó — `Event.seq` empieza en 1 (`ge=1`, `contracts.py`),
        así que `0` es un centinela seguro de "nada aplicado todavía"."""
        return self._last_applied_seq.get(stream_id, 0)

    def apply(self, event: Event) -> None:
        """Punto único de entrada. Ver el docstring de la clase para la garantía de
        idempotencia y de orden fold-antes-que-cursor."""
        aplicado_hasta = self._last_applied_seq.get(event.stream_id, 0)
        if event.seq <= aplicado_hasta:
            return  # entrega repetida (at-least-once, §1.4) — inofensiva por construcción
        self._fold(event)
        self._last_applied_seq[event.stream_id] = event.seq

    def apply_all(self, events: Iterable[Event]) -> None:
        """Conveniencia sobre `apply()`, en el orden en que `events` los produzca. `rebuild()`
        es quien garantiza el orden `(stream_id, seq)`; este método no reordena — permite a un
        consumidor en vivo alimentar eventos en el orden en que realmente llegan (que puede
        intercalar streams distintos) sin que este método presuponga nada sobre esa mezcla."""
        for evento in events:
            self.apply(evento)

    @abstractmethod
    def _fold(self, event: Event) -> None:
        """Aplica UN evento nuevo (ya sabido no-repetido) al estado interno. Debe ser total: un
        `event.type` que la proyección no reconoce se ignora (una proyección ve un subconjunto
        del journal, nunca todo — §1.4, `subscribe(pattern)`), nunca revienta el fold entero."""

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """Vista PURA y estructural del estado — sin floats, sin objetos no serializables. Es
        la entrada de `state_hash()` y lo único que `rebuild()` en otro proceso necesita
        reproducir byte a byte."""

    def state_hash(self) -> str:
        """Hash determinista del `snapshot()` actual — la forma serializada de `CasRef`, así que
        dos proyecciones con el mismo `state_hash` son, por construcción, el mismo estado. Es lo
        que `contracts.Guard.expected_state_hash` cita para un `append_if` condicionado a esta
        proyección."""
        return str(CasRef.from_bytes(_canonical_json(self.snapshot())))

    @classmethod
    def rebuild(cls, events: Iterable[Event]) -> Projection:
        """Reconstruye la proyección DESDE CERO — §1.4. `events` no necesita venir ya ordenado;
        este método fuerza el único orden que produce un resultado determinista:
        `(stream_id, seq)`. Es la operación que el test de "otro proceso" ejercita: dos
        llamadas a `rebuild()` con el mismo conjunto de eventos, en dos procesos con
        `PYTHONHASHSEED` distinto, deben producir el mismo `state_hash()`."""
        instancia = cls()
        for evento in sorted(events, key=lambda e: (e.stream_id, e.seq)):
            instancia.apply(evento)
        return instancia


# --------------------------------------------------------------------------------------- #
# `session_timeline`
# --------------------------------------------------------------------------------------- #


class TimelineEntry:
    """Un hito de la línea de tiempo de una sesión — un `Event` activo reducido a lo que la UI
    "modo seguir" necesita para pintar una fila: qué pasó, a qué llamada pertenece (si aplica) y
    con qué `EffectClass` (si se conoce en el momento del fold). No es un modelo Pydantic porque
    vive solo dentro de `SessionTimelineProjection._entries`; lo que SÍ cruza un límite de
    proceso es `snapshot()`, que lo vuelca a un `dict` puro."""

    __slots__ = ("seq", "type", "call_id", "tool_id", "effect_class")

    def __init__(
        self,
        *,
        seq: int,
        type_: str,
        call_id: str | None,
        tool_id: str | None,
        effect_class: EffectClass | None,
    ) -> None:
        self.seq = seq
        self.type = type_
        self.call_id = call_id
        self.tool_id = tool_id
        self.effect_class = effect_class

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "type": self.type,
            "call_id": self.call_id,
            "tool_id": self.tool_id,
            "effect_class": None if self.effect_class is None else self.effect_class.value,
        }


class SessionTimelineProjection(Projection):
    """`session_timeline` — la línea de tiempo, por sesión (`stream_id`), de los hechos
    ACTIVOS del namespace `session.*`/`tool.call_*` (`contracts._ACTIVOS`). Cada entrada guarda
    el `EffectClass` de la llamada a la que pertenece, resuelto por MEMORIA de fold: solo
    `tool.call_requested` lo lleva en su payload (`ToolCallRequestedPayload.effect_class`); los
    eventos terminales (`tool.call_completed`, `.failed`...) solo llevan `call_id`, así que esta
    proyección recuerda el `effect_class` visto en el `requested` correspondiente y lo repite en
    las entradas siguientes del mismo `call_id` — es exactamente el mismo patrón que
    `KernelState.tool_calls` en `contracts.reduce`, aplicado en modo lectura.

    Usa `SCHEMA_REGISTRY.descriptor(event.type).payload_model` para revalidar el
    `payload_inline` contra su propio modelo tipado en vez de leer el `dict` a mano — así un
    evento cuyo payload no valida contra el esquema con el que se registró revienta aquí, en
    vez de producir una entrada silenciosamente incompleta.
    """

    name: ClassVar[str] = "session_timeline"

    def __init__(self) -> None:
        super().__init__()
        self._session_ids: dict[str, str] = {}  # stream_id -> session_id declarado en el payload
        self._entries: dict[str, list[TimelineEntry]] = {}
        self._effect_class_by_call: dict[str, EffectClass] = {}

    def _fold(self, event: Event) -> None:
        if event.payload_inline is None:
            # Los tipos activos de este paquete son todos `payload_inline` (ver `_ACTIVOS`,
            # ninguno lleva `checkpoint_ref`/`evidence_ref` como payload_REF de nivel evento,
            # solo como campo estructural DENTRO del inline). Un evento con `payload_ref` puro
            # exigiría leer CAS, que esta proyección no tiene inyectado — se ignora sin romper
            # el fold (documentado arriba, en el docstring de `_fold`).
            return
        descriptor = SCHEMA_REGISTRY.descriptor(event.type)
        payload = descriptor.payload_model.model_validate(event.payload_inline)

        session_id = getattr(payload, "session_id", None)
        if session_id is not None:
            self._session_ids[event.stream_id] = session_id

        call_id = getattr(payload, "call_id", None)
        effect_class = getattr(payload, "effect_class", None)
        if call_id is not None and effect_class is not None:
            self._effect_class_by_call[call_id] = effect_class
        efecto_conocido = None if call_id is None else self._effect_class_by_call.get(call_id)

        entradas = self._entries.setdefault(event.stream_id, [])
        entradas.append(
            TimelineEntry(
                seq=event.seq,
                type_=event.type,
                call_id=call_id,
                tool_id=getattr(payload, "tool_id", None),
                effect_class=efecto_conocido,
            )
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_ids": dict(self._session_ids),
            "entries": {
                stream_id: [entrada.as_dict() for entrada in entradas]
                for stream_id, entradas in self._entries.items()
            },
        }

    def timeline(self, stream_id: str) -> tuple[TimelineEntry, ...]:
        """Lectura tipada, por conveniencia del consumidor — `snapshot()` es la vista estructural
        para hashing/serialización; esta es la vista para código Python que quiere los objetos,
        no diccionarios."""
        return tuple(self._entries.get(stream_id, ()))


# --------------------------------------------------------------------------------------- #
# `budget_ledger` — ver el hueco documentado en el docstring del módulo
# --------------------------------------------------------------------------------------- #

_OUTCOME_BY_SUFFIX: dict[str, str] = {
    "tool.call_requested": "requested",
    "tool.call_completed": "completed",
    "tool.call_failed": "failed",
    "tool.call_cancelled": "cancelled",
    "tool.call_rejected": "rejected",
    "tool.call_suspended": "suspended",
    "tool.call_orphaned": "orphaned",
    "tool.call_unknown": "unknown",
}
"""Los ocho tipos `tool.call_*` que este ledger tally-ea, mapeados a la clave de desenlace de la
tabla. `tool.call_admitted` queda fuera a propósito: es un subestado interno de admisión
(`contracts.py`, §4b), no un desenlace que consuma o libere presupuesto."""

_OUTCOME_KEYS: tuple[str, ...] = (
    "requested",
    "completed",
    "failed",
    "cancelled",
    "rejected",
    "suspended",
    "orphaned",
    "unknown",
)


class BudgetLedgerProjection(Projection):
    """`budget_ledger` — SUSTITUTO documentado de un ledger de `Budget`/`usd_micros` real (ver
    el hueco en el docstring del módulo): tally de llamadas a herramienta por `EffectClass` y
    desenlace, por sesión (`stream_id`).

    El `EffectClass` de cada llamada se conoce en `tool.call_requested`
    (`ToolCallRequestedPayload.effect_class`) y se recuerda por `call_id` para atribuir
    correctamente los eventos terminales, que no repiten el campo — mismo patrón que
    `SessionTimelineProjection`. Un evento terminal cuyo `call_id` nunca se vio en un
    `requested` dentro de la ventana de eventos plegados (recorte de historia, réplica
    parcial...) se tally-ea bajo `EffectClass` desconocida en vez de reventar: una proyección de
    solo lectura no puede exigir que su entrada esté completa, solo puede ser honesta sobre lo
    que falta.
    """

    name: ClassVar[str] = "budget_ledger"

    UNKNOWN_EFFECT_CLASS_KEY: ClassVar[str] = "unknown"
    """Clave de tally para una llamada cuyo `EffectClass` no se pudo atribuir — ver docstring."""

    def __init__(self) -> None:
        super().__init__()
        self._tally: dict[str, dict[str, dict[str, int]]] = {}
        self._effect_class_by_call: dict[str, EffectClass] = {}

    def _fold(self, event: Event) -> None:
        clave_desenlace = _OUTCOME_BY_SUFFIX.get(event.type)
        if clave_desenlace is None or event.payload_inline is None:
            return
        descriptor = SCHEMA_REGISTRY.descriptor(event.type)
        payload = descriptor.payload_model.model_validate(event.payload_inline)
        call_id = getattr(payload, "call_id", None)
        if call_id is None:
            return

        effect_class = getattr(payload, "effect_class", None)
        if effect_class is not None:
            self._effect_class_by_call[call_id] = effect_class
        conocida = self._effect_class_by_call.get(call_id)
        clave_effect_class = self.UNKNOWN_EFFECT_CLASS_KEY if conocida is None else conocida.value

        por_sesion = self._tally.setdefault(event.stream_id, {})
        por_clase = por_sesion.setdefault(clave_effect_class, dict.fromkeys(_OUTCOME_KEYS, 0))
        por_clase[clave_desenlace] += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "tally": {
                stream_id: {clase: dict(desenlaces) for clase, desenlaces in por_clase.items()}
                for stream_id, por_clase in self._tally.items()
            }
        }

    def tally_for(self, stream_id: str, effect_class: EffectClass) -> dict[str, int]:
        """Lectura tipada de conveniencia — cuenta por desenlace para `(stream_id,
        effect_class)`, todo en cero si no hay tally todavía (nunca `KeyError`)."""
        por_sesion = self._tally.get(stream_id, {})
        return dict(por_sesion.get(effect_class.value, dict.fromkeys(_OUTCOME_KEYS, 0)))
