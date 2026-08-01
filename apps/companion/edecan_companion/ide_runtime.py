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

import httpx
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
from edecan_companion.ide_opencode_lsp import ClienteLspOpencode, LspOpencodeNoDisponibleError
from edecan_companion.ide_plan import PlanStore
from edecan_companion.ide_projects import IDEProjectError, ProjectRegistry
from edecan_companion.ide_referencias import ReferenceService
from edecan_companion.ide_sessions import IDESessionError, SessionManager
from edecan_companion.ide_workspaces import (
    IDEWorkspaceError,
    WorkspaceStore,
    pick_workspace_folder,
)
from edecan_companion.preparacion import EjecutorPreparacion

# Comandos cuya capacidad real vive fuera de lo que este companion local
# puede alcanzar hoy -- verificado ruta por ruta contra el propio repo (no
# supuesto), así que cada mensaje apunta al lugar EXACTO, no a un genérico.
#
# Dato importante encontrado al verificar (fuera de mi alcance tocar, pero
# hace falta para no mentir en el docstring): ``apps/api/edecan_api/routers/
# ide.py::post_command_execute`` YA intercepta ``/usage``, ``/memory``
# (cuenta) y ``/mcp``/``/voice`` ANTES de reenviar nada a este companion --
# los resuelve server-side con datos reales del tenant
# (``_ejecutar_usage_servidor``/``_ejecutar_memory_servidor``/
# ``_ejecutar_mcp_servidor``/``_ejecutar_voice_servidor``) y NUNCA los manda
# por ``ide_command_execute``. O sea: cuando alguien los escribe desde el IDE
# en la web, este diccionario de acá abajo ni siquiera se ejecuta para esos
# tres (usage/mcp/voice) -- solo aplica si algo llama a
# ``ide_command_execute`` en este companion directo, sin pasar por esa API
# (verificación aislada de este comando, u otro cliente futuro). Por eso el
# mensaje de cada uno igual apunta al lugar real, no a un genérico:
# - ``/usage``: el consumo del plan lo calcula el servidor multi-tenant
#   (``apps/api``), no este companion -- se ve en vivo en la pantalla "Panel"
#   (``/app/panel``, `apps/web/src/app/(app)/app/panel/page.tsx`) y el plan en
#   sí en "Facturación" (``/app/facturacion``).
# - ``/mcp``: los servidores MCP conectados los administra
#   ``apps/api/edecan_api/routers/mcp.py`` (``/v1/mcp/servers``), no el
#   companion -- este solo EJECUTA las herramientas que ya le llegan armadas
#   en ``mcp_tools`` durante un turno (ver ``ide_agent_mcp_pending`` /
#   ``ide_agent_mcp_resolve`` más abajo, que resuelven una llamada puntual en
#   curso, no administran el listado). Se administra en Ajustes > Conexiones,
#   tarjeta "Herramientas externas (MCP)" (``/app/ajustes#conexiones``,
#   `apps/web/src/components/configuracion/CardServidoresMcp.tsx`).
# - ``/voice``: la síntesis/transcripción de voz corre server-side
#   (``packages/voice``), el companion no sintetiza audio -- se conecta y
#   prueba en la misma pantalla de Conexiones, tarjeta "Voz"
#   (``/app/ajustes#conexiones``).
# - ``/remote-control``: SÍ existe control remoto real en este companion
#   (``actions.py``, captura de pantalla + teclado/mouse gateados por
#   ``config.remote_input_enabled``), pero se activa como una sesión en vivo
#   con su propio consentimiento desde su propia pantalla dedicada
#   (``/app/remoto``, grupo "Herramientas técnicas" -- NO está bajo Ajustes),
#   nunca desde un comando de una línea de chat. A diferencia de
#   usage/mcp/voice, ``post_command_execute`` SÍ reenvía este comando tal
#   cual al companion -- este mensaje es el que de verdad ve la persona.
# - ``/workflows``: "flujos de trabajo guardados" son las Automatizaciones de
#   la cuenta (trigger + acción + corridas, `apps/api/edecan_api/routers/
#   automations.py`, tabla `automations`/`automation_runs`), no las Agent
#   Skills de ``/app/skills`` (esas son instrucciones en lenguaje natural que
#   el agente sigue, otro concepto -- ver `docs/skills.md`). Se administran
#   en "Automatizaciones" (``/app/automatizaciones``), servidor multi-tenant,
#   fuera del alcance de este companion local. Igual que ``/remote-control``,
#   ``post_command_execute`` reenvía este comando al companion tal cual.
#
# ``/memory`` NO está en este diccionario: la memoria por WORKSPACE que el
# agente de código guarda de este proyecto (distinta de la memoria de CUENTA
# de ``/app/memoria``/``GET /v1/memory``, otro producto -- WhatsApp/
# ``edecan_core.memory`` -- que es la que ``post_command_execute``
# intercepta bajo el mismo nombre "/memory" antes de llegar aquí) SÍ vive en
# este companion (``self.sessions.memoria``, ``ide_memoria.MemoriaStore``) y
# ya tiene acciones reales cableadas (``ide_memory_list``/
# ``ide_memory_forget``, la misma fuente que el botón "Memoria y
# conocimiento" del IDE en la web, ``GET``/``DELETE
# /workspaces/{id}/memory`` -- una superficie REST aparte, no
# ``/commands/execute``) -- así que ``/memory`` se resuelve en su propio
# bloque en ``_despachar_comando``, no aquí. Si algo llama a
# ``ide_command_execute`` directo en este companion (bypaseando la
# interceptación de la API), "/memory" aquí devuelve la memoria del
# WORKSPACE, no la de cuenta -- deliberado (es la única de las dos que este
# companion puede alcanzar), pero distinto de lo que ve la persona en el IDE
# web normal. Documentado para que nadie lo confunda con un bug.
_MENSAJE_INFORMATIVO: dict[str, str] = {
    "usage": (
        "El consumo del plan lo calcula el servidor, no este companion: revísalo en "
        "Panel (/app/panel) y gestiona el plan en Facturación (/app/facturacion)."
    ),
    "mcp": (
        "Los servidores MCP conectados se administran en Ajustes > Conexiones, "
        "tarjeta «Herramientas externas (MCP)» (/app/ajustes#conexiones); este comando "
        "no administra ese listado, solo ejecuta las herramientas ya conectadas durante "
        "un turno."
    ),
    "voice": (
        "La voz (transcripción y síntesis) se conecta y prueba en Ajustes > Conexiones, "
        "tarjeta «Voz» (/app/ajustes#conexiones); este companion no sintetiza audio."
    ),
    "remote-control": (
        "El control remoto de este equipo sí existe en este companion, pero se activa "
        "como sesión en vivo con su propio consentimiento desde su propia pantalla, "
        "«Control remoto» (/app/remoto); este comando de chat no lo activa."
    ),
    "workflows": (
        "Los flujos de trabajo guardados son las Automatizaciones de la cuenta "
        "(/app/automatizaciones) -- trigger, acción y corridas en el servidor "
        "multi-tenant, fuera del alcance de este companion local. No confundir con "
        "«Skills» (/app/skills), que son instrucciones para el agente, no flujos "
        "disparados por evento."
    ),
}

# Avisos de ``ide_lsp_symbols``/``ide_lsp_status`` cuando el resultado viene
# vacío -- ver el docstring de ``ide_opencode_lsp.py`` ("El hallazgo que
# manda"), verificado con un servidor de lenguaje real (pyright) funcionando:
# la superficie HTTP pública de ``opencode serve`` no tiene ninguna ruta que
# le pida "abrí/indexá este archivo" antes de reportar símbolos o servidores
# conectados, así que ``[]`` es el resultado esperado casi siempre, NO
# evidencia de "no hay servidor instalado" ni de "símbolo inexistente". Un
# ``[]`` mudo aquí sería exactamente el patrón que el dueño lleva encontrando
# en este proyecto -- por eso ambas acciones anexan este aviso cuando el
# resultado viene vacío en vez de dejar que la interfaz lo lea como éxito.
_AVISO_LSP_SIMBOLOS_VACIO = (
    "Sin resultados. Esto no significa que el símbolo no exista: la API pública de "
    "opencode no tiene ninguna forma de pedirle que abra/indexe archivos antes de "
    "buscar, así que una búsqueda de símbolos casi siempre viene vacía hoy, incluso "
    "con un servidor de lenguaje instalado y funcionando."
)
_AVISO_LSP_ESTADO_VACIO = (
    "No se reporta ningún servidor de lenguaje conectado. Esto no distingue \"este "
    "proyecto no tiene un servidor de lenguaje instalado\" de \"opencode nunca llegó a "
    "intentar levantar uno\": ambos casos son indistinguibles con la API pública actual "
    "de opencode serve."
)

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
        "ide_agent_question_list",
        "ide_agent_question_answer",
        "ide_agent_question_reject",
        "ide_agent_permission_list",
        "ide_agent_permission_answer",
        "ide_agent_diff",
        "ide_agent_diff_reject",
        "ide_agent_cost",
        "ide_plan_approve",
        "ide_plan_edit",
        "ide_plan_reject",
        "ide_modo_get",
        "ide_modo_set",
        "ide_agent_model_set",
        "ide_plan_active",
        "ide_plan_resume",
        "ide_reference_search",
        "ide_lsp_symbols",
        "ide_lsp_status",
        "ide_lsp_definition",
        "ide_lsp_references",
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
        "ide_preparacion_list",
        "ide_preparacion_instalar",
        "ide_preparacion_leer",
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
        # Ejecuta un instalador real (winget / Set-ItemProperty en el
        # registro) -- mismo nivel de impacto que abrir una terminal, así que
        # exige la misma aprobación humana en el camino remoto (el puente
        # local del escritorio la sigue auto-concediendo, ver
        # `companion_bridge._LOCAL_IDE_ACTIONS`).
        "ide_preparacion_instalar",
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


def _required_integer(params: dict[str, Any], key: str) -> int:
    """Como ``_integer`` pero sin default -- para ``line``/``character`` de
    posiciones LSP, donde ``0`` es un valor válido y no debe confundirse con
    "no vino" (a diferencia de ``_integer``, que no puede distinguir los dos
    casos sin un default explícito)."""
    value = params.get(key)
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
    def __init__(self, config: CompanionConfig, *, motor: str | None = None) -> None:
        self.config = config
        state_dir = config.config_path.parent / "ide"
        self.workspaces = WorkspaceStore(state_dir)
        self.files = FileService(self.workspaces)
        # ``motor``: ``None`` (por defecto) deja que ``SessionManager`` lea
        # ``EDECAN_IDE_MOTOR`` (opencode salvo que se pida "viejo" -- ver su
        # docstring); un valor explícito acá lo fija sin tocar la variable
        # de entorno del proceso, útil para pruebas.
        self.sessions = SessionManager(state_dir, self.workspaces, motor=motor)
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
        # MISMA instancia que ``self.sessions.effort_store``, no una segunda:
        # el ciclo real del agente (``ide_sessions._run_workers_agent`` /
        # ``_ejecutar_paso_de_plan``) lee de ahí en cada vuelta, y si acá se
        # creara un ``EsfuerzoStore`` propio, ``/effort`` desde la interfaz
        # cambiaría un almacén que el agente nunca consulta -- la UI diría
        # "cambiado" y el modelo seguiría razonando con el nivel de antes.
        self.effort_store = self.sessions.effort_store
        self.plan_mode_store = ide_modos.ModoPlanificacionStore()
        self.plan_store = PlanStore()
        self.background_store = ide_sesion_extras.SegundoPlanoStore(state_dir)
        self.permissions_store = ide_sesion_extras.PermissionsStore(state_dir)
        # Pantalla de preparación de Windows (docs/edecan-windows.md §9/§10):
        # no depende de ningún workspace -- corre ANTES de que exista uno.
        self.preparacion = EjecutorPreparacion()

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
        # Modo y esfuerzo de la sesion. Los stores viven en `ide_modos.py` desde
        # hace tiempo (`EsfuerzoStore`, `ModoPlanificacionStore`) pero no tenian
        # cable: se podian fijar por comando de chat (`/effort`, `/plan`) y no
        # desde la interfaz. Esto los expone.
        if action == "ide_modo_get":
            sid = _required_text(params, "session_id")
            nivel = self.effort_store.obtener(sid)
            # ``modo`` (manual/aceptar_ediciones/plan/auto -- ver
            # ``ide_modos.ModoAgenteStore``) es distinto de ``modo_plan``
            # (arriba, ``ModoPlanificacionStore``): ese es el "no toques
            # archivos, solo propón" del motor VIEJO; este es el que ahora
            # alimenta de verdad a ``PuenteDePermisos`` del motor opencode
            # (docs/opencode-motor.md, encargo punto 6). Los dos coexisten
            # a propósito -- ver el docstring de ``SessionManager.set_modo_agente``.
            modo_agente = self.sessions.modo_agente_store.public(sid)
            return {
                "modo_plan": self.plan_mode_store.esta_activo(sid),
                "esfuerzo": nivel.nombre,
                "reasoning_effort": nivel.reasoning_effort,
                "niveles": list(ide_modos.NIVELES_ESFUERZO),
                "modo": modo_agente["modo"],
                "modo_motivo": modo_agente["motivo"],
                "modos_disponibles": [m.value for m in ide_modos.ModoAgente],
            }
        if action == "ide_modo_set":
            sid = _required_text(params, "session_id")
            resultado: dict[str, Any] = {}
            esfuerzo = params.get("esfuerzo")
            if esfuerzo is not None:
                if not isinstance(esfuerzo, str):
                    raise IDESessionError("El nivel de esfuerzo no es válido.")
                # Vía ``SessionManager.fijar_esfuerzo`` (no
                # ``self.effort_store.fijar`` directo): si la sesión ya
                # tiene una sesión de opencode viva, esto además cambia el
                # modelo EN VIVO (encargo punto 5 -- "eso ES el control de
                # esfuerzo"), no solo al próximo turno.
                nivel = self.sessions.fijar_esfuerzo(sid, esfuerzo)
                resultado["esfuerzo"] = nivel.nombre
                resultado["reasoning_effort"] = nivel.reasoning_effort
            modo_plan = params.get("modo_plan")
            if modo_plan is not None:
                if not isinstance(modo_plan, bool):
                    raise IDESessionError("«modo_plan» debe ser true o false.")
                if modo_plan:
                    motivo = params.get("motivo")
                    self.plan_mode_store.activar(
                        sid, motivo if isinstance(motivo, str) and motivo.strip() else None
                    )
                else:
                    self.plan_mode_store.salir(sid)
                resultado["modo_plan"] = modo_plan
            modo = params.get("modo")
            if modo is not None:
                if not isinstance(modo, str):
                    raise IDESessionError("«modo» debe ser texto.")
                motivo_modo = params.get("motivo")
                modo_publico = self.sessions.set_modo_agente(
                    sid,
                    modo,
                    motivo_modo if isinstance(motivo_modo, str) and motivo_modo.strip() else None,
                )
                resultado["modo"] = modo_publico["modo"]
                resultado["modo_motivo"] = modo_publico["motivo"]
            if not resultado:
                raise IDESessionError("Indica «esfuerzo», «modo_plan» o «modo».")
            return resultado
        # Hecho 2 del encargo: `SessionManager.set_modelo_agente` ya existía y
        # aplicaba el cambio de modelo EN VIVO (mismo patrón que
        # `set_modo_agente` arriba) pero no estaba en `IDE_ACTIONS` -- el
        # selector de la interfaz solo podía guardar la preferencia para el
        # arranque, nunca empujarla a una sesión ya viva. La validación contra
        # el catálogo (`modelo_ide_permitido`) vive DENTRO de
        # `set_modelo_agente`, así que acá no se duplica -- solo se deja pasar
        # el `IDESessionError` que ya lanza si el modelo no está permitido.
        if action == "ide_agent_model_set":
            return self.sessions.set_modelo_agente(
                _required_text(params, "session_id"),
                _required_text(params, "model"),
            )
        # `get_active_plan` se escribió literalmente "para que la UI sepa si
        # debe mostrar la tarjeta de aprobación" (ver su docstring) y nunca
        # tuvo cable: por eso un plan propuesto quedaba invisible en la
        # conversación aunque el companion lo tuviera vivo.
        if action == "ide_plan_active":
            return self.sessions.get_active_plan(_required_text(params, "session_id"))
        if action == "ide_plan_resume":
            # `resume_plan` retoma EL ULTIMO plan de la sesión: no recibe
            # `plan_id` porque la sesión solo puede tener uno vivo a la vez.
            return self.sessions.resume_plan(_required_text(params, "session_id"))
        if action == "ide_plan_approve":
            return self.sessions.approve_plan(
                _required_text(params, "session_id"),
                _required_text(params, "plan_id"),
            )
        if action == "ide_plan_edit":
            pasos = params.get("steps")
            if not isinstance(pasos, list) or not all(isinstance(x, str) for x in pasos):
                raise IDESessionError("Los pasos del plan editado no son válidos.")
            return self.sessions.edit_plan(
                _required_text(params, "session_id"),
                _required_text(params, "plan_id"),
                pasos,
            )
        if action == "ide_plan_reject":
            motivo = params.get("reason")
            return self.sessions.reject_plan(
                _required_text(params, "session_id"),
                _required_text(params, "plan_id"),
                motivo if isinstance(motivo, str) and motivo.strip() else None,
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
        # Encargo punto 7: exponer lo que el agente le pregunta a la persona
        # (``question.v2.asked`` de opencode, ver ``PuenteDePermisos`` /
        # ``ide_opencode_eventos.traducir_pregunta``) -- nunca respondido
        # por el companion en nombre de nadie, solo listado/reenviado.
        if action == "ide_agent_question_list":
            return self.sessions.listar_preguntas_agente(_required_text(params, "session_id"))
        if action == "ide_agent_question_answer":
            respuestas_raw = params.get("respuestas")
            if not isinstance(respuestas_raw, list) or not all(
                isinstance(fila, list) and all(isinstance(x, str) for x in fila)
                for fila in respuestas_raw
            ):
                raise IDESessionError(
                    "«respuestas» debe ser una lista de listas de texto (una por pregunta)."
                )
            return self.sessions.responder_pregunta_agente(
                _required_text(params, "session_id"),
                _required_text(params, "request_id"),
                respuestas_raw,
            )
        if action == "ide_agent_question_reject":
            return self.sessions.rechazar_pregunta_agente(
                _required_text(params, "session_id"),
                _required_text(params, "request_id"),
            )
        # Cierra el fallo real que un verificador reprodujo en vivo (modo
        # Manual, ver docstring de ``SessionManager._consumir_turno_opencode``):
        # antes de esta ronda no había NINGUNA acción para conceder/rechazar
        # un permiso (``permission.v2.asked``) pedido a mitad de turno, así
        # que el propio bucle lo rechazaba solo y la única forma de avanzar
        # era abandonar el turno y mandar uno nuevo en modo Auto. Mismo
        # patrón que las tres acciones de preguntas de arriba.
        if action == "ide_agent_permission_list":
            return self.sessions.listar_permisos_agente(_required_text(params, "session_id"))
        if action == "ide_agent_permission_answer":
            conceder = params.get("conceder")
            if not isinstance(conceder, bool):
                raise ValueError("conceder debe ser true o false.")
            recordar = _boolean(params, "recordar", False)
            mensaje = _optional_text(params, "mensaje")
            return self.sessions.responder_permiso_agente(
                _required_text(params, "session_id"),
                _required_text(params, "request_id"),
                conceder=conceder,
                recordar=recordar,
                mensaje=mensaje,
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

        # ``ide_opencode_lsp.ClienteLspOpencode`` ya envolvía las rutas LSP de
        # opencode (construido, verificado contra un ``opencode serve`` real, y
        # sin cable -- ver su docstring). Esto es ese cable: cuatro acciones,
        # las cuatro con el nombre y los parámetros que ese cliente de verdad
        # soporta, ni uno inventado. Ver los métodos ``_lsp_*`` más abajo para
        # el detalle de cada una (incluido por qué ``definition``/``references``
        # SIEMPRE fallan con una explicación real, nunca datos simulados).
        if action == "ide_lsp_symbols":
            return asyncio.run(
                self._lsp_buscar_simbolos(
                    _required_text(params, "workspace_id"), _required_text(params, "query")
                )
            )
        if action == "ide_lsp_status":
            return asyncio.run(self._lsp_estado(_required_text(params, "workspace_id")))
        if action == "ide_lsp_definition":
            return asyncio.run(
                self._lsp_definicion(
                    _required_text(params, "workspace_id"),
                    _required_text(params, "path"),
                    _required_integer(params, "line"),
                    _required_integer(params, "character"),
                )
            )
        if action == "ide_lsp_references":
            return asyncio.run(
                self._lsp_referencias(
                    _required_text(params, "workspace_id"),
                    _required_text(params, "path"),
                    _required_integer(params, "line"),
                    _required_integer(params, "character"),
                )
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

        if action == "ide_preparacion_list":
            return self.preparacion.listar()
        if action == "ide_preparacion_instalar":
            return self.preparacion.instalar(_required_text(params, "id"))
        if action == "ide_preparacion_leer":
            return self.preparacion.leer(
                _required_text(params, "id"), _integer(params, "cursor", 0)
            )

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
    # LSP (``ide_opencode_lsp.ClienteLspOpencode``) -- ver el bloque de
    # ``dispatch`` de arriba y el docstring de ese módulo para la evidencia
    # completa de qué SÍ y qué NO expone opencode por HTTP.
    # ------------------------------------------------------------------ #

    async def _lsp_servidor_para(self, workspace_id: str) -> Any:
        """El ``ServidorOpencode`` vivo de ``workspace_id`` -- MISMA instancia
        (o una arrancada igual) que ``SessionManager`` usa para las sesiones de
        agente de ese workspace, nunca un proceso ``opencode serve`` propio de
        este módulo (ver "Dónde engancharlo" en el docstring de
        ``ide_opencode_lsp.py``).

        ``self.sessions.motor_opencode`` es ``None`` cuando esta instalación
        corre el motor VIEJO (``EDECAN_IDE_MOTOR`` distinto de opencode, ver
        ``SessionManager.__init__``/``_asegurar_motor_opencode``): ese motor no
        habla el protocolo HTTP de opencode, así que no hay ningún
        ``ServidorOpencode`` que ofrecerle a ``ClienteLspOpencode`` -- se avisa
        con un error real (``IDESessionError``, la misma familia que ya maneja
        ``execute_ide_action``) en vez de fingir un resultado vacío.
        """
        if self.sessions.motor_opencode is None:
            raise IDESessionError(
                "El LSP solo está disponible con el motor opencode; esta instalación "
                "todavía corre el motor anterior."
            )
        workspace_root = self.workspaces.root(workspace_id)
        return await self.sessions.motor_opencode.servidor_para(str(workspace_root))

    async def _lsp_buscar_simbolos(self, workspace_id: str, query: str) -> dict[str, Any]:
        """``ide_lsp_symbols`` -- ``ClienteLspOpencode.buscar_simbolos``
        (``GET /find/symbol``), ver ``_AVISO_LSP_SIMBOLOS_VACIO`` para por qué
        un resultado vacío no es "no se encontró"."""
        servidor = await self._lsp_servidor_para(workspace_id)
        async with ClienteLspOpencode.creado_desde(servidor) as cliente:
            simbolos = await cliente.buscar_simbolos(query)
        resultado: dict[str, Any] = {
            "simbolos": [simbolo.model_dump(mode="json") for simbolo in simbolos]
        }
        if not simbolos:
            resultado["aviso"] = _AVISO_LSP_SIMBOLOS_VACIO
        return resultado

    async def _lsp_estado(self, workspace_id: str) -> dict[str, Any]:
        """``ide_lsp_status`` -- ``ClienteLspOpencode.estado_lsp``
        (``GET /lsp``): qué servidores de lenguaje reporta opencode para este
        workspace. Es la herramienta para explicar un ``ide_lsp_symbols``
        vacío -- ver ``_AVISO_LSP_ESTADO_VACIO``."""
        servidor = await self._lsp_servidor_para(workspace_id)
        async with ClienteLspOpencode.creado_desde(servidor) as cliente:
            estados = await cliente.estado_lsp()
        resultado: dict[str, Any] = {
            "servidores": [estado.model_dump(mode="json") for estado in estados]
        }
        if not estados:
            resultado["aviso"] = _AVISO_LSP_ESTADO_VACIO
        return resultado

    def _lsp_cliente_sin_servidor(self) -> ClienteLspOpencode:
        """Un ``ClienteLspOpencode`` que NUNCA hace red -- para
        ``definicion()``/``referencias()``, que lanzan
        ``LspOpencodeNoDisponibleError`` antes de tocar ``self._cliente`` (ver
        su código en ``ide_opencode_lsp.py``: ningún ``_solicitar`` de por
        medio). Arrancar un ``opencode serve`` real solo para una llamada que
        de todas formas SIEMPRE falla sería pagar el costo (varios segundos,
        un proceso nuevo) de una capacidad que la propia superficie pública de
        opencode no ofrece -- ver el docstring del módulo, puntos 3-4."""
        return ClienteLspOpencode(puerto=0, cliente=httpx.AsyncClient())

    async def _lsp_definicion(
        self, workspace_id: str, path: str, line: int, character: int
    ) -> dict[str, Any]:
        """``ide_lsp_definition`` -- valida el workspace (mismo error real que
        cualquier otra acción ``ide_*`` si no existe/no está autorizado) y
        luego deja que ``ClienteLspOpencode.definicion`` lance
        ``LspOpencodeNoDisponibleError`` con su explicación real: opencode
        1.17.18 no expone ``textDocument/definition`` por HTTP bajo ninguna
        ruta (ver el docstring del módulo, punto 3). Nunca llega a devolver un
        valor -- ``execute_ide_action`` traduce esa excepción a un HTTP con
        significado, no a datos inventados."""
        self.workspaces.root(workspace_id)
        cliente = self._lsp_cliente_sin_servidor()
        try:
            await cliente.definicion(path=path, line=line, character=character)
        finally:
            await cliente.cerrar()
        raise AssertionError(  # pragma: no cover - cliente.definicion siempre lanza
            "ClienteLspOpencode.definicion() debía haber lanzado LspOpencodeNoDisponibleError."
        )

    async def _lsp_referencias(
        self, workspace_id: str, path: str, line: int, character: int
    ) -> dict[str, Any]:
        """``ide_lsp_references`` -- mismo criterio que ``_lsp_definicion``,
        para ``textDocument/references`` (docstring del módulo, punto 4: OJO,
        ``GET /api/reference`` SÍ existe pero es un concepto no relacionado --
        catálogo de documentos de referencia del proyecto, no referencias de
        código -- por eso no se usa acá)."""
        self.workspaces.root(workspace_id)
        cliente = self._lsp_cliente_sin_servidor()
        try:
            await cliente.referencias(path=path, line=line, character=character)
        finally:
            await cliente.cerrar()
        raise AssertionError(  # pragma: no cover - cliente.referencias siempre lanza
            "ClienteLspOpencode.referencias() debía haber lanzado LspOpencodeNoDisponibleError."
        )

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
        self,
        workspace_id: str,
        plan_equipo: Any,
        nivel_esfuerzo: ide_modos.NivelEsfuerzo | None,
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

        ``nivel_esfuerzo`` es el nivel vigente de la conversación que pidió
        ``/batch``, YA leído por el caller (``_ejecutar_comando``) en el
        instante del reparto -- cada sub-tarea arranca con ESE nivel (ver
        ``_correr_subtarea_batch``). Un sub-agente que ya está corriendo no
        vuelve a consultarlo: si la persona cambia ``/effort`` mientras un
        ``/batch`` sigue en vuelo, las sub-tareas YA lanzadas no se enteran
        (no comparten sesión con la conversación, así que no hay un "entre
        vueltas" de la conversación que las alcance).
        """

        async def runner(sub: Subtarea, control: ControlEquipo) -> str:
            return await self._correr_subtarea_batch(workspace_id, sub, control, nivel_esfuerzo)

        equipo = EquipoDeAgentes(runner=runner, max_concurrencia=_BATCH_MAX_CONCURRENCIA)
        return await equipo.ejecutar(plan_equipo)

    async def _correr_subtarea_batch(
        self,
        workspace_id: str,
        sub: Subtarea,
        control: ControlEquipo,
        nivel_esfuerzo: ide_modos.NivelEsfuerzo | None,
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
        # pisen el estado. Por esa misma razón, heredar el nivel de ``/effort``
        # de la conversación no es automático (``self.sessions.effort_store``
        # no tiene entrada para ``sub.id`` todavía) -- hay que sembrarla acá,
        # ANTES de arrancar el turno, con el nivel que ``nivel_esfuerzo`` trae
        # desde la conversación real.
        #
        # ``effort_store`` solo se pasa cuando SÍ hay un nivel real que
        # heredar. Pasarlo siempre y dejar que ``sub.id`` caiga al default del
        # store (``EsfuerzoStore.obtener`` -> "medio") sería la regresión
        # silenciosa que la regla 6 del encargo prohíbe: hoy, sin ningún
        # cable, un ``/batch`` corre fijo en "alto"; con el store puesto pero
        # sin sembrar caería a "medio" sin que nadie lo pidiera. Sin
        # ``nivel_esfuerzo`` (comando ``/batch`` sin ``session_id``), no se
        # pasa el store y el turno cae al mismo "alto"/8192 fijo de siempre
        # (ver el ``else`` del gate en ``WorkersIDEAgent.run``).
        if nivel_esfuerzo is not None:
            self.sessions.effort_store.fijar(sub.id, nivel_esfuerzo.nombre)
        await self.sessions.workers_agent.run(
            workspace_id=workspace_id,
            prompt=prompt,
            write_event=write_event,
            cancelled=control.cancelado,
            semantic=self.sessions.semantic,
            memoria=self.sessions.memoria,
            plan_store=self.sessions.plans,
            session_id=sub.id,
            effort_store=self.sessions.effort_store if nivel_esfuerzo is not None else None,
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
          ``/export``, ``/copy``, ``/background``, ``/simplify``). ``/batch``
          también lo usa, pero solo para LEER el nivel de ``/effort``
          vigente y sembrarlo en cada sub-tarea -- opcional, no revienta si
          falta (ver el bloque ``if nombre == "batch"``).
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
            # ``ide_acciones_codigo``, ``ide_plan``, ``ide_equipo``,
            # ``ide_memoria`` -- ``/memory``) y el resto de servicios de
            # ``IDERuntime`` (``ide_workspaces``, ``ide_git``, ``ide_projects``,
            # ``ide_sessions``, ``ide_checkpoints``) declaran su excepción
            # propia como
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
            # Bug real encontrado corriendo este comando contra un
            # ``opencode serve`` de verdad (no al leer el código): usaba
            # ``clave`` (``_clave_contexto`` -- conversation_id o
            # workspace_id) como si fuera el ``session_id`` de un agente.
            # ``EsfuerzoStore`` SIEMPRE se lee/escribe con el ``session.id``
            # real en todos los demás sitios que importan de verdad --
            # ``ide_sessions.py`` al arrancar CADA turno
            # (``self.effort_store.obtener(session.id)``), ``ide_modo_set``
            # (``sid = _required_text(params, "session_id")``) y
            # ``SessionManager.fijar_esfuerzo`` (que busca la sesión viva en
            # ``self._sessions.get(session_id)`` para aplicar el cambio EN
            # VIVO). Como ``clave`` es un id de CONVERSACIÓN, nunca coincide
            # con ninguna clave de ``self._sessions`` -- así que
            # ``fijar_esfuerzo`` no encontraba la sesión, el bloque "en vivo"
            # nunca corría, y el nivel quedaba guardado bajo una llave que
            # ningún turno real vuelve a leer jamás. El comando devolvía
            # ``ok: true`` igual: parecía funcionar (leía de vuelta lo mismo
            # que acababa de escribir) pero no cambiaba nada real. Mismo
            # requisito que ``/context``/``/compact``: hace falta una sesión
            # de agente activa para que "el esfuerzo de ESE agente" tenga
            # sentido.
            sid = self._command_execution_context(session_id, para="/effort")
            if not argumentos:
                nivel = self.effort_store.obtener(sid)
                return {
                    "mensaje": f"Nivel de esfuerzo actual: {nivel.nombre}.",
                    "data": {"nivel": nivel.nombre, "reasoning_effort": nivel.reasoning_effort},
                }
            # Vía ``SessionManager.fijar_esfuerzo`` (no ``self.effort_store``
            # directo): sincroniza el modelo EN VIVO si esta sesión ya tiene
            # una sesión de opencode -- ver el mismo comentario en
            # ``ide_modo_set``.
            nivel = self.sessions.fijar_esfuerzo(sid, argumentos)
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
            # Comprobado corriendo este comando contra un ``opencode serve``
            # de verdad: ``self.plan_store``/``self.plan_mode_store`` son
            # SEGUIMIENTO local puro -- ningún motor los consulta para
            # decidir si el agente puede tocar archivos. ``_run_opencode_agent``
            # (``ide_sessions.py``) nunca los importa; y bajo el motor viejo
            # tampoco: ``WorkersIDEAgent.run`` recibe su PROPIO
            # ``plan_store`` (``self.sessions.plans``, una instancia
            # DISTINTA que solo el agente llena solo cuando ÉL MISMO decide
            # que un lote de pasos lo amerita, vía
            # ``ide_plan.requires_plan``) -- nunca el que llena este comando
            # a mano. Antes de esta ronda el mensaje decía "ejecutando paso a
            # paso" al aprobar, dando a entender que el agente quedaba
            # frenado hasta la aprobación -- falso en los dos motores. Regla
            # 4 del encargo ("un comando roto no puede seguir ofreciéndose
            # como si tal"): se deja el seguimiento (sigue siendo útil como
            # bitácora de pasos para la persona) pero el mensaje ya no
            # insinúa una ejecución real, y ``aviso`` lo deja explícito para
            # quien integre esto en la UI. Quien quiera de verdad frenar al
            # agente hasta revisar tiene ``/permissions`` para lo destructivo
            # o el modo "Plan" del selector de modo (acción
            # ``ide_modo_set`` con ``modo: "plan"``, que sí lo hace vía
            # ``ModoAgenteStore``/``PuenteDePermisos``).
            aviso_plan = (
                "Este plan es solo seguimiento local: no bloquea ni ordena lo que el "
                "agente ejecuta. Para que el agente de verdad se quede en solo-lectura "
                "hasta que lo autorices, usa el modo «Plan» del selector de modo."
            )
            if argumentos and argumentos.strip().casefold() in {"aprobar", "approve"}:
                activo = self.plan_store.get_active_for_session(clave)
                if activo is None:
                    raise ide_comandos.IDEComandoError("No hay ningún plan propuesto que aprobar.")
                plan = self.plan_store.approve(activo.id)
                self.plan_mode_store.sincronizar_con_plan(plan, clave)
                return {
                    "mensaje": "Plan aprobado (seguimiento local; no frena al agente).",
                    "data": plan.public(),
                    "aviso": aviso_plan,
                }
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
                "mensaje": (
                    f"Plan propuesto ({len(plan.steps)} paso(s)); pendiente de aprobación "
                    "(seguimiento local, no frena al agente)."
                ),
                "aviso": aviso_plan,
                "data": plan.public(),
            }

        if nombre == "context":
            self._command_execution_context(session_id, para="/context")
            registros = ide_contexto.leer_bitacora(self.config.config_path.parent)
            resumen = ide_contexto.analizar_contexto(registros)
            resultado_context: dict[str, Any] = {
                "mensaje": "Uso de contexto calculado.",
                "data": resumen.resumen(),
            }
            if not registros:
                # Comprobado corriendo este comando contra un turno real (sobre
                # opencode y sobre el motor viejo): ``llm-calls.jsonl``
                # (``edecan_core.llm_call_log``) NUNCA se llena para un turno
                # del IDE en NINGUNO de los dos motores -- ``log_llm_call`` solo
                # lo llama ``edecan_core.agent`` (el agente de WhatsApp/teléfono,
                # un módulo sin relación), ni ``WorkersIDEAgent.run`` ni
                # ``ide_sessions._turno_opencode`` lo tocan. El "0 llamadas" de
                # antes daba a entender "todavía no, pero ya viene"; la verdad
                # es que esta bitácora nunca reflejará un turno del IDE hoy.
                resultado_context["aviso"] = (
                    "La bitácora local de llamadas al modelo no registra turnos del IDE "
                    "en ningún motor (ni opencode ni el viejo) -- esto no cuenta cuánto "
                    "contexto usó de verdad este turno, aunque el turno haya hecho "
                    "llamadas reales."
                )
            return resultado_context

        if nombre == "debug":
            registros = ide_contexto.leer_bitacora(self.config.config_path.parent)
            resultado_debug: dict[str, Any] = {
                "mensaje": f"{len(registros)} llamada(s) registrada(s) en la bitácora local.",
                "data": {"llamadas": registros[-50:]},
            }
            if not registros:
                resultado_debug["aviso"] = (
                    "Esta bitácora no registra turnos del IDE en ningún motor -- ver el "
                    "mismo aviso de '/context'."
                )
            return resultado_debug

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
            # Nivel de ``/effort`` vigente de ESTA conversación, leído en el
            # instante del reparto -- cada sub-tarea arranca con él (ver
            # ``_ejecutar_batch``/``_correr_subtarea_batch``). Sin
            # ``session_id`` (comando disparado sin contexto de sesión) no hay
            # nada que heredar; las sub-tareas caen al fijo de siempre.
            nivel_esfuerzo = self.effort_store.obtener(session_id) if session_id else None
            resultado = asyncio.run(
                self._ejecutar_batch(workspace_id, plan_equipo, nivel_esfuerzo)
            )
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

        if nombre == "memory":
            # Cableado real (no informativo): ``self.sessions.memoria`` es la
            # MISMA ``ide_memoria.MemoriaStore`` que ya usan las acciones
            # ``ide_memory_list``/``ide_memory_forget`` y que alimenta el
            # botón "Memoria y conocimiento" del IDE en la web
            # (`apps/web/src/app/(app)/app/ide/page.tsx`) -- memoria por
            # WORKSPACE de lo que el agente de código recuerda de ESTE
            # proyecto, no la memoria de cuenta de ``/app/memoria`` (otro
            # producto, fuera del alcance del companion). Comprobado contra
            # un workspace real: sin recuerdos guardados todavía devuelve
            # lista vacía, no un error -- ``list_notes``/``forget`` de
            # ``ide_memoria.py`` no distinguen "workspace sin memoria" de
            # "workspace con memoria", así que el mensaje lo deja explícito
            # para que no se lea como una falla.
            if not workspace_id:
                raise ide_comandos.IDEComandoError("'/memory' necesita un workspace activo.")
            if not argumentos:
                notas = self.sessions.memoria.list_notes(workspace_id)
                mensaje_memoria = (
                    f"{len(notas)} recuerdo(s) guardado(s) en este workspace."
                    if notas
                    else "Todavía no hay memoria guardada en este workspace."
                )
                return {"mensaje": mensaje_memoria, "data": {"notas": notas}}
            partes_memoria = argumentos.split(maxsplit=1)
            verbo_memoria = partes_memoria[0].strip().casefold()
            if verbo_memoria in {"olvidar", "forget"} and len(partes_memoria) > 1:
                nota_id = partes_memoria[1].strip()
                resultado_forget = self.sessions.memoria.forget(workspace_id, nota_id)
                return {"mensaje": "Recuerdo olvidado.", "data": resultado_forget}
            raise ide_comandos.IDEComandoError(
                "'/memory' se usa como: /memory (lista lo guardado) o "
                "/memory olvidar <id> (borra un recuerdo puntual)."
            )

        if nombre in {"usage", "mcp", "voice", "remote-control", "workflows"}:
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
    except LspOpencodeNoDisponibleError as exc:
        # Distinto de los demás fallos de abajo a propósito: esto NO es "la
        # acción falló esta vez" (permiso denegado, workspace inexistente,
        # timeout) -- es "esta capacidad no existe en la superficie pública de
        # opencode 1.17.18, verificado, nunca va a funcionar hasta que
        # opencode cambie" (ver ``ide_opencode_lsp.py``, puntos 3-4). El campo
        # ``lsp_no_disponible`` deja que la capa HTTP (``routers/ide.py``)
        # responda con un código con ese significado (501) en vez del 422
        # genérico de "el companion rechazó la acción".
        error = _safe_error(exc)
        audit.log_action(
            action=action,
            params=safe_params,
            approved=approved,
            ok=False,
            error=error,
            log_path=config.audit_log_path,
        )
        return {"ok": False, "error": error, "lsp_no_disponible": True}
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
