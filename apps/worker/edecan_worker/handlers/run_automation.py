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
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from edecan_core.tools import ToolContext, ToolRegistry
from edecan_schemas import FLAG_AUTOMATIONS_RULES, PLANES, JobEnvelope, PersonaConfig
from sqlalchemy import text

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)


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

    # Bring-your-own por tenant (WP-V3-02, ver `Deps.llm_router_for`):
    # resuelto DESPUÉS de los 3 guardas de arriba (automatización no
    # encontrada/desactivada/plan sin automations.rules) — ninguno de esos
    # casos necesita jamás el LLM. Abre su PROPIA sesión internamente (ver
    # `Deps.llm_router_for`), no depende de ninguna sesión del llamador.
    # Lanza `TenantLLMNotConnectedError` (nunca cae a `deps.llm_router` de
    # plataforma) si no se puede resolver — se deja propagar, el
    # despachador del job la trata como cualquier otro fallo (reintento con
    # backoff, luego DLQ/`status='error'` con este mensaje claro en
    # `last_error`). Tampoco crea ninguna fila `automation_runs` si falla.
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
        run_deps = RunnerDeps(
            ctx=ctx,
            llm_router=llm_router,
            registry=registry,
            persona=persona,
            flags=flags,
            save_run=_make_save_run(deps.session_factory, tenant_id, automation_id, run_id),
        )

        await run_automation_turn(automation, run_deps)

    logger.info("run_automation completado automation_id=%s tenant_id=%s", automation_id, tenant_id)


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
    )


def _parse_jsonb(value: Any) -> dict[str, Any]:
    """El driver puede devolver una columna `jsonb` como `str` crudo — mismo
    gotcha que `edecan_toolkit.contactos._desde_jsonb`/
    `edecan_automations.tools._from_jsonb`/`edecan_api.routers.automations._from_jsonb`."""
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value) if value else {}


def _make_save_run(session_factory: Any, tenant_id: UUID, automation_id: UUID, run_id: UUID) -> Any:
    """A diferencia de antes de WP-V7-06, recibe `deps.session_factory` (NO
    una `session` ya abierta): la escritura TERMINAL del run abre su PROPIA
    sesión corta, independiente de la sesión de trabajo del turno — ver
    docstring del módulo, "## Evidencia de que el run arrancó". Las dos
    escrituras (`automation_runs`/`automations.last_run_at`) siguen siendo
    atómicas ENTRE SÍ (misma sesión nueva para ambas), solo dejaron de
    compartir sesión con el resto de `handle()`."""

    async def _save_run(status: str, detalle: dict[str, Any]) -> None:
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
            await session.execute(
                text(
                    "UPDATE automations SET last_run_at = now(), updated_at = now() "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": str(tenant_id), "id": str(automation_id)},
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
