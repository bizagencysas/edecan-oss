"""Demostración y Validación de la Fase F — Orquestación, Bucle del Agente, GLM/Kimi y MCP."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from edecan_forge_kernel.cas import Cas
from edecan_forge_kernel.orchestration import (
    McpServerProtocol,
    NativeModelProvider,
)
from edecan_forge_kernel.tools import ToolRegistry, ToolResult
from edecan_forge_kernel.vfs import Vfs


def run_phase_f_demo():
    print("============================================================")
    print("       DEMO DE LA FASE F: ORQUESTACIÓN, MCP Y MODELOS LLM   ")
    print("============================================================\n")

    # 1. Prohibición estricta de Ollama
    print("1. Verificando regla de negocio: Prohibición total de Ollama...")
    try:
        NativeModelProvider(model_name="ollama-llama3")
        raise AssertionError("Ollama debió ser rechazado")
    except ValueError as exc:
        print(f"   Excepción esperada capturada: {exc}")
        print("   [OK] Ollama está estrictamente prohibido.\n")

    # 2. Cliente nativo GLM 5.2 / Kimi 2.7
    print("2. Probando proveedor nativo GLM 5.2 / Kimi 2.7 Code...")
    provider = NativeModelProvider(model_name="GLM-5.2")
    response = provider.chat_completion(
        messages=[{"role": "user", "content": "Crea el archivo con el parche"}]
    )
    print(f"   Modelo: {provider.model_name}")
    print(f"   Respuesta: {response.content}")
    print(f"   Tool Calls generados: {response.tool_calls}")
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "apply_patch"
    print("   [OK] Invocación con Tool Calling nativo validada.\n")

    # 3. Protocolo Servidor MCP JSON-RPC 2.0
    print("3. Probando Servidor MCP (JSON-RPC 2.0 / tools/list y tools/call)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        vfs = Vfs(cas=Cas(root=Path(tmp_dir)))
        registry = ToolRegistry(vfs)

        def greet_handler(args: dict) -> ToolResult:
            name = args.get("name", "User")
            return ToolResult(
                call_id="1",
                status="ok",
                output=f"Hola {name}, bienvenido a Forge!",
            )

        registry.register_custom_tool(
            name="greet",
            description="Saluda al usuario",
            handler=greet_handler,
        )

        mcp_server = McpServerProtocol(registry)

        # Solicitar tools/list vía JSON-RPC
        req_list = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        res_list_raw = mcp_server.handle_raw_message(req_list)
        res_list = json.loads(res_list_raw)
        print("   Respuesta MCP tools/list:")
        print(f"   {res_list['result']}")
        assert "tools" in res_list["result"]

        # Invocación tools/call vía JSON-RPC
        req_call = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "greet", "arguments": {"name": "Developer"}},
            }
        )
        res_call_raw = mcp_server.handle_raw_message(req_call)
        res_call = json.loads(res_call_raw)
        print("   Respuesta MCP tools/call:")
        print(f"   {res_call['result']}")
        assert "Hola Developer" in res_call["result"]["content"][0]["text"]
        print("   [OK] Protocolo de Servidor MCP JSON-RPC 2.0 validado.\n")

    print("============================================================")
    print("          VEREDICTO FASE F: ORQUESTACIÓN Y MCP EN VERDE     ")
    print("============================================================\n")


if __name__ == "__main__":
    run_phase_f_demo()
