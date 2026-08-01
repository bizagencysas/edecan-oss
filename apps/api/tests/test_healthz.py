"""`GET /healthz` (ARCHITECTURE.md §10.12)."""

from __future__ import annotations

import uuid

from edecan_api.deps import get_platform_session, get_redis


async def test_healthz_ok(client) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_request_id_reuses_only_bounded_log_safe_values(client) -> None:
    accepted = await client.get("/healthz", headers={"X-Request-ID": "edge-01:trace_42"})
    assert accepted.headers["X-Request-ID"] == "edge-01:trace_42"

    rejected = await client.get("/healthz", headers={"X-Request-ID": "x" * 10_000})
    generated = rejected.headers["X-Request-ID"]
    assert len(generated) == 36
    assert uuid.UUID(generated)


async def test_readyz_checks_database_and_redis(app, client, fake_redis) -> None:
    class ReadySession:
        async def execute(self, statement):
            assert str(statement) == "SELECT 1"

    app.dependency_overrides[get_platform_session] = lambda: ReadySession()
    app.dependency_overrides[get_redis] = lambda: fake_redis

    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_fails_closed_without_database(app, client, fake_redis) -> None:
    class FailedSession:
        async def execute(self, statement):
            raise RuntimeError("database unavailable")

    app.dependency_overrides[get_platform_session] = lambda: FailedSession()
    app.dependency_overrides[get_redis] = lambda: fake_redis

    response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
