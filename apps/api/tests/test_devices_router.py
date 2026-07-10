"""`edecan_api.routers.devices` — `/v1/devices/*` (`ARCHITECTURE.md` §13, WP-V4-01).

`edecan_api.main.create_app()` YA monta `devices.router` de forma defensiva
(`V4_ROUTER_NAMES`, ver `test_v4_mounting.py`), pero — mismo criterio que
`test_remote_router.py`/`test_commerce_router.py` — este archivo monta el
router manualmente sobre la `app` de `conftest.py` para no depender de eso.

`fake_session` reemplaza `edecan_api.deps.get_tenant_session` con un doble
LOCAL (`FakeSession`/`FakeResult`, mismo patrón que `test_commerce_router.py`):
consume una cola de respuestas programadas, en el orden EXACTO en que el
router las pide. `get_repo` NO se sobreescribe aquí: sigue apuntando al
`fake_repo` que `conftest.py::app` ya inyecta (`FakeRepo`, en memoria) — por
eso `revoke_device` (el único endpoint que usa `repo.add_audit_log`, ver el
docstring del router) deja su rastro en `fake_repo.audit_log`, nunca en
`fake_session` (dos colaboradores desacoplados en los tests, tal como pide el
paquete de trabajo: "NO edites repo.py ni conftest.py/api_fakes.py — dobles
locales en el test si hace falta").
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

import edecan_api.deps as edecan_deps
from edecan_api.routers import devices


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


@dataclass
class FakeSession:
    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


def _device_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "nombre": "MacBook de Prueba",
        "plataforma": "macos",
        "kind": "companion",
        "status": "active",
        "last_seen_at": None,
        "fingerprint": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession):
    """`app` (de `conftest.py`) + `devices.router` montado + `get_tenant_session`
    reemplazado por `fake_session` — `get_repo` sigue siendo el `fake_repo` que
    `conftest.py` ya inyecta (ver docstring del módulo)."""
    app.include_router(devices.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    # Sombrea a propósito el fixture `client` de `conftest.py`: ese vive sobre
    # `app` sin `devices.router` montado.
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers(**kw: Any) -> dict[str, str]:
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("tenant_id", uuid.uuid4())
    return auth_headers(**kw)


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------


async def test_list_devices_requires_authentication(client) -> None:
    response = await client.get("/v1/devices")
    assert response.status_code == 401


async def test_create_device_requires_authentication(client) -> None:
    response = await client.post(
        "/v1/devices", json={"nombre": "iPhone", "plataforma": "ios", "kind": "mobile"}
    )
    assert response.status_code == 401


async def test_heartbeat_requires_authentication(client) -> None:
    response = await client.post(f"/v1/devices/{uuid.uuid4()}/heartbeat")
    assert response.status_code == 401


async def test_revoke_requires_authentication(client) -> None:
    response = await client.post(f"/v1/devices/{uuid.uuid4()}/revoke")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET "" — lista del tenant
# ---------------------------------------------------------------------------


async def test_list_devices_returns_rows_scoped_to_tenant(client, fake_session) -> None:
    fila = _device_row()
    fake_session.respuestas = [[fila]]

    response = await client.get("/v1/devices", headers=_headers())

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(fila["id"])
    assert body[0]["nombre"] == "MacBook de Prueba"

    sql, params = fake_session.llamadas[0]
    assert "FROM devices" in sql
    assert "tenant_id = :tenant_id" in sql
    assert set(params) == {"tenant_id"}


async def test_list_devices_empty(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.get("/v1/devices", headers=_headers())
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# POST "" — creación simple (sin fingerprint) y validación
# ---------------------------------------------------------------------------


async def test_create_device_without_fingerprint_always_inserts_and_returns_201(
    client, fake_session
) -> None:
    creada = _device_row(nombre="Mi PC", plataforma="windows", kind="companion")
    fake_session.respuestas = [[creada]]  # una sola llamada: el INSERT

    response = await client.post(
        "/v1/devices",
        json={"nombre": "Mi PC", "plataforma": "windows", "kind": "companion"},
        headers=_headers(),
    )

    assert response.status_code == 201
    assert response.json()["id"] == str(creada["id"])
    assert len(fake_session.llamadas) == 1
    sql, params = fake_session.llamadas[0]
    assert "INSERT INTO devices" in sql
    assert params["fingerprint"] is None


async def test_create_device_rejects_invalid_kind(client) -> None:
    response = await client.post(
        "/v1/devices",
        json={"nombre": "X", "plataforma": "linux", "kind": "servidor"},
        headers=_headers(),
    )
    assert response.status_code == 422


async def test_create_device_rejects_blank_nombre(client) -> None:
    response = await client.post(
        "/v1/devices",
        json={"nombre": "", "plataforma": "ios", "kind": "mobile"},
        headers=_headers(),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST "" — idempotencia por fingerprint (apps móviles)
# ---------------------------------------------------------------------------


async def test_create_device_with_fingerprint_no_match_inserts_new_and_returns_201(
    client, fake_session
) -> None:
    creada = _device_row(fingerprint="abc123")
    fake_session.respuestas = [[], [creada]]  # 1) SELECT existing (vacío) 2) INSERT

    response = await client.post(
        "/v1/devices",
        json={"nombre": "iPhone", "plataforma": "ios", "kind": "mobile", "fingerprint": "abc123"},
        headers=_headers(),
    )

    assert response.status_code == 201
    assert len(fake_session.llamadas) == 2
    sql_select, params_select = fake_session.llamadas[0]
    assert "SELECT * FROM devices" in sql_select
    assert "fingerprint = :fingerprint" in sql_select
    assert "status = 'active'" in sql_select
    assert params_select["fingerprint"] == "abc123"
    sql_insert, _ = fake_session.llamadas[1]
    assert "INSERT INTO devices" in sql_insert


async def test_create_device_with_fingerprint_match_updates_existing_and_returns_200(
    client, fake_session
) -> None:
    existente = _device_row(fingerprint="abc123", nombre="iPhone viejo")
    actualizado = _device_row(
        id=existente["id"], fingerprint="abc123", nombre="iPhone de Prueba"
    )
    fake_session.respuestas = [[existente], [actualizado]]  # 1) SELECT match 2) UPDATE

    response = await client.post(
        "/v1/devices",
        json={
            "nombre": "iPhone de Prueba",
            "plataforma": "ios",
            "kind": "mobile",
            "fingerprint": "abc123",
        },
        headers=_headers(),
    )

    assert response.status_code == 200  # NO 201: coincidió con uno existente
    body = response.json()
    assert body["id"] == str(existente["id"])
    assert body["nombre"] == "iPhone de Prueba"
    assert len(fake_session.llamadas) == 2
    sql_update, params_update = fake_session.llamadas[1]
    assert "UPDATE devices" in sql_update
    assert "last_seen_at = now()" in sql_update
    assert params_update["nombre"] == "iPhone de Prueba"


async def test_create_device_empty_fingerprint_string_is_treated_as_no_fingerprint(
    client, fake_session
) -> None:
    """`fingerprint=""` no debe disparar la búsqueda de idempotencia (mismo
    criterio que `fingerprint=None`) — ver docstring del router."""
    creada = _device_row(fingerprint=None)
    fake_session.respuestas = [[creada]]  # una sola llamada: el INSERT directo

    response = await client.post(
        "/v1/devices",
        json={"nombre": "X", "plataforma": "linux", "kind": "companion", "fingerprint": ""},
        headers=_headers(),
    )

    assert response.status_code == 201
    assert len(fake_session.llamadas) == 1


# ---------------------------------------------------------------------------
# POST /{id}/heartbeat
# ---------------------------------------------------------------------------


async def test_heartbeat_success_returns_204(client, fake_session) -> None:
    device_id = uuid.uuid4()
    fake_session.respuestas = [[{"id": device_id}]]

    response = await client.post(f"/v1/devices/{device_id}/heartbeat", headers=_headers())

    assert response.status_code == 204
    assert response.content == b""
    sql, params = fake_session.llamadas[0]
    assert "UPDATE devices SET last_seen_at = now()" in sql
    assert params["id"] == str(device_id)


async def test_heartbeat_404_for_unknown_device(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.post(f"/v1/devices/{uuid.uuid4()}/heartbeat", headers=_headers())
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /{id}/revoke — incluye auditoría real vía repo.add_audit_log
# ---------------------------------------------------------------------------


async def test_revoke_success_sets_status_and_audits(client, fake_session, fake_repo) -> None:
    device_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    revocado = _device_row(id=device_id, tenant_id=tenant_id, user_id=user_id, status="revoked")
    fake_session.respuestas = [[revocado]]

    response = await client.post(
        f"/v1/devices/{device_id}/revoke",
        headers=_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "revoked"

    sql, params = fake_session.llamadas[0]
    assert "SET status = 'revoked'" in sql
    assert params["id"] == str(device_id)

    assert len(fake_repo.audit_log) == 1
    entry = fake_repo.audit_log[0]
    assert entry["action"] == "devices.revoked"
    assert entry["target"] == str(device_id)
    assert entry["tenant_id"] == tenant_id
    assert entry["actor_user_id"] == user_id


async def test_revoke_404_for_unknown_device_does_not_audit(
    client, fake_session, fake_repo
) -> None:
    fake_session.respuestas = [[]]
    response = await client.post(f"/v1/devices/{uuid.uuid4()}/revoke", headers=_headers())
    assert response.status_code == 404
    assert fake_repo.audit_log == []
