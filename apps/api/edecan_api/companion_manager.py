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
from collections.abc import Awaitable, Callable
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
        self._socket_names: dict[uuid.UUID, str] = {}
        # En la app instalada, la propia computadora ES el companion. El
        # runtime local registra aquí un ejecutor in-process después de abrir
        # la sesión single-owner; el teléfono emparejado por QR no necesita
        # un segundo código ni otro proceso escondido en una terminal.
        self._local_handlers: dict[
            uuid.UUID, Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
        ] = {}
        self._local_names: dict[uuid.UUID, str] = {}
        self._local_default_handler: (
            Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None
        self._local_default_name: str = "VPS"
        self._pending: dict[str, _Pending] = {}

    def is_connected(self, tenant_id: uuid.UUID) -> bool:
        return (
            tenant_id in self._sockets
            or tenant_id in self._local_handlers
            or self._local_default_handler is not None
        )

    def list_machines(self, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
        """Máquinas disponibles para este tenant: la local (VPS) y la que se
        conectó por WebSocket (p. ej. la Mac del dueño). El orden deja la
        conexión WS (la Mac) primero: es el destino preferido para lectura de
        vida digital (WhatsApp/LinkedIn/Mail viven en ella)."""
        maquinas: list[dict[str, Any]] = []
        local = self._local_handlers.get(tenant_id) or self._local_default_handler
        if local is not None:
            nombre = self._local_names.get(tenant_id) or self._local_default_name
            maquinas.append(
                {
                    "machineId": nombre,
                    "label": nombre,
                    "name": nombre,
                    "kind": "local",
                    "connected": True,
                }
            )
        if tenant_id in self._sockets:
            nombre = self._socket_names.get(tenant_id) or "Mac"
            maquinas.append(
                {
                    "machineId": nombre,
                    "label": nombre,
                    "name": nombre,
                    "kind": "remote",
                    "connected": True,
                }
            )
        return maquinas

    def register_local_default(
        self,
        handler: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
        *,
        name: str = "VPS",
    ) -> None:
        """Registra el único equipo de un runtime local single-owner.

        Solo ``edecan_local`` llama este método. Permite que el destino esté
        disponible desde el arranque aunque el WebView conserve un JWT y no
        necesite volver a llamar ``/v1/auth/local``.
        """
        self._local_default_handler = handler
        self._local_default_name = name
        logger.info("Computadora del runtime local disponible (name=%s)", name)

    def register_local(
        self,
        tenant_id: uuid.UUID,
        handler: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
        *,
        name: str = "VPS",
    ) -> None:
        """Registra la computadora de una instalación local como destino.

        No acepta URLs ni credenciales: es una función que vive en el mismo
        proceso y solo puede crear ``edecan_local``. El canal WebSocket se
        conserva para instalaciones hospedadas o equipos adicionales.
        """
        self._local_handlers[tenant_id] = handler
        self._local_names[tenant_id] = name
        logger.info("Computadora local disponible para tenant_id=%s (name=%s)", tenant_id, name)

    async def connect(self, tenant_id: uuid.UUID, websocket: WebSocket, *, name: str = "Mac") -> None:
        await websocket.accept()
        anterior = self._sockets.pop(tenant_id, None)
        if anterior is not None and anterior is not websocket:
            # Reemplazo limpio: cierra la conexión vieja ANTES de registrar la
            # nueva. El finally del handler viejo correrá su disconnect, que
            # por identidad NO tocará la nueva (ver disconnect).
            try:
                await anterior.close(code=4001, reason="reemplazada por una conexión más nueva")
            except Exception:  # noqa: BLE001 - la vieja puede ya estar muerta
                pass
        self._sockets[tenant_id] = websocket
        self._socket_names[tenant_id] = name
        logger.info("Companion conectado para tenant_id=%s (name=%s)", tenant_id, name)

    def disconnect(self, tenant_id: uuid.UUID, websocket: WebSocket | None = None) -> None:
        actual = self._sockets.get(tenant_id)
        if actual is None:
            return
        if websocket is not None and actual is not websocket:
            # El finally de una conexión VIEJA no puede borrar la NUEVA:
            # este era el bug que hacía aparecer/desaparecer la Mac a cada
            # reconexión (el handler antiguo barría el socket fresco).
            logger.info(
                "disconnect ignorado: la conexión que cierra no es la vigente "
                "(tenant_id=%s)",
                tenant_id,
            )
            return
        self._sockets.pop(tenant_id, None)
        self._socket_names.pop(tenant_id, None)
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
        *,
        machine: str | None = None,
    ) -> dict[str, Any]:
        """Envía `{request_id, action, params}` al companion del tenant y espera su respuesta.

        Modelo Grok: el DEFAULT es la computadora de los bots (el box = el
        VPS local). La Mac del dueño se usa SOLO cuando `machine` la nombra
        explícitamente (p. ej. vida digital: WhatsApp/LinkedIn/Mail viven en
        ella, o builds de Xcode). Las máquinas se listan en
        GET /v1/remote/machines."""
        local = self._local_handlers.get(tenant_id) or self._local_default_handler
        local_name = self._local_names.get(tenant_id) or self._local_default_name
        socket = self._sockets.get(tenant_id)
        socket_name = self._socket_names.get(tenant_id) or "Mac"

        if machine is not None:
            objetivo = machine.strip().lower()
            if objetivo == local_name.lower() and local is not None:
                socket = None
            elif socket is not None and objetivo == socket_name.lower():
                local = None
            else:
                raise CompanionError(
                    f"No hay una máquina conectada con el nombre '{machine}'."
                )
        if local is not None:
            try:
                return await asyncio.wait_for(local(action, params), timeout=timeout)
            except TimeoutError as exc:
                raise CompanionError(
                    f"La computadora local no respondió a la acción '{action}' en {timeout}s."
                ) from exc

        if socket is None:
            raise CompanionError(f"No hay companion conectado para el tenant {tenant_id}.")

        request_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = _Pending(future=future, tenant_id=tenant_id)
        try:
            await socket.send_json(
                {"request_id": request_id, "action": action, "params": params}
            )
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            raise CompanionError(
                f"El companion no respondió a la acción '{action}' en {timeout}s."
            ) from exc
        finally:
            self._pending.pop(request_id, None)
