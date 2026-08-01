"""Tests de `edecan_messaging.tools`: `EnviarMensajeTool`, `LeerMensajesTool`,
`get_all_tools` (`ARCHITECTURE.md` §10.15). Sin red real (`respx`), sin
importar `edecan_db`/`edecan_connectors` — fakes locales de `conftest.py`."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import respx
from edecan_messaging.tools import (
    FLAG_CONNECTORS_MESSAGING,
    EnviarMensajeTool,
    LeerMensajesTool,
    get_all_tools,
)

TELEGRAM_SEND_URL = "https://api.telegram.org/bot123:ABC/sendMessage"
TELEGRAM_UPDATES_URL = "https://api.telegram.org/bot123:ABC/getUpdates"
DISCORD_SEND_URL = "https://discord.com/api/v10/channels/chan-1/messages"
SLACK_SEND_URL = "https://slack.com/api/chat.postMessage"
SLACK_HISTORY_URL = "https://slack.com/api/conversations.history"
WHATSAPP_PHONE_NUMBER_ID = "109876543210987"
WHATSAPP_SEND_URL = f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"


def _fila_cuenta(cuenta_id: str = "acc-1") -> dict:
    return {"id": cuenta_id}


def _vault_con(make_vault, token: str):
    return make_vault(bundle=SimpleNamespace(access_token=token))


def _vault_con_whatsapp(make_vault, token: str, phone_number_id: str = WHATSAPP_PHONE_NUMBER_ID):
    return make_vault(bundle=SimpleNamespace(access_token=token, scopes=[phone_number_id]))


# ---------------------------------------------------------------------------
# Metadatos de las tools
# ---------------------------------------------------------------------------


def test_enviar_mensaje_es_dangerous_y_requiere_el_flag():
    tool = EnviarMensajeTool()
    assert tool.dangerous is True
    assert tool.requires_flags == frozenset({FLAG_CONNECTORS_MESSAGING})
    assert tool.name == "enviar_mensaje"


def test_leer_mensajes_no_es_dangerous_pero_requiere_el_flag():
    tool = LeerMensajesTool()
    assert tool.dangerous is False
    assert tool.requires_flags == frozenset({FLAG_CONNECTORS_MESSAGING})
    assert tool.name == "leer_mensajes"


def test_get_all_tools_devuelve_las_dos_tools():
    tools = get_all_tools()
    nombres = {t.name for t in tools}
    assert nombres == {"enviar_mensaje", "leer_mensajes"}


# ---------------------------------------------------------------------------
# EnviarMensajeTool — validación de argumentos
# ---------------------------------------------------------------------------


async def test_enviar_mensaje_plataforma_no_soportada(make_ctx):
    # "signal" (no "whatsapp"): Signal está excluida permanentemente por no
    # tener API oficial (`docs/mensajeria.md`), así que sigue siendo un buen
    # ejemplo de plataforma no soportada — a diferencia de "whatsapp", que
    # WP-V3-13 agregó como cuarta plataforma soportada (ver tests más abajo).
    ctx = make_ctx()
    resultado = await EnviarMensajeTool().run(
        ctx, {"plataforma": "signal", "destino": "x", "texto": "hola"}
    )
    assert "no es una plataforma de mensajería soportada" in resultado.content


async def test_enviar_mensaje_sin_destino(make_ctx):
    ctx = make_ctx()
    resultado = await EnviarMensajeTool().run(
        ctx, {"plataforma": "telegram", "destino": "", "texto": "hola"}
    )
    assert "destino" in resultado.content


async def test_enviar_mensaje_sin_texto(make_ctx):
    ctx = make_ctx()
    resultado = await EnviarMensajeTool().run(
        ctx, {"plataforma": "telegram", "destino": "chat-1", "texto": "  "}
    )
    assert "texto" in resultado.content


async def test_enviar_mensaje_sin_cuenta_conectada(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await EnviarMensajeTool().run(
        ctx, {"plataforma": "telegram", "destino": "chat-1", "texto": "hola"}
    )
    assert "Telegram" in resultado.content
    assert "conectores" in resultado.content


# ---------------------------------------------------------------------------
# EnviarMensajeTool — envío real (por plataforma) + auditoría/uso
# ---------------------------------------------------------------------------


@respx.mock
async def test_enviar_mensaje_por_telegram_ok(make_ctx, make_session, make_vault):
    respx.post(TELEGRAM_SEND_URL).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )
    session = make_session([[_fila_cuenta()]])
    ctx = make_ctx(session=session, vault=_vault_con(make_vault, "123:ABC"))

    resultado = await EnviarMensajeTool().run(
        ctx, {"plataforma": "telegram", "destino": "chat-1", "texto": "Hola equipo"}
    )

    assert "Mensaje enviado por telegram a chat-1" in resultado.content
    assert "Hola equipo" in resultado.content
    assert resultado.data["resultado"]["message_id"] == 1

    # audit_log + usage_events insertados (mismo patrón que
    # edecan_premium.telephony._audit_and_meter).
    sentencias = [sql for sql, _ in session.llamadas]
    assert any("audit_log" in s for s in sentencias)
    assert any("usage_events" in s for s in sentencias)
    usage_params = next(p for sql, p in session.llamadas if "usage_events" in sql)
    assert usage_params["tenant_id"] == str(ctx.tenant_id)


@respx.mock
async def test_enviar_mensaje_por_discord_ok(make_ctx, make_session, make_vault):
    respx.post(DISCORD_SEND_URL).mock(
        return_value=httpx.Response(200, json={"id": "msg-1", "content": "hola"})
    )
    session = make_session([[_fila_cuenta()]])
    ctx = make_ctx(session=session, vault=_vault_con(make_vault, "bot-token"))

    resultado = await EnviarMensajeTool().run(
        ctx, {"plataforma": "discord", "destino": "chan-1", "texto": "hola"}
    )

    assert "Mensaje enviado por discord a chan-1" in resultado.content
    assert resultado.data["resultado"]["id"] == "msg-1"


@respx.mock
async def test_enviar_mensaje_por_slack_ok(make_ctx, make_session, make_vault):
    respx.post(SLACK_SEND_URL).mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1.1", "channel": "C1"})
    )
    session = make_session([[_fila_cuenta()]])
    ctx = make_ctx(session=session, vault=_vault_con(make_vault, "xoxb-1"))

    resultado = await EnviarMensajeTool().run(
        ctx, {"plataforma": "slack", "destino": "C1", "texto": "hola"}
    )

    assert "Mensaje enviado por slack a C1" in resultado.content
    assert resultado.data["resultado"]["ts"] == "1.1"


async def test_enviar_mensaje_recorta_vista_previa_larga(make_ctx, make_session, make_vault):
    texto_largo = "x" * 300
    with respx.mock:
        respx.post(TELEGRAM_SEND_URL).mock(
            return_value=httpx.Response(200, json={"ok": True, "result": {}})
        )
        session = make_session([[_fila_cuenta()]])
        ctx = make_ctx(session=session, vault=_vault_con(make_vault, "123:ABC"))
        resultado = await EnviarMensajeTool().run(
            ctx, {"plataforma": "telegram", "destino": "chat-1", "texto": texto_largo}
        )
    assert "…" in resultado.content
    assert len(resultado.content) < len(texto_largo)


async def test_enviar_mensaje_error_de_la_api_no_rompe_y_no_registra_uso(
    make_ctx, make_session, make_vault
):
    with respx.mock:
        respx.post(TELEGRAM_SEND_URL).mock(
            return_value=httpx.Response(400, json={"ok": False, "description": "chat not found"})
        )
        session = make_session([[_fila_cuenta()]])
        ctx = make_ctx(session=session, vault=_vault_con(make_vault, "123:ABC"))
        resultado = await EnviarMensajeTool().run(
            ctx, {"plataforma": "telegram", "destino": "chat-1", "texto": "hola"}
        )

    assert "No se pudo enviar el mensaje por telegram" in resultado.content
    # Ni audit_log ni usage_events: el envío falló antes de llegar a registrarse.
    # Solo debe haber corrido la consulta de `connector_accounts` (resolver_credenciales).
    assert len(session.llamadas) == 1
    assert "connector_accounts" in session.llamadas[0][0]


# ---------------------------------------------------------------------------
# LeerMensajesTool
# ---------------------------------------------------------------------------


async def test_leer_mensajes_plataforma_no_soportada(make_ctx):
    # Ver comentario de `test_enviar_mensaje_plataforma_no_soportada`: "signal"
    # sigue siendo no soportada, "whatsapp" ya no lo es (aunque para lectura
    # responde con un mensaje de limitación, no con este — ver
    # `test_leer_mensajes_whatsapp_no_soportado_para_lectura` más abajo).
    resultado = await LeerMensajesTool().run(make_ctx(), {"plataforma": "signal"})
    assert "no es una plataforma de mensajería soportada" in resultado.content


async def test_leer_mensajes_discord_sin_origen(make_ctx):
    resultado = await LeerMensajesTool().run(make_ctx(), {"plataforma": "discord"})
    assert "origen" in resultado.content


async def test_leer_mensajes_slack_sin_origen(make_ctx):
    resultado = await LeerMensajesTool().run(make_ctx(), {"plataforma": "slack"})
    assert "origen" in resultado.content


async def test_leer_mensajes_sin_cuenta_conectada(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await LeerMensajesTool().run(ctx, {"plataforma": "slack", "origen": "C1"})
    assert "Slack" in resultado.content


@respx.mock
async def test_leer_mensajes_telegram_sin_origen_usa_get_updates(
    make_ctx, make_session, make_vault
):
    respx.get(TELEGRAM_UPDATES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {"update_id": 1, "message": {"from": {"first_name": "Ana"}, "text": "hola"}}
                ],
            },
        )
    )
    session = make_session([[_fila_cuenta()]])
    ctx = make_ctx(session=session, vault=_vault_con(make_vault, "123:ABC"))

    resultado = await LeerMensajesTool().run(ctx, {"plataforma": "telegram"})

    assert "Ana" in resultado.content
    assert "hola" in resultado.content
    assert len(resultado.data["mensajes"]) == 1


@respx.mock
async def test_leer_mensajes_slack_con_origen(make_ctx, make_session, make_vault):
    respx.get(SLACK_HISTORY_URL).mock(
        return_value=httpx.Response(
            200, json={"ok": True, "messages": [{"user": "U1", "text": "reporte listo"}]}
        )
    )
    session = make_session([[_fila_cuenta()]])
    ctx = make_ctx(session=session, vault=_vault_con(make_vault, "xoxb-1"))

    resultado = await LeerMensajesTool().run(
        ctx, {"plataforma": "slack", "origen": "C1", "limite": 5}
    )

    assert "reporte listo" in resultado.content


@respx.mock
async def test_leer_mensajes_sin_mensajes_devuelve_mensaje_vacio(
    make_ctx, make_session, make_vault
):
    respx.get(SLACK_HISTORY_URL).mock(
        return_value=httpx.Response(200, json={"ok": True, "messages": []})
    )
    session = make_session([[_fila_cuenta()]])
    ctx = make_ctx(session=session, vault=_vault_con(make_vault, "xoxb-1"))

    resultado = await LeerMensajesTool().run(ctx, {"plataforma": "slack", "origen": "C1"})

    assert "No hay mensajes recientes" in resultado.content
    assert resultado.data["mensajes"] == []


async def test_leer_mensajes_error_de_la_api(make_ctx, make_session, make_vault):
    with respx.mock:
        respx.get(SLACK_HISTORY_URL).mock(
            return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
        )
        session = make_session([[_fila_cuenta()]])
        ctx = make_ctx(session=session, vault=_vault_con(make_vault, "xoxb-1"))
        resultado = await LeerMensajesTool().run(ctx, {"plataforma": "slack", "origen": "C-mala"})

    assert "No se pudo leer mensajes de slack" in resultado.content


# ---------------------------------------------------------------------------
# WhatsApp (WP-V3-13) — enviar_mensaje: feliz (texto y plantilla) + sin
# credenciales; leer_mensajes: mensaje de limitación (solo envío en v3).
# ---------------------------------------------------------------------------


def test_enviar_mensaje_incluye_whatsapp_como_plataforma_soportada():
    assert "whatsapp" in EnviarMensajeTool().input_schema["properties"]["plataforma"]["enum"]
    assert "whatsapp" in LeerMensajesTool().input_schema["properties"]["plataforma"]["enum"]


@respx.mock
async def test_enviar_mensaje_por_whatsapp_texto_ok(make_ctx, make_session, make_vault):
    respx.post(WHATSAPP_SEND_URL).mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
    )
    session = make_session([[_fila_cuenta()]])
    ctx = make_ctx(
        session=session, vault=_vault_con_whatsapp(make_vault, "token-permanente-de-meta")
    )

    resultado = await EnviarMensajeTool().run(
        ctx, {"plataforma": "whatsapp", "destino": "+525512345678", "texto": "Hola equipo"}
    )

    assert "Mensaje enviado por whatsapp a +525512345678" in resultado.content
    assert resultado.data["resultado"]["messages"][0]["id"] == "wamid.1"
    enviado = respx.calls.last.request
    assert enviado.headers["Authorization"] == "Bearer token-permanente-de-meta"


@respx.mock
async def test_enviar_mensaje_por_whatsapp_con_plantilla_fuera_de_ventana(
    make_ctx, make_session, make_vault
):
    respx.post(WHATSAPP_SEND_URL).mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.2"}]})
    )
    session = make_session([[_fila_cuenta()]])
    ctx = make_ctx(
        session=session, vault=_vault_con_whatsapp(make_vault, "token-permanente-de-meta")
    )

    resultado = await EnviarMensajeTool().run(
        ctx,
        {
            "plataforma": "whatsapp",
            "destino": "525512345678",
            "texto": "texto de respaldo (ignorado, se usa la plantilla)",
            "plantilla": "confirmacion_pedido",
            "idioma": "es_MX",
        },
    )

    assert "Mensaje enviado por whatsapp" in resultado.content
    body = json.loads(respx.calls.last.request.content)
    assert body["type"] == "template"
    assert body["template"]["name"] == "confirmacion_pedido"
    assert body["template"]["language"] == {"code": "es_MX"}


async def test_enviar_mensaje_whatsapp_sin_cuenta_conectada(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await EnviarMensajeTool().run(
        ctx, {"plataforma": "whatsapp", "destino": "+525512345678", "texto": "hola"}
    )
    assert "WhatsApp" in resultado.content
    assert "conectores" in resultado.content


async def test_enviar_mensaje_whatsapp_es_dangerous_igual_que_las_demas():
    # `dangerous` es un atributo de la tool, no depende de la plataforma —
    # este test documenta explícitamente que WhatsApp no es una excepción:
    # sigue exigiendo confirmación humana (`Agent.run_turn`, ARCHITECTURE.md
    # §10.7) antes de enviarse.
    assert EnviarMensajeTool().dangerous is True


async def test_leer_mensajes_whatsapp_no_soportado_para_lectura(make_ctx):
    resultado = await LeerMensajesTool().run(make_ctx(), {"plataforma": "whatsapp"})
    assert resultado.content == (
        "Leer WhatsApp requiere webhooks entrantes con URL pública; en v3 Edecán solo envía."
    )


async def test_leer_mensajes_whatsapp_no_intenta_resolver_credenciales(make_ctx, make_session):
    # El mensaje de limitación debe salir ANTES de tocar `ctx.session`/`ctx.vault`
    # (no hace falta estar conectado para enterarse de que no se puede leer).
    session = make_session([[]])
    ctx = make_ctx(session=session)
    await LeerMensajesTool().run(ctx, {"plataforma": "whatsapp"})
    assert session.llamadas == []
