"""Tests offline (respx) de `edecan_messaging.whatsapp`: `WhatsAppClient`,
`normalizar_to` (WP-V3-13). Sin red real — `ARCHITECTURE.md` §10.15."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from edecan_messaging.clients import MessagingClientError
from edecan_messaging.whatsapp import WhatsAppClient, normalizar_to

PHONE_NUMBER_ID = "109876543210987"
SEND_URL = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
TOKEN = "token-permanente-de-meta-bien-largo"


# ---------------------------------------------------------------------------
# normalizar_to
# ---------------------------------------------------------------------------


def test_normalizar_to_quita_el_mas_inicial():
    assert normalizar_to("+525512345678") == "525512345678"


def test_normalizar_to_deja_igual_si_no_trae_mas():
    assert normalizar_to("525512345678") == "525512345678"


def test_normalizar_to_recorta_espacios():
    assert normalizar_to("  +525512345678  ") == "525512345678"


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_whatsapp_client_exige_access_token():
    with pytest.raises(MessagingClientError):
        WhatsAppClient("", PHONE_NUMBER_ID)


def test_whatsapp_client_exige_phone_number_id():
    with pytest.raises(MessagingClientError):
        WhatsAppClient(TOKEN, "")


# ---------------------------------------------------------------------------
# enviar_texto
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_enviar_texto_ok():
    ruta = respx.post(SEND_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "messaging_product": "whatsapp",
                "contacts": [{"input": "525512345678", "wa_id": "525512345678"}],
                "messages": [{"id": "wamid.ABC123"}],
            },
        )
    )
    cliente = WhatsAppClient(TOKEN, PHONE_NUMBER_ID)
    resultado = await cliente.enviar_texto("+525512345678", "hola equipo")

    assert resultado["messages"][0]["id"] == "wamid.ABC123"
    enviado = ruta.calls.last.request
    assert enviado.headers["Authorization"] == f"Bearer {TOKEN}"
    body = json.loads(enviado.content)
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == "525512345678"  # normalizado sin '+'
    assert body["type"] == "text"
    assert body["text"]["body"] == "hola equipo"


# ---------------------------------------------------------------------------
# enviar_plantilla
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_enviar_plantilla_ok():
    ruta = respx.post(SEND_URL).mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.TPL1"}]})
    )
    cliente = WhatsAppClient(TOKEN, PHONE_NUMBER_ID)
    resultado = await cliente.enviar_plantilla(
        "525512345678",
        "confirmacion_pedido",
        "es_MX",
        componentes=[{"type": "body", "parameters": [{"type": "text", "text": "42"}]}],
    )

    assert resultado["messages"][0]["id"] == "wamid.TPL1"
    body = json.loads(ruta.calls.last.request.content)
    assert body["type"] == "template"
    assert body["template"]["name"] == "confirmacion_pedido"
    assert body["template"]["language"] == {"code": "es_MX"}
    assert body["template"]["components"] == [
        {"type": "body", "parameters": [{"type": "text", "text": "42"}]}
    ]


@pytest.mark.asyncio
@respx.mock
async def test_enviar_plantilla_sin_componentes_no_los_incluye():
    ruta = respx.post(SEND_URL).mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.TPL2"}]})
    )
    cliente = WhatsAppClient(TOKEN, PHONE_NUMBER_ID)
    await cliente.enviar_plantilla("525512345678", "bienvenida")

    body = json.loads(ruta.calls.last.request.content)
    assert body["template"]["language"] == {"code": "es"}  # default
    assert "components" not in body["template"]


# ---------------------------------------------------------------------------
# Errores de la Graph API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_enviar_texto_fuera_de_ventana_24h_da_mensaje_claro():
    respx.post(SEND_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "message": "(#131047) Message failed to send because more than 24 "
                    "hours have passed since the customer last replied",
                    "type": "OAuthException",
                    "code": 131047,
                }
            },
        )
    )
    cliente = WhatsAppClient(TOKEN, PHONE_NUMBER_ID)
    with pytest.raises(MessagingClientError, match="ventana de 24 horas"):
        await cliente.enviar_texto("525512345678", "hola")


@pytest.mark.asyncio
@respx.mock
async def test_enviar_texto_destinatario_no_disponible():
    respx.post(SEND_URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Recipient not available", "code": 131026}}
        )
    )
    cliente = WhatsAppClient(TOKEN, PHONE_NUMBER_ID)
    with pytest.raises(MessagingClientError, match="no está disponible"):
        await cliente.enviar_texto("525512345678", "hola")


@pytest.mark.asyncio
@respx.mock
async def test_enviar_texto_401_redacta_el_detalle():
    respx.post(SEND_URL).mock(
        return_value=httpx.Response(
            401,
            json={
                "error": {
                    "message": "Bearer abcdefgh12345678 no autorizado",
                    "type": "OAuthException",
                    "code": 190,
                }
            },
        )
    )
    cliente = WhatsAppClient(TOKEN, PHONE_NUMBER_ID)
    with pytest.raises(MessagingClientError) as exc_info:
        await cliente.enviar_texto("525512345678", "hola")
    mensaje = str(exc_info.value)
    assert "[REDACTED]" in mensaje
    assert "abcdefgh12345678" not in mensaje


@pytest.mark.asyncio
@respx.mock
async def test_respuesta_no_json_lanza_error_claro():
    respx.post(SEND_URL).mock(return_value=httpx.Response(200, text="no soy json"))
    cliente = WhatsAppClient(TOKEN, PHONE_NUMBER_ID)
    with pytest.raises(MessagingClientError, match="no-JSON"):
        await cliente.enviar_texto("525512345678", "hola")
