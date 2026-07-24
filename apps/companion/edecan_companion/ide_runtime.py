"""Runtime local de IDE, terminal, agentes y Git para Edecán.

Este módulo es deliberadamente aditivo: las acciones históricas continúan en
``actions.py``. Las acciones ``ide_*`` usan workspaces autorizados y procesos
residentes que sobreviven a desconexiones del teléfono.
"""

from __future__ import annotations

import asyncio
import atexit
import re
import subprocess
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from edecan_companion import audit
from edecan_companion.config import CompanionConfig
from edecan_companion.ide_files import FileService, IDEFileError
from edecan_companion.ide_git import GitService, IDEGitError
from edecan_companion.ide_sessions import IDESessionError, SessionManager
from edecan_companion.ide_workspaces import IDEWorkspaceError, WorkspaceStore

Approver = Callable[
    [str, dict[str, Any], CompanionConfig], Awaitable[bool]
]

IDE_ACTIONS = frozenset(
    {
        "ide_workspace_list",
        "ide_workspace_authorize",
        "ide_workspace_activate",
        "ide_tree",
        "ide_read_file",
        "ide_write_file",
        "ide_apply_edit",
        "ide_search",
        "ide_terminal_list",
        "ide_terminal_start",
        "ide_terminal_read",
        "ide_terminal_input",
        "ide_terminal_close",
        "ide_agent_list",
        "ide_agent_start",
        "ide_agent_read",
        "ide_agent_cancel",
        "ide_git_status",
        "ide_git_diff",
        "ide_git_log",
        "ide_git_stage",
        "ide_git_unstage",
        "ide_git_commit",
        "ide_git_branch",
        "ide_git_checkout",
        "ide_git_push",
    }
)

_APPROVAL_ACTIONS = frozenset(
    {
        "ide_workspace_authorize",
        "ide_terminal_start",
        "ide_agent_start",
        "ide_write_file",
        "ide_apply_edit",
        "ide_git_stage",
        "ide_git_unstage",
        "ide_git_commit",
        "ide_git_branch",
        "ide_git_checkout",
        "ide_git_push",
    }
)
_RUNTIMES: dict[str, IDERuntime] = {}
_RUNTIMES_LOCK = threading.Lock()
_URL_CREDENTIALS = re.compile(r"(https?://)[^/@\s]+(?::[^/@\s]*)?@")
_TOKENISH = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,})\b"
)
_HIGH_ENTROPY_SECRET = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{40,}(?![A-Za-z0-9_-])")


def _required_text(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} debe ser texto no vacío.")
    return value


def _optional_text(params: dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} debe ser texto.")
    return value


def _text(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} debe ser texto.")
    return value


def _integer(params: dict[str, Any], key: str, default: int) -> int:
    value = params.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} debe ser un entero.")
    return value


def _boolean(params: dict[str, Any], key: str, default: bool = False) -> bool:
    value = params.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} debe ser true o false.")
    return value


def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
    return audit.sanitize_params(params)


def _safe_error(exc: BaseException) -> str:
    message = str(exc) or type(exc).__name__
    message = _URL_CREDENTIALS.sub(r"\1***@", message)
    message = _TOKENISH.sub("<credencial omitida>", message)
    message = _HIGH_ENTROPY_SECRET.sub("<credencial omitida>", message)
    return message[:2000]


class IDERuntime:
    def __init__(self, config: CompanionConfig) -> None:
        state_dir = config.config_path.parent / "ide"
        self.workspaces = WorkspaceStore(state_dir)
        self.files = FileService(self.workspaces)
        self.sessions = SessionManager(state_dir, self.workspaces)
        self.git = GitService(self.workspaces)

    def dispatch(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if action == "ide_workspace_list":
            return {"workspaces": self.workspaces.list()}
        if action == "ide_workspace_authorize":
            return {
                "workspace": self.workspaces.authorize(
                    _required_text(params, "path"), _optional_text(params, "name")
                )
            }
        if action == "ide_workspace_activate":
            return {
                "workspace": self.workspaces.activate(_required_text(params, "workspace_id"))
            }

        if action == "ide_tree":
            return self.files.tree(
                _required_text(params, "workspace_id"),
                str(params.get("path") or "."),
                max_depth=_integer(params, "max_depth", 4),
                max_entries=_integer(params, "max_entries", 500),
            )
        if action == "ide_read_file":
            return self.files.read(
                _required_text(params, "workspace_id"), _required_text(params, "path")
            )
        if action == "ide_write_file":
            return self.files.write(
                _required_text(params, "workspace_id"),
                _required_text(params, "path"),
                _text(params, "content"),
            )
        if action == "ide_apply_edit":
            return self.files.edit(
                _required_text(params, "workspace_id"),
                _required_text(params, "path"),
                _required_text(params, "old_string"),
                _text(params, "new_string"),
                replace_all=_boolean(params, "replace_all"),
            )
        if action == "ide_search":
            return self.files.search(
                _required_text(params, "workspace_id"),
                _required_text(params, "query"),
                str(params.get("path") or "."),
            )

        if action == "ide_terminal_list":
            return self.sessions.list("terminal", _optional_text(params, "workspace_id"))
        if action == "ide_terminal_start":
            return self.sessions.start_terminal(
                _required_text(params, "workspace_id"),
                params.get("argv"),
                params.get("title"),
            )
        if action == "ide_terminal_read":
            return self.sessions.read(
                _required_text(params, "session_id"),
                "terminal",
                _integer(params, "cursor", 0),
            )
        if action == "ide_terminal_input":
            return self.sessions.input_terminal(
                _required_text(params, "session_id"), _required_text(params, "data")
            )
        if action == "ide_terminal_close":
            return self.sessions.close(_required_text(params, "session_id"), "terminal")

        if action == "ide_agent_list":
            return self.sessions.list("agent", _optional_text(params, "workspace_id"))
        if action == "ide_agent_start":
            provider = str(params.get("provider") or "auto")
            return self.sessions.start_agent(
                _required_text(params, "workspace_id"),
                _required_text(params, "prompt"),
                provider,
                params.get("title"),
                _optional_text(params, "model"),
            )
        if action == "ide_agent_read":
            return self.sessions.read(
                _required_text(params, "session_id"),
                "agent",
                _integer(params, "cursor", 0),
            )
        if action == "ide_agent_cancel":
            return self.sessions.close(_required_text(params, "session_id"), "agent")

        if action == "ide_git_status":
            return self.git.status(_required_text(params, "workspace_id"))
        if action == "ide_git_diff":
            return self.git.diff(
                _required_text(params, "workspace_id"),
                staged=_boolean(params, "staged"),
                paths=params.get("paths"),
            )
        if action == "ide_git_log":
            return self.git.log(
                _required_text(params, "workspace_id"), limit=_integer(params, "limit", 50)
            )
        if action == "ide_git_stage":
            return self.git.stage(_required_text(params, "workspace_id"), params.get("paths"))
        if action == "ide_git_unstage":
            return self.git.unstage(_required_text(params, "workspace_id"), params.get("paths"))
        if action == "ide_git_commit":
            return self.git.commit(
                _required_text(params, "workspace_id"), params.get("message")
            )
        if action == "ide_git_branch":
            return self.git.branch(
                _required_text(params, "workspace_id"),
                params.get("name"),
                checkout=_boolean(params, "checkout"),
            )
        if action == "ide_git_checkout":
            return self.git.checkout(
                _required_text(params, "workspace_id"),
                params.get("name"),
                create=_boolean(params, "create"),
            )
        if action == "ide_git_push":
            return self.git.push(
                _required_text(params, "workspace_id"),
                remote=params.get("remote", "origin"),
                branch=params.get("branch"),
                set_upstream=_boolean(params, "set_upstream"),
            )

        raise ValueError(f"acción IDE no soportada: {action!r}")


def _runtime_for(config: CompanionConfig) -> IDERuntime:
    key = str(config.config_path.expanduser().resolve())
    with _RUNTIMES_LOCK:
        runtime = _RUNTIMES.get(key)
        if runtime is None:
            runtime = IDERuntime(config)
            _RUNTIMES[key] = runtime
        return runtime


def _shutdown_runtimes() -> None:
    with _RUNTIMES_LOCK:
        runtimes = list(_RUNTIMES.values())
    for runtime in runtimes:
        runtime.sessions.shutdown()


atexit.register(_shutdown_runtimes)


async def execute_ide_action(
    action: str,
    raw_params: Any,
    config: CompanionConfig,
    approver: Approver,
) -> dict[str, Any]:
    """Ejecuta una acción ``ide_*`` con aprobación y auditoría local."""

    if action not in IDE_ACTIONS:
        return {"ok": False, "error": f"acción IDE no soportada: {action!r}"}
    if not config.ide_enabled:
        return {"ok": False, "error": "El IDE local está deshabilitado en esta computadora."}
    if raw_params is None:
        params: dict[str, Any] = {}
    elif isinstance(raw_params, dict):
        params = dict(raw_params)
    else:
        return {"ok": False, "error": "params debe ser un objeto JSON."}

    approved = True
    safe_params = _safe_params(params)
    try:
        if action in _APPROVAL_ACTIONS:
            try:
                approved = await approver(action, safe_params, config)
            except Exception as exc:
                error = f"No se pudo obtener la aprobación local: {_safe_error(exc)}"
                audit.log_action(
                    action=action,
                    params=safe_params,
                    approved=False,
                    ok=False,
                    error=error,
                    log_path=config.audit_log_path,
                )
                return {"ok": False, "error": error}
            if not approved:
                error = "Acción rechazada por el dueño de esta computadora."
                audit.log_action(
                    action=action,
                    params=safe_params,
                    approved=False,
                    ok=False,
                    error=error,
                    log_path=config.audit_log_path,
                )
                return {"ok": False, "error": error}

        runtime = _runtime_for(config)
        result = await asyncio.to_thread(runtime.dispatch, action, params)
        audit.log_action(
            action=action,
            params=safe_params,
            approved=approved,
            ok=True,
            log_path=config.audit_log_path,
        )
        return {"ok": True, "result": result}
    except (
        IDEWorkspaceError,
        IDEFileError,
        IDESessionError,
        IDEGitError,
        ValueError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        error = _safe_error(exc)
        audit.log_action(
            action=action,
            params=safe_params,
            approved=approved,
            ok=False,
            error=error,
            log_path=config.audit_log_path,
        )
        return {"ok": False, "error": error}
