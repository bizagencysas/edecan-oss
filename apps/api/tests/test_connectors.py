"""`/v1/connectors/*` — OAuth de conectores oficiales + credenciales Twilio
(ARCHITECTURE.md §10.12, §10.8, §10.10).

`callback` construye su propia `SqlRepo`/`TokenVault` sobre
`edecan_db.session.get_session(tenant_id)` directo en vez de recibirlos por
`Depends(...)`. Igual que `test_conversations.py` sustituye `Agent` con
`monkeypatch.setattr(...)` sobre el símbolo ya importado en el router, aquí
se sustituyen `get_session`, `SqlRepo`, `TokenVault` y `build_key_provider`
importados en `edecan_api.routers.connectors` para poder probar el flujo
completo sin Postgres real ni credenciales reales de ningún proveedor OAuth.

`callback` es la única ruta del router que NO exige `Authorization: Bearer`
(el navegador del usuario llega ahí redirigido por el proveedor OAuth, sin
poder adjuntar el header — ver docstring del router y `docs/api.md`), así que
sus tests no mandan `auth_headers(...)`; `test_callback_does_not_require_authentication`
es el guardarraíl explícito de esa propiedad.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from conftest import auth_headers
from edecan_connectors.base import ConnectorError
from edecan_schemas import TokenBundle
from fastapi import HTTPException

import edecan_api.deps as edecan_deps
import edecan_api.routers.connectors as connectors_module


class FakeVault:
    """Doble de `edecan_db.vault.TokenVault`: `get`/`put` en memoria, keyed
    por `(tenant_id, account_id)` -- necesario desde que `authorize`/`callback`
    también leen (no solo escriben) la app OAuth propia del tenant vía
    `get_oauth_app_credentials`."""

    def __init__(self, *args, **kwargs) -> None:
        self.store: dict[tuple[uuid.UUID, uuid.UUID], TokenBundle] = {}
        self.puts: list[tuple[uuid.UUID, uuid.UUID, TokenBundle]] = []

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self.store[(tenant_id, account_id)] = bundle
        self.puts.append((tenant_id, account_id, bundle))

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self.store.get((tenant_id, account_id))


# ---------------------------------------------------------------------------
# GET /v1/connectors
# ---------------------------------------------------------------------------


async def test_list_connectors_includes_oauth_catalog_and_twilio(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.get("/v1/connectors", headers=headers)

    assert response.status_code == 200
    by_key = {entry["key"]: entry for entry in response.json()}
    assert "google" in by_key
    assert "microsoft" in by_key
    assert "twilio" in by_key
    assert by_key["google"]["accounts"] == []
    assert by_key["twilio"]["display_name"]


async def test_list_connectors_shows_connected_accounts(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="google",
        external_account_id="acc-1",
        display_name="Google (Gmail + Calendar)",
        scopes=["gmail.readonly"],
    )

    response = await client.get("/v1/connectors", headers=headers)
    by_key = {entry["key"]: entry for entry in response.json()}
    assert len(by_key["google"]["accounts"]) == 1
    assert by_key["google"]["accounts"][0]["external_account_id"] == "acc-1"


# ---------------------------------------------------------------------------
# GET /v1/connectors/{key}/authorize
# ---------------------------------------------------------------------------


async def test_authorize_unknown_connector_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.get("/v1/connectors/no-existe/authorize", headers=headers)
    assert response.status_code == 404


async def test_authorize_known_connector_returns_url_with_state(client, app) -> None:
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")

    put_resp = await client.put(
        "/v1/connectors/google/app-credentials",
        json={"client_id": "test-google-client-id", "client_secret": "test-google-client-secret"},
        headers=headers,
    )
    assert put_resp.status_code == 204

    response = await client.get("/v1/connectors/google/authorize", headers=headers)

    assert response.status_code == 200
    url = response.json()["url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "state=" in url
    assert "client_id=test-google-client-id" in url


async def test_authorize_requires_authentication(client) -> None:
    response = await client.get("/v1/connectors/google/authorize")
    assert response.status_code == 401


async def test_authorize_without_app_credentials_returns_400_not_500(client) -> None:
    """Bug real reproducido en la app de escritorio empaquetada: si el tenant
    todavía no pegó su propia app OAuth (`PUT /v1/connectors/google/
    app-credentials`, el caso normal de una instancia self-hosteada recién
    instalada), `authorize` debe rechazar con un mensaje accionable ANTES de
    intentar construir la URL -- nunca un 500 opaco."""
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")

    response = await client.get("/v1/connectors/google/authorize", headers=headers)

    assert response.status_code == 400
    assert "google" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /v1/connectors/{key}/callback
# ---------------------------------------------------------------------------


async def test_callback_does_not_require_authentication(client) -> None:
    """Guardarraíl del bug real que arregla este fix: el `APIRouter(...)` de
    `edecan_api.routers.connectors` declaraba `dependencies=[Depends(rate_limit)]`
    a nivel de router, y `rate_limit` exige `Depends(get_current_user)` — eso
    aplicaba a TODAS las rutas del router, `callback` incluido, y como el
    navegador la visita sin `Authorization: Bearer` (llega redirigido por el
    proveedor OAuth), la ruta siempre devolvía 401 antes de llegar a su
    lógica, rompiendo el flujo real de conexión de conectores. Sin headers,
    debe fallar por `state` inválido (400), nunca por falta de auth (401).
    """
    response = await client.get(
        "/v1/connectors/google/callback",
        params={"code": "abc", "state": "no-es-un-state-valido"},
    )
    assert response.status_code == 400


async def test_callback_unknown_connector_returns_404(client) -> None:
    response = await client.get(
        "/v1/connectors/no-existe/callback",
        params={"code": "abc", "state": "xyz"},
    )
    assert response.status_code == 404


async def test_callback_invalid_state_returns_400(client) -> None:
    response = await client.get(
        "/v1/connectors/google/callback",
        params={"code": "abc", "state": "no-es-un-state-valido"},
    )
    assert response.status_code == 400


async def test_callback_expired_state_returns_400(client, monkeypatch, test_settings) -> None:
    # TTL negativo: el `state` nace expirado, sin tener que fabricar el
    # binario a mano ni esperar los 600s reales.
    monkeypatch.setattr(connectors_module, "STATE_TTL_SECONDS", -1)
    tenant_id = uuid.uuid4()
    state = connectors_module._create_state_token(
        tenant_id=tenant_id, key="google", secret=test_settings.JWT_SECRET
    )

    response = await client.get(
        "/v1/connectors/google/callback",
        params={"code": "abc", "state": state},
    )
    assert response.status_code == 400


async def test_callback_state_from_other_connector_is_rejected(client, test_settings) -> None:
    tenant_id = uuid.uuid4()
    state = connectors_module._create_state_token(
        tenant_id=tenant_id, key="microsoft", secret=test_settings.JWT_SECRET
    )

    response = await client.get(
        "/v1/connectors/google/callback",
        params={"code": "abc", "state": state},
    )
    assert response.status_code == 400


async def test_callback_without_app_credentials_returns_400_not_500(
    client, fake_repo, test_settings, monkeypatch
) -> None:
    """Mismo motivo que `test_authorize_without_app_credentials_returns_400_not_500`:
    si nadie pegó la app OAuth de Google para este tenant, `callback` debe
    rechazar con un mensaje accionable antes de intentar canjear el code."""
    tenant_id = uuid.uuid4()
    state = connectors_module._create_state_token(
        tenant_id=tenant_id, key="google", secret=test_settings.JWT_SECRET
    )

    @asynccontextmanager
    async def fake_get_session(tid):
        yield object()

    monkeypatch.setattr(connectors_module, "get_session", fake_get_session)
    monkeypatch.setattr(connectors_module, "SqlRepo", lambda session: fake_repo)
    monkeypatch.setattr(connectors_module, "TokenVault", lambda session, key_provider: FakeVault())
    monkeypatch.setattr(connectors_module, "build_key_provider", lambda settings: None)

    response = await client.get(
        "/v1/connectors/google/callback",
        params={"code": "the-code", "state": state},
    )

    assert response.status_code == 400
    assert "google" in response.json()["detail"].lower()


async def _seed_app_credentials(
    client,
    app,
    fake_vault: FakeVault,
    tenant_id: uuid.UUID,
    key: str,
    client_id: str,
    client_secret: str,
) -> None:
    """Pega la app OAuth propia del tenant vía el endpoint real (`PUT
    /v1/connectors/{key}/app-credentials`) usando `fake_vault` -- deja
    `callback`/`authorize` listos para encontrarla, ya que ambos comparten la
    misma instancia de `fake_repo`/`fake_vault` monkeypatcheada en el router."""
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")
    response = await client.put(
        f"/v1/connectors/{key}/app-credentials",
        json={"client_id": client_id, "client_secret": client_secret},
        headers=headers,
    )
    assert response.status_code == 204


async def test_callback_connector_error_returns_400_not_500(
    client, app, fake_repo, test_settings, monkeypatch
) -> None:
    """Mismo motivo que `test_authorize_without_app_credentials_returns_400_not_500`
    pero del lado de `exchange_code` (p. ej. el proveedor rechazó el `code`, o
    la app del tenant quedó mal configurada): antes de este fix,
    `ConnectorError` se propagaba sin capturar y `callback` respondía 500 en
    vez de un error accionable."""
    tenant_id = uuid.uuid4()
    state = connectors_module._create_state_token(
        tenant_id=tenant_id, key="google", secret=test_settings.JWT_SECRET
    )

    fake_vault = FakeVault()

    @asynccontextmanager
    async def fake_get_session(tid):
        yield object()

    monkeypatch.setattr(connectors_module, "get_session", fake_get_session)
    monkeypatch.setattr(connectors_module, "SqlRepo", lambda session: fake_repo)
    monkeypatch.setattr(connectors_module, "TokenVault", lambda session, key_provider: fake_vault)
    monkeypatch.setattr(connectors_module, "build_key_provider", lambda settings: None)

    await _seed_app_credentials(client, app, fake_vault, tenant_id, "google", "gid", "gsecret")

    async def fake_exchange_code_falla(
        code, redirect_uri, http, *, client_id, client_secret, code_verifier=None
    ):
        raise ConnectorError("El proveedor rechazó el code.")

    monkeypatch.setattr(
        connectors_module.CONNECTORS["google"], "exchange_code", fake_exchange_code_falla
    )

    response = await client.get(
        "/v1/connectors/google/callback",
        params={"code": "the-code", "state": state},
    )

    assert response.status_code == 400
    assert "google" in response.json()["detail"]


async def test_callback_success_stores_connector_account_and_token(
    client, app, fake_repo, test_settings, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    state = connectors_module._create_state_token(
        tenant_id=tenant_id, key="google", secret=test_settings.JWT_SECRET
    )

    fake_vault = FakeVault()

    @asynccontextmanager
    async def fake_get_session(tid):
        assert tid == tenant_id
        yield object()

    monkeypatch.setattr(connectors_module, "get_session", fake_get_session)
    monkeypatch.setattr(connectors_module, "SqlRepo", lambda session: fake_repo)
    monkeypatch.setattr(connectors_module, "TokenVault", lambda session, key_provider: fake_vault)
    monkeypatch.setattr(connectors_module, "build_key_provider", lambda settings: None)

    await _seed_app_credentials(client, app, fake_vault, tenant_id, "google", "gid", "gsecret")

    bundle = TokenBundle(access_token="at_123", refresh_token="rt_123", scopes=["gmail.readonly"])

    async def fake_exchange_code(
        code, redirect_uri, http, *, client_id, client_secret, code_verifier=None
    ):
        assert code == "the-code"
        assert code_verifier == state
        assert client_id == "gid"
        assert client_secret == "gsecret"
        return bundle

    monkeypatch.setattr(connectors_module.CONNECTORS["google"], "exchange_code", fake_exchange_code)

    response = await client.get(
        "/v1/connectors/google/callback",
        params={"code": "the-code", "state": state},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "http://localhost:3000/app/conectores?ok=1"

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    # La fila de config de app OAuth (`google__app_config`, sembrada por
    # `_seed_app_credentials`) NUNCA debe confundirse con la cuenta real recién
    # conectada (`google`) -- se filtran por separado.
    google_accounts = [a for a in accounts if a["connector_key"] == "google"]
    assert len(google_accounts) == 1
    assert google_accounts[0]["scopes"] == ["gmail.readonly"]

    stored_tenant_id, stored_account_id, stored_bundle = fake_vault.puts[-1]
    assert stored_tenant_id == tenant_id
    assert stored_account_id == google_accounts[0]["id"]
    assert stored_bundle is bundle

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "connectors.connected" in actions


# ---------------------------------------------------------------------------
# PUT / DELETE /v1/connectors/{key}/app-credentials
# ---------------------------------------------------------------------------


async def test_put_app_credentials_unknown_connector_returns_404(client, app) -> None:
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.put(
        "/v1/connectors/no-existe/app-credentials", json={"client_id": "x"}, headers=headers
    )
    assert response.status_code == 404


async def test_put_app_credentials_rejects_empty_client_id(client, app) -> None:
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.put(
        "/v1/connectors/google/app-credentials", json={"client_id": "   "}, headers=headers
    )
    assert response.status_code == 400


async def test_put_app_credentials_stores_client_id_and_secret(client, app, fake_repo) -> None:
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")

    response = await client.put(
        "/v1/connectors/google/app-credentials",
        json={"client_id": "gid-123", "client_secret": "gsecret-456"},
        headers=headers,
    )

    assert response.status_code == 204
    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1
    assert accounts[0]["connector_key"] == "google__app_config"
    assert accounts[0]["external_account_id"] == "gid-123"

    stored_tenant_id, _stored_account_id, stored_bundle = fake_vault.puts[-1]
    assert stored_tenant_id == tenant_id
    assert stored_bundle.access_token == "gsecret-456"

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "connectors.app_credentials_set" in actions


async def test_put_app_credentials_allows_optional_secret(client, app) -> None:
    """X con apps públicas (PKCE puro) no exige `client_secret`."""
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")

    response = await client.put(
        "/v1/connectors/x/app-credentials", json={"client_id": "xid-123"}, headers=headers
    )
    assert response.status_code == 204


async def test_put_app_credentials_replaces_existing_config(client, app, fake_repo) -> None:
    """Reconfigurar con un `client_id` distinto reemplaza la fila entera (no
    deja `external_account_id` desactualizado) -- ver docstring de
    `put_oauth_app_credentials`."""
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")

    await client.put(
        "/v1/connectors/google/app-credentials",
        json={"client_id": "gid-viejo", "client_secret": "s1"},
        headers=headers,
    )
    response = await client.put(
        "/v1/connectors/google/app-credentials",
        json={"client_id": "gid-nuevo", "client_secret": "s2"},
        headers=headers,
    )
    assert response.status_code == 204

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    matches = [a for a in accounts if a["connector_key"] == "google__app_config"]
    assert len(matches) == 1
    assert matches[0]["external_account_id"] == "gid-nuevo"


async def test_delete_app_credentials_unknown_connector_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.delete("/v1/connectors/no-existe/app-credentials", headers=headers)
    assert response.status_code == 404


async def test_delete_app_credentials_is_idempotent_when_not_configured(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.delete("/v1/connectors/google/app-credentials", headers=headers)
    assert response.status_code == 204


async def test_delete_app_credentials_removes_config(client, app, fake_repo) -> None:
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")
    await client.put(
        "/v1/connectors/google/app-credentials",
        json={"client_id": "gid", "client_secret": "gsecret"},
        headers=headers,
    )

    response = await client.delete("/v1/connectors/google/app-credentials", headers=headers)
    assert response.status_code == 204

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert accounts == []


async def test_list_connectors_reflects_app_configured_status(client, app) -> None:
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")

    before = await client.get("/v1/connectors", headers=headers)
    by_key_before = {entry["key"]: entry for entry in before.json()}
    assert by_key_before["google"]["app_configured"] is False
    assert by_key_before["google"]["app_client_id_masked"] is None

    await client.put(
        "/v1/connectors/google/app-credentials",
        json={"client_id": "gid-1234567890", "client_secret": "gsecret"},
        headers=headers,
    )

    after = await client.get("/v1/connectors", headers=headers)
    by_key_after = {entry["key"]: entry for entry in after.json()}
    assert by_key_after["google"]["app_configured"] is True
    assert by_key_after["google"]["app_client_id_masked"] == "gid-1234…7890"
    # La fila de config de app NUNCA debe aparecer como si fuera una "cuenta
    # conectada" de verdad.
    assert by_key_after["google"]["accounts"] == []


# ---------------------------------------------------------------------------
# PUT /v1/connectors/twilio/credentials
# ---------------------------------------------------------------------------


def _twilio_payload(**overrides):
    payload = {
        "account_sid": "AC" + "a" * 32,
        "auth_token": "b" * 32,
        "phone_number": "+525512345678",
    }
    payload.update(overrides)
    return payload


def _mock_twilio_verification_ok(monkeypatch) -> None:
    """Sustituye `_verify_twilio_phone_ownership` por un no-op que siempre
    "verifica" (no pega a la red real de Twilio). Los tests de éxito de
    `connect_twilio` (y los de cuota, que llegan a este punto) usan esto para
    no depender de credenciales reales de Twilio; `_verify_twilio_phone_ownership`
    en sí se prueba por separado con `httpx.MockTransport`
    (`test_verify_twilio_phone_ownership_*` más abajo).
    """

    async def fake_verify(account_sid, auth_token, phone_number, *, http_client):
        return None

    monkeypatch.setattr(connectors_module, "_verify_twilio_phone_ownership", fake_verify)


async def test_connect_twilio_rejects_invalid_account_sid(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.put(
        "/v1/connectors/twilio/credentials",
        json=_twilio_payload(account_sid="no-empieza-con-AC"),
        headers=headers,
    )
    assert response.status_code == 400


async def test_connect_twilio_rejects_invalid_auth_token(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.put(
        "/v1/connectors/twilio/credentials",
        json=_twilio_payload(auth_token="muy-corto"),
        headers=headers,
    )
    assert response.status_code == 400


async def test_connect_twilio_rejects_invalid_phone_number(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.put(
        "/v1/connectors/twilio/credentials",
        json=_twilio_payload(phone_number="5512345678"),  # falta el "+"
        headers=headers,
    )
    assert response.status_code == 400


async def test_connect_twilio_success_stores_credentials(
    client, app, fake_repo, monkeypatch
) -> None:
    _mock_twilio_verification_ok(monkeypatch)
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault

    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")

    response = await client.put(
        "/v1/connectors/twilio/credentials", json=_twilio_payload(), headers=headers
    )

    assert response.status_code == 204
    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1
    assert accounts[0]["connector_key"] == "twilio"
    assert accounts[0]["external_account_id"] == "+525512345678"

    assert len(fake_vault.puts) == 1
    stored_tenant_id, _account_id, stored_bundle = fake_vault.puts[0]
    assert stored_tenant_id == tenant_id
    assert stored_bundle.access_token == "b" * 32
    assert stored_bundle.scopes == ["AC" + "a" * 32]


async def test_check_phone_number_quota_plan_huerfano_deniega_en_vez_de_ilimitado(
    fake_repo,
) -> None:
    """Regresión (barrido v7, WP-V7-08 encontró y corrigió el mismo patrón en
    `files.py`/`voice.py`; `connectors.py` quedó fuera del alcance de ese WP y
    se cierra acá, WP-V7-12): `_check_phone_number_quota` defaulteaba a
    `UNLIMITED` cuando `tenant.flags` no trae `LIMIT_PHONE_NUMBERS` --
    exactamente lo que pasa con un `plan_key` huérfano
    (`edecan_api.deps.flags_for_plan` devuelve `{}`). A nivel HTTP este caso YA
    estaba cubierto por `_require_voice_telephony` (403 antes de llegar acá,
    `FLAG_VOICE_TELEPHONY` por defecto `False` para `flags={}`) -- este test
    unitario prueba la función DIRECTAMENTE (sin pasar por ese primer gate)
    para confirmar que el segundo candado también es fail-closed, defensa en
    profundidad si algún día se reordenan los chequeos (mismo criterio que
    `test_v7_sweep_routers_restantes.py::
    test_check_voice_quota_plan_huerfano_deniega_en_vez_de_ilimitado`)."""
    from edecan_api.deps import TenantCtx
    from edecan_api.routers.connectors import _check_phone_number_quota

    tenant = TenantCtx(tenant_id=uuid.uuid4(), plan_key="plan_no_existe", flags={})

    with pytest.raises(HTTPException) as exc_info:
        await _check_phone_number_quota(fake_repo, tenant)
    assert exc_info.value.status_code == 429


async def test_connect_twilio_allows_second_number_under_higher_plan_limit(
    client, app, fake_repo, monkeypatch
) -> None:
    # hosted_business: flags[LIMIT_PHONE_NUMBERS] == 3, así que un segundo
    # número sigue permitido (a diferencia de hosted_pro, límite 1).
    _mock_twilio_verification_ok(monkeypatch)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()

    tenant_id = uuid.uuid4()
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_business"
    )
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="twilio",
        external_account_id="+525500000000",
        display_name="+525500000000",
        scopes=["AC" + "0" * 32],
    )

    response = await client.put(
        "/v1/connectors/twilio/credentials", json=_twilio_payload(), headers=headers
    )

    assert response.status_code == 204
    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 2


async def test_connect_twilio_rejects_when_ownership_verification_fails(
    client, fake_repo, monkeypatch
) -> None:
    """Hallazgo de auditoría aislamiento-multi-tenant: el formato por sí solo
    NUNCA probó que `phone_number` fuera de verdad de esa cuenta de Twilio —
    si `_verify_twilio_phone_ownership` rechaza (Twilio dice que el número no
    es de esa cuenta, o las credenciales son inválidas), no debe persistirse
    nada."""

    async def fake_verify_fails(account_sid, auth_token, phone_number, *, http_client):
        raise HTTPException(status_code=400, detail="Ese número no pertenece a la cuenta indicada.")

    monkeypatch.setattr(connectors_module, "_verify_twilio_phone_ownership", fake_verify_fails)

    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")

    response = await client.put(
        "/v1/connectors/twilio/credentials", json=_twilio_payload(), headers=headers
    )

    assert response.status_code == 400
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []


async def test_connect_twilio_rejects_when_number_claimed_by_another_tenant(
    client, fake_repo, monkeypatch
) -> None:
    """Núcleo del hallazgo: `connector_accounts` solo tenía
    `UNIQUE(tenant_id, connector_key, external_account_id)` — nada impedía
    que el tenant B reclamara el MISMO número E.164 que el tenant A ya tenía
    conectado. `_resolve_tenant_by_number` (`edecan_premium.twilio_router`)
    resuelve por `ORDER BY created_at DESC LIMIT 1`, así que ese segundo
    registro "robaba" el número — las llamadas/SMS entrantes reales de A
    empezaban a resolverse contra B."""
    _mock_twilio_verification_ok(monkeypatch)

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    numero = "+525512345678"
    await fake_repo.create_connector_account(
        tenant_id=tenant_a,
        connector_key="twilio",
        external_account_id=numero,
        display_name=numero,
        scopes=["AC" + "0" * 32],
    )

    headers_b = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_b, plan_key="hosted_pro")
    response = await client.put(
        "/v1/connectors/twilio/credentials",
        json=_twilio_payload(phone_number=numero),
        headers=headers_b,
    )

    assert response.status_code == 409
    # El número sigue siendo SOLO de tenant_a: ni se le quitó, ni tenant_b
    # consiguió una cuenta propia con él.
    assert len(await fake_repo.list_connector_accounts(tenant_id=tenant_a)) == 1
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_b) == []


async def test_connect_twilio_does_not_block_same_tenant_reclaiming_its_own_number(
    client, app, fake_repo, monkeypatch
) -> None:
    """El chequeo cruzado solo debe bloquear a OTRO tenant — no debe impedir
    que el mismo tenant vuelva a mandar sus propias credenciales para el
    número que ya tiene conectado."""
    _mock_twilio_verification_ok(monkeypatch)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()

    tenant_id = uuid.uuid4()
    numero = "+525512345678"
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="twilio",
        external_account_id=numero,
        display_name=numero,
        scopes=["AC" + "0" * 32],
    )

    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_business")
    response = await client.put(
        "/v1/connectors/twilio/credentials",
        json=_twilio_payload(phone_number=numero),
        headers=headers,
    )

    # hosted_business permite hasta 3 números, así que esto no lo bloquea la
    # cuota — el punto de este test es que el chequeo cruzado (409) no se
    # dispara contra el propio tenant.
    assert response.status_code != 409


# ---------------------------------------------------------------------------
# `_verify_twilio_phone_ownership` (función pura, sin red real — httpx.MockTransport)
# ---------------------------------------------------------------------------


async def test_verify_twilio_phone_ownership_ok_cuando_el_numero_esta_en_la_cuenta() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/2010-04-01/Accounts/ACsid/IncomingPhoneNumbers.json"
        assert request.url.params["PhoneNumber"] == "+525512345678"
        auth = request.headers["authorization"]
        assert auth.startswith("Basic ")
        return httpx.Response(
            200, json={"incoming_phone_numbers": [{"phone_number": "+525512345678"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        await connectors_module._verify_twilio_phone_ownership(
            "ACsid", "token", "+525512345678", http_client=http_client
        )  # no lanza


async def test_verify_twilio_phone_ownership_rechaza_credenciales_invalidas() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Authenticate"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(HTTPException) as exc_info:
            await connectors_module._verify_twilio_phone_ownership(
                "ACsid", "token-malo", "+525512345678", http_client=http_client
            )
    assert exc_info.value.status_code == 400


async def test_verify_twilio_phone_ownership_rechaza_numero_ajeno() -> None:
    # Credenciales válidas, pero el número no aparece en LOS números de esa
    # cuenta (p. ej. es de otro tenant): Twilio responde 200 con la lista
    # vacía para ese filtro `PhoneNumber`.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"incoming_phone_numbers": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(HTTPException) as exc_info:
            await connectors_module._verify_twilio_phone_ownership(
                "ACsid", "token", "+525512345678", http_client=http_client
            )
    assert exc_info.value.status_code == 400


async def test_verify_twilio_phone_ownership_502_si_twilio_no_responde() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(HTTPException) as exc_info:
            await connectors_module._verify_twilio_phone_ownership(
                "ACsid", "token", "+525512345678", http_client=http_client
            )
    assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# DELETE /v1/connectors/{key}/{account_id}
# ---------------------------------------------------------------------------


async def test_disconnect_unknown_connector_key_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.delete(
        f"/v1/connectors/no-existe/{uuid.uuid4()}", headers=headers
    )
    assert response.status_code == 404


async def test_disconnect_unknown_account_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.delete(f"/v1/connectors/google/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_disconnect_removes_connected_account(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")
    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="google",
        external_account_id="acc-1",
        display_name="Google",
        scopes=[],
    )

    response = await client.delete(f"/v1/connectors/google/{account['id']}", headers=headers)
    assert response.status_code == 204

    remaining = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert remaining == []
