"""Tests offline (respx) de `edecan_messaging.clients`: `TelegramClient`,
`DiscordClient`, `SlackClient`. Sin red real — `ARCHITECTURE.md` §10.15."""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_messaging.clients import (
    DiscordClient,
    MessagingClientError,
    SlackClient,
    TelegramClient,
    clamp_limit,
)

# ---------------------------------------------------------------------------
# clamp_limit
# ---------------------------------------------------------------------------


def test_clamp_limit_por_defecto_sin_valor():
    assert clamp_limit(None) == 20


def test_clamp_limit_acota_arriba_y_abajo():
    assert clamp_limit(500) == 20
    assert clamp_limit(0) == 1
    assert clamp_limit(-5) == 1
    assert clamp_limit(7) == 7


def test_clamp_limit_valor_invalido_cae_a_default():
    assert clamp_limit("no-es-un-numero") == 20  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TelegramClient
# ---------------------------------------------------------------------------


def test_telegram_client_exige_token():
    with pytest.raises(MessagingClientError):
        TelegramClient("")


@pytest.mark.asyncio
@respx.mock
async def test_telegram_send_message_ok():
    ruta = respx.post("https://api.telegram.org/bot123:ABC/sendMessage").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"message_id": 42, "text": "hola"}}
        )
    )
    cliente = TelegramClient("123:ABC")
    resultado = await cliente.send_message("chat-1", "hola")
    assert resultado["message_id"] == 42
    enviado = ruta.calls.last.request
    assert b"chat_id" in enviado.content


@pytest.mark.asyncio
@respx.mock
async def test_telegram_send_message_error_redacta_el_detalle():
    respx.post("https://api.telegram.org/bot123:ABC/sendMessage").mock(
        return_value=httpx.Response(
            400, json={"ok": False, "description": "Bearer abcdefgh12345678 no autorizado"}
        )
    )
    cliente = TelegramClient("123:ABC")
    with pytest.raises(MessagingClientError) as exc_info:
        await cliente.send_message("chat-1", "hola")
    mensaje = str(exc_info.value)
    assert "[REDACTED]" in mensaje
    assert "abcdefgh12345678" not in mensaje


@pytest.mark.asyncio
@respx.mock
async def test_telegram_get_updates_clampa_limite_y_desactiva_polling():
    ruta = respx.get("https://api.telegram.org/bot123:ABC/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": [{"update_id": 1}]})
    )
    cliente = TelegramClient("123:ABC")
    resultado = await cliente.get_updates(500)
    assert resultado == [{"update_id": 1}]
    params = ruta.calls.last.request.url.params
    assert params["limit"] == "20"
    assert params["timeout"] == "0"


@pytest.mark.asyncio
@respx.mock
async def test_telegram_respuesta_no_json_lanza_error_claro():
    respx.post("https://api.telegram.org/bot123:ABC/sendMessage").mock(
        return_value=httpx.Response(200, text="no soy json")
    )
    cliente = TelegramClient("123:ABC")
    with pytest.raises(MessagingClientError, match="no-JSON"):
        await cliente.send_message("chat-1", "hola")


# ---------------------------------------------------------------------------
# DiscordClient
# ---------------------------------------------------------------------------


def test_discord_client_exige_token():
    with pytest.raises(MessagingClientError):
        DiscordClient("")


@pytest.mark.asyncio
@respx.mock
async def test_discord_send_ok_usa_header_bot():
    ruta = respx.post("https://discord.com/api/v10/channels/chan-1/messages").mock(
        return_value=httpx.Response(200, json={"id": "msg-1", "content": "hola"})
    )
    cliente = DiscordClient("bot-token-1")
    resultado = await cliente.send("chan-1", "hola")
    assert resultado["id"] == "msg-1"
    enviado = ruta.calls.last.request
    assert enviado.headers["Authorization"] == "Bot bot-token-1"


@pytest.mark.asyncio
@respx.mock
async def test_discord_send_error_lanza_messaging_client_error():
    respx.post("https://discord.com/api/v10/channels/chan-1/messages").mock(
        return_value=httpx.Response(403, json={"message": "Missing Access"})
    )
    cliente = DiscordClient("bot-token-1")
    with pytest.raises(MessagingClientError, match="Missing Access"):
        await cliente.send("chan-1", "hola")


@pytest.mark.asyncio
@respx.mock
async def test_discord_list_clampa_limite():
    ruta = respx.get("https://discord.com/api/v10/channels/chan-1/messages").mock(
        return_value=httpx.Response(200, json=[{"id": "m1"}, {"id": "m2"}])
    )
    cliente = DiscordClient("bot-token-1")
    resultado = await cliente.list("chan-1", limit=999)
    assert len(resultado) == 2
    params = ruta.calls.last.request.url.params
    assert params["limit"] == "20"


# ---------------------------------------------------------------------------
# SlackClient
# ---------------------------------------------------------------------------


def test_slack_client_exige_token():
    with pytest.raises(MessagingClientError):
        SlackClient("")


@pytest.mark.asyncio
@respx.mock
async def test_slack_chat_post_message_ok():
    ruta = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "123.456", "channel": "C1"})
    )
    cliente = SlackClient("xoxb-1")
    resultado = await cliente.chat_post_message("C1", "hola")
    assert resultado["ts"] == "123.456"
    enviado = ruta.calls.last.request
    assert enviado.headers["Authorization"] == "Bearer xoxb-1"


@pytest.mark.asyncio
@respx.mock
async def test_slack_chat_post_message_ok_false_lanza_error_aunque_http_200():
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )
    cliente = SlackClient("xoxb-1")
    with pytest.raises(MessagingClientError, match="channel_not_found"):
        await cliente.chat_post_message("C-no-existe", "hola")


@pytest.mark.asyncio
@respx.mock
async def test_slack_conversations_history_ok():
    respx.get("https://slack.com/api/conversations.history").mock(
        return_value=httpx.Response(
            200,
            json={"ok": True, "messages": [{"user": "U1", "text": "hola equipo"}]},
        )
    )
    cliente = SlackClient("xoxb-1")
    mensajes = await cliente.conversations_history("C1")
    assert mensajes[0]["text"] == "hola equipo"
