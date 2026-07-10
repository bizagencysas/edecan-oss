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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from edecan_core.agent import Agent
from edecan_core.tools import ToolRegistry

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
        if tool is None or tool.dangerous:
            continue
        safe.register(tool)
    return safe


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
    # Invariante de seguridad de un run headless: SIEMPRE vacío, sin importar
    # lo que el caller haya dejado en ctx.extras — nadie puede haber
    # aprobado nada de antemano porque no hay nadie mirando (ver docstring
    # del módulo/README del paquete).
    ctx.extras["approved_tool_calls"] = set()
    ctx.extras.setdefault("flags", deps.flags)

    safe_registry = _build_safe_registry(deps.registry, deps.flags)
    agent = Agent(deps.llm_router, safe_registry)

    text_parts: list[str] = []
    tool_log: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}

    events = agent.run_turn(
        ctx=ctx, persona=deps.persona, history=[], user_text=instruccion, flags=deps.flags
    )
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

    # `done` es siempre el último evento salvo que ya se haya retornado
    # arriba (confirmation_required/error) — `Agent.run_turn` nunca deja el
    # generador terminar sin uno de los tres (ver su docstring).
    await deps.save_run(
        "done", {"resultado": "".join(text_parts), "tool_log": tool_log, "usage": usage}
    )
