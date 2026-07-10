"""Tests de `edecan_toolkit.correo`: `buscar_correo` y `enviar_correo`."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import respx
from edecan_toolkit.correo import BuscarCorreoTool, EnviarCorreoTool

GMAIL_SEARCH_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


async def test_buscar_correo_sin_cuenta_conectada_pide_conectar(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await BuscarCorreoTool().run(ctx, {"consulta": "facturas"})
    assert "/app/conectores" in resultado.content


@respx.mock
async def test_buscar_correo_via_google(make_ctx, make_session, make_vault):
    fila_cuenta = {"id": "acc-1", "connector_key": "google"}
    bundle = SimpleNamespace(access_token="tok-123")
    ctx = make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=bundle))

    respx.get(GMAIL_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "m1"}]})
    )
    respx.get(f"{GMAIL_SEARCH_URL}/m1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "t1",
                "snippet": "Aquí está tu factura...",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "facturacion@empresa.com"},
                        {"name": "Subject", "value": "Tu factura de julio"},
                    ]
                },
            },
        )
    )

    resultado = await BuscarCorreoTool().run(ctx, {"consulta": "factura"})

    assert "Tu factura de julio" in resultado.content
    assert resultado.data["mensajes"][0]["from"] == "facturacion@empresa.com"


async def test_enviar_correo_valida_direccion_antes_de_llamar_al_conector(
    make_ctx, make_session, make_vault
):
    fila_cuenta = {"id": "acc-1", "connector_key": "google"}
    bundle = SimpleNamespace(access_token="tok-123")
    ctx = make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=bundle))

    with respx.mock:
        ruta = respx.post(GMAIL_SEND_URL).mock(
            return_value=httpx.Response(200, json={"id": "no-debe-usarse"})
        )
        resultado = await EnviarCorreoTool().run(
            ctx, {"destinatario": "no-es-un-correo", "asunto": "hola", "cuerpo": "hola"}
        )
        assert not ruta.called

    assert "no parece una dirección de correo válida" in resultado.content


@respx.mock
async def test_enviar_correo_via_google_construye_mime_en_base64(
    make_ctx, make_session, make_vault
):
    fila_cuenta = {"id": "acc-1", "connector_key": "google"}
    bundle = SimpleNamespace(access_token="tok-123")
    ctx = make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=bundle))

    ruta = respx.post(GMAIL_SEND_URL).mock(return_value=httpx.Response(200, json={"id": "sent-1"}))

    resultado = await EnviarCorreoTool().run(
        ctx,
        {
            "destinatario": "cliente@ejemplo.com",
            "asunto": "Propuesta",
            "cuerpo": "Aquí va la propuesta.",
        },
    )

    assert ruta.called
    cuerpo_enviado = ruta.calls.last.request.content
    assert b"raw" in cuerpo_enviado
    assert "cliente@ejemplo.com" in resultado.content
    assert resultado.data["resultado"]["id"] == "sent-1"


def test_enviar_correo_es_dangerous():
    assert EnviarCorreoTool().dangerous is True
