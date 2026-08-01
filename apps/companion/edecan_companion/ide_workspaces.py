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
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from edecan_companion.platform_paths import reemplazar_con_reintentos


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
PICKER_TIMEOUT_SECONDS = 300
# Variables de entorno cuyo valor, en Windows, apunta a una carpeta del
# sistema (equivalente Windows de ``_SENSITIVE_POSIX_ROOTS``). Sin este
# bloqueo, alguien podría autorizar ``C:\Windows`` o ``C:\Program Files``
# como workspace y el IDE tendría lectura/escritura sobre el sistema
# operativo completo — la misma clase de fuga que la lista POSIX evita en
# macOS/Linux, pero ausente para Windows si no se declara aquí.
_SENSITIVE_WINDOWS_ROOT_ENV_VARS = (
    "SystemRoot",
    "windir",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "ProgramData",
    "CommonProgramFiles",
)


def _sensitive_windows_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    for name in _SENSITIVE_WINDOWS_ROOT_ENV_VARS:
        value = os.environ.get(name)
        if not value:
            continue
        try:
            roots.append(Path(value).resolve())
        except OSError:
            continue
    return tuple(roots)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _normalize_workspace_root(raw_path: str, *, require_exists: bool) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise IDEWorkspaceError("La ruta del workspace está vacía.")
    if "\x00" in raw_path:
        raise IDEWorkspaceError("La ruta del workspace contiene caracteres inválidos.")

    root = Path(raw_path).expanduser().resolve(strict=require_exists)
    if require_exists and not root.is_dir():
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
    if os.name == "nt" and not is_temporary_workspace:
        for sensitive in _sensitive_windows_roots():
            if root == sensitive or _is_relative_to(root, sensitive):
                raise IDEWorkspaceError(
                    f"No se puede autorizar una carpeta del sistema: {sensitive}."
                )
    return root


def validate_workspace_root(raw_path: str) -> Path:
    """Resuelve y valida una carpeta que el usuario quiere autorizar.

    Se bloquean raíces demasiado amplias y carpetas que normalmente contienen
    credenciales. Proyectos dentro de ``HOME`` sí son válidos; ``HOME`` entero
    no lo es.
    """

    return _normalize_workspace_root(raw_path, require_exists=True)


def pick_workspace_folder() -> str | None:
    """Abre el selector nativo de carpetas y devuelve la ruta elegida.

    El diálogo ocurre en la computadora, no en el teléfono. Cancelar es un
    resultado normal y devuelve ``None``. No se usa shell ni se interpola
    contenido enviado por la red en ningún script.
    """

    if sys.platform == "darwin":
        # Un ``choose folder`` ejecutado por el proceso ``osascript`` del
        # sidecar puede quedar detrás de la ventana Tauri porque ese proceso
        # no es una aplicación gráfica visible. Hacer que Finder sea dueño del
        # panel le da una ventana frontal real, accesible y reconocida por
        # macOS, sin conceder al backend acceso a una ruta que la persona no
        # haya seleccionado explícitamente.
        script = (
            'tell application "Finder"\n'
            "activate\n"
            'set selectedFolder to choose folder with prompt "Elige una carpeta para Edecán"\n'
            "end tell\n"
            "return POSIX path of selectedFolder"
        )
        argv = [
            "/usr/bin/osascript",
            "-e",
            script,
        ]
    elif os.name == "nt":
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            raise IDEWorkspaceError("No se encontró el selector de carpetas de Windows.")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$dialog.Description = 'Elige una carpeta para Edecán';"
            "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
            "{ [Console]::Out.Write($dialog.SelectedPath) }"
        )
        argv = [powershell, "-NoProfile", "-STA", "-Command", script]
    else:
        zenity = shutil.which("zenity")
        kdialog = shutil.which("kdialog")
        if zenity is not None:
            argv = [
                zenity,
                "--file-selection",
                "--directory",
                "--title=Elige una carpeta para Edecán",
            ]
        elif kdialog is not None:
            argv = [kdialog, "--getexistingdirectory", ".", "Elige una carpeta para Edecán"]
        else:
            raise IDEWorkspaceError(
                "No hay un selector gráfico de carpetas disponible. "
                "Instala zenity o kdialog, o autoriza la ruta manualmente."
            )

    try:
        completed = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            timeout=PICKER_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise IDEWorkspaceError("El selector de carpetas agotó el tiempo de espera.") from exc

    selected = completed.stdout.strip()
    if completed.returncode != 0 or not selected:
        return None
    return str(validate_workspace_root(selected))


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
                    # Conservar workspaces de volúmenes externos temporalmente
                    # desconectados. ``list`` los marca ``available=false`` y
                    # vuelven a estar listos cuando reaparece la misma ruta.
                    resolved = _normalize_workspace_root(path, require_exists=False)
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
        # Reintentos SOLO en Windows: un antivirus/backup puede tener el
        # registro de workspaces abierto un instante (ver
        # `platform_paths.reemplazar_con_reintentos`) -- en POSIX se comporta
        # exactamente igual que el `os.replace` de siempre.
        reemplazar_con_reintentos(temp_path, self.path)

    @staticmethod
    def _snapshot(row: dict[str, Any]) -> dict[str, Any]:
        snapshot = dict(row)
        root = Path(row["path"])
        snapshot["available"] = root.is_dir()
        snapshot["is_git_repository"] = snapshot["available"] and (root / ".git").exists()
        return snapshot

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [self._snapshot(row) for row in self._workspaces.values()]
            return sorted(
                rows,
                key=lambda row: (
                    not bool(row["active"]),
                    str(row["name"]).casefold(),
                    str(row["id"]),
                ),
            )

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
                    return self._snapshot(row)
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
            return self._snapshot(row)

    def activate(self, workspace_id: str) -> dict[str, Any]:
        with self._lock:
            selected = self._workspaces.get(workspace_id)
            if selected is None:
                raise IDEWorkspaceError("Workspace no encontrado.")
            for row in self._workspaces.values():
                row["active"] = row["id"] == workspace_id
            self._save()
            return self._snapshot(selected)

    def get(self, workspace_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._workspaces.get(workspace_id)
            if row is None:
                raise IDEWorkspaceError("Workspace no encontrado.")
            return self._snapshot(row)

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
