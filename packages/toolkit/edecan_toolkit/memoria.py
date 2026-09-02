"""Tool `guardar_memoria`: el usuario pide recordar algo y queda en `memory_items`.

La consolidación automática (`memory_consolidate`) ya guarda recuerdos tras cada
turno, pero NO hay forma de que el usuario guarde uno EXPLÍCITO ("recuerda que
prefiero X"). Esta tool cierra ese hueco: escribe un `memory_item` directo con
`source="manual"`. Se inserta con `embedding` NULL (la consolidación/reindexación
lo embebe después; el ítem es legible por listing aunque no tenga vector).
"""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text

_KINDS_VALIDOS = ("fact", "preference", "event", "entity")
_SOURCE_MANUAL = "manual"


class GuardarMemoriaTool(Tool):
    name = "guardar_memoria"
    description = (
        "Guarda un recuerdo explícito del usuario en su memoria permanente, para "
        "recordarlo en conversaciones futuras. Úsalo cuando el usuario pida "
        "recordar algo ('recuerda que...', 'anota que...', 'guarda esto'). No lo "
        "uses para datos que ya están en el historial de la conversación."
    )
    category = "write"
    risk_level = "low"
    input_schema = {
        "type": "object",
        "properties": {
            "contenido": {
                "type": "string",
                "description": "El recuerdo, en una frase clara y en tercera persona o "
                "neutra (p. ej. 'Prefiere que le hablen de tú').",
            },
            "tipo": {
                "type": "string",
                "enum": list(_KINDS_VALIDOS),
                "description": "Tipo de recuerdo: fact (hecho), preference (preferencia), "
                "event (evento) o entity (entidad/persona).",
                "default": "fact",
            },
            "importancia": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Qué tan importante es recordarlo (0-1).",
                "default": 0.6,
            },
        },
        "required": ["contenido"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        contenido = str(args.get("contenido") or "").strip()
        if not contenido:
            return ToolResult(content="El recuerdo necesita un texto no vacío.")
        tipo = str(args.get("tipo") or "fact").strip().lower()
        if tipo not in _KINDS_VALIDOS:
            validos = ", ".join(_KINDS_VALIDOS)
            return ToolResult(
                content=f"Tipo de recuerdo inválido: {tipo}. Debe ser uno de {validos}."
            )
        try:
            importancia = float(args.get("importancia", 0.6))
        except (TypeError, ValueError):
            importancia = 0.6
        importancia = min(max(importancia, 0.0), 1.0)

        try:
            await ctx.session.execute(
                text(
                    "INSERT INTO memory_items ("
                    "id, tenant_id, user_id, kind, content, importance, confidence, source, "
                    "created_at, updated_at"
                    ") VALUES ("
                    ":id, :tenant_id, :user_id, :kind, :contenido, :importancia, :confidence, "
                    ":source, :now, :now"
                    ")"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": ctx.tenant_id,
                    "user_id": ctx.user_id,
                    "kind": tipo,
                    "contenido": contenido,
                    "importancia": importancia,
                    "confidence": 0.8,
                    "source": _SOURCE_MANUAL,
                    "now": _utcnow(),
                },
            )
        except Exception as exc:  # noqa: BLE001 - la tool nunca debe tumbar el turno
            return ToolResult(content=f"No pude guardar el recuerdo: {exc}")

        return ToolResult(
            content="Listo, lo recordaré.",
            data={"tipo": tipo, "importancia": importancia},
        )


def _utcnow() -> Any:
    from datetime import datetime

    return datetime.now(UTC)