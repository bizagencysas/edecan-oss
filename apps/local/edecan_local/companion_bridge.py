"""Puente entre la instalación local, el IDE y el control remoto móvil.

En el producto instalado no existe un segundo "companion" que la persona
deba ejecutar o emparejar: ``edecan_local`` ya corre dentro de Edecán y es el
agente de esta computadora. Este puente registra sus acciones directamente en
``ConnectionManager`` cuando se crea/recupera el dueño local.

El QR continúa siendo la credencial del teléfono. Una sesión remota exige la
confirmación explícita en el teléfono y el backend valida tenant, sesión y
flags antes de llegar aquí. Las acciones del IDE ya llegan desde rutas
autenticadas y gateadas del API; el puente las aprueba dentro del proceso local
porque la app instalada no tiene una segunda terminal donde preguntar. Las
acciones históricas conservan ``sandbox_dir``/``allowed_commands``; el runtime
nuevo exige ``ide_enabled``, un workspace autorizado y auditoría local.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from edecan_companion import actions
from edecan_companion.config import CompanionConfig, load_config
from edecan_companion.ide_runtime import IDE_ACTIONS, execute_ide_action

logger = logging.getLogger(__name__)

_REMOTE_ACTIONS = frozenset(
    {
        "screenshot",
        "input_pointer",
        "input_key",
        # Portapapeles y transferencia de archivos compartidos entre el
        # teléfono y esta computadora dentro de una sesión de control remoto
        # (mismo requisito de `session_id` que el resto — ver `approve`).
        # No tocan TCC ni el bridge nativo: corren en el sidecar.
        "clipboard_get",
        "clipboard_set",
        "transfer_push",
        "transfer_list",
        "transfer_pull",
    }
)
_PERSONAL_MAC_ACTIONS = frozenset(
    {
        "mac_mail_accounts",
        "mac_mail_search",
        "mac_mail_send",
        "mac_contacts_search",
        "mac_messages_recent",
        "mac_messages_send",
    }
)
# Superficie exacta de `routers/ide.py`.
_LEGACY_LOCAL_IDE_ACTIONS = frozenset(
    {
        "list_tree",
        "search_files",
        "apply_edit",
        "read_file",
        "write_file",
        "run_command",
    }
)
# Esta Mac es del dueño. Desde el chat de escritorio Edecán debe poder abrir
# apps, URLs, capturar pantalla y usar teclado/mouse sin un segundo
# emparejamiento ni sesión remota del teléfono.
_DESKTOP_OWNER_ACTIONS = frozenset(
    {
        "open_app",
        "open_url",
        "read_dir",
        "read_file",
        "write_file",
        "trash_path",
        "clipboard_get",
        "clipboard_set",
        "run_command",
        "screenshot",
        "input_pointer",
        "input_key",
    }
)
_DEFAULT_LOCAL_COMMANDS = (
    "open",
    "osascript",
    "screencapture",
    "ls",
    "cat",
    "head",
    "tail",
    "find",
    "grep",
    "mkdir",
    "cp",
    "mv",
    "rm",
    "say",
    "curl",
    "python3",
    "git",
    "npm",
    "node",
    "shortcuts",
    "defaults",
    "textutil",
    "sips",
    "date",
    "whoami",
    "pwd",
    "echo",
)
_LOCAL_IDE_ACTIONS = _LEGACY_LOCAL_IDE_ACTIONS | IDE_ACTIONS
_LOCAL_ACTIONS = (
    _REMOTE_ACTIONS | _LOCAL_IDE_ACTIONS | _PERSONAL_MAC_ACTIONS | _DESKTOP_OWNER_ACTIONS
)

# Portapapeles/teclado/mouse del dueño: solo los toca una sesión remota
# autorizada (que SIEMPRE manda `session_id` en los params, ver
# `routers/remote.py`). Un bot/worker no manda `session_id`, así que cualquier
# llamada a estas acciones sin sesión es un bot tocando el equipo del dueño —
# se deniega. `screenshot` queda FUERA a propósito: el chat del dueño (no bot)
# la usa legítimamente sin sesión vía `_DESKTOP_OWNER_ACTIONS` (ver el test
# `test_desktop_owner_can_capture_screen_without_phone_session`) y en el
# puente no hay forma de distinguir al dueño de un bot en esa llamada.
_SIN_SESION_BLOQUEADAS = frozenset(
    {
        "clipboard_get",
        "clipboard_set",
        "input_pointer",
        "input_key",
    }
)

# Comandos de terminal que NUNCA se ejecutan, vengan de donde vengan (bot o
# dueño): el patrón es más destructivo que útil y un bot comprometido los
# usaría para dañar la Mac. Denylist por subcadena (fail-closed: un falso
# positivo cuesta una negativa, un falso negativo cuesta la máquina). El caso
# `osascript` exige las TRES subcadenas: `osascript` solo (p.ej. para abrir
# apps) sigue permitido, la combinación con `tell application` + `do shell` es
# el clásico escalado de privilegios vía AppleScript.
_PATRONES_PELIGROSOS_RUN_COMMAND: tuple[str, ...] = (
    "rm -rf", "rm -fr", "rm -r -f", "rm -r - f",
    "sudo", "; rm", "&& rm", "|| rm",
    "dd if", "dd of", ">/dev/sda",
    "chmod -R 777", "chmod 777 -R",
    "passwd", "shutdown", "reboot",
    "find / -delete", "diskutil erase",
    "| bash", "| zsh", "| /bin/sh", "| /bin/bash",
)
# Pipe de `curl` a `sh` (`curl -fsSL https://x | sh`): la subcadena literal
# "curl | sh" NO la cubre (el flag y la URL van en medio), así que se exige
# "curl" presente + un pipe a `sh` como palabra (`\b` evita falsos positivos
# como "| sha256sum").
_PIPE_CURL_A_SH = re.compile(r"\|\s*sh\b")


def _comando_peligroso(command: str) -> bool:
    # Portapapeles: un bot no debe leer ni escribir el clipboard
    for _cmd_clip in ("pbpaste", "pbcopy"):
        if _cmd_clip in command:
            return True

    """True si `command` no debe correr jamás en el terminal compartido."""
    if any(patron in command for patron in _PATRONES_PELIGROSOS_RUN_COMMAND):
        return True
    if "curl" in command and _PIPE_CURL_A_SH.search(command):
        return True
    if (
        "osascript" in command
        and "tell application" in command
        and "do shell" in command
    ):
        return True
    return False


class LocalCompanionBridge:
    """Ejecutor in-process para el IDE autenticado y sesiones remotas."""

    def __init__(self, *, app: Any, data_dir: Path) -> None:
        self._manager = app.state.companion_manager
        self._config = load_config(data_dir / "companion.yaml")
        self._config.remote_input_enabled = True
        self._config.allow_all_apps = True
        # Mac del dueño: acceso TOTAL a la terminal (cualquier comando, incl.
        # sudo) — la defensa real es el sistema pidiendo la contraseña de
        # sudo, no una allowlist de ejecutables.
        existing = [cmd for cmd in (self._config.allowed_commands or []) if cmd]
        self._config.allowed_commands = list(dict.fromkeys([*existing, *_DEFAULT_LOCAL_COMMANDS]))
        self._manager.register_local_default(self.execute)

    async def ensure_registered(self, tenant_id: uuid.UUID) -> None:
        if not self._manager.is_connected(tenant_id):
            self._manager.register_local(tenant_id, self.execute)

    async def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        async def approve(
            requested_action: str,
            requested_params: dict[str, Any],
            _config: CompanionConfig,
        ) -> bool:
            # Un bot/worker no toca el portapapeles ni el teclado/mouse del
            # dueño: solo la sesión remota del teléfono lo hace, y SIEMPRE
            # manda `session_id`. Sin `session_id`, quien pide esto es un bot
            # — denegar (ver `_SIN_SESION_BLOQUEADAS` y por qué `screenshot`
            # queda fuera).
            if requested_action in _SIN_SESION_BLOQUEADAS:
                # EXCEPCIÓN: turno proactivo del companion DEL DUEÑO (vida
                # digital). La tool `usar_computadora` fija `owner_approved`
                # SERVER-SIDE desde `ctx.extras` (pre-aprobación + companion_wake)
                # — el modelo no puede falsificarlo. Scrollear y leer
                # WhatsApp/LinkedIn es el propósito declarado de esa visita.
                if requested_params.get("owner_approved") is True:
                    return True
                session_id = requested_params.get("session_id")
                if not (isinstance(session_id, str) and session_id.strip()):
                    return False
            if requested_action in _LOCAL_IDE_ACTIONS | _PERSONAL_MAC_ACTIONS | _DESKTOP_OWNER_ACTIONS:
                return True
            return (
                requested_action in _REMOTE_ACTIONS
                and isinstance(requested_params.get("session_id"), str)
                and bool(requested_params["session_id"].strip())
            )

        if action not in _LOCAL_ACTIONS:
            logger.warning("El puente local rechazó una acción no expuesta: %s", action)
            return {"ok": False, "error": f"acción no disponible en el puente local: {action!r}"}
        # Terminal COMPARTIDO del proyecto: cuando el bot pide `run_command`
        # con `workspace_root` (la ruta que `usar_computadora` fija
        # server-side), se enruta al MISMO shell que el IDE — así los bots
        # cooperan sobre el proyecto sin abrir Terminal.app.
        if action == "run_command" and isinstance(params, dict):
            # Antes de aprobar nada: un comando con patrón destructivo se
            # bloquea en seco, venga del bot o del dueño. La defensa del
            # sistema (contraseña de sudo) no alcanza para `rm -rf`/`dd`,
            # que no piden nada.
            if _comando_peligroso(str(params.get("command") or "")):
                logger.warning(
                    "Comando bloqueado por seguridad del dueño (patrón peligroso): %r",
                    str(params.get("command") or "")[:200],
                )
                return {
                    "ok": False,
                    "error": "comando bloqueado por seguridad del dueño: patrón peligroso",
                }
            # Allowlist del companion.yaml: si `allowed_commands` está definido,
            # SOLO esos ejecutables pasan (a menos que allow_all_commands=True).
            _comando = str(params.get("command") or "").strip()
            _config_cmds = getattr(self._config, "allowed_commands", None)
            if _config_cmds and not getattr(self._config, "allow_all_commands", False):
                _exe = _comando.split()[0] if _comando else ""
                _exe_limpio = _exe.strip('"\'')
                _allowed = any(_exe_limpio == c or _exe_limpio.startswith(c) for c in _config_cmds)
                if not _allowed:
                    logger.warning(
                        "run_command fuera del allowlist del companion: %r", _comando[:200],
                    )
                    return {
                        "ok": False,
                        "error": "comando fuera de allowed_commands del companion.yaml",
                    }
            # SIEMPRE terminal COMPARTIDO del proyecto para el bot: cwd =
            # `workspace_root` (si lo fijó usar_computadora) o el repo del
            # dueño (EDECAN_LOCAL_REPO_PATH). Así los bots cooperan sobre el
            # MISMO shell y no abren ventanas.
            cwd = (
                str(params.get("workspace_root") or "")
                or os.environ.get("EDECAN_LOCAL_REPO_PATH")
                or str(self._config.sandbox_dir or "")
                or "/"
            )
            return await execute_ide_action(
                "ide_terminal_exec_propio",
                {"cwd": cwd, "command": str(params.get("command") or "")},
                self._config,
                approve,
            )
        if action in IDE_ACTIONS:
            return await execute_ide_action(action, params, self._config, approve)
        return await actions.execute(action, params, self._config, approve)
