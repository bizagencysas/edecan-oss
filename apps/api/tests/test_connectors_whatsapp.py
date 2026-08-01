"""`PUT /v1/connectors/whatsapp/credentials` — WhatsApp Business Platform
(Cloud API oficial de Meta), WP-V3-13 (`ARCHITECTURE.md` §12.b).

Complementa a `test_connectors.py` (Twilio/OAuth) y
`test_connectors_credentials_v2.py` (Telegram/Discord/Slack) — este archivo
SOLO agrega tests nuevos para WhatsApp, no duplica ni reescribe los
existentes. Mismas convenciones: `client`/`fake_repo`/`auth_headers` de
`conftest.py`, `FakeVault` local (doble mínimo de `edecan_db.vault.TokenVault`),
`respx` para simular la Graph API de Meta (`graph.facebook.com`) sin red real
— ver `packages/messaging/tests/test_whatsapp.py` para el mismo patrón sobre
el cliente de envío.
"""

from __future__ import annotations

import uuid

import httpx
import respx
from conftest import auth_headers
from edecan_schemas import TokenBundle

import edecan_api.deps as edecan_deps
import edecan_api.routers.connectors as connectors_module

PHONE_NUMBER_ID = "109876543210987"
VERIFY_URL = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}"
VALID_TOKEN = "token-permanente-de-meta-bien-largo-123456"


class FakeVault:
    """Doble de `edecan_db.vault.TokenVault`: solo registra `put()` — mismo
    fake mínimo que usan `test_connectors.py`/`test_connectors_credentials_v2.py`."""

    def __init__(self, *args, **kwargs) -> None:
        self.puts: list[tuple[uuid.UUID, uuid.UUID, TokenBundle]] = []

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self.puts.append((tenant_id, account_id, bundle))


def _headers(**overrides):
    return auth_headers(
        user_id=overrides.pop("user_id", uuid.uuid4()),
        tenant_id=overrides.pop("tenant_id", uuid.uuid4()),
        plan_key=overrides.pop("plan_key", "hosted_pro"),
    )


def _payload(**overrides):
    payload = {
        "access_token": VALID_TOKEN,
        "phone_number_id": PHONE_NUMBER_ID,
        "validate": False,
    }
    payload.update(overrides)
    return payload


def _mock_meta_verification_ok(display_phone_number: str = "+52 55 1234 5678"):
    return respx.get(VERIFY_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "display_phone_number": display_phone_number,
                "verified_name": "Mi Negocio",
                "id": PHONE_NUMBER_ID,
            },
        )
    )


# ---------------------------------------------------------------------------
# PUT /v1/connectors/whatsapp/credentials — validación de formato
# ---------------------------------------------------------------------------


async def test_connect_whatsapp_requires_authentication(client) -> None:
    response = await client.put("/v1/connectors/whatsapp/credentials", json=_payload())
    assert response.status_code == 401


async def test_connect_whatsapp_rejects_empty_access_token(client) -> None:
    headers = _headers()
    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(access_token="   "),
        headers=headers,
    )
    assert response.status_code == 400
    assert "access token" in response.json()["detail"].lower()


async def test_connect_whatsapp_rejects_short_access_token(client) -> None:
    headers = _headers()
    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(access_token="corto"),
        headers=headers,
    )
    assert response.status_code == 400


async def test_connect_whatsapp_rejects_empty_phone_number_id(client) -> None:
    headers = _headers()
    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(phone_number_id=""),
        headers=headers,
    )
    assert response.status_code == 400
    assert "phone_number_id" in response.json()["detail"]


async def test_connect_whatsapp_rejects_non_numeric_phone_number_id(client) -> None:
    headers = _headers()
    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(phone_number_id="no-es-numerico"),
        headers=headers,
    )
    assert response.status_code == 400


@respx.mock
async def test_connect_whatsapp_format_validation_runs_before_network_ping(client) -> None:
    # `validate=True` pero el formato ya es inválido: no debe intentar llegar
    # a la red. `@respx.mock` está activo SIN ninguna ruta registrada a
    # propósito: si el código intentara salir a la red de todas formas,
    # respx lo interceptaría y lanzaría un error (nunca una llamada real),
    # así que este test solo puede pasar en 400 si la validación de formato
    # de verdad corre ANTES de tocar `httpx.AsyncClient`.
    headers = _headers()
    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(access_token="corto", validate=True),
        headers=headers,
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# PUT /v1/connectors/whatsapp/credentials — éxito (validate=True, respx 200)
# ---------------------------------------------------------------------------


@respx.mock
async def test_connect_whatsapp_success_verifies_and_stores_credentials(
    client, app, fake_repo
) -> None:
    ruta = _mock_meta_verification_ok("+52 55 1234 5678")
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(validate=True),
        headers=headers,
    )

    assert response.status_code == 204
    # El ping de propiedad se hizo con Bearer + los fields esperados.
    enviado = ruta.calls.last.request
    assert enviado.headers["Authorization"] == f"Bearer {VALID_TOKEN}"
    assert enviado.url.params["fields"] == "display_phone_number,verified_name"

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1
    assert accounts[0]["connector_key"] == "whatsapp"
    assert accounts[0]["external_account_id"] == PHONE_NUMBER_ID
    # `display_name` = el `display_phone_number` humano-legible que devolvió Meta.
    assert accounts[0]["display_name"] == "+52 55 1234 5678"
    assert accounts[0]["scopes"] == [PHONE_NUMBER_ID]

    assert len(fake_vault.puts) == 1
    stored_tenant_id, stored_account_id, stored_bundle = fake_vault.puts[0]
    assert stored_tenant_id == tenant_id
    assert stored_account_id == accounts[0]["id"]
    assert stored_bundle.access_token == VALID_TOKEN
    assert stored_bundle.scopes == [PHONE_NUMBER_ID]

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "connectors.connected" in actions


async def test_connect_whatsapp_validate_false_skips_the_network_ping(
    client, app, fake_repo
) -> None:
    # Sin `@respx.mock` activo: si el código intentara pegarle a la red real
    # de todas formas, este test fallaría con un error de conexión en vez de
    # 204 — es la prueba de que `validate=False` de verdad no sale a la red.
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(validate=False),
        headers=headers,
    )

    assert response.status_code == 204
    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    # Sin verificación real, `display_name` cae al propio `phone_number_id`.
    assert accounts[0]["display_name"] == PHONE_NUMBER_ID


@respx.mock
async def test_connect_whatsapp_validate_defaults_to_true(client, app) -> None:
    # Sin mandar `validate` en el payload, el default es `True`: el ping real
    # a Meta debe dispararse igual que con `validate: true` explícito (se
    # confirma observando que la ruta mockeada de verificación SÍ se llamó,
    # sin arriesgar una llamada de red real si el default fuera incorrecto).
    ruta = _mock_meta_verification_ok()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    headers = _headers()
    payload = _payload()
    del payload["validate"]

    response = await client.put(
        "/v1/connectors/whatsapp/credentials", json=payload, headers=headers
    )
    assert response.status_code == 204
    assert ruta.called


# ---------------------------------------------------------------------------
# PUT /v1/connectors/whatsapp/credentials — ping de propiedad (respx 401/404/network)
# ---------------------------------------------------------------------------


@respx.mock
async def test_connect_whatsapp_rejects_when_meta_returns_401(client, fake_repo) -> None:
    respx.get(VERIFY_URL).mock(
        return_value=httpx.Response(401, json={"error": {"message": "Invalid OAuth token"}})
    )
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(validate=True),
        headers=headers,
    )

    assert response.status_code == 400
    assert "rechazó el access token" in response.json()["detail"]
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []


@respx.mock
async def test_connect_whatsapp_rejects_when_meta_returns_403(client) -> None:
    respx.get(VERIFY_URL).mock(return_value=httpx.Response(403, json={"error": {}}))
    headers = _headers()

    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(validate=True),
        headers=headers,
    )
    assert response.status_code == 400


@respx.mock
async def test_connect_whatsapp_rejects_when_meta_returns_404(client, fake_repo) -> None:
    respx.get(VERIFY_URL).mock(return_value=httpx.Response(404, json={"error": {}}))
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(validate=True),
        headers=headers,
    )

    assert response.status_code == 400
    assert "no encontró ese phone_number_id" in response.json()["detail"].lower()
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []


@respx.mock
async def test_connect_whatsapp_returns_502_when_meta_unreachable(client) -> None:
    respx.get(VERIFY_URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
    headers = _headers()

    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(validate=True),
        headers=headers,
    )
    assert response.status_code == 502


@respx.mock
async def test_connect_whatsapp_rejects_when_meta_omits_display_phone_number(client) -> None:
    # 200 pero sin `display_phone_number` en el cuerpo: no hay nada útil que
    # guardar como `display_name` — se trata como verificación fallida
    # ("fail closed"), no como éxito con un display_name vacío.
    respx.get(VERIFY_URL).mock(return_value=httpx.Response(200, json={"verified_name": "X"}))
    headers = _headers()

    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(validate=True),
        headers=headers,
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# PUT /v1/connectors/whatsapp/credentials — upsert (singleton por tenant)
# ---------------------------------------------------------------------------


async def test_connect_whatsapp_upsert_replaces_previous_account(client, app, fake_repo) -> None:
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    primera = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(phone_number_id="111111111111111", validate=False),
        headers=headers,
    )
    assert primera.status_code == 204

    segunda = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(phone_number_id="222222222222222", validate=False),
        headers=headers,
    )
    assert segunda.status_code == 204

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    whatsapp_accounts = [a for a in accounts if a["connector_key"] == "whatsapp"]
    assert len(whatsapp_accounts) == 1
    assert whatsapp_accounts[0]["external_account_id"] == "222222222222222"


async def test_connect_whatsapp_upsert_does_not_touch_other_connectors(
    client, app, fake_repo
) -> None:
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="telegram",
        external_account_id="hash-abc",
        display_name="Telegram",
        scopes=[],
    )

    response = await client.put(
        "/v1/connectors/whatsapp/credentials", json=_payload(validate=False), headers=headers
    )
    assert response.status_code == 204

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    by_key = {a["connector_key"] for a in accounts}
    assert by_key == {"telegram", "whatsapp"}


async def test_connect_whatsapp_does_not_touch_another_tenants_account(
    client, app, fake_repo
) -> None:
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await fake_repo.create_connector_account(
        tenant_id=tenant_a,
        connector_key="whatsapp",
        external_account_id=PHONE_NUMBER_ID,
        display_name=PHONE_NUMBER_ID,
        scopes=[PHONE_NUMBER_ID],
    )

    headers_b = _headers(tenant_id=tenant_b)
    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json=_payload(phone_number_id="999999999999999", validate=False),
        headers=headers_b,
    )
    assert response.status_code == 204

    assert len(await fake_repo.list_connector_accounts(tenant_id=tenant_a)) == 1
    assert len(await fake_repo.list_connector_accounts(tenant_id=tenant_b)) == 1


# ---------------------------------------------------------------------------
# GET /v1/connectors — WhatsApp aparece en el catálogo
# ---------------------------------------------------------------------------


async def test_list_connectors_includes_whatsapp(client) -> None:
    headers = _headers()
    response = await client.get("/v1/connectors", headers=headers)

    assert response.status_code == 200
    by_key = {entry["key"]: entry for entry in response.json()}
    assert "whatsapp" in by_key
    assert by_key["whatsapp"]["display_name"] == "WhatsApp Business Platform"
    assert by_key["whatsapp"]["accounts"] == []


async def test_list_connectors_shows_connected_whatsapp_account(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="whatsapp",
        external_account_id=PHONE_NUMBER_ID,
        display_name="+52 55 1234 5678",
        scopes=[PHONE_NUMBER_ID],
    )

    response = await client.get("/v1/connectors", headers=headers)
    by_key = {entry["key"]: entry for entry in response.json()}
    assert len(by_key["whatsapp"]["accounts"]) == 1
    assert by_key["whatsapp"]["accounts"][0]["external_account_id"] == PHONE_NUMBER_ID
    assert by_key["whatsapp"]["accounts"][0]["scopes"] == [PHONE_NUMBER_ID]


# ---------------------------------------------------------------------------
# DELETE /v1/connectors/whatsapp/{account_id}
# ---------------------------------------------------------------------------


async def test_disconnect_whatsapp_account(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="whatsapp",
        external_account_id=PHONE_NUMBER_ID,
        display_name=PHONE_NUMBER_ID,
        scopes=[PHONE_NUMBER_ID],
    )

    response = await client.delete(f"/v1/connectors/whatsapp/{account['id']}", headers=headers)
    assert response.status_code == 204
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []


async def test_disconnect_whatsapp_unknown_account_returns_404(client) -> None:
    headers = _headers()
    response = await client.delete(f"/v1/connectors/whatsapp/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_disconnect_whatsapp_requires_authentication(client) -> None:
    response = await client.delete(f"/v1/connectors/whatsapp/{uuid.uuid4()}")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Regresión de ordenamiento de rutas: `/whatsapp/credentials` (fija) no debe
# caer en la ruta genérica `/{key}/credentials` de `connect_bot_token` (mismo
# guardarraíl que `test_connect_bot_token_does_not_shadow_twilio_route` en
# `test_connectors_credentials_v2.py`, aplicado a WhatsApp).
# ---------------------------------------------------------------------------


async def test_connect_whatsapp_route_is_not_shadowed_by_generic_bot_token_route(
    client,
) -> None:
    # Un payload con la forma de `BotTokenCredentialsIn` (`bot_token`, sin
    # `access_token`/`phone_number_id`) contra `/v1/connectors/whatsapp/credentials`
    # debe fallar la validación de `WhatsAppCredentialsIn` (422), nunca
    # devolver 204 (que sería lo que haría `connect_bot_token` con cualquier
    # token suficientemente largo) ni 404 (que sería "whatsapp" tratado como
    # conector desconocido, el comportamiento previo a WP-V3-13).
    headers = _headers()
    response = await client.put(
        "/v1/connectors/whatsapp/credentials",
        json={"bot_token": "esto-no-es-un-payload-de-whatsapp"},
        headers=headers,
    )
    assert response.status_code == 422


async def test_verify_whatsapp_phone_ownership_is_a_pure_function() -> None:
    # Espejo de `test_verify_twilio_phone_ownership_*` (`test_connectors.py`):
    # función pura, sin red real (`httpx.MockTransport`), reutilizable fuera
    # del endpoint HTTP.
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v21.0/{PHONE_NUMBER_ID}"
        assert request.headers["authorization"] == f"Bearer {VALID_TOKEN}"
        return httpx.Response(200, json={"display_phone_number": "+52 55 0000 0000"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        display_name = await connectors_module._verify_whatsapp_phone_ownership(
            VALID_TOKEN, PHONE_NUMBER_ID, http_client=http_client
        )
    assert display_name == "+52 55 0000 0000"
