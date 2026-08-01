"""Orquestador de agentes en paralelo dentro del IDE.

El dueño pidió explícitamente poder repartir una tarea grande entre varios
sub-agentes a la vez, como hace un agente de código real (varios workers
tocando el repo al mismo tiempo). Este módulo es esa pieza: NO ejecuta un
sub-agente concreto (eso lo decide quien lo conecte, por ejemplo llamando a
``WorkersIDEAgent.run`` una vez por sub-tarea con un prompt acotado a sus
rutas); su trabajo es la parte peligrosa de verdad -- repartir el trabajo sin
que dos agentes se pisen los archivos, limitar cuántos corren a la vez, y que
un fallo o una cancelación de uno no le explote el turno a los demás.

Quién decide CÓMO dividir la tarea grande en sub-tareas es el modelo (el
agente principal): solo él entiende la semántica de la tarea. Lo que este
módulo garantiza es que, sea cual sea esa división, el reparto es SEGURO
antes de lanzar un solo proceso: si dos sub-tareas reclaman el mismo archivo
o zona, el plan se rechaza entero en ``construir_plan`` y no se ejecuta nada.
Esa es la propiedad que de verdad importa -- dos agentes editando el mismo
archivo se destruyen el trabajo mutuamente, y eso hay que impedirlo antes de
arrancar, no descubrirlo después con un diff roto.

Consistencia ante cancelación: como cada sub-tarea es dueña exclusiva de sus
rutas (eso ya lo garantizó ``construir_plan``), cancelar a la mitad nunca deja
a una sub-tarea pisando el trabajo de otra -- en el peor caso, la sub-tarea
que estaba en curso queda a medias (responsabilidad del runner inyectado,
p. ej. escribir_archivo ya es atómico en ``ide_files.py``), pero el resto del
reparto queda intacto porque nunca hubo dos agentes compartiendo un archivo.

Este archivo es autocontenido a propósito: no importa ``ide_sessions``,
``ide_workers_agent`` ni nada de ``packages/llm``. La integración real
(qué ``runner`` conecta cada sub-tarea a un turno de agente de verdad, con
qué prompt, en qué sesión) la hace el humano después.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

# Techo duro de concurrencia. El dueño fue explícito: "no lances 50 procesos
# en la máquina de alguien". Cada sub-agente puede ser un proceso real (un
# ``claude -p`` o un turno de Workers AI con sus propias tools), así que el
# límite protege la máquina del usuario, no solo el event loop.
MAX_CONCURRENCIA_PERMITIDA = 8

EstadoNombre = Literal["completada", "fallida", "cancelada"]

EventoCallback = Callable[[str, str, str], None]
"""``(id_subtarea, tipo_evento, texto) -> None``. Gancho opcional de
observabilidad -- quien conecte esto a una ``Session`` de ``ide_sessions``
puede usarlo para llamar ``session.append(...)`` sin que este módulo sepa que
``Session`` existe."""


class EquipoError(ValueError):
    """Entrada inválida: plan mal formado, reparto solapado, config inválida."""


def _normaliza_ruta(ruta_cruda: str) -> str:
    """Normaliza una ruta relativa declarada por una sub-tarea.

    Solo hace higiene de la CADENA (sin tocar disco: este módulo no sabe
    dónde vive el workspace). Rechaza lo que en cualquier integración futura
    sería una fuga fuera del reparto acordado: rutas absolutas, ``..``, y
    bytes de control. La distinción archivo/zona vive en si termina en "/".
    """
    if not isinstance(ruta_cruda, str) or not ruta_cruda.strip():
        raise EquipoError("Una ruta de reparto no puede estar vacía.")
    if "\x00" in ruta_cruda or any(ord(c) < 32 for c in ruta_cruda):
        raise EquipoError(f"Ruta de reparto inválida: {ruta_cruda!r}.")
    ruta = ruta_cruda.strip().replace("\\", "/")
    es_zona = ruta.endswith("/")
    partes = [parte for parte in ruta.split("/") if parte not in ("", ".")]
    if not partes:
        raise EquipoError(f"Ruta de reparto inválida: {ruta_cruda!r}.")
    if any(parte == ".." for parte in partes):
        raise EquipoError(f"Ruta de reparto fuera del workspace: {ruta_cruda!r}.")
    normalizada = "/".join(partes)
    return normalizada + "/" if es_zona else normalizada


@dataclass(frozen=True, slots=True)
class Subtarea:
    """Una porción independiente de la tarea grande, con dueño único de archivos.

    ``rutas`` mezcla archivos exactos (``"apps/api/main.py"``) y zonas
    completas (``"apps/api/routers/"``, con "/" al final: posee todo lo que
    esté debajo, recursivo). ``instrucciones`` es el prompt que le
    correspondería a esa sub-tarea; este módulo lo transporta sin leerlo --
    quien conecte el runner real decide cómo convertirlo en un turno de
    agente.
    """

    id: str
    titulo: str
    instrucciones: str
    rutas: tuple[str, ...]
    contexto: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise EquipoError("Toda sub-tarea necesita un id no vacío.")
        if not isinstance(self.titulo, str) or not self.titulo.strip():
            raise EquipoError(f"La sub-tarea {self.id!r} necesita un título.")
        if not isinstance(self.instrucciones, str) or not self.instrucciones.strip():
            raise EquipoError(f"La sub-tarea {self.id!r} necesita instrucciones.")
        if not self.rutas:
            raise EquipoError(
                f"La sub-tarea {self.id!r} no reclama ninguna ruta/zona; "
                "sin dueño de archivos no se puede repartir de forma segura."
            )
        normalizadas = tuple(_normaliza_ruta(ruta) for ruta in self.rutas)
        # ``frozen=True`` impide asignar directo; esto guarda la versión ya
        # normalizada sin romper la inmutabilidad del dataclass.
        object.__setattr__(self, "rutas", normalizadas)


@dataclass(frozen=True, slots=True)
class ConflictoReparto:
    """Un solapamiento concreto detectado entre dos sub-tareas."""

    subtarea_a: str
    subtarea_b: str
    ruta_a: str
    ruta_b: str

    def __str__(self) -> str:  # pragma: no cover - solo formatea texto
        return (
            f"'{self.subtarea_a}' ({self.ruta_a}) choca con "
            f"'{self.subtarea_b}' ({self.ruta_b})"
        )


def _es_zona(ruta: str) -> bool:
    return ruta.endswith("/")


def _rutas_chocan(ruta_a: str, ruta_b: str) -> bool:
    """True si ``ruta_a`` y ``ruta_b`` (ya normalizadas) reclaman lo mismo."""
    if ruta_a == ruta_b:
        return True
    zona_a, zona_b = _es_zona(ruta_a), _es_zona(ruta_b)
    if not zona_a and not zona_b:
        # dos archivos exactos: solo chocan si son literalmente el mismo,
        # ya cubierto arriba.
        return False
    if zona_a and zona_b:
        # dos zonas chocan si una contiene a la otra (o son iguales, ya
        # cubierto arriba). "a/b/" contiene a "a/b/c/".
        return ruta_a.startswith(ruta_b) or ruta_b.startswith(ruta_a)
    # una zona y un archivo: chocan si el archivo cae dentro de la zona.
    archivo, zona = (ruta_b, ruta_a) if zona_a else (ruta_a, ruta_b)
    return archivo.startswith(zona)


def detectar_solapamientos(subtareas: Sequence[Subtarea]) -> list[ConflictoReparto]:
    """Devuelve todos los pares de rutas que chocan entre sub-tareas distintas.

    Lista vacía = reparto seguro. No muta nada; separado de
    ``construir_plan`` para que se pueda inspeccionar un reparto propuesto
    (p. ej. mostrárselo al dueño) sin forzar la excepción.
    """
    conflictos: list[ConflictoReparto] = []
    for i, sub_a in enumerate(subtareas):
        for sub_b in subtareas[i + 1 :]:
            for ruta_a in sub_a.rutas:
                for ruta_b in sub_b.rutas:
                    if _rutas_chocan(ruta_a, ruta_b):
                        conflictos.append(
                            ConflictoReparto(sub_a.id, sub_b.id, ruta_a, ruta_b)
                        )
    return conflictos


@dataclass(frozen=True, slots=True)
class PlanEquipo:
    """Un reparto ya validado: ninguna sub-tarea pisa las rutas de otra."""

    subtareas: tuple[Subtarea, ...]


def construir_plan(subtareas: Sequence[Subtarea]) -> PlanEquipo:
    """Valida un reparto propuesto y lo congela en un ``PlanEquipo``.

    Rechaza (``EquipoError``) en vez de dejar pasar un reparto solapado: esa
    es la regla dura que pidió el dueño. También rechaza ids repetidos --
    dos sub-tareas con el mismo id harían ambiguo el resultado final.
    """
    if not subtareas:
        raise EquipoError("Un plan de equipo necesita al menos una sub-tarea.")
    vistos: set[str] = set()
    for sub in subtareas:
        if sub.id in vistos:
            raise EquipoError(f"El id de sub-tarea {sub.id!r} está repetido en el plan.")
        vistos.add(sub.id)
    conflictos = detectar_solapamientos(subtareas)
    if conflictos:
        detalle = "; ".join(str(c) for c in conflictos)
        raise EquipoError(
            f"Reparto rechazado: {len(conflictos)} solapamiento(s) de archivos/zonas. {detalle}"
        )
    return PlanEquipo(subtareas=tuple(subtareas))


class ControlEquipo:
    """Lo único que un ``runner`` de sub-tarea puede ver del equipo.

    Deliberadamente angosto: un sub-agente no necesita (ni debe) ver el
    estado de las demás sub-tareas mientras corre -- eso rompería el
    aislamiento que hace que un fallo ajeno no le importe. Lo único que
    necesita es poder preguntar "¿me cancelaron?" para cortar limpio.
    """

    __slots__ = ("_evento_cancelacion",)

    def __init__(self, evento_cancelacion: asyncio.Event) -> None:
        self._evento_cancelacion = evento_cancelacion

    def cancelado(self) -> bool:
        return self._evento_cancelacion.is_set()


SubtareaRunner = Callable[[Subtarea, ControlEquipo], Awaitable[str]]
"""Firma que debe cumplir quien conecte un sub-agente real. Recibe la
sub-tarea y un ``ControlEquipo``, devuelve el texto de resultado del turno.
Si lanza una excepción, esa sub-tarea (y solo esa) queda como fallida."""


@dataclass(frozen=True, slots=True)
class EstadoSubtarea:
    """Resultado de una sub-tarea después de que el equipo terminó."""

    id: str
    titulo: str
    estado: EstadoNombre
    salida: str | None = None
    error: str | None = None
    duracion_s: float | None = None


@dataclass(frozen=True, slots=True)
class ResultadoEquipo:
    """Lo que junta el orquestador al terminar (o al cancelar) un plan."""

    estados: dict[str, EstadoSubtarea]
    cancelado: bool

    @property
    def completadas(self) -> list[str]:
        return [e.id for e in self.estados.values() if e.estado == "completada"]

    @property
    def fallidas(self) -> list[str]:
        return [e.id for e in self.estados.values() if e.estado == "fallida"]

    @property
    def canceladas(self) -> list[str]:
        return [e.id for e in self.estados.values() if e.estado == "cancelada"]

    @property
    def exito_total(self) -> bool:
        return not self.cancelado and not self.fallidas

    def resumen(self) -> str:
        """Texto en español listo para devolver como resultado de la herramienta."""
        total = len(self.estados)
        partes = [
            f"{len(self.completadas)}/{total} sub-tarea(s) completadas",
        ]
        if self.fallidas:
            detalle_fallas = "; ".join(
                f"{self.estados[id_].titulo} ({self.estados[id_].error})"
                for id_ in self.fallidas
            )
            partes.append(f"{len(self.fallidas)} fallida(s): {detalle_fallas}")
        if self.canceladas:
            partes.append(f"{len(self.canceladas)} cancelada(s)")
        return ". ".join(partes) + "."


class EquipoDeAgentes:
    """Lanza un ``PlanEquipo`` con un tope real de concurrencia.

    Uso típico (desde un handler de herramienta async del agente principal)::

        equipo = EquipoDeAgentes(runner=mi_runner, max_concurrencia=3)
        resultado = await equipo.ejecutar(plan)
        return resultado.resumen()

    Cada instancia ejecuta UN plan a la vez -- si se necesita otro reparto en
    paralelo, se crea otra instancia. Eso evita que dos llamadas a
    ``ejecutar`` en la misma instancia se pisen el estado de cancelación.
    """

    def __init__(
        self,
        runner: SubtareaRunner,
        *,
        max_concurrencia: int = 3,
        on_evento: EventoCallback | None = None,
    ) -> None:
        if max_concurrencia < 1 or max_concurrencia > MAX_CONCURRENCIA_PERMITIDA:
            raise EquipoError(
                "max_concurrencia debe estar entre 1 y "
                f"{MAX_CONCURRENCIA_PERMITIDA}; se recibió {max_concurrencia}."
            )
        self._runner = runner
        self._max_concurrencia = max_concurrencia
        self._on_evento = on_evento
        self._en_curso = False
        self._cancelar: asyncio.Event | None = None
        self._tareas_activas: dict[str, asyncio.Task[None]] = {}

    def _emitir(self, id_subtarea: str, tipo: str, texto: str) -> None:
        if self._on_evento is not None:
            self._on_evento(id_subtarea, tipo, texto)

    async def ejecutar(
        self, plan: PlanEquipo, *, cancelar: asyncio.Event | None = None
    ) -> ResultadoEquipo:
        """Corre todas las sub-tareas del plan y junta los resultados.

        Un fallo (excepción) de una sub-tarea NO cancela a las demás: se
        captura, se registra como "fallida" y el resto sigue su curso. Solo
        se detiene lo que aún no arrancó (o se corta a media ejecución) si
        alguien marca ``cancelar`` -- ya sea el propio llamador con su
        ``asyncio.Event``, o esta instancia vía ``cancelar_todo()``.
        """
        if self._en_curso:
            raise EquipoError("Esta instancia ya tiene un plan en ejecución.")
        self._en_curso = True
        self._cancelar = cancelar if cancelar is not None else asyncio.Event()
        cancelar_evento = self._cancelar
        semaforo = asyncio.Semaphore(self._max_concurrencia)
        control = ControlEquipo(cancelar_evento)
        estados: dict[str, EstadoSubtarea] = {}

        async def ejecutar_una(sub: Subtarea) -> None:
            if cancelar_evento.is_set():
                estados[sub.id] = EstadoSubtarea(sub.id, sub.titulo, "cancelada")
                self._emitir(sub.id, "cancelada", "No se inició: el equipo fue cancelado.")
                return
            async with semaforo:
                if cancelar_evento.is_set():
                    estados[sub.id] = EstadoSubtarea(sub.id, sub.titulo, "cancelada")
                    self._emitir(
                        sub.id, "cancelada", "No se inició: el equipo fue cancelado."
                    )
                    return
                self._emitir(sub.id, "iniciando", sub.titulo)
                inicio = time.monotonic()
                try:
                    salida = await self._runner(sub, control)
                except asyncio.CancelledError:
                    estados[sub.id] = EstadoSubtarea(
                        sub.id,
                        sub.titulo,
                        "cancelada",
                        duracion_s=time.monotonic() - inicio,
                    )
                    self._emitir(sub.id, "cancelada", "Interrumpida a la mitad.")
                    return
                except Exception as exc:  # noqa: BLE001 - aislar el fallo es la regla
                    estados[sub.id] = EstadoSubtarea(
                        sub.id,
                        sub.titulo,
                        "fallida",
                        error=str(exc)[:2000],
                        duracion_s=time.monotonic() - inicio,
                    )
                    self._emitir(sub.id, "fallida", str(exc)[:2000])
                    return
                estados[sub.id] = EstadoSubtarea(
                    sub.id,
                    sub.titulo,
                    "completada",
                    salida=salida,
                    duracion_s=time.monotonic() - inicio,
                )
                self._emitir(sub.id, "completada", salida)

        tareas = {sub.id: asyncio.create_task(ejecutar_una(sub)) for sub in plan.subtareas}
        self._tareas_activas = tareas
        try:
            await asyncio.gather(*tareas.values())
        finally:
            self._tareas_activas = {}
            self._en_curso = False
            self._cancelar = None
        return ResultadoEquipo(
            estados={sub.id: estados[sub.id] for sub in plan.subtareas},
            cancelado=cancelar_evento.is_set(),
        )

    def cancelar_todo(self, *, forzado: bool = False) -> None:
        """Pide que el equipo pare, dejando el workspace consistente.

        Cooperativo por defecto: marca el evento de cancelación y las
        sub-tareas que todavía no arrancaron (o esperan cupo de
        concurrencia) no llegan a ejecutarse. Con ``forzado=True`` además
        cancela (``asyncio.Task.cancel``) las sub-tareas que ya están
        corriendo -- defensa en profundidad para un ``runner`` que no revisa
        ``control.cancelado()`` por su cuenta. En cualquiera de los dos
        modos, gracias a la propiedad de archivos exclusiva del plan, ninguna
        sub-tarea cortada a la mitad puede haber corrompido el trabajo de
        otra: como mucho deja a medias SU propia porción.
        """
        if self._cancelar is not None:
            self._cancelar.set()
        if forzado:
            for tarea in self._tareas_activas.values():
                if not tarea.done():
                    tarea.cancel()


# ---------------------------------------------------------------------------
# Descripción de herramienta, en el mismo formato (name/description/
# input_schema) que usa ``ide_workers_agent._tool`` -- para que el agente
# principal pueda registrar "repartir_equipo" como una tool más sin que este
# módulo dependa de ``edecan_llm.ToolSpec``. Quien conecte esto decide si lo
# envuelve en un ``ToolSpec`` real.
# ---------------------------------------------------------------------------


def especificacion_herramienta() -> dict[str, Any]:
    """Describe la tool "repartir_equipo" para que el agente principal la use.

    El agente principal propone la división de la tarea (id/título/
    instrucciones/rutas por sub-tarea) y esta herramienta reparte, ejecuta en
    paralelo con tope de concurrencia y devuelve el resumen. La validación de
    solapamiento ocurre server-side (``construir_plan``), no es responsabilidad
    del modelo acertar con eso -- solo tiene que declarar rutas honestas.
    """
    return {
        "name": "repartir_equipo",
        "description": (
            "Divide la tarea actual en sub-tareas independientes, cada una "
            "dueña exclusiva de sus archivos/zonas, y las ejecuta en "
            "paralelo con un tope de concurrencia. Si dos sub-tareas "
            "reclaman el mismo archivo o zona, se rechaza el reparto entero "
            "y no se ejecuta nada."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subtareas": {
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
                                "minItems": 1,
                                "items": {"type": "string"},
                                "description": (
                                    "Archivos exactos o zonas (terminan en "
                                    "'/') que esta sub-tarea posee en "
                                    "exclusiva."
                                ),
                            },
                        },
                        "required": ["id", "titulo", "instrucciones", "rutas"],
                        "additionalProperties": False,
                    },
                },
                "max_concurrencia": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_CONCURRENCIA_PERMITIDA,
                },
            },
            "required": ["subtareas"],
            "additionalProperties": False,
        },
    }


def subtareas_desde_json(bruto: Any) -> list[Subtarea]:
    """Convierte el payload crudo (dict/list ya deserializado de JSON) del
    argumento ``subtareas`` de la tool en objetos ``Subtarea`` validados.

    Separado de ``especificacion_herramienta`` porque esta función SÍ valida
    contenido (tipos, campos requeridos); la de arriba solo describe forma.
    """
    if not isinstance(bruto, list) or not bruto:
        raise EquipoError("El campo 'subtareas' debe ser una lista no vacía.")
    subtareas: list[Subtarea] = []
    for i, item in enumerate(bruto):
        if not isinstance(item, dict):
            raise EquipoError(f"subtareas[{i}] debe ser un objeto.")
        try:
            subtareas.append(
                Subtarea(
                    id=str(item.get("id", "")),
                    titulo=str(item.get("titulo", "")),
                    instrucciones=str(item.get("instrucciones", "")),
                    rutas=tuple(str(r) for r in (item.get("rutas") or [])),
                )
            )
        except EquipoError:
            raise
        except (TypeError, ValueError) as exc:
            raise EquipoError(f"subtareas[{i}] inválida: {exc}") from exc
    return subtareas
