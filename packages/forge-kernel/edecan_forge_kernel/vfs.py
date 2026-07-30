"""Virtual File System (VFS) y Workspaces — Fase C de Forge.

Proporciona la abstracción de sistema de archivos virtual por la que pasa TODA lectura y escritura
de los agentes. Ofrece:
1. `Vfs` / `VfsTxn`: lectura consistente por snapshot, escritura transaccional con detección de
   conflictos de concurrencia (write-write y read-write).
2. `workspace.fork()`: copy-on-write ultra-rápido en < 200 ms para 200.000 ficheros basado en
   punteros del CAS.
3. `ExecWindow`: ventana de ejecución exclusiva con reconciliación sin pérdida de datos frente a
   procesos externos de compilación/construcción.
4. `TextEdit` anclado: ediciones resistentes a desplazamientos de líneas.
5. `subtree_hash()`: cálculo determinista del hash de subárbol respetando ignorados (.gitignore).
6. Clasificador de archivos y detección de secretos (`secret_candidate`).
7. Sanitización estricta de rutas (prevención de `..`, escapes por symlinks, normalización NFC/NFD).
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from edecan_forge_kernel.cas import Cas
from edecan_forge_kernel.contracts import CasRef

# --------------------------------------------------------------------------- #
# Excepciones tipadas
# --------------------------------------------------------------------------- #


class VfsError(Exception):
    """Excepción base para operaciones del VFS."""


class PathTraversalError(VfsError):
    """Intento de acceso o escritura fuera de la raíz del workspace (`..`, symlink fuera, etc.)."""


class VfsConflictError(VfsError):
    """Conflicto de concurrencia en la escritura de VfsTxn (write-write o read-write)."""


class ExecWindowReconciliationError(VfsError):
    """Error al reconciliar modificaciones externas durante una ExecWindow."""


# --------------------------------------------------------------------------- #
# Clasificación de Archivos y Detección de Secretos
# --------------------------------------------------------------------------- #

_SECRET_PATTERNS = [
    re.compile(
        r"(?i)(api[_-]?key|secret[_-]?key|auth[_-]?token|access[_-]?token)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{16,})['\"]?"
    ),
    re.compile(r"-----BEGIN (RSA|EC|OPENSSH|PRIVATE) KEY-----"),
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),  # GitHub PAT
    re.compile(r"sk-[a-zA-Z0-9]{32,}"),  # OpenAI key
]

_GENERATED_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".turbo",
    "target",
}


class FileClassification(BaseModel, frozen=True):
    kind: Literal["code", "text", "binary", "generated"]
    is_secret_candidate: bool = False
    encoding: str = "utf-8"
    size_bytes: int = 0


def classify_file(path: str, content: bytes) -> FileClassification:
    """Clasifica un archivo según su tipo y analiza si contiene secretos candidatos."""
    parts = Path(path).parts
    if any(p in _GENERATED_DIRS for p in parts):
        kind: Literal["code", "text", "binary", "generated"] = "generated"
    else:
        # Detectar binario comprobando presencia de null bytes en los primeros 8KB
        sample = content[:8192]
        if b"\x00" in sample:
            kind = "binary"
        else:
            ext = Path(path).suffix.lower()
            if ext in {
                ".py",
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
                ".go",
                ".rs",
                ".c",
                ".cpp",
                ".h",
                ".java",
                ".sh",
                ".sql",
                ".yaml",
                ".yml",
                ".json",
            }:
                kind = "code"
            else:
                kind = "text"

    # Verificación de secretos candidatos
    is_secret = False
    if kind != "binary":
        try:
            text_str = content.decode("utf-8", errors="ignore")
            for pattern in _SECRET_PATTERNS:
                if pattern.search(text_str):
                    is_secret = True
                    break
        except Exception:
            pass

    return FileClassification(
        kind=kind,
        is_secret_candidate=is_secret,
        size_bytes=len(content),
    )


# --------------------------------------------------------------------------- #
# Normalización de Rutas y Sanitización
# --------------------------------------------------------------------------- #


def normalize_vfs_path(path: str) -> str:
    """Normaliza la ruta a formato relativo Unix NFC.

    Lanza PathTraversalError si la ruta intenta escapar (`..` o absoluta).
    """
    if not path or path == ".":
        return ""

    # Normalización NFC para macOS (macOS usa NFD por defecto)
    normalized = unicodedata.normalize("NFC", path)

    # Convertir separadores Windows a Unix
    normalized = normalized.replace("\\", "/")

    # Verificar rutas absolutas o escapes
    if normalized.startswith("/") or normalized.startswith("~"):
        raise PathTraversalError(f"Ruta absoluta no permitida en VFS: {path!r}")

    parts = [p for p in normalized.split("/") if p and p != "."]
    cleaned_parts: list[str] = []
    for part in parts:
        if part == "..":
            if not cleaned_parts:
                raise PathTraversalError(f"Intento de path traversal fuera de la raíz: {path!r}")
            cleaned_parts.pop()
        else:
            cleaned_parts.append(part)

    return "/".join(cleaned_parts)


# --------------------------------------------------------------------------- #
# Nodos Inmutables del VFS (Basados en CAS)
# --------------------------------------------------------------------------- #


class VfsFileEntry(BaseModel, frozen=True):
    cas_ref: CasRef
    size_bytes: int
    executable: bool = False
    classification: FileClassification


class VfsDirEntry(BaseModel, frozen=True):
    entries: dict[str, VfsFileEntry | VfsDirEntry] = Field(default_factory=dict)

    def get_path(self, norm_path: str) -> VfsFileEntry | VfsDirEntry | None:
        if not norm_path:
            return self
        parts = norm_path.split("/")
        curr: VfsFileEntry | VfsDirEntry = self
        for part in parts:
            if not isinstance(curr, VfsDirEntry):
                return None
            if part not in curr.entries:
                return None
            curr = curr.entries[part]
        return curr

    def with_file(self, norm_path: str, file_entry: VfsFileEntry) -> VfsDirEntry:
        parts = norm_path.split("/")
        if not parts or not parts[0]:
            raise ValueError("Ruta vacía")

        head = parts[0]
        if len(parts) == 1:
            new_entries = dict(self.entries)
            new_entries[head] = file_entry
            return VfsDirEntry(entries=new_entries)

        child = self.entries.get(head)
        if child is None or isinstance(child, VfsFileEntry):
            child_dir = VfsDirEntry()
        else:
            child_dir = child

        updated_child = child_dir.with_file("/".join(parts[1:]), file_entry)
        new_entries = dict(self.entries)
        new_entries[head] = updated_child
        return VfsDirEntry(entries=new_entries)

    def without_path(self, norm_path: str) -> VfsDirEntry:
        parts = norm_path.split("/")
        if not parts or not parts[0]:
            return self
        head = parts[0]
        if head not in self.entries:
            return self

        if len(parts) == 1:
            new_entries = dict(self.entries)
            del new_entries[head]
            return VfsDirEntry(entries=new_entries)

        child = self.entries[head]
        if isinstance(child, VfsFileEntry):
            return self

        updated_child = child.without_path("/".join(parts[1:]))
        new_entries = dict(self.entries)
        if not updated_child.entries:
            del new_entries[head]
        else:
            new_entries[head] = updated_child
        return VfsDirEntry(entries=new_entries)

    def list_files(self, prefix: str = "") -> list[tuple[str, VfsFileEntry]]:
        res: list[tuple[str, VfsFileEntry]] = []
        for name, entry in sorted(self.entries.items()):
            curr_path = f"{prefix}/{name}" if prefix else name
            if isinstance(entry, VfsFileEntry):
                res.append((curr_path, entry))
            else:
                res.extend(entry.list_files(curr_path))
        return res


# --------------------------------------------------------------------------- #
# Reglas de Ignorado (.gitignore) y Hashing de Subárbol
# --------------------------------------------------------------------------- #


class IgnoreRules:
    """Maneja reglas de ignorado simples estilo .gitignore."""

    def __init__(self, patterns: Sequence[str] = ()):
        self.patterns = list(patterns) + [".git", ".git/*"]

    def is_ignored(self, norm_path: str) -> bool:
        if not norm_path:
            return False
        parts = norm_path.split("/")
        for part in parts:
            if part in _GENERATED_DIRS or part == ".git":
                return True
            for pat in self.patterns:
                if fnmatch.fnmatch(part, pat) or fnmatch.fnmatch(norm_path, pat):
                    return True
        return False


def calculate_subtree_hash(tree: VfsDirEntry, ignore_rules: IgnoreRules | None = None) -> str:
    """Calcula un hash determinista del subárbol ignorando archivos excluidos.

    Sirve para la detección de agentes atascados en la fase G.
    """
    if ignore_rules is None:
        ignore_rules = IgnoreRules()

    h = hashlib.blake2b(digest_size=32)
    files = tree.list_files()
    for rel_path, entry in files:
        if ignore_rules.is_ignored(rel_path):
            continue
        h.update(rel_path.encode("utf-8"))
        h.update(b"\x00")
        h.update(entry.cas_ref.digest.encode("utf-8"))
        h.update(b"\x00")
    return f"b2b:{h.hexdigest()}"


# --------------------------------------------------------------------------- #
# Edición de Texto Anclada (TextEdit)
# --------------------------------------------------------------------------- #


class TextEdit(BaseModel, frozen=True):
    """Representa una edición de texto anclada."""

    target_content: str
    replacement_content: str
    start_line_hint: int | None = None

    def apply(self, source_text: str) -> str:
        """Aplica la sustitución de forma tolerante a desplazamientos."""
        if self.target_content in source_text:
            return source_text.replace(self.target_content, self.replacement_content, 1)

        # Intento con strip de cada línea
        target_lines = [line.strip() for line in self.target_content.splitlines() if line.strip()]
        if not target_lines:
            raise ValueError("Target content está vacío o solo contiene espacios")

        lines = source_text.splitlines()
        for i in range(len(lines) - len(target_lines) + 1):
            window = [lines[i + j].strip() for j in range(len(target_lines))]
            if window == target_lines:
                # Coincidencia aproximada encontrada en las líneas [i:i+len(target_lines)]
                before = "\n".join(lines[:i])
                after = "\n".join(lines[i + len(target_lines) :])
                res = self.replacement_content
                if before:
                    res = before + "\n" + res
                if after:
                    res = res + "\n" + after
                return res

        raise ValueError(
            f"No se pudo anclar la edición de texto para: {self.target_content[:30]!r}"
        )


# --------------------------------------------------------------------------- #
# VfsTxn — Transacción de Escritura en VFS
# --------------------------------------------------------------------------- #


class VfsTxn:
    """Transacción de escritura con verificación de lecturas y escrituras previas."""

    def __init__(self, vfs: Vfs):
        self.vfs = vfs
        self.base_version = vfs.version
        self.base_tree = vfs.root_tree
        self.staged_tree = vfs.root_tree
        self.read_paths: set[str] = set()
        self.written_paths: set[str] = set()

    def read_bytes(self, path: str) -> bytes:
        norm_path = normalize_vfs_path(path)
        self.read_paths.add(norm_path)
        entry = self.staged_tree.get_path(norm_path)
        if entry is None or isinstance(entry, VfsDirEntry):
            raise FileNotFoundError(f"Archivo no encontrado en VFS: {path!r}")
        return self.vfs.cas.obtener(entry.cas_ref)

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        data = self.read_bytes(path)
        return data.decode(encoding)

    def write_bytes(self, path: str, content: bytes, executable: bool = False) -> VfsTxn:
        norm_path = normalize_vfs_path(path)
        self.written_paths.add(norm_path)
        cas_ref = self.vfs.cas.poner(content)
        classification = classify_file(norm_path, content)
        entry = VfsFileEntry(
            cas_ref=cas_ref,
            size_bytes=len(content),
            executable=executable,
            classification=classification,
        )
        self.staged_tree = self.staged_tree.with_file(norm_path, entry)
        return self

    def write_text(self, path: str, text: str, encoding: str = "utf-8") -> VfsTxn:
        return self.write_bytes(path, text.encode(encoding))

    def remove(self, path: str) -> VfsTxn:
        norm_path = normalize_vfs_path(path)
        self.written_paths.add(norm_path)
        self.staged_tree = self.staged_tree.without_path(norm_path)
        return self

    def apply_text_edit(self, path: str, edit: TextEdit) -> VfsTxn:
        text = self.read_text(path)
        new_text = edit.apply(text)
        return self.write_text(path, new_text)

    def commit(self) -> int:
        """Intenta realizar commit de la transacción en el VFS.

        Verifica si hubo conflictos con escrituras concurrentes desde `base_version`.
        """
        return self.vfs._commit_txn(self)


# --------------------------------------------------------------------------- #
# Vfs — Sistema de Archivos Virtual Central
# --------------------------------------------------------------------------- #


class Vfs:
    """Representa un árbol VFS con soporte para versión, snapshots y forking Copy-on-Write."""

    def __init__(self, cas: Cas, root_tree: VfsDirEntry | None = None, version: int = 1):
        self.cas = cas
        self.root_tree = root_tree or VfsDirEntry()
        self.version = version

    def begin_txn(self) -> VfsTxn:
        return VfsTxn(self)

    def read_bytes(self, path: str) -> bytes:
        norm_path = normalize_vfs_path(path)
        entry = self.root_tree.get_path(norm_path)
        if entry is None or isinstance(entry, VfsDirEntry):
            raise FileNotFoundError(f"Archivo no encontrado en VFS: {path!r}")
        return self.cas.obtener(entry.cas_ref)

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(path).decode(encoding)

    def exists(self, path: str) -> bool:
        norm_path = normalize_vfs_path(path)
        return self.root_tree.get_path(norm_path) is not None

    def list_files(self) -> list[tuple[str, VfsFileEntry]]:
        return self.root_tree.list_files()

    def fork(self) -> Vfs:
        """Crea una bifurcación Copy-on-Write del VFS de forma instantánea (<200 ms)."""
        return Vfs(cas=self.cas, root_tree=self.root_tree, version=self.version)

    def subtree_hash(self, ignore_rules: IgnoreRules | None = None) -> str:
        return calculate_subtree_hash(self.root_tree, ignore_rules)

    def populate_from_real_dir(
        self, real_dir: Path, ignore_rules: IgnoreRules | None = None
    ) -> None:
        """Carga iterativamente un directorio real en el VFS."""
        if ignore_rules is None:
            ignore_rules = IgnoreRules()

        txn = self.begin_txn()
        real_dir = real_dir.resolve()
        for root, dirs, files in os.walk(real_dir):
            rel_root = os.path.relpath(root, real_dir)
            if rel_root == ".":
                rel_root = ""

            # Filtrar directorios ignorados
            dirs[:] = [d for d in dirs if not ignore_rules.is_ignored(f"{rel_root}/{d}".strip("/"))]

            for file_name in files:
                rel_path = f"{rel_root}/{file_name}".strip("/")
                if ignore_rules.is_ignored(rel_path):
                    continue

                abs_file_path = Path(root) / file_name
                # Evitar symlinks que se escapan del directorio raíz
                try:
                    canonical_file = abs_file_path.resolve()
                    canonical_file.relative_to(real_dir)
                except ValueError:
                    # Symlink saliendo de la raíz -> omitir
                    continue

                try:
                    stat = abs_file_path.stat()
                    executable = bool(stat.st_mode & 0o111)
                    content = abs_file_path.read_bytes()
                    txn.write_bytes(rel_path, content, executable=executable)
                except Exception:
                    pass

        txn.commit()

    def _commit_txn(self, txn: VfsTxn) -> int:
        if txn.base_version != self.version:
            # Conflicto de versiones: verificar si las rutas modificadas chocan
            msg = (
                f"Conflicto de concurrencia: VFS cambió de versión "
                f"{txn.base_version} a {self.version}"
            )
            raise VfsConflictError(msg)

        self.root_tree = txn.staged_tree
        self.version += 1
        return self.version


# --------------------------------------------------------------------------- #
# ExecWindow — Ventana de Ejecución Exclusiva con Reconciliación
# --------------------------------------------------------------------------- #


class ExecWindow:
    """Ventana de ejecución exclusiva sobre disco real con reconciliación sin pérdida.

    Escribe los archivos del VFS a un directorio real temporal, permite la ejecución de un
    comando externo y luego reconcilia los cambios externos sin borrar las ediciones del agente.
    """

    def __init__(self, vfs: Vfs, target_dir: Path):
        self.vfs = vfs
        self.target_dir = target_dir.resolve()

    def sync_vfs_to_disk(self) -> None:
        """Vuelca todo el árbol VFS al directorio de ejecución."""
        self.target_dir.mkdir(parents=True, exist_ok=True)
        for rel_path, entry in self.vfs.list_files():
            dest = self.target_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = self.vfs.cas.obtener(entry.cas_ref)
            dest.write_bytes(content)
            if entry.executable:
                dest.chmod(dest.stat().st_mode | 0o111)

    def reconcile_from_disk(self) -> None:
        """Reconcilia los cambios del disco real hacia el VFS.

        Preserva las escrituras del VFS y absorbe los nuevos artefactos creados por builds externos.
        """
        txn = self.vfs.begin_txn()
        ignore_rules = IgnoreRules()

        for root, dirs, files in os.walk(self.target_dir):
            rel_root = os.path.relpath(root, self.target_dir)
            if rel_root == ".":
                rel_root = ""

            dirs[:] = [d for d in dirs if not ignore_rules.is_ignored(f"{rel_root}/{d}".strip("/"))]

            for file_name in files:
                rel_path = f"{rel_root}/{file_name}".strip("/")
                if ignore_rules.is_ignored(rel_path):
                    continue

                abs_file_path = Path(root) / file_name
                content = abs_file_path.read_bytes()

                # Si el VFS ya tiene el archivo y coincide, no hace falta reescribir
                if self.vfs.exists(rel_path) and self.vfs.read_bytes(rel_path) == content:
                    continue

                stat = abs_file_path.stat()
                executable = bool(stat.st_mode & 0o111)
                txn.write_bytes(rel_path, content, executable=executable)

        txn.commit()
