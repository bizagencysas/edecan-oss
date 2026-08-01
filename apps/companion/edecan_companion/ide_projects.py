"""Proyectos y conversaciones del IDE (Forge Studio).

Hasta ahora el IDE solo conocía dos cosas: ``workspaces`` (carpetas de repo
autorizadas, ``ide_workspaces.py``) y sesiones sueltas de agente/terminal
(``ide_sessions.py``). No existía ningún concepto que agrupara conversaciones
bajo un proyecto -- cada mensaje de Forge Studio abría una sesión nueva sin
ninguna jerarquía visible, que es justo lo que el usuario pidió resolver con
"proyectos como carpetas, conversaciones anidadas dentro" (estilo Antigravity).

Este módulo agrega exactamente esa capa de agrupación:

- ``Project``: nombre + una carpeta de repo asociada (reutiliza un
  ``workspace_id`` ya autorizado por ``WorkspaceStore`` -- así toda la
  validación de rutas sensibles/fuera-de-límites vive en un solo lugar).
- ``Conversation``: pertenece a un proyecto (o a ninguno, "sin proyecto").
  Su ``id`` es deliberadamente el mismo valor que ``AgentStartIn.conversation_id``
  en ``routers/ide.py``: cuando la fase siguiente conecte la UI, crear una
  conversación aquí y pasar su ``id`` como ``conversation_id`` a
  ``POST /v1/ide/agents`` hace que ``ide_sessions._prompt_with_conversation_context``
  seguirá reconstruyendo el historial exactamente como ya hace hoy, sin tocar
  ese archivo.

Deliberadamente NO importa ni modifica ``ide_sessions.py`` (propiedad de otro
agente trabajando en paralelo sobre ese archivo). Por eso "borrar una
conversación" en este módulo es responsabilidad exclusiva del registro
projects/conversations; el mejor esfuerzo de cancelar sesiones de agente que
sigan corriendo para esa conversación se hace en ``ide_runtime.py`` (que ya
tiene una referencia viva a ``SessionManager``) usando únicamente su API
pública (``list``/``close``). Ver el docstring de
``execute_ide_action``/``IDERuntime.dispatch`` para el paso exacto.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from edecan_companion.ide_workspaces import IDEWorkspaceError, WorkspaceStore

DeleteProjectConversations = Literal["keep", "delete"]

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_UNASSIGNED = "__sin_proyecto__"  # sentinel interno de list_conversations(); nunca se persiste.


class IDEProjectError(ValueError):
    """Solicitud de proyecto/conversación inválida."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_text(value: Any, *, field: str, max_length: int, fallback: str | None = None) -> str:
    if value is None and fallback is not None:
        return fallback
    if not isinstance(value, str):
        raise IDEProjectError(f"{field} debe ser texto.")
    text = value.strip()
    if not text and fallback is not None:
        return fallback
    if not text or len(text) > max_length or _CONTROL_CHARS.search(text):
        raise IDEProjectError(f"{field} no es válido.")
    return text


class ProjectRegistry:
    """Registro JSON local, atómico y privado de proyectos + conversaciones.

    Un solo archivo (``ide-projects.json``) y un solo lock para ambas
    colecciones: borrar un proyecto puede tener que tocar varias
    conversaciones a la vez (cascada o desasignación) y esa operación debe
    ser atómica -- dos archivos separados podrían quedar inconsistentes si el
    proceso muere entre el guardado de uno y el del otro.
    """

    def __init__(self, state_dir: Path, workspaces: WorkspaceStore) -> None:
        self.state_dir = state_dir
        self.path = state_dir / "ide-projects.json"
        self.workspaces = workspaces
        self._lock = threading.RLock()
        self._projects: dict[str, dict[str, Any]] = {}
        self._conversations: dict[str, dict[str, Any]] = {}
        self._load()

    # -- persistencia ------------------------------------------------------

    def _load(self) -> None:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            projects = data.get("projects", []) if isinstance(data, dict) else []
            conversations = data.get("conversations", []) if isinstance(data, dict) else []
            for row in projects if isinstance(projects, list) else []:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    continue
                self._projects[row["id"]] = {
                    "id": row["id"],
                    "name": str(row.get("name") or "Proyecto"),
                    "workspace_id": str(row.get("workspace_id") or ""),
                    "created_at": str(row.get("created_at") or _now()),
                    "updated_at": str(row.get("updated_at") or row.get("created_at") or _now()),
                }
            for row in conversations if isinstance(conversations, list) else []:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    continue
                project_id = row.get("project_id")
                self._conversations[row["id"]] = {
                    "id": row["id"],
                    "project_id": str(project_id) if isinstance(project_id, str) else None,
                    "title": str(row.get("title") or "Nueva conversación"),
                    "created_at": str(row.get("created_at") or _now()),
                    "updated_at": str(row.get("updated_at") or row.get("created_at") or _now()),
                }

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        payload = {
            "version": 1,
            "projects": list(self._projects.values()),
            "conversations": list(self._conversations.values()),
        }
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temp_path, self.path)

    # -- proyectos -----------------------------------------------------------

    def _snapshot_project(self, row: dict[str, Any]) -> dict[str, Any]:
        snapshot = dict(row)
        try:
            workspace = self.workspaces.get(row["workspace_id"])
        except IDEWorkspaceError:
            workspace = None
        snapshot["workspace_name"] = workspace["name"] if workspace else None
        snapshot["workspace_path"] = workspace["path"] if workspace else None
        snapshot["workspace_available"] = bool(workspace and workspace["available"])
        snapshot["conversation_count"] = sum(
            1 for row2 in self._conversations.values() if row2["project_id"] == row["id"]
        )
        return snapshot

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [self._snapshot_project(row) for row in self._projects.values()]
            return sorted(rows, key=lambda row: (str(row["name"]).casefold(), str(row["id"])))

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._projects.get(project_id)
            if row is None:
                raise IDEProjectError("Proyecto no encontrado.")
            return self._snapshot_project(row)

    def create_project(self, name: Any, workspace_id: Any) -> dict[str, Any]:
        clean_name = _clean_text(name, field="El nombre del proyecto", max_length=120)
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise IDEProjectError("workspace_id debe ser texto no vacío.")
        # Falla temprano si la carpeta no está autorizada; no se acepta
        # ninguna ruta aquí, solo un ``workspace_id`` ya existente en
        # ``WorkspaceStore``. Se reenvuelve como ``IDEProjectError`` para que
        # quien llame este módulo tenga un único tipo de error que atrapar.
        try:
            self.workspaces.get(workspace_id)
        except IDEWorkspaceError as exc:
            raise IDEProjectError(str(exc)) from exc
        with self._lock:
            project_id = str(uuid.uuid4())
            row = {
                "id": project_id,
                "name": clean_name,
                "workspace_id": workspace_id,
                "created_at": _now(),
                "updated_at": _now(),
            }
            self._projects[project_id] = row
            self._save()
            return self._snapshot_project(row)

    def rename_project(self, project_id: str, name: Any) -> dict[str, Any]:
        clean_name = _clean_text(name, field="El nombre del proyecto", max_length=120)
        with self._lock:
            row = self._projects.get(project_id)
            if row is None:
                raise IDEProjectError("Proyecto no encontrado.")
            row["name"] = clean_name
            row["updated_at"] = _now()
            self._save()
            return self._snapshot_project(row)

    def delete_project(
        self, project_id: str, *, conversations: DeleteProjectConversations = "keep"
    ) -> dict[str, Any]:
        """Borra el registro del proyecto. Nunca toca la carpeta del repo en disco.

        ``conversations="keep"`` (default, lo que pide un botón "Borrar" sin
        más contexto) desasigna sus conversaciones -- pasan a "sin proyecto"
        en vez de desaparecer. ``conversations="delete"`` es la respuesta
        explícita de la persona a "¿qué hago con sus conversaciones?" y las
        borra también (ver ``delete_conversation``, que documenta por qué eso
        no purga las sesiones de agente ya archivadas).
        """
        if conversations not in ("keep", "delete"):
            raise IDEProjectError("conversations debe ser 'keep' o 'delete'.")
        with self._lock:
            if project_id not in self._projects:
                raise IDEProjectError("Proyecto no encontrado.")
            affected = [
                row["id"] for row in self._conversations.values() if row["project_id"] == project_id
            ]
            if conversations == "delete":
                for conversation_id in affected:
                    self._conversations.pop(conversation_id, None)
            else:
                for conversation_id in affected:
                    self._conversations[conversation_id]["project_id"] = None
                    self._conversations[conversation_id]["updated_at"] = _now()
            del self._projects[project_id]
            self._save()
            return {
                "deleted_project_id": project_id,
                "conversations_deleted": conversations == "delete",
                "affected_conversation_ids": affected,
            }

    # -- conversaciones --------------------------------------------------

    def list_conversations(
        self, project_id: str | None = None, *, only_unassigned: bool = False
    ) -> list[dict[str, Any]]:
        with self._lock:
            if only_unassigned:
                rows = [row for row in self._conversations.values() if row["project_id"] is None]
            elif project_id is not None:
                if project_id not in self._projects:
                    raise IDEProjectError("Proyecto no encontrado.")
                rows = [
                    row for row in self._conversations.values() if row["project_id"] == project_id
                ]
            else:
                rows = list(self._conversations.values())
            return sorted(
                [dict(row) for row in rows],
                key=lambda row: str(row["updated_at"]),
                reverse=True,
            )

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conversations.get(conversation_id)
            if row is None:
                raise IDEProjectError("Conversación no encontrada.")
            return dict(row)

    def create_conversation(self, project_id: Any, title: Any = None) -> dict[str, Any]:
        clean_title = _clean_text(
            title,
            field="El título de la conversación",
            max_length=160,
            fallback="Nueva conversación",
        )
        with self._lock:
            resolved_project_id: str | None = None
            if project_id is not None:
                if not isinstance(project_id, str) or not project_id.strip():
                    raise IDEProjectError("project_id debe ser texto no vacío.")
                if project_id not in self._projects:
                    raise IDEProjectError("Proyecto no encontrado.")
                resolved_project_id = project_id
            conversation_id = str(uuid.uuid4())
            row = {
                "id": conversation_id,
                "project_id": resolved_project_id,
                "title": clean_title,
                "created_at": _now(),
                "updated_at": _now(),
            }
            self._conversations[conversation_id] = row
            self._save()
            return dict(row)

    def rename_conversation(self, conversation_id: str, title: Any) -> dict[str, Any]:
        clean_title = _clean_text(title, field="El título de la conversación", max_length=160)
        with self._lock:
            row = self._conversations.get(conversation_id)
            if row is None:
                raise IDEProjectError("Conversación no encontrada.")
            row["title"] = clean_title
            row["updated_at"] = _now()
            self._save()
            return dict(row)

    def move_conversation(self, conversation_id: str, project_id: Any) -> dict[str, Any]:
        with self._lock:
            row = self._conversations.get(conversation_id)
            if row is None:
                raise IDEProjectError("Conversación no encontrada.")
            resolved_project_id: str | None = None
            if project_id is not None:
                if not isinstance(project_id, str) or not project_id.strip():
                    raise IDEProjectError("project_id debe ser texto no vacío.")
                if project_id not in self._projects:
                    raise IDEProjectError("Proyecto no encontrado.")
                resolved_project_id = project_id
            row["project_id"] = resolved_project_id
            row["updated_at"] = _now()
            self._save()
            return dict(row)

    def delete_conversation(self, conversation_id: str) -> dict[str, Any]:
        """Borra el registro de la conversación.

        Esto quita la conversación de proyectos/listados -- el contrato REST
        que consume la fase siguiente. Las sesiones de agente ya archivadas
        que tenían este ``conversation_id`` (``ide_sessions.SessionManager``)
        no se purgan desde aquí a propósito: ese archivo lo edita otro agente
        en paralelo ahora mismo y no se toca. ``IDERuntime.dispatch`` (que sí
        tiene una referencia viva a ``SessionManager``) hace el mejor
        esfuerzo de cancelar cualquier sesión de agente todavía corriendo
        para este ``conversation_id`` antes de llamar aquí -- ver su
        docstring para el motivo exacto y qué falta para una purga completa.
        """
        with self._lock:
            if conversation_id not in self._conversations:
                raise IDEProjectError("Conversación no encontrada.")
            del self._conversations[conversation_id]
            self._save()
            return {"deleted_conversation_id": conversation_id}
