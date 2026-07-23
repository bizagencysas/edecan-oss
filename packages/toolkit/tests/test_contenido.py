"""Tests de `edecan_toolkit.contenido`: `generar_contenido` y `publicar_social`."""

from __future__ import annotations

import pytest
from edecan_toolkit.contenido import GenerarContenidoTool, PublicarSocialTool

# Estas redes no tienen hoy conector directo en `publicar_social`. LinkedIn sí
# dispone de creación multimedia y publicación por sesión local aprobada; esa
# vía pertenece a herramientas distintas.
REDES_NO_SOPORTADAS = ["linkedin", "tiktok", ""]


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
    for red_soportada in ("meta", "x", "youtube"):
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
