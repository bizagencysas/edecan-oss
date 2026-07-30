"""Operaciones Git tipadas para workspaces autorizados.

No acepta comandos de shell. Cada operación construye un ``argv`` fijo y
ejecuta ``git`` con ``shell=False`` dentro de la raíz autorizada.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from typing import Any

from edecan_companion.ide_workspaces import WorkspaceStore

MAX_GIT_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_GIT_STATUS_BYTES = 4 * 1024 * 1024
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class IDEGitError(ValueError):
    """Operación Git inválida o fallida."""


def _validate_ref(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _REF.fullmatch(value) or ".." in value:
        raise IDEGitError(f"{label} no es válido.")
    return value


class GitService:
    def __init__(self, workspaces: WorkspaceStore) -> None:
        self.workspaces = workspaces
        self.git = shutil.which("git")
        self._mutation_lock = threading.RLock()

    def _run(
        self,
        workspace_id: str,
        args: list[str],
        *,
        check: bool = True,
        timeout: float = 60,
    ) -> subprocess.CompletedProcess[bytes]:
        root = self.workspaces.root(workspace_id)
        if not self.git:
            raise IDEGitError("Git no está instalado o no está en PATH.")
        completed = subprocess.run(
            [self.git, "-C", str(root), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            shell=False,
            timeout=timeout,
            check=False,
        )
        if check and completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise IDEGitError(message[:1000] or f"Git terminó con código {completed.returncode}.")
        return completed

    def _paths(self, workspace_id: str, raw: Any, *, allow_empty: bool = False) -> list[str]:
        if not isinstance(raw, list) or (not raw and not allow_empty):
            raise IDEGitError("paths debe ser una lista no vacía.")
        paths: list[str] = []
        root = self.workspaces.root(workspace_id)
        for item in raw:
            if not isinstance(item, str) or not item or "\x00" in item:
                raise IDEGitError("paths contiene una ruta inválida.")
            resolved = self.workspaces.resolve(workspace_id, item)
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError as exc:
                raise IDEGitError("Una ruta intenta salir del workspace.") from exc
            paths.append(relative or ".")
        return paths

    def status(self, workspace_id: str) -> dict[str, Any]:
        completed = self._run(
            workspace_id,
            ["status", "--porcelain=v1", "-z", "--branch", "--untracked-files=all"],
        )
        if len(completed.stdout) > MAX_GIT_STATUS_BYTES:
            raise IDEGitError("El estado Git es demasiado grande para mostrarse de forma segura.")
        records = completed.stdout.decode("utf-8", errors="replace").split("\0")
        branch: str | None = None
        upstream: str | None = None
        ahead = 0
        behind = 0
        files: list[dict[str, Any]] = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            if record.startswith("## "):
                header = record[3:]
                if header.startswith("No commits yet on "):
                    branch = header.removeprefix("No commits yet on ")
                    continue
                relation = re.match(
                    r"^(?P<branch>.+?)(?:\.\.\.(?P<upstream>[^\s]+))?(?: \[(?P<counts>.+)\])?$",
                    header,
                )
                if relation:
                    branch = relation.group("branch")
                    upstream = relation.group("upstream")
                    counts = relation.group("counts") or ""
                    ahead_match = re.search(r"ahead (\d+)", counts)
                    behind_match = re.search(r"behind (\d+)", counts)
                    ahead = int(ahead_match.group(1)) if ahead_match else 0
                    behind = int(behind_match.group(1)) if behind_match else 0
                continue
            if len(record) < 4:
                continue
            index_status, worktree_status = record[0], record[1]
            path = record[3:]
            original_path: str | None = None
            if index_status in {"R", "C"} and index < len(records):
                original_path = records[index] or None
                index += 1
            files.append(
                {
                    "path": path,
                    "index_status": index_status,
                    "worktree_status": worktree_status,
                    "original_path": original_path,
                }
            )
        if branch == "HEAD (no branch)":
            branch = None
        return {
            "branch": branch,
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
            "files": files,
        }

    def diff(
        self, workspace_id: str, *, staged: bool = False, paths: Any = None
    ) -> dict[str, Any]:
        args = ["diff", "--no-ext-diff", "--no-color"]
        if staged:
            args.append("--cached")
        if paths:
            args.extend(["--", *self._paths(workspace_id, paths)])
        completed = self._run(workspace_id, args)
        truncated = len(completed.stdout) > MAX_GIT_OUTPUT_BYTES
        text = completed.stdout[:MAX_GIT_OUTPUT_BYTES].decode("utf-8", errors="replace")
        return {"text": text, "truncated": truncated}

    def log(self, workspace_id: str, *, limit: int = 50) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise IDEGitError("limit debe estar entre 1 y 200.")
        separator = "\x1f"
        record_separator = "\x1e"
        fmt = separator.join(["%H", "%h", "%an", "%ae", "%aI", "%s"]) + record_separator
        completed = self._run(
            workspace_id,
            ["log", f"--max-count={limit}", f"--format={fmt}"],
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace")
            if "does not have any commits" in stderr or "unknown revision" in stderr:
                return {"commits": []}
            raise IDEGitError(stderr.strip()[:1000] or "No se pudo leer el historial Git.")
        commits: list[dict[str, Any]] = []
        for record in completed.stdout.decode("utf-8", errors="replace").split(record_separator):
            fields = record.strip().split(separator)
            if len(fields) != 6:
                continue
            raw_timestamp = fields[4]
            try:
                timestamp = datetime.fromisoformat(raw_timestamp).astimezone(UTC).isoformat()
            except ValueError:
                timestamp = raw_timestamp
            commits.append(
                {
                    "hash": fields[0],
                    "short_hash": fields[1],
                    "author": fields[2],
                    "email": fields[3],
                    "timestamp": timestamp,
                    "subject": fields[5],
                }
            )
        return {"commits": commits}

    def stage(self, workspace_id: str, paths: Any) -> dict[str, Any]:
        selected = self._paths(workspace_id, paths)
        with self._mutation_lock:
            self._run(workspace_id, ["add", "--", *selected])
        return {"ok": True, "paths": selected}

    def unstage(self, workspace_id: str, paths: Any) -> dict[str, Any]:
        selected = self._paths(workspace_id, paths)
        with self._mutation_lock:
            completed = self._run(
                workspace_id, ["restore", "--staged", "--", *selected], check=False
            )
            if completed.returncode != 0:
                # Repositorio todavía sin commits: se limpia el índice con
                # argv tipado, sin shell.
                self._run(
                    workspace_id,
                    ["rm", "--cached", "-r", "--ignore-unmatch", "--", *selected],
                )
        return {"ok": True, "paths": selected}

    def commit(self, workspace_id: str, message: Any) -> dict[str, Any]:
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message) > 10_000
            or "\x00" in message
        ):
            raise IDEGitError("El mensaje del commit no es válido.")
        with self._mutation_lock:
            self._run(workspace_id, ["commit", "--message", message])
            completed = self._run(workspace_id, ["rev-parse", "HEAD"])
        commit_hash = completed.stdout.decode("utf-8", errors="replace").strip()
        return {"ok": True, "hash": commit_hash}

    def branch(
        self, workspace_id: str, name: Any, *, checkout: bool = False
    ) -> dict[str, Any]:
        clean = _validate_ref(name, "El nombre de rama")
        with self._mutation_lock:
            if checkout:
                self._run(workspace_id, ["switch", "--create", clean])
            else:
                self._run(workspace_id, ["branch", clean])
        return {"ok": True, "name": clean, "checked_out": bool(checkout)}

    def checkout(
        self, workspace_id: str, name: Any, *, create: bool = False
    ) -> dict[str, Any]:
        clean = _validate_ref(name, "El nombre de rama")
        args = ["switch"]
        if create:
            args.append("--create")
        args.append(clean)
        with self._mutation_lock:
            self._run(workspace_id, args)
        return {"ok": True, "name": clean, "created": bool(create)}

    def push(
        self,
        workspace_id: str,
        *,
        remote: Any = "origin",
        branch: Any = None,
        set_upstream: bool = False,
    ) -> dict[str, Any]:
        clean_remote = _validate_ref(remote, "El remoto")
        clean_branch: str
        if branch is None:
            completed = self._run(workspace_id, ["branch", "--show-current"])
            clean_branch = completed.stdout.decode("utf-8", errors="replace").strip()
            if not clean_branch:
                raise IDEGitError("No hay una rama activa para publicar.")
        else:
            clean_branch = _validate_ref(branch, "La rama")
        args = ["push"]
        if set_upstream:
            args.append("--set-upstream")
        args.extend([clean_remote, clean_branch])
        with self._mutation_lock:
            self._run(workspace_id, args, timeout=180)
        return {
            "ok": True,
            "remote": clean_remote,
            "branch": clean_branch,
            "set_upstream": bool(set_upstream),
        }
