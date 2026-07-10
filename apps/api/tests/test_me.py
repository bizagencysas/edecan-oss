"""`GET /v1/me` (ARCHITECTURE.md §10.12)."""

from __future__ import annotations

import uuid

from conftest import auth_headers


async def test_get_me_returns_user_tenant_and_flags(client, fake_repo) -> None:
    register = await client.post(
        "/v1/auth/register",
        json={"email": "me@example.com", "password": "supersecreta123", "tenant_name": "Me Co"},
    )
    assert register.status_code == 201
    access_token = register.json()["access_token"]

    response = await client.get("/v1/me", headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "me@example.com"
    assert body["user"]["is_superadmin"] is False
    assert body["tenant"]["name"] == "Me Co"
    assert body["tenant"]["plan_key"] == "free_selfhost"
    # free_selfhost trae voice.web y connectors.social en ARCHITECTURE.md §10.13.
    assert body["flags"]["voice.web"] is True
    assert body["flags"]["connectors.social"] is True


async def test_get_me_for_unknown_user_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get("/v1/me", headers=headers)
    assert response.status_code == 404
