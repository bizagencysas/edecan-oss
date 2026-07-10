"""Mensajes JSON-RPC 2.0 del protocolo MCP (Model Context Protocol, spec
2025-03-26) — `edecan_mcp.transport`/`edecan_mcp.client` los usan para
hablar con un servidor MCP externo.

Adaptado de OpenJarvis (`src/openjarvis/mcp/protocol.py`, Apache-2.0, ver
`NOTICE` en la raíz del repo) — la forma del mensaje JSON-RPC no cambia entre
sync/async (es puro dato, sin I/O), así que este módulo es casi idéntico al
original, solo con docstrings en español y nombres alineados al resto del
repo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Códigos de error JSON-RPC 2.0 / spec MCP.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


@dataclass
class MCPRequest:
    """Mensaje de *request* JSON-RPC 2.0.

    `id=None` (default `0`, pero cualquier caller que quiera una
    **notificación** debe pasar `id=None` explícito) omite la clave `"id"`
    en el JSON serializado — eso es lo que distingue una notificación (sin
    respuesta esperada) de un request normal, ver `MCPClient.notify`/
    `edecan_mcp.transport.MCPTransport.send_notification`.
    """

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int | str | None = 0
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        obj: dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
            "params": self.params,
        }
        if self.id is not None:
            obj["id"] = self.id
        return obj

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, data: str) -> MCPRequest:
        parsed = json.loads(data)
        return cls(
            method=parsed["method"],
            params=parsed.get("params", {}),
            id=parsed.get("id", 0),
            jsonrpc=parsed.get("jsonrpc", "2.0"),
        )


@dataclass
class MCPResponse:
    """Mensaje de *response* JSON-RPC 2.0 — `error` y `result` son mutuamente
    excluyentes (ver `to_json`)."""

    result: Any = None
    error: dict[str, Any] | None = None
    id: int | str = 0
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        obj: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            obj["error"] = self.error
        else:
            obj["result"] = self.result
        return json.dumps(obj)

    @classmethod
    def from_json(cls, data: str) -> MCPResponse:
        parsed = json.loads(data)
        if not isinstance(parsed, dict) or "jsonrpc" not in parsed:
            raise ValueError("Respuesta MCP inválida: no parece JSON-RPC 2.0.")
        return cls(
            result=parsed.get("result"),
            error=parsed.get("error"),
            id=parsed.get("id", 0),
            jsonrpc=parsed.get("jsonrpc", "2.0"),
        )

    @classmethod
    def error_response(
        cls, id: int | str, code: int, message: str, data: Any = None
    ) -> MCPResponse:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return cls(error=error, id=id)


@dataclass
class MCPError(Exception):
    """Error de protocolo MCP con código JSON-RPC — lo lanza `MCPClient`
    cuando el servidor responde `{"error": {...}}`."""

    code: int
    message: str
    data: Any = None

    def __str__(self) -> str:
        return f"MCPError({self.code}): {self.message}"


__all__ = [
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "MCPError",
    "MCPRequest",
    "MCPResponse",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
]
