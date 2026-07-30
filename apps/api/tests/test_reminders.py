"""CRUD `/v1/reminders` (ARCHITECTURE.md §10.12, §10.3)."""

from __future__ import annotations

import uuid

from conftest import auth_headers


async def _create(client, headers, **overrides):
    payload = {"due_at": "2026-08-01T09:00:00Z", "message": "Llamar al banco"}
    payload.update(overrides)
    response = await client.post("/v1/reminders", json=payload, headers=headers)
    assert response.status_code == 201
    return response.json()


async def test_create_and_list_reminders(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)
    assert created["message"] == "Llamar al banco"
    assert created["status"] == "pending"

    listed = await client.get("/v1/reminders", headers=headers)
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()] == [created["id"]]


async def test_get_reminder_by_id(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)

    response = await client.get(f"/v1/reminders/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


async def test_get_unknown_reminder_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get(f"/v1/reminders/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_update_reminder_patches_status(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)

    response = await client.put(
        f"/v1/reminders/{created['id']}", json={"status": "cancelled"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    # Lo que no se envía no cambia.
    assert response.json()["message"] == "Llamar al banco"


async def test_update_reminder_rejects_invalid_status(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)

    response = await client.put(
        f"/v1/reminders/{created['id']}", json={"status": "done"}, headers=headers
    )
    assert response.status_code == 422


async def test_update_unknown_reminder_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.put(
        f"/v1/reminders/{uuid.uuid4()}", json={"status": "cancelled"}, headers=headers
    )
    assert response.status_code == 404


async def test_delete_reminder(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)

    deleted = await client.delete(f"/v1/reminders/{created['id']}", headers=headers)
    assert deleted.status_code == 204

    listed = await client.get("/v1/reminders", headers=headers)
    assert listed.json() == []


async def test_delete_unknown_reminder_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.delete(f"/v1/reminders/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_reminders_are_scoped_per_tenant(client) -> None:
    headers_a = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    headers_b = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    await _create(client, headers_a)

    listed_b = await client.get("/v1/reminders", headers=headers_b)
    assert listed_b.json() == []


# ---------------------------------------------------------------------------
# v5 (ARCHITECTURE.md §14, dueño WP-V5-01): canal "mobile" (push a la app
# móvil, dueño real WP-V5-13) — sumado al vocabulario de v1
# (web|voice|phone|api), y este router ahora valida `channel` explícitamente
# (antes de v5 aceptaba cualquier string sin restricción).
# ---------------------------------------------------------------------------


async def test_create_reminder_with_mobile_channel(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers, channel="mobile")
    assert created["channel"] == "mobile"

    listed = await client.get("/v1/reminders", headers=headers)
    assert listed.json()[0]["channel"] == "mobile"


async def test_create_reminder_rejects_invalid_channel(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    payload = {
        "due_at": "2026-08-01T09:00:00Z",
        "message": "Llamar al banco",
        "channel": "carrier-pigeon",
    }
    response = await client.post("/v1/reminders", json=payload, headers=headers)
    assert response.status_code == 422


async def test_update_reminder_accepts_mobile_channel(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    created = await _create(client, headers)

    response = await client.put(
        f"/v1/reminders/{created['id']}", json={"channel": "mobile"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["channel"] == "mobile"
