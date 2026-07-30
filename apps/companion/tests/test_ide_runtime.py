"""Pruebas del runtime IDE residente, sin tocar ``actions.py``."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from edecan_companion import ide_clone as ide_clone_module
from edecan_companion import ide_runtime as ide_runtime_module
from edecan_companion import ide_workspaces as ide_workspaces_module
from edecan_companion.config import CompanionConfig
from edecan_companion.ide_clone import (
    CloneService,
    IDECloneError,
    validate_destination_name,
    validate_repository_url,
)
from edecan_companion.ide_runtime import IDERuntime, _safe_error, execute_ide_action
from edecan_companion.ide_sessions import SessionManager
from edecan_companion.ide_workspaces import IDEWorkspaceError, validate_workspace_root


async def _approve(_action: str, _params: dict[str, Any], _config: CompanionConfig) -> bool:
    return True


async def _reject(_action: str, _params: dict[str, Any], _config: CompanionConfig) -> bool:
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


async def test_workspace_picker_authorizes_local_selection_without_remote_path(
    companion_config: CompanionConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project = _project(tmp_path, "elegido-en-finder")
    monkeypatch.setattr(ide_runtime_module, "pick_workspace_folder", lambda: str(project))

    # El propio diálogo local es la aprobación explícita. No se muestra un
    # segundo prompt remoto y ninguna ruta absoluta llega desde el teléfono.
    result = await execute_ide_action(
        "ide_workspace_pick",
        {"name": "Proyecto elegido", "activate": True},
        companion_config,
        _reject,
    )

    assert result["ok"] is True
    picked = result["result"]
    assert picked["cancelled"] is False
    assert picked["workspace"]["path"] == str(project.resolve())
    assert picked["workspace"]["active"] is True


async def test_workspace_picker_cancellation_is_not_an_error(
    companion_config: CompanionConfig,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(ide_runtime_module, "pick_workspace_folder", lambda: None)

    result = await _call(companion_config, "ide_workspace_pick", {})

    assert result == {"cancelled": True}


def test_macos_workspace_picker_is_owned_by_frontmost_finder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project = _project(tmp_path, "seleccion-visible")
    observed: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout=f"{project}\n", stderr="")

    monkeypatch.setattr(ide_workspaces_module.sys, "platform", "darwin")
    monkeypatch.setattr(ide_workspaces_module.subprocess, "run", fake_run)

    selected = ide_workspaces_module.pick_workspace_folder()

    assert selected == str(project.resolve())
    assert observed["argv"][0] == "/usr/bin/osascript"
    script = observed["argv"][2]
    assert 'tell application "Finder"' in script
    assert "activate" in script
    assert "choose folder" in script
    assert observed["kwargs"]["shell"] is False


async def test_workspace_list_reports_live_availability_and_git_state(
    companion_config: CompanionConfig, tmp_path: Path
):
    project = _project(tmp_path)
    (project / ".git").mkdir()
    workspace = await _authorize(companion_config, project)

    listed = await _call(companion_config, "ide_workspace_list", {})
    assert listed["workspaces"][0]["id"] == workspace["id"]
    assert listed["workspaces"][0]["available"] is True
    assert listed["workspaces"][0]["is_git_repository"] is True

    (project / ".git").rmdir()
    project.rmdir()
    listed = await _call(companion_config, "ide_workspace_list", {})
    assert listed["workspaces"][0]["available"] is False
    assert listed["workspaces"][0]["is_git_repository"] is False

    fresh = IDERuntime(companion_config)
    reloaded = fresh.workspaces.list()
    assert reloaded[0]["id"] == workspace["id"]
    assert reloaded[0]["available"] is False


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


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "/tmp/repository",
        "file:///tmp/repository",
        "https://user:secret@example.com/team/repository.git",
        "https://example.com/team/repository.git?token=secret",
        "ftp://example.com/team/repository.git",
    ],
)
def test_clone_rejects_local_paths_credentials_and_unsupported_urls(unsafe_url: str):
    with pytest.raises(IDECloneError):
        validate_repository_url(unsafe_url)


def test_clone_validates_https_ssh_and_destination_names():
    assert validate_repository_url("https://github.com/example/repository.git") == (
        "https://github.com/example/repository.git",
        "github.com",
        "repository",
    )
    assert validate_repository_url("git@github.com:example/repository.git") == (
        "git@github.com:example/repository.git",
        "github.com",
        "repository",
    )
    with pytest.raises(IDECloneError):
        validate_destination_name("../escape")


async def test_clone_runs_typed_git_inside_an_authorized_workspace_and_registers_result(
    companion_config: CompanionConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    parent = _project(tmp_path, "repositorios")
    parent_workspace = await _authorize(companion_config, parent)
    captured: dict[str, Any] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = dict(kwargs)
        checkout = Path(argv[-1])
        checkout.mkdir(parents=True)
        (checkout / ".git").mkdir()
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(ide_clone_module.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(ide_clone_module.subprocess, "run", fake_run)

    cloned = await _call(
        companion_config,
        "ide_workspace_clone",
        {
            "parent_workspace_id": parent_workspace["id"],
            "url": "https://github.com/example/demo.git",
            "name": "demo-seguro",
            "branch": "main",
            "depth": 1,
            "activate": True,
        },
    )

    destination = parent / "demo-seguro"
    assert destination.is_dir()
    assert cloned["workspace"]["path"] == str(destination.resolve())
    assert cloned["workspace"]["active"] is True
    assert cloned["repository"] == {
        "name": "demo-seguro",
        "source_host": "github.com",
        "branch": "main",
        "depth": 1,
    }
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == parent.resolve()
    assert captured["kwargs"]["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["argv"][-3:-1] == ["--", "https://github.com/example/demo.git"]


async def test_clone_requires_approval_before_creating_any_directory(
    companion_config: CompanionConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    parent = _project(tmp_path, "repositorios")
    parent_workspace = await _authorize(companion_config, parent)

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("Git no debe ejecutarse sin aprobación")

    monkeypatch.setattr(CloneService, "clone", should_not_run)
    result = await execute_ide_action(
        "ide_workspace_clone",
        {
            "parent_workspace_id": parent_workspace["id"],
            "url": "https://github.com/example/demo.git",
        },
        companion_config,
        _reject,
    )

    assert result["ok"] is False
    assert list(parent.iterdir()) == []


def test_default_terminal_uses_a_clean_shell(monkeypatch: pytest.MonkeyPatch):
    if not Path("/bin/zsh").exists():
        pytest.skip("zsh no está disponible en este sistema")
    monkeypatch.setenv("SHELL", "/bin/zsh")

    argv = SessionManager._default_terminal_argv()

    assert argv == ["/bin/zsh", "-f"]


async def test_default_terminal_does_not_load_broken_user_zshenv(
    companion_config: CompanionConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    if not Path("/bin/zsh").exists():
        pytest.skip("zsh no está disponible en este sistema")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".zshenv").write_text(
        "print 'NO_DEBE_APARECER_DESDE_ZSHENV'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    workspace = await _authorize(companion_config, _project(tmp_path))
    started = await _call(
        companion_config,
        "ide_terminal_start",
        {"workspace_id": workspace["id"], "title": "Shell limpio"},
    )
    session_id = started["session"]["id"]
    await _call(
        companion_config,
        "ide_terminal_input",
        {"session_id": session_id, "data": "printf 'TERMINAL_LIMPIA\\n'\n"},
    )

    seen = await _wait_for_session(
        companion_config,
        "ide_terminal_read",
        session_id,
        text="TERMINAL_LIMPIA",
    )

    assert "NO_DEBE_APARECER_DESDE_ZSHENV" not in seen["_collected"]
    await _call(companion_config, "ide_terminal_close", {"session_id": session_id})


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


async def test_session_event_log_is_append_only_and_pages_old_events(
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

    persisted = session.read(0)
    assert session._event_path().stat().st_size > 750 * len(chunk)
    assert persisted["events"][0]["cursor"] == 1
    assert persisted["has_more"] is True
    second_page = session.read(persisted["next_cursor"])
    assert second_page["events"]
    assert second_page["events"][-1]["cursor"] == 750


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


async def test_workers_agent_streams_in_background(
    companion_config: CompanionConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = await _authorize(companion_config, _project(tmp_path))

    async def fake_run(_self, **kwargs):
        assert kwargs["prompt"] == "Construye algo"
        assert kwargs["model"] == "test-model"
        kwargs["write_event"]("status", "analizando")
        await asyncio.sleep(0.02)
        kwargs["write_event"]("assistant_final", "trabajo terminado")

    monkeypatch.setattr(
        "edecan_companion.ide_workers_agent.WorkersIDEAgent.run",
        fake_run,
    )
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
    assert started["session"]["provider"] == "workers_ai"

    result = await _wait_for_session(
        companion_config, "ide_agent_read", session_id, text="trabajo terminado"
    )
    assert "analizando" in result["_collected"]
    listed = await _call(companion_config, "ide_agent_list", {})
    assert any(row["id"] == session_id for row in listed["sessions"])


async def test_workers_agent_cannot_complete_without_a_final_answer(
    companion_config: CompanionConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = await _authorize(companion_config, _project(tmp_path))

    async def fake_run_without_final(_self, **kwargs):
        kwargs["write_event"]("output", "solo salida técnica")

    monkeypatch.setattr(
        "edecan_companion.ide_workers_agent.WorkersIDEAgent.run",
        fake_run_without_final,
    )
    started = await _call(
        companion_config,
        "ide_agent_start",
        {
            "workspace_id": workspace["id"],
            "prompt": "Haz el trabajo",
            "provider": "auto",
        },
    )
    session_id = started["session"]["id"]

    latest: dict[str, Any] = {}
    for _ in range(80):
        latest = await _call(
            companion_config,
            "ide_agent_read",
            {"session_id": session_id, "cursor": 0},
        )
        if latest["session"]["status"] != "running":
            break
        await asyncio.sleep(0.05)

    assert latest["session"]["status"] == "failed"
    assert latest["session"]["exit_code"] == 1
    finals = [event for event in latest["events"] if event["type"] == "assistant_final"]
    assert len(finals) == 1
    assert "No marqué la tarea como completada" in finals[0]["text"]
    assert any(
        event["type"] == "error" and "sin entregar una respuesta final" in event["text"]
        for event in latest["events"]
    )


async def test_workers_agent_rejects_legacy_cli_provider(
    companion_config: CompanionConfig, tmp_path: Path
):
    runtime = IDERuntime(companion_config)
    workspace = runtime.workspaces.authorize(str(_project(tmp_path)))
    with pytest.raises(Exception, match="Workers AI"):
        runtime.sessions.start_agent(
            workspace["id"],
            "Hazlo",
            provider="codex",
        )


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

    status = await _call(companion_config, "ide_git_status", {"workspace_id": workspace["id"]})
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
        ValueError("falló https://user:password@example.com/repo " + "con " + "ghp_" + "a" * 30)
    )
    assert "password" not in message
    assert "ghp_" not in message
    assert "https://***@example.com/repo" in message
