"""Apps personales de la Mac enlazada.

El toolkit no abre bases de datos de macOS desde el servidor. En su lugar
delega acciones estructuradas al companion local, que aplica los permisos TCC
de la propia persona y devuelve solo el resultado solicitado. Esto conserva
el modelo Mac-first y evita subir historiales completos a la nube.
"""

from __future__ import annotations

from typing import Any

from edecan_core import Tool, ToolContext, ToolResult

_SIN_MAC = (
    "No tengo una Mac enlazada disponible ahora. Abre Edecán en tu computadora "
    "y vuelve a intentarlo."
)


async def invocar_app_personal(
    ctx: ToolContext,
    accion: str,
    parametros: dict[str, Any],
) -> dict[str, Any] | None:
    """Invoca una acción local y normaliza su envoltorio.

    ``None`` significa que no existe companion. Los errores del companion se
    preservan como ``{"ok": False, "error": ...}`` para que cada tool pueda
    explicarlos sin convertirlos en una excepción interna del agente.
    """

    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    companion = extras.get("companion")
    if not callable(companion):
        return None
    resultado = await companion(accion, parametros)
    return (
        resultado
        if isinstance(resultado, dict)
        else {
            "ok": False,
            "error": "La Mac no devolvió una respuesta válida.",
        }
    )


def resultado_app_personal(
    respuesta: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, ToolResult | None]:
    """Extrae ``result`` o crea un ``ToolResult`` legible para el modelo."""

    if respuesta is None:
        return None, ToolResult(content=_SIN_MAC)
    if respuesta.get("ok") is not True:
        error = str(respuesta.get("error") or "La Mac no confirmó la operación.")
        return None, ToolResult(content=f"No pude completar la operación en tu Mac: {error}")
    payload = respuesta.get("result")
    if not isinstance(payload, dict):
        return None, ToolResult(content="La Mac respondió, pero el resultado no era válido.")
    return payload, None


class LeerMensajesPersonalesTool(Tool):
    name = "leer_mensajes_personales"
    description = (
        "Lee los mensajes iMessage/SMS recientes desde la app Mensajes de la Mac enlazada. "
        "Úsala solo cuando la persona pida revisar sus mensajes personales."
    )
    category = "read"
    risk_level = "none"
    requires_flags = frozenset({"companion"})
    input_schema = {
        "type": "object",
        "properties": {
            "limite": {
                "type": "integer",
                "description": "Máximo de mensajes recientes a devolver (1-25).",
                "default": 10,
            },
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        respuesta = await invocar_app_personal(
            ctx,
            "mac_messages_recent",
            {"limit": args.get("limite", 10)},
        )
        payload, error = resultado_app_personal(respuesta)
        if error is not None:
            return error
        assert payload is not None
        mensajes = payload.get("messages")
        if not isinstance(mensajes, list) or not mensajes:
            return ToolResult(content="No encontré mensajes recientes.", data={"mensajes": []})

        lineas: list[str] = []
        salida: list[dict[str, Any]] = []
        for item in mensajes:
            if not isinstance(item, dict):
                continue
            salida.append(dict(item))
            index = len(salida)
            direccion = "Tú" if item.get("from_me") else str(item.get("handle") or "Contacto")
            texto = str(item.get("text") or "").strip()
            fecha = str(item.get("sent_at") or "").strip()
            lineas.append(f"{index}. {direccion}{f' · {fecha}' if fecha else ''}: {texto}")
        if not salida:
            return ToolResult(content="No encontré mensajes recientes.", data={"mensajes": []})
        return ToolResult(content="\n".join(lineas), data={"mensajes": salida})


class EnviarMensajePersonalTool(Tool):
    name = "enviar_mensaje_personal"
    description = (
        "Envía un iMessage o SMS real mediante la app Mensajes de la Mac enlazada. "
        "Requiere confirmación explícita antes de contactar a otra persona."
    )
    category = "external_comm"
    risk_level = "high"
    requires_flags = frozenset({"companion"})
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "destinatario": {
                "type": "string",
                "description": "Teléfono o Apple ID exacto del destinatario.",
            },
            "mensaje": {"type": "string", "description": "Texto que se enviará."},
        },
        "required": ["destinatario", "mensaje"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        destinatario = str(args.get("destinatario") or "").strip()
        mensaje = str(args.get("mensaje") or "").strip()
        if not destinatario or not mensaje:
            return ToolResult(content="Necesito el destinatario exacto y el mensaje.")
        respuesta = await invocar_app_personal(
            ctx,
            "mac_messages_send",
            {"to": destinatario, "message": mensaje},
        )
        payload, error = resultado_app_personal(respuesta)
        if error is not None:
            return error
        assert payload is not None
        if payload.get("sent") is not True:
            return ToolResult(content="Mensajes no confirmó que el mensaje se haya enviado.")
        return ToolResult(
            content=f"Mensaje enviado a {destinatario}.",
            data={
                "destinatario": destinatario,
                "transport": payload.get("transport") or "messages",
            },
        )
