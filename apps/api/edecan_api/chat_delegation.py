"""Delegación natural del chat principal al workforce (paridad con voz).

Desde el chat de Edecán, frases como «pon a María a investigar esto» deben
encolar trabajo real (`delegar_mision`) — no una respuesta inventada del modelo.
Reutiliza el router determinista de `voice_orchestration` y el mismo camino
de encolado que `voice_turn_service`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from edecan_api.voice_orchestration import (
    VoiceOrchestration,
    resolve_worker_id,
    route_voice_intent,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatDelegationOutcome:
    """Resultado de preparar un turno de chat con delegación opcional."""

    user_text: str
    initial_prefix: str = ""
    delegated: bool = False


async def _listar_workers(ctx: Any) -> list[dict[str, Any]]:
    session = getattr(ctx, "session", None)
    if session is None:
        return []
    try:
        from sqlalchemy import text

        result = await session.execute(
            text(
                "SELECT id, name, display_name, role_title FROM persistent_agents "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id AND status <> 'disabled'"
            ),
            {"tenant_id": str(ctx.tenant_id), "user_id": str(ctx.user_id)},
        )
        return [dict(row) for row in result.mappings().all()]
    except Exception:  # noqa: BLE001 - best-effort
        logger.warning("delegación de chat: no se pudo listar workers", exc_info=True)
        return []


async def ejecutar_delegaciones(
    ctx: Any, orchestration: VoiceOrchestration
) -> tuple[list[str], list[str]]:
    """Encola cada sub-tarea delegada vía `DelegarMisionTool`. Best-effort."""
    from edecan_agents.tools import DelegarMisionTool

    confirmaciones: list[str] = []
    pendientes: list[str] = []
    tool = DelegarMisionTool()
    workers = await _listar_workers(ctx)
    for item in orchestration.delegated:
        if item.requires_approval:
            pendientes.append(f"{item.target + ': ' if item.target else ''}{item.instruction}")
            continue
        objetivo = item.instruction
        owner_agent_id = None
        if item.kind == "agent" and item.target:
            owner_agent_id = resolve_worker_id(workers, item.target)
            if owner_agent_id is None:
                objetivo = f"{item.instruction} (destinado a {item.target})"
        args: dict[str, Any] = {"objetivo": objetivo}
        if owner_agent_id is not None:
            args["owner_agent_id"] = owner_agent_id
        try:
            resultado = await tool.run(ctx, args)
            if getattr(resultado, "data", None):
                if item.target:
                    confirmaciones.append(
                        f"Le encargué a {item.target} que {item.instruction}"
                    )
                else:
                    confirmaciones.append(f"Encargué la tarea de {item.instruction}")
            else:
                logger.warning(
                    "delegación de chat no encolada (target=%r): %r",
                    item.target,
                    getattr(resultado, "content", None),
                )
        except Exception:  # noqa: BLE001 - best-effort
            logger.warning("delegación de chat falló (target=%r)", item.target, exc_info=True)
    return confirmaciones, pendientes


def build_delegation_prefix(confirmaciones: list[str], pendientes: list[str]) -> str:
    frases = list(confirmaciones)
    if pendientes:
        frases.append("No encargué lo que requiere tu aprobación: " + "; ".join(pendientes))
    return ". ".join(frases)


async def prepare_chat_delegation(ctx: Any, text: str) -> ChatDelegationOutcome:
    """Detecta delegación NL y encola misiones antes del turno del agente."""
    orchestration = route_voice_intent(text)
    if orchestration is None or not orchestration.delegated:
        return ChatDelegationOutcome(user_text=text)

    confirmaciones, pendientes = await ejecutar_delegaciones(ctx, orchestration)
    if not confirmaciones and not pendientes:
        return ChatDelegationOutcome(user_text=text)

    prefix = build_delegation_prefix(confirmaciones, pendientes)
    reply = orchestration.reply_text.strip()
    return ChatDelegationOutcome(
        user_text=reply,
        initial_prefix=prefix,
        delegated=True,
    )


__all__ = [
    "ChatDelegationOutcome",
    "build_delegation_prefix",
    "ejecutar_delegaciones",
    "prepare_chat_delegation",
    "route_voice_intent",
]
