"""Tests de `edecan_messaging._creds.resolver_credenciales` (`ARCHITECTURE.md` §10.15)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from edecan_messaging._creds import (
    CONNECTOR_KEYS,
    MessagingNotConnectedError,
    display_name,
    resolver_credenciales,
)


def test_connector_keys_pinned():
    assert CONNECTOR_KEYS == ("telegram", "discord", "slack", "whatsapp")


def test_display_name_conocidas_y_desconocida():
    assert display_name("telegram") == "Telegram"
    assert display_name("discord") == "Discord"
    assert display_name("slack") == "Slack"
    assert display_name("whatsapp") == "WhatsApp"
    assert display_name("otra-cosa") == "otra-cosa"


async def test_plataforma_desconocida_lanza_value_error(make_ctx):
    # "signal" (no "whatsapp"): Signal está excluida permanentemente por no
    # tener API oficial (`docs/mensajeria.md`, "Exclusiones"), así que sigue
    # siendo un buen ejemplo estable de connector_key desconocido — a
    # diferencia de "whatsapp", que WP-V3-13 promovió a connector_key real
    # (ver `CONNECTOR_KEYS` arriba).
    ctx = make_ctx()
    with pytest.raises(ValueError):
        await resolver_credenciales(ctx, "signal")


async def test_sin_cuenta_conectada_lanza_not_connected(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    with pytest.raises(MessagingNotConnectedError) as exc_info:
        await resolver_credenciales(ctx, "telegram")
    assert "Telegram" in str(exc_info.value)
    assert exc_info.value.plataforma == "telegram"


async def test_cuenta_conectada_pero_vault_sin_bundle_lanza_not_connected(
    make_ctx, make_session, make_vault
):
    fila_cuenta = {"id": "acc-1"}
    ctx = make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=None))
    with pytest.raises(MessagingNotConnectedError):
        await resolver_credenciales(ctx, "discord")


async def test_bundle_sin_access_token_lanza_not_connected(make_ctx, make_session, make_vault):
    fila_cuenta = {"id": "acc-1"}
    bundle_vacio = SimpleNamespace(access_token="")
    ctx = make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=bundle_vacio))
    with pytest.raises(MessagingNotConnectedError):
        await resolver_credenciales(ctx, "slack")


async def test_credenciales_resueltas_ok(make_ctx, make_session, make_vault):
    fila_cuenta = {"id": "acc-42"}
    bundle = SimpleNamespace(access_token="xoxb-token-42")
    session = make_session([[fila_cuenta]])
    vault = make_vault(bundle=bundle)
    ctx = make_ctx(session=session, vault=vault)

    credencial = await resolver_credenciales(ctx, "slack")

    assert credencial.connector_account_id == "acc-42"
    assert credencial.access_token == "xoxb-token-42"
    assert credencial.scopes == ()  # Slack no guarda scopes en el bundle.
    # La consulta debe filtrar por el connector_key pedido.
    _, params = session.llamadas[0]
    assert params["connector_key"] == "slack"
    assert params["tenant_id"] == str(ctx.tenant_id)
    # Y el vault se consulta con el connector_account_id resuelto.
    assert vault.llamadas == [(ctx.tenant_id, "acc-42")]


async def test_credenciales_whatsapp_incluyen_phone_number_id_en_scopes(
    make_ctx, make_session, make_vault
):
    """WhatsApp (WP-V3-13, ver docstring del módulo) es el único de los
    cuatro conectores cuyo `TokenBundle.scopes` trae un dato que `tools.py`
    necesita (`phone_number_id`) — `resolver_credenciales` debe propagarlo a
    `CredencialMensajeria.scopes` tal cual."""
    fila_cuenta = {"id": "acc-wa-1"}
    bundle = SimpleNamespace(access_token="token-permanente-de-meta", scopes=["109876543210987"])
    ctx = make_ctx(session=make_session([[fila_cuenta]]), vault=make_vault(bundle=bundle))

    credencial = await resolver_credenciales(ctx, "whatsapp")

    assert credencial.access_token == "token-permanente-de-meta"
    assert credencial.scopes == ("109876543210987",)
