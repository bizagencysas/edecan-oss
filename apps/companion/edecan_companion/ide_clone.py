"""Clonado seguro de repositorios dentro de workspaces autorizados.

El teléfono nunca elige una ruta absoluta de destino. Recibe un
``parent_workspace_id`` ya autorizado, un URL Git sin credenciales embebidas
y, opcionalmente, un nombre de carpeta. El clonado ocurre en una carpeta
temporal hija del workspace y se publica con un rename atómico únicamente
después de verificar que Git produjo un repositorio.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from edecan_companion.ide_workspaces import WorkspaceStore

CLONE_TIMEOUT_SECONDS = 300
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")
_SCP_STYLE_URL = re.compile(
    r"^(?:(?P<user>[A-Za-z0-9._-]+)@)?"
    r"(?P<host>[A-Za-z0-9.-]+):(?P<path>[^\\\s?#]+)$"
)
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)


class IDECloneError(ValueError):
    """Solicitud de clonado inválida o clonado fallido."""


def _repository_path_parts(path: str) -> list[str]:
    clean = path.strip("/")
    parts = [part for part in clean.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise IDECloneError("La URL no identifica un repositorio Git válido.")
    return parts


def validate_repository_url(raw_url: str) -> tuple[str, str, str]:
    """Valida una URL Git remota y devuelve ``(url, host, repo_name)``.

    Se aceptan HTTPS, SSH y la forma SCP ``git@host:owner/repo.git``. Se
    rechazan rutas locales, ``file://``, parámetros y credenciales embebidas.
    Los repositorios privados pueden usar el agente SSH o el credential helper
    local de Git sin revelar secretos al API ni al log de auditoría.
    """

    if not isinstance(raw_url, str):
        raise IDECloneError("La URL del repositorio debe ser texto.")
    url = raw_url.strip()
    if not url or len(url) > 2048 or _CONTROL_OR_SPACE.search(url):
        raise IDECloneError("La URL del repositorio no es válida.")

    if "://" in url:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"https", "ssh"}:
            raise IDECloneError("Solo se permiten repositorios HTTPS o SSH.")
        if parsed.query or parsed.fragment:
            raise IDECloneError("La URL del repositorio no puede incluir parámetros.")
        try:
            host = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise IDECloneError("La URL del repositorio tiene un puerto inválido.") from exc
        if not host:
            raise IDECloneError("La URL del repositorio no tiene un host válido.")
        if parsed.password is not None:
            raise IDECloneError("No incluyas contraseñas ni tokens dentro de la URL.")
        if parsed.scheme.lower() == "https" and parsed.username is not None:
            raise IDECloneError("No incluyas credenciales dentro de la URL HTTPS.")
        parts = _repository_path_parts(parsed.path)
    else:
        match = _SCP_STYLE_URL.fullmatch(url)
        if match is None:
            raise IDECloneError("Usa una URL HTTPS o SSH; no se permiten rutas locales.")
        host = match.group("host")
        parts = _repository_path_parts(match.group("path"))

    repository_name = unquote(parts[-1])
    if repository_name.lower().endswith(".git"):
        repository_name = repository_name[:-4]
    repository_name = validate_destination_name(repository_name)
    return url, host.lower(), repository_name


def validate_destination_name(raw_name: str) -> str:
    if not isinstance(raw_name, str):
        raise IDECloneError("El nombre de destino debe ser texto.")
    name = raw_name.strip()
    if (
        not name
        or len(name) > 120
        or _CONTROL_OR_SPACE.search(name)
        or "/" in name
        or "\\" in name
        or name in {".", ".."}
        or name.startswith(".")
        or name.endswith((".", " "))
    ):
        raise IDECloneError("El nombre de la carpeta de destino no es válido.")
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise IDECloneError("El nombre de la carpeta de destino está reservado.")
    return name


def validate_branch(raw_branch: str | None) -> str | None:
    if raw_branch is None:
        return None
    if not isinstance(raw_branch, str):
        raise IDECloneError("La rama debe ser texto.")
    branch = raw_branch.strip()
    if (
        not branch
        or len(branch) > 200
        or _CONTROL_OR_SPACE.search(branch)
        or branch.startswith(("-", "/", "."))
        or branch.endswith(("/", "."))
        or "//" in branch
        or ".." in branch
        or "@{" in branch
        or any(character in branch for character in "\\~^:?*[")
    ):
        raise IDECloneError("El nombre de la rama no es válido.")
    return branch


def _git_failure_message(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    # El runtime aplica una segunda redacción. Aquí evitamos reflejar la línea
    # "Cloning into..." o el URL completo aunque Git los haya escrito.
    safe_lines = [
        line
        for line in lines
        if "cloning into" not in line.lower()
        and "http://" not in line.lower()
        and "https://" not in line.lower()
        and "ssh://" not in line.lower()
    ]
    detail = safe_lines[-1][:500] if safe_lines else "Git rechazó el repositorio."
    return f"No se pudo clonar el repositorio. {detail}"


class CloneService:
    def __init__(self, workspaces: WorkspaceStore) -> None:
        self.workspaces = workspaces

    def clone(
        self,
        *,
        parent_workspace_id: str,
        url: str,
        name: str | None = None,
        branch: str | None = None,
        depth: int | None = None,
        activate: bool = True,
    ) -> dict[str, Any]:
        repository_url, source_host, inferred_name = validate_repository_url(url)
        destination_name = validate_destination_name(name or inferred_name)
        selected_branch = validate_branch(branch)
        if depth is not None and (
            not isinstance(depth, int) or isinstance(depth, bool) or depth < 1 or depth > 1000
        ):
            raise IDECloneError("La profundidad debe ser un entero entre 1 y 1000.")
        if not isinstance(activate, bool):
            raise IDECloneError("activate debe ser true o false.")

        parent = self.workspaces.root(parent_workspace_id)
        destination = (parent / destination_name).resolve(strict=False)
        try:
            destination.relative_to(parent)
        except ValueError as exc:
            raise IDECloneError("El destino intenta salir del workspace autorizado.") from exc
        if destination.exists():
            raise IDECloneError("Ya existe una carpeta con ese nombre en el workspace.")

        git = shutil.which("git")
        if git is None:
            raise IDECloneError("Git no está instalado en esta computadora.")

        temp_parent = Path(tempfile.mkdtemp(prefix=".edecan-clone-", dir=parent))
        temp_checkout = temp_parent / "repository"
        argv = [
            git,
            "-c",
            "core.hooksPath=/dev/null",
            "clone",
            "--no-recurse-submodules",
        ]
        if selected_branch is not None:
            argv.extend(["--branch", selected_branch])
        if depth is not None:
            argv.extend(["--depth", str(depth)])
        argv.extend(["--", repository_url, str(temp_checkout)])

        environment = os.environ.copy()
        environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_LFS_SKIP_SMUDGE": "1",
            }
        )
        try:
            completed = subprocess.run(
                argv,
                cwd=parent,
                env=environment,
                text=True,
                capture_output=True,
                timeout=CLONE_TIMEOUT_SECONDS,
                check=False,
                shell=False,
            )
            if completed.returncode != 0:
                raise IDECloneError(_git_failure_message(completed.stderr))
            if not (temp_checkout / ".git").exists():
                raise IDECloneError("Git terminó sin crear un repositorio válido.")
            os.replace(temp_checkout, destination)
        except subprocess.TimeoutExpired as exc:
            raise IDECloneError("El clonado superó los cinco minutos y fue cancelado.") from exc
        finally:
            shutil.rmtree(temp_parent, ignore_errors=True)

        workspace = self.workspaces.authorize(str(destination), destination_name)
        if activate:
            workspace = self.workspaces.activate(workspace["id"])
        return {
            "workspace": workspace,
            "repository": {
                "name": destination_name,
                "source_host": source_host,
                "branch": selected_branch,
                "depth": depth,
            },
        }
