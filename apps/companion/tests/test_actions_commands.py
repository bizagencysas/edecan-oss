"""Tests de `run_command` y del portapapeles (siempre con allowlist explícita; sin red)."""

from __future__ import annotations

import sys

import pytest
from edecan_companion import actions


def test_run_command_rejects_executable_not_in_allowlist(companion_config):
    assert companion_config.allowed_commands == []  # nada permitido por defecto

    with pytest.raises(actions.ActionError, match="no permitido"):
        actions._run_command({"command": "ls -la"}, companion_config)


def test_run_command_runs_allowed_executable(companion_config):
    companion_config.allowed_commands.append(sys.executable)

    result = actions._run_command(
        {"command": f"{sys.executable} -c \"print('hola')\""}, companion_config
    )

    assert result["returncode"] == 0
    assert "hola" in result["stdout"]
    assert result["truncated"] is False


def test_run_command_never_interprets_shell_metacharacters(companion_config, tmp_path):
    """Un ';' en el comando no debe encadenar un segundo proceso: siempre shell=False."""
    marker = tmp_path / "should_not_exist.txt"
    companion_config.allowed_commands.append("echo")

    result = actions._run_command({"command": f"echo hola; touch {marker}"}, companion_config)

    assert not marker.exists()
    assert "hola;" in result["stdout"]  # el ";" llegó como texto literal, no como separador


def test_run_command_truncates_long_output(companion_config, monkeypatch):
    monkeypatch.setattr(actions, "MAX_COMMAND_OUTPUT_BYTES", 10)
    companion_config.allowed_commands.append(sys.executable)

    result = actions._run_command(
        {"command": f"{sys.executable} -c \"print('x' * 1000)\""}, companion_config
    )

    assert result["truncated"] is True
    assert len(result["stdout"].encode("utf-8")) <= 10


def test_run_command_empty_command_raises(companion_config):
    with pytest.raises(actions.ActionError):
        actions._run_command({"command": "   "}, companion_config)


def test_run_command_missing_param_raises(companion_config):
    with pytest.raises(actions.ActionError, match="command"):
        actions._run_command({}, companion_config)


def test_run_command_runs_with_cwd_pinned_to_sandbox(companion_config):
    companion_config.allowed_commands.append(sys.executable)
    (companion_config.sandbox_dir / "marker.txt").write_text("x")

    result = actions._run_command(
        {"command": f'{sys.executable} -c "import os; print(os.getcwd())"'}, companion_config
    )

    assert result["stdout"].strip() == str(companion_config.sandbox_dir)


def test_clipboard_actions_reject_unsupported_platform(companion_config, monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "win32")

    with pytest.raises(actions.ActionError, match="no soportado"):
        actions._clipboard_get({}, companion_config)

    with pytest.raises(actions.ActionError, match="no soportado"):
        actions._clipboard_set({"text": "hola"}, companion_config)


def test_clipboard_set_requires_text_param(companion_config):
    with pytest.raises(actions.ActionError, match="text"):
        actions._clipboard_set({}, companion_config)


def test_open_app_rejects_app_not_in_allowlist(companion_config):
    with pytest.raises(actions.ActionError, match="no permitida"):
        actions._open_app({"app": "Safari"}, companion_config)


def test_open_app_requires_app_param(companion_config):
    with pytest.raises(actions.ActionError, match="app"):
        actions._open_app({}, companion_config)


def test_open_app_rejects_unsupported_platform(companion_config, monkeypatch):
    companion_config.allowed_apps.append("Safari")
    monkeypatch.setattr(actions.sys, "platform", "win32")

    with pytest.raises(actions.ActionError, match="no está soportado"):
        actions._open_app({"app": "Safari"}, companion_config)
