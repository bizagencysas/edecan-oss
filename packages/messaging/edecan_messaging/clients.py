"""Clientes HTTP puros de Telegram, Discord y Slack (`ARCHITECTURE.md` §10.8;
ROADMAP_V2.md §7.7, WP-V2-05).

Cada cliente recibe la credencial del tenant YA RESUELTA (ver `_creds.py`) y
habla directo con la API oficial de la plataforma vía `httpx` — nunca lee
credenciales de variables de entorno ni las persiste. Ningún mensaje de error
incluye el token en claro: siempre se pasa por `edecan_core.safety.redact`
antes de convertirse en `MessagingClientError` (última red de seguridad,
igual que el resto del repo — `ARCHITECTURE.md` §0.1), aunque en la práctica
los cuerpos de error de estas tres APIs normalmente no citan el token.
"""

from __future__ import annotations

from typing import Any

import httpx
from edecan_core.safety import redact

DEFAULT_TIMEOUT = httpx.Timeout(15.0)
MAX_READ_LIMIT = 20


class MessagingClientError(RuntimeError):
    """Error al hablar con la API oficial de una plataforma de mensajería."""


def clamp_limit(limit: int | None, *, default: int = MAX_READ_LIMIT) -> int:
    """Acota `limit` a `[1, MAX_READ_LIMIT]`; `None`/inválido cae a `default`."""
    if limit is None:
        return default
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_READ_LIMIT))


async def _do_request(
    http: httpx.AsyncClient | None,
    method: str,
    url: str,
    *,
    plataforma: str,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """`httpx` puro compartido por los tres clientes: crea (y cierra) su
    propio `AsyncClient` si no se inyecta uno (tests con `respx`/`MockTransport`),
    y traduce cualquier error de red a `MessagingClientError` con el texto
    redactado."""
    client = http
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        return await client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise MessagingClientError(
            f"No se pudo contactar la API de {plataforma}: {redact(str(exc))}"
        ) from exc
    finally:
        if owns_client:
            await client.aclose()


class TelegramClient:
    """Cliente del Bot API de Telegram para EL bot propio del tenant.

    `token` es el token del bot que el propio tenant creó con @BotFather (ver
    `docs/mensajeria.md`) — nunca un token compartido de la plataforma.
    """

    PLATAFORMA = "Telegram"

    def __init__(self, token: str, *, http: httpx.AsyncClient | None = None) -> None:
        if not token or not token.strip():
            raise MessagingClientError("Falta el token del bot de Telegram.")
        self._token = token.strip()
        self._http = http
        self._base_url = f"https://api.telegram.org/bot{self._token}"

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await _do_request(
            self._http, method, f"{self._base_url}/{path}", plataforma=self.PLATAFORMA, **kwargs
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MessagingClientError(
                f"Respuesta no-JSON de Telegram (HTTP {response.status_code})."
            ) from exc

        if response.status_code >= 400 or not payload.get("ok", False):
            detail = payload.get("description") or response.text
            raise MessagingClientError(f"Error de la API de Telegram: {redact(str(detail))}")
        return payload

    async def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        """`POST /sendMessage` — envía `text` al chat `chat_id`."""
        payload = await self._call("POST", "sendMessage", json={"chat_id": chat_id, "text": text})
        return payload.get("result", {})

    async def get_updates(self, limit: int = MAX_READ_LIMIT) -> list[dict[str, Any]]:
        """`GET /getUpdates` — últimos eventos pendientes del bot.

        `timeout=0` desactiva el long-polling de Telegram: esta tool debe
        responder de inmediato, no bloquear esperando updates nuevos.
        """
        payload = await self._call(
            "GET", "getUpdates", params={"limit": clamp_limit(limit), "timeout": 0}
        )
        return payload.get("result", [])


class DiscordClient:
    """Cliente de la API REST v10 de Discord para EL bot propio del tenant.

    `bot_token` es el token del bot que el propio tenant creó en el Discord
    Developer Portal (ver `docs/mensajeria.md`).
    """

    PLATAFORMA = "Discord"
    BASE_URL = "https://discord.com/api/v10"

    def __init__(self, bot_token: str, *, http: httpx.AsyncClient | None = None) -> None:
        if not bot_token or not bot_token.strip():
            raise MessagingClientError("Falta el token del bot de Discord.")
        self._bot_token = bot_token.strip()
        self._http = http

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bot {self._bot_token}"}

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await _do_request(
            self._http,
            method,
            f"{self.BASE_URL}{path}",
            plataforma=self.PLATAFORMA,
            headers=self._headers(),
            **kwargs,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("message", response.text)
            except ValueError:
                detail = response.text
            raise MessagingClientError(f"Error de la API de Discord: {redact(str(detail))}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    async def send(self, channel_id: str, text: str) -> dict[str, Any]:
        """`POST /channels/{channel_id}/messages` — envía `text` al canal."""
        resultado = await self._call(
            "POST", f"/channels/{channel_id}/messages", json={"content": text}
        )
        return resultado or {}

    async def list(self, channel_id: str, limit: int = MAX_READ_LIMIT) -> list[dict[str, Any]]:
        """`GET /channels/{channel_id}/messages?limit=` — últimos mensajes del canal."""
        resultado = await self._call(
            "GET", f"/channels/{channel_id}/messages", params={"limit": clamp_limit(limit)}
        )
        return resultado or []


class SlackClient:
    """Cliente de la Web API de Slack para la app/bot ya autorizada del tenant.

    `access_token` es el token de bot (`xoxb-...`) obtenido vía OAuth v2
    (`edecan_connectors.messaging.slack.SlackConnector`), resuelto por
    `_creds.py` desde el `TokenVault` del tenant.
    """

    PLATAFORMA = "Slack"
    BASE_URL = "https://slack.com/api"

    def __init__(self, access_token: str, *, http: httpx.AsyncClient | None = None) -> None:
        if not access_token or not access_token.strip():
            raise MessagingClientError("Falta el access token de Slack.")
        self._access_token = access_token.strip()
        self._http = http

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await _do_request(
            self._http,
            method,
            f"{self.BASE_URL}/{path}",
            plataforma=self.PLATAFORMA,
            headers=self._headers(),
            **kwargs,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MessagingClientError(
                f"Respuesta no-JSON de Slack (HTTP {response.status_code})."
            ) from exc

        # Slack casi siempre responde HTTP 200 incluso en error: señala
        # fallas con `{"ok": false, "error": "<código>"}` en el cuerpo (mismo
        # comportamiento que `edecan_connectors.messaging.slack`).
        if response.status_code >= 400 or not payload.get("ok", False):
            detail = payload.get("error") or response.text
            raise MessagingClientError(f"Error de la API de Slack: {redact(str(detail))}")
        return payload

    async def chat_post_message(self, channel: str, text: str) -> dict[str, Any]:
        """`POST chat.postMessage` — envía `text` al canal (id o `#nombre`)."""
        return await self._call("POST", "chat.postMessage", json={"channel": channel, "text": text})

    async def conversations_history(
        self, channel: str, limit: int = MAX_READ_LIMIT
    ) -> list[dict[str, Any]]:
        """`GET conversations.history` — últimos mensajes del canal."""
        payload = await self._call(
            "GET", "conversations.history", params={"channel": channel, "limit": clamp_limit(limit)}
        )
        return payload.get("messages", [])
