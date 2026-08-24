"""Acción `gym_checkin` de una automatización: el check-in proactivo
"¿Vas a ir al gym hoy?" (Lun-Sáb 11:00).

NO corre un turno de agente headless: igual que la delegación directa a
`create_linkedin_post` (ver `run_automation.py`, "## Delegación directa"), el
camino de DISPARO es 100% determinista (cero LLM). Esta función hace DOS
cosas:

1. **Publicar la pregunta en el chat del asistente** como un mensaje `role=
   "assistant"` en la conversación PRINCIPAL (`is_main`), con un bloque
   `presentation` de tipo `"gym_checkin"` de shape
   `{"titulo": "¿Vas a ir al gym hoy?", "botones": [{"label": "Sí",
   "accion": "gym_yes"}, {"label": "No", "accion": "gym_no"}]}`. El mismo
   bloque viaja también dentro de `tool_calls[0].blocks` (con
   `blocks_version: 1`), que es el canal que iOS lee para pintar la tarjeta en
   vivo (`.toolEnd`), igual que `create_linkedin_post.py`.
2. **Enviar un push** con `category="GYM_CHECKIN"` (título "Edecán", cuerpo
   "¿Vas a ir al gym hoy?"). El push es SIEMPRE best-effort y va DESPUÉS de
   que la card ya quedó persistida (mismo criterio que `send_reminder.py`):
   un fallo de push nunca hace que la pregunta "se pierda".

Firma `run_gym_checkin(ctx, save_run)`: `ctx` es un `ToolContext` cuyo
`extras` trae `deps` (`edecan_worker.deps.Deps`), inyectado por
`run_automation.handle` al armar el contexto para esta rama; `save_run` es el
mismo callable `(status, detalle) -> None` de `RunnerDeps` (persiste la fila
`automation_runs` y `automations.last_run_at` en su propia sesión corta).

Si publicar la card falla (base caída), se persiste `save_run("error", ...)`
y NO se envía push (un push de algo que no está en el chat sería peor que no
avisar, mismo criterio que `create_linkedin_post` con `work_failed`). El push
sí se envía aun si la card ya está guardada; su fallo se registra en el
`detalle` del run pero nunca se considera un error de la automatización.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from edecan_worker import push
from edecan_worker.repo import SqlRepo

logger = logging.getLogger(__name__)

TITULO_PUSH = "Edecán"
CUERPO_PUSH = "¿Vas a ir al gym hoy?"
CATEGORY_PUSH = "GYM_CHECKIN"

_TITULO_CARD = "¿Vas a ir al gym hoy?"

# Los botones son un vocabulario CERRADO de la UI: iOS solo reconoce
# `gym_yes`/`gym_no` (ver `apps/mobile/ios/.../GymCheckinCardView.swift`).
_BOTONES_CARD: tuple[dict[str, str], ...] = (
    {"label": "Sí", "accion": "gym_yes"},
    {"label": "No", "accion": "gym_no"},
)


def card_gym_checkin() -> dict[str, Any]:
    """El bloque `presentation` de tipo `"gym_checkin"` (shape exacta que iOS
    decodifica como `GymCheckinBlock`, ver su docstring)."""
    return {
        "type": "gym_checkin",
        "schema_version": 1,
        "fallback_text": _TITULO_CARD,
        "titulo": _TITULO_CARD,
        "botones": list(_BOTONES_CARD),
    }


async def run_gym_checkin(ctx: Any, save_run: Any) -> None:
    """Ver docstring del módulo. Nunca lanza por un fallo del push (best-effort);
    sí deja propagar un fallo de infraestructura grave al persistir la card, de
    la misma forma que `RunnerDeps.save_run` deja propagar si no puede escribir."""
    deps = ctx.extras["deps"]
    tenant_id = ctx.tenant_id
    user_id = ctx.user_id

    # 1) Card durable en el chat principal — sesión corta independiente,
    # comiteada al salir del `async with` (igual que `save_run`/`send_reminder`).
    conversation_id: uuid.UUID | None = None
    try:
        async with deps.session_factory(None) as session:
            repo = SqlRepo(session)
            conversation = await repo.resolve_main_conversation(
                tenant_id=tenant_id, user_id=user_id
            )
            conversation_id = conversation["id"]
            card = card_gym_checkin()
            tool_call_id = f"gym-{uuid.uuid4().hex[:12]}"
            await repo.add_message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                role="assistant",
                content={"text": _TITULO_CARD, "presentation": [card]},
                tool_calls=[
                    {
                        "type": "tool_end",
                        "tool_call_id": tool_call_id,
                        "name": "gym_checkin",
                        "result_preview": _TITULO_CARD,
                        "blocks_version": 1,
                        "blocks": [card],
                    }
                ],
            )
    except Exception:
        logger.exception(
            "run_gym_checkin: no se pudo publicar la card en el chat principal "
            "(tenant_id=%s, user_id=%s).",
            tenant_id,
            user_id,
        )
        await save_run("error", {"error": "no se pudo publicar la card del gym_checkin"})
        return

    # 2) Push best-effort con category (ver docstring del módulo). El mensaje
    # YA quedó persistido arriba, así que un fallo acá no pierde la pregunta.
    data = {"route": "activity", "chat_id": str(conversation_id)} if conversation_id else None
    try:
        resultado = await push.enviar_push_a_usuario(
            deps,
            tenant_id=tenant_id,
            user_id=user_id,
            titulo=TITULO_PUSH,
            cuerpo=CUERPO_PUSH,
            data=data,
            category=CATEGORY_PUSH,
        )
    except Exception:
        logger.warning(
            "run_gym_checkin: fallo inesperado enviando el push (la card ya quedó "
            "guardada; esto no la afecta).",
            exc_info=True,
        )
        resultado = push.ResultadoEnvioPush(0, 0)

    await save_run(
        "done", {"enviados": resultado.enviados, "fallidos": resultado.fallidos}
    )
    logger.info(
        "run_gym_checkin completado tenant_id=%s enviados=%d fallidos=%d",
        tenant_id,
        resultado.enviados,
        resultado.fallidos,
    )