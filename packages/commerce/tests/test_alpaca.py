from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import respx
from edecan_commerce.alpaca import (
    ALPACA_PAPER_BASE_URL,
    AlpacaAPIError,
    AlpacaCredentials,
    AlpacaPaperBroker,
    AlpacaPaperClient,
    ConsultarAlpacaTool,
    resolve_alpaca_paper_client,
)


@respx.mock
async def test_account_uses_only_paper_host_and_secret_headers() -> None:
    route = respx.get(f"{ALPACA_PAPER_BASE_URL}/v2/account").mock(
        return_value=httpx.Response(200, json={"equity": "100000"})
    )
    client = AlpacaPaperClient(AlpacaCredentials("paper-key", "paper-secret-value"))

    account = await client.get_account()

    request = route.calls.last.request
    assert account["equity"] == "100000"
    assert request.headers["APCA-API-KEY-ID"] == "paper-key"
    assert request.headers["APCA-API-SECRET-KEY"] == "paper-secret-value"
    assert request.url.host == "paper-api.alpaca.markets"


@respx.mock
async def test_market_order_is_idempotent_and_uses_safe_defaults() -> None:
    route = respx.post(f"{ALPACA_PAPER_BASE_URL}/v2/orders").mock(
        return_value=httpx.Response(200, json={"id": "remote-1", "status": "accepted"})
    )
    client = AlpacaPaperClient(AlpacaCredentials("paper-key", "paper-secret-value"))

    order = await client.submit_market_order(
        symbol="AAPL",
        side="buy",
        quantity=Decimal("0.5"),
        client_order_id="edecan-local-order",
    )

    assert order["id"] == "remote-1"
    assert json.loads(route.calls.last.request.content) == {
        "symbol": "AAPL",
        "qty": "0.5",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": "edecan-local-order",
    }


@respx.mock
async def test_duplicate_submit_recovers_by_client_order_id() -> None:
    respx.post(f"{ALPACA_PAPER_BASE_URL}/v2/orders").mock(
        return_value=httpx.Response(422, json={"message": "client_order_id must be unique"})
    )
    recovery = respx.get(f"{ALPACA_PAPER_BASE_URL}/v2/orders:by_client_order_id").mock(
        return_value=httpx.Response(200, json={"id": "already-created", "status": "accepted"})
    )
    client = AlpacaPaperClient(AlpacaCredentials("paper-key", "paper-secret-value"))

    order = await client.submit_market_order(
        symbol="BTC/USD",
        side="buy",
        quantity=Decimal("0.001"),
        client_order_id="edecan-retry",
    )

    assert order["id"] == "already-created"
    assert recovery.calls.last.request.url.params["client_order_id"] == "edecan-retry"


@respx.mock
async def test_errors_never_include_credentials() -> None:
    secret = "super-private-alpaca-secret"
    respx.get(f"{ALPACA_PAPER_BASE_URL}/v2/account").mock(
        return_value=httpx.Response(401, json={"message": "unauthorized"})
    )
    client = AlpacaPaperClient(AlpacaCredentials("paper-key", secret))

    try:
        await client.get_account()
    except AlpacaAPIError as exc:
        assert secret not in str(exc)
        assert secret not in repr(exc)
    else:  # pragma: no cover
        raise AssertionError("Se esperaba AlpacaAPIError")


async def test_resolver_reads_encrypted_config(make_session) -> None:
    account_id = uuid4()
    session = make_session([[{"id": account_id}]])
    bundle = SimpleNamespace(
        access_token=json.dumps(
            {
                "environment": "paper",
                "api_key_id": "paper-key",
                "secret_key": "paper-secret-value",
            }
        )
    )

    class Vault:
        async def get(self, tenant_id, connector_account_id):  # noqa: ANN001
            assert connector_account_id == account_id
            return bundle

    client = await resolve_alpaca_paper_client(session=session, vault=Vault(), tenant_id=uuid4())

    assert isinstance(client, AlpacaPaperClient)
    assert session.llamadas[0][1]["connector_key"] == "alpaca_paper"


async def test_consult_tool_reports_account(monkeypatch, make_ctx) -> None:
    class FakeClient:
        async def get_account(self):
            return {
                "equity": "120000",
                "cash": "100000",
                "buying_power": "200000",
                "trading_blocked": False,
            }

    async def fake_resolve(**kwargs):  # noqa: ANN003
        return FakeClient()

    monkeypatch.setattr("edecan_commerce.alpaca.resolve_alpaca_paper_client", fake_resolve)

    result = await ConsultarAlpacaTool().run(make_ctx(), {"vista": "cuenta"})

    assert "patrimonio $120,000.00" in result.content


async def test_broker_persists_remote_order_evidence(make_session) -> None:
    order_id = uuid4()
    tenant_id = uuid4()
    user_id = uuid4()
    updated = {
        "id": order_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "kind": "trade",
        "status": "executed_paper",
    }
    session = make_session([[updated], []])

    class FakeClient:
        async def submit_market_order(self, **kwargs):  # noqa: ANN003
            assert kwargs["client_order_id"] == f"edecan-{order_id}"
            return {"id": "remote-order", "status": "accepted", **kwargs}

    result = await AlpacaPaperBroker().execute(
        {
            "id": order_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "kind": "trade",
            "status": "confirmed",
            "simbolo": "AAPL",
            "lado": "buy",
            "cantidad": "1",
            "meta": {},
        },
        session,
        FakeClient(),
    )

    assert result["status"] == "executed_paper"
    update_params = session.llamadas[0][1]
    assert json.loads(update_params["meta"])["alpaca_paper"]["order_id"] == "remote-order"
