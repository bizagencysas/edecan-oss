"""`GET /v1/admin/tenants|usage` — solo superadmin (ARCHITECTURE.md §10.12)."""

from __future__ import annotations

import uuid

from conftest import auth_headers


async def _register_superadmin(client, fake_repo, *, email: str):
    register = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "supersecreta123", "tenant_name": f"{email} Co"},
    )
    assert register.status_code == 201
    body = register.json()

    user = await fake_repo.get_user_by_email(email)
    fake_repo.users[user["id"]]["is_superadmin"] = True

    return {"Authorization": f"Bearer {body['access_token']}"}


async def test_list_tenants_requires_superadmin(client) -> None:
    register = await client.post(
        "/v1/auth/register",
        json={
            "email": "normal@example.com",
            "password": "supersecreta123",
            "tenant_name": "Normal Co",
        },
    )
    access_token = register.json()["access_token"]

    response = await client.get(
        "/v1/admin/tenants", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 403


async def test_list_tenants_rejects_unknown_user(client) -> None:
    # Token con firma válida pero cuyo usuario nunca existió en el repo (p. ej. borrado).
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get("/v1/admin/tenants", headers=headers)
    assert response.status_code == 403


async def test_list_tenants_returns_all_tenants_for_superadmin(client, fake_repo) -> None:
    headers = await _register_superadmin(client, fake_repo, email="root@example.com")
    await client.post(
        "/v1/auth/register",
        json={
            "email": "otro@example.com",
            "password": "supersecreta123",
            "tenant_name": "Otro Co",
        },
    )

    response = await client.get("/v1/admin/tenants", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    names = {t["name"] for t in body}
    assert names == {"root@example.com Co", "Otro Co"}
    assert all(t["slug"] and t["status"] == "active" for t in body)


async def test_all_usage_aggregates_across_tenants_for_superadmin(client, fake_repo) -> None:
    headers = await _register_superadmin(client, fake_repo, email="root2@example.com")
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await fake_repo.add_usage_event(tenant_id=tenant_a, kind="messages", quantity=3.0)
    await fake_repo.add_usage_event(tenant_id=tenant_b, kind="messages", quantity=9.0)

    response = await client.get("/v1/admin/usage", headers=headers)

    assert response.status_code == 200
    totals = {(row["tenant_id"], row["kind"]): row["total"] for row in response.json()}
    assert totals[(str(tenant_a), "messages")] == 3.0
    assert totals[(str(tenant_b), "messages")] == 9.0


async def test_all_usage_non_superadmin_returns_403(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get("/v1/admin/usage", headers=headers)
    assert response.status_code == 403
