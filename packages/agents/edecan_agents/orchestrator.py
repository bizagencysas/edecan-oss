"""`Orchestrator` — planifica y ejecuta una misión multi-agente (`ARCHITECTURE.md`
§10.7, `ROADMAP_V2.md` §7.4, §7.9; dependencias/paralelismo/replan/timeout en
`WP-V5-05`; `started_at`/`finished_at` por paso en `WP-V6-10`, ver esa sección
al final de este docstring).

Dos fases separadas, ambas expuestas como métodos públicos:

- `plan(objetivo, flags, settings)`: UNA llamada al LLM (alias `"profundo"`)
  pidiéndole un plan estructurado en JSON. Parseo tolerante (acepta el JSON
  envuelto en prosa/markdown, ver `_extraer_json`), valida que cada `agente`
  sea uno de los perfiles `disponible=True` (`profiles.IMPLEMENTED_AGENT_KEYS`)
  — si no, lo reasigna a `research` en vez de descartar el paso —, resuelve/
  valida el campo OPCIONAL `depende_de` de cada paso (ver sección
  "Dependencias entre pasos" abajo), y trunca al presupuesto de pasos. Si el
  LLM no da JSON usable (falla la llamada, no hay bloque `{...}`/`[...]`
  balanceado, o `pasos` queda vacío tras filtrar), cae a un plan de UN paso:
  delegar el objetivo completo a `research`. Nunca lanza: cualquier excepción
  se atrapa y se trata igual que "el LLM no dio JSON válido".
- `run(mission, deps)`: ejecuta `mission.plan` por OLAS (`WP-V5-05`: grupos de
  pasos cuyas dependencias ya terminaron se ejecutan EN PARALELO vía
  `asyncio.gather`, limitados por `MISSIONS_PARALLEL_MAX`) en vez de un paso a
  la vez. Por paso: resuelve el `AgentProfile`, arma un `edecan_core.agent.Agent`
  nuevo con un `RestrictedRegistry` recortado a `allowed_tools` del perfil y
  con el `model_alias` de ese mismo perfil, y consume `Agent.run_turn`
  acumulando los `text_delta` como resultado del paso. El resultado de cada
  paso completado se antepone como *historial sintético* (`ChatMessage`
  user=instrucción / assistant=resultado) SOLO a los pasos que dependen de él
  (ver "Dependencias entre pasos") — así un paso "ve" lo que produjeron
  aquellos de los que depende, aunque los haya ejecutado un `Agent`/perfil
  distinto. Al terminar todos los pasos, UNA llamada más al LLM sintetiza el
  historial completo en la respuesta final (`agent_missions.resultado`,
  status `done`). Si un paso termina en error, `run()` intenta REPLANEAR lo
  restante una única vez (ver "Replan acotado" abajo) antes de rendirse.

`run()` nunca toca una `AsyncSession`/tabla directamente: recibe `deps` (ver
`RunDeps` abajo) con callables `save_step`/`save_mission`/`insert_steps`
async — el `Orchestrator` no sabe ni le importa si eso termina en un `UPDATE`/
`INSERT` SQL, un mock de test o cualquier otra cosa (`apps/worker/
edecan_worker/handlers/run_mission.py` es quien construye esos callables
sobre SQL real). Esto es lo que permite testear `run()` end-to-end con fakes
puros, sin Postgres ni importar `edecan_core`/`edecan_db` en los tests
(`ARCHITECTURE.md` §10.1).

## Dependencias entre pasos y ejecución por olas (`WP-V5-05`)

Cada paso del plan (el jsonb `agent_missions.plan` — NO hay migración nueva,
el formato vive en el propio jsonb, ver `apps/worker/edecan_worker/handlers/
run_mission.py`) acepta una clave OPCIONAL `"depende_de"`: una lista de
índices 0-based (posición dentro de la lista `pasos` de ESE MISMO plan — el
primer paso es el índice 0) de pasos ANTERIORES cuyo resultado necesita este
paso. `plan()` la resuelve/valida vía `_resolver_depende_de` ANTES de
persistir el plan (documentado también en el prompt del planificador,
`_planner_system_prompt`); `run()` la vuelve a resolver de forma DEFENSIVA
sobre `mission.plan` tal cual llega (mismo `_resolver_depende_de`) — así un
plan viejo persistido ANTES de `WP-V5-05` (sin la clave en absoluto), o un
plan armado a mano en un test, funciona exactamente igual sin tocar nada.

Reglas de resolución (`_resolver_depende_de`/`_validar_depende_de`):

- **Sin la clave `"depende_de"` en absoluto** (plan viejo/retrocompatible):
  el paso depende de TODOS los pasos anteriores del plan (`list(range(idx))`)
  — reproduce BYTE A BYTE la acumulación total de historial que ya existía
  antes de este campo (cada paso veía el resultado de TODOS los anteriores,
  nunca solo el inmediato), y de paso fuerza una ejecución 100% secuencial
  (un paso por ola) para cualquier plan que nunca use el campo nuevo — cero
  cambio de comportamiento observable para misiones viejas.
- **`"depende_de": []` explícito**: el paso NO depende de nada — puede
  ejecutarse en la primera ola en la que sea elegible (útil para que el
  planificador marque pasos independientes/paralelizables desde el inicio).
- **`"depende_de"` con índices inválidos** (no son enteros, están fuera de
  `[0, total_pasos - 1]`, o son `>= índice propio` — la ÚNICA forma en que
  `depende_de` podría formar un ciclo: como cada índice solo puede apuntar
  hacia atrás en la lista, el grafo resultante es SIEMPRE un DAG por
  construcción, así que rechazar cualquier referencia igual-o-posterior basta
  para descartar tanto índices fuera de rango como ciclos en una sola
  pasada): se descarta la lista COMPLETA (no solo el valor malo), se deja un
  `logger.warning`, y el paso queda secuencial SOLO tras el paso
  inmediatamente anterior (`[idx - 1]`, o `[]` si es el primero) — un plan
  NUEVO que ya intentaba declarar dependencias y falló no necesita
  reconstruir el historial completo de antes, con "tras el anterior" alcanza.

`run()` agrupa los pasos pendientes en OLAS por orden topológico sobre
`depende_de` (`_construir_olas`): una ola contiene todos los pasos cuyas
dependencias YA terminaron. Dentro de una ola, los `Agent` de cada paso
corren con `asyncio.gather`, limitados por un `asyncio.Semaphore(
getattr(deps.settings, "MISSIONS_PARALLEL_MAX", DEFAULT_PARALLEL_MAX))`
(`deps.settings` se lee de forma defensiva — WP-V5-01 agrega el setting real,
pero `run()` nunca revienta si falta).

### Reglas de seguridad del paralelismo

1. **Ningún perfil `permite_dangerous_con_confirmacion=True` entra jamás a
   una ola paralela**: `_construir_olas` separa cualquier paso así en su
   PROPIA ola solitaria (antes y/o después de sus compañeros de ola
   topológica, preservando el orden relativo) — así `waiting_confirmation`
   sigue siendo un estado único y determinista: nunca hay que decidir qué
   hacer con "otro paso peligroso" corriendo al mismo tiempo, porque nunca
   ocurre. Consecuencia directa: una ola con MÁS de un paso JAMÁS puede
   producir `confirmation_required` (ninguno de sus miembros puede pedir una
   tool `dangerous`, ver `registry_view.RestrictedRegistry`) — la pausa por
   confirmación solo puede salir de una ola solitaria.
2. **Las escrituras se serializan con un `asyncio.Lock`** (`_LockedRunDeps`,
   envuelto una vez por llamada a `run()`): varios pasos de una misma ola
   pueden terminar casi al mismo tiempo y llamar a `deps.save_step`/
   `save_mission`/`insert_steps` concurrentemente, pero TODOS comparten la
   MISMA `deps.session` (una única `AsyncSession` de SQLAlchemy por misión,
   inyectada una sola vez por `run_mission.py` para toda la ejecución) — una
   `AsyncSession` NO soporta que dos corutinas la usen al mismo tiempo
   (corrompe su estado interno bajo carga real). El lock serializa solo el
   INSTANTE de escribir cada resultado, nunca el trabajo del paso en sí
   (LLM/tools siguen corriendo en paralelo).
3. **El historial sintético es por-dependencia, no por-ola**: cada paso
   recibe (`_historial_de_dependencias`) el resultado de SUS dependencias
   declaradas, en orden de índice ascendente — nunca el de "todo lo que
   terminó antes", ni el de sus compañeros de ola que no sean also una
   dependencia declarada. Determinista: no importa en qué orden terminen
   dentro del `gather`, el historial de un paso siempre se arma igual.
4. **Si un paso de una ola entra a `waiting_confirmation`**: `run()` espera a
   que TERMINEN los demás pasos de esa misma ola en vuelo (un `asyncio.gather`
   normal no cancela a los demás porque uno pause), persiste los resultados
   de los que sí terminaron, y la misión queda `waiting_confirmation` con el
   `pending_tool_call` de ESE paso — los pasos que ni siquiera se habían
   lanzado (de olas posteriores) quedan `pending` tal cual, listos para que
   la reanudación (ver más abajo) los retome desde ahí.

## Replan acotado (`WP-V5-05`)

Si un paso termina en `error` (excepción del `Agent`/tool, o timeout — ver
abajo) y la misión NO consumió todavía su único replan (contador persistido
en `agent_missions.presupuesto["replans_usados"]`, default `0`, máximo `1`),
`run()` intenta UNA llamada al LLM `"principal"` (`_replan`) con el objetivo
original, un resumen de los pasos ya completados (instrucción + resultado) y
el error, pidiendo un plan NUEVO **solo para lo que falta** (mismo formato
JSON que `plan()`, incluido `depende_de` — resuelto de forma LOCAL a la
sub-lista nueva y luego desplazado al insertarla, ver `_replan`/`run`), con
la restricción dura de que `pasos_completados + pasos_nuevos <= presupuesto
original` (trunca exactamente igual que `plan()`, usando lo que quede de
`MISSIONS_MAX_STEPS`).

- Los pasos ya `done` se conservan tal cual (con su resultado, que sigue
  disponible como dependencia/historial e insumo de la síntesis final).
- Los pasos que quedaron `pending` sin siquiera haberse lanzado (de olas
  posteriores a la que falló) se marcan `skipped` — nunca se ejecutan. El
  paso que SÍ falló ya quedó `error` (persistido por el paso mismo, no se
  toca de nuevo).
- Los pasos nuevos se insertan como filas `agent_steps` nuevas
  (`deps.insert_steps`, ver `RunDeps` abajo) con `seq` continuando después
  del último `seq` usado — nunca reutiliza `seq` de un paso viejo — y
  `run()` sigue ejecutando por olas desde ahí, con el contador
  `replans_usados` ya en `1` (persistido vía `save_mission(presupuesto=...)`
  ANTES de seguir).
- Si el replan falla (el LLM no da JSON usable, o ya no queda presupuesto de
  pasos) o la misión ya había usado su replan → la misión pasa a `error` con
  el mensaje del paso que la disparó (comportamiento previo a `WP-V5-05`,
  sin cambios).
- **Nunca se replanea un paso `waiting_confirmation`**: eso es una PAUSA
  humana esperada (ver "Confirmación pendiente" abajo), no un fallo — el
  replan solo se dispara desde la rama de `error`, jamás desde la de
  `confirmation_required`.

## Timeout por paso (`WP-V5-05`)

Cada ejecución de `Agent.run_turn` (dentro de `_run_step`, el camino normal
de un paso — NO el camino de reanudación de una tool aprobada,
`_run_resumed_step`, que no construye ningún `Agent`) se envuelve en
`asyncio.timeout(getattr(deps.settings, "MISSIONS_STEP_TIMEOUT_SECONDS",
DEFAULT_STEP_TIMEOUT_SECONDS))`. Si se agota, el paso se marca `error` con un
mensaje claro (incluye el número de paso y el timeout usado) y dispara la
MISMA lógica de replan de arriba — un timeout es, a todos los efectos de
`run()`, un tipo más de error de paso.

## Confirmación pendiente: camino esperado para los perfiles con
## `permite_dangerous_con_confirmacion=True` (WP-V4-05), red de seguridad
## para el resto

Si durante un paso el sub-agente pide una tool `dangerous` (evento
`confirmation_required` de `Agent.run_turn`), `run()` persiste el
`tool_call_id`/`name`/`args` pendientes en `agent_steps.usage` (clave
`"pending_tool_call"`), marca el paso y la misión `waiting_confirmation`, y
RETORNA sin seguir (ver "Reglas de seguridad del paralelismo" arriba para el
caso de una ola con más de un paso en vuelo).

Desde `WP-V4-05` esto ya no es solo una red de seguridad hipotética: los
perfiles con `profiles.AgentProfile.permite_dangerous_con_confirmacion=True`
(`marketing`/`sales`/`social_media`/`developer`/`qa`/`security`/`devops` —
NO `finance` ni `voice`, ver `profiles.py` para el detalle exacto por perfil)
reciben un `RestrictedRegistry` que SÍ deja ver sus tools `dangerous` de
`allowed_tools` (`registry_view.py`), así que `Agent.run_turn` puede legítimamente toparse
con una y disparar `confirmation_required` en el curso normal de una misión —
no es un bug ni un perfil mal configurado, es el flujo de diseño: "pausar +
aprobación humana explícita" en vez de "ocultar para siempre" (ver docstring
de `registry_view.RestrictedRegistry`).

Para los perfiles con `permite_dangerous_con_confirmacion=False` (default —
incluye los tres P0: `research`/`data_analyst`/`content`, y el resto de
perfiles sin tools `dangerous` hoy: `ceo`/`design`/`legal`/`video`/`finance`/
`voice`) esto sigue sin poder ocurrir nunca en la práctica: ninguno de sus
`allowed_tools` es `dangerous` (verificado en `profiles.py`), y aunque lo
fuera, `RestrictedRegistry` la seguiría ocultando en `.get()` — así que para
ESOS perfiles sigue siendo una red de seguridad para un error humano futuro,
no un camino esperado. En cualquier caso, `run()` nunca auto-aprueba
(`ROADMAP_V2.md` §7.9: "ante una tool dangerous o no permitida: la misión
pasa a waiting_confirmation y NUNCA auto-aprueba").

### Reanudación: NUNCA se reinvoca al LLM para el paso pendiente

Una vez que el usuario aprueba (`POST /v1/missions/{id}/confirm` ->
`run_mission.py` con `resume=true`), `run()` NO reconstruye un `Agent` ni
vuelve a llamar al LLM para "reproducir" la tool call aprobada: el
`tool_call_id` lo acuñó el proveedor LLM en esa respuesta puntual (ver
`edecan_core.agent.Agent._run_turn`), y una llamada nueva al LLM mintaría un
`tool_call_id` DISTINTO que `approved_tool_calls` jamás reconocería como
aprobado — dejando la misión en un loop de "aprobar" que nunca progresa
(mismo motivo, documentado igual, en
`apps/api/edecan_api/routers/conversations.py`). En vez de eso,
`_run_resumed_step` ejecuta DIRECTO la tool/args que quedaron pendientes
(propagados desde `agent_steps.usage['pending_tool_call']` vía
`Mission.approved_tool_name`/`approved_tool_args`) contra el `ToolRegistry`
completo — mismo patrón que `_stream_approved_confirmation` en
`conversations.py`. El paso reanudado sigue siendo, por construcción, el
único miembro de su ola (todo paso que puede llegar a `waiting_confirmation`
tiene `permite_dangerous_con_confirmacion=True`, y esos SIEMPRE corren
solitarios — ver "Reglas de seguridad del paralelismo" #1), así que no hace
falta ningún caso especial en `_construir_olas` para la reanudación.

Antes de ejecutar, `_run_resumed_step` revalida `tool.requires_flags` contra
`deps.flags` (el plan ACTUAL del tenant, releído por `run_mission.py::handle`
en CADA job — nunca el que tenía el tenant cuando el paso propuso la tool):
`self._registry` es el `ToolRegistry` COMPLETO, y a diferencia de `.specs()`
(que sí filtra por flags al anunciar), ni su `.get()` ni el de
`RestrictedRegistry` (`registry_view.py`) filtran por flags — sin este
chequeo, un downgrade de plan (o la revocación de un flag fino) entre la
pausa y la aprobación humana no impedía ejecutar una tool que el tenant ya no
tiene contratada (p. ej. `publicar_social`/`connectors.social`). Es el mismo
hueco que el "Hallazgo 1" de `docs/seguridad-modelo-amenazas.md` documenta
para `Agent.run_turn` (`requires_flags` solo se aplicaba al ANUNCIAR una
tool, nunca al EJECUTARLA), reimplementado de forma independiente en este
método — que ni siquiera pasa por `Agent`. Si el flag no se satisface, el
paso termina en `error` sin llegar a `tool.run()`, mismo tratamiento que "la
tool ya no existe" (ver `_flags_satisfechos`).

## `started_at`/`finished_at` por paso (`WP-V6-10`)

`agent_steps` no tiene columnas propias para el instante en que un paso
empezó/terminó a ejecutarse (`ARCHITECTURE.md` §10.3/`ROADMAP_V2.md` §7.4, y
este WP tampoco agrega ninguna migración — prohibido tocar el esquema).
`_ejecutar_paso_de_ola` captura `started_at` (`_now_iso()`, ISO-8601 UTC) justo
antes de correr el paso (tras adquirir `semaforo`, así que mide tiempo de
EJECUCIÓN, no de espera en cola) y lo pasa a `_run_step`/`_run_resumed_step`;
cada uno de sus caminos TERMINALES (`done`/`error`/`waiting_confirmation`,
incluidos el timeout y la excepción inesperada que atrapa la propia
`_ejecutar_paso_de_ola`) persiste `started_at` junto a un `finished_at` fresco
dentro del MISMO dict que ya se guardaba en `agent_steps.usage` —
`_timing_usage()` los mezcla con lo que hubiera (tokens de `Usage`,
`pending_tool_call`) en vez de reemplazarlo. La transición intermedia a
`status="running"` (`deps.save_step(..., usage=None)`) se deja SIN TOCAR a
propósito: sigue significando "no toques `usage`" (`RunDeps.save_step`), que es
precisamente lo que mantiene vivo el `depende_de` escondido ahí por
`run_mission.py::_insert_steps` mientras el paso sigue `pending` (ver
`docs/agentes.md` §4bis) — solo el guardado TERMINAL de cada paso, que de
todos modos ya reemplazaba `usage` por completo antes de este WP, gana las dos
claves nuevas. `apps/api/edecan_api/routers/missions.py` (`GET
/v1/missions/{id}/detalle`, WP-V6-10) las expone como `started`/`finished` —
`None` para pasos que corrieron antes de este WP (su `usage` nunca tuvo esas
claves) o que todavía no terminaron.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from edecan_core.agent import Agent
from edecan_core.llm_types import ChatMessage, CompletionRequest
from edecan_core.tools.base import ToolContext, ToolResult
from edecan_schemas import PersonaConfig

from .profiles import IMPLEMENTED_AGENT_KEYS, PROFILES, AgentProfile
from .registry_view import RestrictedRegistry

logger = logging.getLogger(__name__)

_LLM_ALIAS = "profundo"
DEFAULT_MAX_STEPS = 8
"""Igual al default de `MISSIONS_MAX_STEPS` (`ROADMAP_V2.md` §7.5) — se
duplica aquí como literal (no se importa `settings` real, ver `plan()`)."""

DEFAULT_PARALLEL_MAX = 3
"""Igual al default de `MISSIONS_PARALLEL_MAX` (setting agregado por
WP-V5-01, `getattr` defensivo — ver docstring del módulo) — mismo criterio de
duplicar el literal que `DEFAULT_MAX_STEPS`."""

DEFAULT_STEP_TIMEOUT_SECONDS = 300
"""Igual al default de `MISSIONS_STEP_TIMEOUT_SECONDS` (setting agregado por
WP-V5-01) — mismo criterio que `DEFAULT_PARALLEL_MAX`."""

MAX_REPLANS_PER_MISSION = 1
"""Presupuesto de replanificaciones por misión (`agent_missions.presupuesto
["replans_usados"]`) — ver la sección "Replan acotado" del docstring."""

FALLBACK_AGENT_KEY = "research"

_PLAN_MAX_TOKENS = 1024
_SYNTHESIS_MAX_TOKENS = 1536

_PASO_DONE = "done"
_PASO_WAITING = "waiting_confirmation"
_PASO_ERROR = "error"


# ---------------------------------------------------------------------------
# Tipos de datos
# ---------------------------------------------------------------------------


@dataclass
class Mission:
    """Instantánea de una misión que `run()` necesita para ejecutarla.

    `plan` es una lista de dicts `{"seq", "agente", "instruccion",
    ["depende_de", "status", "resultado", "usage"]}` — todas salvo las tres
    primeras son opcionales: para una misión recién planificada todos los
    pasos vienen `status="pending"` (o sin la clave, se asume `"pending"`);
    para una reanudación (`resume_step_seq` no `None`), los pasos ya
    completados llegan con `status="done"` y su `resultado`, así `run()`
    reconstruye el estado de dependencias sin tener que re-ejecutarlos (ver
    `Orchestrator.run`). `depende_de` (WP-V5-05) es opcional: `run()` la
    resuelve de forma defensiva vía `_resolver_depende_de` si falta (plan
    viejo/retrocompatible, ver docstring del módulo).
    """

    id: Any
    tenant_id: UUID
    user_id: UUID
    objetivo: str
    plan: list[dict[str, Any]]
    presupuesto: dict[str, Any] = field(default_factory=dict)
    resume_step_seq: int | None = None
    approved_tool_call_id: str | None = None
    approved_tool_name: str | None = None
    """Nombre de la tool `dangerous` aprobada por el usuario para
    `resume_step_seq` (junto con `approved_tool_args`) — `run()` la ejecuta
    DIRECTO vía `_run_resumed_step` en vez de reinvocar al LLM (ver docstring
    del módulo, sección "Reanudación"). `None` para una misión que no está
    reanudando un paso `waiting_confirmation`."""
    approved_tool_args: dict[str, Any] | None = None


class RunDeps(Protocol):
    """Subconjunto de colaboradores que `Orchestrator.run` necesita.

    Cualquier objeto (dataclass, `SimpleNamespace`, etc.) con estos atributos
    sirve — no hace falta heredar de este `Protocol`, es solo documentación
    tipada (mismo criterio que `edecan_premium.campaigns.CampaignDeps`).
    """

    session: Any  # se reenvía tal cual a `ToolContext.session` — cada Tool decide qué hacer con él.
    settings: Any  # ídem, a `ToolContext.settings`. También lee `MISSIONS_PARALLEL_MAX`/
    # `MISSIONS_STEP_TIMEOUT_SECONDS` (`getattr` defensivo, ver docstring del módulo).
    vault: Any  # ídem, a `ToolContext.vault`.
    flags: dict[str, Any]  # flags de plan del tenant (mismo dict que `Agent.run_turn(flags=...)`).

    async def save_step(
        self,
        *,
        seq: int,
        status: str | None = None,
        resultado: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Persiste el estado de UN `agent_steps` YA EXISTENTE (ver
        `insert_steps` para crear filas nuevas). `None` en cualquier campo
        significa "no lo toques" (actualización parcial) — es la misma
        convención que `ReminderPatch`/`update_reminder` en `edecan_api`."""
        ...

    async def save_mission(
        self,
        *,
        status: str | None = None,
        resultado: str | None = None,
        error: str | None = None,
        presupuesto: dict[str, Any] | None = None,
    ) -> None:
        """Persiste el estado de `agent_missions`. Misma convención de `None`
        = "no lo toques" que `save_step`. `presupuesto` (WP-V5-05) es como se
        persiste el contador `replans_usados` tras un replan (ver docstring
        del módulo, sección "Replan acotado")."""
        ...

    async def insert_steps(self, pasos: list[dict[str, Any]]) -> None:
        """Inserta filas `agent_steps` NUEVAS (WP-V5-05, replan) — cada
        `paso` trae al menos `seq`/`agente`/`instruccion` (mismo shape que ya
        usa `run_mission.py._insert_steps` para el plan inicial). A
        diferencia de `save_step` (que solo actualiza una fila EXISTENTE por
        `seq`, un `UPDATE ... WHERE seq = :seq` que no hace nada si esa fila
        no existe todavía), este método CREA las filas: el replan puede
        proponer pasos con `seq` que nunca existieron en `agent_steps` (los
        del plan original ya se insertaron en la planificación inicial, pero
        los del replan son pasos genuinamente nuevos)."""
        ...


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """`llm_router`/`registry` son `Any` a propósito, igual que
    `edecan_core.agent.Agent`: no se importa `edecan_llm` (duck typing sobre
    `.resolve(alias, flags) -> (provider, model)` / `provider.complete(req)`)
    y `registry` es el `ToolRegistry` COMPLETO del proceso (se recorta por
    paso con `RestrictedRegistry`, nunca se muta)."""

    def __init__(self, llm_router: Any, registry: Any) -> None:
        self._llm_router = llm_router
        self._registry = registry

    async def plan(
        self, objetivo: str, flags: dict[str, Any], settings: Any = None
    ) -> list[dict[str, Any]]:
        """Devuelve una lista de pasos `{"seq", "agente", "instruccion",
        "depende_de"}`, siempre con al menos uno (nunca una lista vacía).
        Ver docstring del módulo, sección "Dependencias entre pasos", para el
        significado exacto de `depende_de`."""
        objetivo = (objetivo or "").strip()
        max_steps = _coerce_max_steps(getattr(settings, "MISSIONS_MAX_STEPS", DEFAULT_MAX_STEPS))

        pasos: list[dict[str, Any]] = []
        if objetivo:
            try:
                provider, model = self._llm_router.resolve(_LLM_ALIAS, flags)
                prompt_usuario = _planner_user_prompt(objetivo, max_steps)
                request = CompletionRequest(
                    model=model,
                    system=_planner_system_prompt(),
                    messages=[ChatMessage(role="user", content=prompt_usuario)],
                    max_tokens=_PLAN_MAX_TOKENS,
                    temperature=0.2,
                )
                response = await provider.complete(request)
                data = _extraer_json(getattr(response, "text", "") or "")
                pasos = _pasos_desde_json(data, max_steps)
            except Exception:  # noqa: BLE001 - cualquier fallo de planificación cae al fallback
                logger.warning(
                    "Orchestrator.plan: fallo generando/parseando el plan; "
                    "se usa el fallback de 1 paso (research).",
                    exc_info=True,
                )
                pasos = []

        if not pasos:
            pasos = [{"agente": FALLBACK_AGENT_KEY, "instruccion": objetivo or "(sin objetivo)"}]

        pasos = _resolver_depende_de(pasos[:max_steps])
        return [
            {
                "seq": i,
                "agente": paso["agente"],
                "instruccion": paso["instruccion"],
                "depende_de": paso["depende_de"],
            }
            for i, paso in enumerate(pasos, start=1)
        ]

    async def run(self, mission: Mission, deps: RunDeps) -> None:
        """Ejecuta `mission.plan` por olas (paralelismo limitado dentro de
        cada ola) y, al terminar, sintetiza el resultado final. Nunca lanza:
        cualquier excepción no atrapada más abajo se traduce a
        `save_mission(status="error", error=...)` (mismo espíritu que
        `Agent.run_turn`, que nunca deja "reventar" un turno hacia quien lo
        consume). Ver el docstring del módulo para el diseño completo
        (dependencias/olas/replan/timeout/confirmación/reanudación)."""
        try:
            max_steps = _coerce_max_steps(mission.presupuesto.get("max_steps"))
            pasos_todos = sorted(mission.plan, key=lambda p: int(p.get("seq", 0)))[:max_steps]
            pasos_todos = _resolver_depende_de(pasos_todos)

            lock = asyncio.Lock()
            deps_bloqueados = _LockedRunDeps(deps, lock)
            parallel_max = _coerce_parallel_max(
                getattr(deps.settings, "MISSIONS_PARALLEL_MAX", DEFAULT_PARALLEL_MAX)
            )
            step_timeout = _coerce_step_timeout(
                getattr(
                    deps.settings, "MISSIONS_STEP_TIMEOUT_SECONDS", DEFAULT_STEP_TIMEOUT_SECONDS
                )
            )
            semaforo = asyncio.Semaphore(parallel_max)

            resultados: dict[int, str] = {}
            instrucciones: dict[int, str] = {}
            completados: set[int] = set()
            pendientes: dict[int, dict[str, Any]] = {}

            for paso in pasos_todos:
                idx = int(paso["seq"]) - 1
                instrucciones[idx] = str(paso.get("instruccion") or "")
                status_actual = paso.get("status") or "pending"
                if status_actual == "done":
                    resultados[idx] = str(paso.get("resultado") or "")
                    completados.add(idx)
                elif status_actual in ("skipped", "error", "cancelled"):
                    completados.add(idx)
                else:
                    pendientes[idx] = paso

            presupuesto = dict(mission.presupuesto or {})
            replans_usados = _coerce_replans_usados(presupuesto.get("replans_usados"))
            siguiente_idx = max((int(p["seq"]) for p in pasos_todos), default=0)

            while pendientes:
                olas = _construir_olas(list(pendientes.values()), completados)
                error_paso: _ResultadoPaso | None = None

                for ola in olas:
                    tareas = [
                        self._ejecutar_paso_de_ola(
                            paso=paso,
                            mission=mission,
                            deps=deps_bloqueados,
                            semaforo=semaforo,
                            history=_historial_de_dependencias(paso, resultados, instrucciones),
                            step_timeout=step_timeout,
                        )
                        for paso in ola
                    ]
                    resultados_ola = await asyncio.gather(*tareas)

                    for r in resultados_ola:
                        if r.outcome == _PASO_DONE:
                            resultados[r.idx] = r.resultado or ""
                            completados.add(r.idx)
                            pendientes.pop(r.idx, None)
                        elif r.outcome == _PASO_ERROR:
                            completados.add(r.idx)
                            pendientes.pop(r.idx, None)

                    pausa = next((r for r in resultados_ola if r.outcome == _PASO_WAITING), None)
                    if pausa is not None:
                        await deps_bloqueados.save_mission(
                            status="waiting_confirmation", resultado=None, error=None
                        )
                        return

                    fallo = next((r for r in resultados_ola if r.outcome == _PASO_ERROR), None)
                    if fallo is not None:
                        error_paso = fallo
                        break

                if error_paso is None:
                    continue  # todas las olas de esta pasada terminaron -> `pendientes` ya vacío.

                # ------------------------------------------------------------
                # REPLAN ACOTADO (máx. MAX_REPLANS_PER_MISSION por misión)
                # ------------------------------------------------------------
                if replans_usados >= MAX_REPLANS_PER_MISSION:
                    await deps_bloqueados.save_mission(
                        status="error", resultado=None, error=error_paso.error
                    )
                    return

                nuevos = await self._replan(
                    mission=mission,
                    deps=deps_bloqueados,
                    resultados=resultados,
                    instrucciones=instrucciones,
                    completados_ok=len(resultados),
                    error=error_paso.error or "error desconocido",
                    max_steps=max_steps,
                )
                if not nuevos:
                    await deps_bloqueados.save_mission(
                        status="error", resultado=None, error=error_paso.error
                    )
                    return

                replans_usados += 1
                presupuesto["replans_usados"] = replans_usados
                await deps_bloqueados.save_mission(presupuesto=presupuesto)

                # Los pendientes que quedaron sin lanzarse (olas posteriores a
                # la que falló) nunca corrieron -> se marcan 'skipped'. El
                # paso que SÍ falló ya quedó 'error' (persistido dentro de
                # `_ejecutar_paso_de_ola`), no se vuelve a tocar.
                for idx_viejo in list(pendientes.keys()):
                    await deps_bloqueados.save_step(
                        seq=idx_viejo + 1, status="skipped", resultado=None, usage=None
                    )
                    completados.add(idx_viejo)
                pendientes.clear()

                base_idx = siguiente_idx
                filas_nuevas: list[dict[str, Any]] = []
                for offset, paso_nuevo in enumerate(nuevos):
                    idx_nuevo = base_idx + offset
                    fila = {
                        "seq": idx_nuevo + 1,
                        "agente": paso_nuevo["agente"],
                        "instruccion": paso_nuevo["instruccion"],
                        "depende_de": [
                            int(d) + base_idx for d in (paso_nuevo.get("depende_de") or [])
                        ],
                    }
                    instrucciones[idx_nuevo] = fila["instruccion"]
                    pendientes[idx_nuevo] = fila
                    filas_nuevas.append(fila)
                siguiente_idx = base_idx + len(nuevos)
                await deps_bloqueados.insert_steps(filas_nuevas)

            sintesis = await self._synthesize(
                mission.objetivo, _historial_completo(resultados, instrucciones), deps_bloqueados
            )
            await deps_bloqueados.save_mission(status="done", resultado=sintesis, error=None)
        except Exception as exc:  # noqa: BLE001 - ver docstring del método
            logger.exception("Orchestrator.run: fallo irrecuperable en la misión %s", mission.id)
            await deps.save_mission(status="error", resultado=None, error=str(exc))

    async def _ejecutar_paso_de_ola(
        self,
        *,
        paso: dict[str, Any],
        mission: Mission,
        deps: RunDeps,
        semaforo: asyncio.Semaphore,
        history: list[ChatMessage],
        step_timeout: float,
    ) -> _ResultadoPaso:
        """Ejecuta UN paso dentro de una ola, bajo `semaforo` (límite de
        paralelismo, `MISSIONS_PARALLEL_MAX`). Enruta a `_run_resumed_step`
        (reanudación, sin LLM) o a `_run_step` (turno normal, con timeout) —
        y garantiza que NINGUNA excepción (incluido `TimeoutError`) escape
        hacia el `asyncio.gather` de `run()`: cualquier fallo, esperado o no,
        se traduce a un `_ResultadoPaso` de outcome `_PASO_ERROR` con su
        propio `agent_steps` ya marcado `error` — así un paso roto nunca
        cancela ni corrompe el resto de su ola."""
        seq = int(paso["seq"])
        idx = seq - 1
        es_resumido = mission.resume_step_seq == seq and bool(mission.approved_tool_name)
        # WP-V6-10: capturado DESPUÉS de `semaforo` más abajo (mide ejecución,
        # no espera de cupo) — ver la sección `started_at`/`finished_at` del
        # docstring del módulo.
        started_at = _now_iso()

        async with semaforo:
            try:
                if es_resumido:
                    resultado, outcome = await self._run_resumed_step(
                        paso, mission, deps, started_at=started_at
                    )
                else:
                    try:
                        async with asyncio.timeout(step_timeout):
                            resultado, outcome = await self._run_step(
                                paso, mission, deps, history, started_at=started_at
                            )
                    except TimeoutError:
                        mensaje = (
                            f"El paso {seq} ('{paso.get('agente')}') superó el tiempo máximo de "
                            f"{step_timeout:.0f}s de ejecución y se marcó como error."
                        )
                        logger.warning(
                            "Orchestrator: timeout de %.0fs en el paso %s de la misión %s",
                            step_timeout,
                            seq,
                            mission.id,
                        )
                        await deps.save_step(
                            seq=seq,
                            status="error",
                            resultado=mensaje,
                            usage=_timing_usage(started_at),
                        )
                        resultado, outcome = mensaje, _PASO_ERROR
            except Exception as exc:  # noqa: BLE001 - ningún paso debe tumbar la ola completa
                logger.exception(
                    "Orchestrator: fallo inesperado ejecutando el paso %s de la misión %s",
                    seq,
                    mission.id,
                )
                mensaje = f"Error inesperado en el paso {seq}: {exc}"
                try:
                    await deps.save_step(
                        seq=seq,
                        status="error",
                        resultado=mensaje,
                        usage=_timing_usage(started_at),
                    )
                except Exception:  # noqa: BLE001 - ni siquiera persistir el error debe propagar
                    logger.exception(
                        "Orchestrator: también falló guardando el error del paso %s", seq
                    )
                resultado, outcome = mensaje, _PASO_ERROR

        if outcome == _PASO_WAITING:
            return _ResultadoPaso(seq=seq, idx=idx, outcome=outcome)
        if outcome == _PASO_ERROR:
            return _ResultadoPaso(seq=seq, idx=idx, outcome=outcome, error=resultado)
        return _ResultadoPaso(seq=seq, idx=idx, outcome=outcome, resultado=resultado)

    async def _run_step(
        self,
        paso: dict[str, Any],
        mission: Mission,
        deps: RunDeps,
        history: list[ChatMessage],
        started_at: str,
    ) -> tuple[str | None, str]:
        """Ejecuta UN paso vía `Agent.run_turn` (LLM + herramientas). Devuelve
        `(resultado_o_mensaje, outcome)` con `outcome` ∈ `{_PASO_DONE,
        _PASO_WAITING, _PASO_ERROR}`. Solo persiste EL PROPIO `agent_steps`
        de este paso (`deps.save_step`) — nunca `deps.save_mission`: esa
        decisión (¿la misión entera pausa o falla?) depende de cómo terminen
        los DEMÁS pasos de la misma ola, así que la toma `run()` después de
        esperar el `gather` completo (ver docstring del módulo).

        `started_at` (WP-V6-10, capturado por `_ejecutar_paso_de_ola` antes de
        llamar aquí) viaja dentro de `usage` en cada guardado TERMINAL vía
        `_timing_usage` — ver la sección `started_at`/`finished_at` del
        docstring del módulo.

        Nunca se llama para el paso que se está reanudando tras una
        aprobación (`_ejecutar_paso_de_ola` lo enruta a `_run_resumed_step` en
        su lugar) — por eso `approved_tool_calls` siempre viaja vacío: ningún
        `tool_call` que el LLM proponga EN ESTE turno nuevo puede venir
        pre-aprobado (ver docstring del módulo, sección "Reanudación")."""
        seq = int(paso["seq"])
        perfil = _resolver_perfil(paso)

        await deps.save_step(seq=seq, status="running", resultado=None, usage=None)

        restricted = RestrictedRegistry(
            self._registry,
            perfil.allowed_tools,
            permite_dangerous_con_confirmacion=perfil.permite_dangerous_con_confirmacion,
        )
        agent = Agent(self._llm_router, restricted, model_alias=perfil.model_alias)
        persona = PersonaConfig(
            nombre_asistente=perfil.nombre,
            idioma="es",
            instrucciones=perfil.system_prompt_extra,
            memoria_activada=False,
        )
        ctx = ToolContext(
            tenant_id=mission.tenant_id,
            user_id=mission.user_id,
            session=deps.session,
            settings=deps.settings,
            llm=self._llm_router,
            vault=deps.vault,
            extras={"flags": deps.flags, "approved_tool_calls": set()},
        )

        texto_partes: list[str] = []
        usage: dict[str, Any] = {}

        async for event in agent.run_turn(
            ctx=ctx,
            persona=persona,
            history=list(history),
            user_text=str(paso.get("instruccion") or ""),
            flags=deps.flags,
        ):
            tipo = getattr(event, "type", None)
            if tipo == "text_delta":
                texto_partes.append(event.text)
            elif tipo == "confirmation_required":
                pendiente = {"id": event.tool_call_id, "name": event.name, "args": event.args}
                await deps.save_step(
                    seq=seq,
                    status="waiting_confirmation",
                    resultado=None,
                    usage=_timing_usage(started_at, {"pending_tool_call": pendiente}),
                )
                return None, _PASO_WAITING
            elif tipo == "error":
                await deps.save_step(
                    seq=seq,
                    status="error",
                    resultado=event.message,
                    usage=_timing_usage(started_at),
                )
                return event.message, _PASO_ERROR
            elif tipo == "done":
                usage = dict(event.usage or {})

        resultado = "".join(texto_partes).strip() or "(el sub-agente no devolvió texto)"
        await deps.save_step(
            seq=seq,
            status="done",
            resultado=resultado,
            usage=_timing_usage(started_at, usage or None),
        )
        return resultado, _PASO_DONE

    async def _run_resumed_step(
        self, paso: dict[str, Any], mission: Mission, deps: RunDeps, started_at: str
    ) -> tuple[str | None, str]:
        """Reanuda el paso `waiting_confirmation` que el usuario acaba de
        aprobar (`mission.resume_step_seq`), ejecutando DIRECTO la tool que
        quedó pendiente — nunca reinvoca al LLM (ver docstring del módulo,
        sección "Reanudación"; mismo patrón que
        `apps/api/edecan_api/routers/conversations.py::
        _stream_approved_confirmation`). Devuelve `(resultado_o_mensaje,
        outcome)` con la misma convención que `_run_step`; al igual que ese
        método, solo persiste su PROPIO `agent_steps`, nunca `save_mission`.
        `started_at` (WP-V6-10) se persiste igual que en `_run_step`, ver la
        sección `started_at`/`finished_at` del docstring del módulo.

        Busca la tool en `self._registry` COMPLETO (no en un
        `RestrictedRegistry` recortado por perfil): la aprobación la dio un
        humano explícitamente vía `POST /v1/missions/{id}/confirm`, el mismo
        criterio "humano en el loop" que ya vale para `dangerous` en el flujo
        de conversación — no el perfil del sub-agente, que solo gobierna qué
        puede proponer un LLM sin supervisión turno a turno.

        Antes de ejecutar, revalida `tool.requires_flags` contra `deps.flags`
        (recalculado desde el plan ACTUAL del tenant por `run_mission.py::
        handle` en CADA job, nunca cacheado desde el momento en que el paso
        propuso la tool) vía `_flags_satisfechos` — ni `self._registry.get()`
        ni `RestrictedRegistry.get()` (`registry_view.py`) filtran por flags,
        así que sin este chequeo un downgrade de plan (o la revocación de un
        flag fino) entre la pausa y la aprobación humana no impedía nada
        (mismo hueco documentado como "Hallazgo 1" en
        `docs/seguridad-modelo-amenazas.md` para `Agent.run_turn`,
        reimplementado de forma independiente acá). Si no se satisface, se
        trata igual que "herramienta desconocida" abajo: nunca se llega a
        `tool.run()`."""
        seq = int(paso["seq"])
        tool_name = mission.approved_tool_name or ""
        tool_args = mission.approved_tool_args or {}

        await deps.save_step(seq=seq, status="running", resultado=None, usage=None)

        tool = self._registry.get(tool_name)
        if tool is None:
            mensaje = f"La herramienta aprobada «{tool_name}» ya no está disponible."
            await deps.save_step(
                seq=seq, status="error", resultado=mensaje, usage=_timing_usage(started_at)
            )
            return mensaje, _PASO_ERROR

        # plan-flag-bypass: `deps.flags` YA es el plan vigente del tenant en
        # este instante (no el que tenía cuando el paso propuso la tool, ver
        # docstring arriba) — lo único que faltaba era comparar
        # `tool.requires_flags` contra él antes de ejecutar. `getattr` con
        # default (no `tool.requires_flags` directo): igual que
        # `RestrictedRegistry.get()` con `tool.dangerous`, esta tool llega
        # duck-typed y algunos dobles de prueba no declaran el atributo.
        requires_flags = getattr(tool, "requires_flags", frozenset())
        if not _flags_satisfechos(requires_flags, deps.flags):
            mensaje = (
                f"La herramienta aprobada «{tool_name}» ya no está disponible en tu plan "
                "actual; no se ejecutó."
            )
            await deps.save_step(
                seq=seq, status="error", resultado=mensaje, usage=_timing_usage(started_at)
            )
            return mensaje, _PASO_ERROR

        ctx = ToolContext(
            tenant_id=mission.tenant_id,
            user_id=mission.user_id,
            session=deps.session,
            settings=deps.settings,
            llm=self._llm_router,
            vault=deps.vault,
            extras={"flags": deps.flags, "approved_tool_calls": {mission.approved_tool_call_id}},
        )
        try:
            result = await tool.run(ctx, tool_args)
        except Exception as exc:  # noqa: BLE001 - una tool nunca debe tumbar la misión
            logger.warning(
                "La herramienta aprobada %r lanzó una excepción", tool_name, exc_info=True
            )
            result = ToolResult(content=f"Error: {exc}")

        resultado = f"Listo, ejecuté «{tool_name}». {result.content}".strip()
        await deps.save_step(
            seq=seq, status="done", resultado=resultado, usage=_timing_usage(started_at)
        )
        return resultado, _PASO_DONE

    async def _replan(
        self,
        *,
        mission: Mission,
        deps: RunDeps,
        resultados: dict[int, str],
        instrucciones: dict[int, str],
        completados_ok: int,
        error: str,
        max_steps: int,
    ) -> list[dict[str, Any]] | None:
        """UNA llamada al LLM `"principal"` pidiendo un plan NUEVO solo para
        lo que falta, tras el error de un paso (ver docstring del módulo,
        sección "Replan acotado"). Devuelve la lista de pasos nuevos (con
        `depende_de` YA resuelto, índices LOCALES 0-based dentro de esta
        sublista — `run()` los desplaza al mezclarlos con el plan existente)
        o `None` si no se pudo conseguir un plan usable. Nunca lanza:
        cualquier fallo (LLM caído, JSON inválido, sin presupuesto restante)
        devuelve `None`, que `run()` trata como "no se pudo replanear" ->
        misión `error` con el mensaje del paso que la disparó."""
        restante = max_steps - completados_ok
        if restante <= 0:
            return None
        try:
            provider, model = self._llm_router.resolve(_LLM_ALIAS, deps.flags)
            resumen = _resumen_pasos_completados(resultados, instrucciones)
            prompt_usuario = _replan_user_prompt(mission.objetivo, resumen, error, restante)
            request = CompletionRequest(
                model=model,
                system=_planner_system_prompt(),
                messages=[ChatMessage(role="user", content=prompt_usuario)],
                max_tokens=_PLAN_MAX_TOKENS,
                temperature=0.2,
            )
            response = await provider.complete(request)
            data = _extraer_json(getattr(response, "text", "") or "")
            pasos = _pasos_desde_json(data, restante)
        except Exception:  # noqa: BLE001 - cualquier fallo de replanificación cae a "no se pudo".
            logger.warning(
                "Orchestrator._replan: fallo generando/parseando el plan de reemplazo "
                "de la misión %s",
                mission.id,
                exc_info=True,
            )
            return None
        if not pasos:
            return None
        return _resolver_depende_de(pasos)

    async def _synthesize(self, objetivo: str, history: list[ChatMessage], deps: RunDeps) -> str:
        if not history:
            return "La misión no ejecutó ningún paso con resultado."

        provider, model = self._llm_router.resolve(_LLM_ALIAS, deps.flags)
        lineas: list[str] = []
        for mensaje in history:
            if not isinstance(mensaje.content, str):
                continue
            etiqueta = "Instrucción" if mensaje.role == "user" else "Resultado"
            lineas.append(f"{etiqueta}: {mensaje.content}")
        resumen = "\n\n".join(lineas)
        prompt_usuario = f"Objetivo original: {objetivo}\n\n{resumen}"

        request = CompletionRequest(
            model=model,
            system=_SYNTHESIS_SYSTEM_PROMPT,
            messages=[ChatMessage(role="user", content=prompt_usuario)],
            max_tokens=_SYNTHESIS_MAX_TOKENS,
            temperature=0.3,
        )
        response = await provider.complete(request)
        return getattr(response, "text", "") or ""


# ---------------------------------------------------------------------------
# `_LockedRunDeps` — serializa `save_step`/`save_mission`/`insert_steps`
# ---------------------------------------------------------------------------


class _LockedRunDeps:
    """Envuelve un `RunDeps` real serializando sus escrituras
    (`save_step`/`save_mission`/`insert_steps`) detrás de un `asyncio.Lock`
    COMPARTIDO por toda la ejecución de `run()` — ver el docstring del
    módulo, sección "Reglas de seguridad del paralelismo" #2, para el
    porqué (varias corutinas de una misma ola comparten la MISMA
    `AsyncSession`, que no soporta uso concurrente). El resto de atributos
    (`session`/`settings`/`vault`/`flags`) se reenvían tal cual — son solo
    LECTURAS (o se reenvían más abajo, a `ToolContext`, que cada `Tool`
    decide cómo usar), nunca necesitan el lock."""

    def __init__(self, inner: RunDeps, lock: asyncio.Lock) -> None:
        self._inner = inner
        self._lock = lock
        self.session = inner.session
        self.settings = inner.settings
        self.vault = inner.vault
        self.flags = inner.flags

    async def save_step(self, **kwargs: Any) -> None:
        async with self._lock:
            await self._inner.save_step(**kwargs)

    async def save_mission(self, **kwargs: Any) -> None:
        async with self._lock:
            await self._inner.save_mission(**kwargs)

    async def insert_steps(self, pasos: list[dict[str, Any]]) -> None:
        async with self._lock:
            await self._inner.insert_steps(pasos)


@dataclass
class _ResultadoPaso:
    """Resultado de ejecutar un paso dentro de una ola — lo que `run()`
    necesita para decidir qué hacer a continuación, sin volver a tocar
    `deps` (eso ya lo hizo `_ejecutar_paso_de_ola`/`_run_step`/
    `_run_resumed_step` internamente)."""

    seq: int
    idx: int
    outcome: str
    resultado: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers de timing (WP-V6-10) — ver la sección `started_at`/`finished_at`
# del docstring del módulo.
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _timing_usage(started_at: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Arma el dict que se persiste en `agent_steps.usage` al terminar un
    paso (`done`/`error`/`waiting_confirmation`, incluidos timeout y
    excepción inesperada): siempre agrega `started_at`/`finished_at` a lo que
    ya se iba a guardar (`pending_tool_call`, tokens de `Usage`) — nunca los
    reemplaza. El REEMPLAZO completo de la columna `usage` en sí (nunca un
    merge — `apps/worker/edecan_worker/handlers/run_mission.py::_update_step`
    hace un `SET usage = ...` literal) sigue siendo intencional y sin cambios:
    en cuanto un paso corre de verdad, cualquier `depende_de` que sobreviviera
    ahí desde el INSERT inicial (`docs/agentes.md` §4bis) queda descartado —
    ya no hace falta a partir de este punto."""
    datos = dict(extra or {})
    datos["started_at"] = started_at
    datos["finished_at"] = _now_iso()
    return datos


# ---------------------------------------------------------------------------
# Helper de flags — usado por `_run_resumed_step` (fix "plan-flag-bypass",
# ver su docstring y la sección "Reanudación" arriba) para revalidar
# `requires_flags` contra el plan ACTUAL del tenant justo antes de ejecutar
# la tool aprobada.
# ---------------------------------------------------------------------------


def _flags_satisfechos(requires_flags: frozenset[str], flags: dict[str, Any]) -> bool:
    """Mismo criterio EXACTO que `edecan_core.tools.registry._flags_satisfechos`
    (duplicado en vez de importar un símbolo privado de otro paquete — mismo
    criterio de "duplicar con comentario" que `DEFAULT_MAX_STEPS`/
    `DEFAULT_PARALLEL_MAX`/`DEFAULT_STEP_TIMEOUT_SECONDS` arriba): una tool
    solo cuenta como disponible si TODOS sus `requires_flags` están presentes
    en `flags` con un valor verdadero (frozenset vacío = siempre disponible).
    `ToolRegistry.specs()`/`RestrictedRegistry.specs()` ya aplican este mismo
    filtro al ANUNCIAR una tool al modelo; `_run_resumed_step` es quien lo
    aplica también al EJECUTARLA tras una aprobación humana."""
    return all(bool(flags.get(flag_name)) for flag_name in requires_flags)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYNTHESIS_SYSTEM_PROMPT = (
    "Eres Edecán, sintetizando el resultado de una misión que delegaste en "
    "sub-agentes especializados. A continuación ves el historial de la "
    "misión como turnos Instrucción/Resultado. Redacta una respuesta final "
    "clara y directa para el usuario, en español, que resuelva su objetivo "
    "original a partir de esos resultados. No inventes datos que los "
    "sub-agentes no hayan reportado; si algún paso no obtuvo información "
    "útil, dilo con honestidad en vez de rellenar con suposiciones."
)


def _planner_system_prompt() -> str:
    perfiles_texto = "\n".join(
        f"- {p.key}: {p.nombre} — {p.descripcion}" for p in PROFILES.values() if p.disponible
    )
    return (
        "Eres el planificador de misiones de Edecán. Dado el objetivo de un "
        "usuario, divídelo en una secuencia corta de pasos, cada uno "
        "delegable a UNO de los siguientes sub-agentes especializados:\n"
        f"{perfiles_texto}\n\n"
        "Responde ÚNICAMENTE con un objeto JSON de la forma exacta "
        '{"pasos": [{"agente": "<clave>", "instruccion": "<instrucción>", '
        '"depende_de": [<índices>]}, ...]}. '
        "Sin texto antes ni después del JSON, sin bloques de código markdown. "
        "Cada `instruccion` debe ser autocontenida y concreta: el sub-agente "
        "que la reciba NO ve el objetivo original, solo tu instrucción y el "
        "resultado de los pasos de los que depende. `depende_de` es OPCIONAL "
        "(puedes omitirlo): una lista de índices 0-based de pasos ANTERIORES "
        "de este mismo plan (posición dentro de la lista `pasos` — el primer "
        "paso es el índice 0) cuyo resultado necesita este paso como "
        "contexto. Dos pasos SIN dependencia entre sí pueden ejecutarse en "
        "paralelo, así que decláralo solo cuando el paso realmente necesite "
        "el resultado de otro; usa una lista vacía `[]` para un paso "
        "independiente que no necesita nada de los anteriores. Si omites el "
        "campo por completo, el paso se ejecuta en secuencia (comportamiento "
        "por defecto). Nunca un índice igual o mayor al propio (sería una "
        "referencia circular o a un paso futuro — se descarta). Usa el menor "
        "número de pasos que resuelva el objetivo con calidad."
    )


def _planner_user_prompt(objetivo: str, max_steps: int) -> str:
    return (
        f"Objetivo: {objetivo}\n\n"
        f"Divídelo en como máximo {max_steps} paso(s). Responde solo el JSON."
    )


def _resumen_pasos_completados(resultados: dict[int, str], instrucciones: dict[int, str]) -> str:
    if not resultados:
        return "(ningún paso se completó todavía)"
    lineas = [
        f"Paso {idx + 1}: {instrucciones.get(idx, '')}\nResultado: {resultados[idx]}"
        for idx in sorted(resultados)
    ]
    return "\n\n".join(lineas)


def _replan_user_prompt(objetivo: str, resumen: str, error: str, restante: int) -> str:
    return (
        f"Objetivo original: {objetivo}\n\n"
        f"Pasos ya completados:\n{resumen}\n\n"
        f"El siguiente paso de la misión falló con este error: {error}\n\n"
        "Genera un plan NUEVO solo para lo que falta por hacer — no repitas "
        f"los pasos ya completados de arriba. Como máximo {restante} paso(s) "
        "nuevo(s). Responde solo el JSON (mismo formato, `depende_de` "
        "opcional referido a los ÍNDICES de este plan nuevo, no a los pasos "
        "ya completados)."
    )


# ---------------------------------------------------------------------------
# Helpers de parseo/plan
# ---------------------------------------------------------------------------

_OPEN_CLOSE = {"{": "}", "[": "]"}


def _extraer_json(texto: str) -> Any | None:
    """Parseo tolerante: intenta `json.loads` directo y, si falla, busca el
    primer bloque `{...}`/`[...]` balanceado dentro de `texto` (tolera que el
    LLM envuelva la respuesta en prosa o un bloque de código markdown).
    Devuelve `None` si no hay nada parseable."""
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass

    inicio = next((i for i, ch in enumerate(texto) if ch in _OPEN_CLOSE), None)
    if inicio is None:
        return None

    apertura = texto[inicio]
    cierre = _OPEN_CLOSE[apertura]
    profundidad = 0
    for i in range(inicio, len(texto)):
        if texto[i] == apertura:
            profundidad += 1
        elif texto[i] == cierre:
            profundidad -= 1
            if profundidad == 0:
                try:
                    return json.loads(texto[inicio : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _pasos_desde_json(data: Any, max_steps: int) -> list[dict[str, Any]]:
    """`data` puede ser `{"pasos": [...]}` o directamente `[...]` (tolerancia
    extra: algunos modelos omiten el envoltorio pese a la instrucción).
    Cada paso crudo necesita al menos `instruccion` no vacía; `agente` se
    reasigna a `FALLBACK_AGENT_KEY` si no es un perfil `disponible=True` — un
    paso con un agente inventado/no implementado no se descarta, se re-dirige
    (la instrucción del planificador sigue siendo información útil). El
    `depende_de` crudo (si la clave está presente) se copia TAL CUAL, sin
    validar — la validación/resolución final es responsabilidad de
    `_resolver_depende_de`, que necesita ver la lista YA filtrada/truncada
    (las posiciones 0-based de `depende_de` son relativas a ESTA lista
    final, no a `data` crudo, ver docstring del módulo)."""
    if isinstance(data, list):
        crudos: Any = data
    elif isinstance(data, dict):
        crudos = data.get("pasos")
    else:
        crudos = None
    if not isinstance(crudos, list):
        return []

    pasos: list[dict[str, Any]] = []
    for crudo in crudos:
        if not isinstance(crudo, dict):
            continue
        instruccion = str(crudo.get("instruccion") or "").strip()
        if not instruccion:
            continue
        agente = str(crudo.get("agente") or "").strip()
        if agente not in IMPLEMENTED_AGENT_KEYS:
            agente = FALLBACK_AGENT_KEY
        paso: dict[str, Any] = {"agente": agente, "instruccion": instruccion}
        if "depende_de" in crudo:
            paso["depende_de"] = crudo.get("depende_de")
        pasos.append(paso)
        if len(pasos) >= max_steps:
            break
    return pasos


def _coerce_max_steps(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else DEFAULT_MAX_STEPS


def _coerce_parallel_max(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else DEFAULT_PARALLEL_MAX


def _coerce_step_timeout(value: Any) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = 0.0
    return n if n > 0 else float(DEFAULT_STEP_TIMEOUT_SECONDS)


def _coerce_replans_usados(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else 0


def _resolver_perfil(paso: dict[str, Any]) -> AgentProfile:
    """Mismo fallback que usaba `Orchestrator._run_step` inline: un `agente`
    inexistente o `disponible=False` cae a `FALLBACK_AGENT_KEY` ("research").
    Compartido por `_run_step` (para construir el `Agent`) y `_construir_olas`
    (para decidir si el paso puede entrar a una ola paralela)."""
    perfil = PROFILES.get(str(paso.get("agente") or ""))
    if perfil is None or not perfil.disponible:
        perfil = PROFILES[FALLBACK_AGENT_KEY]
    return perfil


def _validar_depende_de(crudo: Any, idx: int, total: int) -> list[int] | None:
    """`None` si `crudo` no es una lista, o si algún valor no es un entero
    (los `bool` se rechazan explícito: son subclase de `int` en Python pero
    `true`/`false` en el JSON del LLM nunca es una lista de índices válida),
    está fuera de `[0, total - 1]`, o es `>= idx` (referencia a sí mismo o a
    un paso posterior — la única forma en que `depende_de` podría formar un
    ciclo, ver docstring del módulo). Si es válida, la normaliza a una lista
    ordenada sin duplicados (determinismo para `_construir_olas`)."""
    if not isinstance(crudo, list):
        return None
    vistos: set[int] = set()
    for valor in crudo:
        if isinstance(valor, bool):
            return None
        try:
            n = int(valor)
        except (TypeError, ValueError):
            return None
        if n < 0 or n >= total or n >= idx:
            return None
        vistos.add(n)
    return sorted(vistos)


def _resolver_depende_de(pasos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Devuelve una copia de `pasos` (mismo orden, mismas demás claves) con
    `"depende_de"` SIEMPRE presente y validado — ver docstring del módulo,
    sección "Dependencias entre pasos", para las 3 reglas exactas (sin la
    clave -> depende de TODOS los anteriores; `[]` explícito -> de ninguno;
    inválida -> se descarta con warning y cae a `[idx - 1]`)."""
    resueltos: list[dict[str, Any]] = []
    total = len(pasos)
    for idx, paso in enumerate(pasos):
        crudo = paso.get("depende_de") if "depende_de" in paso else None
        if crudo is None:
            depende_de = list(range(idx))
        else:
            validado = _validar_depende_de(crudo, idx, total)
            if validado is None:
                logger.warning(
                    "Orchestrator: 'depende_de' inválido (%r) en el paso de índice %s "
                    "(agente=%r); se degrada a depender solo del paso inmediato anterior.",
                    crudo,
                    idx,
                    paso.get("agente"),
                )
                depende_de = [idx - 1] if idx > 0 else []
            else:
                depende_de = validado
        nuevo = dict(paso)
        nuevo["depende_de"] = depende_de
        resueltos.append(nuevo)
    return resueltos


def _construir_olas(
    pasos_pendientes: list[dict[str, Any]], completados_idx: set[int]
) -> list[list[dict[str, Any]]]:
    """Agrupa `pasos_pendientes` (cada uno con `"depende_de"` YA resuelto por
    `_resolver_depende_de`, índices 0-based `seq - 1`) en olas por orden
    topológico sobre `depende_de` — una ola contiene los pasos cuyas
    dependencias ya están todas en `completados_idx` (pasos ya `done`/
    `skipped`/`error`/`cancelled`, sin importar cuál) — y dentro de cada ola
    topológica separa en sub-olas para que NINGÚN paso cuyo perfil tenga
    `permite_dangerous_con_confirmacion=True` comparta ola con otro paso: se
    ejecuta SOLO, en su propia ola (ver docstring del módulo, "Reglas de
    seguridad del paralelismo" #1)."""
    by_idx = {int(p["seq"]) - 1: p for p in pasos_pendientes}
    pendientes = set(by_idx.keys())
    completados = set(completados_idx)
    olas_topologicas: list[list[dict[str, Any]]] = []
    while pendientes:
        elegibles = [
            idx
            for idx in sorted(pendientes)
            if set(int(d) for d in (by_idx[idx].get("depende_de") or [])) <= completados
        ]
        if not elegibles:
            # Salvaguarda defensiva: no debería ocurrir (`depende_de` ya
            # viene validado sin ciclos desde `_resolver_depende_de`), pero
            # si de todos modos llega un plan corrupto (p. ej. cargado a
            # mano desde fuera de este módulo) evita un loop infinito — el
            # resto se fuerza en una ola final sin más chequeo de deps.
            elegibles = sorted(pendientes)
        olas_topologicas.append([by_idx[i] for i in elegibles])
        completados |= set(elegibles)
        pendientes -= set(elegibles)

    olas_finales: list[list[dict[str, Any]]] = []
    for ola in olas_topologicas:
        lote: list[dict[str, Any]] = []
        for paso in ola:
            if _resolver_perfil(paso).permite_dangerous_con_confirmacion:
                if lote:
                    olas_finales.append(lote)
                    lote = []
                olas_finales.append([paso])
            else:
                lote.append(paso)
        if lote:
            olas_finales.append(lote)
    return olas_finales


def _historial_de_dependencias(
    paso: dict[str, Any], resultados: dict[int, str], instrucciones: dict[int, str]
) -> list[ChatMessage]:
    """Historial sintético de UN paso: solo el de SUS dependencias
    declaradas (`paso["depende_de"]`), en orden de índice ascendente — nunca
    el de toda la ola ni el de "todo lo anterior" (ver docstring del módulo,
    "Reglas de seguridad del paralelismo" #3). Una dependencia sin resultado
    todavía (no debería ocurrir: las olas garantizan que toda dependencia ya
    terminó antes de que su paso dependiente se lance) se omite en vez de
    reventar — defensivo, mismo criterio que el resto del módulo."""
    historial: list[ChatMessage] = []
    for idx in sorted(int(d) for d in (paso.get("depende_de") or [])):
        if idx not in resultados:
            continue
        historial.append(ChatMessage(role="user", content=instrucciones.get(idx, "")))
        historial.append(ChatMessage(role="assistant", content=resultados[idx]))
    return historial


def _historial_completo(
    resultados: dict[int, str], instrucciones: dict[int, str]
) -> list[ChatMessage]:
    """Historial COMPLETO (todos los pasos con resultado, en orden de
    índice) — usado SOLO para la síntesis final (`Orchestrator._synthesize`),
    que sí necesita ver la misión entera de punta a punta, a diferencia del
    historial por-paso de `_historial_de_dependencias`."""
    historial: list[ChatMessage] = []
    for idx in sorted(resultados):
        historial.append(ChatMessage(role="user", content=instrucciones.get(idx, "")))
        historial.append(ChatMessage(role="assistant", content=resultados[idx]))
    return historial


__all__ = [
    "DEFAULT_MAX_STEPS",
    "DEFAULT_PARALLEL_MAX",
    "DEFAULT_STEP_TIMEOUT_SECONDS",
    "FALLBACK_AGENT_KEY",
    "MAX_REPLANS_PER_MISSION",
    "Mission",
    "Orchestrator",
    "RunDeps",
]
