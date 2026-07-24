"""Frontera dispositivo+transporte del IDE avanzado."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps


class _Mappings:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def first(self) -> dict[str, Any] | None:
        return self._row


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> _Mappings:
        return _Mappings(self._row)


class _DeviceSession:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement, params):
        self.calls.append((str(statement), dict(params)))
        return _Result(self.row)


def _identity() -> tuple[uuid.UUID, uuid.UUID, dict[str, str]]:
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    return user_id, tenant_id, auth_headers(user_id=user_id, tenant_id=tenant_id)


def _device_headers(device_id: uuid.UUID, token: str) -> dict[str, str]:
    return {
        "X-Edecan-Device-Id": str(device_id),
        "X-Edecan-Device-Token": token,
    }


async def _client(app, *, scheme: str, host: str) -> AsyncClient:
    transport = ASGITransport(app=app, client=(host, 43210))
    return AsyncClient(transport=transport, base_url=f"{scheme}://edecan.test")


@pytest.mark.parametrize(
    "path",
    [
        "/v1/ide/workspaces",
        "/v1/ide/terminals",
        "/v1/ide/agents",
        "/v1/ide/workspaces/workspace-1/git/status",
    ],
)
async def test_every_advanced_ide_family_requires_paired_device(app, path):
    _, _, headers = _identity()
    async with await _client(app, scheme="https", host="198.51.100.7") as client:
        response = await client.get(path, headers=headers)
    assert response.status_code == 403
    assert "emparejado" in response.json()["detail"].lower()


async def test_legacy_ide_remains_available_without_device_headers(app, client):
    _, _, headers = _identity()
    response = await client.get("/v1/ide/status", headers=headers)
    assert response.status_code == 200


async def test_advanced_ide_accepts_active_device_for_same_user_and_tenant_over_https(app):
    user_id, tenant_id, headers = _identity()
    device_id = uuid.uuid4()
    device_token = "d" * 48
    session = _DeviceSession(
        {"pairing_secret_hash": hashlib.sha256(device_token.encode()).hexdigest()}
    )
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: session
    headers.update(_device_headers(device_id, device_token))

    async with await _client(app, scheme="https", host="198.51.100.7") as client:
        response = await client.get("/v1/ide/workspaces", headers=headers)

    # Superó el gate y llegó al manager (sin companion en esta prueba).
    assert response.status_code == 503
    assert len(session.calls) == 1
    sql, params = session.calls[0]
    assert "kind = 'mobile'" in sql
    assert "status = 'active'" in sql
    assert params == {
        "device_id": str(device_id),
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
    }


async def test_advanced_ide_rejects_wrong_or_revoked_device_secret(app):
    _, _, headers = _identity()
    device_id = uuid.uuid4()
    session = _DeviceSession(
        {"pairing_secret_hash": hashlib.sha256(b"correct-device-secret").hexdigest()}
    )
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: session
    headers.update(_device_headers(device_id, "wrong-device-secret-" + "x" * 32))

    async with await _client(app, scheme="https", host="198.51.100.7") as client:
        wrong = await client.get("/v1/ide/workspaces", headers=headers)
    assert wrong.status_code == 403

    session.row = None  # revocado, cross-tenant o inexistente: la query no devuelve fila
    headers.update(_device_headers(device_id, "correct-device-secret-" + "x" * 32))
    async with await _client(app, scheme="https", host="198.51.100.7") as client:
        revoked = await client.get("/v1/ide/workspaces", headers=headers)
    assert revoked.status_code == 403


async def test_advanced_ide_rejects_plain_http_from_lan_before_checking_secret(app):
    _, _, headers = _identity()
    headers.update(_device_headers(uuid.uuid4(), "s" * 48))
    session = _DeviceSession(
        {"pairing_secret_hash": hashlib.sha256(("s" * 48).encode()).hexdigest()}
    )
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: session

    async with await _client(app, scheme="http", host="192.168.1.20") as client:
        response = await client.get("/v1/ide/workspaces", headers=headers)

    assert response.status_code == 426
    assert "https" in response.json()["detail"].lower()
    assert session.calls == []


async def test_forwarded_https_is_only_trusted_from_loopback_proxy(app):
    _, _, headers = _identity()
    token = "s" * 48
    headers.update(_device_headers(uuid.uuid4(), token))
    headers["X-Forwarded-Proto"] = "https"
    session = _DeviceSession(
        {"pairing_secret_hash": hashlib.sha256(token.encode()).hexdigest()}
    )
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: session

    async with await _client(app, scheme="http", host="192.168.1.20") as client:
        spoofed = await client.get("/v1/ide/workspaces", headers=headers)
    assert spoofed.status_code == 426

    async with await _client(app, scheme="http", host="127.0.0.1") as client:
        proxied = await client.get("/v1/ide/workspaces", headers=headers)
    assert proxied.status_code == 503


async def test_loopback_development_transport_is_accepted(app):
    _, _, headers = _identity()
    token = "l" * 48
    headers.update(_device_headers(uuid.uuid4(), token))
    session = _DeviceSession(
        {"pairing_secret_hash": hashlib.sha256(token.encode()).hexdigest()}
    )
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: session

    async with await _client(app, scheme="http", host="::1") as client:
        response = await client.get("/v1/ide/workspaces", headers=headers)
    assert response.status_code == 503
