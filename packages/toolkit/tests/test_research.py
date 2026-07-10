"""Tests de `edecan_toolkit.research`: `SearchProvider`s y la tool `buscar_web`."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from edecan_toolkit.research import (
    SEARCH_CONNECTOR_KEY,
    BraveSearch,
    BuscarWebTool,
    SearchHit,
    StubSearch,
    TavilySearch,
    get_search_provider,
    get_tenant_search_provider,
)


async def test_stub_search_es_determinista_y_no_hace_red(fake_settings):
    proveedor = get_search_provider(fake_settings())
    assert isinstance(proveedor, StubSearch)
    hits = await proveedor.search("cómo hacer pan", k=3)
    assert len(hits) == 3
    assert all(hit.url.startswith("https://example.com/search?q=") for hit in hits)


def test_get_search_provider_brave_sin_api_key_lanza_error(fake_settings):
    with pytest.raises(RuntimeError, match="BRAVE_API_KEY"):
        get_search_provider(fake_settings(SEARCH_PROVIDER="brave", BRAVE_API_KEY=None))


def test_get_search_provider_tavily_sin_api_key_lanza_error(fake_settings):
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        get_search_provider(fake_settings(SEARCH_PROVIDER="tavily", TAVILY_API_KEY=None))


def test_get_search_provider_resuelve_segun_settings(fake_settings):
    brave = get_search_provider(fake_settings(SEARCH_PROVIDER="brave", BRAVE_API_KEY="k"))
    assert isinstance(brave, BraveSearch)

    tavily = get_search_provider(fake_settings(SEARCH_PROVIDER="tavily", TAVILY_API_KEY="k"))
    assert isinstance(tavily, TavilySearch)

    assert isinstance(get_search_provider(fake_settings(SEARCH_PROVIDER="stub")), StubSearch)
    assert isinstance(get_search_provider(fake_settings(SEARCH_PROVIDER="algo-raro")), StubSearch)


@respx.mock
async def test_brave_search_parsea_resultados():
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Pan casero",
                            "url": "https://ejemplo.com/pan",
                            "description": "Receta fácil.",
                        }
                    ]
                }
            },
        )
    )
    hits = await BraveSearch("clave-brave").search("pan casero", k=5)
    assert hits == [
        SearchHit(title="Pan casero", url="https://ejemplo.com/pan", snippet="Receta fácil.")
    ]


@respx.mock
async def test_tavily_search_parsea_resultados():
    ruta = respx.post("https://api.tavily.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Pan casero", "url": "https://ejemplo.com/pan", "content": "Receta."}
                ]
            },
        )
    )
    hits = await TavilySearch("clave-tavily").search("pan casero", k=2)
    assert hits[0].title == "Pan casero"
    enviado = ruta.calls.last.request
    assert b'"api_key":"clave-tavily"' in enviado.content


async def test_buscar_web_tool_usa_stub_por_defecto_y_resume(make_ctx):
    resultado = await BuscarWebTool().run(make_ctx(), {"consulta": "recetas veganas", "k": 2})
    assert len(resultado.data["resultados"]) == 2
    assert "recetas veganas" in resultado.content


async def test_buscar_web_tool_sin_consulta(make_ctx):
    resultado = await BuscarWebTool().run(make_ctx(), {"consulta": "   "})
    assert "busque" in resultado.content.lower()


# --- get_tenant_search_provider (bring-your-own, auditoría "riesgo-legal-tos") ------
#
# Desde la corrección de diseño de `DIRECCION_ACTUAL.md` ("nunca una llave
# compartida de plataforma"), TODAS las ramas de fallback de
# `get_tenant_search_provider` caen DIRECTO a `StubSearch` — nunca a
# `get_search_provider(ctx.settings)` — mismo criterio "tenant → stub" que ya
# sigue `apps/api/edecan_api/routers/voice.py::_stt_para_tenant`. Por eso cada
# `fake_settings(...)` de esta sección deja `SEARCH_PROVIDER="brave"` COMPLETO
# y válido a propósito: si alguna rama volviera a consultarlo (regresión al
# comportamiento viejo), el test fallaría al ver un `BraveSearch` de
# PLATAFORMA en vez de un `StubSearch`.


def _fake_settings_plataforma_completa(fake_settings):
    """`SEARCH_PROVIDER=brave` totalmente configurado y válido — para probar
    que `get_tenant_search_provider` JAMÁS lo usa como fallback."""
    return fake_settings(SEARCH_PROVIDER="brave", BRAVE_API_KEY="clave-de-plataforma")


async def test_get_tenant_search_provider_sin_cuenta_conectada_cae_a_stub(
    make_ctx, make_session, make_vault, fake_settings, caplog
):
    """El tenant nunca hizo `PUT /v1/credentials/search`: la consulta a
    `connector_accounts` no devuelve filas — cae directo a stub, NUNCA a
    `BRAVE_API_KEY`/`SEARCH_PROVIDER` de plataforma (aunque esté completa y
    válida: regresión directa del fix, antes de él esto habría devuelto un
    `BraveSearch` de plataforma), y avisa por log cómo conectar una
    credencial propia."""
    ctx = make_ctx(
        settings=_fake_settings_plataforma_completa(fake_settings),
        session=make_session([[]]),
        vault=make_vault(),
    )
    with caplog.at_level("WARNING"):
        proveedor = await get_tenant_search_provider(ctx)
    assert isinstance(proveedor, StubSearch)
    assert "PUT /v1/credentials/search" in caplog.text


async def test_get_tenant_search_provider_vault_revienta_cae_a_stub_nunca_plataforma(
    make_ctx, make_session, fake_settings, caplog
):
    """El tenant SÍ tiene una cuenta conectada, pero leer el vault revienta
    (vault caído) — cae a stub, JAMÁS a `get_search_provider(ctx.settings)`
    aunque la plataforma esté perfectamente configurada."""

    class _VaultQueRevienta:
        async def get(self, tenant_id: Any, connector_account_id: Any) -> Any:
            raise RuntimeError("vault caído")

    ctx = make_ctx(
        settings=_fake_settings_plataforma_completa(fake_settings),
        session=make_session([[{"id": "66666666-6666-6666-6666-666666666666"}]]),
        vault=_VaultQueRevienta(),
    )
    with caplog.at_level("WARNING"):
        proveedor = await get_tenant_search_provider(ctx)
    assert isinstance(proveedor, StubSearch)
    assert "PUT /v1/credentials/search" in caplog.text


async def test_get_tenant_search_provider_usa_la_credencial_del_tenant(
    make_ctx, make_session, make_vault, fake_settings
):
    """El tenant SÍ conectó su propia key de Brave — se usa esa, no
    `BRAVE_API_KEY`/`SEARCH_PROVIDER` de plataforma (configurados acá a
    propósito, ver `_fake_settings_plataforma_completa`)."""
    cuenta_id = "22222222-2222-2222-2222-222222222222"
    session = make_session([[{"id": cuenta_id}]])
    bundle = SimpleNamespace(
        access_token=json.dumps({"provider": "brave", "api_key": "clave-brave-del-tenant"})
    )
    vault = make_vault(bundle=bundle)
    ctx = make_ctx(
        settings=_fake_settings_plataforma_completa(fake_settings), session=session, vault=vault
    )

    proveedor = await get_tenant_search_provider(ctx)

    assert isinstance(proveedor, BraveSearch)
    assert session.llamadas[0][1]["connector_key"] == SEARCH_CONNECTOR_KEY
    assert vault.llamadas == [(ctx.tenant_id, cuenta_id)]


async def test_get_tenant_search_provider_tavily_del_tenant(make_ctx, make_session, make_vault):
    bundle = SimpleNamespace(
        access_token=json.dumps({"provider": "tavily", "api_key": "clave-tavily-del-tenant"})
    )
    ctx = make_ctx(
        session=make_session([[{"id": "33333333-3333-3333-3333-333333333333"}]]),
        vault=make_vault(bundle=bundle),
    )
    proveedor = await get_tenant_search_provider(ctx)
    assert isinstance(proveedor, TavilySearch)


async def test_get_tenant_search_provider_json_corrupto_cae_a_stub(
    make_ctx, make_session, make_vault, fake_settings
):
    """Config ilegible en el vault: se trata igual que "el tenant no conectó
    nada" (mismo criterio que `_read_config` en `credentials.py`), nunca
    revienta `buscar_web` y nunca cae a plataforma."""
    ctx = make_ctx(
        settings=_fake_settings_plataforma_completa(fake_settings),
        session=make_session([[{"id": "44444444-4444-4444-4444-444444444444"}]]),
        vault=make_vault(bundle=SimpleNamespace(access_token="esto no es JSON")),
    )
    proveedor = await get_tenant_search_provider(ctx)
    assert isinstance(proveedor, StubSearch)


async def test_get_tenant_search_provider_provider_desconocido_cae_a_stub(
    make_ctx, make_session, make_vault, fake_settings
):
    bundle = SimpleNamespace(access_token=json.dumps({"provider": "google", "api_key": "x"}))
    ctx = make_ctx(
        settings=_fake_settings_plataforma_completa(fake_settings),
        session=make_session([[{"id": "55555555-5555-5555-5555-555555555555"}]]),
        vault=make_vault(bundle=bundle),
    )
    proveedor = await get_tenant_search_provider(ctx)
    assert isinstance(proveedor, StubSearch)
