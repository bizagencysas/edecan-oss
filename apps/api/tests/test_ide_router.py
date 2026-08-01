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
from edecan_api.ide_security import PairedIDEDevice
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


@pytest.fixture(autouse=True)
def _paired_device_for_router_contract_tests(app):
    """Estos tests cubren mapeo router→companion, no criptografía del pairing.

    La frontera real tiene su suite dedicada en ``test_ide_security.py``.
    """

    async def paired() -> PairedIDEDevice:
        return PairedIDEDevice(device_id=uuid.uuid4())

    app.dependency_overrides[ide.require_paired_ide_device] = paired
    yield
    app.dependency_overrides.pop(ide.require_paired_ide_device, None)


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

    await client.post("/v1/ide/search", headers=_headers(), json={"query": "hola", "path": "src"})

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


# ---------------------------------------------------------------------------
# Workspaces, sesiones durables y Git tipado
# ---------------------------------------------------------------------------


async def test_workspace_create_maps_to_companion_and_returns_direct_workspace(app, client):
    workspace = {
        "id": str(uuid.uuid4()),
        "name": "Edecán",
        "path": "/tmp/edecan",
        "active": True,
        "created_at": "2026-07-23T12:00:00+00:00",
    }
    fake_manager = _FakeCompanionManager(response={"ok": True, "result": {"workspace": workspace}})
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/workspaces",
        headers=_headers(),
        json={"path": "/tmp/edecan", "name": "Edecán"},
    )

    assert response.status_code == 200
    assert response.json() == workspace
    _, action, params = fake_manager.calls[0]
    assert action == "ide_workspace_authorize"
    assert params == {"path": "/tmp/edecan", "name": "Edecán"}


async def test_workspace_picker_opens_the_local_dialog_without_receiving_a_path(app, client):
    workspace = {
        "id": str(uuid.uuid4()),
        "name": "Proyecto elegido",
        "path": "/tmp/proyecto",
        "active": True,
        "created_at": "2026-07-23T12:00:00+00:00",
        "available": True,
        "is_git_repository": False,
    }
    seen_timeouts: list[float] = []

    class _TimeoutSpy(_FakeCompanionManager):
        async def send_command(self, tenant_id, action, params, timeout=30):
            seen_timeouts.append(timeout)
            return await super().send_command(tenant_id, action, params, timeout=timeout)

    fake_manager = _TimeoutSpy(
        response={
            "ok": True,
            "result": {"cancelled": False, "workspace": workspace},
        }
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/workspaces/pick",
        headers=_headers(),
        json={"name": "Proyecto elegido", "activate": True},
    )

    assert response.status_code == 200
    assert response.json() == {"cancelled": False, "workspace": workspace}
    assert fake_manager.calls[0][1:] == (
        "ide_workspace_pick",
        {"name": "Proyecto elegido", "activate": True},
    )
    assert seen_timeouts == [ide.IDE_FOLDER_PICKER_TIMEOUT_SECONDS]


async def test_workspace_clone_forwards_only_typed_validated_fields_and_long_timeout(app, client):
    workspace_id = str(uuid.uuid4())
    result = {
        "workspace": {"id": str(uuid.uuid4()), "name": "demo"},
        "repository": {
            "name": "demo",
            "source_host": "github.com",
            "branch": "main",
            "depth": 1,
        },
    }
    seen_timeouts: list[float] = []

    class _TimeoutSpy(_FakeCompanionManager):
        async def send_command(self, tenant_id, action, params, timeout=30):
            seen_timeouts.append(timeout)
            return await super().send_command(tenant_id, action, params, timeout=timeout)

    fake_manager = _TimeoutSpy(response={"ok": True, "result": result})
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/workspaces/clone",
        headers=_headers(),
        json={
            "parent_workspace_id": workspace_id,
            "url": "https://github.com/example/demo.git",
            "name": "demo",
            "branch": "main",
            "depth": 1,
            "activate": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == result
    assert fake_manager.calls[0][1:] == (
        "ide_workspace_clone",
        {
            "parent_workspace_id": workspace_id,
            "url": "https://github.com/example/demo.git",
            "name": "demo",
            "branch": "main",
            "depth": 1,
            "activate": True,
        },
    )
    assert seen_timeouts == [ide.IDE_CLONE_TIMEOUT_SECONDS]


async def test_workspace_scoped_file_never_sends_an_absolute_root(app, client):
    fake_manager = _FakeCompanionManager(
        response={"ok": True, "result": {"path": "src/a.py", "content": "x"}}
    )
    _set_fake_manager(app, fake_manager)
    workspace_id = str(uuid.uuid4())

    response = await client.get(
        f"/v1/ide/workspaces/{workspace_id}/file?path=src/a.py",
        headers=_headers(),
    )

    assert response.status_code == 200
    _, action, params = fake_manager.calls[0]
    assert action == "ide_read_file"
    assert params == {"workspace_id": workspace_id, "path": "src/a.py"}


async def test_terminal_start_returns_direct_session_and_uses_long_approval_timeout(app, client):
    session = {
        "id": str(uuid.uuid4()),
        "kind": "terminal",
        "workspace_id": str(uuid.uuid4()),
        "workspace_name": "Proyecto",
        "title": "Terminal",
        "status": "running",
        "started_at": "2026-07-23T12:00:00+00:00",
        "ended_at": None,
        "exit_code": None,
        "command": ["/bin/zsh"],
    }
    seen_timeouts: list[float] = []

    class _TimeoutSpy(_FakeCompanionManager):
        async def send_command(self, tenant_id, action, params, timeout=30):
            seen_timeouts.append(timeout)
            return await super().send_command(tenant_id, action, params, timeout=timeout)

    fake_manager = _TimeoutSpy(response={"ok": True, "result": {"session": session}})
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/terminals",
        headers=_headers(),
        json={"workspace_id": session["workspace_id"], "argv": ["/bin/zsh"]},
    )

    assert response.status_code == 200
    assert response.json() == session
    assert seen_timeouts == [ide.IDE_APPROVAL_TIMEOUT_SECONDS]
    _, action, params = fake_manager.calls[0]
    assert action == "ide_terminal_start"
    assert params == {
        "workspace_id": session["workspace_id"],
        "argv": ["/bin/zsh"],
    }


async def test_terminal_list_filter_and_cursor_are_forwarded(app, client):
    fake_manager = _FakeCompanionManager(response={"ok": True, "result": {"sessions": []}})
    _set_fake_manager(app, fake_manager)
    workspace_id = str(uuid.uuid4())

    response = await client.get(
        f"/v1/ide/terminals?workspace_id={workspace_id}", headers=_headers()
    )
    assert response.status_code == 200
    assert fake_manager.calls[-1][1:] == (
        "ide_terminal_list",
        {"workspace_id": workspace_id},
    )

    fake_manager.response = {
        "ok": True,
        "result": {"session": {}, "events": [], "next_cursor": 7},
    }
    response = await client.get("/v1/ide/terminals/session-1?cursor=7", headers=_headers())
    assert response.status_code == 200
    assert fake_manager.calls[-1][1:] == (
        "ide_terminal_read",
        {"session_id": "session-1", "cursor": 7},
    )


async def test_agent_provider_is_validated_and_start_is_forwarded(
    app, client, monkeypatch: pytest.MonkeyPatch
):
    async def no_skills(*_args, **_kwargs):
        return []

    async def no_mcp_tools(*_args, **_kwargs):
        return []

    monkeypatch.setattr(ide, "list_skills", no_skills)
    monkeypatch.setattr(ide, "get_mcp_tools_for_tenant", no_mcp_tools)
    fake_manager = _FakeCompanionManager(
        response={
            "ok": True,
            "result": {
                "session": {
                    "id": "agent-1",
                    "kind": "agent",
                    "workspace_id": "workspace-1",
                    "workspace_name": "Proyecto",
                    "title": "Agente",
                    "status": "running",
                    "started_at": "2026-07-23T12:00:00+00:00",
                    "ended_at": None,
                    "exit_code": None,
                    "provider": "workers_ai",
                }
            },
        }
    )
    _set_fake_manager(app, fake_manager)

    invalid = await client.post(
        "/v1/ide/agents",
        headers=_headers(),
        json={"workspace_id": "workspace-1", "prompt": "Hazlo", "provider": "otro"},
    )
    assert invalid.status_code == 422

    response = await client.post(
        "/v1/ide/agents",
        headers=_headers(),
        json={
            "workspace_id": "workspace-1",
                "prompt": "Hazlo",
                "provider": "auto",
                "attachments": [],
                "title": "Hazlo",
            },
        )
    assert response.status_code == 200
    assert response.json()["id"] == "agent-1"
    assert fake_manager.calls[-1][1:] == (
        "ide_agent_start",
        {
            "workspace_id": "workspace-1",
            "prompt": "Hazlo",
            "provider": "auto",
            "title": "Hazlo",
            "attachments": [],
        },
    )


async def test_agent_message_while_busy_is_accepted_and_reports_its_queue_position(
    app, client, monkeypatch: pytest.MonkeyPatch
):
    """Con el agente ocupado esto ya no es un 422.

    El companion acepta el mensaje, lo encola contra el turno vivo y devuelve
    `queued`; el router tiene que dejar pasar esa señal, porque es lo único
    que le permite a la interfaz decir "en cola (1 de 5)" en vez de dejar a la
    persona sin respuesta -- que es lo que la hacía reenviar el mismo mensaje.
    """

    async def no_skills(*_args, **_kwargs):
        return []

    async def no_mcp_tools(*_args, **_kwargs):
        return []

    monkeypatch.setattr(ide, "list_skills", no_skills)
    monkeypatch.setattr(ide, "get_mcp_tools_for_tenant", no_mcp_tools)
    fake_manager = _FakeCompanionManager(
        response={
            "ok": True,
            "result": {
                "session": {"id": "agent-1", "status": "running"},
                "queued": {
                    "id": "msg-1",
                    "position": 2,
                    "pending": 2,
                    "max_pending": 5,
                    "queued_at": "2026-07-28T12:00:00+00:00",
                },
            },
        }
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/agents",
        headers=_headers(),
        json={
            "workspace_id": "workspace-1",
            "prompt": "y de paso arregla el test",
            "conversation_id": "conv-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "agent-1"
    assert body["queued"] == {
        "id": "msg-1",
        "position": 2,
        "pending": 2,
        "max_pending": 5,
        "queued_at": "2026-07-28T12:00:00+00:00",
    }


async def test_agent_message_rejected_by_the_companion_is_still_a_422(
    app, client, monkeypatch: pytest.MonkeyPatch
):
    """Encolar no es incondicional: la cola llena (y un plan esperando
    aprobación) siguen siendo un "no" del companion, y el router los sigue
    traduciendo a 422 con el motivo intacto."""

    async def no_skills(*_args, **_kwargs):
        return []

    async def no_mcp_tools(*_args, **_kwargs):
        return []

    monkeypatch.setattr(ide, "list_skills", no_skills)
    monkeypatch.setattr(ide, "get_mcp_tools_for_tenant", no_mcp_tools)
    fake_manager = _FakeCompanionManager(
        response={
            "ok": False,
            "error": "Ya hay 5 mensajes esperando a que el agente los lea.",
        }
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/agents",
        headers=_headers(),
        json={
            "workspace_id": "workspace-1",
            "prompt": "otra idea más",
            "conversation_id": "conv-1",
        },
    )

    assert response.status_code == 422
    assert "5 mensajes esperando" in response.json()["detail"]


async def test_agent_mcp_requires_confirmation_and_executes_exact_tool(
    app, client, monkeypatch: pytest.MonkeyPatch
):
    class Result:
        content = {"rows": [{"ok": True}]}

    class Tool:
        name = "mcp_crm_buscar_cliente"
        description = "Busca un cliente"
        input_schema = {
            "type": "object",
            "properties": {"email": {"type": "string"}},
        }

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run(self, _ctx, args):
            self.calls.append(args)
            return Result()

    tool = Tool()

    async def mcp_tools(*_args, **_kwargs):
        return [tool]

    monkeypatch.setattr(ide, "get_mcp_tools_for_tenant", mcp_tools)

    class MCPManager(_FakeCompanionManager):
        async def send_command(self, tenant_id, action, params, timeout=30):
            self.calls.append((tenant_id, action, dict(params)))
            if action == "ide_agent_mcp_pending":
                return {
                    "ok": True,
                    "result": {
                        "session_id": "agent-1",
                        "call_id": "call-1",
                        "name": tool.name,
                        "arguments": {"email": "persona@example.com"},
                    },
                }
            return {"ok": True, "result": {"accepted": True}}

    manager = MCPManager()
    _set_fake_manager(app, manager)

    response = await client.post(
        "/v1/ide/agents/agent-1/mcp/call-1/confirm",
        headers=_headers(),
        json={"approved": True},
    )

    assert response.status_code == 200
    assert tool.calls == [{"email": "persona@example.com"}]
    assert [call[1] for call in manager.calls] == [
        "ide_agent_mcp_pending",
        "ide_agent_mcp_resolve",
    ]
    assert manager.calls[-1][2]["result"] == {
        "ok": True,
        "content": {"rows": [{"ok": True}]},
    }


async def test_agent_mcp_denial_never_executes_remote_tool(
    app, client, monkeypatch: pytest.MonkeyPatch
):
    class Tool:
        name = "mcp_crm_eliminar_cliente"
        description = "Elimina un cliente"
        input_schema = {"type": "object", "properties": {}}
        calls = 0

        async def run(self, _ctx, _args):
            self.calls += 1
            raise AssertionError("Una tool denegada nunca debe ejecutarse.")

    tool = Tool()

    async def mcp_tools(*_args, **_kwargs):
        return [tool]

    monkeypatch.setattr(ide, "get_mcp_tools_for_tenant", mcp_tools)

    class MCPManager(_FakeCompanionManager):
        async def send_command(self, tenant_id, action, params, timeout=30):
            self.calls.append((tenant_id, action, dict(params)))
            if action == "ide_agent_mcp_pending":
                return {
                    "ok": True,
                    "result": {
                        "session_id": "agent-1",
                        "call_id": "call-2",
                        "name": tool.name,
                        "arguments": {"id": "123"},
                    },
                }
            return {"ok": True, "result": {"accepted": True}}

    manager = MCPManager()
    _set_fake_manager(app, manager)

    response = await client.post(
        "/v1/ide/agents/agent-1/mcp/call-2/confirm",
        headers=_headers(),
        json={"approved": False},
    )

    assert response.status_code == 200
    assert tool.calls == 0
    assert manager.calls[-1][2]["result"]["ok"] is False


# ---------------------------------------------------------------------------
# Proyectos y conversaciones (Cimiento B: agrupación estilo Antigravity)
# ---------------------------------------------------------------------------


async def test_project_create_forwards_name_and_workspace_and_returns_direct_project(app, client):
    project = {
        "id": str(uuid.uuid4()),
        "name": "Forge Studio",
        "workspace_id": str(uuid.uuid4()),
        "workspace_name": "Repo",
        "workspace_path": "/tmp/repo",
        "workspace_available": True,
        "conversation_count": 0,
        "created_at": "2026-07-28T12:00:00+00:00",
        "updated_at": "2026-07-28T12:00:00+00:00",
    }
    fake_manager = _FakeCompanionManager(response={"ok": True, "result": {"project": project}})
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/projects",
        headers=_headers(),
        json={"name": "Forge Studio", "workspace_id": project["workspace_id"]},
    )

    assert response.status_code == 200
    assert response.json() == project
    _, action, params = fake_manager.calls[0]
    assert action == "ide_project_create"
    assert params == {"name": "Forge Studio", "workspace_id": project["workspace_id"]}


async def test_project_list_returns_direct_result(app, client):
    fake_manager = _FakeCompanionManager(response={"ok": True, "result": {"projects": []}})
    _set_fake_manager(app, fake_manager)

    response = await client.get("/v1/ide/projects", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"projects": []}
    assert fake_manager.calls[0][1:] == ("ide_project_list", {})


async def test_project_rename_forwards_project_id_and_name(app, client):
    project_id = str(uuid.uuid4())
    fake_manager = _FakeCompanionManager(
        response={"ok": True, "result": {"project": {"id": project_id, "name": "Nuevo nombre"}}}
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        f"/v1/ide/projects/{project_id}/rename",
        headers=_headers(),
        json={"name": "Nuevo nombre"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Nuevo nombre"
    _, action, params = fake_manager.calls[0]
    assert action == "ide_project_rename"
    assert params == {"project_id": project_id, "name": "Nuevo nombre"}


async def test_project_delete_defaults_to_keeping_conversations(app, client):
    project_id = str(uuid.uuid4())
    fake_manager = _FakeCompanionManager(
        response={
            "ok": True,
            "result": {
                "deleted_project_id": project_id,
                "conversations_deleted": False,
                "affected_conversation_ids": [],
            },
        }
    )
    _set_fake_manager(app, fake_manager)

    response = await client.delete(f"/v1/ide/projects/{project_id}", headers=_headers())

    assert response.status_code == 200
    assert response.json()["conversations_deleted"] is False
    _, action, params = fake_manager.calls[0]
    assert action == "ide_project_delete"
    assert params == {"project_id": project_id, "conversations": "keep"}


async def test_project_delete_forwards_explicit_delete_conversations_choice(app, client):
    project_id = str(uuid.uuid4())
    fake_manager = _FakeCompanionManager(
        response={
            "ok": True,
            "result": {
                "deleted_project_id": project_id,
                "conversations_deleted": True,
                "affected_conversation_ids": ["c1"],
            },
        }
    )
    _set_fake_manager(app, fake_manager)

    response = await client.delete(
        f"/v1/ide/projects/{project_id}?conversations=delete", headers=_headers()
    )

    assert response.status_code == 200
    _, action, params = fake_manager.calls[0]
    assert action == "ide_project_delete"
    assert params == {"project_id": project_id, "conversations": "delete"}


async def test_project_delete_rejects_an_unknown_conversations_value(app, client):
    _set_fake_manager(app, _FakeCompanionManager())

    response = await client.delete(
        f"/v1/ide/projects/{uuid.uuid4()}?conversations=algo-raro", headers=_headers()
    )

    assert response.status_code == 422  # validación de FastAPI, ni llega al companion


async def test_conversation_create_omits_project_id_and_title_when_absent(app, client):
    fake_manager = _FakeCompanionManager(
        response={
            "ok": True,
            "result": {
                "conversation": {"id": "c1", "project_id": None, "title": "Nueva conversación"}
            },
        }
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post("/v1/ide/conversations", headers=_headers(), json={})

    assert response.status_code == 200
    assert response.json()["id"] == "c1"
    _, action, params = fake_manager.calls[0]
    assert action == "ide_conversation_create"
    assert params == {}


async def test_conversation_create_forwards_project_id_and_title_when_given(app, client):
    project_id = str(uuid.uuid4())
    fake_manager = _FakeCompanionManager(
        response={
            "ok": True,
            "result": {
                "conversation": {"id": "c1", "project_id": project_id, "title": "Arreglar bug"}
            },
        }
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/conversations",
        headers=_headers(),
        json={"project_id": project_id, "title": "Arreglar bug"},
    )

    assert response.status_code == 200
    _, action, params = fake_manager.calls[0]
    assert action == "ide_conversation_create"
    assert params == {"project_id": project_id, "title": "Arreglar bug"}


async def test_conversation_list_forwards_project_filter_and_unassigned_flag(app, client):
    fake_manager = _FakeCompanionManager(response={"ok": True, "result": {"conversations": []}})
    _set_fake_manager(app, fake_manager)
    project_id = str(uuid.uuid4())

    await client.get(f"/v1/ide/conversations?project_id={project_id}", headers=_headers())
    assert fake_manager.calls[-1][1:] == (
        "ide_conversation_list",
        {"only_unassigned": False, "project_id": project_id},
    )

    await client.get("/v1/ide/conversations?only_unassigned=true", headers=_headers())
    assert fake_manager.calls[-1][1:] == (
        "ide_conversation_list",
        {"only_unassigned": True},
    )

    await client.get("/v1/ide/conversations", headers=_headers())
    assert fake_manager.calls[-1][1:] == ("ide_conversation_list", {"only_unassigned": False})


async def test_conversation_rename_forwards_conversation_id_and_title(app, client):
    fake_manager = _FakeCompanionManager(
        response={"ok": True, "result": {"conversation": {"id": "c1", "title": "Otro título"}}}
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/conversations/c1/rename", headers=_headers(), json={"title": "Otro título"}
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Otro título"
    _, action, params = fake_manager.calls[0]
    assert action == "ide_conversation_rename"
    assert params == {"conversation_id": "c1", "title": "Otro título"}


async def test_conversation_move_can_detach_with_an_explicit_null_project_id(app, client):
    fake_manager = _FakeCompanionManager(
        response={"ok": True, "result": {"conversation": {"id": "c1", "project_id": None}}}
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/conversations/c1/move", headers=_headers(), json={"project_id": None}
    )

    assert response.status_code == 200
    assert response.json()["project_id"] is None
    _, action, params = fake_manager.calls[0]
    assert action == "ide_conversation_move"
    assert params == {"conversation_id": "c1", "project_id": None}


async def test_conversation_delete_uses_extended_timeout_and_returns_closed_sessions(app, client):
    seen_timeouts: list[float] = []

    class _TimeoutSpy(_FakeCompanionManager):
        async def send_command(self, tenant_id, action, params, timeout=30):
            seen_timeouts.append(timeout)
            return await super().send_command(tenant_id, action, params, timeout=timeout)

    fake_manager = _TimeoutSpy(
        response={
            "ok": True,
            "result": {"deleted_conversation_id": "c1", "closed_session_ids": ["s1"]},
        }
    )
    _set_fake_manager(app, fake_manager)

    response = await client.delete("/v1/ide/conversations/c1", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"deleted_conversation_id": "c1", "closed_session_ids": ["s1"]}
    _, action, params = fake_manager.calls[0]
    assert action == "ide_conversation_delete"
    assert params == {"conversation_id": "c1"}
    assert seen_timeouts == [ide.IDE_GIT_MUTATION_TIMEOUT_SECONDS]


async def test_git_diff_is_typed_and_push_gets_extended_timeout(app, client):
    fake_manager = _FakeCompanionManager(
        response={"ok": True, "result": {"text": "", "truncated": False}}
    )
    seen_timeouts: list[float] = []

    class _TimeoutSpy(_FakeCompanionManager):
        async def send_command(self, tenant_id, action, params, timeout=30):
            seen_timeouts.append(timeout)
            return await super().send_command(tenant_id, action, params, timeout=timeout)

    spy = _TimeoutSpy(response=fake_manager.response)
    _set_fake_manager(app, spy)

    response = await client.get(
        "/v1/ide/workspaces/workspace-1/git/diff?staged=true&path=src/a.py",
        headers=_headers(),
    )
    assert response.status_code == 200
    assert spy.calls[-1][1:] == (
        "ide_git_diff",
        {"workspace_id": "workspace-1", "staged": True, "paths": ["src/a.py"]},
    )

    spy.response = {"ok": True, "result": {"ok": True}}
    response = await client.post(
        "/v1/ide/workspaces/workspace-1/git/push",
        headers=_headers(),
        json={"remote": None, "branch": "main", "set_upstream": True},
    )
    assert response.status_code == 200
    assert spy.calls[-1][1:] == (
        "ide_git_push",
        {
            "workspace_id": "workspace-1",
            "branch": "main",
            "set_upstream": True,
            "remote": "origin",
        },
    )
    assert seen_timeouts[-1] == ide.IDE_GIT_PUSH_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Menú de comandos "/" (``GET /commands`` + ``POST /commands/execute``)
# ---------------------------------------------------------------------------


async def test_get_commands_forwards_to_ide_command_list(app, client):
    fake_manager = _FakeCompanionManager(
        response={"ok": True, "result": {"comandos": [], "ayuda": "/help -- ..."}}
    )
    _set_fake_manager(app, fake_manager)

    response = await client.get("/v1/ide/commands", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {"comandos": [], "ayuda": "/help -- ..."}
    _, action, params = fake_manager.calls[0]
    assert action == "ide_command_list"
    assert params == {}


async def test_post_command_execute_sends_only_the_fields_given(app, client):
    fake_manager = _FakeCompanionManager(
        response={"ok": True, "result": {"ok": True, "comando": "help", "mensaje": "..."}}
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/commands/execute", headers=_headers(), json={"text": "/help"}
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "comando": "help", "mensaje": "..."}
    _, action, params = fake_manager.calls[0]
    assert action == "ide_command_execute"
    assert params == {"text": "/help", "confirmed": False}


async def test_post_command_execute_forwards_all_optional_context_and_uses_approval_timeout(
    app, client
):
    fake_manager = _FakeCompanionManager(
        response={"ok": True, "result": {"ok": False, "requiere_confirmacion": True}}
    )
    seen_timeouts: list[float] = []

    class _TimeoutSpy(_FakeCompanionManager):
        async def send_command(self, tenant_id, action, params, timeout=30):
            seen_timeouts.append(timeout)
            return await super().send_command(tenant_id, action, params, timeout=timeout)

    spy = _TimeoutSpy(response=fake_manager.response)
    _set_fake_manager(app, spy)

    response = await client.post(
        "/v1/ide/commands/execute",
        headers=_headers(),
        json={
            "text": "/rewind ultimo",
            "workspace_id": "workspace-1",
            "conversation_id": "conv-1",
            "project_id": "project-1",
            "session_id": "session-1",
            "confirmed": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "requiere_confirmacion": True}
    assert spy.calls[-1][1:] == (
        "ide_command_execute",
        {
            "text": "/rewind ultimo",
            "confirmed": True,
            "workspace_id": "workspace-1",
            "conversation_id": "conv-1",
            "project_id": "project-1",
            "session_id": "session-1",
        },
    )
    assert seen_timeouts[-1] == ide.IDE_APPROVAL_TIMEOUT_SECONDS


async def test_get_commands_returns_503_when_no_companion_is_connected(app, client):
    _set_fake_manager(app, _FakeCompanionManager(connected=False))

    response = await client.get("/v1/ide/commands", headers=_headers())

    assert response.status_code == 503


# ---------------------------------------------------------------------------
# `/usage`, `/memory`, `/mcp`, `/voice`: resueltos server-side con datos
# reales del tenant, SIN llegar a tocar el companion (a diferencia de todo lo
# de arriba) -- ver el comentario en `routers/ide.py` sobre por qué estos
# cuatro son intrínsecamente server-side. Cada test deja el companion
# desconectado a propósito para probar justo eso.
# ---------------------------------------------------------------------------


async def test_command_usage_resolves_with_real_tenant_usage_and_never_touches_companion(
    app, client, fake_repo
):
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    await fake_repo.add_usage_event(tenant_id=tenant_id, kind="messages", quantity=3.0)
    fake_manager = _FakeCompanionManager(connected=False)
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/commands/execute", headers=headers, json={"text": "/usage"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["comando"] == "usage"
    assert body["data"]["plan_key"] == "hosted_basic"
    assert body["data"]["usage"]["messages"] == 3.0
    assert "sin límite" in body["mensaje"]
    assert fake_manager.calls == []


async def test_command_memory_lists_and_can_search_real_saved_items(app, client):
    headers = _headers()
    _set_fake_manager(app, _FakeCompanionManager(connected=False))

    creado = await client.post(
        "/v1/memory",
        headers=headers,
        json={"kind": "preference", "content": "Prefiere respuestas cortas", "importance": 0.7},
    )
    assert creado.status_code == 201

    sin_argumentos = await client.post(
        "/v1/ide/commands/execute", headers=headers, json={"text": "/memory"}
    )
    assert sin_argumentos.status_code == 200
    body = sin_argumentos.json()
    assert body["ok"] is True
    assert body["comando"] == "memory"
    assert [item["content"] for item in body["data"]["items"]] == ["Prefiere respuestas cortas"]

    con_busqueda = await client.post(
        "/v1/ide/commands/execute", headers=headers, json={"text": "/memory cortas"}
    )
    assert len(con_busqueda.json()["data"]["items"]) == 1

    sin_coincidencia = await client.post(
        "/v1/ide/commands/execute", headers=headers, json={"text": "/memory inexistente"}
    )
    assert sin_coincidencia.json()["data"]["items"] == []


async def test_command_memory_empty_reports_no_recuerdos_without_inventing_data(app, client):
    _set_fake_manager(app, _FakeCompanionManager(connected=False))

    response = await client.post(
        "/v1/ide/commands/execute", headers=_headers(), json={"text": "/memory"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"] == []
    assert "no hay memoria" in body["mensaje"].lower()


async def test_command_mcp_lists_the_tenants_real_tools(
    app, client, monkeypatch: pytest.MonkeyPatch
):
    class _Tool:
        name = "mcp_crm_buscar_cliente"
        description = "Busca un cliente por correo"

    async def fake_mcp_tools(*_args, **_kwargs):
        return [_Tool()]

    monkeypatch.setattr(ide, "get_mcp_tools_for_tenant", fake_mcp_tools)
    fake_manager = _FakeCompanionManager(connected=False)
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/commands/execute", headers=_headers(), json={"text": "/mcp"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["herramientas"] == [
        {"name": "mcp_crm_buscar_cliente", "description": "Busca un cliente por correo"}
    ]
    assert fake_manager.calls == []


async def test_command_mcp_reports_none_connected_without_erroring(
    app, client, monkeypatch: pytest.MonkeyPatch
):
    async def no_tools(*_args, **_kwargs):
        return []

    monkeypatch.setattr(ide, "get_mcp_tools_for_tenant", no_tools)
    _set_fake_manager(app, _FakeCompanionManager(connected=False))

    response = await client.post(
        "/v1/ide/commands/execute", headers=_headers(), json={"text": "/mcp"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["herramientas"] == []


async def test_command_voice_reports_the_real_plan_flags(app, client):
    fake_manager = _FakeCompanionManager(connected=False)
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/commands/execute", headers=_headers(), json={"text": "/voice"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["comando"] == "voice"
    # Modelo de precio de pago único: los 4 planes conceden todos los flags.
    assert body["data"] == {"voz_web": True, "voz_telefonica": True, "clonacion_voz": True}
    assert fake_manager.calls == []


async def test_command_other_than_the_server_side_four_still_forwards_to_companion(app, client):
    """Ningún comando fuera de los cuatro interceptados cambia de camino."""
    fake_manager = _FakeCompanionManager(
        response={"ok": True, "result": {"ok": True, "comando": "help", "mensaje": "..."}}
    )
    _set_fake_manager(app, fake_manager)

    response = await client.post(
        "/v1/ide/commands/execute", headers=_headers(), json={"text": "/help"}
    )

    assert response.status_code == 200
    assert len(fake_manager.calls) == 1
    assert fake_manager.calls[0][1] == "ide_command_execute"


# ---------------------------------------------------------------------------
# Bloques ricos del IDE (`presentation`)
# ---------------------------------------------------------------------------


def _evento_con_tabla(cursor: int = 1) -> dict[str, Any]:
    return {
        "cursor": cursor,
        "type": "blocks",
        "text": "Proveedor | Costo\n--- | ---\nWorkers AI | 0.11",
        "timestamp": "2026-07-29T12:00:00+00:00",
        "presentation": [
            {
                "schema_version": 1,
                "type": "table",
                "title": "Costo por proveedor",
                "columns": [
                    {"key": "proveedor", "title": "Proveedor", "align": "left"},
                    {"key": "costo", "title": "USD", "align": "right"},
                ],
                "rows": [{"proveedor": "Workers AI", "costo": "0.11"}, {"proveedor": "Otro"}],
                "note": None,
                "fallback_text": "Proveedor | Costo\nWorkers AI | 0.11",
            }
        ],
    }


async def test_agent_read_lets_a_valid_rich_block_through_intact(app, client):
    """El bloque llega al teléfono entero, incluida la celda ausente de la
    segunda fila (sin dato = clave ausente, no un cero inventado)."""
    evento = _evento_con_tabla()
    fake_manager = _FakeCompanionManager(
        response={"ok": True, "result": {"session": {"id": "agent-1"}, "events": [evento]}}
    )
    _set_fake_manager(app, fake_manager)

    response = await client.get("/v1/ide/agents/agent-1?cursor=0", headers=_headers())

    assert response.status_code == 200
    (recibido,) = response.json()["events"]
    assert recibido["presentation"] == evento["presentation"]
    assert recibido["presentation"][0]["rows"][1] == {"proveedor": "Otro"}


async def test_agent_read_drops_a_presentation_the_contract_does_not_recognize(app, client):
    """El companion es otro programa, en la máquina de la persona, y este
    router es un pasamanos: la API es el último punto propio antes del
    teléfono. Lo que no valida se cae; el texto del evento nunca."""
    evento = {
        "cursor": 2,
        "type": "blocks",
        "text": "Resumen en texto que sí sobrevive.",
        "presentation": [
            {"type": "html", "html": "<script>alert(1)</script>"},
            # Gráfica degenerada: una sola serie de dos puntos.
            {
                "schema_version": 1,
                "type": "chart",
                "chart_kind": "bar",
                "title": "Antes y después",
                "fallback_text": "antes 900, después 300",
                "series": [
                    {
                        "name": "latencia",
                        "points": [
                            {"label": "antes", "value": 900},
                            {"label": "después", "value": 300},
                        ],
                    }
                ],
            },
        ],
    }
    fake_manager = _FakeCompanionManager(
        response={"ok": True, "result": {"session": {"id": "agent-1"}, "events": [evento]}}
    )
    _set_fake_manager(app, fake_manager)

    response = await client.get("/v1/ide/agents/agent-1", headers=_headers())

    assert response.status_code == 200
    (recibido,) = response.json()["events"]
    assert "presentation" not in recibido
    assert recibido["text"] == "Resumen en texto que sí sobrevive."


async def test_agent_read_leaves_plain_text_events_untouched(app, client):
    eventos = [
        {"cursor": 1, "type": "user", "text": "Compara los proveedores."},
        {"cursor": 2, "type": "assistant_final", "text": "Workers AI es el más barato."},
    ]
    fake_manager = _FakeCompanionManager(
        response={"ok": True, "result": {"session": {"id": "agent-1"}, "events": eventos}}
    )
    _set_fake_manager(app, fake_manager)

    response = await client.get("/v1/ide/agents/agent-1", headers=_headers())

    assert response.status_code == 200
    assert response.json()["events"] == eventos
