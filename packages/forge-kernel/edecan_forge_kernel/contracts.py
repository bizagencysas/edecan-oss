"""Contratos unificados del kernel de Forge.

Este módulo es el que evita que Forge se construya con diez dialectos. Diez bloques de
`docs/arquitectura-forge.md` fueron diseñados por equipos distintos; un revisor final los leyó
juntos y encontró **doce contradicciones reales** más un puñado de campos huérfanos que nadie
había asignado. Están resueltas y documentadas en `docs/arquitectura-forge.md` §13
("Coherencia: contradicciones resueltas y huecos asignados"). Este archivo MATERIALIZA esas
resoluciones en tipos — no las re-discute. Cada sección lleva, en su docstring, la cita del
documento de la que sale la decisión, para que un desacuerdo se dirima ahí y no aquí.

Regla dura heredada de `edecan_forge_probe.modelcard`: nada se rellena a mano ni se inventa
para "que quede bonito". Donde el documento no fija un valor (por ejemplo, la política exacta
de reintento de cada `EffectClass` ante un crash, que el documento nunca tabula explícitamente
para las seis clases juntas), este módulo lo dice en su propio docstring como una síntesis
propia y no como una cita, para que quede auditable.

Dos desviaciones DELIBERADAS frente a lo escrito en el documento, exigidas por las reglas duras
del encargo de este paquete (nada de dependencias fuera de `pydantic` y la stdlib):

1. **Hash de contenido.** El documento fija BLAKE3 (`b3:<64 hex>`) para `CasRef`. `blake3` no
   está disponible en este entorno como dependencia permitida, así que aquí se usa
   `hashlib.blake2b(digest_size=32)`, etiquetado `b2b:<64 hex>`. Es el mismo contrato
   (algoritmo etiquetado, un único `CasRef`, resolución §13.1 fila 4) con otro primitivo de
   hash. Un `b3:` y un `b2b:` nunca son el mismo objeto aunque el contenido coincida — eso es
   justamente lo que el etiquetado existe para dejar explícito.
2. **Canonicalización.** El documento fija CBOR (RFC 8949) para el hash-chain del journal. Sin
   `cbor2` disponible, este módulo canonicaliza con JSON (`json.dumps(..., sort_keys=True,
   separators=(",", ":"))`, sin espacios, claves ordenadas, `ensure_ascii=False`). Vale para
   determinismo dentro de este paquete; el día que el host real firme el journal con CBOR, la
   forma canónica cambia y con ella el `hash` — no son intercambiables byte a byte.

Y una tercera no-desviación que conviene decir en voz alta: `ModelCard` (resolución §13.1 fila
6, "una sola `ModelCard`, propiedad de `edecan_forge_probe`") NO se importa ni se redefine
aquí. Importarla arrastraría `httpx` y `edecan-llm` a un árbol de dependencias que el propio
documento (§1, árbol de dependencias del kernel) exige mínimo: "`pydantic`, `blake3`, `cbor2`,
stdlib. Nada más" — sustituyendo `blake3`/`cbor2` por lo de arriba, la intención de "nada más"
se mantiene. Cualquier contrato de este módulo que necesite señalar un modelo lo hace por la
tripleta `(proveedor, modelo, revision_sonda)` — ver `ModelIdentity` — nunca duplicando los
campos medidos de la tarjeta real.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------------------- #
# Utilidades de hash y canonicalización — ver la nota de desviación en el docstring de arriba
# --------------------------------------------------------------------------------------- #


def _canonical_json(value: Any) -> bytes:
    """Forma canónica JSON: claves ordenadas, sin espacios, UTF-8, sin `NaN`/`Infinity`.

    Sustituye al CBOR canónico (RFC 8949) que fija el documento §1.2, por la razón de
    dependencias explicada en el docstring del módulo. `allow_nan=False` es la forma de
    imponer aquí la invariante "sin floats" del documento: un `float` finito pasa, pero
    `nan`/`inf` — que ni siquiera son JSON válido — revientan antes de mentir en el journal.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _blake2b_hex(data: bytes) -> str:
    """`blake2b` de 256 bits en hexadecimal — el primitivo detrás de `b2b:` (ver arriba)."""
    return hashlib.blake2b(data, digest_size=32).hexdigest()


# --------------------------------------------------------------------------------------- #
# 1. `CasRef` — resolución §13.1 fila 4: "un único `CasRef` con algoritmo etiquetado"
# --------------------------------------------------------------------------------------- #

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

CasAlgorithm = Literal["b2b"]
"""Único algoritmo soportado hoy. El documento admite `b3`/`sha256`; aquí solo `b2b` porque es
el único primitivo disponible sin dependencia nueva (ver desviación #1). Añadir un segundo
algoritmo es coherente con el contrato (`CasRef` ya lleva el algoritmo etiquetado) y NO exige
tocar esta clase, solo ampliar el `Literal` — es la extensibilidad que el diseño compró."""


class CasRef(BaseModel, frozen=True):
    """Referencia a contenido direccionado por hash — invariante 4 del proyecto.

    El journal transporta esto, nunca el payload. `str(ref)` es la forma serializada
    (`"b2b:<64 hex>"`) que viaja en `payload_inline` cuando una referencia a CAS es en sí
    misma un dato estructural (identificador), no el contenido que apunta.
    """

    algorithm: CasAlgorithm = "b2b"
    digest: str

    @field_validator("digest")
    @classmethod
    def _validar_digest(cls, v: str) -> str:
        v = v.lower()
        if not _HEX64_RE.match(v):
            raise ValueError(f"digest de CasRef inválido, se esperaban 64 hex: {v!r}")
        return v

    @classmethod
    def from_bytes(cls, data: bytes) -> CasRef:
        """Calcula el `CasRef` real de un blob. Es la única forma "de verdad" de construir uno;
        el resto de constructores (`parse`) son para reconstituir uno ya calculado."""
        return cls(algorithm="b2b", digest=_blake2b_hex(data))

    @classmethod
    def parse(cls, wire: str) -> CasRef:
        """Reconstituye un `CasRef` desde su forma serializada `"b2b:<64 hex>"`."""
        try:
            algoritmo, digest = wire.split(":", 1)
        except ValueError as exc:
            raise ValueError(f"CasRef mal formado, se esperaba 'alg:hex': {wire!r}") from exc
        if algoritmo != "b2b":
            raise ValueError(f"algoritmo de CasRef no soportado: {algoritmo!r}")
        return cls(algorithm=algoritmo, digest=digest)

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.digest}"


# --------------------------------------------------------------------------------------- #
# 2. `EffectClass` — resolución §13.1 fila 6 y §14.402-416 (crash policy), unificadas
# --------------------------------------------------------------------------------------- #


class EffectClass(StrEnum):
    """Orden TOTAL de riesgo de un efecto, y única taxonomía del sistema — resolución §13.1
    fila 6: "gana el orden total de cinco valores del bloque 5 [...] porque es la única que
    compone con `max()` y la única que gobierna la decisión de aprobar".

    Las cinco primeras clases y su definición son literales del documento §5.6 (tabla de
    `EffectClass`, líneas 3837-3843):

    | Clase           | Definición                                                          |
    |-----------------|----------------------------------------------------------------------|
    | `SAFE`          | sin escritura, sin red, sin efecto                                   |
    | `REVERSIBLE`    | escritura en el workspace CoW (revertible por snapshot)              |
    | `DESTRUCTIVE`   | pérdida local recuperable solo por snapshot (`rm -rf`, `reset --hard`)|
    | `IRREVERSIBLE`  | sin deshacer (force push, drop table, borrado en la nube)            |
    | `EXTERNAL_EFFECT`| observable por terceros (deploy, email, pago, publicación)          |

    `EXTERNAL_IDEMPOTENT` es la sexta clase que el propio encargo pide añadir ("ampliable a
    seis con `external_idempotent`"), fusionando la taxonomía de arriba con la tabla de
    política ante crash del bloque 7 (§4.402-410), que trae CUATRO clases con semántica de
    reintento (`pure`/`workspace`/`external_idempotent`/`external_irreversible`) pero que el
    documento nunca cruza campo a campo con la de cinco. Esa fusión es la única pieza de este
    módulo que es una SÍNTESIS propia, no una cita, y se explica en `RETRY_POLICY_BY_EFFECT`
    más abajo. La posición elegida — entre `IRREVERSIBLE` y `EXTERNAL_EFFECT` — es deliberada:
    un efecto externo con clave de deduplicación aceptada por el destino (`Idempotency-Key`,
    `PUT` de objeto, `--force-with-lease`) es observable por terceros igual que
    `EXTERNAL_EFFECT`, así que exige la misma aprobación asistida; pero es SEGURO de reintentar
    tras un crash, que ningún otro valor por encima de `REVERSIBLE` lo es, así que compone por
    debajo del externo puro cuando lo que se está decidiendo es política de reintento, no
    aprobación.
    """

    SAFE = "safe"
    REVERSIBLE = "reversible"
    DESTRUCTIVE = "destructive"
    IRREVERSIBLE = "irreversible"
    EXTERNAL_IDEMPOTENT = "external_idempotent"
    EXTERNAL_EFFECT = "external_effect"


_EFFECT_CLASS_ORDER: tuple[EffectClass, ...] = (
    EffectClass.SAFE,
    EffectClass.REVERSIBLE,
    EffectClass.DESTRUCTIVE,
    EffectClass.IRREVERSIBLE,
    EffectClass.EXTERNAL_IDEMPOTENT,
    EffectClass.EXTERNAL_EFFECT,
)
EFFECT_CLASS_RANK: dict[EffectClass, int] = {c: i for i, c in enumerate(_EFFECT_CLASS_ORDER)}
"""Rango entero para comparar `EffectClass` sin depender del orden de declaración del `StrEnum`
(que Python no ordena por sí solo). Es la base de `effect_class_max`."""


def effect_class_max(*valores: EffectClass) -> EffectClass:
    """`effect_class = max(declarada, derivada de las capacidades, max(may_invoke))` — §5.6.

    Es la única operación de composición del sistema para riesgo de efecto (resolución §13.1
    fila 6). La declaración de una herramienta puede SUBIR la clase real, nunca bajarla — por
    eso componer siempre es `max()` y nunca un promedio, una prioridad declarada o cualquier
    otra regla que un valor bajo pudiera anular.
    """
    if not valores:
        raise ValueError("effect_class_max necesita al menos un valor")
    return max(valores, key=lambda c: EFFECT_CLASS_RANK[c])


class RetryPolicy(StrEnum):
    """Política de reintento ante crash — síntesis de la tabla del bloque 7 (§4.402-410) sobre
    las seis `EffectClass`. NO es una cita literal: el documento tabula esto para cuatro
    clases de una taxonomía de idempotencia que ya no existe como tipo separado (se fusionó en
    `EffectClass`, ver arriba), así que el mapeo campo-a-campo para las clases que aparecen en
    AMBAS taxonomías del documento (local `DESTRUCTIVE`/`IRREVERSIBLE` vs. la definición de
    `external_irreversible` que solo menciona ejemplos externos) es una decisión de este
    contrato, tomada con el mismo criterio que repite el documento en todo el bloque 5:
    "default hostil" — ante la duda, no reintentar solo.
    """

    FREE = "free"
    """Reintento libre, sin coordinación. `pure`/`workspace` del documento."""
    KEYED = "keyed"
    """Reintento solo con la `idempotency_key` ya reservada. `external_idempotent`."""
    TWO_PHASE_NO_RETRY = "two_phase_no_retry"
    """`intent_recorded` → ejecución → `settled`, nunca reintento automático. Un intento sin
    settle tras un crash escala a un humano — nunca se adivina (§5.3)."""


RETRY_POLICY_BY_EFFECT_CLASS: dict[EffectClass, RetryPolicy] = {
    EffectClass.SAFE: RetryPolicy.FREE,
    EffectClass.REVERSIBLE: RetryPolicy.FREE,
    EffectClass.DESTRUCTIVE: RetryPolicy.TWO_PHASE_NO_RETRY,
    EffectClass.IRREVERSIBLE: RetryPolicy.TWO_PHASE_NO_RETRY,
    EffectClass.EXTERNAL_IDEMPOTENT: RetryPolicy.KEYED,
    EffectClass.EXTERNAL_EFFECT: RetryPolicy.TWO_PHASE_NO_RETRY,
}


def retry_policy_for(effect_class: EffectClass) -> RetryPolicy:
    """Resuelve la política de reintento de una `EffectClass` ya compuesta con `max()`."""
    return RETRY_POLICY_BY_EFFECT_CLASS[effect_class]


# --------------------------------------------------------------------------------------- #
# 3. Confianza y contaminación — resolución §13.1 fila 5, ladder de §5.8 (línea 3974)
# --------------------------------------------------------------------------------------- #


class TrustLevel(StrEnum):
    """Seis niveles de confianza de origen (`TaintLabel`), de más a menos confiable —
    resolución §13.1 fila 5: "gana la del bloque 5: seis niveles, alcance de sesión, marca de
    agua alta [...]. Es la única que resiste el ataque de inyección de prompt". El ladder
    literal es §5.8: `SYSTEM > OPERATOR > USER > WORKSPACE_CODE > TOOL_OUTPUT > NETWORK`.
    """

    SYSTEM = "system"
    OPERATOR = "operator"
    USER = "user"
    WORKSPACE_CODE = "workspace_code"
    TOOL_OUTPUT = "tool_output"
    NETWORK = "network"


_TRUST_ORDER: tuple[TrustLevel, ...] = (
    TrustLevel.SYSTEM,
    TrustLevel.OPERATOR,
    TrustLevel.USER,
    TrustLevel.WORKSPACE_CODE,
    TrustLevel.TOOL_OUTPUT,
    TrustLevel.NETWORK,
)
TAINT_RANK: dict[TrustLevel, int] = {t: i for i, t in enumerate(_TRUST_ORDER)}
"""0 = `SYSTEM` (menos contaminado) .. 5 = `NETWORK` (más contaminado). `taint_hwm` (§5.8,
línea 3983, `max(taint de todo blob ingerido)`) es el nivel de MAYOR rango visto, no el nivel
de mayor confianza — de ahí que este rango crezca con la contaminación, no con la confianza."""


class TaintState(BaseModel):
    """Marca de agua alta de contaminación de una sesión — §5.8, líneas 3979-3991.

    ```
    taint_hwm(session) = max(taint de todo blob ingerido desde el último reset)
    ```

    Monótona no decreciente hasta un reset EXPLÍCITO, que solo ocurre de dos formas — ambas
    journaleadas como `sec.taint.reset` en el documento —: una compactación de contexto que
    DESCARTA los blobs contaminados, o una autorización humana explícita. Ninguna otra
    operación puede bajar el `hwm`; intentarlo con cualquier otro motivo es el bug exacto que
    hace evadible el trinquete de capacidad.
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    hwm: TrustLevel = TrustLevel.SYSTEM

    def ingest(self, label: TrustLevel) -> TaintState:
        """Registra un blob nuevo. `hwm` solo puede subir de rango (contaminarse más)."""
        if TAINT_RANK[label] <= TAINT_RANK[self.hwm]:
            return self
        return self.model_copy(update={"hwm": label})

    def reset(self, *, reason: Literal["compaction_discard", "human_authorization"]) -> TaintState:
        """Las dos únicas vías de reset que admite §5.8. `reason` no es decorativo: es lo que
        el productor debe volcar en `sec.taint.reset` — este método no lo journaliza (eso es
        del host), pero fuerza que quien llama declare por cuál de las dos vías entra."""
        del reason  # documental: obliga a la llamada a declararlo aunque el valor no se use aquí
        return self.model_copy(update={"hwm": TrustLevel.SYSTEM})


class EffectCeilingPolicy(Protocol):
    """`techo(taint_hwm) -> EffectClass` — §5.8, línea 3985: `effect_class_max =
    min(effect_class_max_previo, techo_efecto(taint_hwm))`.

    El documento nunca tabula la función completa (qué `EffectClass` techo corresponde a cada
    uno de los seis `TrustLevel`); solo dos puntos ancla: `taint_hwm >= TOOL_OUTPUT` cierra
    `net:connect` con cuerpo, y subir de ahí exige aprobación humana (línea 3995). Publicar una
    tabla completa sin que el documento la fije sería inventar un número de seguridad, así que
    aquí solo se fija el CONTRATO (protocolo de una función), no la tabla. El bloque de
    seguridad (5.8) es quien la implementa.
    """

    def ceiling_for(self, taint: TrustLevel) -> EffectClass: ...


# --------------------------------------------------------------------------------------- #
# 4. La máquina de estados de la llamada a herramienta — resolución §13.1 fila 1
# --------------------------------------------------------------------------------------- #


class ToolCallState(StrEnum):
    """Máquina de estados ÚNICA de una llamada a herramienta — resolución §13.1 fila 1: "Una
    sola, propiedad del bloque 1. Los estados del bloque 5 (`validated/authorized/
    queued/dispatched`) pasan a ser subestados declarados de `admitted`. `unknown` e
    `indeterminate` se unifican [en `UNKNOWN`]".

    El diagrama pinneado es §1.6, líneas 1580-1586:

    ```
    requested ──► admitted ──► started ──► completed   (terminal)
        │            │            ├──────► failed      (terminal)
        │            │            ├──────► cancelled   (terminal)
        │            │            ├──────► suspended ──► started
        └──► rejected (terminal)  └──────► orphaned ──► completed | failed | unknown (terminal)
    ```

    Terminales: `completed`, `failed`, `cancelled`, `rejected`, `unknown` (§1.6, línea 1588).
    """

    REQUESTED = "requested"
    ADMITTED = "admitted"
    STARTED = "started"
    SUSPENDED = "suspended"
    ORPHANED = "orphaned"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    """Fusiona `unknown` (bloque 1, salida de `orphaned` sin sonda de idempotencia) e
    `indeterminate` (bloque 5, salida de un `EffectLedger.poison`) en un único terminal, tal
    como pide la resolución. Semánticamente son el mismo hecho: "no se sabe si el efecto
    ocurrió, y no se va a adivinar" (§1.6 / §5.3 "resolve no es un adorno")."""


class AdmissionSubstate(StrEnum):
    """Subestados de `ToolCallState.ADMITTED` — el ciclo de vida de §5.2 (línea 3500,
    `received -> admitted -> validated -> authorized -> [approval_pending] -> queued ->
    dispatched -> running`) DEJA de ser una máquina de estados de nivel superior propia y pasa
    a describir qué ocurre DENTRO de `admitted`, por mandato de la resolución §13.1 fila 1.
    `received`/`running`/`settled` no entran aquí: `received` es `ToolCallState.REQUESTED`,
    `running` es `ToolCallState.STARTED`, y `settled` es el evento terminal, no un estado.
    """

    VALIDATED = "validated"
    AUTHORIZED = "authorized"
    APPROVAL_PENDING = "approval_pending"
    QUEUED = "queued"
    DISPATCHED = "dispatched"


_ADMISSION_SUBSTATE_ORDER: tuple[AdmissionSubstate, ...] = (
    AdmissionSubstate.VALIDATED,
    AdmissionSubstate.AUTHORIZED,
    AdmissionSubstate.APPROVAL_PENDING,
    AdmissionSubstate.QUEUED,
    AdmissionSubstate.DISPATCHED,
)
_ADMISSION_SUBSTATE_RANK: dict[AdmissionSubstate, int] = {
    s: i for i, s in enumerate(_ADMISSION_SUBSTATE_ORDER)
}

TERMINAL_TOOL_CALL_STATES: frozenset[ToolCallState] = frozenset(
    {
        ToolCallState.COMPLETED,
        ToolCallState.FAILED,
        ToolCallState.CANCELLED,
        ToolCallState.REJECTED,
        ToolCallState.UNKNOWN,
    }
)

# Transiciones legales del nivel superior. El diagrama de §1.6 dibuja explícitamente
# requested->rejected y started->{completed,failed,cancelled,suspended,orphaned},
# admitted->started, suspended->started y orphaned->{completed,failed,unknown}. Dos bordes se
# añaden por encima del dibujo literal, con su propia justificación citada porque NO son una
# lectura directa del ASCII:
#   - `ADMITTED -> REJECTED`: el ciclo de vida de admisión (§5.2, línea 3503) dice
#     literalmente "rejected # terminal desde admitted / validated / authorized" — una llamada
#     puede fallar validación o autorización DENTRO de `admitted`, antes de llegar a
#     `dispatched`. El diagrama de §1.6 es una simplificación que omite este borde; el texto
#     de §5.2, que describe el mismo mecanismo ya fusionado en subestados, no.
#   - `REQUESTED -> CANCELLED` y `ADMITTED -> CANCELLED`: el diagrama de §1.6 solo dibuja
#     `cancelled` colgando de `started`, pero la invariante 8 del proyecto ("todo es
#     cancelable, reanudable y con presupuesto") no es negociable ni admite excepción para una
#     llamada que todavía no despachó. Negar la cancelación de una llamada en cola violaría esa
#     invariante, así que se incluye aquí explícitamente como lectura de la invariante, no del
#     diagrama.
#   - `SUSPENDED -> CANCELLED`: mismo argumento que el anterior — una llamada suspendida con
#     checkpoint ya escrito es, con más razón, cancelable.
LEGAL_TOOL_CALL_TRANSITIONS: frozenset[tuple[ToolCallState, ToolCallState]] = frozenset(
    {
        (ToolCallState.REQUESTED, ToolCallState.ADMITTED),
        (ToolCallState.REQUESTED, ToolCallState.REJECTED),
        (ToolCallState.REQUESTED, ToolCallState.CANCELLED),
        (ToolCallState.ADMITTED, ToolCallState.STARTED),
        (ToolCallState.ADMITTED, ToolCallState.REJECTED),
        (ToolCallState.ADMITTED, ToolCallState.CANCELLED),
        (ToolCallState.STARTED, ToolCallState.COMPLETED),
        (ToolCallState.STARTED, ToolCallState.FAILED),
        (ToolCallState.STARTED, ToolCallState.CANCELLED),
        (ToolCallState.STARTED, ToolCallState.SUSPENDED),
        (ToolCallState.STARTED, ToolCallState.ORPHANED),
        (ToolCallState.SUSPENDED, ToolCallState.STARTED),
        (ToolCallState.SUSPENDED, ToolCallState.CANCELLED),
        (ToolCallState.ORPHANED, ToolCallState.COMPLETED),
        (ToolCallState.ORPHANED, ToolCallState.FAILED),
        (ToolCallState.ORPHANED, ToolCallState.UNKNOWN),
    }
)


def validate_tool_call_transition(current: ToolCallState, next_: ToolCallState) -> None:
    """Guardia de la máquina de estados. §1.6, línea 1588: "cualquier otra transición es un bug
    del kernel y produce `AssertionError`" — literal: esto no es una regla de negocio que se
    pueda rechazar con gracia, es un invariante de programa."""
    if (current, next_) not in LEGAL_TOOL_CALL_TRANSITIONS:
        raise AssertionError(
            f"transición ilegal de llamada a herramienta: {current.value} -> {next_.value}"
        )


def validate_admission_substate_transition(
    current: AdmissionSubstate | None, next_: AdmissionSubstate
) -> None:
    """Guardia de progreso DENTRO de `admitted`. `APPROVAL_PENDING` es opcional (§5.2 lo marca
    entre corchetes: `[approval_pending]`) así que `AUTHORIZED -> QUEUED` sin pasar por ahí es
    legal; lo que nunca es legal es retroceder o saltar `QUEUED`/`DISPATCHED`."""
    if current is None:
        if next_ is not AdmissionSubstate.VALIDATED:
            raise AssertionError(
                f"una llamada recién admitida debe entrar por 'validated', no {next_.value!r}"
            )
        return
    rango_actual = _ADMISSION_SUBSTATE_RANK[current]
    rango_siguiente = _ADMISSION_SUBSTATE_RANK[next_]
    permitido = rango_siguiente == rango_actual + 1 or (
        next_ is AdmissionSubstate.QUEUED and current is AdmissionSubstate.AUTHORIZED
    )
    if not permitido:
        raise AssertionError(
            f"transición ilegal de subestado de admisión: {current.value} -> {next_.value}"
        )


# --------------------------------------------------------------------------------------- #
# 4b. Estado de dominio del kernel — se define aquí (y no junto a `reduce`, más abajo) porque
#     `Decision` (sección 9) necesita conocer `KernelState` en su anotación de tipo, y con
#     `from __future__ import annotations` Pydantic igual resuelve las anotaciones contra el
#     espacio de nombres del módulo en el momento en que se construye cada clase.
# --------------------------------------------------------------------------------------- #


class SessionRecord(BaseModel, frozen=True):
    session_id: str
    created_at_us: int


class ToolCallRecord(BaseModel, frozen=True):
    """Proyección en memoria de una llamada a herramienta dentro de `KernelState`. `admission`
    solo tiene valor mientras `state is ToolCallState.ADMITTED`; en cualquier otro estado debe
    ser `None` — lo impone `_validar`, no una convención documental."""

    call_id: str
    tool_id: str
    state: ToolCallState
    admission: AdmissionSubstate | None = None
    attempt: int = 1
    effect_class: EffectClass
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def _validar(self) -> ToolCallRecord:
        tiene_admission = self.admission is not None
        esta_admitida = self.state is ToolCallState.ADMITTED
        if tiene_admission != esta_admitida:
            raise ValueError("'admission' solo puede tener valor mientras state == ADMITTED")
        return self


class KernelState(BaseModel, frozen=True):
    """Estado de dominio que `reduce` transforma. Deliberadamente NO lleva `seq`/`hash`/
    `prev_hash` — esos son del `Journal` (host), no del reducer puro: el reducer razona sobre
    "qué existe", no sobre "en qué posición del log quedó escrito" (§1.1, tabla de negativas:
    "clasificar qué eventos necesitan `fsync`" tampoco es del kernel, por la misma razón)."""

    sessions: dict[str, SessionRecord] = Field(default_factory=dict)
    tool_calls: dict[str, ToolCallRecord] = Field(default_factory=dict)


# --------------------------------------------------------------------------------------- #
# 5. Dos claves, dos nombres — resolución §13.1 fila 2
# --------------------------------------------------------------------------------------- #


class CacheKey(BaseModel, frozen=True):
    """Clave CENTRADA EN CONTENIDO — resolución §13.1 fila 2: "para cachear resultados de
    herramientas puras". Dos invocaciones con el mismo `CacheKey` son intercambiables: servir
    el resultado de una en lugar de ejecutar la otra es correcto POR DEFINICIÓN, porque la
    clave ya captura todo lo que podría hacer variar el resultado. Solo aplica a herramientas
    `pure`/`idempotent` con `scope_keys` declarado (§4.1.1, línea 3103): nunca a
    `at_most_once` ni a `sequenced`.
    """

    value: str

    @classmethod
    def derive(
        cls, tool_id: str, args_digest: str, scope_keys_resolved: tuple[str, ...]
    ) -> CacheKey:
        """`scope_keys_resolved` ya viene resuelto por el kernel contra el contenido real (por
        ejemplo `ws_content:{path}` → hash de esa ruta en esa rama, §4.1.1) — nunca contra
        `workspace.head`, que invalidaría la caché en cuanto CUALQUIER agente tocara CUALQUIER
        archivo ajeno."""
        payload = {
            "tool_id": tool_id,
            "args_digest": args_digest,
            "scope": list(scope_keys_resolved),
        }
        return cls(value=_blake2b_hex(_canonical_json(payload)))


class IdempotencyKey(BaseModel, frozen=True):
    """Clave CENTRADA EN EFECTO — resolución §13.1 fila 2: "para no ejecutar dos veces un
    despliegue". Dos invocaciones con la misma `IdempotencyKey` NO son intercambiables — puede
    que la segunda deba fallar en seco en vez de reejecutarse. Lo que la clave garantiza es
    "esto no se ejecuta una segunda vez", nunca "sirve el resultado de la primera sin mirar".

    Es la semántica EXACTAMENTE OPUESTA a `CacheKey` bajo el mismo nombre que tenían los cuatro
    bloques originales, y es la raíz de la contradicción que resuelve la fila 2 de §13.1.
    """

    value: str

    @classmethod
    def derive(
        cls, *, agent_id: str, turn_seq: int, tool_call_id: str, args_digest: str
    ) -> IdempotencyKey:
        """§5.3, línea 4398: `idempotency_key = H(agent_id, turn_seq, tool_call_id,
        args_hash)`. Deliberadamente NO incluye `attempt`: reintentar la MISMA invocación debe
        dar la MISMA clave, o la deduplicación no deduplica nada."""
        payload = {
            "agent_id": agent_id,
            "turn_seq": turn_seq,
            "tool_call_id": tool_call_id,
            "args_digest": args_digest,
        }
        return cls(value=_blake2b_hex(_canonical_json(payload)))


# --------------------------------------------------------------------------------------- #
# 6. `BudgetAuthority` — resolución §13.1 fila 3
# --------------------------------------------------------------------------------------- #


class Budget(BaseModel, frozen=True):
    """Un presupuesto, en unidades enteras — nunca `float` (§1.2, línea 1433: "Sin floats en
    ninguna parte del sistema [...] el dinero va en `micro_usd: int`"). `usd_micros` es
    1e-6 USD, así que 1 USD exacto es `1_000_000`, sin redondeo posible en el camino caliente.
    """

    usd_micros: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    wall_ms: int = Field(default=0, ge=0)
    cpu_ms: int = Field(default=0, ge=0)
    bytes_out: int = Field(default=0, ge=0)


BudgetScopeKind = Literal["tenant", "session", "agent", "invocation", "workspace"]


class BudgetScope(BaseModel, frozen=True):
    """Cadena `tenant -> session -> agent -> invocation` de §5.5, línea 3816: "un hold consume
    del padre". `kind` identifica el eslabón; `id` es el identificador de ESE eslabón."""

    kind: BudgetScopeKind
    id: str


class Hold(BaseModel, frozen=True):
    """Reserva de presupuesto viva — §5.5. Un `Hold` huérfano (su invocación murió sin
    liquidar) lo libera el reaper, nunca el propio agente (§5.5, línea 3817)."""

    hold_id: str
    scope: BudgetScope
    amount: Budget


class BudgetAuthority(Protocol):
    """Único punto de serialización de presupuesto — resolución §13.1 fila 3: "Gana el
    `BudgetAuthority` del bloque 7: un punto de serialización POR WORKSPACE, arriendos de N USD
    consumidos localmente. Los demás lo consumen, no lo reimplementan". Es, junto al journal
    por stream (§13.3), el único mecanismo del sistema al que se le permite linealizar de
    verdad — cualquier otro que quiera hacerlo tiene que justificarlo por escrito (§13.3).

    Protocolo únicamente: la implementación (un Durable Object por workspace, o un lock de
    Postgres) es del bloque 7 y no vive en un paquete que no puede llamar a la red.
    """

    async def hold(self, scope: BudgetScope, amount: Budget) -> Hold: ...
    async def settle(self, hold_id: str, actual: Budget) -> None: ...
    async def release(self, hold_id: str, reason: str) -> None: ...


# --------------------------------------------------------------------------------------- #
# 7. Namespaces de recurso y de efecto, y los campos huérfanos de §13.2
# --------------------------------------------------------------------------------------- #


class ResourceClass(StrEnum):
    """§5.1, líneas 3426-3433 — campo obligatorio de manifiesto, no heurística del kernel."""

    IO_LIGHT = "io_light"
    IO_HEAVY = "io_heavy"
    CPU_HEAVY = "cpu_heavy"
    NET = "net"
    MODEL = "model"
    EXTERNAL_EFFECT = "external_effect"


_EFFECT_TARGET_RE = re.compile(r"^[a-z][a-z0-9_]*(:[A-Za-z0-9_./-]+)+$")


def is_valid_effect_target(value: str) -> bool:
    """Namespace de `effect_target` — huérfano asignado en §13.2: "Nadie producía [...] el
    espacio de nombres de `effect_target`". §5.3, línea 3666: "identificador ESTABLE y GLOBAL
    por tenant (`deploy:prod:api`, `git:github.com/org/repo:refs/heads/main`)". La forma es
    `<dominio>:<segmento>(:<segmento>)*`: un dominio en minúsculas seguido de al menos un
    segmento libre. Es la unidad linealizable del `EffectLedger` (§5.3, línea 3663: "no el
    workspace") — dos agentes en dos workspaces distintos apuntando al mismo `effect_target`
    SÍ se serializan entre sí.
    """
    return bool(_EFFECT_TARGET_RE.match(value))


MayInvoke = frozenset[str] | Literal["ANY"]
"""§5.6, línea 3831: "`may_invoke` es campo obligatorio del manifiesto para cualquier
herramienta que pueda invocar otras [...] El valor `ANY` fuerza la clase máxima de todo el
conjunto de capacidades." Un conjunto vacío (`frozenset()`) significa "no puede invocar nada",
que es el default seguro."""


class AuthzFacts(BaseModel, frozen=True):
    """Proyección acotada de los argumentos para autorizar sin tocar CAS — §5.1, líneas
    3443-3453. Huérfano asignado en §13.2 al descriptor, antes de congelar el ABI. Es también
    la EXCEPCIÓN nombrada a la regla de payload inline "solo estructural" (§13.1, última fila:
    "gana el bloque 1, con una excepción acotada y nombrada"): esto SÍ puede ir inline porque
    ya es, por construcción, una proyección de <=4 KiB de identificadores (rutas, hosts,
    binarios), nunca el contenido de un argumento.
    """

    paths_read: tuple[str, ...] = ()
    paths_write: tuple[str, ...] = ()
    hosts: tuple[tuple[str, int, str], ...] = ()
    effect_targets: tuple[str, ...] = ()
    secret_refs: tuple[str, ...] = ()
    exec_binaries: tuple[str, ...] = ()
    overflow: bool = False
    """`True` es DENEGACIÓN, no advertencia (línea 3456): una superficie de autorización que no
    cabe en el presupuesto se parte o se rechaza, nunca se trunca en silencio."""

    @field_validator("effect_targets")
    @classmethod
    def _validar_effect_targets(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        invalidos = [t for t in v if not is_valid_effect_target(t)]
        if invalidos:
            raise ValueError(f"effect_targets con namespace inválido: {invalidos!r}")
        return v

    def size_bytes(self) -> int:
        """El tamaño real que ocuparía inline — para decidir `overflow` antes de construir."""
        return len(_canonical_json(self.model_dump(mode="json")))

    def fits_budget(self, max_bytes: int = 4096) -> bool:
        return self.size_bytes() <= max_bytes


class RenderHint(BaseModel, frozen=True):
    """§8.4, línea 5747: "Cada plugin declara en su manifiesto un `render_hint`". Es lo que le
    permite a la UI no conocer herramientas por nombre (invariante 10): una herramienta
    desconocida cae al layout `kv` genérico, nunca a una excepción."""

    layout: Literal["diff", "table", "log", "image", "kv"]
    fields: tuple[str, ...] = ()
    summary_template: str | None = None


PlanStepId = str
"""§8.6, línea 5844: el `intent_id` de agrupación de diffs "ES el `plan_step_id` vigente en la
máquina de estados del Agent Runtime, adjuntado automáticamente por la herramienta de escritura
[...] Cero dependencia de fiabilidad del modelo." Es un `str` (ULID) y no un tipo propio a
propósito: lo produce el Agent Runtime, este módulo solo fija dónde vive el campo."""


class ToolResult(BaseModel, frozen=True):
    """Vista mínima y estable de un resultado de invocación — corresponde al `Outcome` del
    Tool ABI (§4.1.1) más los campos huérfanos que §13.2 le asigna "al descriptor, antes de
    congelar el ABI": `score` y `plan_step_id`. No sustituye a `Outcome` (que lleva contenido
    multimodal completo, `usage`, `effects` declarados, etc. — propiedad del bloque 4); es el
    subconjunto que el Agent Runtime y la UI pueden consumir sin importar el ABI entero.
    """

    status: Literal[
        "ok", "business_error", "transient_error", "policy_denied", "internal_fault", "canceled"
    ]
    score: int | None = None
    """§6.3, línea 4316-4320: "menor es mejor", lo rellena el PLUGIN de la herramienta (el de
    pytest sabe contar fallos de pytest). El runtime nunca parsea texto de herramientas — eso
    sería lógica de dominio en el kernel, invariante 6. Sin `score`, la señal de progreso (b)
    no existe."""
    plan_step_id: PlanStepId | None = None
    effect_class: EffectClass


class ExecWindow(Protocol):
    """Ventana de ejecución ya abierta — resolución §13.1, penúltima fila: "El `SandboxSpec`
    deja de recibir una ruta y recibe una `ExecWindow` ya abierta. `spawn()` falla si no hay
    ventana." Solo el TIPO se fija aquí; la implementación (materialización exclusiva y con
    write-through sobre un worktree, §2.3) es de otro bloque — así lo pide el encargo de este
    paquete explícitamente.
    """

    worktree: str
    root: str
    writable: frozenset[str]
    base: str

    async def close(self) -> Any: ...


class SelectionReason(BaseModel, frozen=True):
    """Lo que el motor de contexto EMITE y el inspector CONSUME — resolución §13.1, última
    fila de la tabla de contradicciones: "El bloque 3 emite lo que el bloque 8 consume:
    `SelectionReason` tipado y `DropRecord` con el coste de lo descartado". §8.5, líneas
    5792-5795.
    """

    strategy: Literal[
        "pinned",
        "explicit_mention",
        "retrieval_topk",
        "recency",
        "tool_schema_required",
        "policy_injected",
    ]
    score: float | None = None
    rank: int | None = None
    rule_id: str | None = None


class DropRecord(BaseModel, frozen=True):
    """El coste de lo descartado del contexto — contraparte de `SelectionReason`. Modelado
    sobre `EvictedSegment` (§8.5, línea 5797-5799): la pregunta que este tipo responde es "por
    qué NO vio el modelo el archivo X", que el propio documento (línea 5831) marca como la
    pregunta que casi siempre importa más que la de qué sí vio.
    """

    candidate_ref: str
    kind: str
    tokens_would_cost: int
    reason: Literal["budget_exceeded", "lower_score", "stale", "policy_denied"]


class ModelIdentity(BaseModel, frozen=True):
    """Identifica un modelo medido SIN redefinir `ModelCard` — ver la nota de "no-desviación"
    en el docstring del módulo. Cualquier contrato de este paquete que necesite señalar qué
    modelo se usó lo hace con esta tripleta; para los números medidos (contexto útil,
    fiabilidad de tool-calling, latencia...) el consumidor va a la `ModelCard` real en
    `edecan_forge_probe.modelcard`.
    """

    proveedor: str
    modelo: str
    revision_sonda: str


# --------------------------------------------------------------------------------------- #
# 8. Regla de payload inline "solo estructural" — resolución §13.1, penúltima fila (bloque 1)
# --------------------------------------------------------------------------------------- #

MAX_PAYLOAD_INLINE_BYTES = 4096
"""§1.2, línea 1430: "inline <= 4 KiB"."""

MAX_EVENT_SERIALIZED_BYTES = 8192
"""§1.2, línea 1430: "Registro serializado <= 8 KiB"."""

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")
"""Un identificador/enum/ruta relativa no lleva espacios, saltos de línea ni comillas. El texto
libre sí. Esta es la heurística ESTRUCTURAL que separa ambos casos: no es un analizador de
lenguaje natural, es "¿podría ser un token de un lenguaje de identificadores?"."""


class PayloadInlineError(ValueError):
    """Se levanta cuando un `payload_inline` viola la regla de §1.2, línea 1431: "un
    `payload_inline` solo puede contener identificadores, enumerados, números y booleanos.
    Ningún texto libre, ninguna ruta absoluta del usuario, ningún fragmento de código, ningún
    argumento de herramienta"."""


def is_structural_value(value: Any) -> bool:
    """Recorre un valor y decide si es puramente estructural (identificador/enum/número/bool),
    recursivamente sobre listas y dicts. `float` está prohibido a propósito (§1.2, línea 1433:
    "Sin floats en ninguna parte del sistema"), no solo `NaN`/`Infinity`."""
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, float):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        return bool(_IDENTIFIER_RE.match(value))
    if isinstance(value, list | tuple):
        return all(is_structural_value(v) for v in value)
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and bool(_IDENTIFIER_RE.match(k)) and is_structural_value(v)
            for k, v in value.items()
        )
    return False


def validate_payload_inline(payload: dict[str, Any] | None) -> None:
    """Punto único de validación de la regla dura de §1.2. La usan `EventDraft` y `Event` como
    validador de Pydantic, y cualquier productor externo puede llamarla antes de intentar
    construir un evento."""
    if payload is None:
        return
    if not is_structural_value(payload):
        raise PayloadInlineError(
            "payload_inline contiene texto libre, ruta de usuario, float u otro dato no "
            "estructural — eso va al CAS por payload_ref (§1.2)"
        )
    tamano = len(_canonical_json(payload))
    if tamano > MAX_PAYLOAD_INLINE_BYTES:
        raise PayloadInlineError(
            f"payload_inline ocupa {tamano} B, excede el máximo de "
            f"{MAX_PAYLOAD_INLINE_BYTES} B (§1.2)"
        )


# --------------------------------------------------------------------------------------- #
# 9. El núcleo del kernel — `Event`, `Command`, `Stamp`, `Decision`, `reduce`
# --------------------------------------------------------------------------------------- #


class Actor(BaseModel, frozen=True):
    """§1.2, línea 1378-1381. `capability_id` es `None` únicamente cuando `kind == "kernel"`."""

    kind: Literal["human", "agent", "kernel", "plugin", "provider", "scheduler"]
    id: str
    capability_id: str | None = None

    @model_validator(mode="after")
    def _capability_solo_ausente_en_kernel(self) -> Actor:
        if self.capability_id is None and self.kind != "kernel":
            raise ValueError("capability_id solo puede ser None cuando kind == 'kernel'")
        return self


class Stamp(BaseModel, frozen=True):
    """Todo lo NO determinista, precargado por el host antes de entrar al kernel — §1.1, líneas
    1314-1319. El reducer nunca lee el reloj, nunca tira dados: todo lo que necesita para ser
    determinista viaja aquí.
    """

    ts_physical: int = Field(ge=0)
    """epoch µs, leído por el host."""
    id_seed: bytes
    """16 bytes; el kernel deriva ULIDs deterministas de aquí (`derive_ulid`)."""
    lease_epoch: int = Field(ge=0)
    """Fencing token monotónico del lease vigente."""
    observed_lamport: int = Field(ge=0)
    """Máximo lamport visto por el host en mensajes entrantes — piggyback para causalidad
    cross-journal (§1.2, línea 1436)."""

    @field_validator("id_seed")
    @classmethod
    def _validar_id_seed(cls, v: bytes) -> bytes:
        if len(v) != 16:
            raise ValueError(f"id_seed debe tener 16 bytes, tiene {len(v)}")
        return v


class Command(BaseModel, frozen=True):
    """§1.1, líneas 1321-1328, con `stream_id` de primera clase añadido por la resolución
    §13.1 (bloque 1 vs. 2 y 6): "journal = colección de streams [...] `stream_id` de primera
    clase". El kernel NO valida `args` contra un esquema — eso es del adaptador de plugin,
    antes de que el comando llegue aquí (tabla de negativas, §1.1, línea 1362).
    """

    kind: str
    stream_id: str
    actor: Actor
    stamp: Stamp
    correlation_id: str
    causation_id: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    deadline_us: int | None = None


class Effect(BaseModel, frozen=True):
    """Petición de efecto que el reducer devuelve y el HOST ejecuta — §1.1, líneas 1330-1338.
    El kernel nunca llama a nadie: emite esto y termina."""

    id: str
    kind: Literal[
        "provider.complete",
        "tool.invoke",
        "cas.put",
        "cas.get",
        "timer.set",
        "timer.cancel",
        "stream.open",
        "stream.close",
        "proc.signal",
    ]
    capability_id: str
    args: dict[str, Any] = Field(default_factory=dict)
    payload_ref: CasRef | None = None
    deadline_us: int
    idempotency_key: str | None = None


class EventDraft(BaseModel):
    """Lo que el reducer produce en `Decision.events` — §1.1, línea 1342. Le faltan los campos
    que solo la escritura durable puede fijar de verdad bajo contención entre hosts (`seq`
    final, `hash`, `prev_hash`): eso es competencia de `Journal.append`, no del reducer puro.
    Lleva ya `v`/`cls`/`durability` resueltos contra el `SchemaRegistry` (ver `emit`) y ya pasa
    la regla de payload inline — construir uno con un tipo no registrado o un payload con texto
    libre falla en el constructor, no en tiempo de escritura.
    """

    model_config = ConfigDict(frozen=True)

    stream_id: str
    v: int
    type: str
    cls: Literal["control", "fact", "observation"]
    actor: Actor
    correlation_id: str
    causation_id: str | None = None
    payload_inline: dict[str, Any] | None = None
    payload_ref: CasRef | None = None
    durability: Literal["grouped", "strict"] = "grouped"

    @model_validator(mode="after")
    def _validar(self) -> EventDraft:
        if (self.payload_inline is None) == (self.payload_ref is None):
            raise ValueError("payload_inline y payload_ref son XOR (§1.2, línea 1430)")
        validate_payload_inline(self.payload_inline)
        descriptor = SCHEMA_REGISTRY.require_active(self.type)
        if descriptor.v != self.v:
            raise ValueError(
                f"versión {self.v} no coincide con la registrada "
                f"({descriptor.v}) para {self.type!r}"
            )
        if descriptor.cls != self.cls:
            raise ValueError(
                f"cls {self.cls!r} no coincide con la registrada "
                f"({descriptor.cls!r}) para {self.type!r}"
            )
        if descriptor.requires_strict and self.durability != "strict":
            raise ValueError(f"{self.type!r} exige durability='strict' (lint del registro, §1.1)")
        return self

    def canonical_bytes(self) -> bytes:
        """Forma canónica para comparar determinismo byte a byte entre dos ejecuciones."""
        return _canonical_json(self.model_dump(mode="json", exclude_none=False))


class Event(BaseModel, frozen=True):
    """La forma DURABLE de un evento, ya con su lugar en la cadena — §1.2, líneas 1389-1406,
    con `stream_id` añadido (resolución §13.1: bloque 1 vs. 2 y 6). Un journal es una colección
    de streams: `seq` es contiguo dentro de `(journal_id, stream_id)`, no dentro del journal
    entero.
    """

    v: int
    id: str
    stream_id: str
    seq: int = Field(ge=1)
    lamport: int = Field(ge=0)
    ts_physical: int = Field(ge=0)
    type: str
    cls: Literal["control", "fact", "observation"]
    actor: Actor
    correlation_id: str
    causation_id: str | None = None
    lease_epoch: int = Field(ge=0)
    durability: Literal["grouped", "strict"]
    payload_inline: dict[str, Any] | None = None
    payload_ref: CasRef | None = None
    prev_hash: str
    hash: str

    @model_validator(mode="after")
    def _validar(self) -> Event:
        if (self.payload_inline is None) == (self.payload_ref is None):
            raise ValueError("payload_inline y payload_ref son XOR (§1.2, línea 1430)")
        validate_payload_inline(self.payload_inline)
        return self


def next_lamport(local: int, observed_lamport: int) -> int:
    """§1.2, línea 1436: "`lamport` es un contador local al journal, y al construir un evento
    vale `max(state.lamport, cmd.stamp.observed_lamport) + 1`"."""
    return max(local, observed_lamport) + 1


def derive_ulid(*, ts_physical_us: int, id_seed: bytes, seq: int) -> str:
    """ULID determinista — §1.2, línea 1351: "`event.id` es determinista [...] Dos ejecuciones
    del mismo comando con el mismo `Stamp` producen los mismos identificadores. Sin esto el
    replay produce un journal con hashes distintos y la comparación es imposible."

    El documento deriva la entropía con BLAKE3; aquí, por la desviación #1 del docstring del
    módulo, con `blake2b`. El resto del algoritmo (tiempo de 48 bits en ms + 80 bits de
    entropía, Crockford base32, 26 caracteres) es el estándar ULID, sin variación.
    """
    if len(id_seed) != 16:
        raise ValueError(f"id_seed debe tener 16 bytes, tiene {len(id_seed)}")
    ts_ms = (ts_physical_us // 1000) & 0xFFFFFFFFFFFF  # 48 bits
    entropia = hashlib.blake2b(id_seed + seq.to_bytes(8, "big"), digest_size=10).digest()  # 80 bits
    valor = (ts_ms << 80) | int.from_bytes(entropia, "big")
    crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    caracteres = []
    for i in range(26):
        desplazamiento = 130 - 5 * (i + 1)
        indice = (valor >> desplazamiento) & 0x1F
        caracteres.append(crockford[indice])
    return "".join(caracteres)


class Rejection(BaseModel, frozen=True):
    """`Decision.rejection` — un comando inadmisible produce esto, NUNCA un `raise` de control
    de flujo (§1.1, línea 1311). `raise` en el reducer se reserva para corrupción real del
    `state` o violación de un invariante de programa (como una transición ilegal)."""

    code: str
    message: str


class Decision(BaseModel):
    """§1.1, líneas 1340-1345: el resultado TOTAL de `reduce`. `rejection` es XOR con
    `(events, effects)` no vacíos — o el comando produjo algo, o fue rechazado, nunca ambos."""

    model_config = ConfigDict(frozen=True)

    state: KernelState
    events: tuple[EventDraft, ...] = ()
    effects: tuple[Effect, ...] = ()
    rejection: Rejection | None = None

    @model_validator(mode="after")
    def _xor(self) -> Decision:
        hay_contenido = bool(self.events) or bool(self.effects)
        if self.rejection is not None and hay_contenido:
            raise ValueError("rejection es XOR con events/effects (§1.1, línea 1344)")
        return self


# --------------------------------------------------------------------------------------- #
# 10. `SchemaRegistry` — namespace de eventos por dominio, cerrado y verificado
# --------------------------------------------------------------------------------------- #


class TypeDescriptor(BaseModel, frozen=True):
    """§1.5, líneas 1526-1531."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    type: str
    v: int
    cls: Literal["control", "fact", "observation"]
    status: Literal["active", "reserved", "deprecated"]
    requires_strict: bool
    payload_model: type[BaseModel]


class SchemaRegistryError(ValueError):
    """Emitir un tipo no declarado, o un tipo `reserved`/`deprecated`, es "un error del
    productor" que "el adaptador rechaza" (§1.5, línea 1534). Aquí ese rechazo es esta
    excepción, levantada tan pronto como se intenta construir el evento — no al escribirlo."""


class SchemaRegistry:
    """Registro CERRADO de tipos de evento — §1.5. `seal()` impide registrar tipos nuevos en
    caliente después de la carga del módulo; es lo que hace que "un test que falla si alguien
    añade un tipo sin registrarlo" sea posible: cualquier `EventDraft`/`Event` construido con
    un `type` no presente en el registro falla en el constructor (ver `EventDraft._validar`).
    """

    def __init__(self) -> None:
        self._por_tipo: dict[str, TypeDescriptor] = {}
        self._sellado = False

    def register(self, descriptor: TypeDescriptor) -> None:
        if self._sellado:
            raise SchemaRegistryError(
                "el registro está sellado: no se pueden añadir tipos en caliente"
            )
        if descriptor.type in self._por_tipo:
            raise SchemaRegistryError(f"tipo duplicado: {descriptor.type!r}")
        self._por_tipo[descriptor.type] = descriptor

    def seal(self) -> None:
        self._sellado = True

    def descriptor(self, type_: str) -> TypeDescriptor:
        try:
            return self._por_tipo[type_]
        except KeyError:
            raise SchemaRegistryError(f"tipo de evento no declarado: {type_!r}") from None

    def is_active(self, type_: str) -> bool:
        d = self._por_tipo.get(type_)
        return d is not None and d.status == "active"

    def require_active(self, type_: str) -> TypeDescriptor:
        descriptor = self.descriptor(type_)
        if descriptor.status != "active":
            raise SchemaRegistryError(
                f"tipo {type_!r} no está activo (status={descriptor.status!r}); "
                "emitir un tipo reservado es un error del productor (§1.5, línea 1534)"
            )
        return descriptor

    def all_types(self) -> frozenset[str]:
        return frozenset(self._por_tipo)

    def active_types(self) -> frozenset[str]:
        return frozenset(t for t, d in self._por_tipo.items() if d.status == "active")


class _ReservedPayload(BaseModel, frozen=True):
    """Marcador para tipos `reserved` sin forma propia todavía — el documento pinnea el
    NAMESPACE completo (§1.5: "el espacio de nombres se pinnea entero [...] pero se distingue
    entre tipos activos y reservados") sin dar forma a los que no se activan en esta fase."""


# --- Payloads de los tipos ACTIVOS de este paquete ---------------------------------------- #


class SessionCreatedPayload(BaseModel, frozen=True):
    session_id: str


class ToolCallRequestedPayload(BaseModel, frozen=True):
    call_id: str
    tool_id: str
    attempt: int
    effect_class: EffectClass


class ToolCallAdmittedPayload(BaseModel, frozen=True):
    call_id: str
    admission: AdmissionSubstate


class ToolCallStartedPayload(BaseModel, frozen=True):
    call_id: str
    attempt: int


class ToolCallCompletedPayload(BaseModel, frozen=True):
    call_id: str
    score: int | None = None


class ToolCallFailedPayload(BaseModel, frozen=True):
    call_id: str
    error_code: str


class ToolCallCancelledPayload(BaseModel, frozen=True):
    call_id: str
    reason_code: str


class ToolCallRejectedPayload(BaseModel, frozen=True):
    call_id: str
    reason_code: str


class ToolCallSuspendedPayload(BaseModel, frozen=True):
    call_id: str
    checkpoint_ref: CasRef


class ToolCallOrphanedPayload(BaseModel, frozen=True):
    call_id: str
    idempotency_key: str | None = None


class ToolCallUnknownPayload(BaseModel, frozen=True):
    call_id: str
    evidence_ref: CasRef | None = None


# --- Namespace completo, §1.5 líneas 1536-1550, todo como reservado por defecto ----------- #

_NAMESPACE_RESERVADO: dict[str, tuple[str, ...]] = {
    "session": ("created", "resumed", "forked", "branched_from", "quiesced", "closed"),
    "agent": ("spawned", "role_assigned", "paused", "resumed", "delegated", "terminated"),
    "turn": (
        "started",
        "prompt_sealed",
        "completion_received",
        "malformed_output_observed",
        "stop_reason_observed",
        "cancelled",
        "failed",
    ),
    "tool": (
        "call_requested",
        "call_admitted",
        "call_rejected",
        "call_started",
        "call_suspended",
        "call_completed",
        "call_failed",
        "call_cancelled",
        "call_orphaned",
        "call_unknown",
    ),
    "fs": (
        "read_observed",
        "write_committed",
        "delete_committed",
        "rename_committed",
        "snapshot_taken",
        "conflict_detected",
    ),
    "proc": ("spawned", "output_sealed", "exited", "killed", "signaled"),
    "ctx": (
        "window_assembled",
        "item_admitted",
        "item_evicted",
        "compaction_applied",
        "blob_evicted",
        "blob_dangling",
    ),
    "plugin": (
        "discovered",
        "handshake_completed",
        "capabilities_granted",
        "manifest_changed",
        "quarantined",
        "unloaded",
    ),
    "provider": (
        "selected",
        "request_sent",
        "response_received",
        "error_observed",
        "capability_probed",
        "fallback_applied",
    ),
    "approval": ("requested", "policy_matched", "granted", "denied", "expired"),
    "budget": ("allocated", "charged", "warned", "exhausted", "released"),
    "workspace": (
        "created",
        "branched",
        "merge_requested",
        "merge_applied",
        "merge_rejected",
        "discarded",
    ),
    "kernel": (
        "started",
        "seal_written",
        "snapshot_written",
        "recovered",
        "schema_migrated",
        "event_redacted",
        "host_key_rotated",
        "clock_skew_observed",
        "draining",
        "stopped",
    ),
}
"""Espacio de nombres COMPLETO de §1.5, líneas 1536-1550 — 13 dominios, pinneado entero como
pide la invariante 9, todo registrado como `reserved` salvo lo que se activa explícitamente
abajo. Esto es exactamente lo que el documento pide: "no inventes 150 tipos [ampliar esto
después es añadir status='active' a una entrada YA declarada, nunca inventar un tipo nuevo]."""

# Tipos ACTIVOS de este paquete: los 10 de `tool.call_*` completos — porque la máquina de
# estados que este módulo implementa (§1.6, resolución §13.1 fila 1) es la máquina COMPLETA,
# no el subconjunto de 6 que §1.5 (línea 1552) lista como "activos en fase 1" para la primera
# rebanada de producto. Restringir el registro a esos 6 haría imposible el propio requisito de
# este encargo de probar transiciones legales E ILEGALES sobre rejected/cancelled/suspended/
# unknown. Se añade también `session.created`, mínimo para poder probar `reduce` con más de un
# tipo de comando.
_ACTIVOS: dict[str, tuple[Literal["control", "fact", "observation"], bool, type[BaseModel]]] = {
    "session.created": ("fact", True, SessionCreatedPayload),
    "tool.call_requested": ("fact", False, ToolCallRequestedPayload),
    "tool.call_admitted": ("fact", False, ToolCallAdmittedPayload),
    "tool.call_started": ("fact", True, ToolCallStartedPayload),
    "tool.call_completed": ("fact", True, ToolCallCompletedPayload),
    "tool.call_failed": ("fact", True, ToolCallFailedPayload),
    "tool.call_cancelled": ("fact", True, ToolCallCancelledPayload),
    "tool.call_rejected": ("fact", False, ToolCallRejectedPayload),
    "tool.call_suspended": ("fact", True, ToolCallSuspendedPayload),
    "tool.call_orphaned": ("fact", True, ToolCallOrphanedPayload),
    "tool.call_unknown": ("fact", True, ToolCallUnknownPayload),
}


def _construir_registro() -> SchemaRegistry:
    registro = SchemaRegistry()
    for dominio, verbos in _NAMESPACE_RESERVADO.items():
        for verbo in verbos:
            tipo = f"{dominio}.{verbo}"
            if tipo in _ACTIVOS:
                cls_, requires_strict, payload_model = _ACTIVOS[tipo]
                registro.register(
                    TypeDescriptor(
                        type=tipo,
                        v=1,
                        cls=cls_,
                        status="active",
                        requires_strict=requires_strict,
                        payload_model=payload_model,
                    )
                )
            else:
                registro.register(
                    TypeDescriptor(
                        type=tipo,
                        v=1,
                        cls="fact",
                        status="reserved",
                        requires_strict=False,
                        payload_model=_ReservedPayload,
                    )
                )
    registro.seal()
    return registro


SCHEMA_REGISTRY = _construir_registro()


def emit(
    type_: str,
    *,
    actor: Actor,
    stream_id: str,
    correlation_id: str,
    causation_id: str | None,
    payload: BaseModel,
) -> EventDraft:
    """Construye un `EventDraft` resolviendo `v`/`cls`/`durability` contra `SCHEMA_REGISTRY` en
    vez de que cada `handler` de `reduce` los repita a mano. `type_` no activo o no declarado
    revienta aquí, antes de llegar a `EventDraft`."""
    descriptor = SCHEMA_REGISTRY.require_active(type_)
    durability: Literal["grouped", "strict"] = "strict" if descriptor.requires_strict else "grouped"
    return EventDraft(
        stream_id=stream_id,
        v=descriptor.v,
        type=type_,
        cls=descriptor.cls,
        actor=actor,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload_inline=payload.model_dump(mode="json"),
        durability=durability,
    )


# --------------------------------------------------------------------------------------- #
# 11. `reduce` — la máquina de estados de llamada a herramienta en acción
# --------------------------------------------------------------------------------------- #


def _rechazar(state: KernelState, code: str, message: str) -> Decision:
    return Decision(state=state, rejection=Rejection(code=code, message=message))


def _requerir_call(state: KernelState, call_id: str) -> ToolCallRecord | None:
    return state.tool_calls.get(call_id)


def _con_tool_call(state: KernelState, registro: ToolCallRecord) -> KernelState:
    nuevos = dict(state.tool_calls)
    nuevos[registro.call_id] = registro
    return state.model_copy(update={"tool_calls": nuevos})


def _handle_session_create(state: KernelState, cmd: Command) -> Decision:
    session_id = cmd.args.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return _rechazar(
            state, "INVALID_ARGS", "session_id es obligatorio y debe ser texto no vacío"
        )
    if session_id in state.sessions:
        return _rechazar(state, "ALREADY_EXISTS", f"la sesión {session_id!r} ya existe")
    nuevas = dict(state.sessions)
    nuevas[session_id] = SessionRecord(session_id=session_id, created_at_us=cmd.stamp.ts_physical)
    nuevo_estado = state.model_copy(update={"sessions": nuevas})
    draft = emit(
        "session.created",
        actor=cmd.actor,
        stream_id=cmd.stream_id,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        payload=SessionCreatedPayload(session_id=session_id),
    )
    return Decision(state=nuevo_estado, events=(draft,))


def _handle_tool_call_request(state: KernelState, cmd: Command) -> Decision:
    call_id = cmd.args.get("call_id")
    tool_id = cmd.args.get("tool_id")
    effect_class = cmd.args.get("effect_class")
    if not isinstance(call_id, str) or not call_id:
        return _rechazar(state, "INVALID_ARGS", "call_id es obligatorio")
    if not isinstance(tool_id, str) or not tool_id:
        return _rechazar(state, "INVALID_ARGS", "tool_id es obligatorio")
    if not isinstance(effect_class, EffectClass):
        return _rechazar(
            state, "INVALID_ARGS", "effect_class es obligatorio y debe ser EffectClass"
        )
    if call_id in state.tool_calls:
        return _rechazar(state, "ALREADY_EXISTS", f"la llamada {call_id!r} ya existe")
    registro = ToolCallRecord(
        call_id=call_id, tool_id=tool_id, state=ToolCallState.REQUESTED, effect_class=effect_class
    )
    draft = emit(
        "tool.call_requested",
        actor=cmd.actor,
        stream_id=cmd.stream_id,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        payload=ToolCallRequestedPayload(
            call_id=call_id, tool_id=tool_id, attempt=registro.attempt, effect_class=effect_class
        ),
    )
    return Decision(state=_con_tool_call(state, registro), events=(draft,))


def _handle_tool_call_admit(state: KernelState, cmd: Command) -> Decision:
    call_id = cmd.args.get("call_id")
    registro = _requerir_call(state, call_id) if isinstance(call_id, str) else None
    if registro is None:
        return _rechazar(state, "NOT_FOUND", f"llamada no encontrada: {call_id!r}")
    validate_tool_call_transition(registro.state, ToolCallState.ADMITTED)
    validate_admission_substate_transition(None, AdmissionSubstate.VALIDATED)
    nuevo = registro.model_copy(
        update={"state": ToolCallState.ADMITTED, "admission": AdmissionSubstate.VALIDATED}
    )
    draft = emit(
        "tool.call_admitted",
        actor=cmd.actor,
        stream_id=cmd.stream_id,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        payload=ToolCallAdmittedPayload(
            call_id=nuevo.call_id, admission=AdmissionSubstate.VALIDATED
        ),
    )
    return Decision(state=_con_tool_call(state, nuevo), events=(draft,))


def _handle_tool_call_advance_admission(state: KernelState, cmd: Command) -> Decision:
    """Progreso de subestado DENTRO de `admitted` (validated -> authorized -> [approval_pending]
    -> queued -> dispatched). No emite evento: es la fase de admisión de §5.1 (p99 < 5 ms,
    "no hace ninguna I/O"), y journalizar cada micro-paso violaría ese presupuesto además de
    inflar el journal sin necesidad — solo la entrada y la salida de `admitted` son hechos
    durables (§1.6 diagrama)."""
    call_id = cmd.args.get("call_id")
    a = cmd.args.get("to")
    registro = _requerir_call(state, call_id) if isinstance(call_id, str) else None
    if registro is None:
        return _rechazar(state, "NOT_FOUND", f"llamada no encontrada: {call_id!r}")
    if registro.state is not ToolCallState.ADMITTED:
        return _rechazar(state, "INVALID_STATE", "solo se avanza el subestado mientras admitted")
    try:
        siguiente = AdmissionSubstate(a)
    except ValueError:
        return _rechazar(state, "INVALID_ARGS", f"subestado de admisión inválido: {a!r}")
    validate_admission_substate_transition(registro.admission, siguiente)
    nuevo = registro.model_copy(update={"admission": siguiente})
    return Decision(state=_con_tool_call(state, nuevo))


def _handle_tool_call_start(state: KernelState, cmd: Command) -> Decision:
    call_id = cmd.args.get("call_id")
    registro = _requerir_call(state, call_id) if isinstance(call_id, str) else None
    if registro is None:
        return _rechazar(state, "NOT_FOUND", f"llamada no encontrada: {call_id!r}")
    if registro.admission is not AdmissionSubstate.DISPATCHED:
        return _rechazar(state, "INVALID_STATE", "solo se despacha desde el subestado 'dispatched'")
    validate_tool_call_transition(registro.state, ToolCallState.STARTED)
    nuevo = registro.model_copy(update={"state": ToolCallState.STARTED, "admission": None})
    draft = emit(
        "tool.call_started",
        actor=cmd.actor,
        stream_id=cmd.stream_id,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        payload=ToolCallStartedPayload(call_id=nuevo.call_id, attempt=nuevo.attempt),
    )
    return Decision(state=_con_tool_call(state, nuevo), events=(draft,))


def _handle_tool_call_resume(state: KernelState, cmd: Command) -> Decision:
    call_id = cmd.args.get("call_id")
    registro = _requerir_call(state, call_id) if isinstance(call_id, str) else None
    if registro is None:
        return _rechazar(state, "NOT_FOUND", f"llamada no encontrada: {call_id!r}")
    validate_tool_call_transition(registro.state, ToolCallState.STARTED)
    nuevo = registro.model_copy(
        update={"state": ToolCallState.STARTED, "attempt": registro.attempt + 1}
    )
    draft = emit(
        "tool.call_started",
        actor=cmd.actor,
        stream_id=cmd.stream_id,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        payload=ToolCallStartedPayload(call_id=nuevo.call_id, attempt=nuevo.attempt),
    )
    return Decision(state=_con_tool_call(state, nuevo), events=(draft,))


def _handle_tool_call_suspend(state: KernelState, cmd: Command) -> Decision:
    call_id = cmd.args.get("call_id")
    checkpoint_ref_wire = cmd.args.get("checkpoint_ref")
    registro = _requerir_call(state, call_id) if isinstance(call_id, str) else None
    if registro is None:
        return _rechazar(state, "NOT_FOUND", f"llamada no encontrada: {call_id!r}")
    if not isinstance(checkpoint_ref_wire, str):
        return _rechazar(
            state, "INVALID_ARGS", "checkpoint_ref es obligatorio para suspender (§1.6)"
        )
    validate_tool_call_transition(registro.state, ToolCallState.SUSPENDED)
    nuevo = registro.model_copy(update={"state": ToolCallState.SUSPENDED})
    draft = emit(
        "tool.call_suspended",
        actor=cmd.actor,
        stream_id=cmd.stream_id,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        payload=ToolCallSuspendedPayload(
            call_id=nuevo.call_id, checkpoint_ref=CasRef.parse(checkpoint_ref_wire)
        ),
    )
    return Decision(state=_con_tool_call(state, nuevo), events=(draft,))


def _handle_tool_call_complete(state: KernelState, cmd: Command) -> Decision:
    call_id = cmd.args.get("call_id")
    score = cmd.args.get("score")
    registro = _requerir_call(state, call_id) if isinstance(call_id, str) else None
    if registro is None:
        return _rechazar(state, "NOT_FOUND", f"llamada no encontrada: {call_id!r}")
    validate_tool_call_transition(registro.state, ToolCallState.COMPLETED)
    nuevo = registro.model_copy(update={"state": ToolCallState.COMPLETED})
    draft = emit(
        "tool.call_completed",
        actor=cmd.actor,
        stream_id=cmd.stream_id,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        payload=ToolCallCompletedPayload(call_id=nuevo.call_id, score=score),
    )
    return Decision(state=_con_tool_call(state, nuevo), events=(draft,))


def _handle_tool_call_fail(state: KernelState, cmd: Command) -> Decision:
    call_id = cmd.args.get("call_id")
    error_code = cmd.args.get("error_code")
    registro = _requerir_call(state, call_id) if isinstance(call_id, str) else None
    if registro is None:
        return _rechazar(state, "NOT_FOUND", f"llamada no encontrada: {call_id!r}")
    if not isinstance(error_code, str) or not error_code:
        return _rechazar(state, "INVALID_ARGS", "error_code es obligatorio")
    validate_tool_call_transition(registro.state, ToolCallState.FAILED)
    nuevo = registro.model_copy(update={"state": ToolCallState.FAILED})
    draft = emit(
        "tool.call_failed",
        actor=cmd.actor,
        stream_id=cmd.stream_id,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        payload=ToolCallFailedPayload(call_id=nuevo.call_id, error_code=error_code),
    )
    return Decision(state=_con_tool_call(state, nuevo), events=(draft,))


def _handle_tool_call_cancel(state: KernelState, cmd: Command) -> Decision:
    call_id = cmd.args.get("call_id")
    reason_code = cmd.args.get("reason_code")
    registro = _requerir_call(state, call_id) if isinstance(call_id, str) else None
    if registro is None:
        return _rechazar(state, "NOT_FOUND", f"llamada no encontrada: {call_id!r}")
    if not isinstance(reason_code, str) or not reason_code:
        return _rechazar(state, "INVALID_ARGS", "reason_code es obligatorio")
    validate_tool_call_transition(registro.state, ToolCallState.CANCELLED)
    nuevo = registro.model_copy(update={"state": ToolCallState.CANCELLED, "admission": None})
    draft = emit(
        "tool.call_cancelled",
        actor=cmd.actor,
        stream_id=cmd.stream_id,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        payload=ToolCallCancelledPayload(call_id=nuevo.call_id, reason_code=reason_code),
    )
    return Decision(state=_con_tool_call(state, nuevo), events=(draft,))


def _handle_tool_call_reject(state: KernelState, cmd: Command) -> Decision:
    call_id = cmd.args.get("call_id")
    reason_code = cmd.args.get("reason_code")
    registro = _requerir_call(state, call_id) if isinstance(call_id, str) else None
    if registro is None:
        return _rechazar(state, "NOT_FOUND", f"llamada no encontrada: {call_id!r}")
    if not isinstance(reason_code, str) or not reason_code:
        return _rechazar(state, "INVALID_ARGS", "reason_code es obligatorio")
    validate_tool_call_transition(registro.state, ToolCallState.REJECTED)
    nuevo = registro.model_copy(update={"state": ToolCallState.REJECTED, "admission": None})
    draft = emit(
        "tool.call_rejected",
        actor=cmd.actor,
        stream_id=cmd.stream_id,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        payload=ToolCallRejectedPayload(call_id=nuevo.call_id, reason_code=reason_code),
    )
    return Decision(state=_con_tool_call(state, nuevo), events=(draft,))


def _handle_tool_call_orphan(state: KernelState, cmd: Command) -> Decision:
    """§1.6, línea 1590: al arrancar, toda `tool.call_started` sin terminal produce
    `tool.call_orphaned`. Es el HOST, no el agente, quien emite este comando durante la
    reconciliación de arranque."""
    call_id = cmd.args.get("call_id")
    registro = _requerir_call(state, call_id) if isinstance(call_id, str) else None
    if registro is None:
        return _rechazar(state, "NOT_FOUND", f"llamada no encontrada: {call_id!r}")
    validate_tool_call_transition(registro.state, ToolCallState.ORPHANED)
    nuevo = registro.model_copy(update={"state": ToolCallState.ORPHANED})
    draft = emit(
        "tool.call_orphaned",
        actor=cmd.actor,
        stream_id=cmd.stream_id,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        payload=ToolCallOrphanedPayload(
            call_id=nuevo.call_id, idempotency_key=nuevo.idempotency_key
        ),
    )
    return Decision(state=_con_tool_call(state, nuevo), events=(draft,))


_ORPHAN_OUTCOME_STATE: dict[str, ToolCallState] = {
    "completed": ToolCallState.COMPLETED,
    "failed": ToolCallState.FAILED,
    "unknown": ToolCallState.UNKNOWN,
}
_ORPHAN_OUTCOME_TYPE: dict[str, str] = {
    "completed": "tool.call_completed",
    "failed": "tool.call_failed",
    "unknown": "tool.call_unknown",
}


def _handle_tool_call_resolve_orphan(state: KernelState, cmd: Command) -> Decision:
    """§1.6: `orphaned -> completed | failed | unknown (terminal)`. §1.1, línea 1590: sin sonda
    de idempotencia soportada por el plugin (el caso normal en fase 1), el camino es
    `tool.call_unknown` + una aprobación de tipo `orphan_resolution` — esa aprobación es de otro
    bloque; aquí solo se fija la transición que ella dispara."""
    call_id = cmd.args.get("call_id")
    outcome = cmd.args.get("outcome")
    registro = _requerir_call(state, call_id) if isinstance(call_id, str) else None
    if registro is None:
        return _rechazar(state, "NOT_FOUND", f"llamada no encontrada: {call_id!r}")
    if outcome not in _ORPHAN_OUTCOME_STATE:
        return _rechazar(state, "INVALID_ARGS", f"outcome inválido: {outcome!r}")
    estado_destino = _ORPHAN_OUTCOME_STATE[outcome]
    validate_tool_call_transition(registro.state, estado_destino)
    nuevo = registro.model_copy(update={"state": estado_destino})
    if outcome == "completed":
        payload: BaseModel = ToolCallCompletedPayload(call_id=nuevo.call_id)
    elif outcome == "failed":
        payload = ToolCallFailedPayload(call_id=nuevo.call_id, error_code="ORPHAN_RESOLVED_FAILED")
    else:
        payload = ToolCallUnknownPayload(call_id=nuevo.call_id)
    draft = emit(
        _ORPHAN_OUTCOME_TYPE[outcome],
        actor=cmd.actor,
        stream_id=cmd.stream_id,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        payload=payload,
    )
    return Decision(state=_con_tool_call(state, nuevo), events=(draft,))


_HANDLERS: dict[str, Any] = {
    "session.create": _handle_session_create,
    "tool.call_request": _handle_tool_call_request,
    "tool.call_admit": _handle_tool_call_admit,
    "tool.call_advance_admission": _handle_tool_call_advance_admission,
    "tool.call_start": _handle_tool_call_start,
    "tool.call_resume": _handle_tool_call_resume,
    "tool.call_suspend": _handle_tool_call_suspend,
    "tool.call_complete": _handle_tool_call_complete,
    "tool.call_fail": _handle_tool_call_fail,
    "tool.call_cancel": _handle_tool_call_cancel,
    "tool.call_reject": _handle_tool_call_reject,
    "tool.call_orphan": _handle_tool_call_orphan,
    "tool.call_resolve_orphan": _handle_tool_call_resolve_orphan,
}


def reduce(state: KernelState, cmd: Command) -> Decision:
    """`reduce(state, cmd) -> Decision` — PURA, SÍNCRONA, TOTAL (§1.1, línea 1308).

    Nunca lanza para control de flujo: un comando inadmisible produce `Decision.rejection`. La
    única excepción son las `AssertionError` de `validate_tool_call_transition` /
    `validate_admission_substate_transition`, que el propio documento (§1.6, línea 1588) define
    como bug de programa, no como resultado de negocio — y las de `pydantic` cuando el llamador
    construye un `Command` con datos que ni siquiera tienen la forma correcta.

    Determinismo: mismo `state` + mismo `cmd` (incluido su `Stamp`) producen siempre la misma
    `Decision`, byte a byte en su forma canónica — no hay reloj, aleatoriedad ni I/O en el
    camino.
    """
    manejador = _HANDLERS.get(cmd.kind)
    if manejador is None:
        return _rechazar(state, "UNKNOWN_COMMAND", f"comando no reconocido: {cmd.kind!r}")
    return manejador(state, cmd)


# --------------------------------------------------------------------------------------- #
# 12. Journal y `EffectLedger` — solo el tipo; la implementación durable es de otro bloque
# --------------------------------------------------------------------------------------- #


class Guard(BaseModel, frozen=True):
    """Precondición de un `append_if` — resolución §13.1 (bloque 1 vs. 5): "El bloque 1 añade
    `append_if(...)` con guardia evaluada contra una proyección en la misma transacción. Sin
    esto, el bloque 5 es inconstruible sin violar la invariante 2." La guardia se expresa como
    "esta proyección, en esta clave, debe valer este `state_hash`" — evaluada por el HOST
    dentro de la misma transacción de escritura, nunca por el reducer puro.
    """

    projection_name: str
    key: str
    expected_state_hash: str


class AppendResult(BaseModel, frozen=True):
    accepted: bool
    from_seq: int | None = None
    to_seq: int | None = None
    reason: str | None = None


class Journal(Protocol):
    """§1.3. Solo `append`/`append_if` importan a este paquete; el resto del protocolo completo
    (`read`, `head`, `seal`, `verify`, `offload_prefix`, `redact`) es del host durable."""

    async def append(
        self, drafts: list[EventDraft], *, stream_id: str, expected_seq: int, lease_epoch: int
    ) -> AppendResult: ...

    async def append_if(
        self,
        drafts: list[EventDraft],
        *,
        stream_id: str,
        expected_seq: int,
        lease_epoch: int,
        guard: Guard,
    ) -> AppendResult: ...


class EffectLedger(Protocol):
    """§5.3, líneas 3639-3647 — protocolo de dos fases para idempotencia de efectos externos.
    `poison` es la única vía hacia `ToolCallState.UNKNOWN`/`indeterminate` (ya unificados, ver
    `ToolCallState`); `resolve` es la única salida (línea 3655: "no es un adorno")."""

    async def reserve(self, target: str, key: str, spec_digest: str, ttl_s: int) -> Any: ...
    async def commit(self, key: str, outcome_ref: CasRef) -> None: ...
    async def abandon(self, key: str, reason: str) -> None: ...
    async def poison(self, key: str, evidence_ref: CasRef) -> None: ...
    async def resolve(
        self, key: str, *, outcome: Literal["happened", "did_not_happen"], principal: str, note: str
    ) -> None: ...
