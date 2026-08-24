"""LSP (motor opencode): el cable HTTP que faltaba.

``edecan_companion.ide_opencode_lsp.ClienteLspOpencode`` ya envolvía ``GET
/find/symbol``/``GET /lsp`` de opencode -- construido, verificado contra un
``opencode serve`` real, y sin cable (ver su propio docstring). La ronda que
agregó estos tests conectó ese cliente a cuatro acciones IDE
(``ide_lsp_symbols``/``status``/``definition``/``references``, ver
``edecan_companion.ide_runtime``) y estas cuatro rutas REST.

Estos tests cubren solo el mapeo router→companion (nombre de acción,
parámetros) y el código HTTP que corresponde a cada forma de respuesta --
igual que ``test_ide_pregunta_permiso_router.py``. El comportamiento REAL del
cliente LSP (por qué casi siempre devuelve vacío, por qué
``definition``/``references`` no existen en opencode) ya está probado en
``apps/companion/tests/test_ide_opencode_lsp.py`` y
``apps/companion/tests/test_ide_runtime.py``; acá no se repite.
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
        # Sobreescribible por test: la mayoría solo necesita verificar el
        # mapeo de acción/params con la respuesta "éxito" de siempre.
        self.response: dict[str, Any] = {"ok": True, "result": {}}

    def is_connected(self, tenant_id: uuid.UUID) -> bool:
        return True

    async def send_command(
        self, tenant_id: uuid.UUID, action: str, params: dict[str, Any], timeout: float = 30
    ) -> dict[str, Any]:
        self.calls.append((tenant_id, action, dict(params)))
        return self.response


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


# --- Mapeo acción/params ----------------------------------------------------


async def test_get_lsp_status(client, fake):
    r = await client.get("/v1/ide/workspaces/w1/lsp/status", headers=_headers())
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_lsp_status"
    assert params == {"workspace_id": "w1"}


async def test_get_lsp_symbols(client, fake):
    r = await client.get("/v1/ide/workspaces/w1/lsp/symbols?query=saludar", headers=_headers())
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_lsp_symbols"
    assert params == {"workspace_id": "w1", "query": "saludar"}


async def test_get_lsp_symbols_exige_query(client, fake):
    r = await client.get("/v1/ide/workspaces/w1/lsp/symbols", headers=_headers())
    assert r.status_code == 422
    assert not fake.calls


async def test_get_lsp_definition(client, fake):
    r = await client.get(
        "/v1/ide/workspaces/w1/lsp/definition?path=util.py&line=3&character=5",
        headers=_headers(),
    )
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_lsp_definition"
    assert params == {"workspace_id": "w1", "path": "util.py", "line": 3, "character": 5}


async def test_get_lsp_definition_exige_line_y_character(client, fake):
    r = await client.get("/v1/ide/workspaces/w1/lsp/definition?path=util.py", headers=_headers())
    assert r.status_code == 422
    assert not fake.calls


async def test_get_lsp_references(client, fake):
    r = await client.get(
        "/v1/ide/workspaces/w1/lsp/references?path=util.py&line=3&character=5",
        headers=_headers(),
    )
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_lsp_references"
    assert params == {"workspace_id": "w1", "path": "util.py", "line": 3, "character": 5}


# --- Código HTTP según la forma de la respuesta del companion --------------


async def test_lsp_no_disponible_responde_501_no_422(client, fake):
    # LspOpencodeNoDisponibleError (`ide_opencode_lsp.py`) traducido por
    # `ide_runtime.execute_ide_action` con `lsp_no_disponible: True`: esta
    # capacidad no existe en opencode -- 501, distinto del 422 genérico del
    # resto de rechazos del companion, para que la interfaz pueda distinguir
    # "esto nunca va a funcionar" de "esta vez falló".
    fake.response = {
        "ok": False,
        "error": "opencode no expone textDocument/definition",
        "lsp_no_disponible": True,
    }
    r = await client.get(
        "/v1/ide/workspaces/w1/lsp/definition?path=util.py&line=0&character=0",
        headers=_headers(),
    )
    assert r.status_code == 501
    assert r.json()["detail"] == "opencode no expone textDocument/definition"


async def test_companion_rechaza_sin_marca_responde_422(client, fake):
    fake.response = {"ok": False, "error": "workspace no autorizado"}
    r = await client.get("/v1/ide/workspaces/w1/lsp/status", headers=_headers())
    assert r.status_code == 422
    assert r.json()["detail"] == "workspace no autorizado"


# --- Autenticación -----------------------------------------------------------


async def test_las_rutas_lsp_exigen_autenticacion(client):
    assert (await client.get("/v1/ide/workspaces/w1/lsp/status")).status_code == 401
    assert (await client.get("/v1/ide/workspaces/w1/lsp/symbols?query=x")).status_code == 401
    assert (
        await client.get("/v1/ide/workspaces/w1/lsp/definition?path=a&line=0&character=0")
    ).status_code == 401
    assert (
        await client.get("/v1/ide/workspaces/w1/lsp/references?path=a&line=0&character=0")
    ).status_code == 401
