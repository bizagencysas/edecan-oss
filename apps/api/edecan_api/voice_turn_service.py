"""Turno de voz sobre el mismo cerebro operativo del chat.

El transporte realtime no debe tener una segunda versión de persona, memoria,
tool registry o persistencia. Este servicio compone esas piezas y devuelve
eventos ya serializables para WebSocket u otra interfaz de voz.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from edecan_core.agent import Agent
from edecan_core.queue import enqueue
from edecan_core.safety import redact
from edecan_core.session_store import load_unified_session, save_unified_session
from edecan_core.speech_tags import enriquecer_speech_tags
from edecan_core.tools import ToolContext
from edecan_llm.router import LLMRouter

from edecan_api.chat_context import ChatContextLimits, build_contextual_history
from edecan_api.chat_delegation import (
    build_delegation_prefix,
    ejecutar_delegaciones,
)
from edecan_api.config import Settings
from edecan_api.deps import CurrentUser
from edecan_api.llm_attribution import build_llm_usage_meta
from edecan_api.repo import Repo
from edecan_api.voice_orchestration import route_voice_intent

logger = logging.getLogger(__name__)


def _agent_for_request(request: Any, llm_router: Any, registry: Any) -> Agent:
    kwargs: dict[str, Any] = {}
    try:
        supports_health = "provider_health" in inspect.signature(Agent).parameters
    except (TypeError, ValueError):
        supports_health = False
    if supports_health:
        kwargs["provider_health"] = getattr(request.app.state, "provider_health", None)
    # Bots (chats con nombre y turnos por encargo): razonamiento profundo Sol
    # Xhigh. El Agent lo aplica SOLO a los modelos gpt-5.6 de Azure — los
    # turnos de voz (modelo de baja latencia @cf/...) quedan intactos.
    try:
        if "reasoning_effort" in inspect.signature(Agent).parameters:
            kwargs["reasoning_effort"] = "xhigh"
    except (TypeError, ValueError):
        pass
    return Agent(llm_router, registry, **kwargs)


@dataclass
class VoiceAgentTurnResult:
    text: str
    events: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    attribution: dict[str, Any] = field(default_factory=dict)
    confirmation_required: dict[str, Any] | None = None


async def execute_voice_text_turn(
    *,
    request: Any,
    session: Any,
    repo: Repo,
    vault: Any,
    current_user: CurrentUser,
    settings: Settings,
    llm_router: LLMRouter,
    conversation_id: UUID,
    user_text: str,
    direct_user_content: Any | None = None,
) -> VoiceAgentTurnResult:
    """Ejecuta y persiste un turno de voz usando `Agent.run_turn`.

    El texto se redacta antes de persistirse o entrar al modelo. Las tools
    dangerous siguen detenidas en `confirmation_required`; el transporte no
    auto-aprueba acciones solo por venir de un micrófono.
    """

    from edecan_api.routers.conversations import (
        _build_ctx,
        _event_to_dict,
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

    clean_text = redact(str(user_text or "")).strip()
    if not clean_text:
        raise ValueError("La transcripción de voz quedó vacía.")

    orchestration = route_voice_intent(clean_text)

    history_rows = await repo.list_messages(
        tenant_id=current_user.tenant_id,
        conversation_id=conversation_id,
        limit=max(50, int(settings.CHAT_CONTEXT_MAX_MESSAGES)),
        after=conversation.get("context_cleared_at"),
    )
    history = build_contextual_history(
        current_rows=history_rows,
        cross_chat_rows=[],
        limits=ChatContextLimits(
            enabled=settings.CHAT_CONTEXT_ENABLED,
            recent_messages=settings.CHAT_CONTEXT_RECENT_MESSAGES,
            max_messages=settings.CHAT_CONTEXT_MAX_MESSAGES,
            max_chars=settings.CHAT_CONTEXT_MAX_CHARS,
            cross_chat_enabled=False,
            cross_chat_conversations=0,
            cross_chat_messages_per_conversation=0,
            cross_chat_max_chars=0,
        ),
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
    unified_session.touch(modality="image" if direct_user_content is not None else "voice")

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
    if direct_user_content is not None:
        ctx.extras["direct_user_content"] = direct_user_content
    ctx.extras["lo_pidio_una_persona"] = True
    ctx.extras["tools_con_pregunta_pendiente"] = _tools_con_pregunta_pendiente(history_rows)

    await repo.add_message(
        tenant_id=current_user.tenant_id,
        conversation_id=conversation_id,
        role="user",
        content={"text": clean_text},
    )
    extra_tools = await _extra_conversation_tools(request, current_user)
    agent = _agent_for_request(request, llm_router, request.app.state.tool_registry)

    confirmaciones: list[str] = []
    pendientes: list[str] = []
    effective_text = clean_text
    if orchestration is not None and orchestration.delegated:
        confirmaciones, pendientes = await ejecutar_delegaciones(ctx, orchestration)
        if not confirmaciones and not pendientes:
            # Ninguna delegación se encoló ni quedó pendiente de aprobar:
            # fail-open, el turno responde el pedido original normalmente.
            effective_text = clean_text
        else:
            effective_text = orchestration.reply_text.strip()

    result = VoiceAgentTurnResult(text="")
    if effective_text:
        async for raw_event in agent.run_turn(
            ctx=ctx,
            persona=persona,
            history=history,
            user_text=effective_text,
            flags=current_user.tenant.flags,
            extra_tools=extra_tools,
        ):
            event = _event_to_dict(raw_event)
            result.events.append(event)
            event_type = event.get("type")
            if event_type == "text_delta":
                result.text += str(event.get("text") or "")
            elif event_type == "done":
                usage = event.get("usage") or {}
                result.attribution = build_llm_usage_meta(
                    attribution=event.get("attribution"),
                    input_tokens=int(usage.get("input_tokens", 0) or 0),
                    output_tokens=int(usage.get("output_tokens", 0) or 0),
                    cached_input_tokens=int(usage.get("cached_input_tokens", 0) or 0),
                )
                result.usage = {
                    "input_tokens": int(usage.get("input_tokens", 0) or 0),
                    "output_tokens": int(usage.get("output_tokens", 0) or 0),
                }
            elif event_type == "confirmation_required":
                result.confirmation_required = event

    prefix = build_delegation_prefix(confirmaciones, pendientes)
    if prefix:
        result.text = (prefix + ". " + result.text) if result.text.strip() else prefix

    tool_log = [event for event in result.events if event.get("type") in {"tool_start", "tool_end"}]
    await repo.add_message(
        tenant_id=current_user.tenant_id,
        conversation_id=conversation_id,
        role="assistant",
        content={"text": enriquecer_speech_tags(result.text)},
        tool_calls=tool_log or None,
        tokens_in=result.usage.get("input_tokens", 0),
        tokens_out=result.usage.get("output_tokens", 0),
    )
    total_tokens = result.usage.get("input_tokens", 0) + result.usage.get("output_tokens", 0)
    if total_tokens:
        await repo.add_usage_event(
            tenant_id=current_user.tenant_id,
            kind="llm_tokens",
            quantity=float(total_tokens),
            meta={"conversation_id": str(conversation_id), **result.attribution},
            cost_usd=result.attribution.get("cost_usd"),
        )
    await repo.add_usage_event(
        tenant_id=current_user.tenant_id,
        kind="messages",
        quantity=1.0,
        meta={
            "conversation_id": str(conversation_id),
            "modality": "voice",
            **result.attribution,
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
        logger.warning("no se pudo encolar memory_consolidate para turno de voz", exc_info=True)
    await save_unified_session(
        session,
        unified_session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
    )
    return result
