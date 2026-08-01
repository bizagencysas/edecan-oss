"""CRUD `/v1/finance/transactions` + `/v1/finance/summary` (ARCHITECTURE.md §10.12, §10.3),
más `/v1/finance/stripe/*` (Stripe bring-your-own, ver docstring de
`edecan_api.routers.finance`)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import respx
from conftest import auth_headers
from edecan_schemas import TokenBundle
from httpx import Response

from edecan_api import deps as edecan_deps

_STRIPE_BASE = "https://api.stripe.com/v1"
_RK = "rk_test_clave_restringida"


@dataclass
class FakeVault:
    """`get_vault` falso: un solo `bundle` (una cuenta Stripe por tenant), mismo
    patrón que `test_ads_router.py::FakeVault`."""

    bundle: TokenBundle | None = None
    puts: list[tuple[uuid.UUID, uuid.UUID, TokenBundle]] = field(default_factory=list)

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self.puts.append((tenant_id, account_id, bundle))
        self.bundle = bundle

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self.bundle


def _with_vault(app) -> FakeVault:
    fake = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake
    return fake


async def _create(client, headers, **overrides):
    payload = {"fecha": "2026-07-01", "monto": "100.50", "categoria": "ingresos"}
    payload.update(overrides)
    response = await client.post("/v1/finance/transactions", json=payload, headers=headers)
    assert response.status_code == 201
    return response.json()


async def test_create_and_list_transactions(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)
    assert created["moneda"] == "USD"

    listed = await client.get("/v1/finance/transactions", headers=headers)
    assert listed.status_code == 200
    assert [t["id"] for t in listed.json()] == [created["id"]]


async def test_list_transactions_filters_by_month(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await _create(client, headers, fecha="2026-07-01")
    await _create(client, headers, fecha="2026-01-15")

    response = await client.get(
        "/v1/finance/transactions", params={"mes": "2026-07"}, headers=headers
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["fecha"] == "2026-07-01"


async def test_get_transaction_by_id(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)

    response = await client.get(
        f"/v1/finance/transactions/{created['id']}", headers=headers
    )
    assert response.status_code == 200


async def test_get_unknown_transaction_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get(f"/v1/finance/transactions/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_update_transaction_patches_fields(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)

    response = await client.put(
        f"/v1/finance/transactions/{created['id']}",
        json={"categoria": "gastos"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["categoria"] == "gastos"


async def test_delete_transaction(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)

    deleted = await client.delete(
        f"/v1/finance/transactions/{created['id']}", headers=headers
    )
    assert deleted.status_code == 204

    response = await client.get(
        f"/v1/finance/transactions/{created['id']}", headers=headers
    )
    assert response.status_code == 404


async def test_delete_unknown_transaction_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.delete(f"/v1/finance/transactions/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_finance_summary_aggregates_ingresos_gastos_y_categorias(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await _create(client, headers, fecha="2026-07-01", monto="1000.00", categoria="salario")
    await _create(client, headers, fecha="2026-07-05", monto="-200.00", categoria="renta")
    await _create(client, headers, fecha="2026-06-01", monto="500.00", categoria="salario")

    response = await client.get(
        "/v1/finance/summary", params={"mes": "2026-07"}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ingresos"] == 1000.0
    assert body["gastos"] == -200.0
    assert body["neto"] == 800.0
    assert body["num_transacciones"] == 2


async def test_finance_summary_requires_mes(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get("/v1/finance/summary", headers=headers)
    assert response.status_code == 422


async def test_create_transaction_normalizes_lowercase_moneda(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers, moneda="eur")
    assert created["moneda"] == "EUR"


async def test_create_transaction_rejects_moneda_too_long(client) -> None:
    # `transactions.moneda` es CHAR(3) NOT NULL: sin validación esto rompía el INSERT
    # con un 500 de Postgres en vez de un 422 limpio.
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    payload = {"fecha": "2026-07-01", "monto": "10", "moneda": "DOLLARS"}
    response = await client.post("/v1/finance/transactions", json=payload, headers=headers)
    assert response.status_code == 422


async def test_create_transaction_rejects_moneda_too_short(client) -> None:
    # Un código de 1-2 letras no revienta el INSERT (Postgres rellena CHAR(3) con
    # blancos), pero deja de representar el código ISO-4217 pedido; debe rechazarse.
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    payload = {"fecha": "2026-07-01", "monto": "10", "moneda": "US"}
    response = await client.post("/v1/finance/transactions", json=payload, headers=headers)
    assert response.status_code == 422


async def test_create_transaction_rejects_monto_overflow(client) -> None:
    # `transactions.monto` es NUMERIC(14,2) NOT NULL: 13 dígitos enteros desborda la
    # precisión (máx. 12 enteros + 2 decimales) y sin validación tiraba un 500.
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    payload = {"fecha": "2026-07-01", "monto": "1234567890123.45", "moneda": "USD"}
    response = await client.post("/v1/finance/transactions", json=payload, headers=headers)
    assert response.status_code == 422


async def test_create_transaction_rejects_monto_extra_decimales(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    payload = {"fecha": "2026-07-01", "monto": "10.123", "moneda": "USD"}
    response = await client.post("/v1/finance/transactions", json=payload, headers=headers)
    assert response.status_code == 422


async def test_update_transaction_rejects_invalid_moneda(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)

    response = await client.put(
        f"/v1/finance/transactions/{created['id']}",
        json={"moneda": "DOLLARS"},
        headers=headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# PUT/DELETE/GET /v1/finance/stripe/credentials
# ---------------------------------------------------------------------------


async def test_put_stripe_credentials_rechaza_api_key_vacio(client, app) -> None:
    fake_vault = _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.put(
        "/v1/finance/stripe/credentials", json={"api_key": "   "}, headers=headers
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


async def test_put_stripe_credentials_rechaza_secret_key_completa(client, app) -> None:
    fake_vault = _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.put(
        "/v1/finance/stripe/credentials",
        json={"api_key": "sk_live_una_secret_key_completa"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "rk_" in response.json()["detail"] or "Restricted" in response.json()["detail"]
    assert fake_vault.puts == []


@respx.mock
async def test_put_stripe_credentials_valida_contra_balance_y_guarda(client, app) -> None:
    fake_vault = _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    respx.get(f"{_STRIPE_BASE}/balance").mock(
        return_value=Response(200, json={"object": "balance"})
    )

    response = await client.put(
        "/v1/finance/stripe/credentials", json={"api_key": _RK}, headers=headers
    )

    assert response.status_code == 204
    assert len(fake_vault.puts) == 1
    assert fake_vault.puts[0][2].access_token == _RK


@respx.mock
async def test_put_stripe_credentials_rechaza_key_invalida(client, app) -> None:
    fake_vault = _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    respx.get(f"{_STRIPE_BASE}/balance").mock(
        return_value=Response(401, json={"error": {"message": "Invalid API Key provided"}})
    )

    response = await client.put(
        "/v1/finance/stripe/credentials", json={"api_key": _RK}, headers=headers
    )

    assert response.status_code == 400
    assert "Invalid API Key" in response.json()["detail"]
    assert fake_vault.puts == []


@respx.mock
async def test_put_stripe_credentials_sin_validate_no_llama_a_stripe(client, app) -> None:
    fake_vault = _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    ruta = respx.get(f"{_STRIPE_BASE}/balance").mock(return_value=Response(200, json={}))

    response = await client.put(
        "/v1/finance/stripe/credentials",
        json={"api_key": _RK, "validate": False},
        headers=headers,
    )

    assert response.status_code == 204
    assert ruta.call_count == 0
    assert len(fake_vault.puts) == 1


async def test_get_stripe_status_desconectado_por_defecto(client, app) -> None:
    _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get("/v1/finance/stripe/status", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"connected": False, "masked": None}


@respx.mock
async def test_get_stripe_status_conectado_tras_put(client, app) -> None:
    _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    respx.get(f"{_STRIPE_BASE}/balance").mock(return_value=Response(200, json={}))
    await client.put("/v1/finance/stripe/credentials", json={"api_key": _RK}, headers=headers)

    response = await client.get("/v1/finance/stripe/status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["masked"].endswith(_RK[-4:])


@respx.mock
async def test_delete_stripe_credentials_es_idempotente(client, app) -> None:
    _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.delete("/v1/finance/stripe/credentials", headers=headers)
    assert response.status_code == 204


async def test_stripe_credentials_requires_authentication(client) -> None:
    response = await client.put("/v1/finance/stripe/credentials", json={"api_key": _RK})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /v1/finance/stripe/sync
# ---------------------------------------------------------------------------


async def _conectar_stripe(client, app, headers) -> None:
    _with_vault(app)
    with respx.mock:
        respx.get(f"{_STRIPE_BASE}/balance").mock(return_value=Response(200, json={}))
        response = await client.put(
            "/v1/finance/stripe/credentials", json={"api_key": _RK}, headers=headers
        )
        assert response.status_code == 204


def _stripe_txn(txn_id: str, *, amount: int = 1000, created: int = 1751328000) -> dict:
    return {
        "id": txn_id,
        "amount": amount,
        "currency": "usd",
        "type": "charge",
        "description": "Pago de cliente",
        "created": created,
    }


async def test_sync_stripe_sin_conectar_devuelve_400(client, app) -> None:
    _with_vault(app)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.post("/v1/finance/stripe/sync", headers=headers)
    assert response.status_code == 400


@respx.mock
async def test_sync_stripe_crea_transacciones_nuevas(client, app) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await _conectar_stripe(client, app, headers)

    respx.get(f"{_STRIPE_BASE}/balance_transactions").mock(
        return_value=Response(
            200,
            json={"data": [_stripe_txn("txn_1", amount=2500), _stripe_txn("txn_2", amount=-500)]},
        )
    )

    response = await client.post("/v1/finance/stripe/sync", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body == {"sincronizadas": 2, "total_stripe": 2}

    listed = await client.get("/v1/finance/transactions", headers=headers)
    montos = {float(t["monto"]) for t in listed.json()}
    assert montos == {25.0, -5.0}
    assert all(t["cuenta"] == "Stripe" for t in listed.json())


@respx.mock
async def test_sync_stripe_no_duplica_en_segunda_corrida(client, app) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await _conectar_stripe(client, app, headers)

    respx.get(f"{_STRIPE_BASE}/balance_transactions").mock(
        return_value=Response(200, json={"data": [_stripe_txn("txn_dup")]})
    )

    primera = await client.post("/v1/finance/stripe/sync", headers=headers)
    segunda = await client.post("/v1/finance/stripe/sync", headers=headers)

    assert primera.json()["sincronizadas"] == 1
    assert segunda.json()["sincronizadas"] == 0

    listed = await client.get("/v1/finance/transactions", headers=headers)
    assert len(listed.json()) == 1


async def test_sync_stripe_requires_authentication(client) -> None:
    response = await client.post("/v1/finance/stripe/sync")
    assert response.status_code == 401
