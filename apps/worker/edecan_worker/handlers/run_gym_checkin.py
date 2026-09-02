"""Acción `gym_checkin`: despierta un turno vivo de Edecán con la card Sí/No adjunta."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date
from typing import Any

from edecan_core.companion_wake_enqueue import enqueue_companion_wake

from edecan_worker.repo import SqlRepo

logger = logging.getLogger(__name__)

TITULO_PUSH = "Edecán"
CATEGORY_PUSH = "GYM_CHECKIN"

_BOTONES_CARD: tuple[dict[str, str], ...] = (
    {"label": "Sí", "accion": "gym_yes"},
    {"label": "No", "accion": "gym_no"},
)


def card_gym_checkin() -> dict[str, Any]:
    """Bloque `presentation` tipo `gym_checkin` (shape que decodifica iOS)."""
    return {
        "type": "gym_checkin",
        "schema_version": 1,
        "fallback_text": "Check-in de gym",
        "titulo": "Check-in de gym",
        "botones": list(_BOTONES_CARD),
    }


def _gym_wake_instruction() -> str:
    return "\n".join(
        [
            "[Edecán — turno proactivo interno, no visible para el usuario]",
            "",
            "Es el check-in proactivo de gym (Lun–Sáb ~11:00). Pregúntale al dueño si va a ir "
            "al gym hoy, en tus propias palabras, en español de Venezuela (tú, sin voseo).",
            "",
            "Reglas:",
            "- Este despertar exige un mensaje: [NO_MESSAGE] y el silencio no son válidos.",
            "- El mensaje va acompañado de una card interactiva Sí/No; adjúntala con el texto "
            "que escribas (ya viene en el payload del job).",
            "- No copies una frase fija del sistema; redacta tú.",
        ]
    )


async def run_gym_checkin(ctx: Any, save_run: Any) -> None:
    deps = ctx.extras["deps"]
    tenant_id = ctx.tenant_id
    user_id = ctx.user_id
    hoy = date.today().isoformat()
    wake_key = f"gym_checkin:{hoy}"

    card = card_gym_checkin()
    tool_call_id = f"gym-{uuid.uuid4().hex[:12]}"
    tool_end = {
        "type": "tool_end",
        "tool_call_id": tool_call_id,
        "name": "gym_checkin",
        "result_preview": "Check-in de gym",
        "blocks_version": 1,
        "blocks": [card],
    }

    conversation_id: uuid.UUID | None = None
    try:
        async with deps.session_factory(None) as session:
            repo = SqlRepo(session)
            conversation = await repo.resolve_main_conversation(
                tenant_id=tenant_id, user_id=user_id
            )
            conversation_id = conversation["id"]

        await enqueue_companion_wake(
            deps.settings,
            tenant_id=tenant_id,
            payload={
                "user_id": str(user_id),
                "wake_key": wake_key,
                "source": "gym_checkin",
                "urgent": True,
                "require_message": True,
                "instruction": _gym_wake_instruction(),
                "conversation_id": str(conversation_id) if conversation_id else None,
                "message_presentation": [card],
                "message_tool_calls": [tool_end],
                "push": {
                    "title": TITULO_PUSH,
                    "category": CATEGORY_PUSH,
                    "data": {"route": "activity", "chat_id": str(conversation_id)}
                    if conversation_id
                    else {"route": "activity"},
                },
            },
        )
    except Exception:
        logger.exception(
            "run_gym_checkin: no se pudo encolar el turno companion tenant_id=%s user_id=%s",
            tenant_id,
            user_id,
        )
        await save_run("error", {"error": "no se pudo encolar gym_checkin"})
        return

    await save_run("done", {"wake_key": wake_key, "encolado": True})
    logger.info(
        "run_gym_checkin: turno companion encolado tenant_id=%s wake_key=%s",
        tenant_id,
        wake_key,
    )
