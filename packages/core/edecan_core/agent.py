"""`Agent` — loop de tool-use del agente (ARCHITECTURE.md §9, §10.7).

Flujo de referencia (§9): recupera memorias si `persona.memoria_activada`,
arma el system prompt (`persona.build_system_prompt`), y entra en un loop de
hasta `MAX_TOOL_ITERATIONS` llamadas al LLM. En cada vuelta transmite el texto
como eventos `text_delta`; si el modelo pidió herramientas, las ejecuta (con
gate de confirmación para las `dangerous`) y vuelve a llamar al LLM con los
resultados; si no, termina el turno con `done`. Cualquier excepción no
atrapada en el camino se traduce a un evento `error` (el turno nunca "revienta"
silenciosamente hacia quien consume `run_turn`, típicamente el endpoint SSE de
`edecan_api`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from edecan_schemas import (
    AgentEvent,
    ConfirmationRequiredEvent,
    DoneEvent,
    ErrorEvent,
    PersonaConfig,
    TextDeltaEvent,
    ToolEndEvent,
    ToolSpec,
    ToolStartEvent,
)

from .llm_types import ChatMessage, CompletionRequest
from .persona import build_system_prompt
from .safety import redact
from .tools.base import Tool, ToolContext, ToolResult
from .tools.registry import ToolRegistry, _flags_satisfechos

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8
"""Tope de vueltas LLM↔herramientas dentro de UN turno (ARCHITECTURE.md §10.7)."""

_LLM_ALIAS = "principal"
_RESULT_PREVIEW_LEN = 400
_EXTRAS_MEMORY_STORE = "memory_store"
_EXTRAS_APPROVED_TOOL_CALLS = "approved_tool_calls"


class Agent:
    """Orquesta un turno de conversación: memoria + LLM + herramientas.

    `llm_router` es `Any` a propósito (`edecan_core` no depende de
    `edecan_llm`, ver `llm_types.py`): debe exponer
    `resolve(alias: str, tenant_flags: dict) -> tuple[provider, model]` donde
    `provider.stream(req)` es un `AsyncIterator` de trozos con atributos
    `.type` (`"text"|"tool_call"|"usage"|"stop"`), `.text`, `.tool_call`
    (`.id`/`.name`/`.arguments`) y `.usage` (`.input_tokens`/`.output_tokens`)
    — exactamente la forma de `edecan_llm.router.LLMRouter`/`LLMProvider`.

    `model_alias` (opcional, default `None` → `_LLM_ALIAS`/`"principal"`): el
    alias que este `Agent` resuelve en `llm_router.resolve(alias, flags)` para
    TODO el turno. Existe para que `edecan_agents.orchestrator.Orchestrator`
    pueda construir un `Agent` por paso con el `model_alias` del
    `AgentProfile` de ese paso (`profiles.py`) sin que `Agent` conozca el
    concepto de "perfil" — los demás invocadores (`edecan_api`,
    `edecan_automations`, `edecan_evals`) simplemente no lo pasan y siguen
    resolviendo `"principal"` como antes.
    """

    def __init__(
        self, llm_router: Any, registry: ToolRegistry, *, model_alias: str | None = None
    ) -> None:
        self._llm_router = llm_router
        self._registry = registry
        self._model_alias = model_alias or _LLM_ALIAS

    async def run_turn(
        self,
        *,
        ctx: ToolContext,
        persona: PersonaConfig,
        history: list[ChatMessage],
        user_text: str,
        flags: dict[str, Any],
        extra_tools: Sequence[Tool] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Ejecuta un turno completo, emitiendo `AgentEvent` a medida que ocurren.

        No devuelve nada: quien consume el `AsyncIterator` (típicamente
        `edecan_api`, traduciéndolo 1:1 a SSE) reconstruye el mensaje final a
        partir de los eventos `text_delta`/`tool_start`/`tool_end`/`done`.

        `extra_tools` (opcional, `ARCHITECTURE.md` §15 — nace para las tools
        MCP bring-your-own del tenant, `edecan_mcp.tool_adapter`, pero sirve
        para cualquier tool que un llamador quiera ofrecer SOLO en este turno)
        se FUSIONA con el `ToolRegistry` compartido únicamente para la
        duración de esta llamada: sus specs se agregan a las que ya ofrece el
        registry y sus nombres se resuelven cuando el modelo las invoca —
        pero el `ToolRegistry` en sí NUNCA se muta (`registry.register(...)`
        no se llama en ningún punto de este método), así que el próximo turno
        (con o sin `extra_tools`) no ve ningún residuo de este. Cada tool de
        `extra_tools` además pasa por el mismo filtro `requires_flags` que ya
        aplica `ToolRegistry.specs()` — una tool sin sus flags no aparece ni
        se puede resolver, igual que una tool del registry base. En caso de
        colisión de `name` entre `extra_tools` y el registry base, GANA el
        registry base (se ignora la extra por completo, ni en specs ni en
        resolución) — el registry compartido es la fuente de verdad de las
        herramientas "de plataforma", nunca algo que una tool bring-your-own
        pueda sombrear.

        Aplica también al camino de confirmación/reanudación de una tool
        `dangerous` pendiente: si se vuelve a llamar `run_turn` con
        `ctx.extras["approved_tool_calls"]` conteniendo el `tool_call_id` de
        una `extra_tool` ya solicitada, esta la resuelve y ejecuta igual que
        cualquier tool del registry — no hace falta ningún camino especial.
        (El endpoint HTTP `POST /v1/conversations/{id}/confirm` de
        `edecan_api` en cambio NUNCA vuelve a invocar `Agent.run_turn` — ver
        `edecan_api.routers.conversations`, que resuelve el nombre `mcp_*`
        pendiente contra el registry y, si no está ahí, contra sus propias
        `extra_tools` recién recalculadas, replicando este mismo criterio de
        "el registry base gana" fuera de este método.)
        """
        try:
            async for event in self._run_turn(
                ctx=ctx,
                persona=persona,
                history=history,
                user_text=user_text,
                flags=flags,
                extra_tools=extra_tools,
            ):
                yield event
        except Exception as exc:  # noqa: BLE001 - cualquier excepción se traduce a evento `error`
            logger.exception("Error irrecuperable durante Agent.run_turn")
            # `redact`: el texto de la excepción puede incluir credenciales que
            # se colaron en un mensaje de error (SECURITY.md); este evento sale
            # tal cual hacia el usuario final por SSE, así que nunca debe verse
            # en texto plano.
            yield ErrorEvent(message=redact(str(exc)))

    async def _run_turn(
        self,
        *,
        ctx: ToolContext,
        persona: PersonaConfig,
        history: list[ChatMessage],
        user_text: str,
        flags: dict[str, Any],
        extra_tools: Sequence[Tool] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        memories = await self._recall_memories(ctx, persona, user_text)
        system_prompt = build_system_prompt(persona, memories)

        messages: list[ChatMessage] = [*history, ChatMessage(role="user", content=user_text)]
        # `extra_by_name`: solo las `extra_tools` que (a) tienen sus
        # `requires_flags` satisfechos por `flags` y (b) NO colisionan de
        # nombre con el registry base — ver el docstring de `run_turn`.
        base_specs = self._registry.specs(flags)
        extra_by_name = _extra_tools_disponibles(extra_tools, flags, base_specs)
        tool_specs = [*base_specs, *_extra_specs(extra_by_name)]
        provider, model = self._llm_router.resolve(self._model_alias, flags)
        approved_tool_calls = ctx.extras.get(_EXTRAS_APPROVED_TOOL_CALLS, set())
        usage_totals: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

        for _ in range(MAX_TOOL_ITERATIONS):
            # `messages=list(messages)`: copia superficial para que cada
            # `CompletionRequest` sea una foto fija de ese momento — `messages`
            # se sigue mutando (`append`) en las próximas vueltas, y un
            # `provider`/observador real podría quedarse con la referencia al
            # `req` (p. ej. para loguearlo) más allá de esta iteración.
            request = CompletionRequest(
                model=model, system=system_prompt, messages=list(messages), tools=tool_specs
            )

            text_parts: list[str] = []
            tool_calls: list[Any] = []
            async for chunk in provider.stream(request):
                if chunk.type == "text" and chunk.text:
                    text_parts.append(chunk.text)
                    yield TextDeltaEvent(text=chunk.text)
                elif chunk.type == "tool_call" and chunk.tool_call is not None:
                    tool_calls.append(chunk.tool_call)
                elif chunk.type == "usage" and chunk.usage is not None:
                    usage_totals["input_tokens"] += chunk.usage.input_tokens
                    usage_totals["output_tokens"] += chunk.usage.output_tokens

            if not tool_calls:
                # stop_reason distinto de "tool_use": el turno terminó en texto.
                yield DoneEvent(usage=usage_totals)
                return

            messages.append(
                ChatMessage(role="assistant", content=_assistant_blocks(text_parts, tool_calls))
            )

            # Resolvemos la tool de cada `call` una sola vez y reutilizamos el
            # resultado en las dos pasadas de abajo. `self._registry` primero
            # (siempre gana en colisión, ver docstring de `run_turn`),
            # `extra_by_name` como respaldo — así una `mcp_*`/tool bring-your-
            # own de este turno se resuelve igual que cualquier otra.
            # `_con_flags_satisfechos`: `self._registry.get(name)` (a
            # diferencia de `self._registry.specs(flags)`, usado arriba solo
            # para calcular `tool_specs`) resuelve por NOMBRE contra el
            # registro COMPLETO sin filtrar por `flags` — sin este paso, una
            # tool con `requires_flags` no satisfecho por el plan del tenant
            # se ejecutaría igual (sin fricción si además no es `dangerous`)
            # apenas el modelo pidiera su nombre, así nunca se le hubiera
            # ofrecido en `tools=` este turno (docs/seguridad-modelo-amenazas.md,
            # Hallazgo 1 — el vector real es inyección de prompt indirecta vía
            # `navegar_web`/`consultar_documentos`/una skill de terceros
            # convenciendo al modelo de invocar un nombre de tool que nunca
            # vio ofrecido). Tratarla como no resuelta (`None`) reutiliza tal
            # cual el camino de "herramienta desconocida" que ya arman la 1ª y
            # 2ª pasada de abajo. Se aplica sin importar si `tool` vino de
            # `self._registry` o de `extra_by_name` — para esta última es
            # redundante con el filtro que ya aplica `_extra_tools_disponibles`
            # más arriba, pero inofensivo. Cubre también a `Orchestrator`
            # (`edecan_agents.registry_view.RestrictedRegistry`, que se
            # inyecta como `self._registry` para un paso de misión): esa clase
            # filtra por `allowed_tools`/`dangerous` pero nunca por `flags`, y
            # este es el único punto donde algo llama a `.get()` sobre ella.
            resolved_calls = [
                (
                    call,
                    _con_flags_satisfechos(
                        self._registry.get(call.name) or extra_by_name.get(call.name), flags
                    ),
                )
                for call in tool_calls
            ]

            # 1ª pasada — SOLO chequea, no ejecuta nada todavía. Anthropic
            # permite (y no lo desactivamos en ningún lado, ver
            # `edecan_llm.anthropic._build_body`) que el modelo pida varias
            # `tool_use` en un mismo turno. Si NO chequeáramos la tanda
            # entera antes de ejecutar, una tool NO peligrosa que viniera
            # antes en la lista ya habría corrido de verdad (side effect
            # real, p. ej. crear un recordatorio) para cuando el loop
            # llegara a una `dangerous` sin aprobar más adelante — y como
            # ese caso corta el turno (`return`) antes de llegar al
            # `messages.append(...)` de abajo, el resultado ya ejecutado se
            # perdería sin dejar rastro: no queda en `messages` (el LLM no
            # se entera en el próximo turno de que ya corrió, riesgo de
            # repetirla) ni se persiste en `edecan_api` (que solo guarda
            # mensajes en el evento `done`, nunca en
            # `confirmation_required`). Chequear todo el lote primero evita
            # ejecutar cualquier cosa hasta que no quede ninguna `dangerous`
            # pendiente de aprobar en esta tanda.
            for call, tool in resolved_calls:
                if tool is not None and tool.dangerous and call.id not in approved_tool_calls:
                    yield ConfirmationRequiredEvent(
                        tool_call_id=call.id, name=call.name, args=call.arguments
                    )
                    return

            # 2ª pasada — ya sabemos que ninguna tool de esta tanda queda
            # pendiente de confirmación: recién ahora es seguro ejecutarlas.
            tool_result_blocks: list[dict[str, Any]] = []
            for call, tool in resolved_calls:
                if tool is None:
                    logger.warning("El modelo pidió una herramienta desconocida: %r", call.name)
                    tool_result_blocks.append(
                        _tool_result_block(call.id, f"Error: herramienta desconocida '{call.name}'")
                    )
                    continue

                yield ToolStartEvent(name=call.name, args=call.arguments)
                try:
                    result = await tool.run(ctx, call.arguments)
                except Exception as exc:  # noqa: BLE001 - una tool nunca debe tumbar el turno
                    logger.warning(
                        "La herramienta %r lanzó una excepción", call.name, exc_info=True
                    )
                    # `redact`: este texto termina tanto en `ToolEndEvent.result_preview`
                    # (SSE al usuario) como en el historial de mensajes reenviado al LLM,
                    # así que no debe llevar credenciales en claro (SECURITY.md).
                    result = ToolResult(content=f"Error: {redact(str(exc))}")
                yield ToolEndEvent(
                    name=call.name, result_preview=result.content[:_RESULT_PREVIEW_LEN]
                )
                tool_result_blocks.append(_tool_result_block(call.id, result.content))

            messages.append(ChatMessage(role="tool", content=tool_result_blocks))

        # Se agotaron las MAX_TOOL_ITERATIONS vueltas sin llegar a un final en
        # texto plano: se cierra el turno igual, con el uso acumulado hasta acá.
        yield DoneEvent(usage=usage_totals)

    async def _recall_memories(
        self, ctx: ToolContext, persona: PersonaConfig, user_text: str
    ) -> list[str]:
        if not persona.memoria_activada:
            return []
        store = ctx.extras.get(_EXTRAS_MEMORY_STORE)
        if store is None:
            return []
        hits = await store.search(ctx.tenant_id, ctx.user_id, user_text)
        return [hit.content for hit in hits]


def _assistant_blocks(text_parts: list[str], tool_calls: list[Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    text = "".join(text_parts)
    if text:
        blocks.append({"type": "text", "text": text})
    for call in tool_calls:
        blocks.append(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        )
    return blocks


def _tool_result_block(tool_call_id: str, content: str) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": tool_call_id, "content": content}


def _con_flags_satisfechos(tool: Tool | None, flags: dict[str, Any]) -> Tool | None:
    """`tool` tal cual si `flags` satisface TODOS sus `requires_flags` (o si
    no requiere ninguno); `None` en caso contrario. `tool is None` pasa
    directo (nada que chequear — ya es "herramienta desconocida" para quien
    llama). Mismo criterio (`_flags_satisfechos`) que `ToolRegistry.specs()`
    ya usa para decidir qué se OFRECE al modelo, ahora también aplicado en
    `_run_turn` a qué tool resuelta se EJECUTA — ver el comentario en
    `resolved_calls` y `docs/seguridad-modelo-amenazas.md` (Hallazgo 1)."""
    if tool is None or not _flags_satisfechos(tool.requires_flags, flags):
        return None
    return tool


def _extra_tools_disponibles(
    extra_tools: Sequence[Tool] | None,
    flags: dict[str, Any],
    base_specs: list[ToolSpec],
) -> dict[str, Tool]:
    """`{name: Tool}` de `extra_tools` que pasan el mismo filtro
    `requires_flags` que `ToolRegistry.specs()` (mismo criterio: TODOS los
    flags deben estar presentes con valor verdadero) y cuyo `name` no
    colisiona con ninguna spec del registry base — ver el docstring de
    `Agent.run_turn` ("gana el registry base"). Nunca toca `self._registry`."""
    if not extra_tools:
        return {}
    nombres_base = {spec.name for spec in base_specs}
    return {
        tool.name: tool
        for tool in extra_tools
        if tool.name not in nombres_base
        and all(bool(flags.get(flag_name)) for flag_name in tool.requires_flags)
    }


def _extra_specs(extra_by_name: dict[str, Tool]) -> list[ToolSpec]:
    return [
        ToolSpec(name=tool.name, description=tool.description, input_schema=tool.input_schema)
        for tool in extra_by_name.values()
    ]
