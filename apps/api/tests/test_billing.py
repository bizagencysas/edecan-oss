"""`POST /v1/billing/webhook` y `POST /v1/billing/portal` (ARCHITECTURE.md §10.12).

Cubre en particular `_verify_stripe_signature` (HMAC-SHA256 del header
`Stripe-Signature`), que hasta ahora no tenía ningún test.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

from conftest import auth_headers

STRIPE_WEBHOOK_SECRET = "whsec_test_secret_para_pruebas"


def _sign(
    payload: bytes, *, secret: str = STRIPE_WEBHOOK_SECRET, timestamp: int | None = None
) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    signature = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


async def _post_webhook(client, event: dict, *, secret: str = STRIPE_WEBHOOK_SECRET, **sign_kwargs):
    payload = json.dumps(event).encode("utf-8")
    header = _sign(payload, secret=secret, **sign_kwargs)
    return await client.post(
        "/v1/billing/webhook",
        content=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": header},
    )


def _with_stripe_secret(app, test_settings):
    """El fixture `test_settings` no trae `STRIPE_WEBHOOK_SECRET`: lo inyectamos
    aquí para los tests que necesitan una firma válida, sin tocar `conftest.py`."""
    from edecan_api.config import get_settings

    configured = test_settings.model_copy(update={"STRIPE_WEBHOOK_SECRET": STRIPE_WEBHOOK_SECRET})
    app.dependency_overrides[get_settings] = lambda: configured


async def test_webhook_without_configured_secret_returns_400(client) -> None:
    # `test_settings` (conftest.py) no define `STRIPE_WEBHOOK_SECRET`.
    payload = json.dumps({"type": "checkout.session.completed", "data": {"object": {}}}).encode()
    response = await client.post(
        "/v1/billing/webhook",
        content=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": _sign(payload)},
    )
    assert response.status_code == 400


async def test_webhook_rejects_bad_signature(client, app, test_settings) -> None:
    _with_stripe_secret(app, test_settings)
    payload = json.dumps({"type": "checkout.session.completed", "data": {"object": {}}}).encode()

    response = await client.post(
        "/v1/billing/webhook",
        content=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": "t=123,v1=deadbeef"},
    )
    assert response.status_code == 400


async def test_webhook_rejects_expired_signature(client, app, test_settings) -> None:
    _with_stripe_secret(app, test_settings)
    payload = json.dumps({"type": "checkout.session.completed", "data": {"object": {}}}).encode()
    old_header = _sign(payload, timestamp=int(time.time()) - 3600)

    response = await client.post(
        "/v1/billing/webhook",
        content=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": old_header},
    )
    assert response.status_code == 400


async def test_webhook_rejects_non_json_payload(client, app, test_settings) -> None:
    _with_stripe_secret(app, test_settings)
    payload = b"esto no es json"
    response = await client.post(
        "/v1/billing/webhook",
        content=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": _sign(payload)},
    )
    assert response.status_code == 400


async def test_webhook_checkout_completed_activates_subscription_and_plan(
    client, app, test_settings, fake_repo
) -> None:
    _with_stripe_secret(app, test_settings)
    tenant = await fake_repo.create_tenant(name="Cliente", slug="cliente", plan_key="free_selfhost")
    tenant_id = tenant["id"]

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": str(tenant_id),
                "customer": "cus_123",
                "subscription": "sub_123",
                "metadata": {"plan_key": "hosted_pro"},
            }
        },
    }

    response = await _post_webhook(client, event)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    tenant = await fake_repo.get_tenant(tenant_id)
    assert tenant["plan_key"] == "hosted_pro"
    subscription = await fake_repo.get_subscription_by_stripe_customer("cus_123")
    assert subscription is not None
    assert subscription["status"] == "active"
    assert subscription["stripe_subscription_id"] == "sub_123"
    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "billing.checkout_completed" in actions


async def test_webhook_checkout_completed_without_tenant_id_is_ignored(
    client, app, test_settings, fake_repo
) -> None:
    _with_stripe_secret(app, test_settings)
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"customer": "cus_sin_tenant"}},
    }

    response = await _post_webhook(client, event)

    assert response.status_code == 200
    assert fake_repo.subscriptions == {}


async def test_webhook_checkout_completed_with_invalid_tenant_id_is_ignored(
    client, app, test_settings, fake_repo
) -> None:
    _with_stripe_secret(app, test_settings)
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": "no-es-un-uuid",
                "customer": "cus_123",
                "subscription": "sub_123",
                "metadata": {"plan_key": "hosted_pro"},
            }
        },
    }

    response = await _post_webhook(client, event)

    assert response.status_code == 200
    assert fake_repo.subscriptions == {}


async def test_webhook_checkout_completed_without_customer_or_subscription_is_ignored(
    client, app, test_settings, fake_repo
) -> None:
    _with_stripe_secret(app, test_settings)
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": str(uuid.uuid4()),
                "metadata": {"plan_key": "hosted_pro"},
            }
        },
    }

    response = await _post_webhook(client, event)

    assert response.status_code == 200
    assert fake_repo.subscriptions == {}


async def test_webhook_checkout_completed_pago_unico_base_no_toca_subscriptions(
    client, app, test_settings, fake_repo
) -> None:
    _with_stripe_secret(app, test_settings)
    tenant = await fake_repo.create_tenant(name="Cliente", slug="cliente", plan_key="free_selfhost")
    tenant_id = tenant["id"]

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "mode": "payment",
                "client_reference_id": str(tenant_id),
                "customer": "cus_pago_unico",
                "metadata": {"tier": "base"},
            }
        },
    }

    response = await _post_webhook(client, event)

    assert response.status_code == 200
    tenant = await fake_repo.get_tenant(tenant_id)
    assert tenant["lifetime_updates_purchased_at"] is None
    assert fake_repo.subscriptions == {}
    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "billing.purchase_completed" in actions


async def test_webhook_checkout_completed_pago_unico_lifetime_marca_actualizaciones(
    client, app, test_settings, fake_repo
) -> None:
    _with_stripe_secret(app, test_settings)
    tenant = await fake_repo.create_tenant(
        name="Cliente", slug="cliente-2", plan_key="free_selfhost"
    )
    tenant_id = tenant["id"]

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "mode": "payment",
                "client_reference_id": str(tenant_id),
                "customer": "cus_lifetime",
                "metadata": {"tier": "lifetime"},
            }
        },
    }

    response = await _post_webhook(client, event)

    assert response.status_code == 200
    tenant = await fake_repo.get_tenant(tenant_id)
    assert tenant["lifetime_updates_purchased_at"] is not None
    assert fake_repo.subscriptions == {}


async def test_webhook_checkout_completed_pago_unico_sin_tenant_id_se_ignora(
    client, app, test_settings, fake_repo
) -> None:
    _with_stripe_secret(app, test_settings)
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"mode": "payment", "customer": "cus_sin_tenant"}},
    }

    response = await _post_webhook(client, event)

    assert response.status_code == 200
    assert fake_repo.audit_log == []


async def test_webhook_subscription_deleted_downgrades_tenant_to_free(
    client, app, test_settings, fake_repo
) -> None:
    _with_stripe_secret(app, test_settings)
    tenant = await fake_repo.create_tenant(
        name="Cliente 2", slug="cliente-2", plan_key="hosted_pro"
    )
    await fake_repo.upsert_subscription(
        tenant_id=tenant["id"],
        fields={
            "stripe_customer_id": "cus_456",
            "stripe_subscription_id": "sub_456",
            "status": "active",
        },
    )

    event = {
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_456", "customer": "cus_456"}},
    }
    response = await _post_webhook(client, event)

    assert response.status_code == 200
    updated_tenant = await fake_repo.get_tenant(tenant["id"])
    assert updated_tenant["plan_key"] == "free_selfhost"
    subscription = await fake_repo.get_subscription_by_stripe_subscription("sub_456")
    assert subscription["status"] == "cancelled"


async def test_webhook_subscription_updated_without_local_match_is_ignored(
    client, app, test_settings, fake_repo
) -> None:
    _with_stripe_secret(app, test_settings)
    event = {
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_no_existe",
                "customer": "cus_no_existe",
                "status": "past_due",
            }
        },
    }
    response = await _post_webhook(client, event)

    assert response.status_code == 200
    assert fake_repo.subscriptions == {}


async def test_webhook_unknown_event_type_is_acknowledged_and_ignored(
    client, app, test_settings, fake_repo
) -> None:
    _with_stripe_secret(app, test_settings)
    event = {"type": "invoice.paid", "data": {"object": {}}}
    response = await _post_webhook(client, event)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_billing_portal_returns_placeholder_url(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.post("/v1/billing/portal", headers=headers)

    assert response.status_code == 200
    assert response.json()["url"].startswith("http://localhost:3000/app/facturacion")


async def test_billing_portal_requires_authentication(client) -> None:
    response = await client.post("/v1/billing/portal")
    assert response.status_code == 401
