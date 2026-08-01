"""Recordatorios (`ARCHITECTURE.md` §10.3, tabla `reminders`).

`crear_recordatorio` inserta una fila en `reminders` vía `ctx.session` con
`sqlalchemy.text()` contra el esquema pinned de §10.3 (no importa un ORM de
`edecan_db.models`: esa forma interna no está fijada por el contrato, mientras
que los nombres de tabla/columna sí lo están). El worker (`send_reminder_scan`,
§10.11) es quien de verdad los dispara cuando vencen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text

from ._util import clamp_int

_CANALES_VALIDOS = ("mobile", "web", "voice", "phone", "api")
_LIMITE_DEFECTO = 20
_LIMITE_MAXIMO = 100


def _parsear_fecha(valor: str) -> datetime:
    """Parsea una fecha-hora ISO 8601 (admite el sufijo `Z` como UTC)."""
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"'{valor}' no es una fecha-hora ISO 8601 válida "
            "(ej. '2025-01-01T10:00:00-05:00')."
        ) from exc


class CrearRecordatorioTool(Tool):
    name = "crear_recordatorio"
    description = (
        "Crea un recordatorio para el usuario que se enviará en la fecha y hora "
        "indicadas, opcionalmente recurrente."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "mensaje": {
                "type": "string",
                "description": "Texto del recordatorio, tal como se le mostrará al usuario.",
            },
            "due_at": {
                "type": "string",
                "description": (
                    "Fecha y hora ISO 8601 en que debe dispararse el recordatorio "
                    "(ej. '2025-01-01T10:00:00-05:00')."
                ),
            },
            "rrule": {
                "type": "string",
                "description": (
                    "Regla de recurrencia RFC 5545 opcional (ej. 'FREQ=DAILY'). "
                    "Se omite para un recordatorio de una sola vez."
                ),
            },
            "channel": {
                "type": "string",
                "enum": list(_CANALES_VALIDOS),
                "description": "Canal de entrega del recordatorio.",
                "default": "mobile",
            },
        },
        "required": ["mensaje", "due_at"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        mensaje = str(args.get("mensaje", "")).strip()
        if not mensaje:
            return ToolResult(content="El recordatorio necesita un mensaje no vacío.")

        if not args.get("due_at"):
            return ToolResult(content="Falta 'due_at': la fecha-hora ISO 8601 del recordatorio.")
        try:
            due_at = _parsear_fecha(args["due_at"])
        except ValueError as exc:
            return ToolResult(content=str(exc))

        rrule = args.get("rrule") or None
        channel = args.get("channel") or "mobile"
        if channel not in _CANALES_VALIDOS:
            channel = "web"

        resultado = await ctx.session.execute(
            text(
                "INSERT INTO reminders "
                "(tenant_id, user_id, due_at, rrule, message, channel, status) "
                "VALUES (:tenant_id, :user_id, :due_at, :rrule, :message, :channel, 'pending') "
                "RETURNING id"
            ),
            {
                "tenant_id": str(ctx.tenant_id),
                "user_id": str(ctx.user_id),
                "due_at": due_at,
                "rrule": rrule,
                "message": mensaje,
                "channel": channel,
            },
        )
        fila = resultado.mappings().first()
        recordatorio_id = fila["id"] if fila else None

        return ToolResult(
            content=f"Listo, te recordaré «{mensaje}» el {due_at.isoformat()}.",
            data={
                "id": str(recordatorio_id) if recordatorio_id is not None else None,
                "due_at": due_at.isoformat(),
                "channel": channel,
                "rrule": rrule,
            },
        )


class ListarRecordatoriosTool(Tool):
    name = "listar_recordatorios"
    description = (
        "Lista los recordatorios del usuario, ordenados por fecha (por defecto, solo pendientes)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "incluir_completados": {
                "type": "boolean",
                "description": "Si es true, incluye también recordatorios enviados o cancelados.",
                "default": False,
            },
            "limite": {
                "type": "integer",
                "description": "Máximo de recordatorios a devolver (1-100).",
                "default": _LIMITE_DEFECTO,
            },
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        incluir_completados = bool(args.get("incluir_completados", False))
        limite = clamp_int(
            args.get("limite"), default=_LIMITE_DEFECTO, minimo=1, maximo=_LIMITE_MAXIMO
        )
        filtro_status = "" if incluir_completados else "AND status = 'pending' "

        resultado = await ctx.session.execute(
            text(
                "SELECT id, due_at, rrule, message, channel, status FROM reminders "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id "
                f"{filtro_status}"
                "ORDER BY due_at ASC LIMIT :limite"
            ),
            {"tenant_id": str(ctx.tenant_id), "user_id": str(ctx.user_id), "limite": limite},
        )
        filas = resultado.mappings().all()

        if not filas:
            return ToolResult(
                content="No tienes recordatorios pendientes.", data={"recordatorios": []}
            )

        lineas: list[str] = []
        recordatorios: list[dict[str, Any]] = []
        for i, fila in enumerate(filas, start=1):
            due_at = fila["due_at"]
            due_txt = due_at.isoformat() if hasattr(due_at, "isoformat") else str(due_at)
            recurrente = f" (recurrente: {fila['rrule']})" if fila["rrule"] else ""
            lineas.append(f"{i}. [{fila['status']}] {due_txt} — {fila['message']}{recurrente}")
            recordatorios.append(
                {
                    "id": str(fila["id"]),
                    "due_at": due_txt,
                    "message": fila["message"],
                    "channel": fila["channel"],
                    "status": fila["status"],
                    "rrule": fila["rrule"],
                }
            )

        return ToolResult(content="\n".join(lineas), data={"recordatorios": recordatorios})
