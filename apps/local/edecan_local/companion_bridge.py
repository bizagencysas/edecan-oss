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
    "pbcopy",
    "pbpaste",
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


class LocalCompanionBridge:
    """Ejecutor in-process para el IDE autenticado y sesiones remotas."""

    def __init__(self, *, app: Any, data_dir: Path) -> None:
        self._manager = app.state.companion_manager
        self._config = load_config(data_dir / "companion.yaml")
        self._config.remote_input_enabled = True
        self._config.allow_all_apps = True
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
        if action in IDE_ACTIONS:
            return await execute_ide_action(action, params, self._config, approve)
        return await actions.execute(action, params, self._config, approve)
