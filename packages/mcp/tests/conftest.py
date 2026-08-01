"""Bootstrap de `sys.path` + fixtures compartidas de `packages/mcp/tests`.

`packages/mcp` puede todavía no estar registrado en
`[tool.uv.workspace].members` del `pyproject.toml` raíz (ese archivo no está
en las rutas que WP-V6-07 puede escribir — ver la nota al final de
`pyproject.toml` de este mismo paquete) — así que, mismo criterio que
`apps/api/tests/_stub_siblings.py`/`apps/api/tests/test_ads_router.py`
(`ARCHITECTURE.md` §10.1), este `conftest.py` agrega el propio paquete a
`sys.path` para que `import edecan_mcp` funcione sin depender de que `uv
sync --all-packages` ya lo haya instalado. Es idempotente/inofensivo si el
paquete YA está instalado (no reemplaza esa entrada, solo evita duplicarla).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from edecan_mcp.protocol import MCPRequest, MCPResponse  # noqa: E402


class FakeMCPServer:
    """Servidor MCP mínimo en memoria — `async def handle(request) ->
    MCPResponse`, la misma superficie que espera `InProcessTransport`.

    `tools` es `{name: {"description": str, "input_schema": dict, "result":
    str, "is_error": bool}}`. Guarda cada request recibido en
    `self.recibidos` para que los tests puedan verificar el handshake
    (`initialize` antes de `tools/list`/`tools/call`) sin instrumentar nada
    más.
    """

    def __init__(self, tools: dict[str, dict[str, Any]] | None = None) -> None:
        self.tools = tools or {}
        self.recibidos: list[MCPRequest] = []
        self.inicializado = False

    async def handle(self, request: MCPRequest) -> MCPResponse:
        self.recibidos.append(request)
        if request.method == "initialize":
            self.inicializado = True
            return MCPResponse(
                result={
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "fake-mcp-server", "version": "0.0.1"},
                },
                id=request.id,
            )
        if request.method == "notifications/initialized":
            return MCPResponse(result={}, id=request.id)
        if request.method == "tools/list":
            tools = [
                {
                    "name": name,
                    "description": spec.get("description", ""),
                    "inputSchema": spec.get("input_schema", {}),
                }
                for name, spec in self.tools.items()
            ]
            return MCPResponse(result={"tools": tools}, id=request.id)
        if request.method == "tools/call":
            nombre = request.params.get("name")
            spec = self.tools.get(nombre)
            if spec is None:
                return MCPResponse.error_response(request.id, -32602, f"tool desconocida: {nombre}")
            return MCPResponse(
                result={
                    "content": [{"type": "text", "text": spec.get("result", "")}],
                    "isError": bool(spec.get("is_error", False)),
                },
                id=request.id,
            )
        return MCPResponse.error_response(
            request.id, -32601, f"método desconocido: {request.method}"
        )


@pytest.fixture
def fake_server() -> FakeMCPServer:
    return FakeMCPServer(
        tools={
            "sumar": {
                "description": "Suma dos números.",
                "input_schema": {
                    "type": "object",
                    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                    "required": ["a", "b"],
                },
                "result": "3",
            }
        }
    )
