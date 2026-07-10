"""`MCPClient` — conecta a un servidor MCP externo (vía cualquier
`edecan_mcp.transport.MCPTransport`), hace el *handshake* y descubre/llama
sus tools. Adaptado de OpenJarvis (`src/openjarvis/mcp/client.py`,
Apache-2.0, ver `NOTICE`), reescrito async.
"""

from __future__ import annotations

import itertools
import logging
from typing import Any

from .protocol import MCPRequest, MCPResponse
from .transport import MCPTransport, MCPTransportError

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-03-26"
CLIENT_NAME = "edecan"
CLIENT_VERSION = "1.0"

MAX_RESULT_CHARS = 8000
_TRUNCATION_SUFFIX = "\n\n[…resultado truncado — superó los 8000 caracteres…]"


class MCPClientError(RuntimeError):
    """Error de protocolo/negocio MCP con mensaje YA legible para mostrarle
    al modelo o al usuario — `edecan_mcp.tool_adapter` nunca deja escapar un
    traceback crudo hacia el chat, siempre pasa por este tipo (o por
    `MCPTransportError`, que tiene la misma garantía)."""


class MCPClient:
    """Cliente MCP sobre un `MCPTransport` ya construido.

    Uso típico (ver `edecan_mcp.tool_adapter`, que abre uno nuevo por
    llamada en v1, sin caché de sesión):

        transport = HTTPTransport(url, headers=headers)
        async with MCPClient(transport) as client:
            await client.initialize()
            tools = await client.list_tools()
            texto = await client.call_tool(tools[0]["name"], {})
    """

    def __init__(self, transport: MCPTransport) -> None:
        self._transport = transport
        self._initialized = False
        self._capabilities: dict[str, Any] = {}
        self._id_counter = itertools.count(1)

    def _next_id(self) -> int:
        return next(self._id_counter)

    async def _send(self, method: str, params: dict[str, Any] | None = None) -> MCPResponse:
        request = MCPRequest(method=method, params=params or {}, id=self._next_id())
        try:
            response = await self._transport.send(request)
        except MCPTransportError:
            raise
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de transporte se traduce
            raise MCPTransportError(f"Fallo de transporte MCP: {exc}") from exc
        if response.error is not None:
            mensaje = response.error.get("message", "error desconocido") if isinstance(
                response.error, dict
            ) else str(response.error)
            raise MCPClientError(f"El servidor MCP respondió un error en «{method}»: {mensaje}")
        return response

    async def initialize(self) -> dict[str, Any]:
        """Handshake MCP completo: `initialize` + `notifications/initialized`
        (spec: el cliente DEBE mandar esa notificación tras recibir la
        respuesta de `initialize`, antes de cualquier otra llamada)."""
        params = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
        }
        response = await self._send("initialize", params)
        self._initialized = True
        self._capabilities = (response.result or {}).get("capabilities", {})
        await self._notify("notifications/initialized")
        return response.result or {}

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        request = MCPRequest(method=method, params=params or {}, id=None)
        try:
            await self._transport.send_notification(request)
        except MCPTransportError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MCPTransportError(f"Fallo de transporte MCP (notificación): {exc}") from exc

    async def list_tools(self) -> list[dict[str, Any]]:
        """`[{"name", "description", "input_schema"}, …]` — tools que expone
        el servidor. Descarta silenciosamente cualquier entrada sin `name`
        (servidor mal comportado) en vez de reventar."""
        response = await self._send("tools/list")
        crudo = (response.result or {}).get("tools", [])
        if not isinstance(crudo, list):
            return []
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema", {}),
            }
            for t in crudo
            if isinstance(t, dict) and t.get("name")
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Ejecuta la tool remota `name` y devuelve su resultado como texto
        (bloques `type=text` concatenados, truncado a `MAX_RESULT_CHARS`).

        Si el servidor marca `isError=True`, el texto se devuelve igual (con
        un prefijo "Error MCP:") en vez de lanzar — es el resultado de
        NEGOCIO de la tool remota, no un fallo de protocolo/transporte (ver
        `edecan_core.tools.base.Tool.run`: los errores de negocio son
        contenido, no excepciones).
        """
        response = await self._send("tools/call", {"name": name, "arguments": arguments or {}})
        resultado = response.result or {}
        texto = _concatenar_contenido(resultado.get("content") or [])
        if resultado.get("isError"):
            return f"Error MCP: {texto or 'la herramienta remota reportó un error sin detalle.'}"
        return _truncar(texto)

    async def close(self) -> None:
        await self._transport.close()

    async def __aenter__(self) -> MCPClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


def _concatenar_contenido(bloques: list[Any]) -> str:
    partes = [
        str(bloque.get("text", ""))
        for bloque in bloques
        if isinstance(bloque, dict) and bloque.get("type") == "text"
    ]
    return "\n".join(partes)


def _truncar(texto: str) -> str:
    if len(texto) <= MAX_RESULT_CHARS:
        return texto
    return texto[:MAX_RESULT_CHARS] + _TRUNCATION_SUFFIX


__all__ = ["MCPClient", "MCPClientError", "MAX_RESULT_CHARS"]
