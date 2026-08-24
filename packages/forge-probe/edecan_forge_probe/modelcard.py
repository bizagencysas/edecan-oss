"""Contratos de la fase 0 de Forge: la `ModelCard` y sus mediciones.

Este módulo es el ÚNICO sitio donde se define qué significa "medir un modelo".
Todo lo demás de `edecan_forge_probe` —cada sonda, cada adaptador, el runner y
el informe— se escribe contra estas formas.

La razón de existir de la fase 0 está en `docs/arquitectura-forge.md` §15: entre
el 40 % y el 70 % del diseño de la fase 1 descansa sobre números que nadie ha
medido. Una `ModelCard` es el resultado de medirlos, con su intervalo de
confianza, y `evaluar_umbrales()` es el veredicto de si el diseño se sostiene.

Regla dura: **ningún campo de `ModelCard` se rellena a mano.** Un campo que no
se pudo medir vale `None` y arrastra `Veredicto.SIN_DATO`, nunca un valor
inventado ni un valor "de catálogo" copiado de la documentación del proveedor.
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

# --------------------------------------------------------------------------- #
# Perfiles de argumento
# --------------------------------------------------------------------------- #


class ArgProfile(StrEnum):
    """Forma de los argumentos de una llamada a herramienta.

    La fiabilidad del tool-calling NO es un número único: un modelo puede
    acertar el 99 % con `{"path": "a.py"}` y el 60 % cuando tiene que meter un
    bloque de código de 40 líneas dentro de un campo JSON. Forge depende
    críticamente del segundo caso (`apply_patch`), así que se mide por separado.
    """

    SCALAR = "scalar"
    """Argumentos planos: strings cortos, enteros, booleanos."""

    NESTED = "nested"
    """Objetos anidados o listas de objetos."""

    CODE_BLOB = "code_blob"
    """Un bloque de código multilínea dentro de un campo string. El caso duro."""

    LONG_STRING = "long_string"
    """Texto largo (>2 KiB) en un campo, sin estructura interna."""


class Capability(StrEnum):
    """Capacidades que la sonda intenta detectar, no asumir."""

    NATIVE_TOOLS = "native_tools"
    STRUCTURED_OUTPUT = "structured_output"
    PREFIX_CACHE = "prefix_cache"
    VISION = "vision"
    REASONING = "reasoning"
    STREAMING = "streaming"


class Veredicto(StrEnum):
    """Resultado de contrastar una medición con su umbral."""

    PASA = "pasa"
    JUSTO = "justo"
    """Dentro del umbral pero a menos de un 10 % de margen: tratar como riesgo."""
    FALLA = "falla"
    SIN_DATO = "sin_dato"
    """No se pudo medir. NUNCA se interpreta como que pasa."""


# --------------------------------------------------------------------------- #
# Medición con incertidumbre
# --------------------------------------------------------------------------- #


class Reliability(BaseModel):
    """Una tasa de éxito medida, con su intervalo de confianza.

    Se usa el intervalo de Wilson al 95 % y no la proporción simple: con 20
    intentos, 18 aciertos dan una media de 0,90 cuyo límite inferior real es
    0,70. Todo umbral del sistema se contrasta contra `lower_95`, jamás contra
    `mean` — el diseño tiene que sostenerse en el mal caso, no en el esperado.
    """

    successes: int = Field(ge=0)
    trials: int = Field(ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mean(self) -> float:
        """Proporción observada. Informativa; no se usa para decidir."""
        return self.successes / self.trials if self.trials else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def lower_95(self) -> float:
        """Límite inferior del intervalo de Wilson al 95 %.

        Es el número contra el que se decide. Con pocas muestras es
        deliberadamente pesimista: eso es lo que se quiere.
        """
        n = self.trials
        if n == 0:
            return 0.0
        z = 1.959963984540054
        p = self.successes / n
        denom = 1.0 + z * z / n
        centro = p + z * z / (2 * n)
        margen = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
        return max(0.0, (centro - margen) / denom)

    def __str__(self) -> str:  # pragma: no cover - presentación
        return f"{self.mean:.2f} (≥{self.lower_95:.2f} con 95 %, n={self.trials})"


class Latencia(BaseModel):
    """Percentiles de latencia observados, en segundos."""

    p50: float
    p95: float
    muestras: int = Field(ge=0)


# --------------------------------------------------------------------------- #
# Resultado de una sonda individual
# --------------------------------------------------------------------------- #


class ProbeResult(BaseModel):
    """Lo que devuelve una sonda. Autodescriptivo y serializable.

    `evidencia` guarda las rutas o identificadores de las trazas crudas para que
    cualquier número del informe se pueda auditar hasta la petición real. Una
    medición sin evidencia no entra en la `ModelCard`.
    """

    probe: str
    capability: Capability | None = None
    ok: bool
    """False = la sonda no pudo ejecutarse (error de red, modelo caído).
    Distinto de haber medido un resultado malo, que es `ok=True` con valor bajo."""

    valor: float | int | None = None
    reliability: Reliability | None = None
    latencia: Latencia | None = None
    detalle: dict[str, Any] = Field(default_factory=dict)
    evidencia: list[str] = Field(default_factory=list)
    error: str | None = None
    duracion_s: float = 0.0


# --------------------------------------------------------------------------- #
# Umbrales de decisión
# --------------------------------------------------------------------------- #


class Umbral(BaseModel):
    """Un criterio de sí/no de la fase 0.

    `minimo` y `maximo` son excluyentes entre sí: un umbral o es un suelo
    (contexto útil, fiabilidad, tokens por segundo) o es un techo (latencia).
    """

    clave: str
    descripcion: str
    minimo: float | None = None
    maximo: float | None = None
    bloqueante: bool = True
    """Si `False`, un fallo degrada el diseño pero no lo invalida."""

    def evaluar(self, valor: float | None) -> Veredicto:
        if valor is None:
            return Veredicto.SIN_DATO
        if self.minimo is not None:
            if valor < self.minimo:
                return Veredicto.FALLA
            return Veredicto.JUSTO if valor < self.minimo * 1.10 else Veredicto.PASA
        if self.maximo is not None:
            if valor > self.maximo:
                return Veredicto.FALLA
            return Veredicto.JUSTO if valor > self.maximo * 0.90 else Veredicto.PASA
        raise ValueError(f"umbral {self.clave!r} no declara ni mínimo ni máximo")


UMBRALES_FASE_0: tuple[Umbral, ...] = (
    Umbral(
        clave="usable_context_tokens",
        descripcion=(
            "Contexto ÚTIL: profundidad a la que el modelo aún recupera y razona "
            "sobre lo que se le metió. No es la ventana anunciada."
        ),
        minimo=48_000,
    ),
    Umbral(
        clave="native_tools.code_blob.lower_95",
        descripcion=(
            "Fiabilidad llamando a una herramienta cuyo argumento es un bloque de "
            "código. Es exactamente lo que hace `apply_patch` en cada edición."
        ),
        minimo=0.90,
    ),
    Umbral(
        clave="throughput_tps",
        descripcion="Tokens de salida por segundo en régimen sostenido.",
        minimo=25.0,
    ),
    Umbral(
        clave="ttft_p95_s",
        descripcion="Tiempo hasta el primer token, percentil 95.",
        maximo=2.5,
    ),
    Umbral(
        clave="bench_success_rate",
        descripcion=(
            "Tareas del banco público de Edecán resueltas con criterio "
            "ejecutable, sin ayuda humana."
        ),
        minimo=0.55,
    ),
    Umbral(
        clave="max_tools_effective",
        descripcion=(
            "Cuántas herramientas se pueden ofrecer antes de que la precisión de "
            "selección se derrumbe. Fija el techo de la superficie del ABI."
        ),
        minimo=12,
        bloqueante=False,
    ),
)


class EvaluacionUmbral(BaseModel):
    """Un umbral ya contrastado contra su medición."""

    umbral: Umbral
    valor: float | None
    veredicto: Veredicto

    @computed_field  # type: ignore[prop-decorator]
    @property
    def bloquea(self) -> bool:
        return self.umbral.bloqueante and self.veredicto in (
            Veredicto.FALLA,
            Veredicto.SIN_DATO,
        )


# --------------------------------------------------------------------------- #
# La ModelCard
# --------------------------------------------------------------------------- #


class ModelCard(BaseModel):
    """Lo que Forge sabe —medido— de un modelo concreto en un proveedor concreto.

    Es el objeto que consulta el resto del sistema en vez de preguntar
    `if provider == "workers-ai"`. Sustituye al alias de tres valores
    (`principal`/`rapido`/`profundo`) que hoy vive en `packages/agents` y en
    `edecan_llm.router`, que es acoplamiento a la topología de un proveedor.
    """

    modelo: str
    proveedor: str
    medido_en: datetime
    revision_sonda: str
    """Hash o versión del código de sonda que produjo estos números. Sin esto,
    dos ModelCards no son comparables."""

    ventana_anunciada: int | None = None
    """Lo que dice la documentación del proveedor. Se guarda solo para
    contrastarlo con `usable_context_tokens` y mostrar la diferencia."""

    usable_context_tokens: int | None = None
    native_tools: dict[ArgProfile, Reliability] = Field(default_factory=dict)
    structured_output: Reliability | None = None
    max_tools_effective: int | None = None
    max_schema_bytes: int | None = None
    throughput_tps: float | None = None
    ttft: Latencia | None = None
    prefix_cache: bool | None = None
    vision: bool | None = None
    bench_success: Reliability | None = None

    precio_entrada_usd_mtok: float | None = None
    precio_salida_usd_mtok: float | None = None

    resultados: list[ProbeResult] = Field(default_factory=list)
    notas: list[str] = Field(default_factory=list)

    def _lectura(self, clave: str) -> float | None:
        """Resuelve la clave de un umbral contra los campos medidos."""
        match clave:
            case "usable_context_tokens":
                if self.usable_context_tokens is None:
                    return None
                return float(self.usable_context_tokens)
            case "native_tools.code_blob.lower_95":
                r = self.native_tools.get(ArgProfile.CODE_BLOB)
                return None if r is None else r.lower_95
            case "throughput_tps":
                return self.throughput_tps
            case "ttft_p95_s":
                return None if self.ttft is None else self.ttft.p95
            case "bench_success_rate":
                return None if self.bench_success is None else self.bench_success.lower_95
            case "max_tools_effective":
                return None if self.max_tools_effective is None else float(self.max_tools_effective)
            case _:
                raise KeyError(f"clave de umbral desconocida: {clave!r}")

    def evaluar_umbrales(
        self, umbrales: tuple[Umbral, ...] = UMBRALES_FASE_0
    ) -> list[EvaluacionUmbral]:
        """Contrasta la tarjeta con los umbrales de la fase 0."""
        return [
            EvaluacionUmbral(umbral=u, valor=(v := self._lectura(u.clave)), veredicto=u.evaluar(v))
            for u in umbrales
        ]

    def go(self, umbrales: tuple[Umbral, ...] = UMBRALES_FASE_0) -> bool:
        """¿Se sostiene el diseño de la fase 1 sobre este modelo?

        `False` NO significa que el modelo sea malo. Significa que hay que
        rediseñar los bloques 3, 4 y 6 a sub-tareas cortas con relevo antes de
        escribirlos — que es exactamente para lo que existe esta fase.
        """
        return not any(e.bloquea for e in self.evaluar_umbrales(umbrales))


# --------------------------------------------------------------------------- #
# Banco de tareas
# --------------------------------------------------------------------------- #

CriterioKind = Literal["command", "file_exists", "file_contains", "diff_touches"]


class Criterio(BaseModel):
    """Criterio de aceptación EJECUTABLE de una tarea del banco.

    No hay criterios en prosa. Si un criterio no se puede comprobar con un
    proceso que devuelve un código de salida, no es un criterio: es una
    intención. Esta restricción es deliberada y es la semilla del
    `AcceptanceContract` del bloque 9.
    """

    kind: CriterioKind
    descripcion: str
    comando: list[str] | None = None
    """argv, ejecutado con shell=False dentro del workspace de la tarea."""
    ruta: str | None = None
    patron: str | None = None
    timeout_s: int = 300
    debe_fallar_antes: bool = True
    """Si `True`, el runner verifica que el criterio FALLA sobre el repo sin
    tocar. Una tarea cuyo criterio ya pasa antes de empezar no mide nada."""


class BenchTask(BaseModel):
    """Una tarea real de un repo real, con criterio ejecutable.

    El banco público se construye sobre `edecan`. Bancos privados adicionales
    pueden cargarse localmente sin versionar sus repositorios ni sus oráculos.
    """

    id: str
    repo: Literal["edecan"]
    titulo: str
    enunciado: str
    """Lo que se le dice al agente. Redactado como se lo diría una persona."""

    clase: Literal["trivial", "standard", "guarded"]
    """trivial: un archivo, sin dependencias. standard: varios archivos con
    pruebas existentes. guarded: toca algo con efecto (migración, despliegue) y
    exige aprobación."""

    lenguajes: list[str] = Field(default_factory=list)
    archivos_pista: list[str] = Field(default_factory=list)
    """Archivos que una solución razonable tocaría. NO se le dan al agente: se
    usan para medir si su búsqueda encontró lo correcto."""

    criterios: list[Criterio]
    setup: list[str] | None = None
    presupuesto_usd: float = 0.50
    max_turnos: int = 40


class BenchOutcome(BaseModel):
    """Resultado de correr una tarea del banco."""

    task_id: str
    resuelta: bool
    criterios_pasados: int
    criterios_totales: int
    turnos: int
    tokens_entrada: int = 0
    tokens_salida: int = 0
    coste_usd: float = 0.0
    duracion_s: float = 0.0
    archivos_tocados: list[str] = Field(default_factory=list)
    recall_pistas: float | None = None
    """Fracción de `archivos_pista` que el agente llegó a leer. Mide si el
    problema fue encontrar el código o cambiarlo — dos fallos muy distintos."""
    error: str | None = None
    traza: str | None = None
