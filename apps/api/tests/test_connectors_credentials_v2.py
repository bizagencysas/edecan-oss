"""`PUT /v1/connectors/{key}/credentials` para bots de mensajería sin OAuth
(`key` ∈ {"telegram", "discord"}) y el catálogo de `GET /v1/connectors`
generalizado (`ARCHITECTURE.md` §10.12; ROADMAP_V2.md §7.7, WP-V2-05).

Complementa a `test_connectors.py` (que sigue siendo la fuente de verdad de
los tests de Twilio/OAuth) — este archivo SOLO agrega tests nuevos, no
duplica ni reescribe los existentes. Mismas convenciones que
`test_connectors.py`: `client`/`fake_repo`/`auth_headers` de `conftest.py`,
`FakeVault` local (doble mínimo de `edecan_db.vault.TokenVault`).
"""

from __future__ import annotations

import uuid

from conftest import auth_headers
from edecan_schemas import TokenBundle

import edecan_api.deps as edecan_deps
import edecan_api.routers.connectors as connectors_module


class FakeVault:
    """Doble de `edecan_db.vault.TokenVault`: `get`/`put` en memoria keyed por
    `(tenant_id, account_id)` — mismo fake que usa `test_connectors.py`,
    necesario desde que `authorize`/`callback` también LEEN (no solo
    escriben) la app OAuth propia del tenant."""

    def __init__(self, *args, **kwargs) -> None:
        self.store: dict[tuple[uuid.UUID, uuid.UUID], TokenBundle] = {}
        self.puts: list[tuple[uuid.UUID, uuid.UUID, TokenBundle]] = []

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self.store[(tenant_id, account_id)] = bundle
        self.puts.append((tenant_id, account_id, bundle))

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self.store.get((tenant_id, account_id))


def _headers(**overrides):
    return auth_headers(
        user_id=overrides.pop("user_id", uuid.uuid4()),
        tenant_id=overrides.pop("tenant_id", uuid.uuid4()),
        plan_key=overrides.pop("plan_key", "hosted_pro"),
    )


# ---------------------------------------------------------------------------
# PUT /v1/connectors/{key}/credentials — éxito
# ---------------------------------------------------------------------------


async def test_connect_telegram_success_stores_credentials(client, app, fake_repo) -> None:
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/connectors/telegram/credentials",
        json={"bot_token": "123456789:AAHtsOm5POK_bl2ZzP1zN1Y1YRXhSHwWMTk"},
        headers=headers,
    )

    assert response.status_code == 204
    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1
    assert accounts[0]["connector_key"] == "telegram"
    assert accounts[0]["display_name"] == "Telegram"
    assert accounts[0]["scopes"] == []

    assert len(fake_vault.puts) == 1
    stored_tenant_id, stored_account_id, stored_bundle = fake_vault.puts[0]
    assert stored_tenant_id == tenant_id
    assert stored_account_id == accounts[0]["id"]
    assert stored_bundle.access_token == "123456789:AAHtsOm5POK_bl2ZzP1zN1Y1YRXhSHwWMTk"

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "connectors.connected" in actions


async def test_connect_discord_success_stores_credentials(client, app, fake_repo) -> None:
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/connectors/discord/credentials",
        json={"bot_token": "MTIzNDU2Nzg5MDEyMzQ1Njc4.GhIJKl.MnOpQrStUvWxYz0123456789"},
        headers=headers,
    )

    assert response.status_code == 204
    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1
    assert accounts[0]["connector_key"] == "discord"
    assert accounts[0]["display_name"] == "Discord"


async def test_connect_bot_token_requires_authentication(client) -> None:
    response = await client.put(
        "/v1/connectors/telegram/credentials", json={"bot_token": "a-token-with-enough-length"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PUT /v1/connectors/{key}/credentials — validación
# ---------------------------------------------------------------------------


async def test_connect_bot_token_rejects_unknown_key(client) -> None:
    # "signal" (no "whatsapp"): WP-V3-13 promovió "whatsapp" a conector real
    # con su propia ruta fija (`PUT /v1/connectors/whatsapp/credentials`, ver
    # `test_connectors_whatsapp.py`), así que ya no sirve como ejemplo de
    # "key desconocida" para la ruta GENÉRICA — de hecho, un PUT con este
    # payload (forma de `BotTokenCredentialsIn`) contra esa URL ahora
    # devuelve 422 (`WhatsAppCredentialsIn` exige `access_token`/
    # `phone_number_id`), no 404; ver
    # `test_connect_whatsapp_route_is_not_shadowed_by_generic_bot_token_route`.
    # "signal" sigue excluida permanentemente (`docs/mensajeria.md`,
    # "Exclusiones") y es el mismo reemplazo que ya usan
    # `packages/messaging/tests/{test_tools,test_creds}.py`.
    headers = _headers()
    response = await client.put(
        "/v1/connectors/signal/credentials",
        json={"bot_token": "a-token-with-enough-length"},
        headers=headers,
    )
    assert response.status_code == 404


async def test_connect_bot_token_rejects_empty_token(client) -> None:
    headers = _headers()
    response = await client.put(
        "/v1/connectors/telegram/credentials", json={"bot_token": "   "}, headers=headers
    )
    assert response.status_code == 400


async def test_connect_bot_token_rejects_too_short_token(client) -> None:
    headers = _headers()
    response = await client.put(
        "/v1/connectors/discord/credentials", json={"bot_token": "corto"}, headers=headers
    )
    assert response.status_code == 400


async def test_connect_bot_token_does_not_shadow_twilio_route(client) -> None:
    """Regresión del ordenamiento de rutas descrito en el docstring del
    router: un payload con la forma de Twilio (no de `BotTokenCredentialsIn`)
    contra `/v1/connectors/twilio/credentials` debe seguir resolviéndose por
    `connect_twilio` (422 por payload inválido para ESE modelo — nunca 204,
    que sería lo que devolvería `connect_bot_token` con un `bot_token`
    cualquiera)."""
    headers = _headers()
    response = await client.put(
        "/v1/connectors/twilio/credentials",
        json={"bot_token": "esto-no-es-un-payload-de-twilio"},
        headers=headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /v1/connectors — catálogo generalizado
# ---------------------------------------------------------------------------


async def test_list_connectors_includes_telegram_discord_and_slack(client) -> None:
    headers = _headers()
    response = await client.get("/v1/connectors", headers=headers)

    assert response.status_code == 200
    by_key = {entry["key"]: entry for entry in response.json()}
    assert "telegram" in by_key
    assert "discord" in by_key
    assert "slack" in by_key
    assert by_key["telegram"]["display_name"] == "Telegram"
    assert by_key["discord"]["display_name"] == "Discord"
    assert by_key["telegram"]["accounts"] == []


async def test_list_connectors_shows_connected_bot_token_account(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="telegram",
        external_account_id="hash-abc",
        display_name="Telegram",
        scopes=[],
    )

    response = await client.get("/v1/connectors", headers=headers)
    by_key = {entry["key"]: entry for entry in response.json()}
    assert len(by_key["telegram"]["accounts"]) == 1


# ---------------------------------------------------------------------------
# DELETE /v1/connectors/{key}/{account_id} — funciona para telegram/discord
# ---------------------------------------------------------------------------


async def test_disconnect_telegram_account(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="telegram",
        external_account_id="hash-abc",
        display_name="Telegram",
        scopes=[],
    )

    response = await client.delete(f"/v1/connectors/telegram/{account['id']}", headers=headers)
    assert response.status_code == 204
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []


async def test_disconnect_discord_account(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="discord",
        external_account_id="hash-def",
        display_name="Discord",
        scopes=[],
    )

    response = await client.delete(f"/v1/connectors/discord/{account['id']}", headers=headers)
    assert response.status_code == 204
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []


# ---------------------------------------------------------------------------
# Slack (OAuth) — ya sale gratis del flujo genérico authorize/callback
# ---------------------------------------------------------------------------


async def test_authorize_slack_returns_url_with_comma_separated_scope(client, app) -> None:
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    put_resp = await client.put(
        "/v1/connectors/slack/app-credentials",
        json={"client_id": "test-slack-client-id", "client_secret": "test-slack-client-secret"},
        headers=headers,
    )
    assert put_resp.status_code == 204

    response = await client.get("/v1/connectors/slack/authorize", headers=headers)

    assert response.status_code == 200
    url = response.json()["url"]
    assert url.startswith("https://slack.com/oauth/v2/authorize")
    assert "client_id=test-slack-client-id" in url
    assert "state=" in url


async def test_callback_success_stores_slack_connector_account(
    client, app, fake_repo, test_settings, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    state = connectors_module._create_state_token(
        tenant_id=tenant_id, key="slack", secret=test_settings.JWT_SECRET
    )
    bundle = TokenBundle(access_token="xoxb-slack-1", scopes=["chat:write"])

    async def fake_exchange_code(
        code, redirect_uri, http, *, client_id, client_secret, code_verifier=None
    ):
        assert code == "the-code"
        assert client_id == "test-slack-client-id"
        assert client_secret == "test-slack-client-secret"
        return bundle

    monkeypatch.setattr(connectors_module.CONNECTORS["slack"], "exchange_code", fake_exchange_code)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_get_session(tid):
        assert tid == tenant_id
        yield object()

    fake_vault = FakeVault()
    monkeypatch.setattr(connectors_module, "get_session", fake_get_session)
    monkeypatch.setattr(connectors_module, "SqlRepo", lambda session: fake_repo)
    monkeypatch.setattr(connectors_module, "TokenVault", lambda session, key_provider: fake_vault)
    monkeypatch.setattr(connectors_module, "build_key_provider", lambda settings: None)

    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    headers = _headers(tenant_id=tenant_id)
    put_resp = await client.put(
        "/v1/connectors/slack/app-credentials",
        json={"client_id": "test-slack-client-id", "client_secret": "test-slack-client-secret"},
        headers=headers,
    )
    assert put_resp.status_code == 204

    response = await client.get(
        "/v1/connectors/slack/callback",
        params={"code": "the-code", "state": state},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    # La fila de config de app OAuth (`slack__app_config`, sembrada arriba)
    # NUNCA debe confundirse con la cuenta real recién conectada (`slack`).
    slack_accounts = [a for a in accounts if a["connector_key"] == "slack"]
    assert len(slack_accounts) == 1
    assert len(fake_vault.puts) == 2  # 1: el client_secret sembrado, 2: el TokenBundle real
