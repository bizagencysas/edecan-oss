"""Turnos de la voz gestionada (Speech Engine) sobre el cerebro del chat.

El callback WSS del proveedor recibe el transcript completo por turno. Este
servicio:

1. Carga el contexto canónico (persona, perfil, historial, unified session,
   memoria) con las MISMAS piezas que el chat y el realtime de voz — no hay
   una segunda versión del cerebro.
2. Persiste UNA sola vez el enunciado final NUEVO del usuario (el payload
   completo del proveedor es dato de reconocimiento no confiable, no
   autoridad para reescribir la historia de Edecán).
3. Decide rápido-vs-delegado:
   - Interlocutor rápido: modelo configurado en `voice_model_id` con contexto
     acotado y UNA tool estrecha (`delegate_to_edecan`). Genera respuestas
     reales de charla; no invoca el pipeline pesado para un saludo.
   - Delegado: si el parser determinista detecta delegación
     (`route_voice_intent`) o el interlocutor rápido pidió
     `delegate_to_edecan`, corre el turno COMPLETO del agente
     (`execute_voice_text_turn`) con el modelo de delegación configurado (o,
     si es NULL, el que ya tiene la conversación — nunca se pisa
     `conversations.chat_model`).

Los claims durables `(session_id, event_id)` los hace el llamador (el router
WSS) ANTES de entrar acá, así que un replay del proveedor nunca duplica
mensajes ni efectos de tools.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from edecan_core.agent import Agent, SeleccionDeModelo
from edecan_core.queue import enqueue
from edecan_core.safety import redact
from edecan_core.session_store import load_unified_session, save_unified_session
from edecan_core.speech_tags import enriquecer_speech_tags
from edecan_core.tools import Tool, ToolContext, ToolResult

from edecan_api.chat_context import (
    ChatContextLimits,
    build_contextual_history,
    resumen_llm_hilo_anterior,
)
from edecan_api.config import Settings
from edecan_api.deps import CurrentUser
from edecan_api.llm_attribution import build_llm_usage_meta
from edecan_api.repo import Repo
from edecan_api.voice_orchestration import route_voice_intent

logger = logging.getLogger(__name__)

_MANAGED_MODALITY = "voice_managed"


class DelegateToEdecanTool(Tool):
    """Señal del interlocutor rápido: "esto no es charla, dale el turno al
    agente completo (herramientas, memoria, aprobaciones)".

    El modelo rápido SOLO puede pedir la delegación; no ejecuta nada por su
    cuenta con esta tool. El servicio detecta la invocación, descarta el texto
    parcial del turno rápido y corre el flujo completo con el modelo de
    delegación. La tool nunca se expone fuera del turno del interlocutor.
    """

    name = "delegate_to_edecan"
    description = (
        "Use this when the user's request needs real work — tools, memory, "
        "files, sending messages, delegating tasks, anything beyond a plain "
        "conversational reply. Calling this hands the turn to the full Edecan "
        "agent with all its tools and the configured deeper model. Return no "
        "other content in the same turn."
    )
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    category = "utility"
    risk_level = "none"
    latency_class = "instant"
    cost_class = "free"
    timeout_seconds = 5.0
    idempotent = True

    def __init__(self) -> None:
        super().__init__()
        self.invoked = False

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        del ctx, args
        self.invoked = True
        return ToolResult(content="Delegating this turn to the full Edecan agent.")


@dataclass
class ManagedTurnOutcome:
    text: str = ""
    delegated: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    attribution: dict[str, Any] = field(default_factory=dict)
    confirmation_required: dict[str, Any] | None = None


async def _load_canonical_context(
    *,
    session: Any,
    repo: Repo,
    current_user: CurrentUser,
    settings: Settings,
    conversation_id: UUID,
    request: Any,
    llm_router: Any,
    vault: Any,
    con_resumen: bool = True,
) -> tuple[dict[str, Any], list[Any], Any, Any, ToolContext, Any]:
    """Carga conversación, historia acotada, persona, perfil y ToolContext.

    Espejo de `execute_voice_text_turn` (ver ese módulo para el detalle de cada
    pieza) — duplicado a propósito para que el flujo gestionado pueda variar el
    `seleccion` por ruta sin tocar el contrato del realtime legacy.
    """
    from edecan_api.routers.conversations import (
        _build_ctx,
        _extra_conversation_tools,
        _multimodal_session_for,
        _tools_con_pregunta_pendiente,
    )
    from edecan_api.routers.perfil import profile_context_for
    from edecan_api.routers.persona import persona_from_row

    conversation = await repo.get_conversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise ValueError("Conversación no encontrada.")

    history_rows = await repo.list_messages(
        tenant_id=current_user.tenant_id,
        conversation_id=conversation_id,
        limit=max(50, int(settings.CHAT_CONTEXT_MAX_MESSAGES)),
        after=conversation.get("context_cleared_at"),
    )
    limits = ChatContextLimits(
        enabled=settings.CHAT_CONTEXT_ENABLED,
        recent_messages=settings.CHAT_CONTEXT_RECENT_MESSAGES,
        max_messages=settings.CHAT_CONTEXT_MAX_MESSAGES,
        max_chars=settings.CHAT_CONTEXT_MAX_CHARS,
        cross_chat_enabled=False,
        cross_chat_conversations=0,
        cross_chat_messages_per_conversation=0,
        cross_chat_max_chars=0,
    )
    resumen_llm = None
    if con_resumen:
        resumen_llm = await resumen_llm_hilo_anterior(history_rows, limits, llm_router=llm_router)
    history = build_contextual_history(
        current_rows=history_rows,
        cross_chat_rows=[],
        limits=limits,
        current_summary=resumen_llm or None,
    )
    persona = persona_from_row(
        await repo.get_persona(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
        )
    )
    profile_context = (
        await profile_context_for(session, current_user.tenant_id, current_user.user_id)
        if session is not None
        else ""
    )
    unified_session = await load_unified_session(
        session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    if unified_session is None:
        unified_session = _multimodal_session_for(
            tenant_id=current_user.tenant_id,
            conversation_id=conversation_id,
        )
    unified_session.user_id = str(current_user.user_id)
    unified_session.touch(modality="voice")

    ctx: ToolContext = _build_ctx(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        session=session,
        settings=settings,
        llm_router=llm_router,
        vault=vault,
        persona=persona,
        request=request,
        repo=repo,
        approved_tool_calls=set(),
        flags=current_user.tenant.flags,
        conversation_id=conversation_id,
        profile_context=profile_context,
        unified_session=unified_session,
    )
    ctx.extras["visual_memory"] = unified_session.visual_memory
    ctx.extras["lo_pidio_una_persona"] = True
    ctx.extras["tools_con_pregunta_pendiente"] = _tools_con_pregunta_pendiente(history_rows)
    return conversation, history, persona, unified_session, ctx, _extra_conversation_tools


async def _persist_usage_and_memory(
    *,
    session: Any,
    repo: Repo,
    settings: Settings,
    current_user: CurrentUser,
    unified_session: Any,
    conversation_id: UUID,
    outcome: ManagedTurnOutcome,
) -> None:
    """Persistencia común del turno gestionado (uso + memoria + unified session)."""
    total_tokens = outcome.usage.get("input_tokens", 0) + outcome.usage.get("output_tokens", 0)
    if total_tokens:
        await repo.add_usage_event(
            tenant_id=current_user.tenant_id,
            kind="llm_tokens",
            quantity=float(total_tokens),
            meta={"conversation_id": str(conversation_id), **outcome.attribution},
            cost_usd=outcome.attribution.get("cost_usd"),
        )
    await repo.add_usage_event(
        tenant_id=current_user.tenant_id,
        kind="messages",
        quantity=1.0,
        meta={
            "conversation_id": str(conversation_id),
            "modality": _MANAGED_MODALITY,
            **outcome.attribution,
        },
    )
    try:
        await enqueue(
            settings,
            "memory_consolidate",
            {"user_id": str(current_user.user_id)},
            current_user.tenant_id,
        )
    except Exception:  # noqa: BLE001 - el mensaje ya está persistido
        logger.warning("no se pudo encolar memory_consolidate para turno gestionado", exc_info=True)
    await save_unified_session(
        session,
        unified_session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )


def _modelo_valido_para_turno(model_id: str | None) -> SeleccionDeModelo | None:
    """`SeleccionDeModelo` solo si el id está en el catálogo del chat (la
    autoridad es `config/modelos.yml` → `modelos_chat`; un id fuera de
    catálogo se ignora y decide la heurística, igual que el chat)."""
    from edecan_llm.task_router import modelo_chat_permitido

    if not model_id or not modelo_chat_permitido(model_id):
        return None
    return SeleccionDeModelo(modelo=model_id, esfuerzo=None)


async def execute_fast_interlocutor_turn(
    *,
    request: Any,
    session: Any,
    repo: Repo,
    vault: Any,
    current_user: CurrentUser,
    settings: Settings,
    llm_router: Any,
    conversation_id: UUID,
    user_text: str,
    fast_model_id: str | None,
    already_persisted_input: bool = False,
    on_text_delta: Any | None = None,
) -> ManagedTurnOutcome:
    """Turno del interlocutor RÁPIDO: modelo de voz configurado, contexto
    acotado y solo la tool `delegate_to_edecan`. Persiste el enunciado del
    usuario UNA vez y la respuesta del asistente.

    Devuelve `delegated=True` si el modelo rápido pidió delegar (el llamador
    entonces corre `execute_delegated_turn` SIN volver a persistir el
    enunciado).
    """
    from edecan_api.routers.conversations import _event_to_dict

    clean_text = redact(str(user_text or "")).strip()
    if not clean_text:
        raise ValueError("La transcripción de voz quedó vacía.")

    conversation, history, persona, unified_session, ctx, _extra_tools_fn = (
        await _load_canonical_context(
            session=session,
            repo=repo,
            current_user=current_user,
            settings=settings,
            conversation_id=conversation_id,
            request=request,
            llm_router=llm_router,
            vault=vault,
            # Un saludo no paga un resumen por LLM: el interlocutor rápido usa
            # el historial acotado tal cual (la charla tiene que ser barata e
            # inmediata; el contexto profundo es del flujo delegado).
            con_resumen=False,
        )
    )
    del conversation, _extra_tools_fn

    if not already_persisted_input:
        await repo.add_message(
            tenant_id=current_user.tenant_id,
            conversation_id=conversation_id,
            role="user",
            content={"text": clean_text},
        )

    delegate_tool = DelegateToEdecanTool()
    # Registro RESTRINGIDO: el interlocutor rápido solo ve la tool de
    # delegación. Sin esto, un saludo llevaba el registry completo (tools
    # peligrosas incluidas) y la respuesta dejaba de ser inmediata. La tool
    # estrecha es la única puerta hacia el agente completo.
    from edecan_core.tools import ToolRegistry

    fast_registry = ToolRegistry()
    fast_registry.register(delegate_tool)
    agent = Agent(
        llm_router,
        fast_registry,
        provider_health=getattr(request.app.state, "provider_health", None),
    )
    outcome = ManagedTurnOutcome()
    tool_log: list[dict[str, Any]] = []
    async for raw_event in agent.run_turn(
        ctx=ctx,
        persona=persona,
        history=history,
        user_text=clean_text,
        flags=current_user.tenant.flags,
        extra_tools=[delegate_tool],
        seleccion=_modelo_valido_para_turno(fast_model_id),
    ):
        event = _event_to_dict(raw_event)
        outcome.events.append(event)
        event_type = event.get("type")
        if event_type == "text_delta":
            delta_text = str(event.get("text") or "")
            outcome.text += delta_text
            if on_text_delta is not None:
                await on_text_delta(delta_text)
        elif event_type == "done":
            usage = event.get("usage") or {}
            outcome.attribution = build_llm_usage_meta(
                attribution=event.get("attribution"),
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
                cached_input_tokens=int(usage.get("cached_input_tokens", 0) or 0),
            )
            outcome.usage = {
                "input_tokens": int(usage.get("input_tokens", 0) or 0),
                "output_tokens": int(usage.get("output_tokens", 0) or 0),
            }
        elif event_type == "confirmation_required":
            outcome.confirmation_required = event
        elif event_type in {"tool_start", "tool_end"}:
            tool_log.append(event)

    if delegate_tool.invoked:
        outcome.delegated = True
        # El texto del turno rápido se descarta: la respuesta real la produce
        # el agente completo en el flujo delegado. El uso del modelo rápido es
        # costo REAL y se registra con atribución propia (sin duplicar el del
        # turno delegado); la memoria la consolida el turno delegado.
        outcome.text = ""
        total_tokens = outcome.usage.get("input_tokens", 0) + outcome.usage.get("output_tokens", 0)
        if total_tokens:
            await repo.add_usage_event(
                tenant_id=current_user.tenant_id,
                kind="llm_tokens",
                quantity=float(total_tokens),
                meta={
                    "conversation_id": str(conversation_id),
                    "modality": _MANAGED_MODALITY,
                    "ruta": "interlocutor_rapido_delegacion",
                    **outcome.attribution,
                },
                cost_usd=outcome.attribution.get("cost_usd"),
            )
        return outcome
    await repo.add_message(
        tenant_id=current_user.tenant_id,
        conversation_id=conversation_id,
        role="assistant",
        content={"text": enriquecer_speech_tags(outcome.text)},
        tool_calls=tool_log or None,
        tokens_in=outcome.usage.get("input_tokens", 0),
        tokens_out=outcome.usage.get("output_tokens", 0),
    )

    await _persist_usage_and_memory(
        session=session,
        repo=repo,
        settings=settings,
        current_user=current_user,
        unified_session=unified_session,
        conversation_id=conversation_id,
        outcome=outcome,
    )
    return outcome


async def execute_delegated_turn(
    *,
    request: Any,
    session: Any,
    repo: Repo,
    vault: Any,
    current_user: CurrentUser,
    settings: Settings,
    llm_router: Any,
    conversation_id: UUID,
    user_text: str,
    delegation_seleccion: SeleccionDeModelo | None = None,
    on_text_delta: Any | None = None,
) -> ManagedTurnOutcome:
    """Turno DELEGADO: el agente completo (`Agent.run_turn` con el registry
    entero, aprobaciones y la delegación determinista de `voice_orchestration`)
    con la selección de modelo resuelta por el llamador — herencia incluida:
    `None` cae al modelo/esfuerzo que YA tiene la conversación (nunca se pisa
    `conversations.chat_model`). El enunciado del usuario YA está persistido
    (lo persistió el interlocutor rápido o el router de delegación directa),
    así que no se duplica."""
    from edecan_api.voice_turn_service import (
        VoiceAgentTurnResult,
        execute_voice_text_turn,
    )

    if delegation_seleccion is None:
        # Herencia: el modelo que la persona eligió para este chat.
        conversation = await repo.get_conversation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            conversation_id=conversation_id,
        )
        modelo_chat = (conversation or {}).get("chat_model")
        esfuerzo_chat = (conversation or {}).get("chat_effort")
        delegation_seleccion = _modelo_valido_para_turno(modelo_chat)
        if delegation_seleccion is not None and esfuerzo_chat:
            delegation_seleccion = SeleccionDeModelo(
                modelo=delegation_seleccion.modelo, esfuerzo=esfuerzo_chat
            )

    result: VoiceAgentTurnResult = await execute_voice_text_turn(
        request=request,
        session=session,
        repo=repo,
        vault=vault,
        current_user=current_user,
        settings=settings,
        llm_router=llm_router,
        conversation_id=conversation_id,
        user_text=user_text,
        already_persisted_input=True,
        seleccion=delegation_seleccion,
        on_text_delta=on_text_delta,
    )
    return ManagedTurnOutcome(
        text=result.text,
        delegated=True,
        events=result.events,
        usage=result.usage,
        attribution=result.attribution,
        confirmation_required=result.confirmation_required,
    )


def require_delegation(user_text: str) -> bool:
    """Detección determinista previa al interlocutor rápido (delegación NL
    explícita) — el modelo rápido ni siquiera se consulta para "dile al
    Developer que...". Un turno así va directo al flujo delegado."""
    return route_voice_intent(user_text) is not None


__all__ = [
    "DelegateToEdecanTool",
    "ManagedTurnOutcome",
    "execute_delegated_turn",
    "execute_fast_interlocutor_turn",
    "require_delegation",
]