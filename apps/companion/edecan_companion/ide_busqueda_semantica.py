"""Búsqueda semántica del código de un workspace (T2.2).

Hoy ``ide_files.FileService.search`` busca por texto literal: encuentra
``"login"`` pero no ``"dónde se valida la contraseña"`` si esas palabras no
aparecen tal cual en el archivo. Este módulo agrega un índice por
*significado* -- se parte cada archivo en fragmentos (``chunks`` de líneas
contiguas), se calcula un vector por fragmento, y una consulta se resuelve
por similitud de esos vectores en vez de coincidencia de subcadena.

Reuso deliberado -- y un límite arquitectónico real que se descubrió al
buscar "cómo lo hace Edecán hoy" (el encargo explícito de este ticket)
--
------------------------------------------------------------------------
``ARCHITECTURE.md`` §10.2/§10.3 documenta que la memoria del asistente ya
usa pgvector (``packages/db``, tabla ``memory_items``/``file_chunks``,
columna ``embedding vector(1536)``) y que el importador local
(``apps/local/edecan_local/private_assistant_import.py``) genera esos
vectores con ``edecan_core.memory.embedders.HashEmbedder`` -- un embedder
100% offline y determinista (SHA-256 por token, sin red ni clave de API).
Ese es el camino que este módulo reusa para la parte que importa de verdad:
CÓMO convertir texto en un vector comparable, en vez de inventar un segundo
esquema de embeddings.

Lo que este módulo NO puede hacer es guardar esos vectores en la misma
tabla Postgres que ``memory_items``/``file_chunks``: ``apps/companion``
está construido a propósito SIN dependencia de ``edecan_db``/SQLAlchemy/
``asyncpg`` (ver ``pyproject.toml`` de ese paquete -- es el único paquete
pensado para instalarse solo, en la máquina de la persona usuaria, sin el
servidor). No hay sesión de base de datos que pedir prestada acá. Por eso
el índice de ESTE módulo se persiste como un JSON local por workspace
(mismo patrón atómico que ``ide_workspaces.WorkspaceStore``), y el import
de ``HashEmbedder`` es SUAVE (``try/except``): en ``edecan-local`` (que sí
empaqueta ``edecan-core`` junto a ``edecan-companion``) la búsqueda
semántica funciona; en una instalación de solo ``edecan-companion`` el
import falla y este módulo degrada a ``FileService.search`` (la búsqueda
por texto que YA existe) en vez de fallar -- exactamente el pedido del
ticket.

Por la misma razón la dimensión del vector NO es
``edecan_core.memory.embedders.DEFAULT_EMBEDDINGS_DIM`` (1536, pensada para
UN vector por recuerdo): acá hay potencialmente miles de fragmentos por
workspace que hay que tener cargados en memoria para rankear una consulta
(no hay ``ORDER BY embedding <=> :q`` de pgvector resolviéndolo en el
servidor), así que se usa una dimensión más chica (``DEFAULT_EMBEDDING_DIM``
128) que ``HashEmbedder`` acepta igual de bien -- sigue siendo el mismo
algoritmo, solo con menos cubetas.

Límite de calidad honesto sobre ``HashEmbedder``
--------------------------------------------------
Reusar ``HashEmbedder`` (en vez de inventar otro esquema) es lo pedido, pero
hay que ser directo sobre lo que da: es feature-hashing SHA-256 por token,
sin ningún modelo de sinonimia -- premia solapamiento LITERAL de palabras
entre consulta y fragmento, no relación de significado real, y ese
solapamiento además queda algo ruidoso porque el signo de cada cubeta lo
decide la paridad del hash (dos textos con las mismas palabras pueden
cancelarse en vez de reforzarse). Medido a mano: la consulta "credenciales
invalidas" contra un fragmento que contiene ESA MISMA frase textual no
siempre queda primera en el ranking frente a un fragmento sin relación
alguna. Es el mismo embedder que ya usa ``memory_items`` como *fallback*
cuando no hay proveedor de embeddings configurado (ver su propio docstring:
"self-host sin proveedor... configurado") -- nunca se documentó ahí como
sustituto de un embedding real, y acá tampoco lo es. Lo que este módulo
aporta con calidad real es el PIPELINE (fragmentado, indexado incremental
en segundo plano, degradado a texto) -- está armado para aceptar cualquier
``Embedder`` mejor (p. ej. ``OpenAICompatEmbedder`` contra un proveedor de
verdad) por inyección de dependencia (parámetro ``embedder`` del
constructor) sin tocar una sola línea de esta lógica.

Indexar sin bloquear el IDE
----------------------------
Construir el índice de un repo grande (miles de archivos) tarda; por eso
``start_indexing`` lo hace en un hilo de fondo (``threading.Thread``,
mismo patrón de ``ReferenceService`` para no repetir recorridos en cada
tecla) y ``search`` siempre puede responder de inmediato leyendo el índice
que ya esté persistido en disco (o degradando a texto si todavía no hay
ninguno). La escritura del índice es atómica (archivo temporal +
``os.replace``, igual que ``WorkspaceStore._save``), así que un lector
nunca ve un JSON a medio escribir.

Reindexar solo lo que cambió: por archivo se guarda ``size`` + ``mtime_ns``
del momento en que se indexó; en la siguiente pasada, un archivo cuyo
tamaño y fecha de modificación no cambiaron se salta por completo (no se
vuelve a leer ni a re-embeber). Los archivos borrados se detectan porque ya
no aparecen en el recorrido actual y sus fragmentos se descartan.

Respeta ``.gitignore``: la lista de archivos indexables sale de
``git ls-files -z --cached --others --exclude-standard`` (que ya excluye lo
ignorado) igual que ``ide_referencias.ReferenceService``; sin repositorio
Git disponible cae a un recorrido manual que al menos excluye las carpetas
ruidosas conocidas (``ide_files._IGNORED``).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from edecan_companion.ide_files import _IGNORED as _IGNORED_DIRS
from edecan_companion.ide_files import FileService
from edecan_companion.ide_referencias import BINARY_EXTENSIONS
from edecan_companion.ide_workspaces import IDEWorkspaceError, WorkspaceStore

# Import suave del embedder del servidor -- ver docstring del módulo. Solo se
# usa la clase; nunca se importa ``edecan_db``/SQLAlchemy desde acá.
try:
    from edecan_core.memory.embedders import HashEmbedder as _HashEmbedder
except Exception:  # noqa: BLE001 - cualquier fallo de import cuenta como "no disponible"
    _HashEmbedder = None  # type: ignore[assignment]


class Embedder(Protocol):
    """Misma forma que ``edecan_core.memory.base.Embedder``: no se importa
    ese ``Protocol`` (implicaría la misma dependencia que se evita arriba),
    se repite la firma mínima que este módulo necesita."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


_UNSET: Any = object()
"""Distingue "no me pasaron ``embedder``, autodetectá" de "me pasaron
``embedder=None`` a propósito" (para forzar el modo degradado en tests o en
un entorno donde se sabe que no hay embeddings)."""

DEFAULT_EMBEDDING_DIM = 128
"""Deliberadamente menor que ``DEFAULT_EMBEDDINGS_DIM`` del servidor (1536)
-- ver docstring del módulo: acá se comparan miles de vectores en memoria
Python pura por consulta, no uno solo vía pgvector."""

DEFAULT_CHUNK_LINES = 60
DEFAULT_CHUNK_OVERLAP_LINES = 12
DEFAULT_K = 10
MAX_K = 50
MAX_INDEXABLE_FILES = 5_000
MAX_FILE_BYTES_FOR_INDEX = 512 * 1024
MAX_TOTAL_CHUNKS = 20_000
MAX_EXCERPT_CHARS = 600
GIT_LS_FILES_TIMEOUT_SECONDS = 5
INDEX_FORMAT_VERSION = 1

_INDEX_SUBDIR = "search-index"


def embeddings_available() -> bool:
    """``True`` si ``HashEmbedder`` se pudo importar en este proceso."""
    return _HashEmbedder is not None


class IDESemanticSearchError(ValueError):
    """Workspace, consulta o parámetro inválido."""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------- #
# Listado de archivos indexables -- mismo criterio que
# ``ide_referencias.ReferenceService`` (git primero, respeta .gitignore
# gratis; recorrido manual como respaldo).
# --------------------------------------------------------------------- #


def _list_indexable_files(root: Path) -> tuple[list[str], bool]:
    via_git = _list_files_via_git(root)
    files = via_git if via_git is not None else _list_files_via_walk(root)
    truncated = len(files) > MAX_INDEXABLE_FILES
    return files[:MAX_INDEXABLE_FILES], truncated


def _list_files_via_git(root: Path) -> list[str] | None:
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
    files: list[str] = []
    for entry in result.stdout.decode("utf-8", errors="replace").split("\x00"):
        if not entry:
            continue
        posix_path = PurePosixPath(entry)
        if ".." in posix_path.parts or posix_path.is_absolute():
            continue
        if posix_path.suffix.lower() in BINARY_EXTENSIONS:
            continue
        if any(part in _IGNORED_DIRS for part in posix_path.parts):
            continue
        files.append(entry)
    return sorted(files)


def _list_files_via_walk(root: Path) -> list[str]:
    files: list[str] = []
    collection_cap = MAX_INDEXABLE_FILES + 1
    for current_dir, subdirs, filenames in os.walk(root):
        subdirs[:] = sorted(
            name for name in subdirs if name not in _IGNORED_DIRS and not name.startswith(".")
        )
        for filename in sorted(filenames):
            if Path(filename).suffix.lower() in BINARY_EXTENSIONS:
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


# --------------------------------------------------------------------- #
# Fragmentado en ventanas de líneas con solapamiento.
# --------------------------------------------------------------------- #


def _chunk_lines(
    lines: list[str], *, chunk_lines: int, overlap_lines: int
) -> list[tuple[int, int, str]]:
    """Parte ``lines`` en ventanas ``[start_line, end_line]`` (1-indexadas,
    inclusive) de a lo sumo ``chunk_lines`` líneas, solapando ``overlap_lines``
    entre una ventana y la siguiente -- así una función que cruza el borde de
    una ventana igual queda completa dentro de la ventana vecina."""
    total = len(lines)
    if total == 0:
        return []
    step = max(chunk_lines - overlap_lines, 1)
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < total:
        end = min(start + chunk_lines, total)
        text = "\n".join(lines[start:end])
        if text.strip():
            chunks.append((start + 1, end, text))
        if end >= total:
            break
        start += step
    return chunks


def _cosine(a: list[float], b: list[float]) -> float:
    """Producto punto -- ``HashEmbedder`` ya normaliza a norma L2 = 1, así
    que el producto punto y la similitud coseno son el mismo número."""
    return sum(x * y for x, y in zip(a, b, strict=True))


def _run_embed(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    """Ejecuta ``embedder.embed`` (async pero sin I/O real en
    ``HashEmbedder``) desde código síncrono.

    Seguro de llamar acá porque ``ide_runtime.IDERuntime.dispatch`` -- el
    único llamador real de los servicios del IDE -- ya corre en un hilo
    aparte vía ``asyncio.to_thread`` (ver ``ide_runtime.py``), así que este
    hilo nunca tiene un loop de asyncio propio corriendo todavía.
    """
    return asyncio.run(embedder.embed(texts))


@dataclass(frozen=True)
class _BuildResult:
    indexed_files: int
    reused_files: int
    removed_files: int
    total_chunks: int
    truncated: bool


class SemanticSearchService:
    """Índice semántico por workspace, con degradado a texto plano.

    ``embedder``: dejar sin pasar (autodetecta ``HashEmbedder`` si el
    import de arriba tuvo éxito); pasar ``None`` explícitamente fuerza el
    modo degradado (útil en tests, o si se sabe que este proceso no tiene
    ``edecan-core`` instalado aunque el import haya colado por casualidad).
    """

    def __init__(
        self,
        workspaces: WorkspaceStore,
        state_dir: Path,
        *,
        file_service: FileService | None = None,
        embedder: Embedder | None = _UNSET,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
        chunk_lines: int = DEFAULT_CHUNK_LINES,
        chunk_overlap_lines: int = DEFAULT_CHUNK_OVERLAP_LINES,
        max_file_bytes_for_index: int = MAX_FILE_BYTES_FOR_INDEX,
        max_total_chunks: int = MAX_TOTAL_CHUNKS,
    ) -> None:
        if chunk_overlap_lines >= chunk_lines:
            raise IDESemanticSearchError("chunk_overlap_lines debe ser menor que chunk_lines.")
        self.workspaces = workspaces
        self.file_service = file_service or FileService(workspaces)
        self.embedding_dim = embedding_dim
        self.chunk_lines = chunk_lines
        self.chunk_overlap_lines = chunk_overlap_lines
        self.max_file_bytes_for_index = max_file_bytes_for_index
        self.max_total_chunks = max_total_chunks
        self._index_dir = Path(state_dir) / _INDEX_SUBDIR

        if embedder is _UNSET:
            self._embedder = _HashEmbedder(dim=embedding_dim) if _HashEmbedder is not None else None
        else:
            self._embedder = embedder

        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._last_status: dict[str, dict[str, Any]] = {}

    @property
    def embeddings_available(self) -> bool:
        return self._embedder is not None

    # ------------------------------------------------------------------ #
    # Estado
    # ------------------------------------------------------------------ #

    def status(self, workspace_id: str) -> dict[str, Any]:
        with self._lock:
            thread = self._threads.get(workspace_id)
            building = thread is not None and thread.is_alive()
            last = self._last_status.get(workspace_id)
        on_disk = self._read_index_file(workspace_id)
        return {
            "workspace_id": workspace_id,
            "embeddings_available": self.embeddings_available,
            "state": "indexing" if building else ("ready" if on_disk else "sin_indexar"),
            "index_exists": on_disk is not None,
            "total_chunks": len(on_disk["chunks"]) if on_disk else 0,
            "total_files": len(on_disk["files"]) if on_disk else 0,
            "truncated": bool(on_disk["truncated"]) if on_disk else False,
            "built_at": on_disk.get("built_at") if on_disk else None,
            "last_run": last,
        }

    # ------------------------------------------------------------------ #
    # Indexado
    # ------------------------------------------------------------------ #

    def start_indexing(self, workspace_id: str) -> dict[str, Any]:
        """Lanza (o reusa, si ya hay una corriendo) un reindexado en un hilo
        de fondo. Nunca bloquea -- devuelve el estado inmediatamente."""
        # Valida workspace_id temprano y en el hilo llamador: un id inválido
        # debe fallar de inmediato, no en silencio dentro del hilo de fondo.
        self.workspaces.root(workspace_id)
        with self._lock:
            existing = self._threads.get(workspace_id)
            if existing is not None and existing.is_alive():
                return {"started": False, **self.status(workspace_id)}
            thread = threading.Thread(
                target=self._build_and_record,
                args=(workspace_id,),
                daemon=True,
                name=f"edecan-ide-semantic-index-{workspace_id}",
            )
            self._threads[workspace_id] = thread
            thread.start()
        return {"started": True, **self.status(workspace_id)}

    def wait_for_index(self, workspace_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
        """Espera a que termine un indexado en curso (si lo hay) y devuelve
        el estado resultante. Pensado para tests y para un "indexar ahora y
        esperar" explícito -- el uso normal del IDE es ``start_indexing`` sin
        esperar nada."""
        with self._lock:
            thread = self._threads.get(workspace_id)
        if thread is not None:
            thread.join(timeout=timeout)
        return self.status(workspace_id)

    def build_index(self, workspace_id: str) -> dict[str, Any]:
        """Construye el índice de forma síncrona, en el hilo actual.

        Es lo que ``start_indexing`` corre en segundo plano; expuesto
        también en forma directa para pruebas deterministas y para
        integraciones que prefieran manejar su propio hilo/cola."""
        return self._build_and_record(workspace_id)

    def _build_and_record(self, workspace_id: str) -> dict[str, Any]:
        started_at = time.monotonic()
        try:
            result = self._build_index_now(workspace_id)
            record = {
                "ok": True,
                "at": _utc_now_iso(),
                "duration_seconds": round(time.monotonic() - started_at, 3),
                "indexed_files": result.indexed_files,
                "reused_files": result.reused_files,
                "removed_files": result.removed_files,
                "total_chunks": result.total_chunks,
                "truncated": result.truncated,
            }
        except IDEWorkspaceError as exc:
            record = {"ok": False, "at": _utc_now_iso(), "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - un fallo de indexado no debe tumbar el hilo
            record = {"ok": False, "at": _utc_now_iso(), "error": str(exc)}
        with self._lock:
            self._last_status[workspace_id] = record
        return self.status(workspace_id)

    def _build_index_now(self, workspace_id: str) -> _BuildResult:
        root = self.workspaces.root(workspace_id)
        relative_paths, files_truncated = _list_indexable_files(root)

        previous = self._read_index_file(workspace_id) or {"files": {}, "chunks": {}}
        previous_files: dict[str, Any] = previous.get("files", {})
        previous_chunks: dict[str, Any] = previous.get("chunks", {})

        new_files: dict[str, Any] = {}
        new_chunks: dict[str, Any] = {}
        indexed_files = 0
        reused_files = 0
        truncated = files_truncated

        for relative_path in relative_paths:
            if len(new_chunks) >= self.max_total_chunks:
                truncated = True
                break
            absolute = root / relative_path
            try:
                file_stat = absolute.stat()
            except OSError:
                continue
            if not absolute.is_file() or file_stat.st_size > self.max_file_bytes_for_index:
                continue

            previous_entry = previous_files.get(relative_path)
            unchanged = (
                previous_entry is not None
                and previous_entry.get("size") == file_stat.st_size
                and previous_entry.get("mtime_ns") == file_stat.st_mtime_ns
            )
            if unchanged:
                chunk_ids = previous_entry.get("chunk_ids", [])
                reused_files += 1
                new_files[relative_path] = previous_entry
                for chunk_id in chunk_ids:
                    chunk = previous_chunks.get(chunk_id)
                    if chunk is not None:
                        new_chunks[chunk_id] = chunk
                continue

            try:
                text = absolute.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            windows = _chunk_lines(
                text.splitlines(),
                chunk_lines=self.chunk_lines,
                overlap_lines=self.chunk_overlap_lines,
            )
            if not windows:
                new_files[relative_path] = {
                    "size": file_stat.st_size,
                    "mtime_ns": file_stat.st_mtime_ns,
                    "chunk_ids": [],
                }
                indexed_files += 1
                continue

            embeddings = (
                _run_embed(self._embedder, [chunk_text for _, _, chunk_text in windows])
                if self._embedder is not None
                else None
            )
            chunk_ids: list[str] = []
            for position, (start_line, end_line, chunk_text) in enumerate(windows):
                chunk_id = f"{relative_path}:{start_line}-{end_line}"
                chunk_ids.append(chunk_id)
                new_chunks[chunk_id] = {
                    "path": relative_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "excerpt": chunk_text[:MAX_EXCERPT_CHARS],
                    "embedding": [round(v, 6) for v in embeddings[position]]
                    if embeddings is not None
                    else None,
                }
                if len(new_chunks) >= self.max_total_chunks:
                    truncated = True
                    break
            new_files[relative_path] = {
                "size": file_stat.st_size,
                "mtime_ns": file_stat.st_mtime_ns,
                "chunk_ids": chunk_ids,
            }
            indexed_files += 1

        removed_files = len(set(previous_files) - set(new_files))
        payload = {
            "version": INDEX_FORMAT_VERSION,
            "dim": self.embedding_dim,
            "chunk_lines": self.chunk_lines,
            "chunk_overlap_lines": self.chunk_overlap_lines,
            "built_at": _utc_now_iso(),
            "truncated": truncated,
            "files": new_files,
            "chunks": new_chunks,
        }
        self._write_index_file(workspace_id, payload)
        return _BuildResult(
            indexed_files=indexed_files,
            reused_files=reused_files,
            removed_files=removed_files,
            total_chunks=len(new_chunks),
            truncated=truncated,
        )

    # ------------------------------------------------------------------ #
    # Búsqueda
    # ------------------------------------------------------------------ #

    def search(
        self,
        workspace_id: str,
        query: str,
        *,
        k: int = DEFAULT_K,
        auto_index: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise IDESemanticSearchError("query no puede estar vacío.")
        if not 1 <= k <= MAX_K:
            raise IDESemanticSearchError(f"k debe estar entre 1 y {MAX_K}.")
        # Valida que el workspace exista antes de decidir el modo.
        self.workspaces.root(workspace_id)

        if not self.embeddings_available:
            return self._text_fallback(
                workspace_id, query, k, reason="Embeddings no disponibles en este entorno."
            )

        index = self._read_index_file(workspace_id)
        if index is None:
            if auto_index:
                self.start_indexing(workspace_id)
            return self._text_fallback(
                workspace_id,
                query,
                k,
                reason=(
                    "Todavía no hay índice semántico para este workspace; "
                    "indexando en segundo plano."
                ),
            )

        [query_vector] = _run_embed(self._embedder, [query])
        scored: list[tuple[float, dict[str, Any]]] = []
        for chunk in index["chunks"].values():
            embedding = chunk.get("embedding")
            if not embedding:
                continue
            scored.append((_cosine(query_vector, embedding), chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        matches = [
            {
                "path": chunk["path"],
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "excerpt": chunk["excerpt"],
                "score": round(score, 6),
            }
            for score, chunk in scored[:k]
        ]
        return {
            "query": query,
            "mode": "semantico",
            "matches": matches,
            "total_indexed_chunks": len(index["chunks"]),
            "index_truncated": bool(index["truncated"]),
            "degraded_reason": None,
        }

    def _text_fallback(
        self, workspace_id: str, query: str, k: int, *, reason: str
    ) -> dict[str, Any]:
        raw = self.file_service.search(workspace_id, query)
        matches = [
            {
                "path": row["path"],
                "start_line": row["line"],
                "end_line": row["line"],
                "excerpt": row["text"],
                "score": None,
            }
            for row in raw["matches"][:k]
        ]
        return {
            "query": query,
            "mode": "texto",
            "matches": matches,
            "total_indexed_chunks": 0,
            "index_truncated": bool(raw.get("truncated", False)),
            "degraded_reason": reason,
        }

    def invalidate(self, workspace_id: str | None = None) -> None:
        """Borra el índice persistido -- fuerza una reconstrucción completa
        (no incremental) en el próximo ``start_indexing``/``build_index``."""
        if workspace_id is None:
            with self._lock:
                workspace_ids = list(self._last_status)
            for existing_id in workspace_ids:
                self.invalidate(existing_id)
            if self._index_dir.is_dir():
                for entry in self._index_dir.glob("*.json"):
                    entry.unlink(missing_ok=True)
            return
        with self._lock:
            self._last_status.pop(workspace_id, None)
        self._index_path(workspace_id).unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    # Persistencia -- mismo patrón atómico que ``WorkspaceStore._save``.
    # ------------------------------------------------------------------ #

    def _index_path(self, workspace_id: str) -> Path:
        # ``workspace_id`` es un ``uuid4`` generado por ``WorkspaceStore``
        # (nunca texto libre de la persona usuaria), así que es seguro usarlo
        # tal cual como nombre de archivo.
        return self._index_dir / f"{workspace_id}.json"

    def _read_index_file(self, workspace_id: str) -> dict[str, Any] | None:
        path = self._index_path(workspace_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or data.get("version") != INDEX_FORMAT_VERSION:
            return None
        return data

    def _write_index_file(self, workspace_id: str, payload: dict[str, Any]) -> None:
        self._index_dir.mkdir(parents=True, exist_ok=True)
        target = self._index_path(workspace_id)
        temp = target.with_name(f".{target.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, target)


__all__ = [
    "DEFAULT_CHUNK_LINES",
    "DEFAULT_CHUNK_OVERLAP_LINES",
    "DEFAULT_EMBEDDING_DIM",
    "DEFAULT_K",
    "IDESemanticSearchError",
    "MAX_FILE_BYTES_FOR_INDEX",
    "MAX_K",
    "MAX_TOTAL_CHUNKS",
    "SemanticSearchService",
    "embeddings_available",
]
