"""Plan previo a la ejecución del agente del IDE.

Motivación medida: editar un README le tomó al agente 22 minutos y 53
acciones, y el usuario no se enteró de nada hasta que terminó -- no pudo
corregir el rumbo a mitad de camino. Este módulo NO ejecuta nada: es el
andamiaje de estado que le permite a quien orquesta al agente (hoy
``ide_sessions.py``, mañana lo que sea) intercalar un paso humano entre "el
usuario pidió algo" y "el agente actúa": proponer un plan corto, dejar que el
usuario lo apruebe o lo edite, y solo entonces ejecutar paso a paso con
visibilidad de en cuál va.

Quién llama a qué (para quien integre esto más adelante):
1. Alguien (probablemente el propio LLM, con un prompt tipo "desglosa esta
   tarea en pasos cortos") produce una lista candidata de pasos en texto.
2. ``requires_plan(prompt, candidate_steps)`` decide si esa lista amerita
   mostrarse al usuario para aprobación o si se puede ejecutar derecho. Ver
   su docstring para el criterio exacto y por qué.
3. Si hace falta plan: ``PlanStore.propose(...)`` lo registra en estado
   ``proposed``.
4. El usuario lo edita cero o más veces con ``PlanStore.edit(...)`` (mientras
   siga ``proposed``) y luego lo aprueba con ``PlanStore.approve(...)``, que
   lo pasa a ``executing`` y marca el primer paso ``active``. También puede
   ``reject(...)``.
5. Quien ejecuta llama ``PlanStore.advance(...)`` después de cada paso
   completado (o ``skip_current(...)`` / ``fail(...)`` si algo no sale
   según lo planeado). ``current_step_index`` siempre dice en qué paso va.
6. Al completar el último paso el plan pasa solo a ``completed``. En
   cualquier momento antes de un estado terminal, ``cancel(...)`` corta el
   plan (p. ej. el usuario se arrepiente a mitad de ejecución).

Este módulo es puro estado en memoria protegido por un lock, sin persistir a
disco ni tocar sesiones -- deliberado, para no acoplarse a
``ide_sessions.Session`` (que otro workflow tiene prohibido tocar) ni a cómo
la UI decida guardar/mostrar esto. La única atadura con una sesión es
``session_id`` como string opaco: quien integre puede pasar el id de
``ide_sessions.Session`` o cualquier otro identificador de conversación.
"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Estados en los que un plan sigue "vivo": ocupa el slot de plan activo de su
# sesión y puede seguir recibiendo transiciones (editar, aprobar, avanzar,
# cancelar...). No existe un estado "approved" separado de "executing": ver
# el docstring de ``PlanStore.approve`` sobre por qué se fusionan.
_ACTIVE_STATUSES = frozenset({"proposed", "executing"})
# Estados terminales: el plan ya no cambia y libera el slot de su sesión.
_TERMINAL_STATUSES = frozenset({"completed", "cancelled", "rejected", "failed"})

MAX_STEPS = 12
MAX_STEP_CHARS = 300
MAX_GOAL_CHARS = 2000

# --- Criterio de "¿esto merece un plan?" -----------------------------------
#
# Un plan obligatorio para TODO es tan malo como ninguno: convierte "lee el
# README" en una pregunta de confirmación innecesaria y entrena al usuario a
# aprobar sin leer, que es peor que no preguntar. El criterio de abajo se
# apoya en tres señales, en este orden:
#
# 1. Riesgo declarado por palabras clave (refactor, migración, borrado,
#    arquitectura, seguridad, producción...): si el pedido toca algo así,
#    el costo de un rumbo equivocado es alto aunque el desglose en pasos
#    parezca corto (2 pasos mal dados en un `DROP TABLE` no son "baratos").
#    Este caso gana incluso con pocos pasos candidatos.
# 2. Intención trivial de solo-lectura (lee/muestra/explica/busca/resume...)
#    al INICIO del pedido: aunque el LLM la desglose en varios pasos
#    técnicos, mostrar/leer/explicar no deja rastro que corregir a mitad de
#    camino, así que no amerita interrumpir con una aprobación -- salvo que
#    el desglose sea sospechosamente largo (>=5 pasos), señal de que "lee"
#    es solo el verbo inicial de algo más grande.
# 3. Por defecto: el número de pasos candidatos es el proxy del costo real
#    medido (22 minutos, 53 acciones) -- una tarea que un LLM ya desglosó en
#    3 o más pasos es, en la práctica, la clase de tarea que se salió de
#    cauce en el incidente que motivó esto. 1-2 pasos se ejecutan derecho.
#
# El umbral (3) y las listas de palabras son deliberadamente conservadores:
# ante la duda, mejor preguntar de más en tareas medianas que dejar pasar
# una "refactoriza la autenticación" sin aviso.
MIN_STEPS_FOR_PLAN = 3

_TRIVIAL_INTENT_RE = re.compile(
    r"^\s*(lee|leer|lee[r]?me|muestra|mostrar|explica|explicar|qu[eé] dice|"
    r"qu[eé] dice|resume|resumir|busca|buscar|lista|listar|dime|cu[aá]l|"
    r"cu[aá]les|ense[ñn]a|ense[ñn]ame)\b",
    re.IGNORECASE,
)

_HIGH_RISK_RE = re.compile(
    r"refactor|migra|reescrib|elimina|borra|arquitectur|seguridad|"
    r"autenticaci[oó]n|autorizaci[oó]n|base de datos|esquema|producci[oó]n|"
    r"breaking|credencial|secreto|clave (privada|api)",
    re.IGNORECASE,
)


def requires_plan(prompt: str, candidate_steps: list[str]) -> bool:
    """Decide si ``candidate_steps`` (ya desglosados por quien sea) amerita
    mostrarse como plan para aprobación, o si se puede ejecutar derecho.

    Ver el criterio completo arriba, junto a ``MIN_STEPS_FOR_PLAN``. Nunca
    lanza: ante datos vacíos o basura, responde ``False`` (no bloquear por
    un desglose que no llegó).
    """

    steps = [step.strip() for step in candidate_steps if isinstance(step, str) and step.strip()]
    text = prompt if isinstance(prompt, str) else ""

    if _HIGH_RISK_RE.search(text):
        return len(steps) >= 2

    if len(steps) <= 1:
        return False

    if _TRIVIAL_INTENT_RE.match(text) and len(steps) < 5:
        return False

    return len(steps) >= MIN_STEPS_FOR_PLAN


class IDEPlanError(ValueError):
    """Operación de plan inválida: plan inexistente, pasos inválidos o
    transición de estado no permitida (p. ej. editar un plan ya aprobado)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_text(value: Any, *, max_len: int, field_name: str) -> str:
    if not isinstance(value, str):
        raise IDEPlanError(f"{field_name} debe ser texto.")
    cleaned = value.strip()
    if not cleaned:
        raise IDEPlanError(f"{field_name} no puede estar vacío.")
    if len(cleaned) > max_len:
        raise IDEPlanError(f"{field_name} supera los {max_len} caracteres.")
    if any(ord(char) < 32 and char not in "\n\t" for char in cleaned):
        raise IDEPlanError(f"{field_name} contiene caracteres de control inválidos.")
    return cleaned


def _clean_steps(raw_steps: list[Any]) -> list[str]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise IDEPlanError("El plan necesita al menos un paso.")
    if len(raw_steps) > MAX_STEPS:
        raise IDEPlanError(f"El plan no puede tener más de {MAX_STEPS} pasos.")
    return [
        _clean_text(step, max_len=MAX_STEP_CHARS, field_name=f"El paso {index + 1}")
        for index, step in enumerate(raw_steps)
    ]


@dataclass
class PlanStep:
    """Un paso del plan. ``status`` es la máquina de estados mínima: un paso
    nace ``pending``, el plan lo marca ``active`` cuando le toca, y termina
    en ``done`` o ``skipped``. ``note`` es texto libre opcional que deja
    quien ejecuta (p. ej. "se hizo distinto porque el archivo no existía")."""

    id: str
    description: str
    status: str = "pending"
    note: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "note": self.note,
        }


@dataclass
class Plan:
    """Un plan completo: la meta original, sus pasos en orden, y en cuál va.

    ``current_step_index`` es la respuesta directa a "durante la ejecución
    debe saberse en qué paso va": ``None`` mientras no hay un paso activo
    (plan recién propuesto/aprobado, o ya terminó), y el índice del paso
    ``active`` en ``steps`` mientras ``status == "executing"``.
    """

    id: str
    session_id: str
    goal: str
    steps: list[PlanStep]
    status: str = "proposed"
    current_step_index: int | None = None
    error: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @property
    def current_step(self) -> PlanStep | None:
        if self.current_step_index is None:
            return None
        return self.steps[self.current_step_index]

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "goal": self.goal,
            "status": self.status,
            "current_step_index": self.current_step_index,
            "steps": [step.public() for step in self.steps],
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class PlanStore:
    """Estado de todos los planes vivos y terminados, con un lock único.

    Un ``RLock`` compartido (mismo patrón que ``SessionManager``) es
    suficiente: los planes son de bajo volumen (uno activo por conversación
    como mucho, ver ``propose``) y las transiciones son operaciones cortas
    en memoria, no hay I/O adentro del lock.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._plans: dict[str, Plan] = {}

    # -- lectura --------------------------------------------------------

    def get(self, plan_id: str) -> Plan:
        with self._lock:
            plan = self._plans.get(plan_id)
        if plan is None:
            raise IDEPlanError("Plan no encontrado.")
        return plan

    def get_active_for_session(self, session_id: str) -> Plan | None:
        """El plan vivo (no terminal) más reciente de ``session_id``, si hay uno."""
        with self._lock:
            candidates = [
                plan
                for plan in self._plans.values()
                if plan.session_id == session_id and plan.status in _ACTIVE_STATUSES
            ]
        if not candidates:
            return None
        return max(candidates, key=lambda plan: plan.created_at)

    def list_for_session(self, session_id: str) -> list[Plan]:
        with self._lock:
            rows = [plan for plan in self._plans.values() if plan.session_id == session_id]
        rows.sort(key=lambda plan: plan.created_at)
        return rows

    # -- ciclo de vida ----------------------------------------------------

    def propose(self, session_id: str, goal: str, steps: list[str]) -> Plan:
        """Registra un plan nuevo en estado ``proposed``.

        Rechaza proponer un segundo plan mientras ya hay uno vivo
        (``proposed``/``approved``/``executing``) para la misma sesión: dos
        planes activos a la vez es ambiguo -- ¿cuál aprueba el usuario?--,
        así que quien orqueste debe resolver (aprobar/rechazar/cancelar) el
        anterior antes de proponer otro.
        """

        if not isinstance(session_id, str) or not session_id.strip():
            raise IDEPlanError("session_id no puede estar vacío.")
        clean_goal = _clean_text(goal, max_len=MAX_GOAL_CHARS, field_name="La meta del plan")
        clean_steps = _clean_steps(steps)

        with self._lock:
            if self.get_active_for_session(session_id) is not None:
                raise IDEPlanError(
                    "Ya hay un plan activo para esta sesión; resuélvelo antes de proponer otro."
                )
            plan = Plan(
                id=str(uuid.uuid4()),
                session_id=session_id,
                goal=clean_goal,
                steps=[PlanStep(id=str(uuid.uuid4()), description=text) for text in clean_steps],
            )
            self._plans[plan.id] = plan
            return plan

    def edit(self, plan_id: str, steps: list[str]) -> Plan:
        """Reemplaza los pasos de un plan que el usuario todavía no aprobó.

        Solo mientras ``status == "proposed"``: una vez aprobado el plan ya
        pudo haber avanzado pasos (con su ``note`` de ejecución real), y
        reescribir la lista debajo de eso perdería ese registro. Si hace
        falta cambiar de rumbo a mitad de ejecución, la vía es cancelar y
        proponer un plan nuevo, no editar el viejo.
        """

        clean_steps = _clean_steps(steps)
        with self._lock:
            plan = self.get(plan_id)
            if plan.status != "proposed":
                raise IDEPlanError(
                    f"No se puede editar un plan en estado «{plan.status}»; solo mientras "
                    "está «proposed»."
                )
            plan.steps = [
                PlanStep(id=str(uuid.uuid4()), description=text) for text in clean_steps
            ]
            plan.updated_at = _now()
            return plan

    def approve(self, plan_id: str) -> Plan:
        """``proposed`` -> ``executing``, con el primer paso marcado ``active``.

        Se fusiona "aprobar" y "empezar a ejecutar" en una sola transición a
        propósito: no hay un estado intermedio útil entre "el usuario dijo
        que sí" y "el agente ya puede actuar" -- separar los dos solo
        agregaría una llamada más sin cambiar el comportamiento observable.
        """

        with self._lock:
            plan = self.get(plan_id)
            if plan.status != "proposed":
                raise IDEPlanError(
                    f"No se puede aprobar un plan en estado «{plan.status}»; solo mientras "
                    "está «proposed»."
                )
            plan.status = "executing"
            plan.current_step_index = 0
            plan.steps[0].status = "active"
            plan.updated_at = _now()
            return plan

    def reject(self, plan_id: str, reason: str | None = None) -> Plan:
        """``proposed`` -> ``rejected``. El usuario dijo que no; nada se ejecuta."""

        with self._lock:
            plan = self.get(plan_id)
            if plan.status != "proposed":
                raise IDEPlanError(
                    f"No se puede rechazar un plan en estado «{plan.status}»; solo mientras "
                    "está «proposed»."
                )
            plan.status = "rejected"
            plan.error = reason.strip() if isinstance(reason, str) and reason.strip() else None
            plan.updated_at = _now()
            return plan

    def advance(self, plan_id: str, *, note: str | None = None) -> Plan:
        """Cierra el paso activo como ``done`` y activa el siguiente.

        Si no queda siguiente, el plan pasa a ``completed`` y
        ``current_step_index`` vuelve a ``None`` (ya no hay "paso en curso").
        """

        return self._close_current_step(plan_id, step_status="done", note=note)

    def skip_current(self, plan_id: str, *, note: str | None = None) -> Plan:
        """Igual que ``advance``, pero el paso queda ``skipped`` en vez de ``done``
        (el agente decidió que ese paso ya no aplicaba, sin haberlo hecho)."""

        return self._close_current_step(plan_id, step_status="skipped", note=note)

    def _close_current_step(self, plan_id: str, *, step_status: str, note: str | None) -> Plan:
        with self._lock:
            plan = self.get(plan_id)
            if plan.status != "executing":
                raise IDEPlanError(
                    f"No se puede avanzar un plan en estado «{plan.status}»; solo mientras "
                    "está «executing»."
                )
            current = plan.current_step
            assert current is not None, "executing siempre tiene un paso activo"
            current.status = step_status
            if isinstance(note, str) and note.strip():
                current.note = note.strip()[:MAX_STEP_CHARS]

            next_index = plan.current_step_index + 1  # type: ignore[operator]
            if next_index >= len(plan.steps):
                plan.status = "completed"
                plan.current_step_index = None
            else:
                plan.current_step_index = next_index
                plan.steps[next_index].status = "active"
            plan.updated_at = _now()
            return plan

    def fail(self, plan_id: str, reason: str) -> Plan:
        """Corta la ejecución por un error real (no un rumbo cambiado por el
        usuario, para eso está ``cancel``). Deja ``current_step_index`` tal
        como estaba: es útil saber en qué paso se rompió."""

        clean_reason = _clean_text(reason, max_len=MAX_STEP_CHARS, field_name="El motivo de fallo")
        with self._lock:
            plan = self.get(plan_id)
            if plan.status != "executing":
                raise IDEPlanError(
                    f"No se puede marcar como fallido un plan en estado «{plan.status}»; solo "
                    "mientras está «executing»."
                )
            plan.status = "failed"
            plan.error = clean_reason
            plan.updated_at = _now()
            return plan

    def cancel(self, plan_id: str, reason: str | None = None) -> Plan:
        """El usuario corta el plan a mitad de camino, en cualquier estado no
        terminal (incluso antes de aprobarlo). El paso activo, si lo hay,
        queda como estaba -- no se reescribe a "skipped": cancelar el plan
        entero es una decisión distinta de saltar un paso puntual."""

        with self._lock:
            plan = self.get(plan_id)
            if plan.status not in _ACTIVE_STATUSES:
                raise IDEPlanError(
                    f"No se puede cancelar un plan en estado «{plan.status}»; ya es terminal."
                )
            plan.status = "cancelled"
            plan.error = reason.strip() if isinstance(reason, str) and reason.strip() else None
            plan.updated_at = _now()
            return plan
