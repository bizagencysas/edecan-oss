from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
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


async def test_bridge_rejects_non_remote_actions_even_when_local(tmp_path: Path) -> None:
    manager = _Manager()
    app = SimpleNamespace(state=SimpleNamespace(companion_manager=manager))
    bridge = LocalCompanionBridge(app=app, data_dir=tmp_path)

    result = await bridge.execute("run_command", {"command": "whoami"})

    assert result["ok"] is False
    assert "no disponible" in result["error"]


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
