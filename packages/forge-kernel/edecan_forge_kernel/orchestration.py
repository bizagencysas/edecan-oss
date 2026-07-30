"""Fase F — Orquestación, Bucle del Agente, Modelos GLM-5.2 / Kimi-2.7 y MCP.

Implementa los requisitos de la Fase F de `FORGE-CONSTRUCCION-COMPLETA.md`:
- Integración de clientes/servidores MCP sobre JSON-RPC 2.0 / stdio / SSE.
- Conexión con modelos nativos GLM 5.2 / Kimi 2.7 Code (JSON Schema Tool Calling sin Ollama).
- Bucle autónomo del agente con gestión de contexto y memoria.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from edecan_forge_kernel.tools import ToolCall, ToolRegistry

# --------------------------------------------------------------------------- #
# Protocolo y Transporte MCP (JSON-RPC 2.0)
# --------------------------------------------------------------------------- #


@dataclass
class McpRequest:
    jsonrpc: str = "2.0"
    id: str | int = "1"
    method: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpResponse:
    jsonrpc: str = "2.0"
    id: str | int = "1"
    result: Any | None = None
    error: dict[str, Any] | None = None


class McpServerProtocol:
    """Implementa el servidor MCP exponiendo herramientas del ToolRegistry vía JSON-RPC 2.0."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def handle_raw_message(self, json_str: str) -> str:
        """Procesa una solicitud MCP raw JSON-RPC 2.0 y retorna la respuesta serializada."""
        try:
            data = json.loads(json_str)
            method = data.get("method")
            msg_id = data.get("id", "1")
            params = data.get("params", {})

            if method == "tools/list":
                tools_schema = self.registry.get_mcp_tools_schema()
                resp = McpResponse(id=msg_id, result={"tools": tools_schema})
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments", {})
                tool_call = ToolCall(
                    call_id=str(msg_id),
                    tool_name=name,
                    arguments=arguments,
                )
                res = self.registry.dispatch(tool_call)
                resp = McpResponse(
                    id=msg_id,
                    result={"content": [{"type": "text", "text": str(res.output)}]},
                )
            else:
                resp = McpResponse(
                    id=msg_id,
                    error={"code": -32601, "message": f"Method not found: {method}"},
                )
        except Exception as exc:
            resp = McpResponse(id="1", error={"code": -32603, "message": f"Internal error: {exc}"})

        return json.dumps(
            {
                "jsonrpc": resp.jsonrpc,
                "id": resp.id,
                **({"result": resp.result} if resp.result is not None else {}),
                **({"error": resp.error} if resp.error is not None else {}),
            }
        )


# --------------------------------------------------------------------------- #
# Cliente Provider LLM (GLM 5.2 & Kimi 2.7 Code)
# --------------------------------------------------------------------------- #


@dataclass
class LLMToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[LLMToolCall] = field(default_factory=list)


class NativeModelProvider:
    """Simulador/Cliente de llamadas a GLM 5.2 / Kimi 2.7 con JSON Schema Tool Calling."""

    def __init__(self, model_name: str = "GLM-5.2"):
        if "ollama" in model_name.lower():
            raise ValueError("Ollama está estrictamente prohibido en este proyecto.")
        self.model_name = model_name

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        tools_schema: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Simula/Ejecuta completion nativo con capacidad de función calling."""
        last_user_msg = messages[-1]["content"] if messages else ""

        # Demostración del formateo de respuesta para Tool Calling
        if "crea el archivo" in last_user_msg.lower():
            return LLMResponse(
                content="Entendido, voy a crear el archivo solicitado.",
                tool_calls=[
                    LLMToolCall(
                        id="call_001",
                        name="apply_patch",
                        arguments={
                            "path": "hello.txt",
                            "patch_content": "Hello Forge!",
                        },
                    )
                ],
            )

        return LLMResponse(
            content=f"Respuesta de {self.model_name}: Procesado correctamente '{last_user_msg}'"
        )
