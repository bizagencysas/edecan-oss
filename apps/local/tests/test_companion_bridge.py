from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from edecan_api.companion_manager import ConnectionManager
from edecan_companion.ide_runtime import IDE_ACTIONS
from edecan_local import companion_bridge as companion_bridge_module
from edecan_local.companion_bridge import LocalCompanionBridge


class _Manager:
    def __init__(self) -> None:
        self.handlers = {}
        self.default_handler = None

    def is_connected(self, tenant_id: uuid.UUID) -> bool:
        return tenant_id in self.handlers

    def register_local(self, tenant_id: uuid.UUID, handler) -> None:
        self.handlers[tenant_id] = handler

    def register_local_default(self, handler) -> None:
        self.default_handler = handler


async def test_registers_installed_computer_for_local_owner(tmp_path: Path) -> None:
    manager = _Manager()
    app = SimpleNamespace(state=SimpleNamespace(companion_manager=manager))
    bridge = LocalCompanionBridge(app=app, data_dir=tmp_path)
    tenant_id = uuid.uuid4()

    await bridge.ensure_registered(tenant_id)

    assert manager.is_connected(tenant_id)
    assert manager.default_handler is not None


async def test_bridge_rejects_actions_outside_ide_and_remote_surfaces(tmp_path: Path) -> None:
    manager = _Manager()
    app = SimpleNamespace(state=SimpleNamespace(companion_manager=manager))
    bridge = LocalCompanionBridge(app=app, data_dir=tmp_path)

    result = await bridge.execute("open_app", {"app": "Safari"})

    assert result["ok"] is False
    assert "no disponible" in result["error"]


async def test_every_new_ide_action_is_dispatched_by_the_ide_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ConnectionManager()
    app = SimpleNamespace(state=SimpleNamespace(companion_manager=manager))
    LocalCompanionBridge(app=app, data_dir=tmp_path)
    tenant_id = uuid.uuid4()
    received: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_ide_action(action, params, config, approver):
        assert await approver(action, params, config) is True
        received.append((action, dict(params)))
        return {"ok": True, "result": {"action": action}}

    async def fail_if_legacy_dispatch_is_used(action, params, config, approver):
        raise AssertionError(f"{action} no debe caer en actions.execute")

    monkeypatch.setattr(companion_bridge_module, "execute_ide_action", fake_execute_ide_action)
    monkeypatch.setattr(companion_bridge_module.actions, "execute", fail_if_legacy_dispatch_is_used)

    for action in sorted(IDE_ACTIONS):
        result = await manager.send_command(tenant_id, action, {"marker": action})
        assert result == {"ok": True, "result": {"action": action}}

    assert received == [(action, {"marker": action}) for action in sorted(IDE_ACTIONS)]


async def test_real_local_protocol_authorizes_workspace_and_roundtrips_a_file(
    tmp_path: Path,
) -> None:
    """Ejercita manager -> bridge -> ide_runtime real, sin handlers stub."""
    data_dir = tmp_path / "data"
    workspace = tmp_path / "Proyecto IDE"
    workspace.mkdir()
    manager = ConnectionManager()
    app = SimpleNamespace(state=SimpleNamespace(companion_manager=manager))
    LocalCompanionBridge(app=app, data_dir=data_dir)
    tenant_id = uuid.uuid4()

    authorized = await manager.send_command(
        tenant_id,
        "ide_workspace_authorize",
        {"path": str(workspace), "name": "Proyecto real"},
    )
    assert authorized["ok"] is True
    workspace_out = authorized["result"]["workspace"]
    workspace_id = workspace_out["id"]
    assert workspace_out["name"] == "Proyecto real"
    assert workspace_out["path"] == str(workspace.resolve())

    written = await manager.send_command(
        tenant_id,
        "ide_write_file",
        {
            "workspace_id": workspace_id,
            "path": "src/main.py",
            "content": "print('hola desde IDE')\n",
        },
    )
    assert written == {
        "ok": True,
        "result": {"path": "src/main.py", "bytes_written": 24},
    }

    opened = await manager.send_command(
        tenant_id,
        "ide_read_file",
        {"workspace_id": workspace_id, "path": "src/main.py"},
    )
    assert opened["ok"] is True
    assert opened["result"] == {
        "path": "src/main.py",
        "content": "print('hola desde IDE')\n",
        "encoding": "utf-8",
        "size_bytes": 24,
    }

    listed = await manager.send_command(tenant_id, "ide_workspace_list", {})
    assert listed["result"]["workspaces"] == [workspace_out]


async def test_real_local_protocol_supports_tree_editor_files_search_and_terminal(
    tmp_path: Path,
) -> None:
    """Ejercita manager -> bridge -> companion real; no hay handlers stub."""
    data_dir = tmp_path / "data"
    sandbox = tmp_path / "Proyecto local"
    sandbox.mkdir()
    (sandbox / "README.md").write_text("estado: inicial\n", encoding="utf-8")
    pwd_executable = shutil.which("pwd")
    assert pwd_executable is not None
    data_dir.mkdir()
    (data_dir / "companion.yaml").write_text(
        yaml.safe_dump(
            {
                "sandbox_dir": str(sandbox),
                "allowed_commands": [pwd_executable],
                "ide_enabled": True,
            }
        ),
        encoding="utf-8",
    )

    manager = ConnectionManager()
    app = SimpleNamespace(state=SimpleNamespace(companion_manager=manager))
    LocalCompanionBridge(app=app, data_dir=data_dir)
    tenant_id = uuid.uuid4()

    tree = await manager.send_command(
        tenant_id, "list_tree", {"max_depth": 2, "max_entries": 20}
    )
    assert tree == {
        "ok": True,
        "result": {
            "path": ".",
            "entries": [{"name": "README.md", "is_dir": False, "size_bytes": 16}],
            "truncated": False,
        },
    }

    written = await manager.send_command(
        tenant_id,
        "write_file",
        {"path": "src/main.py", "content": "print('hola')\n"},
    )
    assert written["ok"] is True
    assert written["result"] == {"path": "src/main.py", "bytes_written": 14}

    opened = await manager.send_command(
        tenant_id, "read_file", {"path": "src/main.py"}
    )
    assert opened["ok"] is True
    assert opened["result"]["content"] == "print('hola')\n"
    assert opened["result"]["encoding"] == "utf-8"

    edited = await manager.send_command(
        tenant_id,
        "apply_edit",
        {
            "path": "src/main.py",
            "old_string": "hola",
            "new_string": "Edecán",
            "replace_all": False,
        },
    )
    assert edited["ok"] is True
    assert edited["result"]["replacements"] == 1
    assert (sandbox / "src/main.py").read_text(encoding="utf-8") == "print('Edecán')\n"

    searched = await manager.send_command(
        tenant_id, "search_files", {"query": "edecán", "path": "src"}
    )
    assert searched["ok"] is True
    assert searched["result"]["matches"] == [
        {"path": "src/main.py", "line": 1, "texto": "print('Edecán')"}
    ]

    terminal = await manager.send_command(
        tenant_id, "run_command", {"command": pwd_executable}
    )
    assert terminal["ok"] is True
    assert terminal["result"]["returncode"] == 0
    assert Path(terminal["result"]["stdout"].strip()) == sandbox.resolve()
    assert terminal["result"]["stderr"] == ""

    audit_rows = [
        json.loads(line)
        for line in (data_dir / "companion.log").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["action"] for row in audit_rows] == [
        "list_tree",
        "write_file",
        "read_file",
        "apply_edit",
        "search_files",
        "run_command",
    ]
    assert all(row["approved"] and row["ok"] for row in audit_rows)
    assert audit_rows[1]["params"]["content"] == "<14 caracteres omitidos>"


async def test_local_terminal_still_requires_explicit_command_allowlist(tmp_path: Path) -> None:
    manager = ConnectionManager()
    app = SimpleNamespace(state=SimpleNamespace(companion_manager=manager))
    LocalCompanionBridge(app=app, data_dir=tmp_path)

    result = await manager.send_command(
        uuid.uuid4(), "run_command", {"command": "pwd"}
    )

    assert result["ok"] is False
    assert "allowed_commands" in result["error"]


async def test_bridge_requires_server_injected_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _Manager()
    app = SimpleNamespace(state=SimpleNamespace(companion_manager=manager))
    bridge = LocalCompanionBridge(app=app, data_dir=tmp_path)
    approved: list[bool] = []

    async def fake_execute(action, params, config, approver):
        approved.append(await approver(action, params, config))
        return {"ok": approved[-1]}

    monkeypatch.setattr("edecan_local.companion_bridge.actions.execute", fake_execute)

    assert (await bridge.execute("screenshot", {}))["ok"] is False
    assert (await bridge.execute("screenshot", {"session_id": "session-1"}))["ok"] is True
    assert approved == [False, True]
