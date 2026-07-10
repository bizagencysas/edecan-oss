"""Tests de `edecan_travel.providers`: `StubTravelProvider`/`StubTrackingProvider`
(deterministas, offline) y `get_tenant_travel_provider`/`get_tenant_tracking_provider`
(bring-your-own, "tenant -> stub", NUNCA una credencial de plataforma).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from edecan_travel.amadeus import AmadeusClient
from edecan_travel.providers import (
    TRACKING_CONNECTOR_KEY,
    TRAVEL_CONNECTOR_KEY,
    StubTrackingProvider,
    StubTravelProvider,
    get_tenant_tracking_provider,
    get_tenant_travel_provider,
)
from edecan_travel.tracking import AfterShipClient

# ---------------------------------------------------------------------------
# StubTravelProvider — determinista, offline, claramente marcado como ejemplo.
# ---------------------------------------------------------------------------


async def test_stub_travel_buscar_vuelos_devuelve_ejemplos_marcados():
    ofertas = await StubTravelProvider().buscar_vuelos("BOG", "MIA", "2026-08-01")
    assert len(ofertas) >= 1
    assert all("ejemplo" in o.aerolinea.lower() for o in ofertas)
    assert all(o.origen == "BOG" and o.destino == "MIA" for o in ofertas)


async def test_stub_travel_buscar_hoteles_devuelve_ejemplos_marcados():
    ofertas = await StubTravelProvider().buscar_hoteles("PAR", "2026-09-01", "2026-09-05")
    assert len(ofertas) >= 1
    assert all("ejemplo" in o.nombre.lower() for o in ofertas)


async def test_stub_travel_estado_vuelo_es_determinista():
    a = await StubTravelProvider().estado_vuelo("av", "123", "2026-08-01")
    b = await StubTravelProvider().estado_vuelo("AV", "123", "2026-08-01")
    assert a == b
    assert a.carrier == "AV"


async def test_stub_tracking_rastrear_devuelve_ejemplo_marcado():
    rastreo = await StubTrackingProvider().rastrear("cualquier-numero")
    assert rastreo.estado
    assert len(rastreo.checkpoints) >= 1
    assert any("ejemplo" in cp.mensaje.lower() for cp in rastreo.checkpoints)


async def test_stub_tracking_respeta_courier_slug_recibido():
    rastreo = await StubTrackingProvider().rastrear("123", "dhl")
    assert rastreo.courier == "dhl"


# ---------------------------------------------------------------------------
# get_tenant_travel_provider (bring-your-own, "tenant -> stub", nunca plataforma)
# ---------------------------------------------------------------------------


async def test_get_tenant_travel_provider_sin_vault_cae_a_stub(make_ctx):
    ctx = make_ctx()  # vault=None por defecto
    provider = await get_tenant_travel_provider(ctx)
    assert isinstance(provider, StubTravelProvider)


async def test_get_tenant_travel_provider_sin_cuenta_conectada_cae_a_stub(
    make_ctx, make_session, make_vault, caplog
):
    ctx = make_ctx(session=make_session([[]]), vault=make_vault())
    with caplog.at_level("WARNING"):
        provider = await get_tenant_travel_provider(ctx)
    assert isinstance(provider, StubTravelProvider)
    assert "PUT /v1/viajes/credentials" in caplog.text


async def test_get_tenant_travel_provider_filtra_por_connector_key_travel(
    make_ctx, make_session, make_vault
):
    session = make_session([[]])
    ctx = make_ctx(session=session, vault=make_vault())

    await get_tenant_travel_provider(ctx)

    assert session.llamadas[0][1]["connector_key"] == TRAVEL_CONNECTOR_KEY == "travel"


async def test_get_tenant_travel_provider_vault_revienta_cae_a_stub(make_ctx, make_session, caplog):
    class _VaultQueRevienta:
        async def get(self, tenant_id: Any, connector_account_id: Any) -> Any:
            raise RuntimeError("vault caído")

    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=_VaultQueRevienta(),
    )
    with caplog.at_level("WARNING"):
        provider = await get_tenant_travel_provider(ctx)
    assert isinstance(provider, StubTravelProvider)


async def test_get_tenant_travel_provider_bundle_vacio_cae_a_stub(
    make_ctx, make_session, make_vault
):
    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=None),
    )
    provider = await get_tenant_travel_provider(ctx)
    assert isinstance(provider, StubTravelProvider)


async def test_get_tenant_travel_provider_json_corrupto_cae_a_stub(
    make_ctx, make_session, make_vault
):
    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=SimpleNamespace(access_token="esto no es JSON")),
    )
    provider = await get_tenant_travel_provider(ctx)
    assert isinstance(provider, StubTravelProvider)


async def test_get_tenant_travel_provider_falta_api_secret_cae_a_stub(
    make_ctx, make_session, make_vault
):
    bundle = SimpleNamespace(access_token=json.dumps({"api_key": "solo-key-sin-secret"}))
    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=bundle),
    )
    provider = await get_tenant_travel_provider(ctx)
    assert isinstance(provider, StubTravelProvider)


async def test_get_tenant_travel_provider_usa_la_credencial_del_tenant(
    make_ctx, make_session, make_vault
):
    cuenta_id = "11111111-1111-1111-1111-111111111111"
    bundle = SimpleNamespace(
        access_token=json.dumps({"api_key": "key-del-tenant", "api_secret": "secret-del-tenant"})
    )
    session = make_session([[{"id": cuenta_id}]])
    vault = make_vault(bundle=bundle)
    ctx = make_ctx(session=session, vault=vault)

    provider = await get_tenant_travel_provider(ctx)

    assert isinstance(provider, AmadeusClient)
    assert provider.environment == "test"  # default cuando el tenant no fijó 'environment'
    assert vault.llamadas == [(ctx.tenant_id, cuenta_id)]


async def test_get_tenant_travel_provider_respeta_environment_production_del_tenant(
    make_ctx, make_session, make_vault
):
    bundle = SimpleNamespace(
        access_token=json.dumps(
            {"api_key": "k", "api_secret": "s", "environment": "production"}
        )
    )
    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=bundle),
    )

    provider = await get_tenant_travel_provider(ctx)

    assert isinstance(provider, AmadeusClient)
    assert provider.environment == "production"


async def test_get_tenant_travel_provider_environment_invalido_cae_a_test(
    make_ctx, make_session, make_vault
):
    bundle = SimpleNamespace(
        access_token=json.dumps({"api_key": "k", "api_secret": "s", "environment": "algo-raro"})
    )
    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=bundle),
    )

    provider = await get_tenant_travel_provider(ctx)

    assert isinstance(provider, AmadeusClient)
    assert provider.environment == "test"


async def test_get_tenant_travel_provider_nunca_cae_a_una_credencial_de_plataforma(
    make_ctx, fake_settings
):
    """No existe ningún `AMADEUS_*` de plataforma que este resolver pueda usar —
    `settings` con cualquier contenido nunca afecta el resultado sin vault."""
    ctx = make_ctx(settings=fake_settings(AMADEUS_API_KEY="no-deberia-usarse-nunca"))
    provider = await get_tenant_travel_provider(ctx)
    assert isinstance(provider, StubTravelProvider)


# ---------------------------------------------------------------------------
# get_tenant_tracking_provider (mismo criterio exacto, connector_key "tracking")
# ---------------------------------------------------------------------------


async def test_get_tenant_tracking_provider_sin_vault_cae_a_stub(make_ctx):
    ctx = make_ctx()
    provider = await get_tenant_tracking_provider(ctx)
    assert isinstance(provider, StubTrackingProvider)


async def test_get_tenant_tracking_provider_sin_cuenta_conectada_cae_a_stub(
    make_ctx, make_session, make_vault, caplog
):
    ctx = make_ctx(session=make_session([[]]), vault=make_vault())
    with caplog.at_level("WARNING"):
        provider = await get_tenant_tracking_provider(ctx)
    assert isinstance(provider, StubTrackingProvider)
    assert "PUT /v1/viajes/rastreo/credentials" in caplog.text


async def test_get_tenant_tracking_provider_filtra_por_connector_key_tracking(
    make_ctx, make_session, make_vault
):
    session = make_session([[]])
    ctx = make_ctx(session=session, vault=make_vault())

    await get_tenant_tracking_provider(ctx)

    assert session.llamadas[0][1]["connector_key"] == TRACKING_CONNECTOR_KEY == "tracking"


async def test_get_tenant_tracking_provider_vault_revienta_cae_a_stub(make_ctx, make_session):
    class _VaultQueRevienta:
        async def get(self, tenant_id: Any, connector_account_id: Any) -> Any:
            raise RuntimeError("vault caído")

    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=_VaultQueRevienta(),
    )
    provider = await get_tenant_tracking_provider(ctx)
    assert isinstance(provider, StubTrackingProvider)


async def test_get_tenant_tracking_provider_json_corrupto_cae_a_stub(
    make_ctx, make_session, make_vault
):
    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=SimpleNamespace(access_token="no es JSON")),
    )
    provider = await get_tenant_tracking_provider(ctx)
    assert isinstance(provider, StubTrackingProvider)


async def test_get_tenant_tracking_provider_falta_api_key_cae_a_stub(
    make_ctx, make_session, make_vault
):
    bundle = SimpleNamespace(access_token=json.dumps({}))
    ctx = make_ctx(
        session=make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]]),
        vault=make_vault(bundle=bundle),
    )
    provider = await get_tenant_tracking_provider(ctx)
    assert isinstance(provider, StubTrackingProvider)


async def test_get_tenant_tracking_provider_usa_la_credencial_del_tenant(
    make_ctx, make_session, make_vault
):
    cuenta_id = "22222222-2222-2222-2222-222222222222"
    bundle = SimpleNamespace(access_token=json.dumps({"api_key": "key-del-tenant"}))
    session = make_session([[{"id": cuenta_id}]])
    vault = make_vault(bundle=bundle)
    ctx = make_ctx(session=session, vault=vault)

    provider = await get_tenant_tracking_provider(ctx)

    assert isinstance(provider, AfterShipClient)
    assert vault.llamadas == [(ctx.tenant_id, cuenta_id)]


async def test_get_tenant_tracking_provider_nunca_cae_a_una_credencial_de_plataforma(
    make_ctx, fake_settings
):
    ctx = make_ctx(settings=fake_settings(AFTERSHIP_API_KEY="no-deberia-usarse-nunca"))
    provider = await get_tenant_tracking_provider(ctx)
    assert isinstance(provider, StubTrackingProvider)
