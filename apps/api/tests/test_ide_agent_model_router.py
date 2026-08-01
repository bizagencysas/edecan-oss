"""`POST /v1/ide/agents/{session_id}/model`: el cable que faltaba.

`SessionManager.set_modelo_agente` (`apps/companion/edecan_companion/ide_sessions.py`)
ya existía y aplicaba el cambio de modelo EN VIVO a una sesión de opencode
viva -- pero no estaba en `IDE_ACTIONS` ni tenía ruta REST, así que el
selector de modelo de la interfaz solo podía guardar la preferencia para el
PRÓXIMO arranque, nunca aplicarla a una sesión ya en marcha. Este test cubre
el mapeo router→companion, mismo patrón (y mismos dobles) que
`test_ide_pregunta_permiso_router.py`/`test_ide_plan_router.py`: no valida la
criptografía del emparejamiento ni el comportamiento real de `SessionManager`
(eso lo cubre `apps/companion/tests/test_ide_sessions.py`, fuera de este
paquete de trabajo).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import auth_headers

from edecan_api.ide_security import PairedIDEDevice
from edecan_api.routers import ide


class _FakeCompanionManager:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, str, dict[str, Any]]] = []

    def is_connected(self, tenant_id: uuid.UUID) -> bool:
        return True

    async def send_command(
        self, tenant_id: uuid.UUID, action: str, params: dict[str, Any], timeout: float = 30
    ) -> dict[str, Any]:
        self.calls.append((tenant_id, action, dict(params)))
        return {"ok": True, "result": {"model": params["model"]}}


@pytest.fixture
def fake(app) -> _FakeCompanionManager:
    manager = _FakeCompanionManager()
    app.dependency_overrides[ide.get_companion_manager] = lambda: manager
    yield manager
    app.dependency_overrides.pop(ide.get_companion_manager, None)


@pytest.fixture(autouse=True)
def _paired(app):
    async def paired() -> PairedIDEDevice:
        return PairedIDEDevice(device_id=uuid.uuid4())

    app.dependency_overrides[ide.require_paired_ide_device] = paired
    yield
    app.dependency_overrides.pop(ide.require_paired_ide_device, None)


def _headers() -> dict[str, str]:
    return auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())


# Modelo real del catálogo (`config/modelos.yml -> modelos_ide`), el mismo
# que usa `modelo_ide_permitido` -- no un id inventado.
_MODELO_PERMITIDO = "@cf/zai-org/glm-5.2"


async def test_cambiar_modelo_manda_la_accion_de_puente_al_companion(client, fake):
    r = await client.post(
        "/v1/ide/agents/s1/model",
        headers=_headers(),
        json={"model": _MODELO_PERMITIDO},
    )
    assert r.status_code == 200
    assert r.json() == {"model": _MODELO_PERMITIDO}
    _, accion, params = fake.calls[-1]
    assert accion == "ide_agent_model_set"
    assert params == {"session_id": "s1", "model": _MODELO_PERMITIDO}


async def test_modelo_fuera_de_catalogo_es_422_antes_de_llamar_al_companion(client, fake):
    r = await client.post(
        "/v1/ide/agents/s1/model",
        headers=_headers(),
        json={"model": "@cf/no-existe/inventado"},
    )
    assert r.status_code == 422
    assert not fake.calls


async def test_model_vacio_es_422(client, fake):
    r = await client.post(
        "/v1/ide/agents/s1/model",
        headers=_headers(),
        json={"model": ""},
    )
    assert r.status_code == 422
    assert not fake.calls


async def test_sin_campo_model_es_422(client, fake):
    r = await client.post("/v1/ide/agents/s1/model", headers=_headers(), json={})
    assert r.status_code == 422
    assert not fake.calls


async def test_la_ruta_exige_autenticacion(client):
    r = await client.post(
        "/v1/ide/agents/s1/model", json={"model": _MODELO_PERMITIDO}
    )
    assert r.status_code == 401
