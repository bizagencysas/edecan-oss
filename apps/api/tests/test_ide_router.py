"""Tests de `/v1/ide/*` (`edecan_api.routers.ide`, ARCHITECTURE.md §10.12,
ROADMAP_V2.md §7.6/§7.8, WP-V2-08).

`ide.router` ya se monta solo en `edecan_api.main.create_app()` (montaje
defensivo de WP-V2-01, ROADMAP_V2.md §7.6) apenas este módulo existe, así
que la fixture `app` de `conftest.py` (que llama `create_app()`) alcanza sin
tocar nada más.

`_FakeCompanionManager` es el "manager fake" pedido por el paquete de
trabajo: implementa solo `is_connected`/`send_command` (el mismo
"protocolo" duck-typed que `ide.py` consume de `ConnectionManager`), así se
prueba el MAPEO de errores de este router (503/504/422) de forma
determinista y sin abrir un WebSocket real -- el protocolo WS en sí
(`ConnectionManager.send_command`/`handle_incoming`) ya está cubierto por
`test_companion.py`.

`companion.ide` es `True` en los 4 planes reales de `edecan_schemas.plans`
hoy, así que la mayoría de los tests de abajo usan `auth_headers()` (JWT
real, sin overridear `get_current_user`) igual que el resto de la suite; el
test del gate de plan (`test_...without_the_companion_ide_flag...`) es el
único que necesita un `plan_key` que NO exista en `PLANES` para poder
ejercitar el camino "sin el flag" contra el catálogo de planes real.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import auth_headers

from edecan_api.companion_manager import CompanionError
from edecan_api.routers import ide


class _FakeCompanionManager:
    """Doble mínimo de `ConnectionManager`: solo `is_connected`/`send_command`."""

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
    app.dependency_overrides[ide.get_companion_manager] = lambda: fake_manager


def _headers() -> dict[str, str]:
    return auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# Autenticación y flag gate
# ---------------------------------------------------------------------------


async def test_status_requires_authentication(app, client):
    response = await client.get("/v1/ide/status")
    assert response.status_code == 401


async def test_status_without_the_companion_ide_flag_is_forbidden(app, client):
    # Un plan_key que no existe en PLANES -> flags_for_plan devuelve {} ->
    # el gate falla cerrado (403), no abierto. Con el catálogo real de hoy
    # los 4 planes conocidos SÍ traen companion.ide=True (ver PLANES).
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="plan_fantasma_sin_ide"
    )

    response = await client.get("/v1/ide/status", headers=headers)

    assert response.status_code == 403
    assert "plan" in response.json()["detail"].lower()


async def test_all_endpoints_are_reachable_with_a_known_plan(app, client):
    """Con un plan real (companion.ide=True) ningún endpoint debe devolver 403 -- si
    el companion no está conectado, deben caer a 503, no quedarse bloqueados por el flag."""
    headers = _headers()

    assert (await client.get("/v1/ide/tree", headers=headers)).status_code == 503
    assert (await client.get("/v1/ide/file?path=a.txt", headers=headers)).status_code == 503
    assert (
        await client.put("/v1/ide/file", headers=headers, json={"path": "a.txt", "content": "x"})
    ).status_code == 503
    assert (
        await client.post(
            "/v1/ide/edit",
            headers=headers,
            json={"path": "a.txt", "old_string": "a", "new_string": "b"},
        )
    ).status_code == 503
    assert (
        await client.post("/v1/ide/run", headers=headers, json={"command": "ls"})
    ).status_code == 503
    assert (
        await client.post("/v1/ide/search", headers=headers, json={"query": "x"})
    ).status_code == 503


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


async def test_status_reports_connected_true(app, client):
    _set_fake_manager(app, _FakeCompanionManager(connected=True))

    response = await client.get("/v1/ide/status", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"connected": True}


async def test_status_reports_connected_false_without_erroring(app, client):
    _set_fake_manager(app, _FakeCompanionManager(connected=False))

    response = await client.get("/v1/ide/status", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"connected": False}


# ---------------------------------------------------------------------------
# Mapeo de errores: 503 sin companion, 504 timeout, 422 ActionError
# ---------------------------------------------------------------------------


async def test_tree_returns_503_when_no_companion_is_connected(app, client):
    _set_fake_manager(app, _FakeCompanionManager(connected=False))

    response = await client.get("/v1/ide/tree", headers=_headers())

    assert response.status_code == 503
    assert "companion" in response.json()["detail"].lower()


async def test_run_returns_504_when_the_companion_does_not_answer_in_time(app, client):
    fake_manager = _FakeCompanionManager(
        connected=True, error=CompanionError("el companion no respondió a tiempo")
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post("/v1/ide/run", headers=_headers(), json={"command": "ls"})

    assert response.status_code == 504


async def test_edit_returns_422_when_the_companion_reports_an_action_error(app, client):
    fake_manager = _FakeCompanionManager(
        connected=True, response={"ok": False, "error": "old_string no se encontró en el archivo"}
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/edit",
        headers=_headers(),
        json={"path": "a.py", "old_string": "no-existe", "new_string": "x"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "old_string no se encontró en el archivo"


async def test_file_write_returns_422_when_action_rejected_without_a_message(app, client):
    """`response.get("error")` puede faltar (defensivo) -- igual debe dar un 422 con texto útil."""
    _set_fake_manager(app, _FakeCompanionManager(connected=True, response={"ok": False}))

    response = await client.put(
        "/v1/ide/file", headers=_headers(), json={"path": "a.txt", "content": "x"}
    )

    assert response.status_code == 422
    assert response.json()["detail"]


# ---------------------------------------------------------------------------
# Camino feliz: parámetros correctos hacia el companion, forma de respuesta
# ---------------------------------------------------------------------------


async def test_get_tree_forwards_query_params_and_returns_the_companion_result(app, client):
    fake_manager = _FakeCompanionManager(
        connected=True,
        response={"ok": True, "result": {"path": "src", "entries": [], "truncated": False}},
    )
    _set_fake_manager(app, fake_manager)

    response = await client.get(
        "/v1/ide/tree?path=src&max_depth=2&max_entries=50", headers=_headers()
    )

    assert response.status_code == 200
    assert response.json() == {"path": "src", "entries": [], "truncated": False}
    assert len(fake_manager.calls) == 1
    _, action, params = fake_manager.calls[0]
    assert action == "list_tree"
    assert params == {"path": "src", "max_depth": 2, "max_entries": 50}


async def test_get_tree_without_query_params_sends_empty_params(app, client):
    fake_manager = _FakeCompanionManager(connected=True)
    _set_fake_manager(app, fake_manager)

    response = await client.get("/v1/ide/tree", headers=_headers())

    assert response.status_code == 200
    _, action, params = fake_manager.calls[0]
    assert action == "list_tree"
    assert params == {}


async def test_get_file_sends_the_path_and_returns_the_companion_result(app, client):
    fake_manager = _FakeCompanionManager(
        connected=True,
        response={
            "ok": True,
            "result": {"path": "a.py", "content": "print(1)", "encoding": "utf-8", "size_bytes": 8},
        },
    )
    _set_fake_manager(app, fake_manager)

    response = await client.get("/v1/ide/file?path=a.py", headers=_headers())

    assert response.status_code == 200
    assert response.json()["content"] == "print(1)"
    _, action, params = fake_manager.calls[0]
    assert action == "read_file"
    assert params == {"path": "a.py"}


async def test_get_file_requires_path_query_param(app, client):
    _set_fake_manager(app, _FakeCompanionManager(connected=True))

    response = await client.get("/v1/ide/file", headers=_headers())

    assert response.status_code == 422  # error de validación de FastAPI, ni llega al companion


async def test_put_file_sends_path_and_content(app, client):
    fake_manager = _FakeCompanionManager(
        connected=True, response={"ok": True, "result": {"path": "a.txt", "bytes_written": 5}}
    )
    _set_fake_manager(app, fake_manager)

    response = await client.put(
        "/v1/ide/file", headers=_headers(), json={"path": "a.txt", "content": "hola!"}
    )

    assert response.status_code == 200
    assert response.json() == {"path": "a.txt", "bytes_written": 5}
    _, action, params = fake_manager.calls[0]
    assert action == "write_file"
    assert params == {"path": "a.txt", "content": "hola!"}


async def test_post_edit_sends_all_fields_including_replace_all_default(app, client):
    fake_manager = _FakeCompanionManager(
        connected=True,
        response={"ok": True, "result": {"path": "a.py", "replacements": 1, "bytes_written": 9}},
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/edit",
        headers=_headers(),
        json={"path": "a.py", "old_string": "return 1", "new_string": "return 2"},
    )

    assert response.status_code == 200
    assert response.json()["replacements"] == 1
    _, action, params = fake_manager.calls[0]
    assert action == "apply_edit"
    assert params == {
        "path": "a.py",
        "old_string": "return 1",
        "new_string": "return 2",
        "replace_all": False,
    }


async def test_post_run_maps_returncode_to_exit_code(app, client):
    fake_manager = _FakeCompanionManager(
        connected=True,
        response={
            "ok": True,
            "result": {"returncode": 0, "stdout": "hola\n", "stderr": "", "truncated": False},
        },
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post("/v1/ide/run", headers=_headers(), json={"command": "echo hola"})

    assert response.status_code == 200
    assert response.json() == {
        "stdout": "hola\n",
        "stderr": "",
        "exit_code": 0,
        "truncated": False,
    }
    _, action, params = fake_manager.calls[0]
    assert action == "run_command"
    assert params == {"command": "echo hola"}


async def test_post_run_uses_a_longer_timeout_than_the_default(app, client):
    seen_timeouts = []

    class _TimeoutSpyManager(_FakeCompanionManager):
        async def send_command(self, tenant_id, action, params, timeout=30):
            seen_timeouts.append(timeout)
            return await super().send_command(tenant_id, action, params, timeout=timeout)

    _set_fake_manager(
        app,
        _TimeoutSpyManager(
            connected=True,
            response={"ok": True, "result": {"returncode": 0, "stdout": "", "stderr": ""}},
        ),
    )

    await client.post("/v1/ide/run", headers=_headers(), json={"command": "ls"})

    assert seen_timeouts == [ide.IDE_RUN_TIMEOUT_SECONDS]
    assert ide.IDE_RUN_TIMEOUT_SECONDS > 30  # más que COMMAND_TIMEOUT_SECONDS del companion (30s)


async def test_post_search_omits_path_when_not_given(app, client):
    fake_manager = _FakeCompanionManager(
        connected=True,
        response={"ok": True, "result": {"query": "hola", "matches": [], "truncated": False}},
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post("/v1/ide/search", headers=_headers(), json={"query": "hola"})

    assert response.status_code == 200
    _, action, params = fake_manager.calls[0]
    assert action == "search_files"
    assert params == {"query": "hola"}


async def test_post_search_forwards_path_when_given(app, client):
    fake_manager = _FakeCompanionManager(connected=True)
    _set_fake_manager(app, fake_manager)

    await client.post(
        "/v1/ide/search", headers=_headers(), json={"query": "hola", "path": "src"}
    )

    _, action, params = fake_manager.calls[0]
    assert action == "search_files"
    assert params == {"query": "hola", "path": "src"}


async def test_post_edit_requires_a_non_empty_old_string(app, client):
    _set_fake_manager(app, _FakeCompanionManager(connected=True))

    response = await client.post(
        "/v1/ide/edit",
        headers=_headers(),
        json={"path": "a.py", "old_string": "", "new_string": "x"},
    )

    # validación de pydantic (min_length=1): ni siquiera llama al companion.
    assert response.status_code == 422


@pytest.mark.parametrize(
    "plan_key", ["free_selfhost", "hosted_basic", "hosted_pro", "hosted_business"]
)
async def test_companion_ide_flag_is_true_for_every_real_plan(app, client, plan_key):
    """Documenta/protege ROADMAP_V2.md §7.2: la matriz dice `ide=✔` en los 4 planes."""
    _set_fake_manager(app, _FakeCompanionManager(connected=True))
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key=plan_key)

    response = await client.get("/v1/ide/status", headers=headers)

    assert response.status_code == 200
