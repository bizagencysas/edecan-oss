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
    PendingAgentTurn,
    PendingChatMessage,
    PendingToolCall,
    PersonaConfig,
    TextDeltaEvent,
    ToolEndEvent,
    ToolSpec,
    ToolStartEvent,
)

from .capability_routing import build_capability_guidance, select_tool_specs
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


class PendingTurnValidationError(RuntimeError):
    """El turno persistido ya no es ejecutable bajo las capacidades actuales."""


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

        Sigue aceptando el camino compatible de pre-aprobación de una tool
        `dangerous`: si se llama `run_turn` con
        `ctx.extras["approved_tool_calls"]` conteniendo el `tool_call_id` de
        una `extra_tool` ya solicitada, esta la resuelve y ejecuta igual que
        cualquier tool del registry — no hace falta ningún camino especial.
        El endpoint HTTP `POST /v1/conversations/{id}/confirm` usa
        `resume_turn` para continuar exactamente el turno serializado; solo
        confirmaciones antiguas sin estado de continuación usan el fallback
        directo compatible.
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
        messages: list[ChatMessage] = [*history, ChatMessage(role="user", content=user_text)]
        # `extra_by_name`: solo las `extra_tools` que (a) tienen sus
        # `requires_flags` satisfechos por `flags` y (b) NO colisionan de
        # nombre con el registry base — ver el docstring de `run_turn`.
        all_base_specs = self._registry.specs(flags)
        recent_user_texts = [
            message.content
            for message in history[-6:]
            if message.role == "user" and isinstance(message.content, str)
        ][-2:]
        all_extra_by_name = _extra_tools_disponibles(extra_tools, flags, all_base_specs)
        all_extra_specs = _extra_specs(all_extra_by_name)
        selected_specs = select_tool_specs(
            [*all_base_specs, *all_extra_specs],
            user_text,
            recent_user_texts=recent_user_texts,
        )
        selected_names = {spec.name for spec in selected_specs}
        base_specs = [spec for spec in all_base_specs if spec.name in selected_names]
        extra_by_name = {
            name: tool for name, tool in all_extra_by_name.items() if name in selected_names
        }
        extra_specs = [spec for spec in all_extra_specs if spec.name in selected_names]
        tool_specs = [*base_specs, *extra_specs]
        system_prompt = build_system_prompt(
            persona,
            memories,
            extra_context=build_capability_guidance(
                selected_specs=tool_specs,
                all_specs=[*all_base_specs, *all_extra_specs],
                language=persona.idioma,
            ),
        )
        provider, model = self._llm_router.resolve(self._model_alias, flags)
        approved_tool_calls = set(ctx.extras.get(_EXTRAS_APPROVED_TOOL_CALLS, set()))
        async for event in self._continue_turn(
            ctx=ctx,
            provider=provider,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            tool_specs=tool_specs,
            extra_by_name=extra_by_name,
            flags=flags,
            start_iteration=0,
            approved_tool_calls=approved_tool_calls,
            usage_totals={"input_tokens": 0, "output_tokens": 0},
            accumulated_text="",
            tool_log=[],
        ):
            yield event

    async def resume_turn(
        self,
        *,
        ctx: ToolContext,
        pending: PendingAgentTurn,
        approved_tool_call_id: str,
        flags: dict[str, Any],
        extra_tools: Sequence[Tool] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Continúa un turno suspendido sin volver a enviar la orden original.

        El catálogo base y las tools MCP se resuelven de nuevo, y cada nombre
        del lote se contrasta con la superficie que fue ofrecida originalmente
        y con los flags actuales. Cualquier diferencia falla antes de ejecutar
        una sola tool. Si apareció otra tool peligrosa en el mismo lote, vuelve
        a suspender y solicita esa confirmación antes de iniciar el batch.
        """
        try:
            async for event in self._resume_turn(
                ctx=ctx,
                pending=pending,
                approved_tool_call_id=approved_tool_call_id,
                flags=flags,
                extra_tools=extra_tools,
            ):
                yield event
        except Exception as exc:  # noqa: BLE001 - contrato público: errores como evento
            logger.warning("No se pudo reanudar el turno pendiente", exc_info=True)
            yield ErrorEvent(message=redact(str(exc)))

    async def _resume_turn(
        self,
        *,
        ctx: ToolContext,
        pending: PendingAgentTurn,
        approved_tool_call_id: str,
        flags: dict[str, Any],
        extra_tools: Sequence[Tool] | None,
    ) -> AsyncIterator[AgentEvent]:
        call_ids = {call.id for call in pending.tool_calls}
        if approved_tool_call_id not in call_ids:
            raise PendingTurnValidationError("La confirmación no pertenece al lote pendiente.")
        if approved_tool_call_id in pending.approved_tool_call_ids:
            raise PendingTurnValidationError("Esa acción ya había sido aprobada.")

        base_specs = self._registry.specs(flags)
        extra_by_name = _extra_tools_disponibles(extra_tools, flags, base_specs)
        spec_by_name = {spec.name: spec for spec in _extra_specs(extra_by_name)}
        spec_by_name.update({spec.name: spec for spec in base_specs})
        tool_specs = [
            spec_by_name[name]
            for name in pending.operational_tool_names
            if name in spec_by_name
        ]
        current_operational_names = {spec.name for spec in tool_specs}
        resolved_calls = self._resolve_calls(
            pending.tool_calls,
            operational_names=(
                set(pending.operational_tool_names) & current_operational_names
            ),
            extra_by_name=extra_by_name,
            flags=flags,
        )
        unavailable = [call.name for call, tool in resolved_calls if tool is None]
        if unavailable:
            names = ", ".join(sorted(set(unavailable)))
            raise PendingTurnValidationError(
                f"La capacidad pendiente cambió o ya no está disponible: {names}."
            )

        approved = {*pending.approved_tool_call_ids, approved_tool_call_id}
        for call, tool in resolved_calls:
            if tool is not None and tool.dangerous and call.id not in approved:
                next_pending = pending.model_copy(
                    update={"approved_tool_call_ids": sorted(approved)}
                )
                yield ConfirmationRequiredEvent(
                    tool_call_id=call.id,
                    name=call.name,
                    args=call.arguments,
                    pending_turn=next_pending,
                )
                return

        # Resolver el proveedor antes de cualquier side effect: si el modelo
        # actual ya no está configurado, el lote permanece sin ejecutar.
        provider, model = self._llm_router.resolve(self._model_alias, flags)
        messages = [_chat_message_from_pending(message) for message in pending.messages]
        tool_log = list(pending.tool_log)
        tool_result_blocks: list[dict[str, Any]] = []
        async for event, result_block in self._execute_resolved_calls(
            ctx=ctx, resolved_calls=resolved_calls, tool_log=tool_log
        ):
            if event is not None:
                yield event
            if result_block is not None:
                tool_result_blocks.append(result_block)
        messages.append(ChatMessage(role="tool", content=tool_result_blocks))

        async for event in self._continue_turn(
            ctx=ctx,
            provider=provider,
            model=model,
            system_prompt=pending.system_prompt,
            messages=messages,
            tool_specs=tool_specs,
            extra_by_name=extra_by_name,
            flags=flags,
            start_iteration=pending.iteration + 1,
            approved_tool_calls=set(),
            usage_totals=dict(pending.usage),
            accumulated_text=pending.accumulated_text,
            tool_log=tool_log,
        ):
            yield event

    async def _continue_turn(
        self,
        *,
        ctx: ToolContext,
        provider: Any,
        model: str,
        system_prompt: str | None,
        messages: list[ChatMessage],
        tool_specs: list[ToolSpec],
        extra_by_name: dict[str, Tool],
        flags: dict[str, Any],
        start_iteration: int,
        approved_tool_calls: set[str],
        usage_totals: dict[str, int],
        accumulated_text: str,
        tool_log: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        usage_totals.setdefault("input_tokens", 0)
        usage_totals.setdefault("output_tokens", 0)
        for iteration in range(start_iteration, MAX_TOOL_ITERATIONS):
            request = CompletionRequest(
                model=model, system=system_prompt, messages=list(messages), tools=tool_specs
            )
            text_parts: list[str] = []
            raw_tool_calls: list[Any] = []
            async for chunk in provider.stream(request):
                if chunk.type == "text" and chunk.text:
                    text_parts.append(chunk.text)
                    accumulated_text += chunk.text
                    yield TextDeltaEvent(text=chunk.text)
                elif chunk.type == "tool_call" and chunk.tool_call is not None:
                    raw_tool_calls.append(chunk.tool_call)
                elif chunk.type == "usage" and chunk.usage is not None:
                    usage_totals["input_tokens"] += chunk.usage.input_tokens
                    usage_totals["output_tokens"] += chunk.usage.output_tokens

            if not raw_tool_calls:
                yield DoneEvent(usage=usage_totals)
                return

            tool_calls = [
                PendingToolCall(id=call.id, name=call.name, arguments=call.arguments)
                for call in raw_tool_calls
            ]
            messages.append(
                ChatMessage(role="assistant", content=_assistant_blocks(text_parts, tool_calls))
            )
            operational_names = {spec.name for spec in tool_specs}
            resolved_calls = self._resolve_calls(
                tool_calls,
                operational_names=operational_names,
                extra_by_name=extra_by_name,
                flags=flags,
            )

            for call, tool in resolved_calls:
                if tool is not None and tool.dangerous and call.id not in approved_tool_calls:
                    approvals_for_batch = sorted(
                        call_id
                        for call_id in approved_tool_calls
                        if call_id in {item.id for item in tool_calls}
                    )
                    pending = PendingAgentTurn(
                        messages=[_pending_message(message) for message in messages],
                        tool_calls=tool_calls,
                        operational_tool_names=sorted(operational_names),
                        usage=dict(usage_totals),
                        iteration=iteration,
                        accumulated_text=accumulated_text,
                        tool_log=list(tool_log),
                        system_prompt=system_prompt,
                        approved_tool_call_ids=approvals_for_batch,
                    )
                    yield ConfirmationRequiredEvent(
                        tool_call_id=call.id,
                        name=call.name,
                        args=call.arguments,
                        pending_turn=pending,
                    )
                    return

            tool_result_blocks: list[dict[str, Any]] = []
            async for event, result_block in self._execute_resolved_calls(
                ctx=ctx, resolved_calls=resolved_calls, tool_log=tool_log
            ):
                if event is not None:
                    yield event
                if result_block is not None:
                    tool_result_blocks.append(result_block)
            messages.append(ChatMessage(role="tool", content=tool_result_blocks))

        yield DoneEvent(usage=usage_totals)

    def _resolve_calls(
        self,
        tool_calls: Sequence[Any],
        *,
        operational_names: set[str],
        extra_by_name: dict[str, Tool],
        flags: dict[str, Any],
    ) -> list[tuple[Any, Tool | None]]:
        return [
            (
                call,
                _con_flags_satisfechos(
                    (
                        self._registry.get(call.name) or extra_by_name.get(call.name)
                        if call.name in operational_names
                        else None
                    ),
                    flags,
                ),
            )
            for call in tool_calls
        ]

    async def _execute_resolved_calls(
        self,
        *,
        ctx: ToolContext,
        resolved_calls: Sequence[tuple[Any, Tool | None]],
        tool_log: list[dict[str, Any]],
    ) -> AsyncIterator[tuple[AgentEvent | None, dict[str, Any] | None]]:
        for call, tool in resolved_calls:
            if tool is None:
                logger.warning("El modelo pidió una herramienta desconocida: %r", call.name)
                yield None, _tool_result_block(
                    call.id, f"Error: herramienta desconocida '{call.name}'"
                )
                continue

            start = ToolStartEvent(name=call.name, args=call.arguments)
            tool_log.append(start.model_dump())
            yield start, None
            try:
                result = await tool.run(ctx, call.arguments)
            except Exception as exc:  # noqa: BLE001 - una tool nunca debe tumbar el turno
                logger.warning("La herramienta %r lanzó una excepción", call.name, exc_info=True)
                result = ToolResult(content=f"Error: {redact(str(exc))}")
            end = ToolEndEvent(
                name=call.name, result_preview=result.content[:_RESULT_PREVIEW_LEN]
            )
            tool_log.append(end.model_dump())
            yield end, _tool_result_block(call.id, result.content)

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


def _pending_message(message: ChatMessage) -> PendingChatMessage:
    return PendingChatMessage(role=message.role, content=message.content)


def _chat_message_from_pending(message: PendingChatMessage) -> ChatMessage:
    return ChatMessage(role=message.role, content=message.content)


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
