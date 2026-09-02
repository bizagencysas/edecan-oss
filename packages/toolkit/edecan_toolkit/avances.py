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
from typing import Any

from edecan_core.tools.base import Tool, ToolContext, ToolResult
from sqlalchemy import text


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
        worker_name = chat.get("worker_name") or "Bot"
        if not conversation_id or not worker_id:
            return ToolResult(
                content="Este canal no tiene chat propio: los avisos de avance "
                "solo funcionan en el chat 1:1 de un bot."
            )

        # El aviso se persiste como mensaje real del bot (assistant): aparece
        # como burbuja en el chat, sobrevive recargas y viaja en el
        # result_preview de esta tool para mostrarse EN VIVO mientras el
        # turno sigue corriendo. Misma sesión/transacción del turno: se
        # confirma cuando el turno confirma.
        content = json.dumps(
            {
                "text": mensaje,
                "sender_id": worker_id,
                "sender_name": worker_name,
                "kind": "aviso",
            },
            ensure_ascii=False,
        )
        await ctx.session.execute(
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
        return ToolResult(content=mensaje)
