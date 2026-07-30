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
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

NIVEL_POR_DEFECTO = "medio"
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
