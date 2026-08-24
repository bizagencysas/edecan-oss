"""Planificador del reparto: decide qué pasos de un plan van en paralelo.

``ide_equipo.py`` ya resuelve la parte peligrosa de correr sub-tareas a la vez
-- rechaza reparto solapado, limita concurrencia real, aísla fallos,
permite cancelar. Pero ``ide_equipo`` recibe el reparto YA decidido: alguien
tiene que decir "estos 3 pasos son independientes, corren juntos" y "este
paso depende del anterior, va solo". Ese "alguien" es este módulo.

La entrada natural es un ``ide_plan.Plan`` aprobado (o, más específico, la
lista de pasos que produce ``ide_ejecutor_plan.py`` en esta misma corrida
paralela). Como ese ejecutor es una pieza hermana que no existe todavía al
escribir este archivo, ``ide_reparto`` no depende de su forma exacta: define
su propio tipo de entrada (``PasoReparto``) con una lista de rutas OPCIONAL
por paso, y dos formas de llenarla -- explícita (lo ideal: quien arma los
pasos ya sabe qué archivos toca cada uno) o heurística de respaldo
(``rutas_desde_texto``, best-effort sobre el texto del paso). Quien conecte
esto con ``ide_ejecutor_plan.py`` decide cuál usar.

La regla dura que pidió el dueño, y que gobierna todo este archivo:

    Si no se puede determinar CON CONFIANZA qué archivos toca un paso, ese
    paso va SOLO. Ante la duda, secuencial: es lento, pero lento es
    infinitamente mejor que un repo corrupto.

Por eso ``rutas=None`` en un ``PasoReparto`` no es "sin restricción" (que
lo dejaría chocar con nada y correr con cualquiera) -- es exactamente lo
opuesto: fuerza a que ese paso ocupe una oleada él solo, aislado del paso
anterior y del siguiente.

Cómo se cablearía en la práctica (lo hace el humano después, igual que con
``ide_equipo``): quien orqueste la ejecución de un ``Plan`` (hoy candidato
natural: ``ide_sessions.py``, o el propio ``ide_ejecutor_plan.py``) arma una
lista de ``PasoReparto`` -- uno por ``PlanStep`` de ``ide_plan`` --, se la
pasa a ``PlanificadorReparto.ejecutar(...)`` con un ``runner`` real (el mismo
tipo de función que ya espera ``ide_equipo.SubtareaRunner``: recibe la
sub-tarea convertida y un ``ControlEquipo``, hace un turno de agente
acotado a esas rutas), y por cada paso que termine llama
``PlanStore.advance/skip_current/fail`` para que el estado del plan
(visible al usuario) quede sincronizado con lo que de verdad pasó.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Literal

from edecan_companion import ide_equipo
from edecan_companion.ide_equipo import (
    MAX_CONCURRENCIA_PERMITIDA,
    ControlEquipo,
    EquipoDeAgentes,
    EquipoError,
    ResultadoEquipo,
    Subtarea,
)

__all__ = [
    "MAX_CONCURRENCIA_PERMITIDA",
    "ControlEquipo",
    "EstadoPaso",
    "Oleada",
    "PasoReparto",
    "PlanReparto",
    "PlanificadorReparto",
    "RepartoError",
    "ResultadoReparto",
    "construir_oleadas",
    "especificacion_herramienta",
    "pasos_desde_json",
    "rutas_desde_texto",
]

EstadoNombre = Literal["completada", "fallida", "cancelada"]

# Zona sentinela usada SOLO para poder construir un ``Subtarea`` válido (que
# exige rutas no vacías) a partir de un paso con ``rutas=None``. Es seguro
# usar un valor fijo porque ``construir_oleadas`` garantiza que un paso con
# ``rutas=None`` SIEMPRE queda solo en su oleada -- no hay ningún otro paso
# en esa misma oleada contra el cual esta ruta pudiera "chocar" por
# casualidad, así que el contenido exacto del string es irrelevante para la
# seguridad del reparto.
_ZONA_PASO_SIN_RUTAS_DECLARADAS = "(paso-sin-rutas-declaradas)/"


class RepartoError(ValueError):
    """Entrada inválida: pasos mal formados, ids repetidos, config inválida."""


# ---------------------------------------------------------------------------
# Heurística de respaldo: adivinar rutas a partir del texto de un paso.
#
# Deliberadamente conservadora -- ver la regla dura en el docstring del
# módulo. Solo reconoce tokens con forma de ruta de código real (al menos un
# "/" y una extensión conocida, o una zona que termina en "/"); cualquier
# cosa ambigua se descarta. Si al final no queda ningún token reconocido,
# devuelve ``None`` (no listar rutas a medias: es preferible "no sé" a "creo
# que sé").
# ---------------------------------------------------------------------------

_EXTENSIONES_RECONOCIDAS = (
    "py|ts|tsx|js|jsx|json|yaml|yml|md|toml|cfg|ini|txt|sql|env|go|rs|java|"
    "kt|rb|php|css|scss|html|swift|sh|graphql|proto"
)

_RUTA_ARCHIVO_RE = re.compile(
    r"(?<![\w/.])(?:[\w.\-]+/)+[\w.\-]+\.(?:" + _EXTENSIONES_RECONOCIDAS + r")(?![\w/])",
)
_RUTA_ZONA_RE = re.compile(r"(?<![\w/.])(?:[\w.\-]+/)+(?![\w/.\-])")


def rutas_desde_texto(texto: str) -> tuple[str, ...] | None:
    """Extrae rutas de código con forma reconocible del texto de un paso.

    Best-effort, no autoritativo: sirve de respaldo cuando quien arma los
    ``PasoReparto`` no tiene ya la lista de archivos a mano. Devuelve
    ``None`` (no ``()``) cuando no encuentra nada reconocible, para que
    ``construir_oleadas`` lo trate como "sin confianza" y aísle el paso.
    """
    if not isinstance(texto, str) or not texto.strip():
        return None
    archivos = {m.group(0) for m in _RUTA_ARCHIVO_RE.finditer(texto)}
    zonas = {m.group(0) for m in _RUTA_ZONA_RE.finditer(texto)}
    # Una zona que ya está cubierta por un archivo encontrado (el archivo
    # cae dentro de esa zona) es ruido redundante -- nos quedamos con el
    # archivo, que es más preciso.
    zonas = {z for z in zonas if not any(a.startswith(z) for a in archivos)}
    encontradas = archivos | zonas
    if not encontradas:
        return None
    return tuple(sorted(encontradas))


# ---------------------------------------------------------------------------
# Entrada: un paso del plan con (quizás) sus rutas ya determinadas.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PasoReparto:
    """Un paso de un ``ide_plan.Plan`` visto por el planificador de reparto.

    ``rutas=None`` (o ``()``, que se normaliza a ``None``) significa "no se
    sabe con confianza qué archivos toca este paso" -- ver la regla dura del
    docstring del módulo. Con rutas conocidas, el formato es el mismo que
    ``ide_equipo.Subtarea.rutas``: archivos exactos o zonas terminadas en
    "/".
    """

    id: str
    titulo: str
    instrucciones: str
    rutas: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise RepartoError("Todo paso necesita un id no vacío.")
        if not isinstance(self.titulo, str) or not self.titulo.strip():
            raise RepartoError(f"El paso {self.id!r} necesita un título.")
        if not isinstance(self.instrucciones, str) or not self.instrucciones.strip():
            raise RepartoError(f"El paso {self.id!r} necesita instrucciones.")
        if self.rutas is not None and len(self.rutas) == 0:
            # Lista vacía declarada explícitamente == no se declaró nada:
            # mismo tratamiento que ``None`` (va solo).
            object.__setattr__(self, "rutas", None)

    def _a_subtarea(self) -> Subtarea:
        """Convierte a ``ide_equipo.Subtarea`` para reusar su validación de
        rutas y su detección de solapamientos. Ver el comentario sobre
        ``_ZONA_PASO_SIN_RUTAS_DECLARADAS`` para el caso ``rutas=None``."""
        rutas = self.rutas if self.rutas is not None else (_ZONA_PASO_SIN_RUTAS_DECLARADAS,)
        try:
            return Subtarea(
                id=self.id, titulo=self.titulo, instrucciones=self.instrucciones, rutas=rutas
            )
        except EquipoError as exc:
            raise RepartoError(f"Paso {self.id!r} inválido: {exc}") from exc


# ---------------------------------------------------------------------------
# Planificación: agrupar pasos en oleadas (paralelas dentro, en orden entre
# ellas).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Oleada:
    """Un grupo de pasos que pueden correr a la vez (si tiene más de uno) o
    un único paso que debe ir aislado."""

    indice: int
    pasos: tuple[PasoReparto, ...]

    @property
    def es_paralela(self) -> bool:
        return len(self.pasos) > 1


@dataclass(frozen=True, slots=True)
class PlanReparto:
    """El reparto ya resuelto en oleadas, listo para ejecutarse en orden."""

    oleadas: tuple[Oleada, ...]

    @property
    def total_pasos(self) -> int:
        return sum(len(oleada.pasos) for oleada in self.oleadas)


def _chocan(pendientes: list[PasoReparto], candidato: PasoReparto) -> bool:
    """True si ``candidato`` choca con algún paso ya aceptado en la oleada en
    construcción. Reusa ``ide_equipo.detectar_solapamientos`` en vez de
    reimplementar la comparación de rutas -- ese algoritmo ya está probado
    ahí.

    Siempre convierte ``candidato`` a ``Subtarea`` (incluso sin pendientes
    con quien comparar): eso valida sus rutas de una vez -- una ruta
    inválida (p. ej. con "../") se rechaza aquí, no se descubre recién al
    ejecutar.
    """
    candidato_sub = candidato._a_subtarea()
    if not pendientes:
        return False
    subtareas = [p._a_subtarea() for p in pendientes] + [candidato_sub]
    conflictos = ide_equipo.detectar_solapamientos(subtareas)
    return any(c.subtarea_b == candidato.id or c.subtarea_a == candidato.id for c in conflictos)


def construir_oleadas(pasos: list[PasoReparto]) -> PlanReparto:
    """Agrupa ``pasos`` (en el orden del plan) en oleadas.

    Algoritmo, de un solo paso por la lista, preservando siempre el orden
    original como límite de seguridad (nunca se adelanta un paso posterior
    por encima de uno anterior con el que no se pudo agrupar):

    - un paso con rutas desconocidas (``rutas=None``) SIEMPRE cierra la
      oleada en curso y ocupa una oleada propia, él solo -- no se arriesga a
      agruparlo con nada, ni antes ni después.
    - un paso con rutas conocidas se suma a la oleada en curso si no choca
      con ningún paso ya aceptado ahí; si choca, cierra esa oleada y abre
      una nueva empezando por él (que sí puede seguir creciendo con los
      pasos que vengan después, si son compatibles con él).

    Nunca reordena: dos pasos que en el plan aparecen en las posiciones i y
    j (i<j) jamás intercambian su oleada relativa -- lo único que decide
    este algoritmo es si van juntos o separados, no en qué orden.
    """
    if not pasos:
        raise RepartoError("El reparto necesita al menos un paso.")
    vistos: set[str] = set()
    for paso in pasos:
        if paso.id in vistos:
            raise RepartoError(f"El id de paso {paso.id!r} está repetido en el reparto.")
        vistos.add(paso.id)

    oleadas: list[list[PasoReparto]] = []
    actual: list[PasoReparto] = []

    def cerrar_actual() -> None:
        if actual:
            oleadas.append(list(actual))
            actual.clear()

    for paso in pasos:
        if paso.rutas is None:
            cerrar_actual()
            oleadas.append([paso])
            continue
        if _chocan(actual, paso):
            cerrar_actual()
        actual.append(paso)
    cerrar_actual()

    return PlanReparto(
        oleadas=tuple(Oleada(indice=i, pasos=tuple(grupo)) for i, grupo in enumerate(oleadas))
    )


# ---------------------------------------------------------------------------
# Ejecución: correr el ``PlanReparto`` oleada por oleada, reusando
# ``ide_equipo.EquipoDeAgentes`` para la concurrencia/aislamiento/cancelación
# dentro de cada oleada.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EstadoPaso:
    """Resultado de un paso después de que su oleada terminó (o se canceló)."""

    id: str
    titulo: str
    oleada: int
    estado: EstadoNombre
    salida: str | None = None
    error: str | None = None
    duracion_s: float | None = None


@dataclass(frozen=True, slots=True)
class ResultadoReparto:
    """Lo que junta ``PlanificadorReparto`` al terminar (o cancelar) un plan."""

    estados: dict[str, EstadoPaso]
    cancelado: bool
    oleadas_totales: int

    @property
    def completados(self) -> list[str]:
        return [e.id for e in self.estados.values() if e.estado == "completada"]

    @property
    def fallidos(self) -> list[str]:
        return [e.id for e in self.estados.values() if e.estado == "fallida"]

    @property
    def cancelados(self) -> list[str]:
        return [e.id for e in self.estados.values() if e.estado == "cancelada"]

    @property
    def exito_total(self) -> bool:
        return not self.cancelado and not self.fallidos

    def resumen(self) -> str:
        total = len(self.estados)
        partes = [
            f"{len(self.completados)}/{total} paso(s) completados en "
            f"{self.oleadas_totales} oleada(s)"
        ]
        if self.fallidos:
            detalle = "; ".join(
                f"{self.estados[id_].titulo} ({self.estados[id_].error})" for id_ in self.fallidos
            )
            partes.append(f"{len(self.fallidos)} fallido(s): {detalle}")
        if self.cancelados:
            partes.append(f"{len(self.cancelados)} cancelado(s)")
        return ". ".join(partes) + "."


class PlanificadorReparto:
    """Decide las oleadas de un reparto y las ejecuta en orden.

    Cada oleada se lanza como un ``ide_equipo.PlanEquipo`` propio (una
    instancia nueva de ``EquipoDeAgentes`` por oleada, tal como pide su
    docstring: "cada instancia ejecuta un plan a la vez"), pero comparten el
    mismo evento de cancelación de principio a fin -- cancelar a mitad de
    camino no solo detiene la oleada en curso, también evita que arranquen
    las siguientes: cada ``EquipoDeAgentes.ejecutar`` posterior ve el evento
    ya marcado y devuelve sus pasos como "cancelada" sin invocar al runner
    (mismo mecanismo cooperativo documentado en ``ide_equipo``).

    Un fallo de un paso (dentro de una oleada, vía aislamiento de
    ``EquipoDeAgentes``, o entre oleadas) nunca detiene el resto del
    reparto: solo la cancelación explícita lo hace.
    """

    def __init__(
        self,
        runner: ide_equipo.SubtareaRunner,
        *,
        max_concurrencia: int = 3,
        on_evento: ide_equipo.EventoCallback | None = None,
    ) -> None:
        if max_concurrencia < 1 or max_concurrencia > MAX_CONCURRENCIA_PERMITIDA:
            raise RepartoError(
                "max_concurrencia debe estar entre 1 y "
                f"{MAX_CONCURRENCIA_PERMITIDA}; se recibió {max_concurrencia}."
            )
        self._runner = runner
        self._max_concurrencia = max_concurrencia
        self._on_evento = on_evento
        self._en_curso = False
        self._cancelar: asyncio.Event | None = None
        self._equipo_actual: EquipoDeAgentes | None = None

    async def ejecutar(self, pasos: list[PasoReparto]) -> ResultadoReparto:
        if self._en_curso:
            raise RepartoError("Esta instancia ya tiene un reparto en ejecución.")
        plan = construir_oleadas(pasos)
        self._en_curso = True
        self._cancelar = asyncio.Event()
        estados: dict[str, EstadoPaso] = {}
        try:
            for oleada in plan.oleadas:
                equipo = EquipoDeAgentes(
                    runner=self._runner,
                    max_concurrencia=self._max_concurrencia,
                    on_evento=self._on_evento,
                )
                self._equipo_actual = equipo
                plan_equipo = ide_equipo.construir_plan(
                    [p._a_subtarea() for p in oleada.pasos]
                )
                resultado_oleada: ResultadoEquipo = await equipo.ejecutar(
                    plan_equipo, cancelar=self._cancelar
                )
                for paso in oleada.pasos:
                    sub_estado = resultado_oleada.estados[paso.id]
                    estados[paso.id] = EstadoPaso(
                        id=paso.id,
                        titulo=paso.titulo,
                        oleada=oleada.indice,
                        estado=sub_estado.estado,
                        salida=sub_estado.salida,
                        error=sub_estado.error,
                        duracion_s=sub_estado.duracion_s,
                    )
            cancelado = self._cancelar.is_set()
        finally:
            self._equipo_actual = None
            self._en_curso = False
            self._cancelar = None
        return ResultadoReparto(
            estados=estados, cancelado=cancelado, oleadas_totales=len(plan.oleadas)
        )

    def cancelar_todo(self, *, forzado: bool = False) -> None:
        """Pide que el reparto entero pare, dejando el workspace consistente.

        Cooperativo por defecto (las oleadas que no arrancaron no llegan a
        invocar al runner); con ``forzado=True`` además corta la oleada que
        esté corriendo en este momento -- delega en
        ``EquipoDeAgentes.cancelar_todo`` de la oleada activa, que es quien
        de verdad sabe qué tareas están en curso.
        """
        if self._cancelar is not None:
            self._cancelar.set()
        if forzado and self._equipo_actual is not None:
            self._equipo_actual.cancelar_todo(forzado=True)


# ---------------------------------------------------------------------------
# Contrato de herramienta / conversión desde JSON, mismo formato que
# ``ide_equipo.especificacion_herramienta`` / ``subtareas_desde_json``.
# ---------------------------------------------------------------------------


def especificacion_herramienta() -> dict[str, Any]:
    """Describe la tool "planificar_reparto" para quien conecte el agente
    principal a este módulo.

    A diferencia de "repartir_equipo" (``ide_equipo``), aquí el modelo NO
    tiene que decidir qué es paralelizable -- solo declara los pasos del
    plan (y, si las conoce con confianza, sus rutas); este módulo decide las
    oleadas y las ejecuta. Un paso sin ``rutas`` declaradas se trata como
    "va solo", nunca como "sin restricciones".
    """
    return {
        "name": "planificar_reparto",
        "description": (
            "Recibe los pasos de un plan ya aprobado y los ejecuta agrupando "
            "en oleadas: los pasos con archivos disjuntos corren en paralelo "
            "dentro de una oleada, y las oleadas se ejecutan en orden. Un "
            "paso sin rutas declaradas (o que choca con otro) va solo en su "
            "propia oleada."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pasos": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "titulo": {"type": "string"},
                            "instrucciones": {"type": "string"},
                            "rutas": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Archivos exactos o zonas ('/' al final) "
                                    "que este paso toca. Si se omite o va "
                                    "vacía, el paso se ejecuta aislado."
                                ),
                            },
                        },
                        "required": ["id", "titulo", "instrucciones"],
                        "additionalProperties": False,
                    },
                },
                "max_concurrencia": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_CONCURRENCIA_PERMITIDA,
                },
            },
            "required": ["pasos"],
            "additionalProperties": False,
        },
    }


def pasos_desde_json(bruto: Any) -> list[PasoReparto]:
    """Convierte el payload crudo (ya deserializado de JSON) del argumento
    ``pasos`` de la tool en objetos ``PasoReparto`` validados.

    Si un item no trae ``rutas`` (o la trae vacía), el paso queda con
    ``rutas=None`` -- va solo, ver la regla dura del módulo. Esta función NO
    aplica la heurística de ``rutas_desde_texto``: es responsabilidad de
    quien arma el payload decidir si quiere ese respaldo o prefiere que el
    paso vaya aislado por defecto.
    """
    if not isinstance(bruto, list) or not bruto:
        raise RepartoError("El campo 'pasos' debe ser una lista no vacía.")
    pasos: list[PasoReparto] = []
    for i, item in enumerate(bruto):
        if not isinstance(item, dict):
            raise RepartoError(f"pasos[{i}] debe ser un objeto.")
        rutas_bruto = item.get("rutas")
        rutas = tuple(str(r) for r in rutas_bruto) if rutas_bruto else None
        try:
            pasos.append(
                PasoReparto(
                    id=str(item.get("id", "")),
                    titulo=str(item.get("titulo", "")),
                    instrucciones=str(item.get("instrucciones", "")),
                    rutas=rutas,
                )
            )
        except RepartoError:
            raise
        except (TypeError, ValueError) as exc:
            raise RepartoError(f"pasos[{i}] inválido: {exc}") from exc
    return pasos
