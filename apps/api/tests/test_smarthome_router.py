"""`/v1/smarthome/*` — conector Home Assistant, bring-your-own (WP-V3-12,
`ARCHITECTURE.md` §12/§12.b, `apps/api/edecan_api/routers/smarthome.py`,
`docs/casa-inteligente.md`).

Mismas convenciones que `test_credentials_router.py`: `client`/`app`/
`fake_repo`/`auth_headers` de `conftest.py`, `FakeVault` local con `put` +
`get` en memoria (no se toca `conftest.py`). Cada test lleva `@respx.mock`
(incluso los que no esperan tráfico real, p. ej. `validate=false`): con
`assert_all_mocked=True` de fábrica, cualquier llamada HTTP no interceptada
explícitamente hace fallar el test en vez de pegarle a la red real.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import respx
from conftest import auth_headers
from edecan_schemas import TokenBundle

import edecan_api.deps as edecan_deps

BASE_URL = "http://homeassistant.local:8123"
TOKEN = "long-lived-token-de-prueba"


class FakeVault:
    """Doble de `edecan_db.vault.TokenVault` con `put` + `get` en memoria
    (mismo patrón que `test_credentials_router.py`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._store: dict[tuple[uuid.UUID, uuid.UUID], TokenBundle] = {}
        self.puts: list[tuple[uuid.UUID, uuid.UUID, TokenBundle]] = []

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self.puts.append((tenant_id, account_id, bundle))
        self._store[(tenant_id, account_id)] = bundle

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self._store.get((tenant_id, account_id))


def _headers(**overrides: Any) -> dict[str, str]:
    return auth_headers(
        user_id=overrides.pop("user_id", uuid.uuid4()),
        tenant_id=overrides.pop("tenant_id", uuid.uuid4()),
        plan_key=overrides.pop("plan_key", "hosted_pro"),
    )


def _install_vault(app: Any) -> FakeVault:
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    return fake_vault


# ---------------------------------------------------------------------------
# PUT /v1/smarthome/credentials — validación de payload (validate=false)
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_requires_authentication(client) -> None:
    response = await client.put(
        "/v1/smarthome/credentials", json={"base_url": BASE_URL, "token": TOKEN}
    )
    assert response.status_code == 401


@respx.mock
async def test_put_rechaza_esquema_no_http(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": "ftp://homeassistant.local:8123", "token": TOKEN, "validate": False},
        headers=_headers(),
    )
    assert response.status_code == 400


@respx.mock
async def test_put_rechaza_credenciales_embebidas(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/smarthome/credentials",
        json={
            "base_url": "http://user:pass@homeassistant.local:8123",
            "token": TOKEN,
            "validate": False,
        },
        headers=_headers(),
    )
    assert response.status_code == 400


@respx.mock
async def test_put_rechaza_token_vacio(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": BASE_URL, "token": "   ", "validate": False},
        headers=_headers(),
    )
    assert response.status_code == 400


@respx.mock
async def test_put_acepta_ip_privada_como_base_url(client, app) -> None:
    """SSRF invertida (ver `edecan_smarthome.client` / docstring del
    router): una IP privada es el caso NORMAL, nunca se rechaza por eso."""
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": "http://192.168.1.50:8123", "token": TOKEN, "validate": False},
        headers=_headers(),
    )

    assert response.status_code == 204
    assert len(fake_vault.puts) == 1


@respx.mock
async def test_put_validate_false_no_pega_a_la_red_y_guarda(client, app, fake_repo) -> None:
    fake_vault = _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": f"{BASE_URL}/", "token": TOKEN, "validate": False},
        headers=headers,
    )

    assert response.status_code == 204
    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1
    assert accounts[0]["connector_key"] == "homeassistant"
    assert accounts[0]["display_name"] == "Home Assistant"

    assert len(fake_vault.puts) == 1
    stored_tenant_id, stored_account_id, bundle = fake_vault.puts[0]
    assert stored_tenant_id == tenant_id
    assert stored_account_id == accounts[0]["id"]
    assert bundle.access_token == TOKEN  # tal cual, NUNCA envuelto en JSON (§12.b)
    assert bundle.scopes == [BASE_URL]  # normalizado, sin '/' final
    assert bundle.token_type == "bearer"

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "smarthome.connected" in actions


# ---------------------------------------------------------------------------
# PUT /v1/smarthome/credentials — "pegar y validar" (validate=true, respx)
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_valida_contra_home_assistant_real_y_guarda(client, app) -> None:
    ruta = respx.get(f"{BASE_URL}/api/").mock(
        return_value=httpx.Response(200, json={"message": "API running."})
    )
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": BASE_URL, "token": TOKEN},
        headers=_headers(),
    )

    assert response.status_code == 204
    assert len(fake_vault.puts) == 1
    assert ruta.calls.last.request.headers["Authorization"] == f"Bearer {TOKEN}"


@respx.mock
async def test_put_token_rechazado_401_no_guarda_nada(client, app) -> None:
    respx.get(f"{BASE_URL}/api/").mock(return_value=httpx.Response(401))
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": BASE_URL, "token": TOKEN},
        headers=_headers(),
    )

    assert response.status_code == 400
    assert "token" in response.json()["detail"].lower()
    assert fake_vault.puts == []


@respx.mock
async def test_put_home_assistant_inalcanzable_400_no_guarda_nada(client, app) -> None:
    respx.get(f"{BASE_URL}/api/").mock(side_effect=httpx.ConnectError("connection refused"))
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": BASE_URL, "token": TOKEN},
        headers=_headers(),
    )

    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_home_assistant_responde_error_5xx_400(client, app) -> None:
    respx.get(f"{BASE_URL}/api/").mock(return_value=httpx.Response(502, text="bad gateway"))
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": BASE_URL, "token": TOKEN},
        headers=_headers(),
    )

    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_reconecta_reusa_la_misma_cuenta(client, app, fake_repo) -> None:
    """Singleton por tenant: un segundo PUT actualiza la MISMA
    `connector_account`, no crea una segunda."""
    fake_vault = _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    for token in ("primer-token", "segundo-token"):
        response = await client.put(
            "/v1/smarthome/credentials",
            json={"base_url": BASE_URL, "token": token, "validate": False},
            headers=headers,
        )
        assert response.status_code == 204

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1
    assert len(fake_vault.puts) == 2
    assert fake_vault.puts[-1][2].access_token == "segundo-token"


# ---------------------------------------------------------------------------
# DELETE /v1/smarthome/credentials
# ---------------------------------------------------------------------------


@respx.mock
async def test_delete_requires_authentication(client) -> None:
    response = await client.delete("/v1/smarthome/credentials")
    assert response.status_code == 401


@respx.mock
async def test_delete_smarthome_credentials(client, app, fake_repo) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": BASE_URL, "token": TOKEN, "validate": False},
        headers=headers,
    )

    response = await client.delete("/v1/smarthome/credentials", headers=headers)

    assert response.status_code == 204
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []
    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "smarthome.disconnected" in actions


@respx.mock
async def test_delete_es_idempotente(client, app) -> None:
    _install_vault(app)
    response = await client.delete("/v1/smarthome/credentials", headers=_headers())
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# GET /v1/smarthome/status
# ---------------------------------------------------------------------------


@respx.mock
async def test_status_requires_authentication(client) -> None:
    response = await client.get("/v1/smarthome/status")
    assert response.status_code == 401


@respx.mock
async def test_status_no_configurado(client, app) -> None:
    _install_vault(app)
    response = await client.get("/v1/smarthome/status", headers=_headers())
    assert response.status_code == 200
    assert response.json() == {"configured": False, "base_url": None, "reachable": None}


@respx.mock
async def test_status_configurado_y_reachable_true(client, app) -> None:
    respx.get(f"{BASE_URL}/api/").mock(return_value=httpx.Response(200, json={"message": "ok"}))
    _install_vault(app)
    headers = _headers()
    await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": BASE_URL, "token": TOKEN, "validate": False},
        headers=headers,
    )

    response = await client.get("/v1/smarthome/status", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"configured": True, "base_url": BASE_URL, "reachable": True}


@respx.mock
async def test_status_configurado_y_reachable_false_token_vencido(client, app) -> None:
    respx.get(f"{BASE_URL}/api/").mock(return_value=httpx.Response(401))
    _install_vault(app)
    headers = _headers()
    await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": BASE_URL, "token": TOKEN, "validate": False},
        headers=headers,
    )

    response = await client.get("/v1/smarthome/status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["reachable"] is False


@respx.mock
async def test_status_configurado_pero_red_falla_reachable_null_nunca_500(client, app) -> None:
    respx.get(f"{BASE_URL}/api/").mock(side_effect=httpx.ConnectTimeout("timed out"))
    _install_vault(app)
    headers = _headers()
    await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": BASE_URL, "token": TOKEN, "validate": False},
        headers=headers,
    )

    response = await client.get("/v1/smarthome/status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["reachable"] is None


@respx.mock
async def test_status_no_mezcla_tenants(client, app) -> None:
    _install_vault(app)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    await client.put(
        "/v1/smarthome/credentials",
        json={"base_url": BASE_URL, "token": TOKEN, "validate": False},
        headers=_headers(tenant_id=tenant_a),
    )

    response_b = await client.get("/v1/smarthome/status", headers=_headers(tenant_id=tenant_b))
    assert response_b.json() == {"configured": False, "base_url": None, "reachable": None}
