"""Tests de `edecan_toolkit.contenido`: `generar_contenido` y `publicar_social`.

Los casos de LinkedIn usan `respx` (offline, determinista — igual que
`test_agenda.py`) para simular la secuencia real de `linkedin.create_post`:
`GET /v2/userinfo` -> `POST /rest/posts` -> `GET /rest/posts/{id}` (la
relectura de `verify_post`). Son justo los casos que motivan este cambio: el
incidente real de hoy fue publicar "en nombre de" la página de empresa de
alguien y decirle que sí se publicó cuando NO se publicó nada.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx
from edecan_toolkit.contenido import GenerarContenidoTool, PublicarSocialTool

REDES_NO_SOPORTADAS = ["tiktok", ""]
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_POSTS_URL = "https://api.linkedin.com/rest/posts"


def _ctx_con_linkedin_conectado(make_ctx, make_session, make_vault):
    """`ctx` con una cuenta de LinkedIn ya conectada (fila de `connector_accounts`
    + `TokenBundle` en el vault) — lo que hace falta para llegar a
    `_publicar_en_linkedin` sin pasar por el "falta conectar"."""
    fila_cuenta = {"id": "acc-li", "connector_key": "linkedin"}
    bundle = SimpleNamespace(access_token="tok-li")
    return make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=bundle))


async def test_generar_contenido_devuelve_solo_texto_del_llm(make_ctx, make_llm):
    llm = make_llm(texto="Un post buenísimo sobre productividad.")
    ctx = make_ctx(llm=llm)

    resultado = await GenerarContenidoTool().run(
        ctx, {"brief": "3 tips de productividad para freelancers", "tipo": "post"}
    )

    assert resultado.content == "Un post buenísimo sobre productividad."
    assert resultado.data["tipo"] == "post"
    assert len(llm.llamadas) == 1
    alias, tenant_flags, _req = llm.llamadas[0]
    assert alias == "principal"
    assert tenant_flags == {}


async def test_generar_contenido_usa_flags_del_extras_si_estan(make_ctx, make_llm):
    llm = make_llm()
    ctx = make_ctx(llm=llm, extras={"flags": {"models.premium": False}})

    await GenerarContenidoTool().run(ctx, {"brief": "algo"})

    _alias, tenant_flags, _req = llm.llamadas[0]
    assert tenant_flags == {"models.premium": False}


async def test_generar_contenido_sin_brief_no_llama_al_llm(make_ctx, make_llm):
    llm = make_llm()
    resultado = await GenerarContenidoTool().run(make_ctx(llm=llm), {"brief": "   "})
    assert "brief" in resultado.content.lower()
    assert llm.llamadas == []


@pytest.mark.parametrize("red", REDES_NO_SOPORTADAS)
async def test_publicar_social_rechaza_redes_no_soportadas(make_ctx, make_session, red):
    session = make_session([])
    ctx = make_ctx(session=session)

    resultado = await PublicarSocialTool().run(ctx, {"red": red, "texto": "hola mundo"})

    assert "no tiene un conector directo" in resultado.content
    for red_soportada in ("linkedin", "meta", "x", "youtube"):
        assert red_soportada in resultado.content
    # El rechazo es puramente de validación: nunca llega a tocar la sesión/DB.
    assert session.llamadas == []


async def test_publicar_social_sin_cuenta_conectada_pide_conectar(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await PublicarSocialTool().run(ctx, {"red": "x", "texto": "hola"})
    assert "/app/conectores" in resultado.content


async def test_publicar_social_sin_texto_no_resuelve_cuenta(make_ctx, make_session):
    session = make_session([])
    ctx = make_ctx(session=session)
    resultado = await PublicarSocialTool().run(ctx, {"red": "x", "texto": "  "})
    assert "texto" in resultado.content.lower()
    assert session.llamadas == []


def test_publicar_social_tiene_flag_y_es_dangerous():
    tool = PublicarSocialTool()
    assert tool.dangerous is True
    assert tool.requires_flags == frozenset({"connectors.social"})


@respx.mock
async def test_publicar_social_linkedin_confirmado_dice_publicado_sin_matices(
    make_ctx, make_session, make_vault
):
    """Camino feliz: `verify_post` relee el post y SÍ existe -- ahí, y solo ahí,
    el mensaje puede ser el llano "Publicado en LinkedIn." de siempre."""
    ctx = _ctx_con_linkedin_conectado(make_ctx, make_session, make_vault)
    respx.get(LINKEDIN_USERINFO_URL).mock(return_value=httpx.Response(200, json={"sub": "999"}))
    respx.post(LINKEDIN_POSTS_URL).mock(
        return_value=httpx.Response(201, headers={"x-restli-id": "post-123"})
    )
    respx.get(f"{LINKEDIN_POSTS_URL}/post-123").mock(return_value=httpx.Response(200, json={}))

    resultado = await PublicarSocialTool().run(
        ctx, {"red": "linkedin", "texto": "hola mundo"}
    )

    assert resultado.content == "Publicado en LinkedIn."
    assert resultado.data["resultado"]["verified"] == "confirmed"


@respx.mock
async def test_publicar_social_linkedin_no_confirmado_no_afirma_exito(
    make_ctx, make_session, make_vault
):
    """El caso que reventó hoy en producción: LinkedIn acepta el POST (201 +
    `x-restli-id` con forma válida) pero la relectura de `verify_post` no puede
    confirmar nada (403, típico de un token sin scope de lectura). El `content`
    -- lo ÚNICO que el modelo ve, `data` es privado -- NO puede sonar a éxito:
    nada de "Publicado", nada de ✅, y sí una instrucción explícita de decir que
    falta confirmar y de revisarlo en LinkedIn directamente."""
    ctx = _ctx_con_linkedin_conectado(make_ctx, make_session, make_vault)
    respx.get(LINKEDIN_USERINFO_URL).mock(return_value=httpx.Response(200, json={"sub": "999"}))
    respx.post(LINKEDIN_POSTS_URL).mock(
        return_value=httpx.Response(201, headers={"x-restli-id": "post-999"})
    )
    respx.get(f"{LINKEDIN_POSTS_URL}/post-999").mock(
        return_value=httpx.Response(403, json={"message": "ACCESS_DENIED"})
    )

    resultado = await PublicarSocialTool().run(
        ctx, {"red": "linkedin", "texto": "hola mundo"}
    )

    assert resultado.data["resultado"]["verified"] == "unknown"
    assert "Publicado en LinkedIn." != resultado.content
    assert "✅" not in resultado.content
    assert "no se pudo confirmar" in resultado.content.lower()
    assert "linkedin" in resultado.content.lower()


@respx.mock
async def test_publicar_social_linkedin_post_fantasma_propaga_error_sin_reportar_exito(
    make_ctx, make_session, make_vault
):
    """Variante límite del mismo incidente: la relectura confirma que el post NO
    existe (`404`) -- `linkedin.create_post` lo trata como fallo real y levanta
    `ConnectorError` en vez de devolver un resultado "exitoso" a medias. Esta
    tool no debe atajar esa excepción y disfrazarla de éxito: falta que la capa
    de arriba (`Agent.run_turn`) la traduzca a un evento de error, así que aquí
    solo se fija el contrato de que la excepción sale de `run()` tal cual."""
    from edecan_connectors.base import ConnectorError

    ctx = _ctx_con_linkedin_conectado(make_ctx, make_session, make_vault)
    respx.get(LINKEDIN_USERINFO_URL).mock(return_value=httpx.Response(200, json={"sub": "999"}))
    respx.post(LINKEDIN_POSTS_URL).mock(
        return_value=httpx.Response(201, headers={"x-restli-id": "post-fantasma"})
    )
    respx.get(f"{LINKEDIN_POSTS_URL}/post-fantasma").mock(return_value=httpx.Response(404))

    with pytest.raises(ConnectorError):
        await PublicarSocialTool().run(ctx, {"red": "linkedin", "texto": "hola mundo"})
