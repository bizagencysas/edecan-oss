"""Regresión anti-fuga dedicada de `edecan_ads.providers.get_tenant_ads_provider`
(Barrido de seguridad v5).

`test_providers.py` ya incluye
`test_get_tenant_ads_provider_nunca_cae_a_una_credencial_de_plataforma`
(estructural: no existe ningún `ADS_*` de plataforma que este resolver
pudiera usar). Este archivo agrega la pieza que falta: la request HTTP REAL
hacia la Graph API de Meta, capturada con `respx`, confirmando que el
`access_token` que sale en la query string es EXCLUSIVAMENTE el del tenant.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import respx
from edecan_ads.providers import MetaAdsProvider, get_tenant_ads_provider

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


@respx.mock
async def test_get_tenant_ads_provider_request_real_solo_lleva_el_token_del_tenant(
    make_ctx, make_session, make_vault
):
    route = respx.get("https://graph.facebook.com/v23.0/act_999/campaigns").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    bundle = SimpleNamespace(
        access_token=json.dumps({"access_token": "token-real-del-tenant", "ad_account_id": "999"})
    )
    ctx = make_ctx(
        # `settings` con un objeto vacío a propósito: `get_tenant_ads_provider`
        # ni siquiera declara un parámetro que pudiera leerlo (ver docstring
        # del módulo, "SIN el nivel intermedio de config de plataforma").
        session=make_session([[{"id": "cta-1"}]]),
        vault=make_vault(bundle=bundle),
    )

    provider = await get_tenant_ads_provider(ctx)
    assert isinstance(provider, MetaAdsProvider)
    await provider.list_campaigns()

    assert route.called
    query = parse_qs(urlparse(str(route.calls.last.request.url)).query)
    assert query["access_token"] == ["token-real-del-tenant"]
    assert _SENTINEL not in str(route.calls.last.request.url)


@respx.mock
async def test_get_tenant_ads_provider_dos_tenants_seguidos_nunca_mezclan_tokens(
    make_ctx, make_session, make_vault
):
    route = respx.get("https://graph.facebook.com/v23.0/act_999/campaigns").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    async def _run(token: str) -> None:
        bundle = SimpleNamespace(
            access_token=json.dumps({"access_token": token, "ad_account_id": "999"})
        )
        ctx = make_ctx(session=make_session([[{"id": "cta-1"}]]), vault=make_vault(bundle=bundle))
        provider = await get_tenant_ads_provider(ctx)
        await provider.list_campaigns()

    await _run("token-tenant-A")
    await _run("token-tenant-B")

    tokens_enviados = [
        parse_qs(urlparse(str(call.request.url)).query)["access_token"][0] for call in route.calls
    ]
    assert tokens_enviados == ["token-tenant-A", "token-tenant-B"]
