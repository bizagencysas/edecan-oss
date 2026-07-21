"""Puente entre la instalación local y el control remoto móvil.

En el producto instalado no existe un segundo "companion" que la persona
deba ejecutar o emparejar: ``edecan_local`` ya corre dentro de Edecán y es el
agente de esta computadora. Este puente registra sus acciones directamente en
``ConnectionManager`` cuando se crea/recupera el dueño local.

El QR continúa siendo la credencial del teléfono. Una sesión remota exige la
confirmación explícita en el teléfono y el backend valida tenant, sesión y
flags antes de llegar aquí. El aprobador embebido acepta únicamente captura e
input que incluyan el ``session_id`` inyectado por el router; jamás convierte
las demás acciones del companion en auto-aprobadas.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from edecan_companion import actions
from edecan_companion.config import CompanionConfig, load_config

logger = logging.getLogger(__name__)

_REMOTE_ACTIONS = frozenset({"screenshot", "input_pointer", "input_key"})


class LocalCompanionBridge:
    """Ejecutor local único, limitado a sesiones remotas validadas por API."""

    def __init__(self, *, app: Any, data_dir: Path) -> None:
        self._manager = app.state.companion_manager
        self._config = load_config(data_dir / "companion.yaml")
        # El opt-in ya ocurre al seleccionar "Controlar" y confirmar la
        # sesión desde el teléfono emparejado. Los permisos de Accesibilidad
        # y Grabación de pantalla del SO siguen siendo obligatorios.
        self._config.remote_input_enabled = True
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
            return (
                requested_action in _REMOTE_ACTIONS
                and isinstance(requested_params.get("session_id"), str)
                and bool(requested_params["session_id"].strip())
            )

        if action not in _REMOTE_ACTIONS:
            logger.warning("El puente local rechazó una acción fuera de sesión remota: %s", action)
            return {"ok": False, "error": f"acción no disponible en el puente local: {action!r}"}
        return await actions.execute(action, params, self._config, approve)
