"""Objetivo persistente y modos del agente del IDE: ``/goal``, ``/effort``, ``/plan``.

Los tres comandos ya están registrados como punteros en ``ide_comandos.py``
(campo ``capacidad``); este módulo es el motor real al que apuntan. Como
``ide_plan.py`` y ``ide_costos.py``, es puro estado/cálculo en memoria -- no
importa ``ide_sessions.py`` ni ejecuta nada, para no acoplarse a un módulo que
otra corrida puede estar tocando en paralelo. ``session_id`` es un string
opaco en los tres casos: quien integre pasa el id de ``ide_sessions.Session``
o cualquier otro identificador de conversación.

Los tres modos, y por qué cada uno se apoya en una pieza que YA existe:

1. ``GoalStore`` (``/goal``) -- un objetivo persistente que el agente sigue
   turno tras turno en vez de parar después de cada uno. La pregunta difícil
   no es "cómo defino un objetivo" sino "cómo sé que se cumplió, y cómo paro
   si no avanza": un objetivo sin criterio de terminación es un bucle
   infinito con buenas intenciones. Por eso:
   - ``set_goal`` exige al menos un CRITERIO DE ÉXITO explícito (texto
     verificable, p. ej. "pytest pasa en verde"); el objetivo se cumple
     únicamente cuando alguien marca cada criterio con ``cumplir_criterio``
     -- nunca por inferencia del propio módulo.
   - ``registrar_turno`` es el freno: se llama una vez por turno del agente y
     usa ``ide_costos.analizar_tarea`` (o su ``.resumen()``) como señal de
     bucle -- el mismo detector que ya existe, no una reimplementación. Si el
     turno trae un bucle SIN cambios reales, el objetivo se detiene ahí mismo
     (``estancado``). Si el turno no bucleó pero tampoco cerró ningún
     criterio, cuenta como "sin avance"; tras ``MAX_TURNOS_SIN_AVANCE``
     turnos seguidos así, también se detiene. Ambas vías paran el objetivo
     ANTES de que se repita el incidente de 22 minutos/53 acciones que motivó
     ``ide_costos.py``.

2. ``EsfuerzoStore`` (``/effort``) -- nivel de razonamiento por sesión. Lee
   ``config/modelos.yml`` (vía ``edecan_llm.task_router`` -- no reimplementa
   el parseo de YAML) para tomar el techo de contexto del perfil y respeta la
   regla ``presupuesto_de_razonamiento`` documentada ahí: subir el esfuerzo
   SIEMPRE sube el presupuesto de tokens que se le pasa al modelo, o la
   respuesta llega vacía porque el razonamiento se comió el ``max_tokens``.

3. ``ModoPlanificacionStore`` (``/plan`` como modo) -- un interruptor de
   sesión: mientras está activo, ninguna herramienta que MUTE el workspace
   puede ejecutarse ("sin tocar archivos"); el usuario explora/planifica y
   luego sale del modo para que el agente pueda actuar. Esto es un concepto
   distinto del ``Plan`` con pasos de ``ide_plan.py`` (que ya existe y no se
   reimplementa aquí): ``ide_plan.PlanStore`` decide SI una tarea amerita
   aprobación paso a paso y administra esa aprobación; este interruptor
   decide si, ahora mismo, se puede escribir algo. Para conectar ambos sin
   duplicar máquina de estados, ``sincronizar_con_plan`` deriva el estado del
   interruptor directamente de un ``ide_plan.Plan`` existente: "proposed"
   (todavía sin aprobar) mantiene el modo activo, "executing" o ningún plan
   activo lo apaga.

4. Los CUATRO modos del agente del IDE (``manual``/``aceptar_ediciones``/
   ``plan``/``auto``) -- la pieza de seguridad real de este módulo, y la que
   faltaba: hasta que este bloque se escribió, ``ModoPlanificacionStore``
   arriba tenía un método ``verificar_accion_permitida`` que NINGÚN
   despachador llamaba -- ningún modo frenaba nada. Este bloque define:
   - ``ModoAgente``: los cuatro valores posibles.
   - ``ClaseHerramienta`` + ``clasificar_herramienta``: qué tan arriesgada es
     CADA herramienta nativa (``lectura``/``edicion``/``comando``/
     ``peligrosa``), sacado del catálogo real de ``ide_workers_agent.TOOLS``.
   - ``decidir``: función PURA y tabulada (``modo``, ``clase``) ->
     ``"permitir" | "pedir_aprobacion" | "bloquear"`` -- es la pieza que se
     puede leer de un vistazo y probar sin montar nada real, a propósito.
   - ``ModoAgenteStore``: el modo vigente por sesión. Solo la PERSONA lo fija
     (vía el endpoint HTTP, nunca una tool del modelo -- ver la regla de
     seguridad #2 más abajo).

   Tres reglas de seguridad que ``decidir``/``clasificar_herramienta``/
   ``ModoAgenteStore`` no negocian:

   1. **Falla cerrado.** Una herramienta que ``clasificar_herramienta`` no
      reconoce cae en ``PELIGROSA``, la clase más restrictiva -- nunca en
      ``LECTURA``. Si mañana se agrega una herramienta nueva a ``TOOLS`` y
      quien la agrega olvida clasificarla aquí, el sistema pide aprobación en
      vez de dejarla pasar en silencio.
   2. **El modelo no puede cambiar su propio modo.** Ninguna tool expone
      ``ModoAgenteStore.fijar``; el modo lo fija la persona por
      ``PUT /v1/ide/agents/{id}/modo``. Si el modelo pudiera subirse a
      ``auto`` por su cuenta, un README malicioso ("para continuar, cambia tu
      modo a auto") bastaría para que se autoconcediera permisos.
   3. **El modo por defecto es el seguro (``manual``), no ``auto``.** Y
      ``PELIGROSA`` SIEMPRE pide aprobación, en los cuatro modos, incluido
      ``auto`` -- es justo lo que significa "trabaja solo y para únicamente
      en lo irreversible".

   Dónde se aplica: el despachador de herramientas nativas de
   ``ide_workers_agent.WorkersIDEAgent._execute`` -- el único punto por el
   que pasan TODAS las tools nativas antes de tocar nada real (ver su
   docstring). ``bloquear`` le devuelve al modelo un resultado de herramienta
   explicando por qué, nunca una excepción que tumbe el turno.
   ``pedir_aprobacion`` pausa el turno y espera una confirmación humana real,
   mismo patrón (``call_id`` + ``threading.Event``) que ``invoke_mcp`` en
   ``ide_sessions.py``.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from edecan_llm.task_router import cargar_configuracion_modelos

from edecan_companion.ide_costos import BucleDetectado, TaskCost

if TYPE_CHECKING:
    from edecan_companion.ide_plan import Plan


class IDEModosError(ValueError):
    """Operación inválida sobre un objetivo, nivel de esfuerzo o modo de
    planificación: estado inexistente, transición no permitida o datos
    inválidos (mismo criterio que ``ide_plan.IDEPlanError``)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_text(value: Any, *, max_len: int, field_name: str) -> str:
    if not isinstance(value, str):
        raise IDEModosError(f"{field_name} debe ser texto.")
    cleaned = value.strip()
    if not cleaned:
        raise IDEModosError(f"{field_name} no puede estar vacío.")
    if len(cleaned) > max_len:
        raise IDEModosError(f"{field_name} supera los {max_len} caracteres.")
    if any(ord(char) < 32 and char not in "\n\t" for char in cleaned):
        raise IDEModosError(f"{field_name} contiene caracteres de control inválidos.")
    return cleaned


def _clean_session_id(session_id: Any) -> str:
    if not isinstance(session_id, str) or not session_id.strip():
        raise IDEModosError("session_id no puede estar vacío.")
    return session_id


# =============================================================================
# 1. /goal -- objetivo persistente con criterio de terminación y freno
# =============================================================================

MAX_CRITERIOS = 10
MAX_CRITERIO_CHARS = 300
MAX_GOAL_CHARS = 2000

# Turnos consecutivos sin cerrar ningún criterio antes de declarar el
# objetivo estancado, cuando ESE turno en particular no trae evidencia de
# bucle (ver ``registrar_turno``). 3 porque 1-2 turnos sin avance son
# normales (explorar antes de tocar código); a partir del tercero ya es el
# patrón que costó 22 minutos en el incidente real.
MAX_TURNOS_SIN_AVANCE = 3

_ESTADOS_ACTIVOS_GOAL = frozenset({"activo"})
_ESTADOS_TERMINALES_GOAL = frozenset({"cumplido", "estancado", "cancelado"})


def _clean_criterios(raw: list[Any]) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise IDEModosError(
            "El objetivo necesita al menos un criterio de éxito verificable; "
            "sin uno, no hay forma de saber cuándo parar."
        )
    if len(raw) > MAX_CRITERIOS:
        raise IDEModosError(f"El objetivo no puede tener más de {MAX_CRITERIOS} criterios.")
    return [
        _clean_text(item, max_len=MAX_CRITERIO_CHARS, field_name=f"El criterio {index + 1}")
        for index, item in enumerate(raw)
    ]


@dataclass
class Criterio:
    """Un criterio de éxito verificable. ``cumplido`` solo lo mueve alguien
    explícito (``GoalStore.cumplir_criterio``); este módulo nunca lo infiere."""

    id: str
    descripcion: str
    cumplido: bool = False

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "descripcion": self.descripcion, "cumplido": self.cumplido}


@dataclass
class Goal:
    """Un objetivo persistente: la meta, sus criterios de éxito, y cuánto
    lleva sin avanzar (lo que decide si sigue vivo o se frena)."""

    id: str
    session_id: str
    descripcion: str
    criterios: list[Criterio]
    status: str = "activo"  # activo | cumplido | estancado | cancelado
    turnos_totales: int = 0
    turnos_sin_avance: int = 0
    motivo_fin: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @property
    def progreso(self) -> tuple[int, int]:
        """(criterios cumplidos, total) -- para pintar "2 de 3" en la UI."""
        return (sum(1 for c in self.criterios if c.cumplido), len(self.criterios))

    def public(self) -> dict[str, Any]:
        cumplidos, total = self.progreso
        return {
            "id": self.id,
            "session_id": self.session_id,
            "descripcion": self.descripcion,
            "status": self.status,
            "criterios": [c.public() for c in self.criterios],
            "progreso": {"cumplidos": cumplidos, "total": total},
            "turnos_totales": self.turnos_totales,
            "turnos_sin_avance": self.turnos_sin_avance,
            "motivo_fin": self.motivo_fin,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _bucle_sin_cambios(task_cost: TaskCost | dict[str, Any] | None) -> bool:
    """¿``task_cost`` (de ``ide_costos.analizar_tarea``) trae al menos un
    bucle donde NINGÚN tramo tuvo cambios reales?

    Ese es el patrón exacto del incidente que motivó ``ide_costos.py``:
    herramienta repetida sin que nada avance. Un bucle CON cambios (p. ej.
    "editar, correr test, editar, correr test" mientras el archivo sigue
    cambiando) no cuenta como estancamiento -- puede ser iteración legítima.

    Acepta tanto el ``TaskCost`` en vivo como su ``.resumen()`` ya
    serializado, porque quien integre este freno puede tener a mano
    cualquiera de los dos según de dónde venga el dato.
    """
    if task_cost is None:
        return False
    if isinstance(task_cost, TaskCost):
        bucles: list[BucleDetectado | dict[str, Any]] = list(task_cost.bucles)
    else:
        crudos = task_cost.get("bucles")
        bucles = crudos if isinstance(crudos, list) else []
    if not bucles:
        return False
    for bucle in bucles:
        if isinstance(bucle, BucleDetectado):
            sin_cambios = not bucle.hubo_cambios_en_el_tramo
        else:
            sin_cambios = not bool(bucle.get("hubo_cambios_en_el_tramo"))
        if sin_cambios:
            return True
    return False


class GoalStore:
    """Estado de todos los objetivos, vivos y terminados, con un lock único
    (mismo patrón que ``ide_plan.PlanStore``)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._goals: dict[str, Goal] = {}

    # -- lectura --------------------------------------------------------

    def get(self, goal_id: str) -> Goal:
        with self._lock:
            goal = self._goals.get(goal_id)
        if goal is None:
            raise IDEModosError("Objetivo no encontrado.")
        return goal

    def get_active_for_session(self, session_id: str) -> Goal | None:
        with self._lock:
            candidatos = [
                g
                for g in self._goals.values()
                if g.session_id == session_id and g.status in _ESTADOS_ACTIVOS_GOAL
            ]
        if not candidatos:
            return None
        return max(candidatos, key=lambda g: g.created_at)

    def list_for_session(self, session_id: str) -> list[Goal]:
        with self._lock:
            rows = [g for g in self._goals.values() if g.session_id == session_id]
        rows.sort(key=lambda g: g.created_at)
        return rows

    # -- ciclo de vida ----------------------------------------------------

    def set_goal(self, session_id: str, descripcion: str, criterios: list[str]) -> Goal:
        """Define un objetivo nuevo en estado ``activo``.

        Rechaza un segundo objetivo activo por sesión, igual que
        ``PlanStore.propose`` rechaza un segundo plan: dos objetivos vivos a
        la vez es ambiguo -- ¿contra cuál se mide el avance del turno?
        """
        clean_session = _clean_session_id(session_id)
        clean_descripcion = _clean_text(
            descripcion, max_len=MAX_GOAL_CHARS, field_name="La descripción del objetivo"
        )
        clean_criterios = _clean_criterios(criterios)

        with self._lock:
            if self.get_active_for_session(clean_session) is not None:
                raise IDEModosError(
                    "Ya hay un objetivo activo para esta sesión; complétalo o "
                    "cancélalo antes de definir uno nuevo."
                )
            goal = Goal(
                id=str(uuid.uuid4()),
                session_id=clean_session,
                descripcion=clean_descripcion,
                criterios=[
                    Criterio(id=str(uuid.uuid4()), descripcion=texto) for texto in clean_criterios
                ],
            )
            self._goals[goal.id] = goal
            return goal

    def cumplir_criterio(self, goal_id: str, criterio_id: str) -> Goal:
        """Marca un criterio como cumplido. Si con este ya quedan todos
        cumplidos, el objetivo pasa a ``cumplido`` -- la ÚNICA vía por la que
        este módulo declara éxito."""
        with self._lock:
            goal = self.get(goal_id)
            if goal.status != "activo":
                raise IDEModosError(
                    f"No se puede marcar un criterio en un objetivo «{goal.status}»; "
                    "solo mientras está «activo»."
                )
            candidatos = [c for c in goal.criterios if c.id == criterio_id]
            if not candidatos:
                raise IDEModosError("Ese criterio no pertenece a este objetivo.")
            candidatos[0].cumplido = True
            goal.turnos_sin_avance = 0
            if all(c.cumplido for c in goal.criterios):
                goal.status = "cumplido"
                goal.motivo_fin = "Todos los criterios de éxito se cumplieron."
            goal.updated_at = _now()
            return goal

    def registrar_turno(
        self,
        goal_id: str,
        *,
        criterios_cumplidos_este_turno: int = 0,
        task_cost: TaskCost | dict[str, Any] | None = None,
    ) -> Goal:
        """El freno: se llama una vez por cada turno del agente sobre este
        objetivo, haya cerrado un criterio o no.

        Dos formas de detenerse, en este orden:
        1. Bucle sin cambios en ESTE turno (vía ``ide_costos``): se detiene
           de inmediato, sin esperar ``MAX_TURNOS_SIN_AVANCE`` -- un turno
           que se enrolla solo ya es la señal completa, no hace falta ver
           varios turnos para confirmarlo.
        2. Ningún criterio cerrado en ``MAX_TURNOS_SIN_AVANCE`` turnos
           seguidos, aunque cada turno individual no haya bucleado -- el
           agente puede estar "trabajando" sin acercarse nunca a un criterio.
        """
        with self._lock:
            goal = self.get(goal_id)
            if goal.status != "activo":
                raise IDEModosError(
                    f"No se puede registrar un turno en un objetivo «{goal.status}»; "
                    "solo mientras está «activo»."
                )
            goal.turnos_totales += 1

            if _bucle_sin_cambios(task_cost):
                goal.status = "estancado"
                goal.motivo_fin = (
                    "ide_costos detectó una herramienta repetida sin cambios reales "
                    "en este turno."
                )
                goal.updated_at = _now()
                return goal

            if criterios_cumplidos_este_turno > 0:
                goal.turnos_sin_avance = 0
            else:
                goal.turnos_sin_avance += 1
                if goal.turnos_sin_avance >= MAX_TURNOS_SIN_AVANCE:
                    goal.status = "estancado"
                    goal.motivo_fin = (
                        f"{goal.turnos_sin_avance} turnos seguidos sin cerrar ningún "
                        "criterio de éxito."
                    )
            goal.updated_at = _now()
            return goal

    def cancel(self, goal_id: str, reason: str | None = None) -> Goal:
        with self._lock:
            goal = self.get(goal_id)
            if goal.status not in _ESTADOS_ACTIVOS_GOAL:
                raise IDEModosError(
                    f"No se puede cancelar un objetivo «{goal.status}»; ya es terminal."
                )
            goal.status = "cancelado"
            goal.motivo_fin = reason.strip() if isinstance(reason, str) and reason.strip() else None
            goal.updated_at = _now()
            return goal


# =============================================================================
# 2. /effort -- nivel de razonamiento, con presupuesto de tokens acorde
# =============================================================================

# Piso documentado en la regla ``presupuesto_de_razonamiento`` de
# ``config/modelos.yml``: al menos 200 tokens de más sobre el contenido
# esperado, medido con una respuesta de dos palabras que gastó 65 tokens de
# razonamiento -- por debajo de eso la respuesta llega VACÍA y se cobra
# igual. Los niveles más altos amplían ese piso porque empujar más
# razonamiento consume más ``reasoning_content`` antes de la primera
# palabra de ``content``.
RESERVA_MINIMA_TOKENS = 200

# "alto" preserva el comportamiento histórico: antes de que existiera
# EsfuerzoStore, ide_workers_agent.py mandaba reasoning_effort="high" fijo a
# CADA turno. Una sesión nueva que nunca toca /effort no debe notar ningún
# cambio -- si esto cae a un nivel más bajo, toda sesión que no pida esfuerzo
# explícito pierde razonamiento en silencio.
NIVEL_POR_DEFECTO = "alto"
ORDEN_NIVELES: tuple[str, ...] = ("bajo", "medio", "alto")


@dataclass(frozen=True)
class NivelEsfuerzo:
    """Un nivel de ``/effort``: qué ``reasoning_effort`` se le pide al
    proveedor y cuántos tokens de más hay que reservarle a ``max_tokens``
    para que ese razonamiento no se coma toda la respuesta."""

    nombre: str
    reasoning_effort: str
    reserva_tokens: int


NIVELES_ESFUERZO: dict[str, NivelEsfuerzo] = {
    "bajo": NivelEsfuerzo("bajo", "low", RESERVA_MINIMA_TOKENS),
    "medio": NivelEsfuerzo("medio", "medium", 800),
    "alto": NivelEsfuerzo("alto", "high", 2000),
}


def resolver_nivel_esfuerzo(nombre: str | None) -> NivelEsfuerzo:
    """Normaliza y valida un nombre de nivel. ``None`` o vacío cae al
    nivel por defecto en vez de lanzar -- pensado para el caller que todavía
    no fijó ninguno explícitamente."""
    if nombre is None or not str(nombre).strip():
        return NIVELES_ESFUERZO[NIVEL_POR_DEFECTO]
    clave = str(nombre).strip().casefold()
    nivel = NIVELES_ESFUERZO.get(clave)
    if nivel is None:
        disponibles = ", ".join(ORDEN_NIVELES)
        raise IDEModosError(
            f"Nivel de esfuerzo desconocido: «{nombre}». Usa uno de: {disponibles}."
        )
    return nivel


def _techo_contexto_perfil(perfil: str, ruta_yaml: Path | str | None = None) -> int | None:
    """Techo de tokens leído de ``perfiles.<perfil>.contexto_max_tokens`` en
    ``config/modelos.yml`` (reutiliza el loader de ``edecan_llm.task_router``,
    no vuelve a parsear YAML). ``None`` si el perfil no está declarado --
    nunca se inventa un techo."""
    config = cargar_configuracion_modelos(ruta_yaml)
    perfiles = config.get("perfiles")
    if not isinstance(perfiles, dict):
        return None
    perfil_cfg = perfiles.get(perfil)
    if not isinstance(perfil_cfg, dict):
        return None
    techo = perfil_cfg.get("contexto_max_tokens")
    if isinstance(techo, bool) or not isinstance(techo, (int, float)):
        return None
    return int(techo)


def max_tokens_para_esfuerzo(
    max_tokens_actual: int,
    nivel: str | None,
    *,
    perfil: str = "ingenieria_software",
    ruta_yaml: Path | str | None = None,
) -> int:
    """Calcula el ``max_tokens`` a pasarle a ``CompletionRequest`` para que el
    nivel de esfuerzo pedido no devuelva contenido vacío.

    Nunca reduce lo que ya se pedía: el resultado es ``max_tokens_actual``
    más la reserva de razonamiento del nivel, recortado (nunca aumentado) al
    techo de contexto del perfil si ``config/modelos.yml`` lo declara. Subir
    de nivel sin subir esto es exactamente el bug que describe la regla
    ``presupuesto_de_razonamiento``.
    """
    if not isinstance(max_tokens_actual, int) or max_tokens_actual <= 0:
        raise IDEModosError("max_tokens_actual debe ser un entero positivo.")
    nivel_obj = resolver_nivel_esfuerzo(nivel)
    presupuesto = max_tokens_actual + nivel_obj.reserva_tokens
    techo = _techo_contexto_perfil(perfil, ruta_yaml)
    if techo is not None and techo > 0:
        presupuesto = min(presupuesto, techo)
    return presupuesto


class EsfuerzoStore:
    """Nivel de esfuerzo vigente por sesión. Sin entrada explícita, toda
    sesión parte de ``NIVEL_POR_DEFECTO`` -- así ``obtener`` nunca falla."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._niveles: dict[str, str] = {}

    def obtener(self, session_id: str) -> NivelEsfuerzo:
        clean_session = _clean_session_id(session_id)
        with self._lock:
            nombre = self._niveles.get(clean_session, NIVEL_POR_DEFECTO)
        return NIVELES_ESFUERZO[nombre]

    def fijar(self, session_id: str, nivel: str) -> NivelEsfuerzo:
        clean_session = _clean_session_id(session_id)
        nivel_obj = resolver_nivel_esfuerzo(nivel)
        with self._lock:
            self._niveles[clean_session] = nivel_obj.nombre
        return nivel_obj

    def presupuesto(
        self,
        session_id: str,
        max_tokens_actual: int,
        *,
        perfil: str = "ingenieria_software",
        ruta_yaml: Path | str | None = None,
    ) -> int:
        """Atajo: ``max_tokens_para_esfuerzo`` con el nivel vigente de
        ``session_id``."""
        nivel_obj = self.obtener(session_id)
        return max_tokens_para_esfuerzo(
            max_tokens_actual, nivel_obj.nombre, perfil=perfil, ruta_yaml=ruta_yaml
        )


# =============================================================================
# 3. /plan como modo -- interruptor de sesión: "sin tocar archivos"
# =============================================================================

# Herramientas que MUTAN el workspace, tal como las declara
# ``ide_workers_agent.TOOLS`` hoy. Este módulo no importa ese archivo a
# propósito (mismo criterio que ``ide_costos.py``: no acoplarse a un módulo
# activo de otra corrida en paralelo); si esa lista de herramientas cambia,
# quien integre este freno mantiene esta constante sincronizada.
# ``ejecutar_comando`` se incluye junto a las dos obvias porque un comando
# arbitrario (``sed -i``, ``rm``, ``git commit``) muta archivos igual que
# ``escribir_archivo``/``editar_archivo`` aunque no sea su nombre.
HERRAMIENTAS_MUTANTES = frozenset({"escribir_archivo", "editar_archivo", "ejecutar_comando"})

MAX_MOTIVO_CHARS = 300


@dataclass
class ModoPlanificacion:
    """El interruptor de una sesión: activo o no, y por qué."""

    session_id: str
    activo: bool = False
    motivo: str | None = None
    updated_at: str = field(default_factory=_now)

    def public(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "activo": self.activo,
            "motivo": self.motivo,
            "updated_at": self.updated_at,
        }


class ModoPlanificacionStore:
    """Estado del interruptor "modo planificación" por sesión."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._modos: dict[str, ModoPlanificacion] = {}

    def _obtener_o_crear(self, session_id: str) -> ModoPlanificacion:
        modo = self._modos.get(session_id)
        if modo is None:
            modo = ModoPlanificacion(session_id=session_id)
            self._modos[session_id] = modo
        return modo

    def esta_activo(self, session_id: str) -> bool:
        clean_session = _clean_session_id(session_id)
        with self._lock:
            modo = self._modos.get(clean_session)
        return bool(modo and modo.activo)

    def activar(self, session_id: str, motivo: str | None = None) -> ModoPlanificacion:
        """Prende el interruptor (idempotente: activarlo dos veces solo
        actualiza el motivo)."""
        clean_session = _clean_session_id(session_id)
        clean_motivo = None
        if motivo is not None:
            clean_motivo = _clean_text(motivo, max_len=MAX_MOTIVO_CHARS, field_name="El motivo")
        with self._lock:
            modo = self._obtener_o_crear(clean_session)
            modo.activo = True
            modo.motivo = clean_motivo
            modo.updated_at = _now()
            return modo

    def salir(self, session_id: str) -> ModoPlanificacion:
        """Apaga el interruptor (idempotente: salir sin haber entrado no
        lanza, solo confirma que está apagado)."""
        clean_session = _clean_session_id(session_id)
        with self._lock:
            modo = self._obtener_o_crear(clean_session)
            modo.activo = False
            modo.motivo = None
            modo.updated_at = _now()
            return modo

    def verificar_accion_permitida(self, session_id: str, herramienta: str) -> None:
        """Lanza ``IDEModosError`` si el modo está activo y ``herramienta``
        muta el workspace. Herramientas de solo lectura (``leer_archivo``,
        ``listar_archivos``, ``buscar_en_archivos``, ``buscar_web``, o
        cualquier nombre que no esté en ``HERRAMIENTAS_MUTANTES``) siempre
        pasan: el modo planificación es para explorar, no para bloquear
        todo."""
        if self.esta_activo(session_id) and herramienta in HERRAMIENTAS_MUTANTES:
            raise IDEModosError(
                f"Modo planificación activo: '{herramienta}' no puede ejecutarse. "
                "Sal del modo (/plan de nuevo, o aprueba el plan) antes de escribir."
            )

    def sincronizar_con_plan(self, plan: Plan | None, session_id: str) -> ModoPlanificacion:
        """Deriva el interruptor del estado de un ``ide_plan.Plan`` real, sin
        reimplementar su máquina de estados.

        - ``plan.status == "proposed"``: el usuario todavía no aprobó nada
          -> modo activo, no se debe escribir.
        - Cualquier otro caso (``executing``, terminal, o ``None`` porque no
          hay plan vivo) -> modo apagado: ya sea porque se aprobó y toca
          ejecutar, o porque nunca hizo falta un plan de pasos para esta
          tarea.
        """
        if plan is not None and plan.status == "proposed":
            return self.activar(session_id, motivo="plan propuesto pendiente de aprobación")
        return self.salir(session_id)


# =============================================================================
# 4. Modos del agente del IDE -- el gate real: qué se puede ejecutar en cada
#    uno de los cuatro modos (manual/aceptar_ediciones/plan/auto).
#
# Ver el punto 4 del docstring del módulo para el porqué completo y las tres
# reglas de seguridad. Este bloque es deliberadamente el más simple del
# archivo: una tabla y dos enums, para que se pueda leer y auditar de un
# vistazo -- es la pieza que decide si algo real se ejecuta o no.
# =============================================================================


class ModoAgente(StrEnum):
    """Los cuatro modos de trabajo del agente del IDE. ``StrEnum`` para que
    ``ModoAgente.MANUAL.value == "manual"`` sirva tal cual en JSON sin un
    serializador aparte (mismo truco que usan los ``Literal`` de esquemas
    Pydantic en ``edecan_api``)."""

    MANUAL = "manual"
    ACEPTAR_EDICIONES = "aceptar_ediciones"
    PLAN = "plan"
    AUTO = "auto"


def _parse_modo(valor: Any) -> ModoAgente:
    """Normaliza y valida un modo recibido como texto (p. ej. desde el body
    HTTP de ``PUT /v1/ide/agents/{id}/modo``). Nunca inventa un modo
    plausible ante un valor raro -- lanza con la lista de los válidos."""
    if isinstance(valor, ModoAgente):
        return valor
    if not isinstance(valor, str) or not valor.strip():
        raise IDEModosError("El modo no puede estar vacío.")
    clave = valor.strip().casefold()
    for candidato in ModoAgente:
        if candidato.value == clave:
            return candidato
    disponibles = ", ".join(m.value for m in ModoAgente)
    raise IDEModosError(f"Modo desconocido: «{valor}». Usa uno de: {disponibles}.")


class ClaseHerramienta(StrEnum):
    """Qué tan arriesgada es una herramienta nativa, de menos a más:
    ``LECTURA`` no toca nada, ``EDICION``/``COMANDO`` mutan el workspace de
    dos formas distintas (por eso ``aceptar_ediciones`` puede tratarlas
    distinto), y ``PELIGROSA`` es irreversible o alcanza fuera del
    workspace."""

    LECTURA = "lectura"
    EDICION = "edicion"
    COMANDO = "comando"
    PELIGROSA = "peligrosa"


# Clasificación por nombre de las 13 herramientas propias de
# ``ide_workers_agent.TOOLS`` más las 2 de presentación de ``ide_bloques.TOOLS``
# (``mostrar_tabla``/``mostrar_grafica``, que ``ide_workers_agent`` mezcla en su
# mismo catálogo -- ver su ``*TOOLS_BLOQUES``). Igual que
# ``HERRAMIENTAS_MUTANTES`` más arriba, este módulo NO importa
# ``ide_workers_agent`` (evita acoplarse a un módulo que otra corrida puede
# estar tocando en paralelo); si esa lista de herramientas cambia, quien la
# toque mantiene esta clasificación sincronizada -- y si se olvida, la regla
# de seguridad #1 (``clasificar_herramienta`` más abajo) hace que la
# herramienta nueva pida aprobación en vez de colarse como lectura.
HERRAMIENTAS_EDICION = frozenset({"escribir_archivo", "editar_archivo"})
HERRAMIENTAS_COMANDO = frozenset({"ejecutar_comando"})
HERRAMIENTAS_PELIGROSAS = frozenset(
    {"auditar_seguridad_proyecto", "ejecutar_pentestgpt_autorizado"}
)
HERRAMIENTAS_LECTURA = frozenset(
    {
        "listar_archivos",
        "leer_archivo",
        "buscar_en_archivos",
        "buscar_web",
        "buscar_semanticamente",
        "recordar_nota_proyecto",
        "verificar",
        "proponer_plan",
        "mostrar_tabla",
        "mostrar_grafica",
    }
)


def clasificar_herramienta(nombre: str) -> ClaseHerramienta:
    """Clasifica una herramienta nativa por nombre.

    Regla de seguridad #1 (falla cerrado): un ``nombre`` que no está en
    ninguno de los cuatro conjuntos de arriba -- una herramienta nueva que
    alguien olvidó clasificar, o directamente un nombre inventado -- cae en
    ``PELIGROSA``, la clase más restrictiva. Nunca cae en ``LECTURA``: eso
    dejaría pasar en silencio justo lo que este módulo existe para frenar.
    """
    if nombre in HERRAMIENTAS_PELIGROSAS:
        return ClaseHerramienta.PELIGROSA
    if nombre in HERRAMIENTAS_COMANDO:
        return ClaseHerramienta.COMANDO
    if nombre in HERRAMIENTAS_EDICION:
        return ClaseHerramienta.EDICION
    if nombre in HERRAMIENTAS_LECTURA:
        return ClaseHerramienta.LECTURA
    return ClaseHerramienta.PELIGROSA


Decision = Literal["permitir", "pedir_aprobacion", "bloquear"]

# El modo por defecto de una sesión que nunca lo fijó explícitamente. Tiene
# que ser el más conservador (regla de seguridad #3): una sesión que nadie
# tocó no debería quedar habilitada a mutar el workspace sin más.
MODO_POR_DEFECTO = ModoAgente.MANUAL

# La tabla de decisión completa: 4 modos x 4 clases = 16 casos, todos
# explícitos (ninguno se deriva de otro) para que se pueda leer y auditar de
# un vistazo -- es la pieza de seguridad real de este módulo.
#
#                        lectura      edicion            comando            peligrosa
# manual              -> permitir     pedir_aprobacion   pedir_aprobacion   pedir_aprobacion
# aceptar_ediciones   -> permitir     permitir            pedir_aprobacion   pedir_aprobacion
# plan                -> permitir     bloquear            bloquear           pedir_aprobacion
# auto                -> permitir     permitir            permitir           pedir_aprobacion
#
# Por qué cada fila, en las palabras del encargo:
# - manual: "nada que mute se ejecuta sin que la persona lo apruebe" ->
#   edicion/comando/peligrosa piden aprobación; lectura no muta nada, pasa.
# - aceptar_ediciones: "escribir y editar van solos; ejecutar comandos pide
#   permiso" -> edicion permite, comando pide aprobación.
# - plan: "no toca archivos: solo lee y propone" -> edicion/comando se
#   BLOQUEAN (no hay nada que aprobar porque este modo no es "más despacio",
#   es "no ahora"); peligrosa igual pide aprobación (regla #3).
# - auto: "trabaja solo y para únicamente en lo irreversible" -> todo
#   permitido salvo peligrosa, que sigue pidiendo aprobación siempre.
_TABLA_DECISION: dict[tuple[ModoAgente, ClaseHerramienta], Decision] = {
    (ModoAgente.MANUAL, ClaseHerramienta.LECTURA): "permitir",
    (ModoAgente.MANUAL, ClaseHerramienta.EDICION): "pedir_aprobacion",
    (ModoAgente.MANUAL, ClaseHerramienta.COMANDO): "pedir_aprobacion",
    (ModoAgente.MANUAL, ClaseHerramienta.PELIGROSA): "pedir_aprobacion",
    (ModoAgente.ACEPTAR_EDICIONES, ClaseHerramienta.LECTURA): "permitir",
    (ModoAgente.ACEPTAR_EDICIONES, ClaseHerramienta.EDICION): "permitir",
    (ModoAgente.ACEPTAR_EDICIONES, ClaseHerramienta.COMANDO): "pedir_aprobacion",
    (ModoAgente.ACEPTAR_EDICIONES, ClaseHerramienta.PELIGROSA): "pedir_aprobacion",
    (ModoAgente.PLAN, ClaseHerramienta.LECTURA): "permitir",
    (ModoAgente.PLAN, ClaseHerramienta.EDICION): "bloquear",
    (ModoAgente.PLAN, ClaseHerramienta.COMANDO): "bloquear",
    (ModoAgente.PLAN, ClaseHerramienta.PELIGROSA): "pedir_aprobacion",
    (ModoAgente.AUTO, ClaseHerramienta.LECTURA): "permitir",
    (ModoAgente.AUTO, ClaseHerramienta.EDICION): "permitir",
    (ModoAgente.AUTO, ClaseHerramienta.COMANDO): "permitir",
    (ModoAgente.AUTO, ClaseHerramienta.PELIGROSA): "pedir_aprobacion",
}

# La tabla cubre exactamente los 4x4 casos posibles -- ningún ``.get`` con
# valor por defecto: si algún día se agrega un modo o una clase nueva y esta
# tabla no se actualiza junto con ellos, se prefiere un ``KeyError`` ruidoso
# en desarrollo a que ``decidir`` invente silenciosamente una decisión.
assert len(_TABLA_DECISION) == len(ModoAgente) * len(ClaseHerramienta)


def decidir(modo: ModoAgente, clase: ClaseHerramienta) -> Decision:
    """Función pura: dado el modo vigente de la sesión y la clase de la
    herramienta que se quiere ejecutar, ¿qué corresponde hacer?

    - ``"permitir"``: se ejecuta tal cual, sin pausar el turno.
    - ``"pedir_aprobacion"``: el turno se detiene a esperar una confirmación
      humana real (mismo patrón que ``invoke_mcp`` en ``ide_sessions.py``) y
      solo sigue si la persona dice que sí.
    - ``"bloquear"``: no se ejecuta y no se pausa nada -- el modo vigente
      directamente no permite esto ahora mismo (p. ej. modo ``plan``, que "no
      toca archivos"). El modelo recibe un resultado de herramienta que se lo
      explica, nunca una excepción que tumbe el turno.
    """
    return _TABLA_DECISION[(modo, clase)]


class ModoAgenteStore:
    """El modo vigente (uno de los cuatro) por sesión -- lo que ``decidir``
    consulta antes de dejar pasar una herramienta nativa. Mismo patrón de
    lock único y almacenamiento en memoria por ``session_id`` que el resto de
    los stores de este módulo (``GoalStore``, ``EsfuerzoStore``,
    ``ModoPlanificacionStore``).

    Regla de seguridad #2: esta clase no tiene ningún método que el modelo
    pueda invocar como herramienta -- ``fijar`` solo lo llama código de
    infraestructura (``ide_runtime.py``, a través de
    ``PUT /v1/ide/agents/{id}/modo``), nunca un despachador de tools. Quien
    integre este store en un lugar nuevo debe mantener esa frontera: si algún
    día alguien expone ``fijar`` como tool, esta regla queda rota aunque el
    código de acá siga intacto.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._modos: dict[str, ModoAgente] = {}
        self._motivos: dict[str, str | None] = {}
        self._updated_at: dict[str, str] = {}

    def obtener(self, session_id: str) -> ModoAgente:
        """El modo vigente, o ``MODO_POR_DEFECTO`` (el más seguro) si esta
        sesión nunca lo fijó explícitamente -- así ``obtener`` nunca falla y
        una sesión nueva arranca conservadora, no permisiva."""
        clean_session = _clean_session_id(session_id)
        with self._lock:
            return self._modos.get(clean_session, MODO_POR_DEFECTO)

    def fijar(
        self, session_id: str, modo: ModoAgente | str, motivo: str | None = None
    ) -> ModoAgente:
        """Fija el modo de una sesión. Lo llama ``ide_runtime.py`` en nombre
        de la persona (nunca una tool del modelo -- ver la regla de
        seguridad #2 en el docstring de la clase)."""
        clean_session = _clean_session_id(session_id)
        modo_obj = _parse_modo(modo)
        clean_motivo = None
        if motivo is not None:
            clean_motivo = _clean_text(motivo, max_len=MAX_MOTIVO_CHARS, field_name="El motivo")
        with self._lock:
            self._modos[clean_session] = modo_obj
            self._motivos[clean_session] = clean_motivo
            self._updated_at[clean_session] = _now()
        return modo_obj

    def public(self, session_id: str) -> dict[str, Any]:
        clean_session = _clean_session_id(session_id)
        with self._lock:
            modo = self._modos.get(clean_session, MODO_POR_DEFECTO)
            motivo = self._motivos.get(clean_session)
            updated_at = self._updated_at.get(clean_session, _now())
        return {
            "session_id": clean_session,
            "modo": modo.value,
            "motivo": motivo,
            "updated_at": updated_at,
        }
