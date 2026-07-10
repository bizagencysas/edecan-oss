"""Agenda / calendario (`ARCHITECTURE.md` §10.8): usa la cuenta Google o
Microsoft que el tenant tenga conectada. Si no hay ninguna conectada, la tool
pide conectarla en `/app/conectores` en vez de fallar.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from edecan_connectors.google import gcal as google_gcal
from edecan_connectors.microsoft import graph as ms_graph
from edecan_core import Tool, ToolContext, ToolResult

from ._conectores import CuentaConectada, buscar_cuenta_conectada, resultado_falta_conexion

_CONECTORES_AGENDA = ("google", "microsoft")
_NOMBRE_AGENDA = "Google o Microsoft"
_TIMEOUT = 15.0


def _parse_o(valor: Any, default: datetime) -> datetime:
    if not valor:
        return default
    return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))


def _resumir_evento_google(evento: dict) -> dict[str, Any]:
    inicio = evento.get("start", {}).get("dateTime") or evento.get("start", {}).get("date", "")
    fin = evento.get("end", {}).get("dateTime") or evento.get("end", {}).get("date", "")
    return {
        "id": evento.get("id"),
        "titulo": evento.get("summary") or "(sin título)",
        "inicio": inicio,
        "fin": fin,
    }


def _resumir_evento_microsoft(evento: dict) -> dict[str, Any]:
    inicio = (evento.get("start") or {}).get("dateTime", "")
    fin = (evento.get("end") or {}).get("dateTime", "")
    return {
        "id": evento.get("id"),
        "titulo": evento.get("subject") or "(sin título)",
        "inicio": inicio,
        "fin": fin,
    }


async def _cuenta_y_bundle(
    ctx: ToolContext,
) -> tuple[CuentaConectada, Any] | ToolResult:
    """Resuelve la cuenta conectada de agenda + su `TokenBundle`, o el
    `ToolResult` que hay que devolver si falta alguno de los dos."""
    cuenta = await buscar_cuenta_conectada(ctx, _CONECTORES_AGENDA)
    if cuenta is None:
        return resultado_falta_conexion(_NOMBRE_AGENDA)
    bundle = await ctx.vault.get(ctx.tenant_id, cuenta.connector_account_id)
    if bundle is None:
        return resultado_falta_conexion(_NOMBRE_AGENDA)
    return cuenta, bundle


class AgendaEventosTool(Tool):
    name = "agenda_eventos"
    description = (
        "Lista los próximos eventos del calendario conectado del usuario "
        "(Google Calendar u Outlook Calendar)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "desde": {
                "type": "string",
                "description": "Inicio del rango, fecha-hora ISO 8601. Por defecto: ahora.",
            },
            "hasta": {
                "type": "string",
                "description": "Fin del rango, ISO 8601. Por defecto: 7 días después de 'desde'.",
            },
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        resuelto = await _cuenta_y_bundle(ctx)
        if isinstance(resuelto, ToolResult):
            return resuelto
        cuenta, bundle = resuelto

        desde = _parse_o(args.get("desde"), datetime.now(UTC))
        hasta = _parse_o(args.get("hasta"), desde + timedelta(days=7))

        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            if cuenta.connector_key == "google":
                eventos = await google_gcal.list_events(
                    http, bundle, desde.isoformat(), hasta.isoformat()
                )
                resumen = [_resumir_evento_google(e) for e in eventos]
            else:
                eventos = await ms_graph.list_events(
                    http, bundle, desde.isoformat(), hasta.isoformat()
                )
                resumen = [_resumir_evento_microsoft(e) for e in eventos]

        if not resumen:
            return ToolResult(
                content="No tienes eventos en ese rango de fechas.", data={"eventos": []}
            )

        lineas = [
            f"{i}. {e['titulo']} — {e['inicio']} a {e['fin']}"
            for i, e in enumerate(resumen, start=1)
        ]
        return ToolResult(content="\n".join(lineas), data={"eventos": resumen})


class CrearEventoTool(Tool):
    name = "crear_evento"
    description = "Crea un evento en el calendario conectado del usuario (Google u Outlook)."
    input_schema = {
        "type": "object",
        "properties": {
            "titulo": {"type": "string", "description": "Título del evento."},
            "inicio": {"type": "string", "description": "Inicio, fecha-hora ISO 8601."},
            "fin": {"type": "string", "description": "Fin, fecha-hora ISO 8601."},
            "descripcion": {"type": "string", "description": "Descripción o notas opcionales."},
        },
        "required": ["titulo", "inicio", "fin"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        resuelto = await _cuenta_y_bundle(ctx)
        if isinstance(resuelto, ToolResult):
            return resuelto
        cuenta, bundle = resuelto

        titulo = str(args.get("titulo", "")).strip()
        if not titulo:
            return ToolResult(content="El evento necesita un título.")
        if not args.get("inicio"):
            return ToolResult(
                content="Falta 'inicio': la fecha-hora ISO 8601 de inicio del evento."
            )
        if not args.get("fin"):
            return ToolResult(content="Falta 'fin': la fecha-hora ISO 8601 de fin del evento.")
        inicio = str(args["inicio"])
        fin = str(args["fin"])
        descripcion = args.get("descripcion") or None

        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            if cuenta.connector_key == "google":
                evento = await google_gcal.create_event(
                    http, bundle, titulo, inicio, fin, descripcion
                )
                link = evento.get("htmlLink")
            else:
                evento = await ms_graph.create_event(
                    http, bundle, titulo, inicio, fin, descripcion
                )
                link = evento.get("webLink")

        extra = f" ({link})" if link else ""
        return ToolResult(
            content=f"Evento «{titulo}» creado del {inicio} al {fin}{extra}.",
            data={"evento": evento},
        )
