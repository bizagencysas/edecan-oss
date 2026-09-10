"""`run_automation(automation, deps)` — corre UN turno headless de agente
para una automatización y persiste el resultado (`ROADMAP_V2.md` §7.3, §7.4,
§7.7).

Este módulo SÍ importa `edecan_core` a nivel de módulo (`Agent`/
`ToolRegistry` son clases reales que hace falta instanciar/subclasificar,
mismo criterio que `edecan_premium.tools` — ver su docstring): es importable
porque `edecan_core` es un paquete v1 ya estable, a diferencia de otros
paquetes hermanos que sí se construyen en paralelo en esta ronda (v2). NO
importa `edecan_db`, no abre sesiones y no sabe hablar SQL: todo lo que
necesita persistir lo hace a través de `RunnerDeps.save_run`, un callable que
inyecta el llamador real (`apps/worker/edecan_worker/handlers/
run_automation.py`, que sí sabe hablar con Postgres). Así este paquete se
testea con un `Agent` falso y un `save_run` en memoria, sin Postgres ni
`edecan_db` (`ARCHITECTURE.md` §10.1).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from copy import copy
from dataclasses import dataclass
from inspect import Parameter, signature
from typing import Any

from edecan_core.agent import Agent, SeleccionDeModelo
from edecan_core.bot_harness import (
    AUTONOMY_LEVEL_FULL,
    autonomy_allows_operation,
    tool_local_operation,
)
from edecan_core.tools import Tool, ToolRegistry

logger = logging.getLogger(__name__)

# Tools que un run headless NUNCA debe poder invocar, aunque el tenant tenga
# el flag correspondiente activo — segunda barrera contra recursión,
# redundante a propósito con `dangerous=True` en ambas (ver más abajo por
# qué la redundancia es deliberada, no un descuido):
# - `gestionar_automatizacion` podría crear/activar OTRA automatización
#   (o desactivar esta misma) desde dentro de su propio run.
# - `delegar_mision` (WP-V2-06, si ya aterrizó) podría delegar una misión de
#   agente que a su vez... — misma familia de riesgo, un run headless jamás
#   debe poder generar MÁS trabajo autónomo sin que un humano intervenga.
EXCLUDED_TOOL_NAMES = frozenset({"delegar_mision", "gestionar_automatizacion"})
DEFAULT_HEADLESS_MODEL_ALIAS = "chat_rapido"

SaveRun = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class RunnerDeps:
    """Colaboradores que `run_automation` necesita, todos inyectados por el
    llamador (ver docstring del módulo).

    - `ctx`: `edecan_core.tools.ToolContext` YA armado (tenant_id/user_id/
      session/settings/llm/vault/extras) — típicamente `ctx.session` es una
      sesión "dueño" del worker (bypassa RLS, ARCHITECTURE.md §2), porque
      cada `Tool` del toolkit ya filtra explícito por `ctx.tenant_id` en su
      SQL (nunca confía en RLS), así que es segura de reutilizar tal cual.
    - `llm_router`: se lo pasa tal cual a `Agent(llm_router, registry)`.
    - `registry`: el `ToolRegistry` COMPLETO (sin filtrar) — `run_automation`
      arma el subconjunto seguro internamente (`_build_safe_registry`), así
      el llamador no tiene que conocer esa lógica.
    - `persona`: `edecan_schemas.PersonaConfig` del usuario dueño de la
      automatización (no necesariamente "el usuario actual": no hay uno en
      un run headless).
    - `flags`: flags de plan del tenant (mismo dict que ya trae
      `ctx.extras["flags"]`) — se repite acá porque `ToolRegistry.specs()`
      y `Agent.run_turn(flags=...)` lo piden explícito).
    - `save_run`: `(status, detalle) -> None` — persiste una fila en
      `automation_runs` y actualiza `automations.last_run_at`. `status` es
      uno de `"done"|"error"|"waiting_confirmation"` (nunca `"running"`: esa
      fila ya la crea el llamador ANTES de invocar `run_automation`, para
      que un run que se cuelga o que el worker mata a mitad de camino siga
      quedando visible como `running` en vez de no existir).
    """

    ctx: Any
    llm_router: Any
    registry: ToolRegistry
    persona: Any
    flags: dict[str, Any]
    save_run: SaveRun
    provider_health: Any | None = None
    # Esfuerzo de razonamiento del turno (p. ej. "xhigh" para los bots en los
    # despliegues gpt-5.6 de Azure). Opcional; el Agent lo aplica solo a los
    # modelos de la familia gpt-5.
    reasoning_effort: str | None = None
    # Bootstrap data is optional so existing automation callers retain their
    # public contract. Persistent workers can pass their configured policy and
    # the same bounded context/tools used by their interactive route.
    model_alias: str | None = None
    model_policy: dict[str, Any] | None = None
    profile_context: str | None = None
    skills_context: str | None = None
    extra_tools: Sequence[Tool] | None = None
    # Persistent bot builder runs: sandbox/code/MCP pre-approved; dangerous tools
    # allowed except recursion (`delegar_mision`, `gestionar_automatizacion`).
    builder_mode: bool = False
    # BOTS-02: nivel de autonomía del worker (`ask|read_only|draft|full`). Se
    # aplica ANTES de ejecutar para restringir el registro de capacidades:
    # `read_only` rechaza herramientas de escritura/envió aunque el modelo las
    # pida; `full` conserva la funcionalidad autorizada completa. `full` es el
    # default para no alterar el contrato de los llamadores no-persistentes.
    autonomy_level: str = AUTONOMY_LEVEL_FULL
    # Called by Agent immediately before every provider call. The hook owns the
    # authoritative accumulated-use lookup/reservation for the current run.
    budget_gate: Callable[[Any], Awaitable[None] | None] | None = None


def _build_safe_registry(full_registry: ToolRegistry, flags: dict[str, Any]) -> ToolRegistry:
    """`ToolRegistry` nuevo con solo las tools NO `dangerous` que el tenant
    tendría disponibles según `flags`, excluyendo además `EXCLUDED_TOOL_NAMES`
    por nombre (ver docstring del módulo). Usa únicamente la API pública de
    `ToolRegistry` (`specs`/`get`/`register`) — no hay forma de enumerar las
    tools registradas sin pasar por `specs(flags)` primero, así que ESE
    `flags` (el del tenant dueño de la automatización, no uno "todo
    permitido") es el que decide qué tools existen siquiera como candidatas.
    """
    safe = ToolRegistry()
    for spec in full_registry.specs(flags):
        if spec.name in EXCLUDED_TOOL_NAMES:
            continue
        tool = full_registry.get(spec.name)
        if tool is None or bool(
            getattr(tool, "intrinsically_dangerous", getattr(tool, "dangerous", False))
        ):
            continue
        safe.register(tool)
    return safe


def _build_builder_registry(full_registry: ToolRegistry, flags: dict[str, Any]) -> ToolRegistry:
    """Registry for persistent bot builder runs: includes dangerous sandbox/code
    and MCP tools, but never recursion tools."""
    builder = ToolRegistry()
    for spec in full_registry.specs(flags):
        if spec.name in EXCLUDED_TOOL_NAMES:
            continue
        tool = full_registry.get(spec.name)
        if tool is None:
            continue
        builder.register(tool)
    return builder


def _build_level_registry(
    full_registry: ToolRegistry, flags: dict[str, Any], *, autonomy_level: str
) -> ToolRegistry:
    """Registry for a persistent bot run restricted by autonomy level (BOTS-02).

    `full` conserva el builder registry completo (dangerous sandbox/code incluido,
    sin tools de recursión). Los niveles restrictivos (`read_only`/`ask`/`draft`)
    rechazan capacidades ANTES de ejecutar: `read_only`/`ask` solo dejan lectura;
    `draft` deja lectura + escritura interna y rechaza envío externo.
    """
    if autonomy_level == AUTONOMY_LEVEL_FULL:
        return _build_builder_registry(full_registry, flags)

    restricted = ToolRegistry()
    for spec in full_registry.specs(flags):
        if spec.name in EXCLUDED_TOOL_NAMES:
            continue
        tool = full_registry.get(spec.name)
        if tool is None:
            continue
        operation = tool_local_operation(
            name=spec.name,
            dangerous=bool(
                getattr(tool, "intrinsically_dangerous", getattr(tool, "dangerous", False))
            ),
        )
        if autonomy_allows_operation(autonomy_level, operation):
            restricted.register(tool)
    return restricted


def _filter_extra_tools_by_level(
    extra_tools: Sequence[Tool], *, autonomy_level: str
) -> list[Tool]:
    """Filtra las tools extra (MCP dinámicas + tools de persona) por nivel de
    autonomía ANTES de ofrecerlas al modelo (BOTS-02). `full` no filtra nada;
    los niveles restrictivos descartan toda tool cuya operación local no esté
    permitida (una tool MCP no clasificable → `None` → rechazada, fail-closed).
    """
    if autonomy_level == AUTONOMY_LEVEL_FULL:
        return list(extra_tools)
    filtered: list[Tool] = []
    for tool in extra_tools:
        name = str(getattr(tool, "name", "") or "")
        operation = tool_local_operation(
            name=name,
            input_schema=getattr(tool, "input_schema", None),
            dangerous=bool(
                getattr(tool, "intrinsically_dangerous", getattr(tool, "dangerous", False))
            ),
        )
        if autonomy_allows_operation(autonomy_level, operation):
            filtered.append(tool)
    return filtered


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Mismo helper que `edecan_api.routers.conversations._event_to_dict`
    (duplicado a propósito, no se importa `apps/api` desde un paquete de
    `packages/` — direcciones de dependencia invertidas): un `AgentEvent` es
    Pydantic, pero los tests de este módulo hacen que el `Agent` falso yield
    `dict`s planos directamente."""
    if isinstance(event, dict):
        return event
    if hasattr(event, "model_dump"):
        return event.model_dump()
    return dict(vars(event))


def _agent_accepts_kwarg(name: str) -> bool:
    """Keep third-party/test Agent doubles compatible with additive options."""

    parameters = signature(Agent).parameters.values()
    return any(
        parameter.name == name or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _model_policy_for_run(
    automation: dict[str, Any], explicit_policy: dict[str, Any] | None
) -> dict[str, Any]:
    if explicit_policy is not None:
        return dict(explicit_policy)
    accion = automation.get("accion")
    candidates = (
        automation.get("model_policy"),
        automation.get("worker", {}).get("model_policy")
        if isinstance(automation.get("worker"), dict)
        else None,
        accion.get("model_policy") if isinstance(accion, dict) else None,
    )
    return next((dict(value) for value in candidates if isinstance(value, dict)), {})


def _requested_model(policy: dict[str, Any]) -> str | None:
    for key in ("model", "model_id", "modelo"):
        value = policy.get(key)
        if value is not None and (normalized := str(value).strip()):
            return normalized
    return None


def _persona_with_skills_context(persona: Any, skills_context: str | None) -> Any:
    context = str(skills_context or "").strip()
    if not context:
        return persona
    if hasattr(persona, "model_copy"):
        prepared = persona.model_copy(deep=True)
    else:
        prepared = copy(persona)
    current = str(getattr(prepared, "instrucciones", None) or "").strip()
    prepared.instrucciones = "\n\n".join(part for part in (current, context) if part)
    return prepared


def _effective_execution(
    *,
    model_alias: str,
    requested_model: str | None,
    attribution: dict[str, Any],
) -> dict[str, Any]:
    effective_model = str(attribution.get("model") or "").strip() or None
    execution: dict[str, Any] = {
        "model_alias": str(attribution.get("model_alias") or model_alias),
        "model_policy": {
            "requested_model": requested_model,
            "effective_model": effective_model,
            "applied": (
                effective_model == requested_model
                if requested_model is not None and effective_model is not None
                else None
            ),
        },
    }
    for key in ("provider", "model", "reasoning_effort", "fallback_used"):
        value = attribution.get(key)
        if value is not None and str(value).strip():
            execution[key] = str(value)
    return execution


async def run_automation(automation: dict[str, Any], deps: RunnerDeps) -> None:
    """Corre `accion.instruccion` de `automation` como UN turno headless y
    persiste el resultado vía `deps.save_run`.

    Nunca lanza por un fallo "de negocio" del turno (el LLM se equivocó, una
    tool falló, el modelo pidió algo `dangerous`): `edecan_core.agent.Agent`
    ya atrapa esos casos y los traduce a un evento `error`/
    `confirmation_required` (ver su docstring), que este loop convierte 1:1
    en el `status`/`detalle` que persiste. Si algo por FUERA de esa
    resiliencia revienta (p. ej. `deps.save_run` no puede escribir en
    Postgres), la excepción se propaga tal cual — el worker la atrapa y
    reintenta el job entero con backoff (`ARCHITECTURE.md` §10.11); tragarla
    acá silenciaría un fallo de infraestructura real.
    """
    accion = automation.get("accion") or {}
    instruccion = str(accion.get("instruccion", "")).strip()
    if not instruccion:
        await deps.save_run("error", {"error": "La automatización no tiene instrucción."})
        return

    ctx = deps.ctx
    if deps.builder_mode:
        ctx.extras["approved_tool_calls"] = set(ctx.extras.get("approved_tool_calls") or set())
    else:
        # Invariante de seguridad de un run headless genérico: SIEMPRE vacío.
        ctx.extras["approved_tool_calls"] = set()
    ctx.extras.setdefault("flags", deps.flags)
    if deps.profile_context is not None:
        ctx.extras["profile_context"] = deps.profile_context

    if deps.builder_mode:
        # BOTS-02: en un run de bot persistente, la autonomía del worker
        # restringe las capacidades ANTES de ejecutar (read_only rechaza
        # escritura aunque el modelo la pida; full conserva todo lo autorizado).
        safe_registry = _build_level_registry(
            deps.registry, deps.flags, autonomy_level=deps.autonomy_level
        )
    else:
        safe_registry = _build_safe_registry(deps.registry, deps.flags)
    if ctx.extras.get("companion") is not None:
        for nombre_mac in ("usar_computadora", "delegar_al_ide"):
            mac = deps.registry.get(nombre_mac)
            if mac is None:
                continue
            # BOTS-02: la capability de Mac (escritura) también respeta la
            # autonomía del worker — read_only/draft no la recuperan aunque haya
            # companion. Para llamadores no-persistentes el default es "full" y
            # el comportamiento histórico se conserva.
            mac_operation = tool_local_operation(
                name=nombre_mac,
                dangerous=bool(
                    getattr(mac, "intrinsically_dangerous", getattr(mac, "dangerous", False))
                ),
            )
            if not autonomy_allows_operation(deps.autonomy_level, mac_operation):
                continue
            safe_registry.register(mac)
    model_alias = str(deps.model_alias or "").strip() or DEFAULT_HEADLESS_MODEL_ALIAS
    model_policy = _model_policy_for_run(automation, deps.model_policy)
    requested_model = _requested_model(model_policy)
    seleccion = SeleccionDeModelo(modelo=requested_model) if requested_model else None
    persona = _persona_with_skills_context(deps.persona, deps.skills_context)

    agent_kwargs = {}
    if _agent_accepts_kwarg("model_alias"):
        agent_kwargs["model_alias"] = model_alias
    if deps.provider_health is not None:
        agent_kwargs["provider_health"] = deps.provider_health
    if deps.reasoning_effort:
        agent_kwargs["reasoning_effort"] = deps.reasoning_effort
    if deps.budget_gate is not None and _agent_accepts_kwarg("budget_gate"):
        agent_kwargs["budget_gate"] = deps.budget_gate
    agent = Agent(deps.llm_router, safe_registry, **agent_kwargs)

    text_parts: list[str] = []
    tool_log: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    attribution: dict[str, Any] = {}

    turn_kwargs: dict[str, Any] = {
        "ctx": ctx,
        "persona": persona,
        "history": [],
        "user_text": instruccion,
        "flags": deps.flags,
    }
    if deps.extra_tools is not None:
        turn_kwargs["extra_tools"] = (
            _filter_extra_tools_by_level(deps.extra_tools, autonomy_level=deps.autonomy_level)
            if deps.builder_mode
            else deps.extra_tools
        )
    if seleccion is not None:
        turn_kwargs["seleccion"] = seleccion
    events = agent.run_turn(**turn_kwargs)
    async for raw_event in events:
        event = _event_to_dict(raw_event)
        event_type = event.get("type")

        if event_type == "text_delta":
            text_parts.append(str(event.get("text", "")))
        elif event_type in ("tool_start", "tool_end"):
            tool_log.append(event)
        elif event_type == "confirmation_required":
            pendiente = {
                "tool_call_id": event.get("tool_call_id"),
                "name": event.get("name"),
                "args": event.get("args") or {},
            }
            logger.info(
                "run_automation: pausada en confirmation_required, tool=%r", pendiente.get("name")
            )
            await deps.save_run(
                "waiting_confirmation", {"pendiente": pendiente, "tool_log": tool_log}
            )
            return
        elif event_type == "error":
            mensaje = str(event.get("message") or "Error desconocido durante el turno.")
            logger.warning("run_automation: el turno terminó en error: %s", mensaje)
            await deps.save_run("error", {"error": mensaje, "tool_log": tool_log})
            return
        elif event_type == "done":
            usage = event.get("usage") or {}
            attribution = dict(event.get("attribution") or {})

    # `done` es siempre el último evento salvo que ya se haya retornado
    # arriba (confirmation_required/error) — `Agent.run_turn` nunca deja el
    # generador terminar sin uno de los tres (ver su docstring).
    await deps.save_run(
        "done",
        {
            "resultado": "".join(text_parts),
            "tool_log": tool_log,
            "usage": usage,
            "execution": _effective_execution(
                model_alias=model_alias,
                requested_model=requested_model,
                attribution=attribution,
            ),
        },
    )
