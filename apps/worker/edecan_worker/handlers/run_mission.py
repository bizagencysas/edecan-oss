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
ejecución también termine limpio.

    **BOTS-23 (posterior a WP-V7-06):** la sesión larga original ya NO existe:
    `_RunDeps` no expone `session`/`vault`, expone `session_factory`/
    `vault_factory`. CADA paso paralelo abre SU propia `AsyncSession`
    tenant-scoped (`session_factory(mission.tenant_id)`, con RLS activo para
    ese tenant) y su propio vault (`vault_factory(session)`) — dos pasos de una
    misma ola ya no comparten `AsyncSession` (SQLAlchemy la declara no segura
    en tareas concurrentes), así que el rollback de uno no arrastra al otro.
    La durabilidad de las tools sigue cubierta por la capa de `Agent._run_turn`
    (ver el punto 1 de arriba).
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

from edecan_core.notifications import ImportantNotificationEvent
from edecan_core.tools import ToolRegistry, registry_para_tenant
from edecan_schemas import PLANES, JobEnvelope
from sqlalchemy import text

from edecan_worker.deps import Deps
from edecan_worker.universal_notifications import notify_important_event

logger = logging.getLogger(__name__)

_DEFAULT_MAX_STEPS = 8
_TERMINAL_STATUSES = ("done", "error", "cancelled")
_RUN_STOP_STATUSES = ("cancelled", "paused")
"""BOTS-01: estados que un run activo NO controla. `cancellation_requested`
devuelve `True` ante ellos; `_update_mission(guard_active=True)` los incluye
(junto a `done`/`error`) en la condición `status NOT IN (...)` para que una
escritura tardía del orquestador nunca los sobrescriba."""
_GUARD_STATUSES = ("done", "error", "cancelled", "paused")


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("run_mission requiere tenant_id")
    tenant_id: UUID = env.tenant_id
    mission_id = UUID(str(env.payload["mission_id"]))
    resume = bool(env.payload.get("resume", False))
    resume_paused = bool(env.payload.get("resume_paused", False))
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
        if mission_row["status"] == "paused" and not resume_paused:
            logger.info(
                "run_mission: misión %s está pausada; se conserva el checkpoint.", mission_id
            )
            return
        if resume_paused and mission_row["status"] != "paused":
            logger.warning("run_mission: resume_paused inválido para misión %s", mission_id)
            return

        tenant = await _load_tenant(session, tenant_id)
        plan_key = tenant["plan_key"] if tenant else "free_selfhost"
        flags = dict(PLANES.get(plan_key, PLANES["free_selfhost"]).flags)

        resume_step_seq: int | None = None
        pending_step: dict[str, Any] | None = None

        if resume_paused:
            await _update_mission(session, tenant_id, mission_id, status="running")
        elif resume and approved_step_seq is not None:
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

        # Se resuelve después de todos los early-return para no inicializar
        # inferencia en una misión inválida o ya terminal.
        llm_router = await deps.llm_router_for(tenant_id)
        registry = _build_registry(tenant_id)
        # MCP bring-your-own (ARCHITECTURE.md §15): se registran en ESTE
        # `ToolRegistry` recién construido (uno nuevo por job, nunca el
        # compartido de `edecan_api`) ANTES de construir el `Orchestrator` —
        # así el `RestrictedRegistry` que arma `Orchestrator._run_step` por
        # `AgentProfile.allowed_tools` (ver docstring de `run_automation.py`,
        # mismo criterio acá) se aplica DESPUÉS del merge: un perfil sin
        # `mcp_*` en `allowed_tools` simplemente no las ve.
        for mcp_tool in await deps.mcp_tools_para(tenant_id, session, flags):
            registry.register(mcp_tool)
        orchestrator_kwargs = {}
        if deps.provider_health is not None:
            orchestrator_kwargs["provider_health"] = deps.provider_health
        orchestrator = Orchestrator(llm_router, registry, **orchestrator_kwargs)

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
            #
            # C8b: el reseteo es un CLAIM durable (CAS `waiting_confirmation` →
            # `pending`), no un UPDATE ciego — un job `resume` duplicado
            # (SQS at-least-once) ve el paso ya `pending` y pierde el claim,
            # así no se re-ejecuta el paso aprobado dos veces.
            claimed_step = await _claim_step(
                session,
                tenant_id,
                mission_id,
                resume_step_seq,
                expected_status="waiting_confirmation",
            )
            if not claimed_step:
                logger.info(
                    "run_mission: paso %s de la misión %s ya fue reclamado por otro run; "
                    "se ignora.",
                    resume_step_seq,
                    mission_id,
                )
                return
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
                # C8b: claim durable ANTES de planificar — dos jobs
                # `run_mission` concurrentes para una misión nueva compiten por
                # `planning` → `running` y solo UNO gana; el perdedor sale sin
                # llamar al LLM de planificación ni insertar pasos. El claim va
                # en la MISMA sesión corta que la planificación/INSERT de pasos,
                # así que un crash antes del commit lo deshace TODO (la misión
                # sigue `planning`, sin pasos a medias ni estado "running"
                # huérfano).
                claimed = await _claim_mission(session, tenant_id, mission_id, "planning")
                if not claimed:
                    logger.info(
                        "run_mission: misión %s ya fue reclamada por otro run; se ignora.",
                        mission_id,
                    )
                    return
                pasos = await orchestrator.plan(
                    _objetivo_con_steering(mission_row), flags, deps.settings
                )
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
        objetivo=_objetivo_con_steering(mission_row),
        plan=plan,
        presupuesto=mission_row.get("presupuesto") or {"max_steps": _DEFAULT_MAX_STEPS},
        resume_step_seq=resume_step_seq,
        approved_tool_call_id=approved_tool_call_id,
        approved_tool_name=approved_tool_name,
        approved_tool_args=approved_tool_args,
    )

    # BOTS-23: NO hay una sesión larga de turno. `_RunDeps` recibe las
    # FACTORIES (`session_factory`/`vault_factory`) y CADA paso paralelo abre
    # su propia `AsyncSession` tenant-scoped + su propio vault (`session`/
    # `vault` compartidos ya no existen). `save_step`/`save_mission`/
    # `insert_steps` siguen abriendo su propia sesión corta por invocación
    # (WP-V7-06). `cancellation_requested` relee el estado durable antes de
    # cada ola y antes del commit terminal (BOTS-01).
    run_deps = _RunDeps(
        session_factory=deps.session_factory,
        vault_factory=deps.vault,
        settings=deps.settings,
        flags=flags,
        cancellation_requested=_make_cancellation_check(
            deps.session_factory, tenant_id, mission_id
        ),
        save_step=_make_save_step(deps.session_factory, tenant_id, mission_id),
        save_mission=_make_save_mission(deps.session_factory, tenant_id, mission_id),
        insert_steps=_make_insert_steps(deps.session_factory, tenant_id, mission_id),
    )

    await orchestrator.run(mission, run_deps)

    # El estado de la misión fue guardado por ``save_mission`` en una sesión
    # independiente. Se relee y solo se avisa por una transición terminal;
    # waiting_confirmation/running siguen visibles en Actividad sin ruido.
    async with deps.session_factory(None) as session:
        final_mission = await _load_mission(session, tenant_id, mission_id)
    if final_mission is not None and final_mission["status"] in {"done", "error"}:
        kind = "work_completed" if final_mission["status"] == "done" else "work_failed"
        # Push con RESUMEN real (el dueño pidió ChatGPT-like: al terminar,
        # el push dice qué hizo; al abrir, el trabajo está en el chat).
        resumen = " ".join(str(final_mission.get("resultado") or "").split())[:160]
        await notify_important_event(
            deps,
            ImportantNotificationEvent(
                tenant_id=tenant_id,
                user_id=UUID(str(final_mission["user_id"])),
                kind=kind,
                event_id=mission_id,
                resource_id=mission_id,
                apns_title="Misión terminada" if kind == "work_completed" else "La misión falló",
                apns_body=resumen or "El trabajo terminó. Ábrelo para ver el resultado.",
            ),
        )
        # La misión nació del CHAT de un bot (owner_agent_id): el resultado
        # NO se queda solo en Misiones — se entrega en el chat del bot y el
        # bot se despierta para preguntarle al dueño si procede a ejecutar
        # (el dueño pidió exactamente este flujo: el bot recibe el resultado
        # de Astra y pregunta, como los demás).
        if final_mission.get("owner_agent_id"):
            await _entregar_resultado_al_chat_del_bot(
                deps,
                tenant_id=tenant_id,
                mission=final_mission,
            )

    logger.info("run_mission completado mission_id=%s tenant_id=%s", mission_id, tenant_id)


async def _entregar_resultado_al_chat_del_bot(
    deps: Deps, *, tenant_id: UUID, mission: dict[str, Any]
) -> None:
    """Entrega el resultado de la misión en el chat del bot que la creó y lo
    despierta con un turno que le pide preguntar al dueño cómo proceder."""
    worker_id_raw = mission.get("owner_agent_id")
    try:
        worker_id = UUID(str(worker_id_raw))
    except (TypeError, ValueError):
        logger.warning("owner_agent_id no es UUID (%r)", worker_id_raw)
        return
    resultado = str(mission.get("resultado") or "").strip() or "(sin texto)"
    estado = str(mission.get("status") or "")
    titulo = "terminó" if estado == "done" else "falló"
    try:
        async with deps.session_factory(None) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT conversation_id, name FROM persistent_agents "
                        "WHERE tenant_id = :tenant_id AND id = :id"
                    ),
                    {"tenant_id": str(tenant_id), "id": str(worker_id)},
                )
            ).mappings().first()
            if row is None:
                return
            conversation_id = str(row["conversation_id"])
            sender_name = str(row["name"] or "Bot").strip()
            await session.execute(
                text(
                    "INSERT INTO messages "
                    "(id, conversation_id, tenant_id, role, content, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :cid, :tid, 'assistant', :content ::jsonb, now(), now())"
                ),
                {
                    "cid": conversation_id,
                    "tid": str(tenant_id),
                    "content": json.dumps(
                        {
                            "text": (
                                f"La misión que delegaste {titulo}. Resultado: "
                                f"{resultado}"
                            ),
                            "sender_id": str(worker_id),
                            "sender_name": sender_name,
                            "mission_id": str(mission["id"]),
                            "mission_status": estado,
                        },
                        default=str,
                    ),
                },
            )
    except Exception:  # noqa: BLE001 - la entrega jamás debe romper run_mission
        logger.exception("no pude entregar el resultado en el chat del bot")
        return
    try:
        from edecan_core.queue import enqueue_outbox

        async with deps.session_factory(None) as session:
            await enqueue_outbox(
                session,
                tenant_id=tenant_id,
                job_type="run_persistent_agent",
                payload={
                    "worker_id": str(worker_id),
                    "instruction": (
                        f"La misión {mission['id']} {titulo}. Su resultado YA quedó "
                        "escrito en tu chat. Revisa si el resultado menciona "
                        "archivos entregables (.md, PDF, código, imágenes): "
                        "LÉELOS con `acceder_codigo_local` y preséntalos "
                        "COMPLETOS en el chat en su formato. Luego pregúntale "
                        "al dueño, con una tarjeta de opciones, si procedes a "
                        "EJECUTAR las recomendaciones, y con qué modelo quiere "
                        "que lo hagas (menciónale Luna y los modelos de Workers AI)."
                    ),
                    "task_id": f"entrega-mision:{mission['id']}",
                    "source": "delegacion_resultado",
                },
            )
    except Exception:  # noqa: BLE001
        logger.exception("no pude despertar al bot tras la entrega")



def _build_registry(tenant_id: UUID | None = None) -> ToolRegistry:
    import os

    root = os.environ.get("EDECAN_PLUGINS_DIR") or "/opt/edecan/data/plugins"
    if tenant_id is None:
        # Llamadores que todavía no pasan tenant (p. ej. `run_companion_turn`,
        # fuera del alcance BOTS-14) conservan el comportamiento previo: solo
        # el nivel raíz, sin subdirectorio por tenant — sin regresión.
        registry = ToolRegistry()
        registry.load_entry_points(group="edecan.tools")
        registry.load_plugin_dir(root)
        return registry
    return registry_para_tenant(root, tenant_id)


class _RunDeps:
    """Implementación concreta de `edecan_agents.orchestrator.RunDeps` sobre
    SQL real. BOTS-23: NO expone una `AsyncSession`/`vault` compartidos —
    expone `session_factory` (cada paso paralelo abre su propia sesión
    tenant-scoped) y `vault_factory` (un vault por sesión de paso). BOTS-01:
    `cancellation_requested` relee el estado durable de `agent_missions` en
    una sesión corta (dueño, filtro manual por `tenant_id`)."""

    def __init__(
        self,
        *,
        session_factory: Any,
        vault_factory: Any,
        settings: Any,
        flags: dict[str, Any],
        cancellation_requested: Any,
        save_step: Any,
        save_mission: Any,
        insert_steps: Any,
    ) -> None:
        self.session_factory = session_factory
        self.vault_factory = vault_factory
        self.settings = settings
        self.flags = flags
        self.cancellation_requested = cancellation_requested
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
    """Ídem `_make_save_step`: sesión corta e independiente por invocación.
    BOTS-01: pasa `guard_active=True` a `_update_mission` para que la escritura
    del orquestador JAMÁS sobrescriba un estado terminal/pausado (`done`/
    `error`/`cancelled`/`paused`) que el usuario (la API) ya dejó en la fila —
    una escritura tardía de un paso que terminaba justo cuando el usuario
    canceló no revive la misión `cancelled`."""

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
                guard_active=True,
            )

    return _save_mission


def _make_cancellation_check(
    session_factory: Any, tenant_id: UUID, mission_id: UUID
) -> Any:
    """`RunDeps.cancellation_requested` (BOTS-01): relee el estado durable de
    `agent_missions` en una sesión corta y devuelve `True` si la misión ya no
    está en un estado que este run controle (`cancelled`/`paused`). El
    `Orchestrator` lo consulta ANTES de lanzar cada ola y ANTES del commit
    terminal — nunca se confía en un flag local que pueda quedar
    desincronizado entre el worker y la API."""

    async def _cancellation_requested() -> bool:
        async with session_factory(None) as session:
            row = await _load_mission(session, tenant_id, mission_id)
        if row is None:
            # La misión desapareció (borrado externo): tratar como cancelada
            # es lo seguro — nunca seguir haciendo trabajo sobre algo que ya
            # no existe.
            return True
        return row["status"] in _RUN_STOP_STATUSES

    return _cancellation_requested


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


def _objetivo_con_steering(mission_row: dict[str, Any]) -> str:
    """Aplica direcciones mid-run sin reescribir el objetivo original."""
    objetivo = str(mission_row.get("objetivo") or "").strip()
    presupuesto = mission_row.get("presupuesto") or {}
    if isinstance(presupuesto, str):
        try:
            presupuesto = json.loads(presupuesto)
        except json.JSONDecodeError:
            presupuesto = {}
    if not isinstance(presupuesto, dict):
        return objetivo
    notes = presupuesto.get("steering") or []
    lines: list[str] = []
    if isinstance(notes, list):
        for note in notes:
            if isinstance(note, dict):
                text = str(note.get("instruction") or "").strip()
            else:
                text = str(note).strip()
            if text:
                lines.append(text)
    if not lines:
        return objetivo
    joined = "\n".join(f"- {line}" for line in lines[-12:])
    return (
        f"{objetivo}\n\nInstrucciones añadidas mientras trabajabas "
        f"(no reinicies; adáptate):\n{joined}"
    )


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
    guard_active: bool = False,
) -> None:
    """`None` en cualquier campo (salvo `status`, que casi siempre se pasa)
    significa "no lo toques" — actualización parcial, mismo criterio que
    `edecan_api.routers.reminders.ReminderPatch`. `presupuesto` (WP-V5-05):
    así persiste `Orchestrator.run` el contador `replans_usados` tras un
    replan (ver `edecan_agents.orchestrator`, sección "Replan acotado").

    `guard_active` (BOTS-01): cuando es `True`, el `UPDATE` añade
    `AND status NOT IN (...)` a su `WHERE` — la escritura del orquestador
    JAMÁS sobrescribe un estado que este run ya no controla (`done`/`error`/
    `cancelled`/`paused`). Es una condición ATÓMICA en la base de datos, no un
    check-then-act: si el usuario canceló/pausó entre medias, la fila
    simplemente no se toca. Solo lo activa `_make_save_mission` (el seam del
    `Orchestrator`); las transiciones de PASO 1 (`handle`) siguen sin guarda
    porque son legítimas y ya van precedidas de su propio chequeo de estado."""
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
    where = "WHERE tenant_id = :tenant_id AND id = :id"
    if guard_active:
        literal = ", ".join(f"'{s}'" for s in _GUARD_STATUSES)
        where += f" AND status NOT IN ({literal})"
    await session.execute(
        text(f"UPDATE agent_missions SET {', '.join(sets)} {where}"),
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


async def _claim_mission(
    session: Any, tenant_id: UUID, mission_id: UUID, expected_status: str
) -> bool:
    """Claim durable de la misión (C8b): CAS `status = 'running'` SOLO si la
    fila sigue en `expected_status` (p. ej. `'planning'`). Devuelve `True` si
    afectó UNA fila (este run ganó el claim), `False` si otro run/request ya la
    movió — en cuyo caso el llamador debe salir sin ejecutar nada.

    La transición es ATÓMICA en la base de datos (un único UPDATE condicional),
    no un check-then-act: dos jobs `run_mission` concurrentes para la misma
    misión compiten por el claim y solo uno lo gana, evitando planificar/
    ejecutar los mismos pasos dos veces (hallazgo C8b).
    """
    result = await session.execute(
        text(
            "UPDATE agent_missions SET status = 'running', updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :id AND status = :expected"
        ),
        {
            "tenant_id": str(tenant_id),
            "id": str(mission_id),
            "expected": expected_status,
        },
    )
    return (getattr(result, "rowcount", 1) or 0) == 1


async def _claim_step(
    session: Any,
    tenant_id: UUID,
    mission_id: UUID,
    seq: int,
    *,
    expected_status: str,
    new_status: str = "pending",
) -> bool:
    """Claim durable de un paso (C8b): CAS `status = :new_status` SOLO si el
    paso sigue en `expected_status` (p. ej. `'waiting_confirmation'` →
    `'pending'`). Devuelve `True` solo si ganó el claim. Un job `run_mission`
    `resume` duplicado (SQS at-least-once) ve el paso ya `pending` y pierde el
    claim → no re-ejecuta el paso aprobado dos veces.
    """
    result = await session.execute(
        text(
            "UPDATE agent_steps SET status = :new_status, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND mission_id = :mission_id AND seq = :seq "
            "AND status = :expected"
        ),
        {
            "tenant_id": str(tenant_id),
            "mission_id": str(mission_id),
            "seq": seq,
            "expected": expected_status,
            "new_status": new_status,
        },
    )
    return (getattr(result, "rowcount", 1) or 0) == 1
