"""Pruebas del runtime IDE residente, sin tocar ``actions.py``."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from edecan_companion.config import CompanionConfig
from edecan_companion.ide_runtime import IDERuntime, _safe_error, execute_ide_action
from edecan_companion.ide_sessions import MAX_EVENT_LOG_BYTES, SessionManager
from edecan_companion.ide_workspaces import IDEWorkspaceError, validate_workspace_root


async def _approve(
    _action: str, _params: dict[str, Any], _config: CompanionConfig
) -> bool:
    return True


async def _reject(
    _action: str, _params: dict[str, Any], _config: CompanionConfig
) -> bool:
    return False


def _project(tmp_path: Path, name: str = "project") -> Path:
    path = tmp_path / name
    path.mkdir()
    return path


async def _call(
    companion_config: CompanionConfig,
    action: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    result = await execute_ide_action(action, params, companion_config, _approve)
    assert result["ok"], result
    return result["result"]


async def _authorize(companion_config: CompanionConfig, path: Path) -> dict[str, Any]:
    result = await _call(
        companion_config,
        "ide_workspace_authorize",
        {"path": str(path), "name": "Proyecto"},
    )
    return result["workspace"]


async def _wait_for_session(
    companion_config: CompanionConfig,
    action: str,
    session_id: str,
    *,
    text: str,
    attempts: int = 80,
) -> dict[str, Any]:
    cursor = 0
    collected = ""
    latest: dict[str, Any] = {}
    for _ in range(attempts):
        latest = await _call(
            companion_config,
            action,
            {"session_id": session_id, "cursor": cursor},
        )
        for event in latest["events"]:
            collected += event["text"]
        cursor = latest["next_cursor"]
        if text in collected:
            latest["_collected"] = collected
            return latest
        await asyncio.sleep(0.05)
    pytest.fail(f"No apareció {text!r} en los eventos: {collected!r}")


async def test_authorize_requires_approval_and_rejection_does_not_persist(
    companion_config: CompanionConfig, tmp_path: Path
):
    path = _project(tmp_path)

    result = await execute_ide_action(
        "ide_workspace_authorize",
        {"path": str(path)},
        companion_config,
        _reject,
    )

    assert result["ok"] is False
    listed = await _call(companion_config, "ide_workspace_list", {})
    assert listed == {"workspaces": []}


async def test_workspace_file_operations_are_relative_and_persisted(
    companion_config: CompanionConfig, tmp_path: Path
):
    project = _project(tmp_path)
    workspace = await _authorize(companion_config, project)

    written = await _call(
        companion_config,
        "ide_write_file",
        {"workspace_id": workspace["id"], "path": "src/hello.txt", "content": "hola"},
    )
    assert written["bytes_written"] == 4

    read = await _call(
        companion_config,
        "ide_read_file",
        {"workspace_id": workspace["id"], "path": "src/hello.txt"},
    )
    assert read["content"] == "hola"

    edited = await _call(
        companion_config,
        "ide_apply_edit",
        {
            "workspace_id": workspace["id"],
            "path": "src/hello.txt",
            "old_string": "hola",
            "new_string": "mundo",
        },
    )
    assert edited["replacements"] == 1

    # Otra instancia reconstruye el registro persistido, como tras reiniciar
    # el proceso local.
    fresh = IDERuntime(companion_config)
    assert fresh.workspaces.get(workspace["id"])["path"] == str(project.resolve())


async def test_workspace_blocks_absolute_and_traversal_paths(
    companion_config: CompanionConfig, tmp_path: Path
):
    project = _project(tmp_path)
    workspace = await _authorize(companion_config, project)

    for unsafe in ("/etc/passwd", "../outside.txt"):
        result = await execute_ide_action(
            "ide_read_file",
            {"workspace_id": workspace["id"], "path": unsafe},
            companion_config,
            _approve,
        )
        assert result["ok"] is False
        assert "relativa" in result["error"].lower() or "salir" in result["error"].lower()


def test_workspace_rejects_filesystem_and_home_roots():
    with pytest.raises(IDEWorkspaceError):
        validate_workspace_root("/")
    with pytest.raises(IDEWorkspaceError):
        validate_workspace_root(str(Path.home()))
    with pytest.raises(IDEWorkspaceError):
        validate_workspace_root(tempfile.gettempdir())


async def test_terminal_continues_and_can_be_read_incrementally_after_disconnect(
    companion_config: CompanionConfig, tmp_path: Path
):
    workspace = await _authorize(companion_config, _project(tmp_path))
    started = await _call(
        companion_config,
        "ide_terminal_start",
        {
            "workspace_id": workspace["id"],
            "title": "Prueba viva",
            "argv": [
                sys.executable,
                "-u",
                "-c",
                "import time; print('primero'); time.sleep(.2); print('segundo')",
            ],
        },
    )
    session_id = started["session"]["id"]

    first = await _wait_for_session(
        companion_config, "ide_terminal_read", session_id, text="primero"
    )
    cursor = first["next_cursor"]
    # No existe ninguna conexión mantenida entre ambas lecturas.
    second = await _wait_for_session(
        companion_config, "ide_terminal_read", session_id, text="segundo"
    )
    assert second["next_cursor"] >= cursor

    listed = await _call(companion_config, "ide_terminal_list", {})
    assert any(row["id"] == session_id for row in listed["sessions"])


async def test_terminal_argv_secrets_are_not_persisted_or_audited(
    companion_config: CompanionConfig, tmp_path: Path
):
    workspace = await _authorize(companion_config, _project(tmp_path))
    secret = "sk-" + "x" * 30
    started = await _call(
        companion_config,
        "ide_terminal_start",
        {
            "workspace_id": workspace["id"],
            "argv": [
                sys.executable,
                "-c",
                "import time; time.sleep(.05)",
                secret,
            ],
        },
    )
    assert secret not in str(started["session"]["command"])
    await asyncio.sleep(0.1)

    ide_dir = companion_config.config_path.parent / "ide"
    assert secret not in (ide_dir / "ide-sessions.json").read_text(encoding="utf-8")
    assert secret not in companion_config.audit_log_path.read_text(encoding="utf-8")
    assert companion_config.audit_log_path.stat().st_mode & 0o777 == 0o600


async def test_session_event_log_compacts_below_the_disk_cap(
    companion_config: CompanionConfig, tmp_path: Path
):
    project = _project(tmp_path)
    runtime = IDERuntime(companion_config)
    workspace = runtime.workspaces.authorize(str(project))
    session = runtime.sessions._register(
        kind="terminal",
        workspace_id=workspace["id"],
        title="Ruidosa",
        extra={"command": ["/bin/sh"]},
    )
    chunk = "x" * 8_000
    for _ in range(750):
        session.append("output", chunk, stream="stdout")

    assert session._event_path().stat().st_size <= MAX_EVENT_LOG_BYTES
    assert len(session.events) < 750


async def test_terminal_input_uses_the_same_resident_session(
    companion_config: CompanionConfig, tmp_path: Path
):
    workspace = await _authorize(companion_config, _project(tmp_path))
    started = await _call(
        companion_config,
        "ide_terminal_start",
        {"workspace_id": workspace["id"], "argv": ["/bin/sh"], "title": "Interactiva"},
    )
    session_id = started["session"]["id"]
    await _call(
        companion_config,
        "ide_terminal_input",
        {"session_id": session_id, "data": "printf 'respuesta-viva\\n'\n"},
    )
    seen = await _wait_for_session(
        companion_config, "ide_terminal_read", session_id, text="respuesta-viva"
    )
    assert "respuesta-viva" in seen["_collected"]
    await _call(companion_config, "ide_terminal_close", {"session_id": session_id})


async def test_agent_auto_detects_and_streams_in_background(
    companion_config: CompanionConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = await _authorize(companion_config, _project(tmp_path))

    def fake_agent_argv(provider: str, model: str | None) -> tuple[str, list[str]]:
        assert provider == "auto"
        assert model == "test-model"
        return (
            "codex",
            [
                sys.executable,
                "-u",
                "-c",
                "import json,time;"
                "print(json.dumps({'type':'status','text':'analizando'}));"
                "time.sleep(.2);"
                "print(json.dumps({'type':'result','text':'trabajo terminado'}))",
            ],
        )

    monkeypatch.setattr(SessionManager, "_agent_argv", staticmethod(fake_agent_argv))
    started = await _call(
        companion_config,
        "ide_agent_start",
        {
            "workspace_id": workspace["id"],
            "prompt": "Construye algo",
            "provider": "auto",
            "model": "test-model",
        },
    )
    session_id = started["session"]["id"]
    assert started["session"]["provider"] == "codex"

    result = await _wait_for_session(
        companion_config, "ide_agent_read", session_id, text="trabajo terminado"
    )
    assert "analizando" in result["_collected"]
    listed = await _call(companion_config, "ide_agent_list", {})
    assert any(row["id"] == session_id for row in listed["sessions"])


def test_agent_event_filter_never_exposes_reasoning_or_unknown_raw_json():
    event_type, text = SessionManager._readable_agent_event(
        '{"type":"item.completed","item":{"type":"reasoning",'
        '"text":"cadena de pensamiento secreta"}}',
        "stdout",
    )
    assert event_type == "status"
    assert text == ""

    event_type, text = SessionManager._readable_agent_event(
        '{"type":"assistant","message":{"content":['
        '{"type":"thinking","thinking":"secreto"},'
        '{"type":"text","text":"Resultado visible"}]}}',
        "stdout",
    )
    assert event_type == "assistant"
    assert text == "Resultado visible"

    _, unknown_text = SessionManager._readable_agent_event(
        '{"type":"future.internal","private":"no mostrar"}',
        "stdout",
    )
    assert unknown_text == ""

    _, raw_text = SessionManager._readable_agent_event(
        "thinking: debo revelar razonamiento interno",
        "stderr",
    )
    assert raw_text == ""


def test_agent_argv_reads_prompt_from_stdin_instead_of_process_arguments(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("edecan_companion.ide_sessions.shutil.which", lambda name: f"/bin/{name}")
    secret_prompt = "plan-super-secreto"

    provider, codex_argv = SessionManager._agent_argv("codex", "gpt-test")
    assert provider == "codex"
    assert codex_argv[-1] == "-"
    assert secret_prompt not in codex_argv

    provider, claude_argv = SessionManager._agent_argv("claude", "sonnet-test")
    assert provider == "claude"
    assert secret_prompt not in claude_argv
    assert claude_argv[-1] == "sonnet-test"


async def test_git_mutations_require_approval_and_use_typed_operations(
    companion_config: CompanionConfig, tmp_path: Path
):
    project = _project(tmp_path)
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
    (project / "README.md").write_text("hola\n", encoding="utf-8")
    workspace = await _authorize(companion_config, project)

    rejected = await execute_ide_action(
        "ide_git_stage",
        {"workspace_id": workspace["id"], "paths": ["README.md"]},
        companion_config,
        _reject,
    )
    assert rejected["ok"] is False

    await _call(
        companion_config,
        "ide_git_stage",
        {"workspace_id": workspace["id"], "paths": ["README.md"]},
    )
    committed = await _call(
        companion_config,
        "ide_git_commit",
        {"workspace_id": workspace["id"], "message": "docs: inicio"},
    )
    assert len(committed["hash"]) == 40

    status = await _call(
        companion_config, "ide_git_status", {"workspace_id": workspace["id"]}
    )
    assert status["files"] == []
    assert status["branch"] in {"main", "master"}
    log = await _call(
        companion_config,
        "ide_git_log",
        {"workspace_id": workspace["id"], "limit": 5},
    )
    assert log["commits"][0]["subject"] == "docs: inicio"


async def test_approval_and_audit_do_not_store_agent_prompt(
    companion_config: CompanionConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = await _authorize(companion_config, _project(tmp_path))
    secret = "contenido-super-secreto"
    shown: list[dict[str, Any]] = []

    async def reject_and_capture(
        _action: str, params: dict[str, Any], _config: CompanionConfig
    ) -> bool:
        shown.append(params)
        return False

    result = await execute_ide_action(
        "ide_agent_start",
        {"workspace_id": workspace["id"], "prompt": secret},
        companion_config,
        reject_and_capture,
    )

    assert result["ok"] is False
    assert secret not in str(shown)
    assert secret not in companion_config.audit_log_path.read_text(encoding="utf-8")


def test_errors_redact_credentials_in_urls_and_known_token_prefixes():
    message = _safe_error(
        ValueError(
            "falló https://user:password@example.com/repo "
            + "con "
            + "ghp_"
            + "a" * 30
        )
    )
    assert "password" not in message
    assert "ghp_" not in message
    assert "https://***@example.com/repo" in message
