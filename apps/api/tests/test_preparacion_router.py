"""Tests de `/v1/preparacion` (`edecan_api.routers.preparacion`).

Mismo criterio que `test_ide_router.py` (que este router imita a propósito):
`_FakeCompanionManager` es el doble mínimo de `ConnectionManager`
(`is_connected`/`send_command`) que prueba el MAPEO de errores del router
(503/504/422) y el reenvío de parámetros de forma determinista, sin abrir un
WebSocket real. La regla de seguridad de fondo -- que un `id` de requisito
que no está en el manifiesto se rechaza antes de tocar el sistema -- ya está
probada del lado companion en `apps/companion/tests/test_preparacion.py`
(`EjecutorPreparacion._requisito`); acá se prueba que el router TRADUCE esa
respuesta `{"ok": false, ...}` a un 422, que es lo único que le corresponde a
esta capa.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import auth_headers

from edecan_api.companion_manager import CompanionError
from edecan_api.ide_security import PairedIDEDevice
from edecan_api.routers import preparacion


class _FakeCompanionManager:
    """Doble mínimo de `ConnectionManager` -- ver `test_ide_router.py`."""

    def __init__(
        self,
        *,
        connected: bool = True,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.connected = connected
        self.response = response if response is not None else {"ok": True, "result": {}}
        self.error = error
        self.calls: list[tuple[uuid.UUID, str, dict[str, Any]]] = []

    def is_connected(self, tenant_id: uuid.UUID) -> bool:
        return self.connected

    async def send_command(
        self, tenant_id: uuid.UUID, action: str, params: dict[str, Any], timeout: float = 30
    ) -> dict[str, Any]:
        self.calls.append((tenant_id, action, dict(params)))
        if self.error is not None:
            raise self.error
        return self.response


def _set_fake_manager(app, fake_manager: _FakeCompanionManager) -> None:
    app.dependency_overrides[preparacion.get_companion_manager] = lambda: fake_manager


def _headers() -> dict[str, str]:
    return auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())


@pytest.fixture(autouse=True)
def _paired_device_for_router_contract_tests(app):
    """Mismo doble que `test_ide_router.py`: estos tests cubren mapeo
    router->companion, no la criptografía del pairing (`test_ide_security.py`)."""

    async def paired() -> PairedIDEDevice:
        return PairedIDEDevice(device_id=uuid.uuid4())

    app.dependency_overrides[preparacion.require_paired_ide_device] = paired
    yield
    app.dependency_overrides.pop(preparacion.require_paired_ide_device, None)


# ---------------------------------------------------------------------------
# Autenticación y flag de plan
# ---------------------------------------------------------------------------


async def test_get_preparacion_requires_authentication(app, client):
    response = await client.get("/v1/preparacion")

    assert response.status_code == 401


async def test_get_preparacion_without_the_companion_ide_flag_is_forbidden(app, client):
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="plan_fantasma_sin_ide"
    )

    response = await client.get("/v1/preparacion", headers=headers)

    assert response.status_code == 403
    assert "plan" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Mapeo de errores: 503 sin companion, 504 timeout, 422 acción rechazada
# ---------------------------------------------------------------------------


async def test_get_preparacion_returns_503_when_no_companion_is_connected(app, client):
    _set_fake_manager(app, _FakeCompanionManager(connected=False))

    response = await client.get("/v1/preparacion", headers=_headers())

    assert response.status_code == 503
    assert "companion" in response.json()["detail"].lower()


async def test_post_instalar_returns_504_when_the_companion_does_not_answer_in_time(app, client):
    fake_manager = _FakeCompanionManager(
        connected=True, error=CompanionError("el companion no respondió a tiempo")
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post("/v1/preparacion/git/instalar", headers=_headers())

    assert response.status_code == 504


async def test_post_instalar_returns_422_for_a_requisito_id_outside_the_manifest(app, client):
    """El companion es quien de verdad rechaza el id (ver
    `EjecutorPreparacion._requisito` en `edecan_companion.preparacion`); este
    test prueba que el router traduce ESA respuesta a 422, tal como lo pide
    el encargo ("el endpoint rechaza un id que no está en el manifiesto")."""
    fake_manager = _FakeCompanionManager(
        connected=True,
        response={"ok": False, "error": "Requisito desconocido: 'no-existe'."},
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post("/v1/preparacion/no-existe/instalar", headers=_headers())

    assert response.status_code == 422
    assert "desconocido" in response.json()["detail"].lower()


async def test_get_preparacion_read_returns_422_for_a_requisito_id_outside_the_manifest(
    app, client
):
    fake_manager = _FakeCompanionManager(
        connected=True,
        response={"ok": False, "error": "Requisito desconocido: 'no-existe'."},
    )
    _set_fake_manager(app, fake_manager)

    response = await client.get("/v1/preparacion/no-existe", headers=_headers())

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Camino feliz: acción y parámetros correctos hacia el companion
# ---------------------------------------------------------------------------


async def test_get_preparacion_forwards_to_ide_preparacion_list_with_no_params(app, client):
    fake_manager = _FakeCompanionManager(
        connected=True,
        response={
            "ok": True,
            "result": {
                "requisitos": [
                    {
                        "id": "git",
                        "nombre": "Git",
                        "por_que": "El panel de Git del IDE depende de esto.",
                        "estado": "falta",
                        "instalable": True,
                        "requiere_admin": False,
                        "obligatorio": True,
                    }
                ],
                "elevado": False,
            },
        },
    )
    _set_fake_manager(app, fake_manager)

    response = await client.get("/v1/preparacion", headers=_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["elevado"] is False
    assert body["requisitos"][0]["id"] == "git"
    assert fake_manager.calls[-1][1] == "ide_preparacion_list"
    assert fake_manager.calls[-1][2] == {}


async def test_post_instalar_forwards_the_path_id_as_the_only_param(app, client):
    fake_manager = _FakeCompanionManager(
        connected=True,
        response={"ok": True, "result": {"id": "git", "estado": "ejecutando", "error": None}},
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post("/v1/preparacion/git/instalar", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"id": "git", "estado": "ejecutando", "error": None}
    assert fake_manager.calls[-1][1] == "ide_preparacion_instalar"
    assert fake_manager.calls[-1][2] == {"id": "git"}


async def test_get_preparacion_read_forwards_id_and_cursor(app, client):
    fake_manager = _FakeCompanionManager(
        connected=True,
        response={
            "ok": True,
            "result": {
                "id": "git",
                "estado": "completado",
                "error": None,
                "events": [{"cursor": 1, "type": "exit", "text": "listo", "timestamp": "now"}],
                "next_cursor": 1,
                "has_more": False,
            },
        },
    )
    _set_fake_manager(app, fake_manager)

    response = await client.get("/v1/preparacion/git?cursor=3", headers=_headers())

    assert response.status_code == 200
    assert response.json()["next_cursor"] == 1
    assert fake_manager.calls[-1][1] == "ide_preparacion_leer"
    assert fake_manager.calls[-1][2] == {"id": "git", "cursor": 3}


async def test_get_preparacion_read_defaults_cursor_to_zero(app, client):
    fake_manager = _FakeCompanionManager(connected=True)
    _set_fake_manager(app, fake_manager)

    response = await client.get("/v1/preparacion/git", headers=_headers())

    assert response.status_code == 200
    assert fake_manager.calls[-1][2] == {"id": "git", "cursor": 0}


async def test_get_preparacion_read_rejects_negative_cursor(app, client):
    fake_manager = _FakeCompanionManager(connected=True)
    _set_fake_manager(app, fake_manager)

    response = await client.get("/v1/preparacion/git?cursor=-1", headers=_headers())

    assert response.status_code == 422
