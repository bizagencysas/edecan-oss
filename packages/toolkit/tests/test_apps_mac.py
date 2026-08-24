"""Pruebas de las herramientas personales delegadas al companion de la Mac."""

from __future__ import annotations

from edecan_toolkit.apps_mac import (
    EnviarMensajePersonalTool,
    LeerMensajesPersonalesTool,
    invocar_app_personal,
    resultado_app_personal,
)


async def test_invocar_app_personal_sin_companion_devuelve_none(make_ctx):
    assert await invocar_app_personal(make_ctx(), "mac_messages_recent", {}) is None


async def test_invocar_app_personal_normaliza_respuesta_no_dict(make_ctx):
    async def companion(_action, _params):
        return "respuesta inválida"

    resultado = await invocar_app_personal(
        make_ctx(extras={"companion": companion}),
        "mac_messages_recent",
        {},
    )
    assert resultado == {
        "ok": False,
        "error": "La Mac no devolvió una respuesta válida.",
    }


def test_resultado_app_personal_distingue_sin_mac_error_y_payload():
    payload, error = resultado_app_personal(None)
    assert payload is None
    assert "Mac enlazada" in error.content

    payload, error = resultado_app_personal({"ok": False, "error": "permiso denegado"})
    assert payload is None
    assert "permiso denegado" in error.content

    payload, error = resultado_app_personal({"ok": "true", "result": {"count": 1}})
    assert payload is None
    assert "no confirmó" in error.content

    payload, error = resultado_app_personal({"ok": True, "result": {"count": 1}})
    assert error is None
    assert payload == {"count": 1}


async def test_leer_mensajes_formatea_y_conserva_datos(make_ctx):
    llamadas: list[tuple[str, dict]] = []

    async def companion(action, params):
        llamadas.append((action, params))
        return {
            "ok": True,
            "result": {
                "messages": [
                    {
                        "from_me": False,
                        "handle": "Contacto",
                        "text": "¿Cómo estás?",
                        "sent_at": "2026-07-27 10:00",
                    },
                    "registro inválido",
                    {
                        "from_me": True,
                        "handle": "",
                        "text": "Muy bien",
                        "sent_at": "",
                    },
                ]
            },
        }

    resultado = await LeerMensajesPersonalesTool().run(
        make_ctx(extras={"companion": companion}),
        {"limite": 4},
    )

    assert llamadas == [("mac_messages_recent", {"limit": 4})]
    assert "1. Contacto · 2026-07-27 10:00: ¿Cómo estás?" in resultado.content
    assert "2. Tú: Muy bien" in resultado.content
    assert len(resultado.data["mensajes"]) == 2


async def test_enviar_mensaje_usa_clave_redactada_y_exige_confirmacion(make_ctx):
    llamadas: list[tuple[str, dict]] = []

    async def companion(action, params):
        llamadas.append((action, params))
        return {
            "ok": True,
            "result": {"sent": True, "transport": "imessage"},
        }

    tool = EnviarMensajePersonalTool()
    resultado = await tool.run(
        make_ctx(extras={"companion": companion}),
        {"destinatario": "+15550000000", "mensaje": "Hola"},
    )

    assert tool.dangerous is True
    assert llamadas == [
        (
            "mac_messages_send",
            {"to": "+15550000000", "message": "Hola"},
        )
    ]
    assert "enviado" in resultado.content
    assert resultado.data["transport"] == "imessage"


async def test_enviar_mensaje_no_inventa_exito_si_companion_no_confirma(make_ctx):
    async def companion(_action, _params):
        return {"ok": True, "result": {"sent": False}}

    resultado = await EnviarMensajePersonalTool().run(
        make_ctx(extras={"companion": companion}),
        {"destinatario": "+15550000000", "mensaje": "Hola"},
    )

    assert "no confirmó" in resultado.content
    assert resultado.data is None


async def test_enviar_mensaje_valida_campos_antes_de_llamar_companion(make_ctx):
    llamadas = 0

    async def companion(_action, _params):
        nonlocal llamadas
        llamadas += 1
        return {"ok": True, "result": {"sent": True}}

    resultado = await EnviarMensajePersonalTool().run(
        make_ctx(extras={"companion": companion}),
        {"destinatario": " ", "mensaje": "Hola"},
    )

    assert llamadas == 0
    assert "destinatario" in resultado.content
