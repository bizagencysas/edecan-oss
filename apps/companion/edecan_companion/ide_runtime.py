"""Runtime local de IDE, terminal, agentes y Git para Edecán.

Este módulo es deliberadamente aditivo: las acciones históricas continúan en
``actions.py``. Las acciones ``ide_*`` usan workspaces autorizados y procesos
residentes que sobreviven a desconexiones del teléfono.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import re
import subprocess
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_llm.workers_ai import MODELO_IDE_POR_DEFECTO, WorkersAIProvider

from edecan_companion import audit, ide_comandos, ide_contexto, ide_modos, ide_sesion_extras
from edecan_companion.config import CompanionConfig
from edecan_companion.ide_acciones_codigo import auditar_seguridad as _auditar_seguridad
from edecan_companion.ide_acciones_codigo import (
    escribir_agents_md as _escribir_agents_md,
)
from edecan_companion.ide_acciones_codigo import (
    preparar_revision as _preparar_revision,
)
from edecan_companion.ide_acciones_codigo import (
    preparar_simplificacion as _preparar_simplificacion,
)
from edecan_companion.ide_clone import CloneService, IDECloneError
from edecan_companion.ide_equipo import (
    ControlEquipo,
    EquipoDeAgentes,
    ResultadoEquipo,
    Subtarea,
    construir_plan,
    especificacion_herramienta,
)
from edecan_companion.ide_files import FileService, IDEFileError
from edecan_companion.ide_git import GitService, IDEGitError
from edecan_companion.ide_plan import PlanStore
from edecan_companion.ide_projects import IDEProjectError, ProjectRegistry
from edecan_companion.ide_referencias import ReferenceService
from edecan_companion.ide_sessions import IDESessionError, SessionManager
from edecan_companion.ide_workspaces import (
    IDEWorkspaceError,
    WorkspaceStore,
    pick_workspace_folder,
)

# Comandos cuya capacidad real vive fuera de lo que este companion local
# puede alcanzar hoy: ``/usage``/``/memory`` son del servidor multi-tenant
# (planes y memoria son por cuenta, no por máquina); ``/mcp``/``/voice``/
# ``/workflows`` dependen de paquetes que otra corrida tiene en uso ahora
# mismo (``packages/mcp``, ``packages/voice``, ``packages/skills``) o de
# traer una dependencia pesada solo para este atajo; ``/remote-control``
# activa control remoto del equipo, una acción con consecuencias reales que
# amerita su propia pantalla dedicada, no un comando de una línea. Ninguno
# inventa un resultado -- todos señalan dónde SÍ existe la función hoy.
_MENSAJE_INFORMATIVO: dict[str, str] = {
    "usage": "El consumo del plan se administra en el servidor; revisa Cuenta > Plan en la app.",
    "memory": "La memoria persistente se administra en Cuenta > Memoria en la app.",
    "mcp": "Los servidores MCP se administran en Ajustes > Integraciones.",
    "voice": "Prueba y configura la voz en Ajustes > Voz.",
    "remote-control": "Activa el control remoto desde Ajustes > Control remoto.",
    "workflows": "Los flujos de trabajo guardados viven en la sección Skills de la app.",
}

Approver = Callable[[str, dict[str, Any], CompanionConfig], Awaitable[bool]]

IDE_ACTIONS = frozenset(
    {
        "ide_workspace_list",
        "ide_workspace_pick",
        "ide_workspace_authorize",
        "ide_workspace_activate",
        "ide_workspace_clone",
        "ide_tree",
        "ide_read_file",
        "ide_write_file",
        "ide_apply_edit",
        "ide_search",
        "ide_terminal_list",
        "ide_terminal_start",
        "ide_terminal_read",
        "ide_terminal_input",
        "ide_terminal_close",
        "ide_agent_list",
        "ide_agent_start",
        "ide_agent_read",
        "ide_agent_cancel",
        "ide_agent_mcp_pending",
        "ide_agent_mcp_resolve",
        "ide_agent_diff",
        "ide_agent_diff_reject",
        "ide_agent_cost",
        "ide_reference_search",
        "ide_semantic_search",
        "ide_semantic_search_status",
        "ide_semantic_search_reindex",
        "ide_memory_list",
        "ide_memory_forget",
        "ide_git_status",
        "ide_git_diff",
        "ide_git_log",
        "ide_git_stage",
        "ide_git_unstage",
        "ide_git_commit",
        "ide_git_branch",
        "ide_git_checkout",
        "ide_git_push",
        "ide_project_list",
        "ide_project_create",
        "ide_project_rename",
        "ide_project_delete",
        "ide_conversation_list",
        "ide_conversation_create",
        "ide_conversation_rename",
        "ide_conversation_move",
        "ide_conversation_delete",
        "ide_command_list",
        "ide_command_execute",
    }
)

_APPROVAL_ACTIONS = frozenset(
    {
        "ide_workspace_authorize",
        "ide_workspace_clone",
        "ide_terminal_start",
        "ide_agent_start",
        "ide_write_file",
        "ide_apply_edit",
        "ide_agent_diff_reject",
        "ide_git_stage",
        "ide_git_unstage",
        "ide_git_commit",
        "ide_git_branch",
        "ide_git_checkout",
        "ide_git_push",
    }
)
_RUNTIMES: dict[str, IDERuntime] = {}
_RUNTIMES_LOCK = threading.Lock()
_URL_CREDENTIALS = re.compile(r"(https?://)[^/@\s]+(?::[^/@\s]*)?@")
_TOKENISH = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,})\b"
)
_HIGH_ENTROPY_SECRET = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{40,}(?![A-Za-z0-9_-])")

# Techo del historial que ``/branch`` reinyecta en la conversación bifurcada
# -- mismo criterio de recorte que ``ide_sessions.MAX_REINJECTED_CONTEXT_CHARS``
# (una cifra propia acá porque este archivo no importa ese módulo interno).
_MAX_HISTORIAL_BIFURCACION_CHARS = 20_000
# Tope de sub-tareas en paralelo que ``/batch`` lanza a la vez -- deliberadamente
# más bajo que ``ide_equipo.MAX_CONCURRENCIA_PERMITIDA`` (8): varios procesos
# reales de ``WorkersIDEAgent`` a la vez en la máquina de la persona, no solo
# tareas async livianas.
_BATCH_MAX_CONCURRENCIA = 3


def _required_text(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} debe ser texto no vacío.")
    return value


def _optional_text(params: dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} debe ser texto.")
    return value


def _text(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} debe ser texto.")
    return value


def _integer(params: dict[str, Any], key: str, default: int) -> int:
    value = params.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} debe ser un entero.")
    return value


def _boolean(params: dict[str, Any], key: str, default: bool = False) -> bool:
    value = params.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} debe ser true o false.")
    return value


def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
    return audit.sanitize_params(params)


def _safe_error(exc: BaseException) -> str:
    message = str(exc) or type(exc).__name__
    message = _URL_CREDENTIALS.sub(r"\1***@", message)
    message = _TOKENISH.sub("<credencial omitida>", message)
    message = _HIGH_ENTROPY_SECRET.sub("<credencial omitida>", message)
    return message[:2000]


class IDERuntime:
    def __init__(self, config: CompanionConfig) -> None:
        self.config = config
        state_dir = config.config_path.parent / "ide"
        self.workspaces = WorkspaceStore(state_dir)
        self.files = FileService(self.workspaces)
        self.sessions = SessionManager(state_dir, self.workspaces)
        self.git = GitService(self.workspaces)
        self.clone_service = CloneService(self.workspaces)
        self.projects = ProjectRegistry(state_dir, self.workspaces)
        self.references = ReferenceService(self.workspaces)
        # Estado de los comandos "/" que necesitan memoria propia (ver
        # ``ide_modos.py``/``ide_sesion_extras.py``): en memoria los tres
        # primeros (bajo volumen, se pierden si el companion reinicia, igual
        # que ``ide_plan.PlanStore`` ya asume en su propio docstring);
        # persistidos a disco los dos últimos porque sí importa que
        # sobrevivan a un reinicio (qué quedó en segundo plano, qué permisos
        # se aflojaron).
        self.goal_store = ide_modos.GoalStore()
        self.effort_store = ide_modos.EsfuerzoStore()
        self.plan_mode_store = ide_modos.ModoPlanificacionStore()
        self.plan_store = PlanStore()
        self.background_store = ide_sesion_extras.SegundoPlanoStore(state_dir)
        self.permissions_store = ide_sesion_extras.PermissionsStore(state_dir)

    def dispatch(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if action == "ide_workspace_list":
            return {"workspaces": self.workspaces.list()}
        if action == "ide_workspace_pick":
            selected = pick_workspace_folder()
            if selected is None:
                return {"cancelled": True}
            workspace = self.workspaces.authorize(selected, _optional_text(params, "name"))
            if _boolean(params, "activate", True):
                workspace = self.workspaces.activate(workspace["id"])
            return {"cancelled": False, "workspace": workspace}
        if action == "ide_workspace_authorize":
            return {
                "workspace": self.workspaces.authorize(
                    _required_text(params, "path"), _optional_text(params, "name")
                )
            }
        if action == "ide_workspace_activate":
            return {"workspace": self.workspaces.activate(_required_text(params, "workspace_id"))}
        if action == "ide_workspace_clone":
            depth = params.get("depth")
            if depth is not None:
                depth = _integer(params, "depth", 0)
            return self.clone_service.clone(
                parent_workspace_id=_required_text(params, "parent_workspace_id"),
                url=_required_text(params, "url"),
                name=_optional_text(params, "name"),
                branch=_optional_text(params, "branch"),
                depth=depth,
                activate=_boolean(params, "activate", True),
            )

        if action == "ide_tree":
            return self.files.tree(
                _required_text(params, "workspace_id"),
                str(params.get("path") or "."),
                max_depth=_integer(params, "max_depth", 4),
                max_entries=_integer(params, "max_entries", 500),
            )
        if action == "ide_read_file":
            return self.files.read(
                _required_text(params, "workspace_id"), _required_text(params, "path")
            )
        if action == "ide_write_file":
            return self.files.write(
                _required_text(params, "workspace_id"),
                _required_text(params, "path"),
                _text(params, "content"),
            )
        if action == "ide_apply_edit":
            return self.files.edit(
                _required_text(params, "workspace_id"),
                _required_text(params, "path"),
                _required_text(params, "old_string"),
                _text(params, "new_string"),
                replace_all=_boolean(params, "replace_all"),
            )
        if action == "ide_search":
            return self.files.search(
                _required_text(params, "workspace_id"),
                _required_text(params, "query"),
                str(params.get("path") or "."),
            )

        if action == "ide_terminal_list":
            return self.sessions.list("terminal", _optional_text(params, "workspace_id"))
        if action == "ide_terminal_start":
            return self.sessions.start_terminal(
                _required_text(params, "workspace_id"),
                params.get("argv"),
                params.get("title"),
            )
        if action == "ide_terminal_read":
            return self.sessions.read(
                _required_text(params, "session_id"),
                "terminal",
                _integer(params, "cursor", 0),
            )
        if action == "ide_terminal_input":
            return self.sessions.input_terminal(
                _required_text(params, "session_id"), _required_text(params, "data")
            )
        if action == "ide_terminal_close":
            return self.sessions.close(_required_text(params, "session_id"), "terminal")

        if action == "ide_agent_list":
            return self.sessions.list("agent", _optional_text(params, "workspace_id"))
        if action == "ide_agent_start":
            provider = str(params.get("provider") or "auto")
            return self.sessions.start_agent(
                _required_text(params, "workspace_id"),
                _required_text(params, "prompt"),
                provider,
                params.get("title"),
                _optional_text(params, "model"),
                params.get("attachments"),
                _optional_text(params, "skill_context"),
                _optional_text(params, "conversation_id"),
                params.get("mcp_tools"),
            )
        if action == "ide_agent_read":
            return self.sessions.read(
                _required_text(params, "session_id"),
                "agent",
                _integer(params, "cursor", 0),
            )
        if action == "ide_agent_cancel":
            return self.sessions.close(_required_text(params, "session_id"), "agent")
        if action == "ide_agent_mcp_pending":
            return self.sessions.pending_mcp(
                _required_text(params, "session_id"),
                _required_text(params, "call_id"),
            )
        if action == "ide_agent_mcp_resolve":
            result = params.get("result")
            if not isinstance(result, dict):
                raise ValueError("result debe ser un objeto.")
            return self.sessions.resolve_mcp(
                _required_text(params, "session_id"),
                _required_text(params, "call_id"),
                result,
            )
        if action == "ide_agent_diff":
            return self.sessions.turn_diff(_required_text(params, "session_id"))
        if action == "ide_agent_diff_reject":
            return self.sessions.reject_turn_file(
                _required_text(params, "session_id"), _required_text(params, "path")
            )
        if action == "ide_agent_cost":
            return self.sessions.turn_cost(_required_text(params, "session_id"))

        if action == "ide_reference_search":
            kinds_raw = params.get("kinds")
            kinds = tuple(kinds_raw) if isinstance(kinds_raw, list) and kinds_raw else None
            recent_raw = params.get("recently_opened")
            recent = [str(item) for item in recent_raw] if isinstance(recent_raw, list) else None
            return self.references.search(
                _required_text(params, "workspace_id"),
                str(params.get("prefix") or ""),
                kinds=kinds,
                limit=_integer(params, "limit", 20),
                recently_opened=recent,
            )

        if action == "ide_semantic_search":
            # 2.2 del plan de paridad: mismo servicio (``self.sessions.semantic``,
            # ver ``ide_sessions.SessionManager.__init__``) que ya usa el agente
            # como herramienta -- el panel de búsqueda de la persona y el
            # tool del agente comparten índice, nunca dos copias separadas.
            return self.sessions.semantic.search(
                _required_text(params, "workspace_id"),
                _required_text(params, "query"),
                k=_integer(params, "k", 10),
            )
        if action == "ide_semantic_search_status":
            return self.sessions.semantic.status(_required_text(params, "workspace_id"))
        if action == "ide_semantic_search_reindex":
            return self.sessions.semantic.start_indexing(_required_text(params, "workspace_id"))

        if action == "ide_memory_list":
            # 2.3 del plan de paridad, vista de transparencia: toda la
            # memoria guardada de este workspace, no solo la relevante para
            # un prompt puntual (eso es ``recall``, ya inyectado en el
            # prompt del turno por ``ide_sessions._memory_block_for``).
            workspace_id = _required_text(params, "workspace_id")
            return {"notes": self.sessions.memoria.list_notes(workspace_id)}
        if action == "ide_memory_forget":
            return self.sessions.memoria.forget(
                _required_text(params, "workspace_id"), _required_text(params, "note_id")
            )

        if action == "ide_git_status":
            return self.git.status(_required_text(params, "workspace_id"))
        if action == "ide_git_diff":
            return self.git.diff(
                _required_text(params, "workspace_id"),
                staged=_boolean(params, "staged"),
                paths=params.get("paths"),
            )
        if action == "ide_git_log":
            return self.git.log(
                _required_text(params, "workspace_id"), limit=_integer(params, "limit", 50)
            )
        if action == "ide_git_stage":
            return self.git.stage(_required_text(params, "workspace_id"), params.get("paths"))
        if action == "ide_git_unstage":
            return self.git.unstage(_required_text(params, "workspace_id"), params.get("paths"))
        if action == "ide_git_commit":
            return self.git.commit(_required_text(params, "workspace_id"), params.get("message"))
        if action == "ide_git_branch":
            return self.git.branch(
                _required_text(params, "workspace_id"),
                params.get("name"),
                checkout=_boolean(params, "checkout"),
            )
        if action == "ide_git_checkout":
            return self.git.checkout(
                _required_text(params, "workspace_id"),
                params.get("name"),
                create=_boolean(params, "create"),
            )
        if action == "ide_git_push":
            return self.git.push(
                _required_text(params, "workspace_id"),
                remote=params.get("remote", "origin"),
                branch=params.get("branch"),
                set_upstream=_boolean(params, "set_upstream"),
            )

        if action == "ide_project_list":
            return {"projects": self.projects.list_projects()}
        if action == "ide_project_create":
            return {
                "project": self.projects.create_project(
                    _required_text(params, "name"), _required_text(params, "workspace_id")
                )
            }
        if action == "ide_project_rename":
            return {
                "project": self.projects.rename_project(
                    _required_text(params, "project_id"), _required_text(params, "name")
                )
            }
        if action == "ide_project_delete":
            return self._delete_project(params)

        if action == "ide_conversation_list":
            only_unassigned = _boolean(params, "only_unassigned", False)
            return {
                "conversations": self.projects.list_conversations(
                    _optional_text(params, "project_id"), only_unassigned=only_unassigned
                )
            }
        if action == "ide_conversation_create":
            return {
                "conversation": self.projects.create_conversation(
                    _optional_text(params, "project_id"), params.get("title")
                )
            }
        if action == "ide_conversation_rename":
            return {
                "conversation": self.projects.rename_conversation(
                    _required_text(params, "conversation_id"), _required_text(params, "title")
                )
            }
        if action == "ide_conversation_move":
            return {
                "conversation": self.projects.move_conversation(
                    _required_text(params, "conversation_id"),
                    _optional_text(params, "project_id"),
                )
            }
        if action == "ide_conversation_delete":
            return self._delete_conversation(_required_text(params, "conversation_id"))

        if action == "ide_command_list":
            return self._command_list()
        if action == "ide_command_execute":
            return self._ejecutar_comando(params)

        raise ValueError(f"acción IDE no soportada: {action!r}")

    def _cancel_running_agent_sessions(self, conversation_id: str) -> list[str]:
        """Mejor esfuerzo: cancela sesiones de agente vivas de una conversación.

        Usa solo la API pública de ``SessionManager`` (``list``/``close``),
        nunca sus internos -- ``ide_sessions.py`` es del otro agente que
        trabaja en paralelo ahora mismo y este runtime no lo edita. Las
        sesiones ya terminadas (completed/failed/cancelled) no se tocan aquí:
        su registro histórico sigue viviendo en ``ide-sessions.json`` incluso
        después de borrar la conversación. Purgarlas de verdad requeriría un
        método de borrado en ``SessionManager`` que hoy no existe; lo más
        limpio es que la fase siguiente lo agregue ahí junto con la UI que lo
        va a usar, no reconstruirlo a mano desde aquí.
        """
        closed: list[str] = []
        for row in self.sessions.list("agent").get("sessions", []):
            if row.get("conversation_id") != conversation_id:
                continue
            if row.get("status") not in {"starting", "running"}:
                continue
            self.sessions.close(str(row["id"]), "agent")
            closed.append(str(row["id"]))
        return closed

    def _delete_project(self, params: dict[str, Any]) -> dict[str, Any]:
        conversations_mode = str(params.get("conversations") or "keep")
        project_id = _required_text(params, "project_id")
        if conversations_mode == "delete":
            # Cascada: primero cortar cualquier sesión viva de cada
            # conversación afectada, y solo entonces borrar el registro.
            for row in self.projects.list_conversations(project_id):
                self._cancel_running_agent_sessions(str(row["id"]))
        return self.projects.delete_project(project_id, conversations=conversations_mode)  # type: ignore[arg-type]

    def _delete_conversation(self, conversation_id: str) -> dict[str, Any]:
        closed = self._cancel_running_agent_sessions(conversation_id)
        result = self.projects.delete_conversation(conversation_id)
        result["closed_session_ids"] = closed
        return result

    # ------------------------------------------------------------------ #
    # Menú de comandos "/" -- ver ``ide_comandos.py`` (registro y parser) y
    # los cuatro módulos de capacidad que construyó la misma corrida
    # (``ide_contexto``, ``ide_modos``, ``ide_sesion_extras``,
    # ``ide_acciones_codigo``). Este bloque es exclusivamente el CABLEADO:
    # ninguna de las funciones de abajo reimplementa lógica que ya vive en
    # esos módulos o en el resto de ``IDERuntime`` -- solo decide, para cada
    # nombre de comando, a qué llamada real corresponde y con qué
    # identificador (workspace/conversación/sesión de agente) se llama.
    #
    # Comandos cuya "capacidad" señalada en el registro vive fuera del
    # companion (servidor multi-tenant, paquetes que otra corrida tiene en
    # uso, o una dependencia pesada que no vale la pena traer para un IDE
    # local) devuelven un resultado "informativo": nunca inventan datos, solo
    # explican dónde sí está esa función hoy. Quedan listados en
    # ``_COMANDOS_INFORMATIVOS`` para que quede explícito cuáles son (y no se
    # confundan con un olvido).
    # ------------------------------------------------------------------ #

    def _command_list(self) -> dict[str, Any]:
        """``ide_command_list``: todo lo que necesita el menú "/" del
        compositor -- un registro por comando, más el texto de ``/help`` ya
        generado (ver ``ide_comandos.texto_ayuda``, siempre derivado del
        mismo registro, nunca escrito a mano en el frontend)."""
        comandos = [
            {
                "nombre": comando.nombre,
                "alias": list(comando.alias),
                "nombres": list(comando.nombres),
                "descripcion": comando.descripcion,
                "argumentos": comando.argumentos,
                "destructivo": comando.destructivo,
            }
            for comando in ide_comandos.listar_comandos()
        ]
        return {"comandos": comandos, "ayuda": ide_comandos.texto_ayuda()}

    def _gather_events(self, session_id: str, kind: str = "agent") -> list[dict[str, Any]]:
        """Junta TODAS las páginas de ``SessionManager.read`` de una sesión.

        ``ide_contexto``/``ide_sesion_extras`` esperan la lista completa de
        eventos (para ``/compact``, ``/btw``, ``/export``, ``/copy``); paginar
        es responsabilidad de quien integra, según el propio docstring de
        esos módulos -- este es exactamente ese bucle, en el único lugar que
        tiene acceso directo a ``SessionManager``.
        """
        eventos: list[dict[str, Any]] = []
        cursor = 0
        while True:
            pagina = self.sessions.read(session_id, kind, cursor)
            eventos.extend(pagina.get("events", []))
            next_cursor = pagina.get("next_cursor", cursor)
            if not pagina.get("has_more") or next_cursor == cursor:
                break
            cursor = next_cursor
        return eventos

    def _historial_conversacion(self, conversation_id: str) -> str | None:
        """Todo el texto real (lo pedido + lo respondido) de las sesiones de
        agente de ``conversation_id``, para que ``/branch`` sea una
        bifurcación de verdad y no "una conversación vacía con otro nombre".

        Una conversación puede haber tenido varias sesiones de agente a lo
        largo del tiempo (cada vez que ``ide_sessions`` no pudo reusar la
        anterior) -- se juntan TODAS, en orden de inicio, no solo la última.
        Devuelve ``None`` si la conversación todavía no tuvo ningún turno
        (nada que copiar), nunca una cadena vacía.
        """
        filas = [
            row
            for row in self.sessions.list("agent").get("sessions", [])
            if row.get("conversation_id") == conversation_id
        ]
        if not filas:
            return None
        filas.sort(key=lambda row: str(row.get("started_at") or ""))
        fragmentos: list[str] = []
        for fila in filas:
            for evento in self._gather_events(str(fila["id"])):
                tipo = evento.get("type")
                if tipo not in {"user", "assistant_final"}:
                    continue
                texto = str(evento.get("text") or "").strip()
                if texto:
                    fragmentos.append(f"[{tipo}] {texto}")
        if not fragmentos:
            return None
        recorte = "\n".join(fragmentos)[-_MAX_HISTORIAL_BIFURCACION_CHARS:]
        return (
            "Esta conversación es una bifurcación; abajo está el historial completo de "
            "la conversación original para que lo retomes. No hace falta que vuelvas a "
            "explicar lo ya dicho salvo que la petición de abajo dependa de algo que "
            "pudo haber cambiado.\n\n"
            "<historial_copiado>\n"
            f"{recorte}\n"
            "</historial_copiado>\n\n"
            "Continúa desde aquí: "
        )

    @staticmethod
    def _clave_contexto(
        conversation_id: str | None, workspace_id: str | None
    ) -> str:
        """A qué "sesión" (string opaco) atar el estado de ``/goal``,
        ``/effort``, ``/plan`` y el modo planificación -- la conversación si
        se conoce (persiste turno a turno, que es la granularidad correcta
        para un objetivo u nivel de esfuerzo), si no el workspace, si no un
        cajón compartido para cuando ninguno de los dos llegó todavía."""
        return conversation_id or workspace_id or "global"

    def _command_execution_context(
        self, session_id: str | None, *, para: str
    ) -> str:
        if not session_id:
            raise ide_comandos.IDEComandoError(
                f"'{para}' necesita una sesión de agente activa en esta conversación."
            )
        return session_id

    async def _responder_btw(self, pregunta: str, resumen_texto: str) -> str:
        """Llamada EFÍMERA a un modelo para responder ``/btw``: una sola
        ronda, sin herramientas, y sin que ninguna parte de esto (ni la
        pregunta ni la respuesta) llegue a ``Session.append`` -- ver el
        aviso de ``ide_contexto.preparar_contexto_btw`` sobre por qué esa
        garantía depende de quien integre este módulo, no del propio
        ``ide_contexto``. Deliberadamente NO reusa ``WorkersIDEAgent.run``:
        ese motor está pensado para un turno completo de ingeniería (system
        prompt largo, herramientas, checkpoints); esto es una nota al margen
        que debe responderse rápido y quedar fuera del hilo principal.
        """
        provider = WorkersAIProvider(model=MODELO_IDE_POR_DEFECTO)
        request = CompletionRequest(
            model=MODELO_IDE_POR_DEFECTO,
            system=(
                "Respondes una pregunta lateral y puntual sobre una conversación de "
                "ingeniería en curso, usando SOLO el resumen de contexto que te dan "
                "abajo. Sé breve y directo: esto es una nota al margen, no un nuevo "
                "turno del agente ni una invitación a actuar sobre el repo."
            ),
            messages=[
                ChatMessage(
                    role="user",
                    content=(
                        f"<contexto_de_la_conversacion>\n{resumen_texto}\n"
                        f"</contexto_de_la_conversacion>\n\nPregunta: {pregunta}"
                    ),
                )
            ],
            tools=[],
            max_tokens=1024,
            temperature=0.2,
        )
        partes: list[str] = []
        async for chunk in provider.stream(request):
            if chunk.type == "text" and chunk.text:
                partes.append(chunk.text)
        respuesta = "".join(partes).strip()
        if not respuesta:
            raise ValueError("El modelo no devolvió ninguna respuesta.")
        return respuesta

    async def _ejecutar_batch(
        self, workspace_id: str, plan_equipo: Any
    ) -> ResultadoEquipo:
        """Corre un ``ide_equipo.PlanEquipo`` YA VALIDADO (sin solapamientos)
        de verdad: un turno real y acotado de ``WorkersIDEAgent`` por
        sub-tarea, en paralelo hasta ``_BATCH_MAX_CONCURRENCIA`` a la vez.

        ``ide_equipo.py`` es deliberadamente autocontenido -- su propio
        docstring dice "quien lo conecte" decide el runner real; este es
        exactamente ese cableado, reusando ``self.sessions.workers_agent``
        (atributo PÚBLICO de ``SessionManager``, el mismo que
        ``ide_sessions._ejecutar_paso_de_plan`` usa para ejecutar los pasos
        de ``/plan``) en vez de abrir un segundo camino hacia Workers AI.
        """

        async def runner(sub: Subtarea, control: ControlEquipo) -> str:
            return await self._correr_subtarea_batch(workspace_id, sub, control)

        equipo = EquipoDeAgentes(runner=runner, max_concurrencia=_BATCH_MAX_CONCURRENCIA)
        return await equipo.ejecutar(plan_equipo)

    async def _correr_subtarea_batch(
        self, workspace_id: str, sub: Subtarea, control: ControlEquipo
    ) -> str:
        """Un turno normal de ``WorkersIDEAgent`` acotado al alcance de
        ``sub`` -- MISMO gate de herramientas peligrosas que cualquier otro
        turno (nunca recibe ``approved_tool_call_ids``): si la sub-tarea pide
        una, el turno se pausa sin tocar nada y esta función la cuenta como
        fallida, nunca como un atajo que se salte la confirmación humana
        dentro de un reparto en paralelo.
        """
        respuesta_final: str | None = None
        vio_confirmacion_pendiente = False

        def write_event(
            event_type: str,
            text: str,
            *,
            presentation: list[dict[str, Any]] | None = None,
        ) -> None:
            # ``presentation`` se ignora a propósito: una sub-tarea de /batch
            # no tiene hilo propio donde dibujar (solo devuelve su respuesta
            # final al agente que reparte). Aceptar el parámetro y descartarlo
            # es lo que hace que mostrar una tabla dentro de un reparto no
            # reviente el reparto entero -- ver el contrato de ``EventWriter``.
            nonlocal respuesta_final, vio_confirmacion_pendiente
            if event_type == "confirmation_required":
                vio_confirmacion_pendiente = True
            elif event_type == "assistant_final":
                respuesta_final = text

        alcance = ", ".join(sub.rutas)
        prompt = (
            "Estás ejecutando UNA sub-tarea de un reparto en paralelo (/batch); tu "
            f"alcance exclusivo son estos archivos/zonas: {alcance}. No toques nada "
            "fuera de ese alcance; si de verdad hace falta, dilo en tu respuesta "
            "final en vez de hacerlo.\n\n"
            f"Instrucciones: {sub.instrucciones}"
        )
        # `plan_store` y `session_id` van SIEMPRE, igual que en un turno normal
        # (`ide_sessions`, donde se pasan `self.plans` y `session.id`). Sin ellos,
        # `proponer_plan` encuentra `plan_store is None` y se degrada a un aviso
        # de texto -- "gestión de planes no disponible, procede con cuidado" -- y
        # el sub-agente sigue de largo escribiendo archivos y corriendo comandos
        # sin la pausa que un turno normal sí hace. O sea: pedir lo mismo por
        # `/batch` en vez de directo cambiaba el comportamiento, y justo en el
        # camino donde corren VARIOS agentes a la vez y es más difícil ver qué
        # pasó. Un reparto reparte el trabajo, no afloja las reglas.
        #
        # `session_id` es el de la sub-tarea, no el del reparto: cada una lleva su
        # propio plan, que es lo que hace que dos sub-agentes en paralelo no se
        # pisen el estado.
        await self.sessions.workers_agent.run(
            workspace_id=workspace_id,
            prompt=prompt,
            write_event=write_event,
            cancelled=control.cancelado,
            semantic=self.sessions.semantic,
            memoria=self.sessions.memoria,
            plan_store=self.sessions.plans,
            session_id=sub.id,
        )
        if vio_confirmacion_pendiente:
            raise RuntimeError(
                "Esta sub-tarea pidió una herramienta que necesita confirmación "
                "humana explícita; no se ejecutó nada. Resuélvelo por fuera del "
                "reparto y reintenta."
            )
        if respuesta_final is None:
            raise RuntimeError("La sub-tarea terminó sin una respuesta final.")
        return respuesta_final

    def _ejecutar_comando(self, params: dict[str, Any]) -> dict[str, Any]:
        """``ide_command_execute``: resuelve el texto "/..." y, si no hace
        falta confirmación adicional, ejecuta el comando de verdad.

        Contrato de ``params`` (todo menos ``text`` es opcional -- cada
        comando pide lo que en verdad necesita, no todos requieren los
        cuatro):
        - ``text``: la línea completa tal como se escribió ("/rename Foo").
        - ``workspace_id``, ``conversation_id``, ``project_id``: contexto de
          la UI en el momento de escribir el comando.
        - ``session_id``: la sesión de AGENTE activa (el turno en curso),
          para los comandos que operan sobre "el turno actual"
          (``/diff``, ``/cost``, ``/context``, ``/compact``, ``/btw``,
          ``/export``, ``/copy``, ``/background``, ``/simplify``).
        - ``confirmed``: ``true`` cuando la persona ya confirmó un comando
          destructivo (ver más abajo) -- de lo contrario, un comando
          destructivo nunca llega a ejecutarse en esta misma llamada.
        """
        texto = _required_text(params, "text")
        confirmado = _boolean(params, "confirmed", False)
        workspace_id = _optional_text(params, "workspace_id")
        conversation_id = _optional_text(params, "conversation_id")
        project_id = _optional_text(params, "project_id")
        session_id = _optional_text(params, "session_id")

        try:
            resuelto = ide_comandos.resolver_comando(texto)
        except ide_comandos.IDEComandoError as exc:
            # Comando desconocido o mal escrito: NO se re-lanza -- el
            # resultado ya trae ``.sugerencia`` (puede ser ``None``) para que
            # la UI muestre "¿Quisiste decir...?" sin tener que parsear el
            # mensaje de error.
            return {"ok": False, "error": str(exc), "sugerencia": exc.sugerencia}

        comando = resuelto.comando
        argumentos = resuelto.argumentos

        if comando.destructivo and not confirmado:
            return {
                "ok": False,
                "comando": comando.nombre,
                "requiere_confirmacion": True,
                "destructivo": True,
                "mensaje": (
                    f"'/{comando.nombre}' es una acción destructiva "
                    f"({comando.descripcion}). Confirma para continuar."
                ),
            }

        try:
            resultado = self._despachar_comando(
                comando.nombre,
                argumentos,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                project_id=project_id,
                session_id=session_id,
            )
        except ValueError as exc:
            # Cada módulo de capacidad conectado acá (``ide_comandos``,
            # ``ide_contexto``, ``ide_modos``, ``ide_sesion_extras``,
            # ``ide_acciones_codigo``, ``ide_plan``, ``ide_equipo``) y el
            # resto de servicios de ``IDERuntime`` (``ide_workspaces``,
            # ``ide_git``, ``ide_projects``, ``ide_sessions``,
            # ``ide_checkpoints``) declaran su excepción propia como
            # subclase de ``ValueError`` -- atraparla acá basta para que
            # CUALQUIER falla de un comando puntual quede con la misma forma
            # ``{"ok": false, "comando": ..., "error": ...}`` en vez de
            # escapar como fallo de toda la acción IDE.
            return {"ok": False, "comando": comando.nombre, "error": str(exc)}

        resultado.setdefault("ok", True)
        resultado.setdefault("comando", comando.nombre)
        return resultado

    def _informativo(self, mensaje: str) -> dict[str, Any]:
        """Resultado de un comando cuya capacidad real vive fuera del
        alcance de este companion local (servidor multi-tenant, paquete que
        otra corrida tiene en uso, o dependencia pesada que no amerita traer
        acá) -- ``limitado=True`` para que la UI lo distinga visualmente de
        un resultado ejecutado de verdad, en vez de darlo a entender como uno
        más."""
        return {"mensaje": mensaje, "limitado": True}

    def _despachar_comando(  # noqa: C901 - un switch por comando es lo más legible acá
        self,
        nombre: str,
        argumentos: str | None,
        *,
        workspace_id: str | None,
        conversation_id: str | None,
        project_id: str | None,
        session_id: str | None,
    ) -> dict[str, Any]:
        clave = self._clave_contexto(conversation_id, workspace_id)

        if nombre == "help":
            listado = self._command_list()
            return {"mensaje": listado["ayuda"], "data": listado}

        if nombre == "clear":
            conversacion = self.projects.create_conversation(project_id, argumentos)
            return {
                "mensaje": f"Conversación nueva: «{conversacion['title']}».",
                "data": {"conversation": conversacion},
                "nueva_conversacion_id": conversacion["id"],
            }

        if nombre == "rename":
            if not conversation_id:
                raise ide_comandos.IDEComandoError(
                    "'/rename' necesita una conversación activa."
                )
            conversacion = self.projects.rename_conversation(conversation_id, argumentos)
            return {
                "mensaje": f"Conversación renombrada a «{conversacion['title']}».",
                "data": {"conversation": conversacion},
            }

        if nombre == "branch":
            if not conversation_id:
                raise ide_comandos.IDEComandoError(
                    "'/branch' necesita una conversación activa."
                )
            original = self.projects.get_conversation(conversation_id)
            titulo = argumentos or f"{original['title']} (bifurcada)"
            bifurcada = self.projects.create_conversation(original.get("project_id"), titulo)
            historial = self._historial_conversacion(conversation_id)
            resultado: dict[str, Any] = {
                "data": {
                    "conversation": bifurcada,
                    "origen_conversation_id": conversation_id,
                    "historial_copiado": historial is not None,
                },
                "nueva_conversacion_id": bifurcada["id"],
            }
            if historial is None:
                resultado["mensaje"] = (
                    f"Bifurcada como «{bifurcada['title']}»; «{original['title']}» todavía no "
                    "tiene historial (nada que copiar)."
                )
                return resultado
            resultado["mensaje"] = (
                f"Bifurcada como «{bifurcada['title']}» con el historial de "
                f"«{original['title']}» copiado."
            )
            # No se auto-envía (ver ``/review``/``/simplify`` para el patrón
            # contrario): esto es CONTEXTO para retomar, no una instrucción
            # puntual -- la persona decide qué pedir a continuación y lo
            # manda ella misma, con el historial ya listo delante en el
            # compositor.
            resultado["prefill_prompt"] = historial
            return resultado

        if nombre == "resume":
            filas = [
                row
                for row in self.sessions.list("agent", workspace_id).get("sessions", [])
                if row.get("status") not in {"starting", "running"}
            ]
            filas.sort(key=lambda row: str(row.get("started_at") or ""), reverse=True)
            if not argumentos:
                return {
                    "mensaje": (
                        f"{len(filas)} sesión(es) reciente(s) para retomar."
                        if filas
                        else "No hay sesiones anteriores para retomar en este workspace."
                    ),
                    "data": {"sesiones": filas[:10]},
                }
            objetivo = argumentos.strip()
            if not any(str(row.get("id")) == objetivo for row in filas):
                raise ide_comandos.IDEComandoError(
                    f"No se encontró la sesión «{objetivo}» para retomar."
                )
            return {
                "mensaje": "Sesión retomada.",
                "data": {"session_id": objetivo},
                "reanudar_session_id": objetivo,
            }

        if nombre == "model":
            from edecan_llm.task_router import modelos_ide_disponibles

            modelos = modelos_ide_disponibles()
            if not argumentos:
                return {
                    "mensaje": f"{len(modelos)} modelo(s) disponible(s) para el IDE.",
                    "data": {"modelos": modelos},
                }
            elegido = argumentos.strip()
            if not any(str(fila.get("id")) == elegido for fila in modelos):
                disponibles = ", ".join(str(fila.get("id")) for fila in modelos) or (
                    "(ninguno configurado)"
                )
                raise ide_comandos.IDEComandoError(
                    f"Modelo desconocido: «{elegido}». Disponibles: {disponibles}."
                )
            return {
                "mensaje": f"Modelo activo: {elegido}.",
                "data": {"modelos": modelos},
                "set_model": elegido,
            }

        if nombre == "effort":
            if not argumentos:
                nivel = self.effort_store.obtener(clave)
                return {
                    "mensaje": f"Nivel de esfuerzo actual: {nivel.nombre}.",
                    "data": {"nivel": nivel.nombre, "reasoning_effort": nivel.reasoning_effort},
                }
            nivel = self.effort_store.fijar(clave, argumentos)
            return {
                "mensaje": f"Nivel de esfuerzo cambiado a: {nivel.nombre}.",
                "data": {"nivel": nivel.nombre, "reasoning_effort": nivel.reasoning_effort},
            }

        if nombre == "goal":
            if not argumentos:
                activo = self.goal_store.get_active_for_session(clave)
                if activo is None:
                    return {"mensaje": "No hay ningún objetivo activo en esta conversación."}
                return {
                    "mensaje": f"Objetivo activo: {activo.descripcion}",
                    "data": activo.public(),
                }
            partes = [p.strip() for p in argumentos.split("|") if p.strip()]
            if len(partes) < 2:
                raise ide_comandos.IDEComandoError(
                    "'/goal' necesita la descripción y al menos un criterio de éxito, "
                    "separados por '|': /goal <descripción> | <criterio 1> | <criterio 2>"
                )
            objetivo = self.goal_store.set_goal(clave, partes[0], partes[1:])
            return {
                "mensaje": f"Objetivo definido: {objetivo.descripcion}",
                "data": objetivo.public(),
            }

        if nombre == "plan":
            if argumentos and argumentos.strip().casefold() in {"aprobar", "approve"}:
                activo = self.plan_store.get_active_for_session(clave)
                if activo is None:
                    raise ide_comandos.IDEComandoError("No hay ningún plan propuesto que aprobar.")
                plan = self.plan_store.approve(activo.id)
                self.plan_mode_store.sincronizar_con_plan(plan, clave)
                return {"mensaje": "Plan aprobado; ejecutando paso a paso.", "data": plan.public()}
            if argumentos and argumentos.strip().casefold() in {"cancelar", "cancel"}:
                activo = self.plan_store.get_active_for_session(clave)
                if activo is None:
                    raise ide_comandos.IDEComandoError("No hay ningún plan activo que cancelar.")
                plan = self.plan_store.cancel(activo.id)
                self.plan_mode_store.sincronizar_con_plan(None, clave)
                return {"mensaje": "Plan cancelado.", "data": plan.public()}
            if not argumentos:
                activo = self.plan_store.get_active_for_session(clave)
                if activo is None:
                    return {"mensaje": "No hay ningún plan activo en esta conversación."}
                return {"mensaje": f"Plan activo: {activo.goal}", "data": activo.public()}
            partes = [p.strip() for p in argumentos.split("|") if p.strip()]
            if len(partes) < 2:
                raise ide_comandos.IDEComandoError(
                    "'/plan' necesita la meta y al menos un paso, separados por '|': "
                    "/plan <meta> | <paso 1> | <paso 2>"
                )
            plan = self.plan_store.propose(clave, partes[0], partes[1:])
            self.plan_mode_store.sincronizar_con_plan(plan, clave)
            return {
                "mensaje": f"Plan propuesto ({len(plan.steps)} paso(s)); pendiente de aprobación.",
                "data": plan.public(),
            }

        if nombre == "context":
            self._command_execution_context(session_id, para="/context")
            registros = ide_contexto.leer_bitacora(self.config.config_path.parent)
            resumen = ide_contexto.analizar_contexto(registros)
            return {"mensaje": "Uso de contexto calculado.", "data": resumen.resumen()}

        if nombre == "debug":
            registros = ide_contexto.leer_bitacora(self.config.config_path.parent)
            return {
                "mensaje": f"{len(registros)} llamada(s) registrada(s) en la bitácora local.",
                "data": {"llamadas": registros[-50:]},
            }

        if nombre == "compact":
            sid = self._command_execution_context(session_id, para="/compact")
            eventos = self._gather_events(sid)
            resumen = ide_contexto.compactar(eventos, objetivo_pendiente=argumentos)
            return {"mensaje": resumen.resumen_texto, "data": resumen.resumen()}

        if nombre == "btw":
            sid = self._command_execution_context(session_id, para="/btw")
            if not argumentos:
                raise ide_comandos.IDEComandoError("'/btw' necesita una pregunta.")
            eventos = self._gather_events(sid)
            contexto = ide_contexto.preparar_contexto_btw(eventos, argumentos)
            try:
                respuesta = asyncio.run(
                    self._responder_btw(contexto.pregunta, contexto.resumen_texto)
                )
            except Exception as exc:  # noqa: BLE001 - una llamada efímera rota no tumba el comando
                raise ide_comandos.IDEComandoError(
                    f"No se pudo responder '/btw': {_safe_error(exc)}"
                ) from exc
            return {
                "mensaje": respuesta,
                "data": {**contexto.resumen(), "respuesta": respuesta},
            }

        if nombre == "background":
            sid = self._command_execution_context(session_id, para="/background")
            tarea = ide_sesion_extras.enviar_a_segundo_plano(
                self.sessions, self.background_store, sid, "agent"
            )
            return {
                "mensaje": f"«{tarea.title}» sigue en segundo plano.",
                "data": {
                    "id": tarea.id,
                    "kind": tarea.kind,
                    "status": tarea.status,
                    "en_segundo_plano": tarea.en_segundo_plano,
                },
            }

        if nombre == "tasks":
            solo_activas = not (argumentos and argumentos.strip().casefold() in {"todas", "all"})
            tareas = ide_sesion_extras.listar_tareas(
                self.sessions, self.background_store, solo_activas=solo_activas
            )
            filas = [
                {
                    "id": t.id,
                    "kind": t.kind,
                    "workspace_name": t.workspace_name,
                    "title": t.title,
                    "status": t.status,
                    "en_segundo_plano": t.en_segundo_plano,
                    "elapsed_s": t.elapsed_s,
                }
                for t in tareas
            ]
            return {"mensaje": f"{len(filas)} tarea(s).", "data": {"tareas": filas}}

        if nombre == "export":
            sid = self._command_execution_context(session_id, para="/export")
            sesion = self.sessions.list("agent").get("sessions", [])
            fila = next((row for row in sesion if str(row.get("id")) == sid), None)
            if fila is None:
                raise ide_comandos.IDEComandoError("Sesión no encontrada para exportar.")
            eventos = self._gather_events(sid)
            markdown = ide_sesion_extras.exportar_markdown(fila, eventos)
            nombre_archivo = f"{fila.get('title') or 'sesion'}.md".replace("/", "-")
            return {
                "mensaje": "Exportación lista para descargar.",
                "data": {"markdown": markdown},
                "download": {"filename": nombre_archivo, "content": markdown},
            }

        if nombre == "copy":
            sid = self._command_execution_context(session_id, para="/copy")
            eventos = self._gather_events(sid)
            texto = ide_sesion_extras.copiar_ultima_respuesta(eventos)
            return {
                "mensaje": "Última respuesta lista para copiar.",
                "copy_text": texto,
            }

        if nombre == "permissions":
            if not argumentos:
                politica = self.permissions_store.politica_actual()
                return {
                    "mensaje": "Política de permisos actual (todo lo que falta pide confirmación).",
                    "data": {
                        "politica": politica,
                        "descripciones": ide_sesion_extras.CATEGORIA_DESCRIPCION,
                        "historial": self.permissions_store.historial()[-20:],
                    },
                }
            partes = argumentos.split(maxsplit=1)
            if len(partes) != 2 or partes[1].strip().casefold() not in {"on", "off"}:
                raise ide_comandos.IDEComandoError(
                    "'/permissions' se usa como: /permissions <categoria> on|off "
                    f"(categorías: {', '.join(ide_sesion_extras.CATEGORIAS)})"
                )
            categoria, valor = partes[0].strip(), partes[1].strip().casefold()
            cambio = (
                self.permissions_store.permitir_automatico(categoria)
                if valor == "on"
                else self.permissions_store.exigir_confirmacion(categoria)
            )
            estado = "corre automático" if cambio.automatico else "pide confirmación"
            return {
                "mensaje": f"'{categoria}' ahora {estado}.",
                "data": {"politica": self.permissions_store.politica_actual()},
            }

        if nombre == "config":
            return {
                "mensaje": (
                    "Cambiar la configuración todavía no está disponible desde el chat; "
                    "usa Ajustes. Resumen de solo lectura:"
                ),
                "data": {
                    "ide_habilitado": self.config.ide_enabled,
                    "workspaces_autorizados": len(self.workspaces.list()),
                },
                "limitado": True,
            }

        if nombre == "diff":
            sid = self._command_execution_context(session_id, para="/diff")
            return {"mensaje": "Diff del turno calculado.", "data": self.sessions.turn_diff(sid)}

        if nombre == "cost":
            sid = self._command_execution_context(session_id, para="/cost")
            return {"mensaje": "Costo del turno calculado.", "data": self.sessions.turn_cost(sid)}

        if nombre == "rewind":
            if not workspace_id:
                raise ide_comandos.IDEComandoError("'/rewind' necesita un workspace activo.")
            if not argumentos:
                raise ide_comandos.IDEComandoError(
                    "'/rewind' necesita un id de checkpoint (o 'ultimo' para el más reciente)."
                )
            objetivo = argumentos.strip()
            if objetivo.casefold() in {"ultimo", "último", "last"}:
                disponibles = self.sessions.checkpoints.list(workspace_id)
                if not disponibles:
                    raise ide_comandos.IDEComandoError(
                        "No hay ningún checkpoint vigente en este workspace."
                    )
                objetivo = disponibles[0]["id"]
            resultado = self.sessions.checkpoints.restore(objetivo)
            return {
                "mensaje": (
                    f"{len(resultado['restored'])} archivo(s) restaurado(s), "
                    f"{len(resultado['deleted'])} eliminado(s)."
                    + (
                        f" {len(resultado['conflicts'])} en conflicto (sin tocar)."
                        if resultado["conflicts"]
                        else ""
                    )
                ),
                "data": resultado,
            }

        if nombre == "review":
            if not workspace_id:
                raise ide_comandos.IDEComandoError("'/review' necesita un workspace activo.")
            rutas = (
                [p.strip() for p in argumentos.split(",") if p.strip()] if argumentos else None
            )
            revision = _preparar_revision(self.git, workspace_id, paths=rutas)
            return {
                "mensaje": "Diff pendiente listo para que el agente lo comente.",
                "data": {"diff_texto": revision.diff_texto, "truncado": revision.truncado},
                "prefill_prompt": revision.as_prompt_block(),
                "auto_send": True,
            }

        if nombre == "security-review":
            if not workspace_id:
                raise ide_comandos.IDEComandoError(
                    "'/security-review' necesita un workspace activo."
                )
            ruta = argumentos.strip() if argumentos else "."
            resultado = asyncio.run(_auditar_seguridad(self.workspaces, workspace_id, ruta=ruta))
            return {"mensaje": resultado["content"], "data": resultado.get("data") or {}}

        if nombre == "doctor":
            return {
                "mensaje": self._diagnostico_local(workspace_id),
                "data": self._salud_local(workspace_id),
            }

        if nombre == "init":
            if not workspace_id:
                raise ide_comandos.IDEComandoError("'/init' necesita un workspace activo.")
            palabras_overwrite = {"overwrite", "sobrescribir"}
            overwrite = bool(argumentos and argumentos.strip().casefold() in palabras_overwrite)
            resultado = _escribir_agents_md(self.workspaces, workspace_id, overwrite=overwrite)
            if not resultado["escrito"]:
                return {
                    "mensaje": (
                        f"Ya existe un archivo de reglas ({resultado['archivo_existente']}); "
                        "usa '/init overwrite' para reemplazarlo."
                    ),
                    "data": resultado,
                }
            lenguajes = ", ".join(resultado["lenguajes"])
            return {"mensaje": f"AGENTS.md generado ({lenguajes}).", "data": resultado}

        if nombre == "simplify":
            sid = self._command_execution_context(session_id, para="/simplify")
            diff_turno = self.sessions.turn_diff(sid)
            checkpoint_id = diff_turno.get("checkpoint_id")
            if not checkpoint_id:
                raise ide_comandos.IDEComandoError(
                    "Este turno todavía no registró ningún checkpoint; no hay nada que simplificar."
                )
            plan_equipo = _preparar_simplificacion(
                self.sessions.checkpoints, checkpoint_id, instrucciones=argumentos
            )
            subtarea = plan_equipo.subtareas[0]
            rutas_texto = ", ".join(subtarea.rutas)
            return {
                "mensaje": "Pidiendo una pasada de simplificación sobre lo que se acaba de tocar.",
                "data": {"rutas": list(subtarea.rutas)},
                "prefill_prompt": f"{subtarea.instrucciones}\n\nArchivos: {rutas_texto}",
                "auto_send": True,
            }

        if nombre == "agents":
            return {
                "mensaje": (
                    "Especificación del sub-agente de reparto "
                    "(ya disponible para el agente principal)."
                ),
                "data": especificacion_herramienta(),
            }

        if nombre == "batch":
            if not workspace_id:
                raise ide_comandos.IDEComandoError("'/batch' necesita un workspace activo.")
            if not argumentos:
                raise ide_comandos.IDEComandoError(
                    "'/batch' necesita al menos una sub-tarea: "
                    "/batch ruta1: instrucciones 1; ruta2: instrucciones 2"
                )
            segmentos = [s.strip() for s in argumentos.split(";") if s.strip()]
            subtareas: list[Subtarea] = []
            for indice, segmento in enumerate(segmentos):
                if ":" not in segmento:
                    raise ide_comandos.IDEComandoError(
                        f"Sub-tarea {indice + 1} mal formada (falta ':'): {segmento!r}"
                    )
                rutas_texto, instrucciones = segmento.split(":", 1)
                rutas = tuple(r.strip() for r in rutas_texto.split(",") if r.strip())
                subtareas.append(
                    Subtarea(
                        id=f"batch-{indice}",
                        titulo=f"Sub-tarea {indice + 1}",
                        instrucciones=instrucciones.strip(),
                        rutas=rutas,
                    )
                )
            # ``construir_plan`` rechaza (``EquipoError``, subclase de
            # ``ValueError``) un reparto solapado ANTES de lanzar un solo
            # sub-agente -- ver el propio docstring de ``ide_equipo``: mejor
            # no arrancar nada que arrancar con dos procesos pisándose el
            # mismo archivo.
            plan_equipo = construir_plan(subtareas)
            resultado = asyncio.run(self._ejecutar_batch(workspace_id, plan_equipo))
            return {
                "mensaje": resultado.resumen(),
                "data": {
                    "estados": {
                        id_: {
                            "titulo": estado.titulo,
                            "estado": estado.estado,
                            "salida": estado.salida,
                            "error": estado.error,
                            "duracion_s": estado.duracion_s,
                        }
                        for id_, estado in resultado.estados.items()
                    },
                    "cancelado": resultado.cancelado,
                },
            }

        if nombre in {"usage", "memory", "mcp", "voice", "remote-control", "workflows"}:
            return self._informativo(_MENSAJE_INFORMATIVO[nombre])

        raise ide_comandos.IDEComandoError(f"'/{nombre}' no tiene una ejecución conectada todavía.")

    def _salud_local(self, workspace_id: str | None) -> dict[str, Any]:
        """``/doctor``: diagnóstico local liviano y de solo lectura.

        Nota de alcance: el registro de comandos apunta ``/doctor`` a la tool
        ``diagnosticar_autorreparacion_local`` de ``edecan_toolkit``, pero esa
        tool exige un fallo puntual ya ocurrido (``intencion_original``,
        ``fallo_reportado``) para poder recomendar una vía -- no existe eso
        todavía cuando la persona simplemente escribe "/doctor" sin haber
        visto un error primero. Inventar un fallo para poder llamarla sería
        peor que no llamarla. Este chequeo cubre en su lugar lo que SÍ se
        puede verificar sin ese contexto: que el IDE local está habilitado y
        que el workspace activo es legible/escribible.
        """
        datos: dict[str, Any] = {
            "ide_habilitado": self.config.ide_enabled,
            "workspaces_autorizados": len(self.workspaces.list()),
        }
        if workspace_id:
            try:
                root = self.workspaces.root(workspace_id)
                datos["workspace_existe"] = root.is_dir()
                datos["workspace_escribible"] = os.access(root, os.W_OK)
                try:
                    self.git.status(workspace_id)
                    datos["es_repo_git"] = True
                except IDEGitError:
                    datos["es_repo_git"] = False
            except IDEWorkspaceError as exc:
                datos["workspace_error"] = str(exc)
        return datos

    def _diagnostico_local(self, workspace_id: str | None) -> str:
        datos = self._salud_local(workspace_id)
        lineas = [
            "IDE local: " + ("habilitado" if datos["ide_habilitado"] else "deshabilitado"),
            f"Workspaces autorizados: {datos['workspaces_autorizados']}",
        ]
        if "workspace_existe" in datos:
            lineas.append(
                "Workspace activo: "
                + ("accesible" if datos["workspace_existe"] else "NO accesible")
                + (", escribible" if datos.get("workspace_escribible") else ", solo lectura")
                + (", con Git" if datos.get("es_repo_git") else ", sin Git")
            )
        if datos.get("workspace_error"):
            lineas.append(f"Workspace: {datos['workspace_error']}")
        return "\n".join(lineas)


def _runtime_for(config: CompanionConfig) -> IDERuntime:
    key = str(config.config_path.expanduser().resolve())
    with _RUNTIMES_LOCK:
        runtime = _RUNTIMES.get(key)
        if runtime is None:
            runtime = IDERuntime(config)
            _RUNTIMES[key] = runtime
        return runtime


def _shutdown_runtimes() -> None:
    with _RUNTIMES_LOCK:
        runtimes = list(_RUNTIMES.values())
    for runtime in runtimes:
        runtime.sessions.shutdown()


atexit.register(_shutdown_runtimes)


async def execute_ide_action(
    action: str,
    raw_params: Any,
    config: CompanionConfig,
    approver: Approver,
) -> dict[str, Any]:
    """Ejecuta una acción ``ide_*`` con aprobación y auditoría local."""

    if action not in IDE_ACTIONS:
        return {"ok": False, "error": f"acción IDE no soportada: {action!r}"}
    if not config.ide_enabled:
        return {"ok": False, "error": "El IDE local está deshabilitado en esta computadora."}
    if raw_params is None:
        params: dict[str, Any] = {}
    elif isinstance(raw_params, dict):
        params = dict(raw_params)
    else:
        return {"ok": False, "error": "params debe ser un objeto JSON."}

    approved = True
    safe_params = _safe_params(params)
    try:
        if action in _APPROVAL_ACTIONS:
            try:
                approved = await approver(action, safe_params, config)
            except Exception as exc:
                error = f"No se pudo obtener la aprobación local: {_safe_error(exc)}"
                audit.log_action(
                    action=action,
                    params=safe_params,
                    approved=False,
                    ok=False,
                    error=error,
                    log_path=config.audit_log_path,
                )
                return {"ok": False, "error": error}
            if not approved:
                error = "Acción rechazada por el dueño de esta computadora."
                audit.log_action(
                    action=action,
                    params=safe_params,
                    approved=False,
                    ok=False,
                    error=error,
                    log_path=config.audit_log_path,
                )
                return {"ok": False, "error": error}

        runtime = _runtime_for(config)
        result = await asyncio.to_thread(runtime.dispatch, action, params)
        audit.log_action(
            action=action,
            params=safe_params,
            approved=approved,
            ok=True,
            log_path=config.audit_log_path,
        )
        return {"ok": True, "result": result}
    except (
        IDEWorkspaceError,
        IDECloneError,
        IDEFileError,
        IDESessionError,
        IDEGitError,
        IDEProjectError,
        ValueError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        error = _safe_error(exc)
        audit.log_action(
            action=action,
            params=safe_params,
            approved=approved,
            ok=False,
            error=error,
            log_path=config.audit_log_path,
        )
        return {"ok": False, "error": error}
