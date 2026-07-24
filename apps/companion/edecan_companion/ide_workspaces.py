"""Workspaces autorizados para el IDE local de Edecán.

Un workspace es una carpeta elegida explícitamente por el dueño del equipo.
Después de autorizarla, todas las rutas que cruzan el bridge son relativas a
su ``workspace_id``. La ruta real nunca se acepta desde el teléfono para una
operación de archivo, terminal, agente o Git.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class IDEWorkspaceError(ValueError):
    """Solicitud de workspace/ruta inválida."""


_SENSITIVE_HOME_CHILDREN = frozenset(
    {
        ".ssh",
        ".gnupg",
        ".aws",
        ".azure",
        ".kube",
        ".docker",
        ".config",
        "Library/Keychains",
    }
)
_SENSITIVE_POSIX_ROOTS = (
    Path("/System"),
    Path("/Library"),
    Path("/Applications"),
    Path("/bin"),
    Path("/sbin"),
    Path("/usr"),
    Path("/etc"),
    Path("/var"),
    Path("/private"),
    Path("/dev"),
    Path("/proc"),
)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_workspace_root(raw_path: str) -> Path:
    """Resuelve y valida una carpeta que el usuario quiere autorizar.

    Se bloquean raíces demasiado amplias y carpetas que normalmente contienen
    credenciales. Proyectos dentro de ``HOME`` sí son válidos; ``HOME`` entero
    no lo es.
    """

    if not isinstance(raw_path, str) or not raw_path.strip():
        raise IDEWorkspaceError("La ruta del workspace está vacía.")
    if "\x00" in raw_path:
        raise IDEWorkspaceError("La ruta del workspace contiene caracteres inválidos.")

    root = Path(raw_path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise IDEWorkspaceError("El workspace debe ser una carpeta existente.")

    home = Path.home().resolve()
    filesystem_anchor = Path(root.anchor).resolve()
    if root == filesystem_anchor:
        raise IDEWorkspaceError("No se puede autorizar la raíz completa del sistema.")
    if root == home:
        raise IDEWorkspaceError("No se puede autorizar la carpeta personal completa.")

    for relative in _SENSITIVE_HOME_CHILDREN:
        sensitive = (home / relative).resolve()
        if root == sensitive or _is_relative_to(root, sensitive):
            raise IDEWorkspaceError(f"No se puede autorizar una carpeta sensible: {relative}.")

    temporary_root = Path(tempfile.gettempdir()).resolve()
    # Permitir proyectos aislados *dentro* del directorio temporal facilita
    # builds/tests, pero nunca autorizar la raíz temporal completa.
    if root == temporary_root:
        raise IDEWorkspaceError("No se puede autorizar la raíz temporal completa.")
    is_temporary_workspace = root != temporary_root and _is_relative_to(root, temporary_root)
    if os.name != "nt" and not is_temporary_workspace:
        for sensitive in _SENSITIVE_POSIX_ROOTS:
            if root == sensitive or _is_relative_to(root, sensitive):
                raise IDEWorkspaceError(
                    f"No se puede autorizar una carpeta del sistema: {sensitive}."
                )
    return root


class WorkspaceStore:
    """Registro JSON local, atómico y privado de workspaces autorizados."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.path = state_dir / "ide-workspaces.json"
        self._lock = threading.RLock()
        self._workspaces: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            rows = data.get("workspaces", []) if isinstance(data, dict) else []
            if not isinstance(rows, list):
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                workspace_id = row.get("id")
                path = row.get("path")
                if not isinstance(workspace_id, str) or not isinstance(path, str):
                    continue
                try:
                    resolved = validate_workspace_root(path)
                except (IDEWorkspaceError, OSError):
                    continue
                self._workspaces[workspace_id] = {
                    "id": workspace_id,
                    "name": str(row.get("name") or resolved.name),
                    "path": str(resolved),
                    "active": bool(row.get("active", False)),
                    "created_at": str(row.get("created_at") or utc_now()),
                }
            if self._workspaces and not any(row["active"] for row in self._workspaces.values()):
                next(iter(self._workspaces.values()))["active"] = True

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        payload = {"version": 1, "workspaces": list(self._workspaces.values())}
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temp_path, self.path)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._workspaces.values()]

    def authorize(self, raw_path: str, name: str | None = None) -> dict[str, Any]:
        root = validate_workspace_root(raw_path)
        clean_name = (name or root.name).strip()
        if not clean_name or len(clean_name) > 120 or _CONTROL_CHARS.search(clean_name):
            raise IDEWorkspaceError("El nombre del workspace no es válido.")
        with self._lock:
            for row in self._workspaces.values():
                if Path(row["path"]) == root:
                    if name:
                        row["name"] = clean_name
                        self._save()
                    return dict(row)
            workspace_id = str(uuid.uuid4())
            row = {
                "id": workspace_id,
                "name": clean_name,
                "path": str(root),
                "active": not self._workspaces,
                "created_at": utc_now(),
            }
            self._workspaces[workspace_id] = row
            self._save()
            return dict(row)

    def activate(self, workspace_id: str) -> dict[str, Any]:
        with self._lock:
            selected = self._workspaces.get(workspace_id)
            if selected is None:
                raise IDEWorkspaceError("Workspace no encontrado.")
            for row in self._workspaces.values():
                row["active"] = row["id"] == workspace_id
            self._save()
            return dict(selected)

    def get(self, workspace_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._workspaces.get(workspace_id)
            if row is None:
                raise IDEWorkspaceError("Workspace no encontrado.")
            return dict(row)

    def root(self, workspace_id: str) -> Path:
        row = self.get(workspace_id)
        root = Path(row["path"]).resolve(strict=True)
        if not root.is_dir():
            raise IDEWorkspaceError("La carpeta del workspace ya no existe.")
        return root

    def resolve(self, workspace_id: str, relative_path: str = ".") -> Path:
        root = self.root(workspace_id)
        if not isinstance(relative_path, str) or "\x00" in relative_path:
            raise IDEWorkspaceError("La ruta relativa no es válida.")
        candidate_input = Path(relative_path or ".")
        if candidate_input.is_absolute():
            raise IDEWorkspaceError("La ruta debe ser relativa al workspace.")
        candidate = (root / candidate_input).resolve(strict=False)
        if candidate != root and not _is_relative_to(candidate, root):
            raise IDEWorkspaceError("La ruta intenta salir del workspace autorizado.")
        # Si un ancestro existente es un symlink hacia afuera, ``resolve`` ya
        # lo expande y el chequeo anterior lo rechaza.
        return candidate
