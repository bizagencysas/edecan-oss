from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

import edecan_api.deps as edecan_deps


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


@dataclass
class FakePlatformSession:
    responses: list[list[dict[str, Any]]] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.calls.append((str(stmt), dict(params or {})))
        return FakeResult(self.responses.pop(0) if self.responses else [])


@pytest.fixture
def platform_session() -> FakePlatformSession:
    return FakePlatformSession()


@pytest.fixture
async def pairing_client(app, platform_session) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[edecan_deps.get_platform_session] = lambda: platform_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _device_row(
    *, device_id: uuid.UUID, user_id: uuid.UUID, tenant_id: uuid.UUID
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": device_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "nombre": "Pixel de prueba",
        "plataforma": "android",
        "kind": "mobile",
        "status": "active",
        "last_seen_at": now,
        "fingerprint": "pixel-fingerprint",
        "push_token": None,
        "push_platform": None,
        "paired_at": now,
        "created_at": now,
        "updated_at": now,
    }


async def _crear_qr(client: AsyncClient, *, user_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
    response = await client.post(
        "/v1/devices/pairing",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )
    assert response.status_code == 200
    body = response.json()
    parsed = urlparse(body["pairing_uri"])
    assert parsed.scheme == "edecan"
    assert parsed.netloc == "pair"
    query = parse_qs(parsed.query)
    assert query["server"] == ["http://localhost:8000"]
    assert body["expires_in_seconds"] == 600
    return query["token"][0]


async def test_qr_claim_is_single_use_and_returns_durable_device_identity(
    pairing_client, platform_session
) -> None:
    user_id, tenant_id, device_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token = await _crear_qr(pairing_client, user_id=user_id, tenant_id=tenant_id)
    platform_session.responses = [
        [{"plan_key": "free_selfhost"}],
        [],
        [_device_row(device_id=device_id, user_id=user_id, tenant_id=tenant_id)],
    ]

    response = await pairing_client.post(
        "/v1/devices/pairing/claim",
        json={
            "pairing_token": token,
            "nombre": "Pixel de prueba",
            "plataforma": "android",
            "kind": "mobile",
            "fingerprint": "pixel-fingerprint",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["device_id"] == str(device_id)
    assert len(body["device_token"]) >= 32
    assert "pairing_secret_hash" not in body

    insert_sql, insert_params = platform_session.calls[2]
    assert "pairing_secret_hash" in insert_sql
    assert insert_params["secret_hash"] == hashlib.sha256(body["device_token"].encode()).hexdigest()
    assert token not in str(platform_session.calls)

    reused = await pairing_client.post(
        "/v1/devices/pairing/claim",
        json={
            "pairing_token": token,
            "nombre": "Otro",
            "plataforma": "android",
            "kind": "mobile",
        },
    )
    assert reused.status_code == 401


async def test_durable_refresh_survives_missing_jwt_session(
    pairing_client, platform_session
) -> None:
    device_id, user_id, tenant_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    secret = "durable-secret-" + "x" * 48
    platform_session.responses = [
        [
            {
                "id": device_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "pairing_secret_hash": hashlib.sha256(secret.encode()).hexdigest(),
                "plan_key": "free_selfhost",
            }
        ],
        [],
    ]

    response = await pairing_client.post(
        "/v1/devices/pairing/refresh",
        json={"device_id": str(device_id), "device_token": secret},
    )

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["refresh_token"]
    assert "last_seen_at = now()" in platform_session.calls[1][0]


async def test_durable_refresh_rejects_wrong_secret(pairing_client, platform_session) -> None:
    platform_session.responses = [[]]
    response = await pairing_client.post(
        "/v1/devices/pairing/refresh",
        json={"device_id": str(uuid.uuid4()), "device_token": "x" * 40},
    )
    assert response.status_code == 401


async def test_pairing_claim_requires_long_random_token(pairing_client) -> None:
    response = await pairing_client.post(
        "/v1/devices/pairing/claim",
        json={
            "pairing_token": "123456",
            "nombre": "Pixel",
            "plataforma": "android",
            "kind": "mobile",
        },
    )
    assert response.status_code == 422


async def test_pairing_create_fails_closed_when_ttl_is_disabled(
    pairing_client, test_settings
) -> None:
    test_settings.MOBILE_PAIRING_TTL_SECONDS = 0

    response = await pairing_client.post(
        "/v1/devices/pairing",
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4()),
    )

    assert response.status_code == 503
    assert "temporalmente" in response.json()["detail"]
