"""Puntos de control (deshacer) del workspace para el agente del IDE.

El problema que resuelve: el agente edita archivos del repo del usuario. Si
se equivoca, hoy no hay vuelta atrás salvo ``git`` — y no todo el mundo
commitea antes de pedirle algo a una IA. Sin red de seguridad, el usuario no
suelta tareas grandes. Este módulo es esa red: antes de que el agente toque
un archivo, se guarda su estado ("antes"); después se puede volver atrás,
todo el punto de control o un archivo suelto.

Diseño (decisiones explícitas, porque no vienen dadas palabra por palabra):

1. **Captura perezosa por archivo, no una lista declarada de antemano.** El
   agente no siempre sabe de antemano qué archivos va a tocar. El punto de
   integración natural es ``track()``: se llama justo antes de cada
   escritura/borrado real, y solo la PRIMERA llamada por archivo dentro de
   un mismo checkpoint captura algo — llamadas repetidas sobre el mismo
   archivo en el mismo checkpoint son no-ops. Así "se guarda solo lo que
   cambia": si el agente toca 2 archivos de un repo de 10 000, el checkpoint
   pesa 2 archivos, no 10 000.

2. **Almacenamiento direccionado por contenido (CAS) propio, sin depender de
   ``packages/forge-kernel``.** ``edecan_companion`` está construido a
   propósito SIN dependencias de otros paquetes del monorepo (ver
   ``pyproject.toml`` de este paquete): es el único pensado para instalarse
   solo, en la máquina del usuario. ``forge-kernel/cas.py`` tiene la idea
   correcta (blobs por hash, deduplicados, con capas de prefijo hex para no
   saturar un directorio) y este módulo la reimplementa en miniatura con
   solo la librería estándar, para no romper ese aislamiento.

3. **Topes explícitos, nunca reventar.** Un repo puede tener archivos
   enormes o binarios. Superar ``MAX_TRACKED_FILE_BYTES`` no lanza una
   excepción: el archivo se registra igual (para poder borrarlo si el
   agente lo creó, o al menos avisar que no se puede restaurar su
   contenido) pero sin copiar los bytes. Superar el presupuesto total del
   checkpoint (``MAX_CHECKPOINT_TOTAL_BYTES``) hace lo mismo para los
   archivos siguientes: los ya capturados siguen siendo restaurables.

4. **Los puntos de control caducan.** Cada uno nace con una fecha de
   expiración (``DEFAULT_TTL_HOURS``) y ``prune_expired()`` los barre junto
   con los blobs que ya no pinea ningún checkpoint vivo — igual que el
   "marca y barre" de ``cas.py``, pero mirando manifiestos JSON en vez de
   una tabla SQL. También hay un tope duro de checkpoints por workspace
   (``MAX_CHECKPOINTS_PER_WORKSPACE``): al superarlo se purgan primero los
   más viejos ya expirados y, si no alcanza, el más viejo de todos.

5. **Deshacer nunca debe pisar trabajo manual posterior — decisión de
   diseño explícita.** Un punto de control por sí solo solo conoce el
   estado "antes" del agente. Si se restaura a ciegas después de que el
   agente terminó, se corre el riesgo de machacar una edición manual del
   usuario hecha DESPUÉS de que el agente soltó el archivo. Este módulo
   ataca el problema en dos capas:

   - ``seal()`` — se llama una vez cuando el turno del agente termina (éxito
     o fallo) y registra el hash "después" de cada archivo tocado. En
     ``restore()``, si el contenido actual en disco ya no coincide con ese
     hash "después" sellado, alguien lo tocó tras el turno del agente: se
     reporta como **conflicto** y NO se sobrescribe, salvo que el llamador
     pase ``force=True``. Un checkpoint que nunca se selló (turno todavía en
     curso, o interrumpido antes de sellar) NUNCA reporta conflicto: sin un
     hash "después" de referencia no hay forma de distinguir "esto lo dejó
     el agente" de "esto lo tocó una persona", y bloquear ahí por defecto
     rompería el caso de uso principal (deshacer un turno recién terminado).
     Es una limitación documentada, no una promesa de protección total.
   - Incluso con ``force=True`` sobre un conflicto real (checkpoint sellado
     y contenido distinto al esperado), el contenido que está a punto de
     perderse NUNCA se descarta sin más: se guarda primero en un checkpoint
     de rescate autogenerado (``rescued_from``) antes de sobrescribir.
     "Deshacer" puede pisar un archivo, pero solo con ``force`` explícito, y
     jamás hace desaparecer bytes que no estén recuperables en otro punto de
     control.

Integración prevista (no se toca ``ide_sessions.py`` desde aquí, ver
encargo): en ``SessionManager._run_workers_agent``, antes de cada escritura
real de archivo se llamaría ``checkpoints.track(checkpoint_id, ruta)``; al
terminar el turno (rama de éxito y de excepción, en el ``finally``) se
llamaría ``checkpoints.seal(checkpoint_id)``. El ``checkpoint_id`` se crea
una vez por turno con ``checkpoints.create(workspace_id, label=...)`` justo
antes de lanzar el hilo del agente.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from edecan_companion.ide_workspaces import IDEWorkspaceError, WorkspaceStore

# ------------------------------------------------------------------------- #
# Topes — ver punto 3 del docstring del módulo.
# ------------------------------------------------------------------------- #

MAX_TRACKED_FILE_BYTES = 8 * 1024 * 1024
"""Un solo archivo por encima de este tamaño no se copia; se registra como
``omitido_por_tamano`` (ver ``TrackedFile.status``)."""

MAX_CHECKPOINT_TOTAL_BYTES = 64 * 1024 * 1024
"""Presupuesto acumulado (bytes lógicos, antes de deduplicar) de un
checkpoint. Al superarlo, los archivos NUEVOS que se intenten trackear en
ese checkpoint quedan como ``omitido_por_presupuesto``; los ya capturados
no se ven afectados."""

DEFAULT_TTL_HOURS = 72
"""Un punto de control vive 3 días por defecto: suficiente para deshacer
"lo de esta sesión de trabajo", corto para no acumular disco indefinidamente."""

MAX_CHECKPOINTS_PER_WORKSPACE = 40
"""Tope duro por workspace. Al superarlo, ``create()`` purga primero los ya
expirados y, si no alcanza, el checkpoint más viejo (aunque siga vigente)."""


class IDECheckpointError(ValueError):
    """Solicitud de checkpoint inválida, checkpoint inexistente, o conflicto."""


Status = Literal[
    "captured",  # contenido copiado tal cual, restaurable
    "absent",  # el archivo no existía "antes"; restaurar = borrarlo
    "skipped_too_large",  # superó MAX_TRACKED_FILE_BYTES, no restaurable
    "skipped_budget",  # el checkpoint ya superó su presupuesto total
]


@dataclass
class TrackedFile:
    """Estado "antes" de un archivo dentro de un checkpoint."""

    path: str  # relativo al workspace, siempre con '/'
    status: Status
    digest: str | None = None  # sha256 hex del contenido "antes", si se capturó
    size_bytes: int = 0
    after_digest: str | None = None  # se llena en seal(); None si no está sellado

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
            "after_digest": self.after_digest,
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> TrackedFile:
        return TrackedFile(
            path=str(raw["path"]),
            status=raw["status"],
            digest=raw.get("digest"),
            size_bytes=int(raw.get("size_bytes") or 0),
            after_digest=raw.get("after_digest"),
        )


@dataclass
class Checkpoint:
    """Manifiesto completo de un punto de control (sin los blobs, esos viven aparte)."""

    id: str
    workspace_id: str
    label: str
    created_at_us: int
    expires_at_us: int
    sealed: bool = False
    files: dict[str, TrackedFile] = field(default_factory=dict)  # clave: path
    rescued_from: str | None = None  # id del checkpoint que este rescató, si aplica

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "id": self.id,
            "workspace_id": self.workspace_id,
            "label": self.label,
            "created_at_us": self.created_at_us,
            "expires_at_us": self.expires_at_us,
            "sealed": self.sealed,
            "rescued_from": self.rescued_from,
            "files": [tracked.to_json() for tracked in self.files.values()],
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Checkpoint:
        files = {
            str(row["path"]): TrackedFile.from_json(row) for row in raw.get("files", [])
        }
        return Checkpoint(
            id=str(raw["id"]),
            workspace_id=str(raw["workspace_id"]),
            label=str(raw.get("label") or ""),
            created_at_us=int(raw["created_at_us"]),
            expires_at_us=int(raw["expires_at_us"]),
            sealed=bool(raw.get("sealed", False)),
            files=files,
            rescued_from=raw.get("rescued_from"),
        )

    def public(self) -> dict[str, Any]:
        """Vista resumida para listados — sin volcar cada digest."""
        total_bytes = sum(f.size_bytes for f in self.files.values())
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "label": self.label,
            "created_at_us": self.created_at_us,
            "expires_at_us": self.expires_at_us,
            "sealed": self.sealed,
            "rescued_from": self.rescued_from,
            "file_count": len(self.files),
            "total_bytes": total_bytes,
            "paths": sorted(self.files.keys()),
        }


def _now_us() -> int:
    return int(time.time() * 1_000_000)


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CheckpointStore:
    """Crea, sella y restaura puntos de control de workspaces del IDE.

    Layout en disco bajo ``state_dir / "ide-checkpoints"``::

        blobs/<2 hex>/<2 hex>/<64 hex sha256>   # contenido "antes", deduplicado
        manifests/<checkpoint_id>.json           # metadatos + lista de archivos
        tmp/                                     # staging para escrituras atómicas

    Ningún checkpoint copia el repo entero: solo los archivos que ``track()``
    haya visto, y el CAS deduplica entre checkpoints (dos turnos que tocan el
    mismo archivo con el mismo contenido "antes" comparten un único blob).
    """

    def __init__(
        self,
        state_dir: Path,
        workspaces: WorkspaceStore,
        *,
        max_tracked_file_bytes: int = MAX_TRACKED_FILE_BYTES,
        max_checkpoint_total_bytes: int = MAX_CHECKPOINT_TOTAL_BYTES,
        ttl_hours: float = DEFAULT_TTL_HOURS,
        max_checkpoints_per_workspace: int = MAX_CHECKPOINTS_PER_WORKSPACE,
    ) -> None:
        self.workspaces = workspaces
        self.root = Path(state_dir) / "ide-checkpoints"
        self.blobs_dir = self.root / "blobs"
        self.manifests_dir = self.root / "manifests"
        self.tmp_dir = self.root / "tmp"
        for directory in (self.blobs_dir, self.manifests_dir, self.tmp_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.max_tracked_file_bytes = max_tracked_file_bytes
        self.max_checkpoint_total_bytes = max_checkpoint_total_bytes
        self.ttl_us = int(ttl_hours * 3600 * 1_000_000)
        self.max_checkpoints_per_workspace = max_checkpoints_per_workspace

    # --------------------------------------------------------------- #
    # Envoltorios sobre ``WorkspaceStore`` — este módulo nunca resuelve
    # rutas por su cuenta (esa lógica de seguridad ya vive ahí: nada de
    # rutas absolutas, nada de escapar el workspace, symlinks incluidos) y
    # traduce ``IDEWorkspaceError`` a ``IDECheckpointError`` para que quien
    # use esta API solo tenga que atrapar un tipo de error.
    # --------------------------------------------------------------- #

    def _root(self, workspace_id: str) -> Path:
        try:
            return self.workspaces.root(workspace_id)
        except IDEWorkspaceError as exc:
            raise IDECheckpointError(str(exc)) from exc

    def _resolve(self, workspace_id: str, relative_path: str) -> Path:
        try:
            return self.workspaces.resolve(workspace_id, relative_path)
        except IDEWorkspaceError as exc:
            raise IDECheckpointError(str(exc)) from exc

    # --------------------------------------------------------------- #
    # Blob store (CAS) — mínimo, sin sqlite: el tamaño ya vive en el
    # manifiesto de cada checkpoint, así que no hace falta una tabla de
    # metadatos aparte para este uso.
    # --------------------------------------------------------------- #

    def _blob_path(self, digest: str) -> Path:
        return self.blobs_dir / digest[0:2] / digest[2:4] / digest

    def _put_blob(self, data: bytes) -> str:
        digest = _digest_bytes(data)
        destino = self._blob_path(digest)
        if destino.is_file():
            # Deduplicación: el contenido ya está guardado. No se reescribe
            # ni un byte — igual que ``Cas.poner`` en forge-kernel.
            return digest
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp_name = self.tmp_dir / f".blob-{uuid.uuid4().hex}"
        tmp_name.write_bytes(data)
        os.replace(tmp_name, destino)
        return digest

    def _get_blob(self, digest: str) -> bytes:
        try:
            return self._blob_path(digest).read_bytes()
        except FileNotFoundError as exc:
            raise IDECheckpointError(
                f"El contenido guardado para el checkpoint ya no existe (digest {digest[:12]}…)."
            ) from exc

    # --------------------------------------------------------------- #
    # Persistencia del manifiesto — escritura atómica, igual que el
    # resto del companion (temp + os.replace + chmod 0o600).
    # --------------------------------------------------------------- #

    def _manifest_path(self, checkpoint_id: str) -> Path:
        return self.manifests_dir / f"{checkpoint_id}.json"

    def _save(self, checkpoint: Checkpoint) -> None:
        path = self._manifest_path(checkpoint.id)
        tmp_path = self.tmp_dir / f".manifest-{uuid.uuid4().hex}.tmp"
        tmp_path.write_text(
            json.dumps(checkpoint.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            tmp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)

    def _load(self, checkpoint_id: str) -> Checkpoint:
        path = self._manifest_path(checkpoint_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IDECheckpointError(f"Checkpoint no encontrado: {checkpoint_id}.") from exc
        return Checkpoint.from_json(raw)

    def _load_all(self) -> list[Checkpoint]:
        rows: list[Checkpoint] = []
        for manifest_file in self.manifests_dir.glob("*.json"):
            try:
                raw = json.loads(manifest_file.read_text(encoding="utf-8"))
                rows.append(Checkpoint.from_json(raw))
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        return rows

    def _delete_manifest(self, checkpoint_id: str) -> None:
        self._manifest_path(checkpoint_id).unlink(missing_ok=True)

    # --------------------------------------------------------------- #
    # API pública
    # --------------------------------------------------------------- #

    def create(self, workspace_id: str, *, label: str | None = None) -> dict[str, Any]:
        """Abre un checkpoint vacío para un workspace. No captura nada todavía:
        eso lo hace cada llamada a ``track()``."""

        # Valida que el workspace exista antes de crear nada en disco.
        self._root(workspace_id)
        self._enforce_workspace_cap(workspace_id)
        now = _now_us()
        checkpoint = Checkpoint(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            label=(label or "").strip()[:200],
            created_at_us=now,
            expires_at_us=now + self.ttl_us,
        )
        self._save(checkpoint)
        return checkpoint.public()

    def _enforce_workspace_cap(self, workspace_id: str) -> None:
        existing = sorted(
            (cp for cp in self._load_all() if cp.workspace_id == workspace_id),
            key=lambda cp: cp.created_at_us,
        )
        if len(existing) < self.max_checkpoints_per_workspace:
            return
        now = _now_us()
        # Primero purgar los ya expirados; si con eso no alcanza, tirar el
        # más viejo de todos aunque siga vigente — un tope duro es un tope
        # duro, no se puede dejar crecer sin límite el disco del usuario.
        expired = [cp for cp in existing if cp.expires_at_us <= now]
        for cp in expired:
            self._destroy(cp.id)
        remaining = len(existing) - len(expired)
        if remaining >= self.max_checkpoints_per_workspace:
            oldest = next(cp for cp in existing if cp not in expired)
            self._destroy(oldest.id)

    def track(self, checkpoint_id: str, relative_path: str) -> dict[str, Any]:
        """Captura el estado "antes" de ``relative_path``, si no se había
        capturado ya en este mismo checkpoint. Idempotente a propósito: el
        llamador real (el agente) puede invocarlo antes de CADA escritura
        sin preocuparse de si ya lo había tocado en este turno."""

        checkpoint = self._load(checkpoint_id)
        if checkpoint.sealed:
            raise IDECheckpointError(
                "Este checkpoint ya está sellado (el turno terminó); no admite más archivos."
            )
        normalized = self._normalize_path(checkpoint.workspace_id, relative_path)
        if normalized in checkpoint.files:
            return checkpoint.files[normalized].to_json()

        absolute = self._resolve(checkpoint.workspace_id, normalized)
        tracked = self._capture(checkpoint, absolute, normalized)
        checkpoint.files[normalized] = tracked
        self._save(checkpoint)
        return tracked.to_json()

    def _normalize_path(self, workspace_id: str, relative_path: str) -> str:
        # Reusa la validación de ``WorkspaceStore`` (nada de rutas absolutas,
        # nada de escapar el workspace) y devuelve la forma canónica '/'.
        absolute = self._resolve(workspace_id, relative_path)
        root = self._root(workspace_id)
        try:
            return absolute.relative_to(root).as_posix()
        except ValueError as exc:  # pragma: no cover - resolve() ya lo evita
            raise IDECheckpointError("La ruta intenta salir del workspace autorizado.") from exc

    def _capture(self, checkpoint: Checkpoint, absolute: Path, normalized: str) -> TrackedFile:
        if not absolute.is_file():
            return TrackedFile(path=normalized, status="absent")

        try:
            size = absolute.stat().st_size
        except OSError:
            return TrackedFile(path=normalized, status="absent")

        if size > self.max_tracked_file_bytes:
            return TrackedFile(path=normalized, status="skipped_too_large", size_bytes=size)

        total_so_far = sum(f.size_bytes for f in checkpoint.files.values())
        if total_so_far + size > self.max_checkpoint_total_bytes:
            return TrackedFile(path=normalized, status="skipped_budget", size_bytes=size)

        data = absolute.read_bytes()
        digest = self._put_blob(data)
        return TrackedFile(path=normalized, status="captured", digest=digest, size_bytes=size)

    def seal(self, checkpoint_id: str) -> dict[str, Any]:
        """Se llama una vez cuando el turno del agente termina. Registra el
        hash "después" de cada archivo capturado — es lo que permite a
        ``restore()`` distinguir "esto lo dejó el agente" de "alguien lo
        tocó a mano después". Idempotente: sellar dos veces solo refresca
        los hashes "después" con el estado actual."""

        checkpoint = self._load(checkpoint_id)
        for tracked in checkpoint.files.values():
            if tracked.status not in ("captured", "absent"):
                continue  # sin contenido "antes" tampoco hay "después" que comparar
            absolute = self._resolve(checkpoint.workspace_id, tracked.path)
            if absolute.is_file():
                try:
                    tracked.after_digest = _digest_bytes(absolute.read_bytes())
                except OSError:
                    tracked.after_digest = None
            else:
                tracked.after_digest = None  # "ausente" es un estado válido "después" también
        checkpoint.sealed = True
        self._save(checkpoint)
        return checkpoint.public()

    def get(self, checkpoint_id: str) -> dict[str, Any]:
        return self._load(checkpoint_id).public()

    def list(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        now = _now_us()
        rows = [
            cp.public()
            for cp in self._load_all()
            if cp.expires_at_us > now
            and (workspace_id is None or cp.workspace_id == workspace_id)
        ]
        rows.sort(key=lambda row: row["created_at_us"], reverse=True)
        return rows

    def restore(
        self,
        checkpoint_id: str,
        *,
        paths: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Restaura los archivos capturados por el checkpoint. ``paths=None``
        restaura todos los archivos que se hayan trackeado; una lista
        restringe a un subconjunto (p. ej. un solo archivo suelto).

        Nunca lanza en caso de conflicto: los reporta en ``conflicts`` y dej
        esos archivos intactos, salvo que ``force=True``. Devuelve un
        resumen accionable, no solo un booleano, porque la UI necesita poder
        decirle al usuario exactamente qué pasó con cada archivo.
        """

        checkpoint = self._load(checkpoint_id)
        selected = self._select_files(checkpoint, paths)

        restored: list[str] = []
        deleted: list[str] = []
        skipped_noop: list[str] = []
        conflicts: list[str] = []
        unrestorable: list[dict[str, str]] = []
        rescue_checkpoint_id: str | None = None

        for tracked in selected:
            absolute = self._resolve(checkpoint.workspace_id, tracked.path)

            if tracked.status in ("skipped_too_large", "skipped_budget"):
                unrestorable.append({"path": tracked.path, "reason": tracked.status})
                continue

            current_digest = self._current_digest(absolute)
            has_conflict = self._detect_conflict(checkpoint, tracked, current_digest)
            if has_conflict and not force:
                conflicts.append(tracked.path)
                continue

            if has_conflict and force:
                # Punto 5 del docstring: jamás se descarta sin más lo que se
                # va a pisar. Se guarda antes en un checkpoint de rescate.
                rescue_checkpoint_id = self._rescue(
                    checkpoint, rescue_checkpoint_id, tracked.path, absolute
                )

            if tracked.status == "absent":
                if absolute.exists():
                    absolute.unlink()
                    deleted.append(tracked.path)
                else:
                    skipped_noop.append(tracked.path)
                continue

            # tracked.status == "captured"
            assert tracked.digest is not None
            if current_digest == tracked.digest:
                skipped_noop.append(tracked.path)
                continue
            data = self._get_blob(tracked.digest)
            absolute.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.tmp_dir / f".restore-{uuid.uuid4().hex}"
            tmp_path.write_bytes(data)
            os.replace(tmp_path, absolute)
            restored.append(tracked.path)

        return {
            "checkpoint_id": checkpoint.id,
            "restored": restored,
            "deleted": deleted,
            "unchanged": skipped_noop,
            "conflicts": conflicts,
            "unrestorable": unrestorable,
            "rescue_checkpoint_id": rescue_checkpoint_id,
        }

    def restore_file(
        self, checkpoint_id: str, relative_path: str, *, force: bool = False
    ) -> dict[str, Any]:
        """Azúcar sobre ``restore()`` para el caso "un archivo suelto"."""
        return self.restore(checkpoint_id, paths=[relative_path], force=force)

    def read_before(self, checkpoint_id: str, relative_path: str) -> dict[str, Any]:
        """Contenido "antes" de un archivo trackeado, SIN tocar el disco.

        Desajuste de contrato real que este método arregla: la única forma
        pública de leer el contenido "antes" era ``restore()``/``restore_file()``,
        que ESCRIBE al workspace -- inservible para pintar una vista de diff
        (1.2 del plan de paridad), que necesita leer sin restaurar todavía
        (el usuario decide después, archivo por archivo, con Aceptar/Rechazar).
        Se decodifica como UTF-8 con reemplazo, igual criterio que
        ``ide_reglas.py``: mejor un carácter de reemplazo suelto en la vista
        de diff que reventar por un archivo con codificación rara.
        """

        checkpoint = self._load(checkpoint_id)
        normalized = self._normalize_path(checkpoint.workspace_id, relative_path)
        tracked = checkpoint.files.get(normalized)
        if tracked is None:
            raise IDECheckpointError(
                f"Este checkpoint no capturó el archivo: {relative_path}."
            )
        if tracked.status == "captured":
            assert tracked.digest is not None
            content = self._get_blob(tracked.digest).decode("utf-8", errors="replace")
            return {"path": tracked.path, "status": tracked.status, "content": content}
        # "absent" (no existía antes) y los dos "skipped_*" no tienen bytes
        # capturados que decodificar -- ``content`` queda en ``None`` y quien
        # llama (la composición del diff de un turno) decide qué mostrar.
        return {"path": tracked.path, "status": tracked.status, "content": None}

    def _select_files(
        self, checkpoint: Checkpoint, paths: list[str] | None
    ) -> list[TrackedFile]:
        if paths is None:
            return list(checkpoint.files.values())
        selected: list[TrackedFile] = []
        for raw_path in paths:
            normalized = self._normalize_path(checkpoint.workspace_id, raw_path)
            tracked = checkpoint.files.get(normalized)
            if tracked is None:
                raise IDECheckpointError(
                    f"Este checkpoint no capturó el archivo: {raw_path}."
                )
            selected.append(tracked)
        return selected

    @staticmethod
    def _current_digest(absolute: Path) -> str | None:
        if not absolute.is_file():
            return None
        try:
            return _digest_bytes(absolute.read_bytes())
        except OSError:
            return None

    @staticmethod
    def _detect_conflict(
        checkpoint: Checkpoint, tracked: TrackedFile, current_digest: str | None
    ) -> bool:
        """¿El archivo cambió POR FUERA del propio agente desde que terminó su
        turno? Ver punto 5 del docstring del módulo.

        La señal de "esto es trabajo manual, cuidado" es ``seal()``: antes de
        sellar, el contenido en disco es presumiblemente el propio trabajo a
        medio hacer del agente en ESTE turno — descartarlo es exactamente lo
        que "deshacer" significa, así que un checkpoint sin sellar NUNCA
        reporta conflicto (si no, la restauración inmediata de un solo turno,
        el caso de uso principal de este módulo, quedaría bloqueada por
        ``force`` todo el tiempo). Un checkpoint SIN sellar es una limitación
        documentada, no un agujero: no protege contra ediciones manuales que
        ocurran mientras el turno sigue vivo o quedó interrumpido a medio
        camino, porque no hay forma de distinguirlas del propio trabajo del
        agente sin un punto de referencia "después".

        Tras sellar, se compara el contenido actual contra el hash "después"
        que ``seal()`` registró. Si coincide, nadie lo tocó desde que el
        agente soltó el archivo — restaurar es seguro. Si no coincide, algo
        (casi siempre el usuario, a mano) lo cambió después: es un conflicto
        real y se protege con ``force``.
        """
        if not checkpoint.sealed:
            return False
        return current_digest != tracked.after_digest

    def _rescue(
        self,
        checkpoint: Checkpoint,
        rescue_checkpoint_id: str | None,
        relative_path: str,
        absolute: Path,
    ) -> str:
        """Guarda el contenido que ``restore(force=True)`` está a punto de
        pisar en un checkpoint nuevo, ya sellado, para que sea recuperable.
        Un solo checkpoint de rescate agrupa todos los archivos rescatados
        de esta misma llamada a ``restore()``."""

        if rescue_checkpoint_id is None:
            rescue = Checkpoint(
                id=str(uuid.uuid4()),
                workspace_id=checkpoint.workspace_id,
                label=f"Rescate automático antes de deshacer {checkpoint.id[:8]}",
                created_at_us=_now_us(),
                expires_at_us=_now_us() + self.ttl_us,
                sealed=True,
                rescued_from=checkpoint.id,
            )
        else:
            rescue = self._load(rescue_checkpoint_id)

        tracked = self._capture(rescue, absolute, relative_path)
        # El rescate es una foto fija: su "después" es el mismo contenido
        # que su "antes", así que una restauración futura de ESTE checkpoint
        # de rescate nunca se marca a sí misma como conflicto.
        tracked.after_digest = tracked.digest
        rescue.files[relative_path] = tracked
        self._save(rescue)
        return rescue.id

    # --------------------------------------------------------------- #
    # Mantenimiento
    # --------------------------------------------------------------- #

    def _destroy(self, checkpoint_id: str) -> None:
        self._delete_manifest(checkpoint_id)

    def prune_expired(self) -> dict[str, Any]:
        """Barre checkpoints vencidos y los blobs que ya nadie pinea. Pensado
        para llamarse periódicamente (p. ej. al arrancar el companion, igual
        que la limpieza de imágenes mencionada en ``Aria_DESIGN_DIR``) —
        este módulo no se agenda solo."""

        now = _now_us()
        all_checkpoints = self._load_all()
        expired = [cp for cp in all_checkpoints if cp.expires_at_us <= now]
        for cp in expired:
            self._destroy(cp.id)

        live_digests = {
            tracked.digest
            for cp in all_checkpoints
            if cp.expires_at_us > now
            for tracked in cp.files.values()
            if tracked.digest is not None
        }
        blobs_removed = 0
        bytes_freed = 0
        if self.blobs_dir.is_dir():
            for blob_path in self.blobs_dir.rglob("*"):
                if not blob_path.is_file():
                    continue
                if blob_path.name in live_digests:
                    continue
                try:
                    bytes_freed += blob_path.stat().st_size
                    blob_path.unlink()
                    blobs_removed += 1
                except OSError:
                    continue
        # Directorios de prefijo vacíos no molestan a nadie mucho, pero
        # limpiarlos evita que el árbol de blobs crezca sin fin en número de
        # carpetas incluso cuando ya no queda contenido dentro.
        for sub in sorted(self.blobs_dir.glob("*/*"), reverse=True):
            if sub.is_dir():
                with contextlib.suppress(OSError):
                    sub.rmdir()
        for sub in sorted(self.blobs_dir.glob("*"), reverse=True):
            if sub.is_dir():
                with contextlib.suppress(OSError):
                    sub.rmdir()

        return {
            "checkpoints_removed": len(expired),
            "blobs_removed": blobs_removed,
            "bytes_freed": bytes_freed,
        }

    def discard(self, checkpoint_id: str) -> None:
        """Borra un checkpoint puntual antes de que expire (p. ej. tras un
        `seal()` exitoso si el llamador decide que ya no hace falta poder
        deshacer ese turno). No hace GC de blobs por sí solo — eso lo hace
        `prune_expired()` para no pagar el costo de recorrer todo el árbol
        de blobs en cada `discard()` individual."""

        self._load(checkpoint_id)  # valida que exista antes de borrar
        self._destroy(checkpoint_id)
