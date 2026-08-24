"""Job `run_automation`: corre UNA automatización en modo headless (turno de
agente sin usuario presente) y persiste el resultado en `automation_runs`
(ROADMAP_V2.md §7.3, §7.4, §7.6; `ARCHITECTURE.md` §10.11; dueño WP-V2-07).

## Import perezoso de `edecan_automations`

Igual que `edecan_worker.handlers.run_mission` con `edecan_agents` (ver su
docstring): `edecan_automations` se importa DENTRO de `handle()`, no a nivel
de módulo, porque en este momento del desarrollo v2 es un paquete hermano
que puede todavía no existir/estar instalado en un workspace parcial
mientras el resto se construye en paralelo (ARCHITECTURE.md §10.1) — así
este módulo (y `edecan_worker.handlers`, que lo registra en `HANDLERS` de
forma defensiva, ver `edecan_schemas.queue`) se puede seguir importando y
testeando aunque `edecan_automations` aún no exista. Sin `try/except
ImportError`: es parte del core v2 (no un add-on opcional, a diferencia de
`edecan_premium`), así que si el import falla es un error real de
despliegue/empaquetado — se deja propagar y el worker lo trata como
cualquier otro fallo de handler (reintento con backoff / DLQ, ver
`edecan_worker.main`). `edecan_core.tools.ToolContext`/`ToolRegistry` SÍ se
importan arriba, sin perezosidad: `edecan_core` es v1, ya estable (mismo
criterio que `run_mission.py`).

## Aislamiento multi-tenant y SQL directo

El worker se conecta como "dueño" (bypassa Row-Level Security,
`ARCHITECTURE.md` §2) — TODAS las consultas de este módulo filtran
`tenant_id = env.tenant_id` a mano. Igual que `run_mission.py`: SQL
parametrizado directo contra `automations`/`automation_runs`
(ROADMAP_V2.md §7.4) — no un ORM de `edecan_db.models`, que a la fecha de
este archivo todavía no declara esas dos tablas (migración
`0003_v2_expansion`, dueño WP-V2-01). No se reutiliza
`edecan_worker.repo.SqlRepo` ni siquiera para lo que ya tiene (`get_tenant`,
`get_persona`): mismo criterio de auto-contención que `run_mission.py` (no
se edita `edecan_worker.repo` desde este paquete de trabajo, fuera de su
lista de rutas).

## Verificaciones antes de correr

Tres condiciones deben cumplirse o el job termina sin ejecutar NADA (ni
siquiera crea una fila `automation_runs` — no hubo intento real que
auditar, mismo criterio que `send_reminder.py` cuando el recordatorio ya no
está `pending`): automatización encontrada para ESE tenant, `enabled=true`
(protege contra una automatización desactivada justo después de que
`automation_scan`/un webhook ya la encoló — condición de carrera esperada,
no un bug), y el plan del tenant sigue trayendo el flag `automations.rules`
(protege contra un downgrade de plan entre el momento en que se encoló el
job y el momento en que corre).

## Delegación directa a `create_linkedin_post` (`accion.kind="create_linkedin_post"`)

Paridad REFERENCIA (30-jul-2026, ver `edecan_schemas.automations`, "Segunda
variante"): las 5 automatizaciones sembradas por
`apps/local/edecan_local/linkedin_automations_seed.py` (3 Acme + 2
personales/día) NO usan `agent_instruction`. Se evaluaron los dos caminos:

- **`agent_instruction`** (correr un turno headless que le pide al LLM que
  llame la tool `crear_post_linkedin`): el modelo decide con criterio propio
  QUÉ tool llamar, con qué `destino`/`tema`, y si de verdad la llama —
  riesgo real de que un turno agendado no produzca nada, o publique con el
  destino equivocado, sin ningún error visible más que un `automation_runs`
  vacío. Además `run_automation_turn` (`edecan_automations.runner`) SOLO
  persiste el resultado en `automation_runs.detalle` — nunca arma la card ni
  la entrega al chat principal, así que aunque el LLM acertara, el post
  quedaría enterrado ahí, no en el chat con push, que es justo lo que este
  frente pide ("confíe el resultado al chat principal + push").
- **Delegación directa** (esta rama, elegida): la automatización YA sabe,
  desde que se sembró, exactamente qué generar (`destino`/`tema`/
  `con_imagen` fijos en `accion`) — no hace falta que un LLM decida nada en
  el camino de DISPARO. Este handler solo traduce esos campos al payload de
  `create_linkedin_post` y lo encola
  (`apps/worker/edecan_worker/handlers/create_linkedin_post.py`), que corre
  el motor real (el LLM SÍ entra ahí, escribiendo el post en sí), sube la
  imagen, arma la card y la entrega al chat principal (`is_main`) + push
  `content_created`. Cero turno de agente, cero `ToolRegistry`/`Agent`
  headless para este camino — es el más DETERMINISTA de los dos y el único
  que entrega la card+push que pide la paridad.

Por eso `_delegate_create_linkedin_post` (más abajo) corta el flujo ANTES de
`deps.llm_router_for(tenant_id)`/`_build_registry()`: nada de eso hace falta
para simplemente encolar un job. SÍ abre su propia fila `automation_runs`
(`_create_running_run`/`_make_save_run`, igual que el camino de agente) para
que quede evidencia de que la automatización disparó — pero su `"done"`
significa "encolé `create_linkedin_post` correctamente", NO "el post ya
existe": esa segunda parte la audita el propio job (`jobs.status`) y su
push `content_created`, no `automation_runs`. Por el mismo motivo esta rama
`return`ea ANTES del aviso universal de más abajo ("## Aviso universal al
terminar") — avisar `automation_completed` en el instante de encolar, antes
de que el post exista, sería un push prematuro y redundante con el
`content_created` que ya manda `create_linkedin_post.handle()` cuando el
post de verdad está listo.

## Perfil de agente opcional (`accion.agente`)

`accion.agente` (`edecan_schemas.automations.AgentInstructionAccion.agente`,
ROADMAP_V2.md §7.9 — mismas claves/semántica que `agent_steps.agente` en
misiones, `edecan_schemas.missions.MissionStepOut`) puede nombrar uno de los
perfiles de `edecan_agents.profiles.PROFILES`. `_apply_agent_profile` (abajo)
resuelve esa clave con el mismo criterio que
`edecan_agents.orchestrator.Orchestrator._run_step` usa para un paso de
misión: si resuelve a un perfil `disponible=True`, recorta el `ToolRegistry`
con `edecan_agents.RestrictedRegistry(registry, perfil.allowed_tools)`
—defensa en profundidad, no la única barrera: `edecan_automations.runner.
_build_safe_registry` vuelve a filtrar dangerous/`EXCLUDED_TOOL_NAMES` encima,
sin importar el perfil— y reemplaza la `persona` que ve el LLM por una armada
desde `perfil.nombre`/`perfil.system_prompt_extra`, igual que `_run_step`.
`agente` vacío/`None`, una clave que no existe en `PROFILES`, o una
`disponible=False` dejan el registro/persona sin tocar: ese es el "agente
genérico headless" que ya documenta el schema — a diferencia de
`Orchestrator.plan`/`_run_step`, una clave inválida NO se redirige a
`research` acá: este handler no tiene a quién avisarle que la clave estaba
mal, así que prefiere no cambiar de comportamiento en silencio ante un typo.
Import perezoso de `edecan_agents` dentro de `_apply_agent_profile`, mismo
criterio que `run_mission.py` (ver docstring de ese módulo).

## Payload

`{"automation_id": "<uuid>"}` — lo encola `automation_scan.py` (barrido de
agenda), `POST /v1/automations/{id}/probar` o `POST /v1/hooks/{id}`
(`apps/api/edecan_api/routers/`).

## Evidencia de que el run arrancó — sesión corta independiente (WP-V7-06)

Antes de este WP, `_create_running_run` (el INSERT que marca
`automation_runs.status='running'`) y `_make_save_run` (el UPDATE terminal:
`'done'|'error'|'waiting_confirmation'`, ver `RunnerDeps.save_run` en
`edecan_automations.runner`) compartían la MISMA sesión larga que
`handle()` abría al principio y mantenía viva durante TODO
`run_automation_turn` — sin comitear nada hasta que la función completa
retornaba limpio. `edecan_automations.runner.run_automation` está
documentado (ver su docstring) como "nunca lanza por un fallo DE NEGOCIO"
(el LLM se equivocó, una tool falló — `edecan_core.agent.Agent` ya lo
atrapa), pero SÍ deja propagar cualquier fallo DE INFRAESTRUCTURA (el
propio `deps.save_run` no puede escribir, o algo más grave: el worker
matado a mitad de camino, una `asyncio.CancelledError` real — el mismo
docstring de `RunnerDeps.save_run` ya anticipa este escenario: "para que un
run que se cuelga o que el worker mata a mitad de camino siga quedando
visible como `running` en vez de no existir"). Si esa fila 'running' vivía
en la MISMA transacción sin comitear que el resto, un fallo de
infraestructura A MITAD del turno (después de que alguna tool YA ejecutó un
efecto externo real) se llevaba puesta la fila entera en el rollback —el
run desaparecía sin dejar ningún rastro de que hubo un intento real— y el
reintento del despachador SQS invocaba `run_automation_turn` desde cero
(`Agent.run_turn` con `history=[]`), pudiendo repetir esa misma tool.

**Fix**: `_create_running_run`/`_make_save_run` reciben `deps.session_factory`
(no una `session` ya abierta) y abren SU PROPIA sesión corta por invocación
— mismo patrón que `campaigns.handle` ("sesiones cortas por unidad de
trabajo") y que `run_mission.py` (WP-V7-06, ver su docstring, sección
"Durabilidad por paso"). La fila `running` queda durable ANTES de invocar
`run_automation_turn`; el UPDATE terminal (`save_run`) también comitea
independiente, sin importar el estado de la sesión de trabajo del turno
(`ctx.session`, que las tools siguen usando sin cambios).

**Riesgo residual admitido honestamente** (mismo criterio que
`run_mission.py`): a diferencia de una misión (con pasos individualmente
rastreados en `agent_steps`), una automatización es UN turno headless que
puede incluir VARIAS tool calls dentro de sí (un ciclo ReAct de
`Agent.run_turn`) sin ningún checkpoint intermedio propio — si el turno
falla por infraestructura DESPUÉS de que una tool ya ejecutó un efecto real
pero ANTES de la escritura terminal de `save_run`, el reintento del
despachador sigue pudiendo repetir esa tool call. Este fix garantiza, como
mínimo, que quede evidencia forense durable (`automation_runs` en
`'running'`, nunca desaparecida) en vez de que el intento se pierda por
completo — cerrar el hueco de raíz exigiría claves de idempotencia
por-tool-call dentro de `Agent.run_turn` (fuera de alcance: paquete
`edecan_core`, no este handler).

## Aviso universal al terminar (cobertura de push, ROADMAP_V2.md §7, frente
"Push para todo")

Una automatización corre SIN nadie mirando — si termina o revienta, el dueño
no se entera hasta que abre la app por su cuenta. Igual que `run_mission.py`
avisa `work_completed`/`work_failed` para misiones, este handler avisa vía
`edecan_worker.universal_notifications.notify_important_event` (actividad
durable primero, push best-effort después, idempotente por
`event_key=kind:event_id`, ver su docstring) cuando el turno headless termina
en un estado TERMINAL real:

- `save_run("done", ...)` → `kind="automation_completed"` ("se ejecutó, ve el
  resultado").
- `save_run("error", ...)` → `kind="automation_failed"` (un error que ocurrió
  sin que nadie lo viera en el momento — necesita atención del dueño, mismo
  espíritu que `work_failed` de una misión).

`"waiting_confirmation"` NO avisa: en la práctica un run headless nunca llega
ahí (`_build_safe_registry` ya excluye TODA tool `dangerous` del registro
antes de correr — sin ellas registradas, el `Agent` no puede pedir
confirmación de una que no puede ni ver), así que esa rama solo existe por
simetría con `RunnerDeps.save_run`/`edecan_automations.runner` y no necesita
un tipo de evento propio.

`accion.kind="create_linkedin_post"` tampoco pasa por esta sección: esa rama
(ver "## Delegación directa a `create_linkedin_post`" arriba) `return`ea
antes de llegar acá a propósito — el aviso de esa automatización lo manda
`create_linkedin_post.handle()` (`content_created`, cuando el post de verdad
existe), no este bloque.

`event_id=run_id` (NO `automation_id`): una automatización con `trigger`
tipo `schedule` corre repetidas veces con el MISMO `automation_id` pero un
`run_id` nuevo cada vez (`_create_running_run`, arriba) — si el aviso usara
`automation_id` como clave de dedup, `record_notification_event` trataría
la SEGUNDA corrida exitosa como un duplicado de la primera y jamás
avisaría de nuevo. `run_id` sí es único por corrida, así que cada `done`/
`error` real produce su propio aviso. `resource_id=automation_id` en cambio
sí es la automatización (el deeplink `edecan://activity/{automation_id}`
lleva a ella, no a una fila `automation_runs` sin pantalla propia).

Se captura el `status` terminal envolviendo el `save_run` que ya recibe
`RunnerDeps` (`_save_run_y_recordar`, más abajo) en vez de releer
`automation_runs` en una sesión nueva después: `save_run` YA es la única
fuente de verdad del estado terminal (lo que persiste ES lo que se avisa),
y evita una lectura extra contra Postgres solo para enterarse de lo que este
mismo `handle()` acaba de escribir.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from edecan_core.notifications import ImportantNotificationEvent
from edecan_core.tools import ToolContext, ToolRegistry
from edecan_schemas import FLAG_AUTOMATIONS_RULES, PLANES, JobEnvelope, PersonaConfig
from sqlalchemy import text

from edecan_worker.deps import Deps
from edecan_worker.universal_notifications import notify_important_event

logger = logging.getLogger(__name__)

_KIND_POR_STATUS_TERMINAL: dict[str, str] = {
    "done": "automation_completed",
    "error": "automation_failed",
}
"""Ver docstring del módulo, "## Aviso universal al terminar" — solo los dos
estados terminales reales de un run headless tienen aviso;
`"waiting_confirmation"` deliberadamente no está acá."""


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("run_automation requiere tenant_id")
    tenant_id: UUID = env.tenant_id
    automation_id = UUID(str(env.payload["automation_id"]))

    # Import perezoso, ver docstring del módulo.
    from edecan_automations.runner import RunnerDeps
    from edecan_automations.runner import run_automation as run_automation_turn

    # PASO 1 — sesión corta de solo validación/lectura: automatización
    # encontrada/enabled/plan con el flag. Ninguno de los 3 guardas escribe
    # nada (mismo comportamiento que antes de este WP: "ni siquiera crea una
    # fila automation_runs" si cualquiera falla, ver docstring del módulo).
    async with deps.session_factory(None) as session:
        automation = await _load_automation(session, tenant_id, automation_id)
        if automation is None:
            logger.error(
                "run_automation: automatización %s no encontrada para tenant %s",
                automation_id,
                tenant_id,
            )
            return
        if not automation["enabled"]:
            logger.info(
                "run_automation: automatización %s está desactivada; se ignora.", automation_id
            )
            return

        tenant = await _load_tenant(session, tenant_id)
        plan_key = tenant["plan_key"] if tenant else "free_selfhost"
        flags = dict(PLANES.get(plan_key, PLANES["free_selfhost"]).flags)
        if not flags.get(FLAG_AUTOMATIONS_RULES, False):
            logger.warning(
                "run_automation: el plan %s del tenant %s ya no incluye automations.rules; "
                "automatización %s no se ejecuta.",
                plan_key,
                tenant_id,
                automation_id,
            )
            return

        user_id = UUID(str(automation["user_id"]))
        persona_row = await _load_persona(session, tenant_id, user_id)
        automation["accion"] = _parse_jsonb(automation.get("accion"))

    # Camino DETERMINISTA (ver docstring del módulo, "## Delegación directa a
    # create_linkedin_post"): nada de agente/LLM en el disparo, solo encolar
    # el job correcto con los parámetros que la automatización ya trae fijos.
    # Corta ANTES de `deps.llm_router_for`/`_build_registry`: ninguno de los
    # dos hace falta para esto.
    if automation["accion"].get("kind") == "create_linkedin_post":
        await _delegate_create_linkedin_post(
            deps,
            tenant_id=tenant_id,
            automation_id=automation_id,
            user_id=user_id,
            accion=automation["accion"],
        )
        return

    # Camino DETERMINISTA del check-in de gimnasio (`accion.kind="gym_checkin"`,
    # sembrado por `apps/local/edecan_local/gym_automations_seed.py`): publica
    # la card "¿Vas a ir al gym hoy?" en el chat principal + push, sin correr
    # ningún turno de agente (cero LLM en el disparo, ver docstring de
    # `run_gym_checkin.py`). Corta ANTES de `deps.llm_router_for`/
    # `_build_registry`: no hace falta nada de eso para este camino. El `ctx`
    # de esta rama lleva `deps` en `extras` (lo que `run_gym_checkin` necesita
    # para abrir sus propias sesiones cortas y enviar el push); su
    # `session`/`vault`/`llm` quedan en `None` porque `run_gym_checkin` no los
    # usa (habla con `deps` directamente, igual que `_make_save_run`).
    if automation["accion"].get("kind") == "gym_checkin":
        from edecan_worker.handlers.run_gym_checkin import run_gym_checkin

        run_id = await _create_running_run(deps.session_factory, tenant_id, automation_id)
        save_run = _make_save_run(
            deps.session_factory, deps, tenant_id, automation_id, user_id, run_id
        )
        ctx = ToolContext(
            tenant_id=tenant_id,
            user_id=user_id,
            session=None,
            settings=deps.settings,
            llm=None,
            vault=None,
            extras={"flags": flags, "deps": deps},
        )
        await run_gym_checkin(ctx, save_run)
        return

    # Se resuelve después de los guardas: un job descartado no inicializa
    # innecesariamente el proveedor administrado.
    llm_router = await deps.llm_router_for(tenant_id)

    # PASO 2 — evidencia de que el run arrancó: su PROPIA sesión corta,
    # comiteada ANTES de invocar `run_automation_turn` (que puede llamar
    # tools con efectos externos reales) — ver docstring del módulo,
    # "## Evidencia de que el run arrancó".
    run_id = await _create_running_run(deps.session_factory, tenant_id, automation_id)

    # PASO 3 — sesión de trabajo del turno: vive solo para `ctx.session`/
    # `vault` (lo que las tools usan, sin cambios respecto a antes de este
    # WP). `save_run` YA NO cierra sobre esta sesión (ver `_make_save_run`).
    async with deps.session_factory(None) as session:
        # MCP bring-your-own (ARCHITECTURE.md §15): se registran en ESTE
        # `ToolRegistry` recién construido (uno nuevo por job) ANTES de
        # `_apply_agent_profile`, para que un perfil sin `mcp_*` en
        # `allowed_tools` no las vea (mismo criterio que `run_mission.py`).
        # Como cada tool MCP es SIEMPRE `dangerous=True`
        # (`edecan_mcp.tool_adapter`), `_build_safe_registry` (más abajo, en
        # `edecan_automations.runner.run_automation`) las excluye de todos
        # modos de cualquier run headless — sin humano no hay confirmación
        # posible, ver `apps/worker/tests/test_mcp_en_worker.py` — pero
        # registrarlas ACÁ igual es lo correcto: es el mismo punto por el que
        # pasa cualquier otra tool, no un caso especial.
        base_registry = _build_registry()
        for mcp_tool in await deps.mcp_tools_para(tenant_id, session, flags):
            base_registry.register(mcp_tool)

        registry, persona = _apply_agent_profile(
            base_registry,
            _persona_from_row(persona_row),
            automation["accion"].get("agente"),
        )

        ctx = ToolContext(
            tenant_id=tenant_id,
            user_id=user_id,
            session=session,
            settings=deps.settings,
            llm=llm_router,
            vault=deps.vault(session),
            extras={"flags": flags, "approved_tool_calls": set()},
        )
        estado_terminal: dict[str, str] = {}
        run_deps = RunnerDeps(
            ctx=ctx,
            llm_router=llm_router,
            registry=registry,
            persona=persona,
            flags=flags,
            provider_health=deps.provider_health,
            save_run=_save_run_y_recordar(
                _make_save_run(
                    deps.session_factory, deps, tenant_id, automation_id, user_id, run_id
                ),
                estado_terminal,
            ),
        )

        await run_automation_turn(automation, run_deps)

    # Ver docstring del módulo, "## Aviso universal al terminar": el aviso
    # vive FUERA de la sesión de trabajo del turno (ya cerrada arriba, igual
    # que `run_mission.py`) — `notify_important_event` abre su propia sesión
    # corta e independiente. `estado_terminal` quedó poblado por
    # `_save_run_y_recordar` en cuanto `run_automation_turn` invocó
    # `save_run` con un status terminal real; si por lo que sea no lo hizo
    # (un bug en el runner que no está bajo el alcance de este handler), no
    # hay nada que avisar — el `.get(...)` de abajo simplemente no encuentra
    # `"status"`.
    kind = _KIND_POR_STATUS_TERMINAL.get(estado_terminal.get("status", ""))
    if kind is not None:
        await notify_important_event(
            deps,
            ImportantNotificationEvent(
                tenant_id=tenant_id,
                user_id=user_id,
                kind=kind,  # type: ignore[arg-type]
                event_id=run_id,
                resource_id=automation_id,
            ),
        )

    logger.info("run_automation completado automation_id=%s tenant_id=%s", automation_id, tenant_id)


async def _delegate_create_linkedin_post(
    deps: Deps,
    *,
    tenant_id: UUID,
    automation_id: UUID,
    user_id: UUID,
    accion: dict[str, Any],
) -> None:
    """Ver docstring del módulo, "## Delegación directa a
    `create_linkedin_post`" — encola el job con los parámetros que la
    automatización ya trae fijos y deja evidencia en `automation_runs`, sin
    correr ningún turno de agente ni avisar `automation_completed` (ese
    aviso lo manda `create_linkedin_post.handle()` cuando el post existe de
    verdad, con `content_created`)."""
    from edecan_core.queue import enqueue

    run_id = await _create_running_run(deps.session_factory, tenant_id, automation_id)
    save_run = _make_save_run(deps.session_factory, deps, tenant_id, automation_id, user_id, run_id)

    payload = {
        "user_id": str(user_id),
        "destino": accion.get("destino"),
        "tema": accion.get("tema"),
        "con_imagen": accion.get("con_imagen", True),
        # Este turno NO lo pidió nadie: lo disparó el reloj. `create_linkedin_post` lo usa
        # para no escribirle disculpas en el chat cuando un slot se salta -- saltarse un
        # turno por falta de fuente fresca es comportamiento normal del motor, y tres
        # "dame un ángulo más concreto" diarios por algo que nunca pidió es ruido, no
        # transparencia. Va explícito y no se deduce de que falte `conversation_id`: así
        # sigue siendo cierto si algún día estos posts se entregan en un hilo propio.
        "origen": "automatizacion",
    }
    # Acme usa el autopost de fydesign (video product-led con Opus + bucle de
    # crítica); el resto (p. ej. "personal") sigue el handler genérico de imagen.
    destino = accion.get("destino")
    job_type = (
        "create_organization_linkedin_post"
        if destino in ("organization", "organization_linkedin")
        else "create_linkedin_post"
    )
    job_id = await enqueue(deps.settings, job_type, payload, tenant_id)
    await save_run("done", {"delegado_a": job_type, "job_id": str(job_id)})
    logger.info(
        "run_automation: delegado a %s automation_id=%s job_id=%s",
        job_type,
        automation_id,
        job_id,
    )


def _build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.load_entry_points(group="edecan.tools")
    return registry


def _apply_agent_profile(
    registry: ToolRegistry, persona: PersonaConfig, agente_key: Any
) -> tuple[Any, PersonaConfig]:
    """Ver "## Perfil de agente opcional" en el docstring del módulo.

    `agente_key` vacío/`None`, una clave ausente de `PROFILES`, o una
    `disponible=False` devuelven `(registry, persona)` tal cual llegaron
    (sin importar `edecan_agents` siquiera) — solo una clave que resuelve a
    un perfil `disponible=True` dispara el import perezoso y el recorte.
    """
    if not agente_key:
        return registry, persona

    from edecan_agents import PROFILES, RestrictedRegistry

    perfil = PROFILES.get(str(agente_key))
    if perfil is None or not perfil.disponible:
        return registry, persona

    perfil_persona = PersonaConfig(
        nombre_asistente=perfil.nombre,
        idioma="es",
        instrucciones=perfil.system_prompt_extra,
        memoria_activada=False,
    )
    return RestrictedRegistry(registry, perfil.allowed_tools), perfil_persona


def _persona_from_row(row: dict[str, Any] | None) -> PersonaConfig:
    """Ídem `edecan_api.routers.persona.persona_from_row` (no se importa
    entre apps: `apps/worker` y `apps/api` son deployables independientes,
    ARCHITECTURE.md §10.1)."""
    if row is None:
        return PersonaConfig()
    return PersonaConfig(
        nombre_asistente=row.get("nombre_asistente") or "Edecán",
        idioma=row.get("idioma") or "es",
        tono=row.get("tono") or "cálido y profesional",
        formalidad=row.get("formalidad", 1),
        emojis=bool(row.get("emojis", False)),
        instrucciones=row.get("instrucciones") or "",
        rasgos=list(row.get("rasgos") or []),
        memoria_activada=bool(row.get("memoria_activada", True)),
        voice_id=row.get("voice_id"),
        estilo_relacion=row.get("estilo_relacion") or "profesional",
        adulto_confirmado=bool(row.get("adulto_confirmado", False)),
        consentimiento_romantico=bool(row.get("consentimiento_romantico", False)),
    )


def _parse_jsonb(value: Any) -> dict[str, Any]:
    """El driver puede devolver una columna `jsonb` como `str` crudo — mismo
    gotcha que `edecan_toolkit.contactos._desde_jsonb`/
    `edecan_automations.tools._from_jsonb`/`edecan_api.routers.automations._from_jsonb`."""
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value) if value else {}


def _save_run_y_recordar(save_run: Any, estado_terminal: dict[str, str]) -> Any:
    """Envuelve el `save_run` real (`_make_save_run`) para capturar el
    `status` con el que `edecan_automations.runner.run_automation` lo llamó
    — ver docstring del módulo, "## Aviso universal al terminar": así
    `handle()` sabe DESPUÉS (fuera de la sesión de trabajo del turno) si
    avisar `automation_completed`/`automation_failed`, sin releer
    `automation_runs`. Persiste PRIMERO (`await save_run(...)`, la escritura
    real nunca se salta) y solo LUEGO anota en el dict compartido — un
    `status` capturado sin que la escritura haya comiteado sería peor que no
    avisar nada."""

    async def _envoltorio(status: str, detalle: dict[str, Any]) -> None:
        await save_run(status, detalle)
        estado_terminal["status"] = status

    return _envoltorio


def _make_save_run(
    session_factory: Any,
    deps: Deps,
    tenant_id: UUID,
    automation_id: UUID,
    user_id: UUID,
    run_id: UUID,
) -> Any:
    """A diferencia de antes de WP-V7-06, recibe `deps.session_factory` (NO
    una `session` ya abierta): la escritura TERMINAL del run abre su PROPIA
    sesión corta, independiente de la sesión de trabajo del turno — ver
    docstring del módulo, "## Evidencia de que el run arrancó". Las dos
    escrituras (`automation_runs`/`automations.last_run_at`) siguen siendo
    atómicas ENTRE SÍ (misma sesión nueva para ambas), solo dejaron de
    compartir sesión con el resto de `handle()`.

    Recibe además `deps` y `user_id` para el seguimiento de fallos
    (PHASE2 §61-62): tras persistir el estado terminal, actualiza
    `automations.consecutive_failures` (incrementa en `error`, reinicia en
    `done`) y, si alcanza el umbral de 3 fallos seguidos, desactiva la
    automatización (`enabled=false`, `disabled_at=now()`) y emite UN aviso
    `automation_failed` keyeado en `automation_id` (no `run_id`, para que
    dedupe entre corridas) vía `notify_important_event`."""

    async def _save_run(status: str, detalle: dict[str, Any]) -> None:
        desactivar = False
        async with session_factory(None) as session:
            await session.execute(
                text(
                    "UPDATE automation_runs SET status = :status, detalle = :detalle ::jsonb, "
                    "finished_at = now(), updated_at = now() "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {
                    "status": status,
                    "detalle": json.dumps(detalle),
                    "tenant_id": str(tenant_id),
                    "id": str(run_id),
                },
            )
            if status == "error":
                cf_expr = "consecutive_failures + 1"
            elif status == "done":
                cf_expr = "0"
            else:
                cf_expr = "consecutive_failures"
            result = await session.execute(
                text(
                    f"UPDATE automations SET last_run_at = now(), updated_at = now(), "
                    f"consecutive_failures = {cf_expr} "
                    f"WHERE tenant_id = :tenant_id AND id = :id "
                    f"RETURNING consecutive_failures, enabled"
                ),
                {"tenant_id": str(tenant_id), "id": str(automation_id)},
            )
            row = result.mappings().first()
            nuevos_fallos = int(row["consecutive_failures"]) if row else 0
            if status == "error" and nuevos_fallos >= 3 and row and bool(row["enabled"]):
                await session.execute(
                    text(
                        "UPDATE automations SET enabled = false, disabled_at = now(), "
                        "updated_at = now() WHERE tenant_id = :tenant_id AND id = :id"
                    ),
                    {"tenant_id": str(tenant_id), "id": str(automation_id)},
                )
                desactivar = True

        if desactivar:
            try:
                await notify_important_event(
                    deps,
                    ImportantNotificationEvent(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        kind="automation_failed",
                        event_id=automation_id,
                        resource_id=automation_id,
                    ),
                )
            except Exception:  # noqa: BLE001 - el aviso es best-effort, no debe romper el guardado
                logger.warning(
                    "run_automation: no se pudo notificar auto-disable de %s",
                    automation_id,
                    exc_info=True,
                )

    return _save_run


# ---------------------------------------------------------------------------
# SQL directo (ver docstring del módulo: nombres pinned en ROADMAP_V2.md §7.4)
# ---------------------------------------------------------------------------


async def _load_automation(
    session: Any, tenant_id: UUID, automation_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text("SELECT * FROM automations WHERE tenant_id = :tenant_id AND id = :id"),
        {"tenant_id": str(tenant_id), "id": str(automation_id)},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _load_tenant(session: Any, tenant_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text("SELECT plan_key FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _load_persona(session: Any, tenant_id: UUID, user_id: UUID) -> dict[str, Any] | None:
    # Misma query que `edecan_api.routers.persona`/`edecan_worker.repo.SqlRepo.get_persona`:
    # la fila específica del usuario si existe, si no la fila "default" del
    # tenant (`user_id IS NULL`, ARCHITECTURE.md §10.3).
    result = await session.execute(
        text(
            "SELECT * FROM personas WHERE tenant_id = :tenant_id "
            "AND (user_id = :user_id OR user_id IS NULL) ORDER BY user_id NULLS LAST LIMIT 1"
        ),
        {"tenant_id": str(tenant_id), "user_id": str(user_id)},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _create_running_run(session_factory: Any, tenant_id: UUID, automation_id: UUID) -> UUID:
    """A diferencia de antes de WP-V7-06, recibe `deps.session_factory` (NO
    una `session` ya abierta): abre su PROPIA sesión corta que comitea al
    salir limpio, ANTES de que `handle()` invoque `run_automation_turn` — ver
    docstring del módulo, "## Evidencia de que el run arrancó"."""
    run_id = uuid4()
    async with session_factory(None) as session:
        await session.execute(
            text(
                """
                INSERT INTO automation_runs (
                    id, tenant_id, automation_id, status, detalle, started_at, finished_at
                ) VALUES (
                    :id, :tenant_id, :automation_id, 'running', :detalle ::jsonb, now(), NULL
                )
                """
            ),
            {
                "id": str(run_id),
                "tenant_id": str(tenant_id),
                "automation_id": str(automation_id),
                "detalle": json.dumps({}),
            },
        )
    return run_id
