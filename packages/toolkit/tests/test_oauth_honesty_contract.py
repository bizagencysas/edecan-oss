"""P0 OAuth awareness — ejecución real de tools (contrato de honestidad).

Ejecuta el código de producción de `EstadoConectoresSocialesTool`,
`PublicarSocialTool` y `GenerarContenidoTool`. Los fakes de `conftest` solo
implementan la interfaz de `session`/`vault`/`llm` (duck typing del repo);
el `ToolResult.content` y `data` salen del código real, sin parchear mensajes.
"""

from __future__ import annotations

from types import SimpleNamespace

from edecan_toolkit._conectores import RUTA_CONECTORES_UI
from edecan_toolkit.comunidad import EstadoConectoresSocialesTool
from edecan_toolkit.contenido import GenerarContenidoTool, PublicarSocialTool

_FILA_LINKEDIN = [{"connector_key": "linkedin", "id": "acc-li-99", "created_at": "2026-01-01"}]
_BUNDLE_REAL = SimpleNamespace(access_token="oauth-token-from-vault")


async def test_desconectado_estado_conectores_mensaje_honesto(make_ctx, make_session):
    session = make_session([[]])
    ctx = make_ctx(session=session)
    result = await EstadoConectoresSocialesTool().run(ctx, {})
    assert result.data["conectadas"] == []
    assert "Ninguna red social está conectada" in result.content
    assert RUTA_CONECTORES_UI in result.content
    assert "sin prisa" in result.content.lower()


async def test_desconectado_publicar_social_no_publica(make_ctx, make_session):
    session = make_session([[]])
    ctx = make_ctx(session=session)
    result = await PublicarSocialTool().run(ctx, {"red": "linkedin", "texto": "Hola mundo"})
    assert RUTA_CONECTORES_UI in result.content
    assert "Todavía no tienes conectada" in result.content
    assert len(session.llamadas) == 1


async def test_fila_sin_token_no_cuenta_como_conectada(make_ctx, make_session, make_vault):
    """Fila huérfana en DB sin token en vault → honesto NO conectada (cero ficción)."""
    session = make_session([_FILA_LINKEDIN])
    ctx = make_ctx(session=session, vault=make_vault(bundle=None))
    result = await EstadoConectoresSocialesTool().run(ctx, {})
    assert result.data["conectadas"] == []
    assert "LinkedIn: NO conectada" in result.content
    assert "Ninguna red social está conectada" in result.content


async def test_fila_sin_token_publicar_social_rechaza(make_ctx, make_session, make_vault):
    session = make_session([_FILA_LINKEDIN])
    ctx = make_ctx(session=session, vault=make_vault(bundle=None))
    result = await PublicarSocialTool().run(ctx, {"red": "linkedin", "texto": "Post"})
    assert RUTA_CONECTORES_UI in result.content
    assert "Todavía no tienes conectada" in result.content


async def test_conectado_estado_honesto_y_puede_redactar_borrador(
    make_ctx, make_session, make_vault, make_llm
):
    """Con token real en vault: estado OAuth honesto → camino borrador sin publicar."""
    llm = make_llm(texto="Borrador LinkedIn listo para revisar.")
    session_estado = make_session([_FILA_LINKEDIN])
    ctx_estado = make_ctx(
        session=session_estado,
        vault=make_vault(bundle=_BUNDLE_REAL),
    )
    estado = await EstadoConectoresSocialesTool().run(ctx_estado, {})
    assert "linkedin" in estado.data["conectadas"]
    assert "LinkedIn: conectada" in estado.content
    assert "NO conectada" in estado.content  # otras redes faltan

    ctx_borrador = make_ctx(llm=llm, vault=make_vault(bundle=_BUNDLE_REAL))
    borrador = await GenerarContenidoTool().run(
        ctx_borrador, {"brief": "Tips de productividad", "tipo": "post"}
    )
    assert borrador.content == "Borrador LinkedIn listo para revisar."
    assert len(llm.llamadas) == 1


async def test_conectado_publicar_social_exige_token_en_vault(make_ctx, make_session, make_vault):
    """Cuenta + token en vault: pasa el gate OAuth (no mensaje de desconectado)."""
    session = make_session([_FILA_LINKEDIN])
    ctx = make_ctx(session=session, vault=make_vault(bundle=_BUNDLE_REAL))
    # Sin red externa mockeada: debe superar el gate local y fallar en LinkedIn real
    # o propagar error del conector — nunca el copy de «falta conectar».
    from edecan_connectors.base import ConnectorError

    try:
        result = await PublicarSocialTool().run(ctx, {"red": "linkedin", "texto": "Post real"})
    except ConnectorError as exc:
        assert "Todavía no tienes conectada" not in str(exc)
        assert RUTA_CONECTORES_UI not in str(exc)
    else:
        assert "Todavía no tienes conectada" not in result.content
        assert RUTA_CONECTORES_UI not in result.content
    assert len(session.llamadas) == 1
