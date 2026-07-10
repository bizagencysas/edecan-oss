"""`GET /healthz` (ARCHITECTURE.md §10.12)."""

from __future__ import annotations


async def test_healthz_ok(client) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
