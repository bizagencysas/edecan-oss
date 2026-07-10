"""`edecan_mcp` — cliente MCP (Model Context Protocol) bring-your-own,
adaptado de OpenJarvis (Apache-2.0, ver `NOTICE`). Ver `README.md` de este
paquete y `ARCHITECTURE.md` §15 para el contrato completo.
"""

from __future__ import annotations

from .client import MCPClient, MCPClientError
from .protocol import MCPError, MCPRequest, MCPResponse
from .provider_config import deserializar_config_mcp, serializar_config_mcp
from .seguridad import (
    HallazgoDescripcionTool,
    MCPSeguridadError,
    escanear_descripcion_tool_mcp,
    validar_comando_mcp,
    validar_url_mcp,
)
from .tool_adapter import (
    REQUIRES_FLAG_MCP,
    MCPServerConfig,
    construir_tools_mcp,
    sanear_slug,
)
from .transport import (
    HTTPTransport,
    InProcessTransport,
    MCPTransport,
    MCPTransportError,
    StdioTransport,
)

__all__ = [
    "REQUIRES_FLAG_MCP",
    "HallazgoDescripcionTool",
    "HTTPTransport",
    "InProcessTransport",
    "MCPClient",
    "MCPClientError",
    "MCPError",
    "MCPRequest",
    "MCPResponse",
    "MCPSeguridadError",
    "MCPServerConfig",
    "MCPTransport",
    "MCPTransportError",
    "StdioTransport",
    "construir_tools_mcp",
    "deserializar_config_mcp",
    "escanear_descripcion_tool_mcp",
    "sanear_slug",
    "serializar_config_mcp",
    "validar_comando_mcp",
    "validar_url_mcp",
]
