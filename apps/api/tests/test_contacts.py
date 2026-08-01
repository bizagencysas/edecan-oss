"""CRUD `/v1/contacts` (ARCHITECTURE.md §10.12, §10.3)."""

from __future__ import annotations

import uuid

from conftest import auth_headers


async def _create(client, headers, **overrides):
    payload = {"nombre": "Beatriz Ruiz", "emails": ["bea@example.com"]}
    payload.update(overrides)
    response = await client.post("/v1/contacts", json=payload, headers=headers)
    assert response.status_code == 201
    return response.json()


async def test_create_and_list_contacts(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)
    assert created["nombre"] == "Beatriz Ruiz"
    assert created["emails"] == ["bea@example.com"]

    listed = await client.get("/v1/contacts", headers=headers)
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [created["id"]]


async def test_create_contact_requires_nombre(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.post("/v1/contacts", json={"nombre": ""}, headers=headers)
    assert response.status_code == 422


async def test_list_contacts_filters_by_query(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await _create(client, headers, nombre="Ana López")
    await _create(client, headers, nombre="Carlos Pérez")

    response = await client.get("/v1/contacts", params={"q": "ana"}, headers=headers)
    assert response.status_code == 200
    assert [c["nombre"] for c in response.json()] == ["Ana López"]


async def test_get_contact_by_id(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)

    response = await client.get(f"/v1/contacts/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["nombre"] == "Beatriz Ruiz"


async def test_get_unknown_contact_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get(f"/v1/contacts/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_update_contact_patches_fields(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)

    response = await client.put(
        f"/v1/contacts/{created['id']}", json={"empresa": "Acme"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["empresa"] == "Acme"
    assert response.json()["nombre"] == "Beatriz Ruiz"


async def test_delete_contact(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)

    deleted = await client.delete(f"/v1/contacts/{created['id']}", headers=headers)
    assert deleted.status_code == 204

    response = await client.get(f"/v1/contacts/{created['id']}", headers=headers)
    assert response.status_code == 404


async def test_delete_unknown_contact_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.delete(f"/v1/contacts/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_contacts_are_scoped_per_tenant(client) -> None:
    headers_a = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    headers_b = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await _create(client, headers_a)

    listed_b = await client.get("/v1/contacts", headers=headers_b)
    assert listed_b.json() == []
