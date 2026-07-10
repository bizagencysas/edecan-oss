"""Correo (`ARCHITECTURE.md` §10.8): Gmail u Outlook, según la cuenta que el
tenant tenga conectada.
"""

from __future__ import annotations

from typing import Any

import httpx
from edecan_connectors.google import gmail as google_gmail
from edecan_connectors.microsoft import graph as ms_graph
from edecan_core import Tool, ToolContext, ToolResult

from ._conectores import buscar_cuenta_conectada, resultado_falta_conexion
from ._util import clamp_int

_CONECTORES_CORREO = ("google", "microsoft")
_NOMBRE_CORREO = "Google o Microsoft"
_TIMEOUT = 15.0
_LIMITE_DEFECTO = 10
_LIMITE_MAXIMO = 10


class BuscarCorreoTool(Tool):
    name = "buscar_correo"
    description = "Busca correos en la cuenta conectada del usuario (Gmail u Outlook)."
    input_schema = {
        "type": "object",
        "properties": {
            "consulta": {
                "type": "string",
                "description": (
                    "Texto o sintaxis de búsqueda del proveedor "
                    "(ej. 'from:jefe@empresa.com is:unread')."
                ),
            },
            "limite": {
                "type": "integer",
                "description": "Máximo de correos a devolver (1-10).",
                "default": _LIMITE_DEFECTO,
            },
        },
        "required": ["consulta"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        cuenta = await buscar_cuenta_conectada(ctx, _CONECTORES_CORREO)
        if cuenta is None:
            return resultado_falta_conexion(_NOMBRE_CORREO)
        bundle = await ctx.vault.get(ctx.tenant_id, cuenta.connector_account_id)
        if bundle is None:
            return resultado_falta_conexion(_NOMBRE_CORREO)

        consulta = str(args.get("consulta", "")).strip()
        if not consulta:
            return ToolResult(content="Dime qué quieres buscar en tu correo.")
        limite = clamp_int(
            args.get("limite"), default=_LIMITE_DEFECTO, minimo=1, maximo=_LIMITE_MAXIMO
        )

        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            if cuenta.connector_key == "google":
                mensajes = await google_gmail.search_messages(http, bundle, consulta, limite)
            else:
                mensajes = await ms_graph.search_mail(http, bundle, consulta, limite)

        if not mensajes:
            return ToolResult(
                content=f"No encontré correos para «{consulta}».", data={"mensajes": []}
            )

        lineas = [
            f"{i}. De: {m.get('from') or '(desconocido)'} — "
            f"{m.get('subject') or '(sin asunto)'}\n   {m.get('snippet', '')}"
            for i, m in enumerate(mensajes, start=1)
        ]
        return ToolResult(content="\n".join(lineas), data={"mensajes": mensajes})


class EnviarCorreoTool(Tool):
    name = "enviar_correo"
    description = (
        "Envía un correo desde la cuenta conectada del usuario (Gmail u Outlook). "
        "Requiere confirmación: envía un mensaje real a un tercero."
    )
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "destinatario": {
                "type": "string",
                "description": "Dirección de correo del destinatario.",
            },
            "asunto": {"type": "string", "description": "Asunto del correo."},
            "cuerpo": {"type": "string", "description": "Cuerpo del correo en texto plano."},
        },
        "required": ["destinatario", "asunto", "cuerpo"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        cuenta = await buscar_cuenta_conectada(ctx, _CONECTORES_CORREO)
        if cuenta is None:
            return resultado_falta_conexion(_NOMBRE_CORREO)
        bundle = await ctx.vault.get(ctx.tenant_id, cuenta.connector_account_id)
        if bundle is None:
            return resultado_falta_conexion(_NOMBRE_CORREO)

        destinatario = str(args.get("destinatario", "")).strip()
        asunto = str(args.get("asunto", ""))
        cuerpo = str(args.get("cuerpo", ""))
        if "@" not in destinatario:
            return ToolResult(
                content=f"'{destinatario}' no parece una dirección de correo válida."
            )

        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            if cuenta.connector_key == "google":
                # `gmail.send_message` redacta el MIME (RFC 2822) y lo manda en
                # base64url dentro de `raw`, como exige la Gmail API.
                resultado = await google_gmail.send_message(
                    http, bundle, destinatario, asunto, cuerpo
                )
            else:
                await ms_graph.send_mail(http, bundle, destinatario, asunto, cuerpo)
                resultado = {"status": "enviado"}

        return ToolResult(
            content=f"Correo enviado a {destinatario} con asunto «{asunto}».",
            data={"destinatario": destinatario, "asunto": asunto, "resultado": resultado},
        )
