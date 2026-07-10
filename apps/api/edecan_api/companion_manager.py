"""Gestor de conexiones WebSocket del companion de escritorio (ARCHITECTURE.md §10.12).

Vive en `edecan_api` — no es un contrato de paquete hermano. Mantiene un mapa
`tenant_id -> WebSocket` (un companion conectado por tenant) y expone
`send_command(tenant_id, action, params, timeout=30)`, que la API inyecta en
`ToolContext.extras["companion"]` (ARCHITECTURE.md §10.7) para que las
herramientas del agente puedan pedirle acciones al companion de ese tenant.

Nota de despliegue: este `ConnectionManager` es un diccionario en memoria del
proceso — funciona en un solo worker/proceso `uvicorn`. Un despliegue con
varios workers necesitaría un backend compartido (p. ej. pub/sub de Redis)
para enrutar el comando al proceso que tiene el socket; queda fuera de este
paquete de trabajo.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class CompanionError(RuntimeError):
    """El companion no está conectado, o no respondió a tiempo."""


@dataclass
class _Pending:
    future: asyncio.Future
    tenant_id: uuid.UUID


class ConnectionManager:
    """`tenant_id -> WebSocket` del companion de escritorio conectado."""

    def __init__(self) -> None:
        self._sockets: dict[uuid.UUID, WebSocket] = {}
        self._pending: dict[str, _Pending] = {}

    def is_connected(self, tenant_id: uuid.UUID) -> bool:
        return tenant_id in self._sockets

    async def connect(self, tenant_id: uuid.UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        self._sockets[tenant_id] = websocket
        logger.info("Companion conectado para tenant_id=%s", tenant_id)

    def disconnect(self, tenant_id: uuid.UUID) -> None:
        self._sockets.pop(tenant_id, None)
        logger.info("Companion desconectado para tenant_id=%s", tenant_id)

    async def handle_incoming(self, tenant_id: uuid.UUID, message: dict[str, Any]) -> None:
        """Despacha una respuesta `{request_id, ...}` del companion al `Future` en espera.

        Mensajes sin `request_id` conocido (p. ej. un heartbeat del companion)
        se ignoran silenciosamente. `_pending` es un diccionario de proceso
        compartido entre todos los tenants (solo indexado por `request_id`), así
        que también se valida explícitamente que el `request_id` pertenezca a
        *este* `tenant_id` antes de resolver el `Future` — un companion no debe
        poder completar (ni con datos falsos) una petición pendiente de otro
        tenant, aunque adivinar un `request_id` (uuid4) sea inviable en la
        práctica.
        """
        request_id = message.get("request_id")
        if not request_id:
            return
        pending = self._pending.get(request_id)
        if pending is None or pending.future.done():
            return
        if pending.tenant_id != tenant_id:
            logger.warning(
                "Ignorando respuesta de companion: request_id=%s pertenece a otro "
                "tenant_id (esperado=%s, recibido=%s)",
                request_id,
                pending.tenant_id,
                tenant_id,
            )
            return
        pending.future.set_result(message)

    async def send_command(
        self,
        tenant_id: uuid.UUID,
        action: str,
        params: dict[str, Any],
        timeout: float = 30,
    ) -> dict[str, Any]:
        """Envía `{request_id, action, params}` al companion del tenant y espera su respuesta."""
        websocket = self._sockets.get(tenant_id)
        if websocket is None:
            raise CompanionError(f"No hay companion conectado para el tenant {tenant_id}.")

        request_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = _Pending(future=future, tenant_id=tenant_id)
        try:
            await websocket.send_json(
                {"request_id": request_id, "action": action, "params": params}
            )
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            raise CompanionError(
                f"El companion no respondió a la acción '{action}' en {timeout}s."
            ) from exc
        finally:
            self._pending.pop(request_id, None)
