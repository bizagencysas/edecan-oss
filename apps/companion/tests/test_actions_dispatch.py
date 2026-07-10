"""Tests de `actions.execute`: dispatch, aprobación y auditoría (ARCHITECTURE.md §10.7)."""

from __future__ import annotations

import json

import pytest
from edecan_companion import actions


async def _approve_everything(action, params, config):
    return True


async def _reject_everything(action, params, config):
    return False


async def test_unsupported_action_is_rejected_without_asking_approval(companion_config):
    calls = []

    async def spy_approver(action, params, config):
        calls.append(action)
        return True

    result = await actions.execute("borrar_disco_duro", {}, companion_config, spy_approver)

    assert result == {"ok": False, "error": "acción no soportada: 'borrar_disco_duro'"}
    assert calls == []  # nunca se preguntó: la acción ni existe


async def test_execute_denies_when_approver_rejects(companion_config):
    result = await actions.execute("read_dir", {}, companion_config, _reject_everything)

    assert result["ok"] is False
    assert "rechaz" in result["error"]


async def test_execute_runs_handler_when_approved(companion_config):
    (companion_config.sandbox_dir / "x.txt").write_text("y")

    result = await actions.execute("read_dir", {}, companion_config, _approve_everything)

    assert result["ok"] is True
    names = {e["name"] for e in result["result"]["entries"]}
    assert "x.txt" in names


async def test_execute_reports_action_error_from_handler(companion_config):
    result = await actions.execute(
        "read_file", {"path": "no-existe.txt"}, companion_config, _approve_everything
    )

    assert result["ok"] is False
    assert "no existe" in result["error"]


async def test_execute_accepts_non_dict_params_gracefully(companion_config):
    result = await actions.execute("read_dir", None, companion_config, _approve_everything)
    assert result["ok"] is True


async def test_execute_writes_audit_log_entry_and_redacts_sensitive_content(companion_config):
    await actions.execute(
        "write_file",
        {"path": "a.txt", "content": "informacion secreta"},
        companion_config,
        _approve_everything,
    )

    lines = companion_config.audit_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["action"] == "write_file"
    assert entry["approved"] is True
    assert entry["ok"] is True
    assert "informacion secreta" not in json.dumps(entry)


async def test_execute_audits_rejected_action_too(companion_config):
    await actions.execute("read_dir", {}, companion_config, _reject_everything)

    lines = companion_config.audit_log_path.read_text(encoding="utf-8").strip().splitlines()
    entry = json.loads(lines[0])
    assert entry["approved"] is False
    assert entry["ok"] is False


@pytest.mark.parametrize("action_name", sorted(actions.ACTIONS.keys()))
def test_every_documented_action_has_a_handler(action_name):
    assert callable(actions.ACTIONS[action_name])
