"""Narración en vivo del trabajo de un bot — «avisan todo» (regla del dueño).

El dueño quiere bots que cuenten su trabajo EN EL CHAT mientras trabajan,
como un LLM en un CLI que va diciendo «encontrado:…», «voy a probar…» — no
un silencio con spinner. `AvisarAvanceTool` es ese canal: el modelo la llama
a mitad de un trabajo de varios pasos y el aviso aparece AL INSTANTE en su
chat (viaja en el `tool_end` del turno como result_preview) y queda
PERSISTIDO para cuando el dueño vuelva.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from edecan_core.queue import enqueue
from edecan_core.tools.base import Tool, ToolContext, ToolResult
from sqlalchemy import text

logger = logging.getLogger(__name__)

_AVATAR_PUSH_KEYS = ("avatar_shape", "avatar_fill", "avatar_accent")


def _payload_push_avance_proactivo(
    ctx: ToolContext,
    chat: dict[str, Any],
    *,
    mensaje: str,
) -> dict[str, str]:
    """Payload seguro para notify_important_event (Communication Notification)."""
    worker_name = str(chat.get("worker_name") or "Bot").strip() or "Bot"
    cuerpo = " ".join(mensaje.split())[:200]
    payload: dict[str, str] = {
        "user_id": str(ctx.user_id),
        "kind": "agent_bot_message",
        "event_id": str(uuid4()),
        "chat_id": str(chat["conversation_id"]),
        "worker_id": str(chat["worker_id"]),
        "apns_title": (worker_name or "").strip(),
        "apns_body": cuerpo,
        "sender_display_name": (worker_name or "").strip(),
    }
    for key in _AVATAR_PUSH_KEYS:
        valor = chat.get(key)
        if isinstance(valor, str) and valor.strip():
            payload[key] = valor.strip()
    return payload


async def _encolar_push_avance_proactivo(
    ctx: ToolContext,
    chat: dict[str, Any],
    *,
    mensaje: str,
) -> None:
    """Best-effort: el aviso in-app ya quedó persistido; el push es para background."""
    settings = getattr(ctx, "settings", None)
    tenant_id = getattr(ctx, "tenant_id", None)
    if settings is None or tenant_id is None:
        return
    try:
        await enqueue(
            settings,
            "notify_important_event",
            _payload_push_avance_proactivo(ctx, chat, mensaje=mensaje),
            tenant_id if isinstance(tenant_id, UUID) else UUID(str(tenant_id)),
        )
    except Exception:  # noqa: BLE001 - narración in-app no se frena por push
        logger.warning(
            "avisar_avance: no pude encolar push proactivo (chat=%s worker=%s)",
            chat.get("conversation_id"),
            chat.get("worker_id"),
            exc_info=True,
        )


class AvisarAvanceTool(Tool):
    name = "avisar_avance"
    description = (
        "Cuenta un avance de tu trabajo EN EL CHAT con tu dueño, al instante, "
        "mientras sigues trabajando. Úsalo MUCHO en tareas de varios pasos: al "
        "empezar («voy a revisar tu LinkedIn»), al encontrar algo importante "
        "(«encontré el problema: era un doble envío»), al decidir un cambio "
        "(«voy a reescribirlo en tono directo») y antes de dar el resultado "
        "final. Como un compañero que narra lo que hace en vivo — nunca un "
        "silencio con 'trabajando…'."
    )
    category = "external_comm"
    risk_level = "low"
    input_schema = {
        "type": "object",
        "properties": {
            "mensaje": {
                "type": "string",
                "description": "El avance, en tu voz, en 1-2 frases "
                "(p. ej. 'Encontré el bug: era un doble envío').",
            },
        },
        "required": ["mensaje"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        mensaje = " ".join(str(args.get("mensaje") or "").split())
        if not mensaje:
            return ToolResult(content="El aviso necesita un texto no vacío.")

        chat = ctx.extras.get("worker_chat") or {}
        conversation_id = chat.get("conversation_id")
        worker_id = chat.get("worker_id")
        worker_name = str(chat.get("worker_name") or "Bot").strip() or "Bot"
        if not conversation_id or not worker_id:
            return ToolResult(
                content="Este canal no tiene chat propio: los avisos de avance "
                "solo funcionan en el chat 1:1 de un bot."
            )

        # El aviso se persiste como mensaje real del bot (assistant): aparece
        # como burbuja en el chat, sobrevive recargas y viaja en el
        # result_preview de esta tool para mostrarse EN VIVO mientras el
        # turno sigue corriendo.
        #
        # Va en una SESIÓN DE CORTA VIDA separada, NO en `ctx.session`:
        # `get_tenant_session` mantiene la transacción del turno abierta con
        # `async with session.begin()`, y un `ctx.session.commit()` acá la
        # cerraba — todo lo posterior al aviso (el gate del dueño de la tool
        # siguiente, el `add_message` final del turno) reventaba con
        # `InvalidRequestError: Can't operate on closed transaction`: el bot
        # mismo veía "esta herramienta pertenece al dueño" y los mensajes del
        # turno se perdían. La sesión corta se confirma a sí misma y muere; la
        # transacción del turno queda intacta y sus writes se confirman al
        # final como siempre.
        content = json.dumps(
            {
                "text": mensaje,
                "sender_id": worker_id,
                "sender_name": worker_name,
                "kind": "aviso",
            },
            ensure_ascii=False,
        )
        from edecan_db.session import get_session as abrir_sesion

        async with abrir_sesion(None) as sesion_aviso:
            await sesion_aviso.execute(
                text(
                    "INSERT INTO messages (id, tenant_id, conversation_id, role, content, created_at) "
                    "VALUES (gen_random_uuid(), :tenant_id, :cid, 'assistant', :content ::jsonb, "
                    "clock_timestamp())"
                    # created_at EXPLÍCITO con clock_timestamp(): `now()` es fija
                    # por transacción y DOS avisos del mismo turno empatarían —
                    # el ORDER BY del historial los mostraba invertidos.
                ),
                {
                    "tenant_id": str(ctx.tenant_id),
                    "cid": conversation_id,
                    "content": content,
                },
            )
        # Push personalizado cuando el dueño NO está en el chat (presencia SSE
        # suprime la entrega in-app). Paridad con agent_bot_message de turnos.
        await _encolar_push_avance_proactivo(ctx, chat, mensaje=mensaje)
        return ToolResult(content=mensaje)
