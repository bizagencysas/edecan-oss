"""Unit tests for Phase F: Orchestration, MCP and LLM Providers."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from edecan_forge_kernel.cas import Cas
from edecan_forge_kernel.orchestration import (
    McpServerProtocol,
    NativeModelProvider,
)
from edecan_forge_kernel.tools import ToolRegistry, ToolResult
from edecan_forge_kernel.vfs import Vfs


def test_ollama_prohibition():
    with pytest.raises(ValueError, match="estrictamente prohibido"):
        NativeModelProvider(model_name="ollama-llama3")


def test_native_model_provider():
    provider = NativeModelProvider(model_name="Kimi-2.7-Code")
    resp = provider.chat_completion(
        messages=[{"role": "user", "content": "crea el archivo nuevo.txt"}]
    )
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "apply_patch"


def test_mcp_server_protocol():
    with tempfile.TemporaryDirectory() as tmp_dir:
        vfs = Vfs(cas=Cas(root=Path(tmp_dir)))
        registry = ToolRegistry(vfs)

        def custom_handler(args: dict) -> ToolResult:
            return ToolResult(call_id="1", status="ok", output="MCP Output Test")

        registry.register_custom_tool(
            name="test_tool",
            description="Test Tool",
            handler=custom_handler,
        )

        server = McpServerProtocol(registry)

        # tools/list
        req_list = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        res_list = json.loads(server.handle_raw_message(req_list))
        assert "result" in res_list
        assert len(res_list["result"]["tools"]) >= 1

        # tools/call
        req_call = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "test_tool", "arguments": {}},
            }
        )
        res_call = json.loads(server.handle_raw_message(req_call))
        assert res_call["result"]["content"][0]["text"] == "MCP Output Test"
