"""Tests de `edecan_ads.providers`: `StubAdsProvider`, `MetaAdsProvider` (con
`respx`, sin red real) y `get_tenant_ads_provider` (bring-your-own).

El test más importante del módulo es
`test_meta_provider_create_campaign_paused_siempre_manda_status_paused*`: el
guardrail de dinero pinned en el paquete de trabajo ("hardcodea PAUSED y
testéalo") — verifica, mirando el body EXACTO que se mandó a Meta, que
`status` es SIEMPRE `"PAUSED"`, incluso si alguien intenta colar
`status="ACTIVE"` en `payload`.
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from edecan_ads.providers import (
    ADS_CONNECTOR_KEY,
    META_GRAPH_BASE_URL,
    MetaAdsError,
    MetaAdsProvider,
    StubAdsProvider,
    get_tenant_ads_provider,
    normalizar_ad_account_id,
)

AD_ACCOUNT_ID = "123456789"
ACCESS_TOKEN = "token-de-prueba"
_CAMPAIGNS_URL = f"{META_GRAPH_BASE_URL}/act_{AD_ACCOUNT_ID}/campaigns"
_INSIGHTS_URL = f"{META_GRAPH_BASE_URL}/act_{AD_ACCOUNT_ID}/insights"


# ---------------------------------------------------------------------------
# normalizar_ad_account_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("123456789", "123456789"),
        ("act_123456789", "123456789"),
        ("ACT_123456789", "123456789"),
        ("  act_123  ", "123"),
        ("", ""),
    ],
)
def test_normalizar_ad_account_id(valor: str, esperado: str) -> None:
    assert normalizar_ad_account_id(valor) == esperado


# ---------------------------------------------------------------------------
# StubAdsProvider — determinista, offline, nunca crea nada real.
# ---------------------------------------------------------------------------


async def test_stub_provider_list_campaigns_no_esta_vacio_y_esta_pausada():
    campanas = await StubAdsProvider().list_campaigns()
    assert len(campanas) >= 1
    assert all(c["status"] == "PAUSED" for c in campanas)


async def test_stub_provider_insights_devuelve_ceros_y_respeta_date_preset():
    metricas = await StubAdsProvider().insights("last_7d")
    assert metricas["date_preset"] == "last_7d"
    assert metricas["spend"] == "0"


async def test_stub_provider_create_campaign_paused_es_deterministico():
    provider = StubAdsProvider()
    a = await provider.create_campaign_paused("Campaña A", "OUTCOME_TRAFFIC", 50, "USD", {})
    b = await provider.create_campaign_paused("Campaña A", "OUTCOME_TRAFFIC", 50, "USD", {})
    c = await provider.create_campaign_paused("Campaña B", "OUTCOME_TRAFFIC", 50, "USD", {})
    assert a == b
    assert a != c
    assert a.startswith("stub-campaign-")


async def test_stub_provider_create_campaign_paused_ignora_status_en_payload():
    provider = StubAdsProvider()
    # El stub ni siquiera mira `payload` — nunca habla con ninguna red real,
    # así que "ACTIVE" aquí no puede activar nada de verdad.
    resultado = await provider.create_campaign_paused(
        "X", "OUTCOME_TRAFFIC", None, "USD", {"status": "ACTIVE"}
    )
    assert resultado.startswith("stub-campaign-")


# ---------------------------------------------------------------------------
# MetaAdsProvider.list_campaigns / insights
# ---------------------------------------------------------------------------


@respx.mock
async def test_meta_provider_list_campaigns_pide_los_campos_correctos():
    ruta = respx.get(_CAMPAIGNS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "1",
                        "name": "Campaña 1",
                        "status": "PAUSED",
                        "objective": "OUTCOME_TRAFFIC",
                        "daily_budget": "5000",
                    }
                ]
            },
        )
    )
    provider = MetaAdsProvider(access_token=ACCESS_TOKEN, ad_account_id=AD_ACCOUNT_ID)

    campanas = await provider.list_campaigns()

    assert campanas == [
        {
            "id": "1",
            "name": "Campaña 1",
            "status": "PAUSED",
            "objective": "OUTCOME_TRAFFIC",
            "daily_budget": "5000",
        }
    ]
    request = ruta.calls.last.request
    assert request.url.params["fields"] == "name,status,objective,daily_budget"
    assert request.url.params["access_token"] == ACCESS_TOKEN


@respx.mock
async def test_meta_provider_list_campaigns_acepta_ad_account_id_con_prefijo_act():
    respx.get(_CAMPAIGNS_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    provider = MetaAdsProvider(access_token=ACCESS_TOKEN, ad_account_id=f"act_{AD_ACCOUNT_ID}")

    campanas = await provider.list_campaigns()

    assert campanas == []


@respx.mock
async def test_meta_provider_list_campaigns_data_ausente_devuelve_lista_vacia():
    respx.get(_CAMPAIGNS_URL).mock(return_value=httpx.Response(200, json={}))
    provider = MetaAdsProvider(access_token=ACCESS_TOKEN, ad_account_id=AD_ACCOUNT_ID)

    assert await provider.list_campaigns() == []


@respx.mock
async def test_meta_provider_insights_pide_date_preset_y_fields():
    ruta = respx.get(_INSIGHTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "spend": "12.50",
                        "impressions": "100",
                        "clicks": "3",
                        "cpc": "4.16",
                        "ctr": "3",
                    }
                ]
            },
        )
    )
    provider = MetaAdsProvider(access_token=ACCESS_TOKEN, ad_account_id=AD_ACCOUNT_ID)

    metricas = await provider.insights("last_7d")

    assert metricas["spend"] == "12.50"
    request = ruta.calls.last.request
    assert request.url.params["date_preset"] == "last_7d"
    assert request.url.params["fields"] == "spend,impressions,clicks,cpc,ctr"


@respx.mock
async def test_meta_provider_insights_sin_filas_devuelve_ceros():
    respx.get(_INSIGHTS_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    provider = MetaAdsProvider(access_token=ACCESS_TOKEN, ad_account_id=AD_ACCOUNT_ID)

    metricas = await provider.insights()

    assert metricas["spend"] == "0"
    assert metricas["date_preset"] == "last_30d"


@respx.mock
async def test_meta_provider_token_invalido_lanza_meta_ads_error_con_mensaje_de_meta():
    respx.get(_CAMPAIGNS_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "message": "Error validating access token",
                    "type": "OAuthException",
                    "code": 190,
                }
            },
        )
    )
    provider = MetaAdsProvider(access_token="invalido", ad_account_id=AD_ACCOUNT_ID)

    with pytest.raises(MetaAdsError, match="Error validating access token"):
        await provider.list_campaigns()


@respx.mock
async def test_meta_provider_error_de_red_lanza_meta_ads_error():
    respx.get(_CAMPAIGNS_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    provider = MetaAdsProvider(access_token=ACCESS_TOKEN, ad_account_id=AD_ACCOUNT_ID)

    with pytest.raises(MetaAdsError, match="No se pudo conectar"):
        await provider.list_campaigns()


# ---------------------------------------------------------------------------
# MetaAdsProvider.create_campaign_paused — EL guardrail de dinero.
# ---------------------------------------------------------------------------


@respx.mock
async def test_meta_provider_create_campaign_paused_manda_status_paused():
    ruta = respx.post(_CAMPAIGNS_URL).mock(return_value=httpx.Response(200, json={"id": "camp-1"}))
    provider = MetaAdsProvider(access_token=ACCESS_TOKEN, ad_account_id=AD_ACCOUNT_ID)

    external_id = await provider.create_campaign_paused(
        "Campaña nueva", "OUTCOME_TRAFFIC", Decimal("50.00"), "USD", {}
    )

    assert external_id == "camp-1"
    body = json.loads(ruta.calls.last.request.content)
    assert body["status"] == "PAUSED"
    assert body["name"] == "Campaña nueva"
    assert body["objective"] == "OUTCOME_TRAFFIC"
    assert body["special_ad_categories"] == []
    assert body["daily_budget"] == "5000"  # 50.00 USD -> 5000 centavos


@respx.mock
async def test_meta_provider_create_campaign_paused_ignora_status_activo_en_payload():
    """EL test explícito que pide el paquete de trabajo: ningún `payload` puede
    hacer que la campaña se cree activa."""
    ruta = respx.post(_CAMPAIGNS_URL).mock(return_value=httpx.Response(200, json={"id": "camp-2"}))
    provider = MetaAdsProvider(access_token=ACCESS_TOKEN, ad_account_id=AD_ACCOUNT_ID)

    await provider.create_campaign_paused(
        "Otra campaña",
        "OUTCOME_ENGAGEMENT",
        None,
        "USD",
        {"status": "ACTIVE", "special_ad_categories": ["NONE"]},
    )

    body = json.loads(ruta.calls.last.request.content)
    assert body["status"] == "PAUSED"
    # El resto del payload (que sí es legítimo) se respeta.
    assert body["special_ad_categories"] == ["NONE"]
    assert "daily_budget" not in body  # sin presupuesto_diario, no se inventa uno.


@respx.mock
async def test_meta_provider_create_campaign_paused_respeta_daily_budget_explicito_en_payload():
    ruta = respx.post(_CAMPAIGNS_URL).mock(return_value=httpx.Response(200, json={"id": "camp-3"}))
    provider = MetaAdsProvider(access_token=ACCESS_TOKEN, ad_account_id=AD_ACCOUNT_ID)

    await provider.create_campaign_paused(
        "X", "OUTCOME_SALES", Decimal("999.00"), "USD", {"daily_budget": "1234"}
    )

    body = json.loads(ruta.calls.last.request.content)
    assert body["daily_budget"] == "1234"  # el payload manda, no se recalcula.
    assert body["status"] == "PAUSED"


@respx.mock
async def test_meta_provider_create_campaign_paused_moneda_sin_decimales_no_multiplica():
    ruta = respx.post(_CAMPAIGNS_URL).mock(return_value=httpx.Response(200, json={"id": "camp-4"}))
    provider = MetaAdsProvider(access_token=ACCESS_TOKEN, ad_account_id=AD_ACCOUNT_ID)

    await provider.create_campaign_paused("X", "OUTCOME_TRAFFIC", Decimal("500"), "JPY", {})

    body = json.loads(ruta.calls.last.request.content)
    assert body["daily_budget"] == "500"  # JPY no tiene decimales: sin x100.
    assert body["status"] == "PAUSED"


@respx.mock
async def test_meta_provider_create_campaign_paused_error_meta_lanza_meta_ads_error():
    respx.post(_CAMPAIGNS_URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Invalid parameter", "code": 100}}
        )
    )
    provider = MetaAdsProvider(access_token=ACCESS_TOKEN, ad_account_id=AD_ACCOUNT_ID)

    with pytest.raises(MetaAdsError, match="Invalid parameter"):
        await provider.create_campaign_paused("X", "OUTCOME_TRAFFIC", None, "USD", {})


@respx.mock
async def test_meta_provider_create_campaign_paused_sin_id_en_respuesta_lanza_error():
    respx.post(_CAMPAIGNS_URL).mock(return_value=httpx.Response(200, json={}))
    provider = MetaAdsProvider(access_token=ACCESS_TOKEN, ad_account_id=AD_ACCOUNT_ID)

    with pytest.raises(MetaAdsError, match="id"):
        await provider.create_campaign_paused("X", "OUTCOME_TRAFFIC", None, "USD", {})


# ---------------------------------------------------------------------------
# get_tenant_ads_provider (bring-your-own, "tenant -> stub", nunca plataforma)
# ---------------------------------------------------------------------------


async def test_get_tenant_ads_provider_sin_vault_cae_a_stub(make_ctx):
    ctx = make_ctx()  # vault=None por defecto
    provider = await get_tenant_ads_provider(ctx)
    assert isinstance(provider, StubAdsProvider)


async def test_get_tenant_ads_provider_sin_cuenta_conectada_cae_a_stub(
    make_ctx, make_session, make_vault, caplog
):
    ctx = make_ctx(session=make_session([[]]), vault=make_vault())
    with caplog.at_level("WARNING"):
        provider = await get_tenant_ads_provider(ctx)
    assert isinstance(provider, StubAdsProvider)
    assert "PUT /v1/ads/credentials" in caplog.text


async def test_get_tenant_ads_provider_filtra_por_connector_key_ads(
    make_ctx, make_session, make_vault
):
    session = make_session([[]])
    ctx = make_ctx(session=session, vault=make_vault())

    await get_tenant_ads_provider(ctx)

    assert session.llamadas[0][1]["connector_key"] == ADS_CONNECTOR_KEY


async def test_get_tenant_ads_provider_vault_revienta_cae_a_stub(make_ctx, make_session, caplog):
    class _VaultQueRevienta:
        async def get(self, tenant_id: Any, connector_account_id: Any) -> Any:
            raise RuntimeError("vault caído")

    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=_VaultQueRevienta(),
    )
    with caplog.at_level("WARNING"):
        provider = await get_tenant_ads_provider(ctx)
    assert isinstance(provider, StubAdsProvider)


async def test_get_tenant_ads_provider_bundle_vacio_cae_a_stub(make_ctx, make_session, make_vault):
    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=None),
    )
    provider = await get_tenant_ads_provider(ctx)
    assert isinstance(provider, StubAdsProvider)


async def test_get_tenant_ads_provider_json_corrupto_cae_a_stub(make_ctx, make_session, make_vault):
    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=SimpleNamespace(access_token="esto no es JSON")),
    )
    provider = await get_tenant_ads_provider(ctx)
    assert isinstance(provider, StubAdsProvider)


async def test_get_tenant_ads_provider_campos_incompletos_cae_a_stub(
    make_ctx, make_session, make_vault
):
    bundle = SimpleNamespace(access_token=json.dumps({"access_token": "solo-token-sin-cuenta"}))
    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=bundle),
    )
    provider = await get_tenant_ads_provider(ctx)
    assert isinstance(provider, StubAdsProvider)


async def test_get_tenant_ads_provider_usa_la_credencial_del_tenant(
    make_ctx, make_session, make_vault
):
    cuenta_id = "11111111-1111-1111-1111-111111111111"
    bundle = SimpleNamespace(
        access_token=json.dumps({"access_token": "tok-del-tenant", "ad_account_id": "999"})
    )
    session = make_session([[{"id": cuenta_id}]])
    vault = make_vault(bundle=bundle)
    ctx = make_ctx(session=session, vault=vault)

    provider = await get_tenant_ads_provider(ctx)

    assert isinstance(provider, MetaAdsProvider)
    assert vault.llamadas == [(ctx.tenant_id, cuenta_id)]


async def test_get_tenant_ads_provider_nunca_cae_a_una_credencial_de_plataforma(
    make_ctx, fake_settings
):
    """No existe ningún `ADS_*` de plataforma que este resolver pueda usar —
    `settings` con cualquier contenido nunca afecta el resultado sin vault."""
    ctx = make_ctx(settings=fake_settings(ADS_API_KEY="no-deberia-usarse-nunca"))
    provider = await get_tenant_ads_provider(ctx)
    assert isinstance(provider, StubAdsProvider)
