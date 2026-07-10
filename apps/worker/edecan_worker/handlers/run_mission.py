"""Job `run_mission`: ejecuta el `Orchestrator` de `edecan_agents` para una
misión (`ROADMAP_V2.md` §7.3, §7.4, §7.6, §7.9; `ARCHITECTURE.md` §10.11;
dueño WP-V2-06).

## Import perezoso de `edecan_agents`

Igual que `edecan_worker.deps` con `edecan_core`/`edecan_db.vault`
(ARCHITECTURE.md §10.1): este módulo importa `edecan_agents` DENTRO de
`handle()`, no a nivel de módulo, porque en este momento del desarrollo v2
es un paquete hermano que puede todavía no existir/estar instalado en un
workspace parcial mientras el resto se construye en paralelo — así,
`edecan_worker.handlers.run_mission` (y por tanto `edecan_worker.handlers`,
que lo registrará en `HANDLERS` de forma defensiva, ver
`edecan_schemas.queue`) se puede seguir importando y testeando aunque
`edecan_agents` aún no exista.

A diferencia de `run_campaign_step.py` (que atrapa `ImportError` porque
`edecan_premium` es un paquete COMERCIAL opcional que un self-host
legítimamente puede no tener instalado, ARCHITECTURE.md §6), aquí NO hay
`try/except ImportError`: `edecan_agents` es parte del core v2 (no un
add-on), así que si el import falla es un error real de despliegue/empaquetado,
no un estado soportado — se deja propagar y el worker lo trata como
cualquier otro fallo de handler (reintento con backoff / DLQ, ver
`edecan_worker.main`).

## Aislamiento multi-tenant

El worker se conecta como "dueño" (bypassa Row-Level Security,
`ARCHITECTURE.md` §2) — TODAS las consultas de este módulo filtran
`tenant_id = env.tenant_id` a mano, igual que `edecan_worker.repo.SqlRepo`.

## SQL directo contra `agent_missions`/`agent_steps`

Igual que `edecan_toolkit.recordatorios`/`edecan_premium.campaigns`/
`edecan_agents.tools`: SQL parametrizado contra los nombres de tabla/columna
pinned en `ROADMAP_V2.md` §7.4 (`edecan_schemas.missions.MissionOut`/
`MissionStepOut` documentan la misma forma, y coinciden con los modelos
`edecan_db.models.AgentMission`/`AgentStep` de la migración
`0003_v2_expansion`, dueño WP-V2-01, ya aterrizada) — deliberadamente NO un
ORM de `edecan_db.models`: esa forma interna no está fijada por el contrato,
los nombres de tabla/columna sí (mismo criterio que `recordatorios.py`, no
una limitación temporal de este archivo). Tampoco se edita
`edecan_db`/`edecan_api.repo`/`edecan_worker.repo` desde este paquete de
trabajo (fuera de la lista de rutas que le corresponde escribir).

## Payload

- Misión nueva: `{"mission_id": "<uuid>"}` — planifica
  (`Orchestrator.plan`), persiste los pasos propuestos como filas
  `agent_steps` (`status='pending'`) y el plan en `agent_missions.plan`, pasa
  la misión a `status='running'` y ejecuta (`Orchestrator.run`).
- Reanudación tras aprobar/rechazar una tool peligrosa (`edecan_agents.
  orchestrator`, perfiles con `permite_dangerous_con_confirmacion=True`,
  WP-V4-05): `{"mission_id": "<uuid>", "resume": true, "approved_step_seq":
  <int>}`. `POST /v1/missions/{id}/confirm`
  (`apps/api/edecan_api/routers/missions.py`) es quien encola este payload
  cuando `approved=true`.

Una misión ya en estado terminal (`done`/`error`/`cancelled`) se ignora sin
error: pudo haberse cancelado mientras el job esperaba en la cola.

## `_RunDeps` (WP-V5-05: `insert_steps` + `presupuesto` en `save_mission`)

`edecan_agents.orchestrator.RunDeps` (el "seam" entre el `Orchestrator` y la
persistencia real) ganó dos capacidades para soportar dependencias/olas/
replan (ver el docstring de ese módulo):

- `insert_steps(pasos)`: crea filas `agent_steps` NUEVAS — reutiliza el mismo
  helper `_insert_steps` que ya usaba la planificación inicial (antes solo
  invocado directo desde `handle()`, ahora también expuesto como el método
  de `_RunDeps` que el `Orchestrator` llama cuando un replan agrega pasos
  a mitad de ejecución).
- `save_mission(..., presupuesto=...)`: `_update_mission` ya sabía actualizar
  `agent_missions.presupuesto` internamente (mismo patrón que `plan`), este
  WP solo expone ese kwarg en la firma pública de `_make_save_mission` para
  que el `Orchestrator` pueda persistir el contador `replans_usados`.

Ninguna de las dos cambia el SQL pinned (`ROADMAP_V2.md` §7.4) ni el resto
del flujo de este handler.

## Durabilidad por paso y reanudación implícita (WP-V7-06, evidencia)

`Orchestrator.run` está documentado como "nunca lanza" (atrapa cualquier
excepción por-paso en `_ejecutar_paso_de_ola` y cualquier excepción
irrecuperable en su propio `try/except` de más alto nivel, ver
`edecan_agents.orchestrator`) — pero eso protege la ORQUESTACIÓN, no la
DURABILIDAD de lo que ya se persistió: antes de este WP, `handle()` abría
UNA sola `async with deps.session_factory(None) as session:` que envolvía
TODO (carga, planificación inicial, y la ejecución COMPLETA de
`orchestrator.run`, incluidos TODOS sus `save_step`/`save_mission`
intermedios — que compartían esa MISMA sesión sin comitear nada hasta el
final). Un `BaseException` genuino escapando de ese árbol completo (el
worker matado a mitad de camino por un redeploy/OOM/host-replacement, una
`asyncio.CancelledError` de una cancelación real de tarea — exactamente el
escenario que `edecan_automations.runner.run_automation` ya documenta
explícitamente como real: "un run que se cuelga o que el worker mata a
mitad de camino") deshacía TODO en el rollback — incluidos pasos que YA
habían corrido con efectos externos reales (una tool que envió un SMS, que
llamó a un MCP de terceros, etc.) y cuyo `agent_steps.status='done'` un
instante antes parecía "exitoso". El reintento del despachador SQS (o una
simple entrega duplicada — SQS es *at-least-once*) volvía a invocar
`handle()` desde cero: sin ningún paso durable, siempre tomaba la rama
"misión nueva" (replanifica desde cero e inserta un plan nuevo/distinto),
re-ejecutando pasos con efectos externos que ya habían ocurrido — mismo
patrón de fondo (evidencia de algo ya ocurrido perdida en un rollback) que
`HOTFIXES_PENDIENTES.md` puntos 8/9 y `campaigns.py::handle` (WP-V6-03),
aplicado acá a `agent_steps`/`agent_missions` en vez de `campaign_targets`/
`consents`.

**Fix (dos capas, mismo espíritu que `campaigns.handle`: "sesiones cortas
por unidad de trabajo")**:

1. **`save_step`/`save_mission`/`insert_steps` ya NO comparten la sesión
   larga**: `_make_save_step`/`_make_save_mission`/`_make_insert_steps`
   reciben `deps.session_factory` (no una `session` ya abierta) y cada
   invocación abre su PROPIA sesión corta, dedicada, que comitea al salir
   limpio — así el checkpoint de CADA paso (y de la misión) queda durable
   en el instante en que ocurre, sin depender de que el resto de la
   ejecución también termine limpio. La sesión larga original SIGUE
   existiendo (`run_deps.session`/`ToolContext.session`, sin cambios): las
   `Tool`s que corren dentro de un paso la siguen usando igual que siempre
   (su propia durabilidad ya está cubierta por una capa distinta —
   `edecan_core.agent.Agent._run_turn` nunca deja que la excepción de UNA
   tool escape hacia el límite de esa transacción, ver el barrido de
   `tools.py` en `docs/cumplimiento/barrido-evidencia-v6.md` — este fix no
   toca ni necesita tocar esa garantía).
2. **La planificación inicial (`_insert_steps` + `_update_mission(status=
   "running", plan=...)`) también comitea ANTES de invocar
   `orchestrator.run`**: en la MISMA sesión corta que la validación de
   arriba (PASO 1 de `handle()`), no en la sesión larga — así, para cuando
   `orchestrator.run` empieza a llamar `save_step` por sesiones
   independientes, las filas `agent_steps` que esas llamadas van a
   `UPDATE` YA EXISTEN y están comiteadas (si no, el `UPDATE` de una sesión
   independiente que no ve el `INSERT` todavía sin comitear de otra sesión
   simplemente no afectaría ninguna fila — un no-op silencioso, no un
   error, el peor tipo de bug).
3. **Reanudación implícita**: la rama "misión nueva" (`resume=False`) ahora
   primero comprueba si YA existen `agent_steps` para esta `mission_id`
   (`_load_steps`). Si los hay (evidencia de un intento previo que sí
   alcanzó a comitear su plan/progreso antes de morir), NO se vuelve a
   llamar `orchestrator.plan()` ni a `_insert_steps` — se reusa el plan
   existente tal cual. `Orchestrator.run` ya sabe distinguir pasos `done`/
   `skipped`/`error`/`cancelled` (los da por completados, no los repite) de
   los que siguen `pending` (los ejecuta) — ver su docstring, sección
   "Dependencias entre pasos y ejecución por olas"; sin este chequeo,
   `handle()` volvía a planificar Y a insertar un plan (posiblemente
   distinto del original, del LLM) ENCIMA de pasos que ya podían existir,
   sin que `agent_steps` tenga un `UNIQUE(tenant_id, mission_id, seq)` que
   lo impidiera.

**Riesgo residual admitido honestamente** (mismo criterio de honestidad que
`HOTFIXES_PENDIENTES.md`, sección "fuga de tareas asyncio"): un paso que
quedó en `status='running'` (su transición inicial, ver
`Orchestrator._run_step`, SÍ se comitea de forma independiente bajo este
fix) en el instante exacto en que el proceso murió — es decir, una tool dentro
de ESE paso puede haber alcanzado a ejecutar un efecto externo real antes del
crash — se reintenta igual en la reanudación implícita (`status='running'`
no es ninguno de `done`/`skipped`/`error`/`cancelled`, así que
`Orchestrator.run` lo trata como pendiente). Cerrar ESE hueco por completo
exigiría claves de idempotencia por-tool-call (fuera de alcance: tocaría
`edecan_core.agent.Agent`/`ToolRegistry`, no este handler) — lo que este fix
sí garantiza es que ya NO se pierden/repiten pasos que alcanzaron a
terminar (`done`/`error`/`skipped`), que es la inmensa mayoría de la
ventana de riesgo real.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from edecan_core.tools import ToolRegistry
from edecan_schemas import PLANES, JobEnvelope
from sqlalchemy import text

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)

_DEFAULT_MAX_STEPS = 8
_TERMINAL_STATUSES = ("done", "error", "cancelled")


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("run_mission requiere tenant_id")
    tenant_id: UUID = env.tenant_id
    mission_id = UUID(str(env.payload["mission_id"]))
    resume = bool(env.payload.get("resume", False))
    approved_step_seq = env.payload.get("approved_step_seq")

    # Import perezoso, ver docstring del módulo.
    from edecan_agents import Mission, Orchestrator

    # PASO 1 — sesión corta: valida, resuelve LLM/MCP/registry, y persiste la
    # transición INICIAL de la misión (plan nuevo o reanudación) — TODO
    # comiteado acá, ANTES de invocar `orchestrator.run` (ver docstring del
    # módulo, "## Durabilidad por paso y reanudación implícita"). Ningún
    # paso todavía ejecutó nada con efecto externo en este punto, así que
    # una excepción acá (misión no encontrada/terminal/resume inválido) es
    # segura de dejar sin comitear nada, igual que antes de este WP.
    async with deps.session_factory(None) as session:
        mission_row = await _load_mission(session, tenant_id, mission_id)
        if mission_row is None:
            logger.error(
                "run_mission: misión %s no encontrada para tenant %s", mission_id, tenant_id
            )
            return
        if mission_row["status"] in _TERMINAL_STATUSES:
            logger.info(
                "run_mission: misión %s ya está en estado terminal (%s); se ignora.",
                mission_id,
                mission_row["status"],
            )
            return

        tenant = await _load_tenant(session, tenant_id)
        plan_key = tenant["plan_key"] if tenant else "free_selfhost"
        flags = dict(PLANES.get(plan_key, PLANES["free_selfhost"]).flags)

        resume_step_seq: int | None = None
        pending_step: dict[str, Any] | None = None

        if resume and approved_step_seq is not None:
            resume_step_seq = int(approved_step_seq)
            pending_step = await _load_step(session, tenant_id, mission_id, resume_step_seq)
            if pending_step is None or pending_step["status"] != "waiting_confirmation":
                logger.warning(
                    "run_mission: resume pedido para el paso %s de la misión %s, pero no está "
                    "waiting_confirmation; se ignora.",
                    resume_step_seq,
                    mission_id,
                )
                return
        elif resume:
            # `resume=True` sin `approved_step_seq` es un payload de cola
            # malformado (el único caller real, `missions.confirm_mission`,
            # siempre manda los dos juntos, ver docstring del módulo) — se
            # rechaza explícito en vez de caer al camino de "misión nueva"
            # de abajo, que replanificaría y volvería a INSERTar en
            # `agent_steps` con `seq` que ya podrían existir (no hay
            # UNIQUE(tenant_id, mission_id, seq) en el esquema).
            logger.warning(
                "run_mission: resume=True sin approved_step_seq para la misión %s; "
                "payload malformado, se ignora.",
                mission_id,
            )
            return

        # Bring-your-own por tenant (WP-V3-02, ver `Deps.llm_router_for`):
        # resuelto PEREZOSO acá, DESPUÉS de TODOS los early-return de arriba
        # (misión no encontrada/terminal, resume con paso inválido/payload
        # malformado) — ninguno de esos casos necesita jamás el LLM, así que
        # no deben fallar solo porque el tenant no conectó un proveedor
        # propio. Lanza `TenantLLMNotConnectedError` (nunca cae a
        # `deps.llm_router` de plataforma) si no se puede resolver — se deja
        # propagar, el despachador del job la trata como cualquier otro
        # fallo (reintento con backoff, luego DLQ/`status='error'` con este
        # mensaje claro en `last_error`).
        llm_router = await deps.llm_router_for(tenant_id)
        registry = _build_registry()
        # MCP bring-your-own (ARCHITECTURE.md §15): se registran en ESTE
        # `ToolRegistry` recién construido (uno nuevo por job, nunca el
        # compartido de `edecan_api`) ANTES de construir el `Orchestrator` —
        # así el `RestrictedRegistry` que arma `Orchestrator._run_step` por
        # `AgentProfile.allowed_tools` (ver docstring de `run_automation.py`,
        # mismo criterio acá) se aplica DESPUÉS del merge: un perfil sin
        # `mcp_*` en `allowed_tools` simplemente no las ve.
        for mcp_tool in await deps.mcp_tools_para(tenant_id, session, flags):
            registry.register(mcp_tool)
        orchestrator = Orchestrator(llm_router, registry)

        approved_tool_call_id: str | None = None
        approved_tool_name: str | None = None
        approved_tool_args: dict[str, Any] | None = None

        if resume_step_seq is not None:
            assert pending_step is not None  # validado arriba
            usage = pending_step.get("usage") or {}
            pending_call = usage.get("pending_tool_call") or {}
            approved_tool_call_id = pending_call.get("id")
            approved_tool_name = pending_call.get("name")
            approved_tool_args = pending_call.get("args") or {}
            # El paso vuelve a "pending" para que `Orchestrator.run` lo trate
            # como ejecutable de nuevo: ejecuta DIRECTO la tool/args
            # aprobados (inyectados vía `Mission.approved_tool_name`/
            # `approved_tool_args`, ver `edecan_agents.orchestrator.Mission`/
            # `Orchestrator._run_resumed_step`) en vez de volver a llamar al
            # LLM, que acuñaría un `tool_call_id` nuevo que jamás
            # coincidiría con `approved_tool_call_id`.
            await _update_step(session, tenant_id, mission_id, resume_step_seq, status="pending")
            await _update_mission(session, tenant_id, mission_id, status="running")
        else:
            # Reanudación IMPLÍCITA (ver docstring del módulo): si YA hay
            # `agent_steps` persistidos para esta misión (un intento previo
            # alcanzó a comitear su plan antes de morir/ser reintentado), NO
            # se replanifica desde cero — se reusa el plan existente,
            # `Orchestrator.run` se encarga de saltar lo que ya no está
            # `pending`.
            existing_steps = await _load_steps(session, tenant_id, mission_id)
            if existing_steps:
                logger.info(
                    "run_mission: misión %s ya tenía %d paso(s) persistido(s) — reanudación "
                    "implícita, no se replanifica.",
                    mission_id,
                    len(existing_steps),
                )
                await _update_mission(session, tenant_id, mission_id, status="running")
            else:
                pasos = await orchestrator.plan(mission_row["objetivo"], flags, deps.settings)
                await _insert_steps(session, tenant_id, mission_id, pasos)
                await _update_mission(session, tenant_id, mission_id, status="running", plan=pasos)

        # Relectura DENTRO de esta misma sesión (todavía sin comitear, pero
        # esta sesión SÍ ve sus propios writes) — `plan` refleja exactamente
        # lo que está por comitear: el paso reanudado en 'pending', los pasos
        # ya existentes tal cual, o el plan recién insertado.
        plan = await _load_steps(session, tenant_id, mission_id)

    # La sesión de arriba ya comiteó (PASO 1 completo y durable). A partir de
    # acá, `save_step`/`save_mission`/`insert_steps` abren SU PROPIA sesión
    # corta por invocación (ver `_make_save_step` et al. y el docstring del
    # módulo) — nunca la sesión larga de abajo.
    mission = Mission(
        id=mission_id,
        tenant_id=tenant_id,
        user_id=UUID(str(mission_row["user_id"])),
        objetivo=mission_row["objetivo"],
        plan=plan,
        presupuesto=mission_row.get("presupuesto") or {"max_steps": _DEFAULT_MAX_STEPS},
        resume_step_seq=resume_step_seq,
        approved_tool_call_id=approved_tool_call_id,
        approved_tool_name=approved_tool_name,
        approved_tool_args=approved_tool_args,
    )

    # Sesión de trabajo del turno: vive solo para `ctx.session`/`vault` (lo
    # que las `Tool`s usan durante cada paso — sin cambios respecto a antes
    # de este WP, ver docstring del módulo). `save_step`/`save_mission`/
    # `insert_steps` YA NO cierran sobre esta sesión.
    async with deps.session_factory(None) as session:
        run_deps = _RunDeps(
            session=session,
            settings=deps.settings,
            vault=deps.vault(session),
            flags=flags,
            save_step=_make_save_step(deps.session_factory, tenant_id, mission_id),
            save_mission=_make_save_mission(deps.session_factory, tenant_id, mission_id),
            insert_steps=_make_insert_steps(deps.session_factory, tenant_id, mission_id),
        )

        await orchestrator.run(mission, run_deps)

    logger.info("run_mission completado mission_id=%s tenant_id=%s", mission_id, tenant_id)


def _build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.load_entry_points(group="edecan.tools")
    return registry


class _RunDeps:
    """Implementación concreta de `edecan_agents.orchestrator.RunDeps` sobre
    SQL real sobre `session` (una `AsyncSession` ya abierta por
    `deps.session_factory(None)`, conexión "dueño" — ver docstring del
    módulo)."""

    def __init__(
        self,
        *,
        session: Any,
        settings: Any,
        vault: Any,
        flags: dict[str, Any],
        save_step: Any,
        save_mission: Any,
        insert_steps: Any,
    ) -> None:
        self.session = session
        self.settings = settings
        self.vault = vault
        self.flags = flags
        self.save_step = save_step
        self.save_mission = save_mission
        self.insert_steps = insert_steps


def _make_save_step(session_factory: Any, tenant_id: UUID, mission_id: UUID) -> Any:
    """A diferencia de antes de WP-V7-06, recibe `deps.session_factory` (NO
    una `session` ya abierta): cada invocación abre su PROPIA sesión corta,
    que comitea al salir limpio — ver docstring del módulo, "## Durabilidad
    por paso y reanudación implícita". Para cuando `Orchestrator.run` llama
    a esto, la fila `agent_steps` que este `UPDATE` toca YA existe y está
    comiteada (el plan inicial se persistió en su propia sesión corta antes
    de invocar `orchestrator.run`, ver `handle()`) — si no existiera, este
    `UPDATE` sería un no-op silencioso (ninguna fila con ese `WHERE`), no un
    error, así que el orden entre ambos importa de verdad."""

    async def _save_step(
        *,
        seq: int,
        status: str | None = None,
        resultado: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        async with session_factory(None) as session:
            await _update_step(
                session, tenant_id, mission_id, seq, status=status, resultado=resultado, usage=usage
            )

    return _save_step


def _make_save_mission(session_factory: Any, tenant_id: UUID, mission_id: UUID) -> Any:
    """Ídem `_make_save_step`: sesión corta e independiente por invocación."""

    async def _save_mission(
        *,
        status: str | None = None,
        resultado: str | None = None,
        error: str | None = None,
        presupuesto: dict[str, Any] | None = None,
    ) -> None:
        async with session_factory(None) as session:
            await _update_mission(
                session,
                tenant_id,
                mission_id,
                status=status,
                resultado=resultado,
                error=error,
                presupuesto=presupuesto,
            )

    return _save_mission


def _make_insert_steps(session_factory: Any, tenant_id: UUID, mission_id: UUID) -> Any:
    """`edecan_agents.orchestrator.RunDeps.insert_steps` (WP-V5-05, replan) —
    reutiliza `_insert_steps` tal cual, el mismo helper que ya usa `handle()`
    para persistir el plan inicial. Ídem `_make_save_step` (WP-V7-06):
    sesión corta e independiente por invocación, en vez de la sesión larga
    del turno."""

    async def _insert_steps_dep(pasos: list[dict[str, Any]]) -> None:
        async with session_factory(None) as session:
            await _insert_steps(session, tenant_id, mission_id, pasos)

    return _insert_steps_dep


# ---------------------------------------------------------------------------
# SQL directo (ver docstring del módulo: nombres pinned en ROADMAP_V2.md §7.4)
# ---------------------------------------------------------------------------


async def _load_mission(session: Any, tenant_id: UUID, mission_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT id, tenant_id, user_id, objetivo, status, plan, resultado, "
            "presupuesto, error FROM agent_missions "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": str(tenant_id), "id": str(mission_id)},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _load_tenant(session: Any, tenant_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text("SELECT plan_key FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _load_steps(session: Any, tenant_id: UUID, mission_id: UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT seq, agente, instruccion, status, resultado, usage FROM agent_steps "
            "WHERE tenant_id = :tenant_id AND mission_id = :mission_id ORDER BY seq ASC"
        ),
        {"tenant_id": str(tenant_id), "mission_id": str(mission_id)},
    )
    return [_paso_con_depende_de(dict(row)) for row in result.mappings().all()]


async def _load_step(
    session: Any, tenant_id: UUID, mission_id: UUID, seq: int
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT seq, agente, instruccion, status, resultado, usage FROM agent_steps "
            "WHERE tenant_id = :tenant_id AND mission_id = :mission_id AND seq = :seq"
        ),
        {"tenant_id": str(tenant_id), "mission_id": str(mission_id), "seq": seq},
    )
    row = result.mappings().first()
    return _paso_con_depende_de(dict(row)) if row is not None else None


def _paso_con_depende_de(row: dict[str, Any]) -> dict[str, Any]:
    """Extrae `depende_de` (WP-V5-05) de `usage` hacia una clave propia del
    dict devuelto — ver docstring del módulo, sección `_RunDeps`: `agent_steps`
    no tiene columna propia para `depende_de` (`ROADMAP_V2.md` §7.4, sin
    migración nueva permitida), así que `_insert_steps` lo esconde dentro de
    `usage` (`{"depende_de": [...]}`) en el INSERT. Sobrevive mientras el
    paso siga `pending` (nadie más toca `usage` hasta que el paso corre de
    verdad); en cuanto corre, `_run_step`/`_run_resumed_step` sobreescriben
    `usage` con datos reales (`pending_tool_call` o `{input,output}_tokens`)
    y `depende_de` deja de estar disponible — momento en el que ya no hace
    falta (`Orchestrator.run` solo necesita `depende_de` de pasos PENDIENTES
    para construir olas, nunca de uno ya `done`/`waiting_confirmation`)."""
    usage = row.get("usage")
    if isinstance(usage, dict) and "depende_de" in usage:
        row["depende_de"] = usage["depende_de"]
        resto = {k: v for k, v in usage.items() if k != "depende_de"}
        row["usage"] = resto or None
    return row


async def _insert_steps(
    session: Any, tenant_id: UUID, mission_id: UUID, pasos: list[dict[str, Any]]
) -> None:
    """Inserta filas `agent_steps` nuevas — usado tanto para el plan inicial
    (`handle()`) como para los pasos que agrega un replan a mitad de
    ejecución (`RunDeps.insert_steps`, WP-V5-05). Si `paso` trae
    `"depende_de"` (siempre lo trae si viene de `Orchestrator.plan()`/
    `Orchestrator._replan`, ver ese módulo), se esconde dentro de `usage`
    (`{"depende_de": [...]}`) — ver `_paso_con_depende_de` para el porqué y
    la vida útil de ese valor."""
    for paso in pasos:
        depende_de = paso.get("depende_de")
        usage = json.dumps({"depende_de": depende_de}) if depende_de is not None else None
        await session.execute(
            text(
                "INSERT INTO agent_steps "
                "(id, tenant_id, mission_id, seq, agente, instruccion, status, resultado, usage) "
                "VALUES (gen_random_uuid(), :tenant_id, :mission_id, :seq, :agente, "
                ":instruccion, 'pending', NULL, :usage ::jsonb)"
            ),
            {
                "tenant_id": str(tenant_id),
                "mission_id": str(mission_id),
                "seq": paso["seq"],
                "agente": paso["agente"],
                "instruccion": paso["instruccion"],
                "usage": usage,
            },
        )


async def _update_mission(
    session: Any,
    tenant_id: UUID,
    mission_id: UUID,
    *,
    status: str | None = None,
    plan: list[dict[str, Any]] | None = None,
    resultado: str | None = None,
    error: str | None = None,
    presupuesto: dict[str, Any] | None = None,
) -> None:
    """`None` en cualquier campo (salvo `status`, que casi siempre se pasa)
    significa "no lo toques" — actualización parcial, mismo criterio que
    `edecan_api.routers.reminders.ReminderPatch`. `presupuesto` (WP-V5-05):
    así persiste `Orchestrator.run` el contador `replans_usados` tras un
    replan (ver `edecan_agents.orchestrator`, sección "Replan acotado")."""
    sets = ["updated_at = now()"]
    params: dict[str, Any] = {"tenant_id": str(tenant_id), "id": str(mission_id)}
    if status is not None:
        sets.append("status = :status")
        params["status"] = status
    if plan is not None:
        sets.append("plan = :plan ::jsonb")
        params["plan"] = json.dumps(plan)
    if resultado is not None:
        sets.append("resultado = :resultado")
        params["resultado"] = resultado
    if error is not None:
        sets.append("error = :error")
        params["error"] = error
    if presupuesto is not None:
        sets.append("presupuesto = :presupuesto ::jsonb")
        params["presupuesto"] = json.dumps(presupuesto)
    if len(sets) == 1:  # solo `updated_at`: nada que actualizar de verdad.
        return
    await session.execute(
        text(
            f"UPDATE agent_missions SET {', '.join(sets)} WHERE tenant_id = :tenant_id AND id = :id"
        ),
        params,
    )


async def _update_step(
    session: Any,
    tenant_id: UUID,
    mission_id: UUID,
    seq: int,
    *,
    status: str | None = None,
    resultado: str | None = None,
    usage: dict[str, Any] | None = None,
) -> None:
    sets = ["updated_at = now()"]
    params: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "mission_id": str(mission_id),
        "seq": seq,
    }
    if status is not None:
        sets.append("status = :status")
        params["status"] = status
    if resultado is not None:
        sets.append("resultado = :resultado")
        params["resultado"] = resultado
    if usage is not None:
        sets.append("usage = :usage ::jsonb")
        params["usage"] = json.dumps(usage)
    if len(sets) == 1:
        return
    await session.execute(
        text(
            f"UPDATE agent_steps SET {', '.join(sets)} "
            "WHERE tenant_id = :tenant_id AND mission_id = :mission_id AND seq = :seq"
        ),
        params,
    )
