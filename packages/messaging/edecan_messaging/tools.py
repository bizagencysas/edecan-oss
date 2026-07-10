"""Herramientas de mensajería del agente (`ARCHITECTURE.md` §10.7;
ROADMAP_V2.md §7.7, WP-V2-05; WP-V3-13 agrega WhatsApp): `enviar_mensaje` y
`leer_mensajes` para Telegram, Discord, Slack y WhatsApp Business Platform.

Entry point `edecan.tools` → `edecan_messaging:get_all_tools` (ver
`pyproject.toml`), que `edecan_core.ToolRegistry.load_entry_points(group=
"edecan.tools")` descubre automáticamente.

WhatsApp (WP-V3-13, `ARCHITECTURE.md` §12.b, `docs/mensajeria.md`) solo
soporta ENVÍO en v3: `leer_mensajes` con `plataforma="whatsapp"` devuelve un
`ToolResult` explicando la limitación (leer requiere webhooks entrantes con
URL pública, que este work package no monta) en vez de intentarlo. El envío
de WhatsApp acepta una `plantilla` opcional (nombre de una plantilla ya
aprobada por Meta) para los casos en que la conversación está fuera de la
ventana de 24h — ver `whatsapp.py`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from edecan_schemas.plans import FLAG_CONNECTORS_MESSAGING
from sqlalchemy import text

from ._creds import (
    CONNECTOR_KEYS,
    CredencialMensajeria,
    MessagingNotConnectedError,
    display_name,
    resolver_credenciales,
)
from .clients import (
    DiscordClient,
    MessagingClientError,
    SlackClient,
    TelegramClient,
    clamp_limit,
)
from .whatsapp import WhatsAppClient

logger = logging.getLogger(__name__)

_PLATAFORMAS = CONNECTOR_KEYS  # ("telegram", "discord", "slack", "whatsapp")
_VISTA_PREVIA_MAX = 160
_WHATSAPP_LEER_NO_SOPORTADO = (
    "Leer WhatsApp requiere webhooks entrantes con URL pública; en v3 Edecán solo envía."
)


def _vista_previa(texto: str, *, maximo: int = _VISTA_PREVIA_MAX) -> str:
    texto = texto.strip()
    if len(texto) <= maximo:
        return texto
    return texto[: maximo - 1].rstrip() + "…"


def _plataforma_no_soportada(plataforma: str) -> ToolResult:
    return ToolResult(
        content=(
            f"'{plataforma}' no es una plataforma de mensajería soportada. Usa una de: "
            f"{', '.join(_PLATAFORMAS)}."
        )
    )


async def _registrar_envio(
    ctx: ToolContext, *, plataforma: str, destino: str, preview: str
) -> None:
    """Registra `audit_log` + `usage_events(kind="messages")` de un envío
    real, mismo patrón que `edecan_premium.telephony.TwilioTenantClient
    ._audit_and_meter` (INSERT directo por `ctx.session`, sin importar
    `edecan_db`)."""
    await ctx.session.execute(
        text(
            """
            INSERT INTO audit_log (tenant_id, actor_user_id, action, target, meta)
            VALUES
                (:tenant_id ::uuid, :actor_user_id ::uuid, :action, :target, CAST(:meta AS jsonb))
            """
        ),
        {
            "tenant_id": str(ctx.tenant_id),
            "actor_user_id": str(ctx.user_id),
            "action": "messaging.message.send",
            "target": f"{plataforma}:{destino}",
            "meta": json.dumps({"preview": preview}),
        },
    )
    await ctx.session.execute(
        text(
            """
            INSERT INTO usage_events (tenant_id, kind, quantity, meta)
            VALUES (:tenant_id ::uuid, 'messages', 1, CAST(:meta AS jsonb))
            """
        ),
        {"tenant_id": str(ctx.tenant_id), "meta": json.dumps({"plataforma": plataforma})},
    )


class EnviarMensajeTool(Tool):
    """Envía un mensaje real a un chat/canal de Telegram, Discord, Slack o WhatsApp."""

    name = "enviar_mensaje"
    description = (
        "Envía un mensaje de texto real a un chat/canal en Telegram, Discord, Slack o "
        "WhatsApp, usando la cuenta/bot/número que el tenant ya conectó. Requiere "
        "confirmación: el mensaje llega de verdad y es visible para sus destinatarios. Para "
        "WhatsApp, fuera de la ventana de 24h desde el último mensaje del destinatario, usa "
        "'plantilla' (nombre de una plantilla ya aprobada por Meta) en vez de texto libre."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "plataforma": {
                "type": "string",
                "enum": list(_PLATAFORMAS),
                "description": "Dónde enviar el mensaje.",
            },
            "destino": {
                "type": "string",
                "description": (
                    "chat_id (Telegram), id de canal (Discord), canal (Slack, id o #nombre) "
                    "o número en formato E.164 (WhatsApp, con o sin '+')."
                ),
            },
            "texto": {"type": "string", "description": "Texto del mensaje a enviar."},
            "plantilla": {
                "type": "string",
                "description": (
                    "Solo WhatsApp: nombre de una plantilla ya aprobada por Meta en el "
                    "Business Manager del tenant. Obligatoria fuera de la ventana de 24h "
                    "desde el último mensaje del destinatario."
                ),
            },
            "idioma": {
                "type": "string",
                "description": (
                    "Solo WhatsApp: código de idioma de la plantilla (p. ej. 'es', 'es_MX'). "
                    "Por defecto 'es'."
                ),
                "default": "es",
            },
        },
        "required": ["plataforma", "destino", "texto"],
    }
    requires_flags = frozenset({FLAG_CONNECTORS_MESSAGING})
    dangerous = True

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        plataforma = str(args.get("plataforma", "")).strip().lower()
        destino = str(args.get("destino", "")).strip()
        texto = str(args.get("texto", "")).strip()
        plantilla = str(args.get("plantilla") or "").strip() or None
        idioma = str(args.get("idioma") or "").strip() or "es"

        if plataforma not in _PLATAFORMAS:
            return _plataforma_no_soportada(plataforma)
        if not destino:
            return ToolResult(content="Falta 'destino' (chat/canal) para enviar el mensaje.")
        if not texto:
            return ToolResult(content="Falta 'texto': no hay nada que enviar.")

        try:
            credencial = await resolver_credenciales(ctx, plataforma)
        except MessagingNotConnectedError as exc:
            return ToolResult(content=str(exc))

        try:
            resultado = await _enviar(
                plataforma, credencial, destino, texto, plantilla=plantilla, idioma=idioma
            )
        except MessagingClientError as exc:
            logger.warning("Fallo al enviar mensaje por %s a %s: %s", plataforma, destino, exc)
            return ToolResult(content=f"No se pudo enviar el mensaje por {plataforma}: {exc}")

        preview = _vista_previa(texto)
        await _registrar_envio(ctx, plataforma=plataforma, destino=destino, preview=preview)

        return ToolResult(
            content=f"Mensaje enviado por {plataforma} a {destino}: «{preview}».",
            data={"plataforma": plataforma, "destino": destino, "resultado": resultado},
        )


def _whatsapp_phone_number_id(credencial: CredencialMensajeria) -> str:
    """`credencial.scopes[0]` (ver docstring de `_creds.py`) — defensivo: en
    la práctica siempre está poblado, porque `connect_whatsapp`
    (`edecan_api.routers.connectors`) siempre guarda `scopes=[phone_number_id]`."""
    if not credencial.scopes:
        raise MessagingClientError(
            "La cuenta de WhatsApp conectada no tiene un phone_number_id guardado; "
            "reconéctala en /app/conectores."
        )
    return credencial.scopes[0]


async def _enviar(
    plataforma: str,
    credencial: CredencialMensajeria,
    destino: str,
    texto: str,
    *,
    plantilla: str | None = None,
    idioma: str = "es",
) -> dict[str, Any]:
    if plataforma == "telegram":
        return await TelegramClient(credencial.access_token).send_message(destino, texto)
    if plataforma == "discord":
        return await DiscordClient(credencial.access_token).send(destino, texto)
    if plataforma == "whatsapp":
        cliente = WhatsAppClient(credencial.access_token, _whatsapp_phone_number_id(credencial))
        if plantilla:
            return await cliente.enviar_plantilla(destino, plantilla, idioma)
        return await cliente.enviar_texto(destino, texto)
    return await SlackClient(credencial.access_token).chat_post_message(destino, texto)


async def _leer(
    plataforma: str, credencial: CredencialMensajeria, origen: str, limite: int
) -> list[dict[str, Any]]:
    if plataforma == "telegram":
        return await TelegramClient(credencial.access_token).get_updates(limite)
    if plataforma == "discord":
        return await DiscordClient(credencial.access_token).list(origen, limite)
    return await SlackClient(credencial.access_token).conversations_history(origen, limite)


def _linea_telegram(update: dict[str, Any]) -> str:
    mensaje = update.get("message") or {}
    remitente = (mensaje.get("from") or {}).get("first_name", "desconocido")
    return f"- {remitente}: {_vista_previa(str(mensaje.get('text', '')))}"


def _linea_discord(mensaje: dict[str, Any]) -> str:
    autor = (mensaje.get("author") or {}).get("username", "desconocido")
    return f"- {autor}: {_vista_previa(str(mensaje.get('content', '')))}"


def _linea_slack(mensaje: dict[str, Any]) -> str:
    autor = mensaje.get("user") or mensaje.get("username") or "desconocido"
    return f"- {autor}: {_vista_previa(str(mensaje.get('text', '')))}"


_LINEA_POR_PLATAFORMA = {
    "telegram": _linea_telegram,
    "discord": _linea_discord,
    "slack": _linea_slack,
}


class LeerMensajesTool(Tool):
    """Lee los últimos mensajes de un chat/canal de Telegram, Discord o Slack.

    WhatsApp NO está soportado para lectura en v3 (ver docstring del módulo):
    `run` responde con `_WHATSAPP_LEER_NO_SOPORTADO` antes de intentar
    resolver credenciales."""

    name = "leer_mensajes"
    description = (
        "Lee los últimos mensajes de un chat/canal en Telegram, Discord o Slack, usando la "
        "cuenta/bot que el tenant ya conectó. Solo lectura: nunca envía nada. WhatsApp no está "
        "soportado para lectura en v3 (requiere webhooks entrantes con URL pública)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "plataforma": {
                "type": "string",
                "enum": list(_PLATAFORMAS),
                "description": "De dónde leer los mensajes.",
            },
            "origen": {
                "type": "string",
                "description": (
                    "chat_id (Telegram), id de canal (Discord) o canal (Slack). Opcional para "
                    "Telegram (sin 'origen' usa los últimos updates pendientes del bot). No "
                    "aplica a WhatsApp (no soportado para lectura, ver 'description')."
                ),
            },
            "limite": {
                "type": "integer",
                "description": "Máximo de mensajes a devolver (1-20).",
                "default": 20,
            },
        },
        "required": ["plataforma"],
    }
    requires_flags = frozenset({FLAG_CONNECTORS_MESSAGING})

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        plataforma = str(args.get("plataforma", "")).strip().lower()
        origen = str(args.get("origen") or "").strip()
        limite = clamp_limit(args.get("limite"))

        if plataforma not in _PLATAFORMAS:
            return _plataforma_no_soportada(plataforma)
        if plataforma == "whatsapp":
            return ToolResult(content=_WHATSAPP_LEER_NO_SOPORTADO)
        if plataforma != "telegram" and not origen:
            nombre = display_name(plataforma)
            return ToolResult(
                content=f"Falta 'origen' (chat/canal) para leer mensajes de {nombre}."
            )

        try:
            credencial = await resolver_credenciales(ctx, plataforma)
        except MessagingNotConnectedError as exc:
            return ToolResult(content=str(exc))

        try:
            mensajes = await _leer(plataforma, credencial, origen, limite)
        except MessagingClientError as exc:
            logger.warning("Fallo al leer mensajes de %s: %s", plataforma, exc)
            return ToolResult(content=f"No se pudo leer mensajes de {plataforma}: {exc}")

        if not mensajes:
            return ToolResult(
                content=f"No hay mensajes recientes en {display_name(plataforma)}.",
                data={"mensajes": []},
            )

        formatear = _LINEA_POR_PLATAFORMA[plataforma]
        lineas = [formatear(m) for m in mensajes]
        return ToolResult(content="\n".join(lineas), data={"mensajes": mensajes})


def get_all_tools() -> list[Tool]:
    """Entry point `edecan.tools` (ver `pyproject.toml` y `ToolRegistry.load_entry_points`)."""
    return [EnviarMensajeTool(), LeerMensajesTool()]
