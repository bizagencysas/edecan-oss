"""Archivo/árbol/búsqueda relativos a un workspace autorizado."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from edecan_companion.ide_workspaces import WorkspaceStore

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TREE_ENTRIES = 2000
MAX_SEARCH_MATCHES = 500
_IGNORED = frozenset({".git", "node_modules", "__pycache__", ".venv", "dist", "build"})


class IDEFileError(ValueError):
    pass


class FileService:
    def __init__(self, workspaces: WorkspaceStore) -> None:
        self.workspaces = workspaces

    def tree(
        self,
        workspace_id: str,
        path: str = ".",
        *,
        max_depth: int = 4,
        max_entries: int = 500,
    ) -> dict[str, Any]:
        if not 1 <= max_depth <= 12:
            raise IDEFileError("max_depth debe estar entre 1 y 12.")
        if not 1 <= max_entries <= MAX_TREE_ENTRIES:
            raise IDEFileError(f"max_entries debe estar entre 1 y {MAX_TREE_ENTRIES}.")
        root = self.workspaces.root(workspace_id)
        start = self.workspaces.resolve(workspace_id, path)
        if not start.exists() or not start.is_dir():
            raise IDEFileError("La carpeta no existe.")
        count = 0
        truncated = False

        def walk(directory: Path, depth: int) -> list[dict[str, Any]]:
            nonlocal count, truncated
            rows: list[dict[str, Any]] = []
            if depth > max_depth:
                return rows
            try:
                entries = sorted(
                    directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
                )
            except OSError:
                return rows
            for entry in entries:
                if count >= max_entries:
                    truncated = True
                    break
                if entry.name in _IGNORED:
                    continue
                resolved = entry.resolve(strict=False)
                try:
                    relative = resolved.relative_to(root).as_posix()
                except ValueError:
                    continue
                count += 1
                is_dir = entry.is_dir()
                row: dict[str, Any] = {
                    "name": entry.name,
                    "path": relative,
                    "type": "directory" if is_dir else "file",
                    "is_dir": is_dir,
                }
                if not is_dir:
                    with contextlib.suppress(OSError):
                        row["size_bytes"] = entry.stat().st_size
                elif depth < max_depth:
                    row["children"] = walk(entry, depth + 1)
                rows.append(row)
            return rows

        import contextlib

        return {
            "path": start.relative_to(root).as_posix() or ".",
            "entries": walk(start, 1),
            "truncated": truncated,
        }

    def read(self, workspace_id: str, path: str) -> dict[str, Any]:
        target = self.workspaces.resolve(workspace_id, path)
        if not target.is_file():
            raise IDEFileError("El archivo no existe.")
        size = target.stat().st_size
        if size > MAX_FILE_BYTES:
            raise IDEFileError("El archivo supera el límite de 4 MB.")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise IDEFileError("El archivo no es texto UTF-8.") from exc
        return {
            "path": path,
            "content": content,
            "encoding": "utf-8",
            "size_bytes": size,
        }

    def write(self, workspace_id: str, path: str, content: str) -> dict[str, Any]:
        if not isinstance(content, str):
            raise IDEFileError("content debe ser texto.")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise IDEFileError("El contenido supera el límite de 4 MB.")
        target = self.workspaces.resolve(workspace_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Revalidar después de crear padres por si una carrera introdujo un symlink.
        target = self.workspaces.resolve(workspace_id, path)
        temp = target.with_name(
            f".{target.name}.edecan-tmp-{os.getpid()}-{uuid.uuid4().hex}"
        )
        try:
            temp.write_bytes(encoded)
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        return {"path": path, "bytes_written": len(encoded)}

    def edit(
        self,
        workspace_id: str,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        if not old_string:
            raise IDEFileError("old_string no puede estar vacío.")
        current = self.read(workspace_id, path)["content"]
        occurrences = current.count(old_string)
        if occurrences == 0:
            raise IDEFileError("old_string no se encontró en el archivo.")
        if occurrences > 1 and not replace_all:
            raise IDEFileError("old_string aparece más de una vez; usa replace_all.")
        replacements = occurrences if replace_all else 1
        updated = current.replace(old_string, new_string, -1 if replace_all else 1)
        written = self.write(workspace_id, path, updated)
        return {**written, "replacements": replacements}

    def search(
        self, workspace_id: str, query: str, path: str = "."
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query:
            raise IDEFileError("query no puede estar vacío.")
        root = self.workspaces.root(workspace_id)
        start = self.workspaces.resolve(workspace_id, path)
        if not start.exists():
            raise IDEFileError("La ruta de búsqueda no existe.")
        candidates = [start] if start.is_file() else start.rglob("*")
        matches: list[dict[str, Any]] = []
        truncated = False
        for candidate in candidates:
            if any(part in _IGNORED for part in candidate.parts):
                continue
            if not candidate.is_file():
                continue
            try:
                candidate.resolve(strict=True).relative_to(root)
            except (OSError, ValueError):
                continue
            try:
                if candidate.stat().st_size > MAX_FILE_BYTES:
                    continue
                lines = candidate.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(lines, 1):
                if query.lower() not in line.lower():
                    continue
                excerpt = line[:500]
                matches.append(
                    {
                        "path": candidate.resolve().relative_to(root).as_posix(),
                        "line": line_number,
                        "text": excerpt,
                        "texto": excerpt,
                    }
                )
                if len(matches) >= MAX_SEARCH_MATCHES:
                    truncated = True
                    break
            if truncated:
                break
        return {"query": query, "matches": matches, "truncated": truncated}
