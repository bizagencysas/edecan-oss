"""`GET /v1/usage` — uso del mes vs límites del plan (ARCHITECTURE.md §10.12, §10.13)."""

from __future__ import annotations

import uuid

from conftest import auth_headers
from edecan_schemas.plans import UNLIMITED


async def test_get_usage_with_no_events_reports_zero_and_plan_limits(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get("/v1/usage", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["plan_key"] == "hosted_basic"
    assert body["usage"] == {}
    # Modelo de precio de pago único (edecan_schemas.plans docstring): todos
    # los planes conceden todos los flags y límites ilimitados.
    assert body["limits"]["limits.messages_per_day"] == UNLIMITED
    assert body["limits"]["limits.voice_minutes_month"] == UNLIMITED
    assert body["flags"]["voice.web"] is True
    assert body["flags"]["connectors.social"] is True


async def test_get_usage_sums_events_from_this_month(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    await fake_repo.add_usage_event(tenant_id=tenant_id, kind="messages", quantity=5.0)
    await fake_repo.add_usage_event(tenant_id=tenant_id, kind="messages", quantity=2.0)
    await fake_repo.add_usage_event(tenant_id=tenant_id, kind="voice_seconds", quantity=120.0)

    response = await client.get("/v1/usage", headers=headers)

    assert response.status_code == 200
    usage = response.json()["usage"]
    assert usage["messages"] == 7.0
    assert usage["voice_seconds"] == 120.0


async def test_get_usage_unlimited_plan_reports_minus_one(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")
    response = await client.get("/v1/usage", headers=headers)

    assert response.status_code == 200
    limits = response.json()["limits"]
    assert limits["limits.messages_per_day"] == -1
    assert limits["limits.storage_mb"] == -1
