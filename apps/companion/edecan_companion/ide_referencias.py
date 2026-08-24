"""Referencias con "@": autocompletar archivos, carpetas y símbolos.

Es el mecanismo por el que el compositor le da contexto al agente sin
describirlo con palabras: al escribir "@" el usuario elige un archivo, una
carpeta o un símbolo (función o clase) del proyecto abierto, y esa elección
queda como ficha visible antes de enviar. Este módulo resuelve, dado un
prefijo, qué mostrar en ese menú.

Se llama con cada tecla que el usuario presiona, así que el diseño prioriza
velocidad por encima de exactitud perfecta: en vez de recorrer el disco (o
relanzar ``git``) en cada letra, se mantiene un índice cacheado por
workspace que se reconstruye solo cada ``index_ttl_seconds``.

Reusa ``ide_workspaces.WorkspaceStore`` para la resolución y validación de
rutas (ningún resultado puede salirse del workspace autorizado) y el mismo
conjunto de carpetas ignoradas que ``ide_files`` (``.git``, ``node_modules``,
etc.) para no duplicar esa lista en dos sitios.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from edecan_companion.ide_files import _IGNORED as _IGNORED_DIRS
from edecan_companion.ide_workspaces import IDEWorkspaceError, WorkspaceStore

ReferenceKind = Literal["file", "folder", "symbol"]

# TTL del índice cacheado: suficiente para absorber una racha de teclas sin
# recorrer el disco en cada una, pero corto para no mostrar un archivo recién
# borrado o creado durante varios segundos.
INDEX_TTL_SECONDS = 3.0

# Topes para "repos enormes": sin esto, un monorepo de cientos de miles de
# archivos volvería cada búsqueda lenta o, peor, colgaría el proceso.
MAX_INDEXED_FILES = 20_000
MAX_INDEXED_SYMBOLS = 20_000
MAX_SYMBOLS_PER_FILE = 400
MAX_BYTES_FOR_SYMBOL_SCAN = 512 * 1024

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
GIT_LS_FILES_TIMEOUT_SECONDS = 5

_ALL_KINDS: tuple[ReferenceKind, ...] = ("file", "folder", "symbol")

# Extensiones que no aportan como referencia de texto: mostrarlas en el menú
# de "@" solo generaría ruido, ya que el agente no puede leerlas como código.
BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff", ".avif",
        ".pdf", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
        ".mp3", ".mp4", ".mov", ".avi", ".webm", ".wav", ".flac", ".m4a",
        ".ttf", ".otf", ".woff", ".woff2", ".eot",
        ".so", ".dylib", ".dll", ".exe", ".bin", ".class", ".jar",
        ".pyc", ".pyo", ".o", ".a",
        ".sqlite", ".sqlite3", ".db",
    }
)

# Extensiones donde vale la pena parsear símbolos (funciones/clases) por
# regex. Deliberadamente no es un parser real: alcanza para autocompletar un
# nombre, no para análisis semántico.
SYMBOL_EXTENSIONS = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})

_PY_SYMBOL_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+(?P<function_name>[A-Za-z_]\w*)"
    r"|^\s*class\s+(?P<class_name>[A-Za-z_]\w*)"
)
_JS_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+"
    r"(?P<function_name>[A-Za-z_$]\w*)"
    r"|^\s*(?:export\s+)?(?:default\s+)?class\s+(?P<class_name>[A-Za-z_$]\w*)"
    r"|^\s*(?:export\s+)?const\s+(?P<arrow_name>[A-Za-z_$]\w*)\s*(?::[^=]+)?=\s*"
    r"(?:async\s*)?\([^)]*\)\s*(?::[^=]+)?=>"
)


class IDEReferenceError(ValueError):
    """Prefijo, tipo o límite inválido para la búsqueda de referencias."""


@dataclass(frozen=True)
class _Index:
    built_at: float
    files: tuple[str, ...]
    folders: tuple[str, ...]
    symbols: tuple[tuple[str, str, str, int], ...]  # (name, kind, path, line)
    truncated: bool


class ReferenceService:
    """Autocompleta referencias "@" por prefijo dentro de un workspace autorizado."""

    def __init__(
        self,
        workspaces: WorkspaceStore,
        *,
        index_ttl_seconds: float = INDEX_TTL_SECONDS,
        max_indexed_files: int = MAX_INDEXED_FILES,
        max_indexed_symbols: int = MAX_INDEXED_SYMBOLS,
        max_symbols_per_file: int = MAX_SYMBOLS_PER_FILE,
        max_bytes_for_symbol_scan: int = MAX_BYTES_FOR_SYMBOL_SCAN,
        binary_extensions: frozenset[str] = BINARY_EXTENSIONS,
    ) -> None:
        self.workspaces = workspaces
        self.index_ttl_seconds = index_ttl_seconds
        self.max_indexed_files = max_indexed_files
        self.max_indexed_symbols = max_indexed_symbols
        self.max_symbols_per_file = max_symbols_per_file
        self.max_bytes_for_symbol_scan = max_bytes_for_symbol_scan
        self.binary_extensions = binary_extensions
        self._lock = threading.Lock()
        self._indexes: dict[str, _Index] = {}

    # ------------------------------------------------------------------ #
    # API pública
    # ------------------------------------------------------------------ #

    def search(
        self,
        workspace_id: str,
        prefix: str,
        *,
        kinds: tuple[ReferenceKind, ...] | None = None,
        limit: int = DEFAULT_LIMIT,
        recently_opened: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Busca archivos/carpetas/símbolos cuyo nombre casa con ``prefix``.

        ``recently_opened`` son rutas (relativas al workspace) que el
        llamador considera "abiertas recientemente" -- este módulo no
        rastrea eso, solo usa el orden que le pasan para desempatar.

        Los símbolos solo se evalúan si ``prefix`` no está vacío: sin filtro,
        un proyecto mediano ya tiene miles y el menú dejaría de ser útil.
        """
        if not isinstance(prefix, str):
            raise IDEReferenceError("prefix debe ser texto.")
        if not 1 <= limit <= MAX_LIMIT:
            raise IDEReferenceError(f"limit debe estar entre 1 y {MAX_LIMIT}.")
        allowed_kinds = kinds or _ALL_KINDS
        for kind in allowed_kinds:
            if kind not in _ALL_KINDS:
                raise IDEReferenceError(f"kind desconocido: {kind}.")

        index = self._get_index(workspace_id)
        needle = prefix.strip().lower()
        recent_rank = {path: position for position, path in enumerate(recently_opened or ())}

        ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        if "file" in allowed_kinds:
            for path in index.files:
                name = path.rsplit("/", 1)[-1]
                if not self._matches(needle, name, path):
                    continue
                row = self._row("file", path, name, symbol_kind=None, line=None)
                ranked.append((self._rank(name, path, needle, recent_rank), row))

        if "folder" in allowed_kinds:
            for path in index.folders:
                name = path.rsplit("/", 1)[-1]
                if not self._matches(needle, name, path):
                    continue
                row = self._row("folder", path, name, symbol_kind=None, line=None)
                ranked.append((self._rank(name, path, needle, recent_rank), row))

        if "symbol" in allowed_kinds and needle:
            for name, symbol_kind, path, line in index.symbols:
                if needle not in name.lower():
                    continue
                row = self._row("symbol", path, name, symbol_kind=symbol_kind, line=line)
                ranked.append((self._rank(name, path, needle, recent_rank), row))

        ranked.sort(key=lambda pair: pair[0])
        matches = [row for _, row in ranked[:limit]]
        return {
            "prefix": prefix,
            "matches": matches,
            "total_matches": len(ranked),
            "index_truncated": index.truncated,
        }

    def invalidate(self, workspace_id: str | None = None) -> None:
        """Fuerza reconstruir el índice en la próxima búsqueda.

        Útil para que el integrador la llame tras escribir/crear/borrar un
        archivo y no depender solo del TTL. Sin argumento, limpia todo.
        """
        with self._lock:
            if workspace_id is None:
                self._indexes.clear()
            else:
                self._indexes.pop(workspace_id, None)

    # ------------------------------------------------------------------ #
    # Ranking
    # ------------------------------------------------------------------ #

    @staticmethod
    def _row(
        kind: ReferenceKind, path: str, name: str, *, symbol_kind: str | None, line: int | None
    ) -> dict[str, Any]:
        return {"type": kind, "path": path, "name": name, "symbol_kind": symbol_kind, "line": line}

    @staticmethod
    def _matches(needle: str, name: str, path: str) -> bool:
        if not needle:
            return True
        return needle in name.lower() or needle in path.lower()

    @staticmethod
    def _rank(name: str, path: str, needle: str, recent_rank: dict[str, int]) -> tuple[Any, ...]:
        # Orden pedido: primero lo abierto recientemente, luego lo más
        # cercano a la raíz del proyecto; el resto son desempates estables.
        not_recent = path not in recent_rank
        recent_position = recent_rank.get(path, 1_000_000)
        not_prefix_match = bool(needle) and not name.lower().startswith(needle)
        depth = path.count("/")
        return (not_recent, not_prefix_match, recent_position, depth, name.lower(), path.lower())

    # ------------------------------------------------------------------ #
    # Índice cacheado
    # ------------------------------------------------------------------ #

    def _get_index(self, workspace_id: str) -> _Index:
        with self._lock:
            cached = self._indexes.get(workspace_id)
            if cached is not None and (time.monotonic() - cached.built_at) < self.index_ttl_seconds:
                return cached
        fresh = self._build_index(workspace_id)
        with self._lock:
            self._indexes[workspace_id] = fresh
        return fresh

    def _build_index(self, workspace_id: str) -> _Index:
        # ``root`` ya valida que el workspace exista; ``resolve`` más abajo
        # es la misma validación de límites que usa el resto del IDE, no una
        # reimplementación propia.
        root = self.workspaces.root(workspace_id)
        raw_files = self._list_raw_files(root)
        truncated = len(raw_files) > self.max_indexed_files
        candidates = raw_files[: self.max_indexed_files]

        files: list[str] = []
        for path in candidates:
            try:
                self.workspaces.resolve(workspace_id, path)
            except IDEWorkspaceError:
                # Un symlink que apunta fuera del workspace (u otra ruta que
                # se sale de los límites autorizados) simplemente no entra al
                # índice; no es un error de la búsqueda.
                continue
            files.append(path)

        folders = self._derive_folders(files)
        symbols = self._extract_symbols(root, files)
        return _Index(
            built_at=time.monotonic(),
            files=tuple(files),
            folders=tuple(folders),
            symbols=tuple(symbols),
            truncated=truncated,
        )

    # ------------------------------------------------------------------ #
    # Listado de archivos: git primero (respeta .gitignore gratis), manual
    # como respaldo.
    # ------------------------------------------------------------------ #

    def _list_raw_files(self, root: Path) -> list[str]:
        via_git = self._list_files_via_git(root)
        if via_git is not None:
            return via_git
        return self._list_files_via_walk(root)

    def _list_files_via_git(self, root: Path) -> list[str] | None:
        try:
            result = subprocess.run(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=root,
                capture_output=True,
                timeout=GIT_LS_FILES_TIMEOUT_SECONDS,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        raw_entries = result.stdout.decode("utf-8", errors="replace").split("\x00")
        files: list[str] = []
        for entry in raw_entries:
            if not entry:
                continue
            posix_path = PurePosixPath(entry)
            if ".." in posix_path.parts or posix_path.is_absolute():
                continue
            if posix_path.suffix.lower() in self.binary_extensions:
                continue
            if any(part in _IGNORED_DIRS for part in posix_path.parts):
                continue
            files.append(entry)
        return files

    def _list_files_via_walk(self, root: Path) -> list[str]:
        files: list[str] = []
        # Un archivo de más que el tope basta para saber que hay que marcar
        # "truncado"; no hace falta terminar de recorrer un repo gigante.
        collection_cap = self.max_indexed_files + 1
        for current_dir, subdirs, filenames in os.walk(root):
            subdirs[:] = sorted(
                name for name in subdirs if name not in _IGNORED_DIRS and not name.startswith(".")
            )
            for filename in sorted(filenames):
                if Path(filename).suffix.lower() in self.binary_extensions:
                    continue
                absolute = Path(current_dir) / filename
                try:
                    relative = absolute.relative_to(root)
                except ValueError:
                    continue
                files.append(relative.as_posix())
                if len(files) >= collection_cap:
                    return files
        return files

    @staticmethod
    def _derive_folders(files: list[str]) -> list[str]:
        folders: set[str] = set()
        for path in files:
            parts = path.split("/")[:-1]
            accumulated: list[str] = []
            for part in parts:
                accumulated.append(part)
                folders.add("/".join(accumulated))
        return sorted(folders)

    # ------------------------------------------------------------------ #
    # Símbolos (funciones/clases) por regex -- no es un parser real.
    # ------------------------------------------------------------------ #

    def _extract_symbols(self, root: Path, files: list[str]) -> list[tuple[str, str, str, int]]:
        symbols: list[tuple[str, str, str, int]] = []
        for path in files:
            if len(symbols) >= self.max_indexed_symbols:
                break
            suffix = Path(path).suffix.lower()
            if suffix not in SYMBOL_EXTENSIONS:
                continue
            absolute = root / path
            try:
                if absolute.stat().st_size > self.max_bytes_for_symbol_scan:
                    continue
                text = absolute.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            pattern = _PY_SYMBOL_RE if suffix == ".py" else _JS_SYMBOL_RE
            found_in_file = 0
            for line_number, line in enumerate(text.splitlines(), start=1):
                if found_in_file >= self.max_symbols_per_file:
                    break
                match = pattern.match(line)
                if not match:
                    continue
                groups = match.groupdict()
                class_name = groups.get("class_name")
                if class_name:
                    symbols.append((class_name, "class", path, line_number))
                    found_in_file += 1
                    continue
                function_name = groups.get("function_name") or groups.get("arrow_name")
                if function_name:
                    symbols.append((function_name, "function", path, line_number))
                    found_in_file += 1
        return symbols
