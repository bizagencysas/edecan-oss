"""Regresión anti-fuga dedicada de `edecan_toolkit.research.get_tenant_search_provider`
(Barrido de seguridad v5).

`test_research.py` ya cubre exhaustivamente, rama por rama (con
`_fake_settings_plataforma_completa`), que `get_tenant_search_provider` nunca
degrada a `get_search_provider(ctx.settings)`. Este archivo agrega la pieza
que falta ahí: la request HTTP REAL hacia Brave/Tavily, capturada con
`respx`, confirmando que el header/body de autenticación lleva EXCLUSIVAMENTE
la credencial del tenant — nunca el centinela de `BRAVE_API_KEY`/
`TAVILY_API_KEY` de plataforma, presente al mismo tiempo en `ctx.settings`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import respx
from edecan_toolkit.research import BraveSearch, TavilySearch, get_tenant_search_provider

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


def _fake_settings_plataforma_con_centinela(fake_settings):
    return fake_settings(SEARCH_PROVIDER="brave", BRAVE_API_KEY=_SENTINEL, TAVILY_API_KEY=_SENTINEL)


@respx.mock
async def test_get_tenant_search_provider_request_real_brave_no_lleva_el_centinela(
    make_ctx, make_session, make_vault, fake_settings
):
    route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(200, json={"web": {"results": []}})
    )
    bundle = SimpleNamespace(
        access_token=json.dumps({"provider": "brave", "api_key": "clave-real-del-tenant"})
    )
    ctx = make_ctx(
        settings=_fake_settings_plataforma_con_centinela(fake_settings),
        session=make_session([[{"id": "cta-1"}]]),
        vault=make_vault(bundle=bundle),
    )

    provider = await get_tenant_search_provider(ctx)
    assert isinstance(provider, BraveSearch)
    await provider.search("clima en Bogotá hoy")

    assert route.called
    header = route.calls.last.request.headers["X-Subscription-Token"]
    assert header == "clave-real-del-tenant"
    assert _SENTINEL not in header


@respx.mock
async def test_get_tenant_search_provider_request_real_tavily_no_lleva_el_centinela(
    make_ctx, make_session, make_vault, fake_settings
):
    route = respx.post("https://api.tavily.com/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    bundle = SimpleNamespace(
        access_token=json.dumps({"provider": "tavily", "api_key": "clave-real-del-tenant"})
    )
    ctx = make_ctx(
        settings=_fake_settings_plataforma_con_centinela(fake_settings),
        session=make_session([[{"id": "cta-1"}]]),
        vault=make_vault(bundle=bundle),
    )

    provider = await get_tenant_search_provider(ctx)
    assert isinstance(provider, TavilySearch)
    await provider.search("clima en Bogotá hoy")

    assert route.called
    body = json.loads(route.calls.last.request.content.decode())
    assert body["api_key"] == "clave-real-del-tenant"
    assert _SENTINEL not in json.dumps(body)
