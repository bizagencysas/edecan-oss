"""`edecan_mcp.client.MCPClient` — contra `InProcessTransport`+`fake_server`
(`conftest.py`), 100% offline."""

from __future__ import annotations

import pytest
from edecan_mcp.client import MAX_RESULT_CHARS, MCPClient, MCPClientError
from edecan_mcp.protocol import MCPRequest, MCPResponse
from edecan_mcp.transport import InProcessTransport, MCPTransport, MCPTransportError


async def test_initialize_hace_el_handshake_completo(fake_server) -> None:
    client = MCPClient(InProcessTransport(fake_server))
    resultado = await client.initialize()
    assert resultado["protocolVersion"] == "2025-03-26"
    metodos = [r.method for r in fake_server.recibidos]
    assert metodos == ["initialize", "notifications/initialized"]
    assert fake_server.recibidos[-1].id is None  # la notificación nunca lleva id
    await client.close()


async def test_list_tools_normaliza_la_forma(fake_server) -> None:
    client = MCPClient(InProcessTransport(fake_server))
    await client.initialize()
    tools = await client.list_tools()
    assert tools == [
        {
            "name": "sumar",
            "description": "Suma dos números.",
            "input_schema": {
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
            },
        }
    ]
    await client.close()


class _ServidorConToolMalFormada:
    """`tools/list` devuelve una entrada válida y una sin `"name"` — un
    servidor MCP mal comportado no debe tumbar `list_tools()`."""

    async def handle(self, request: MCPRequest) -> MCPResponse:
        if request.method == "tools/list":
            return MCPResponse(
                result={
                    "tools": [
                        {"name": "valida", "description": "ok", "inputSchema": {"type": "object"}},
                        {"description": "sin name, debe ignorarse"},
                    ]
                },
                id=request.id,
            )
        return MCPResponse(result={}, id=request.id)


async def test_list_tools_ignora_entradas_sin_name() -> None:
    client = MCPClient(InProcessTransport(_ServidorConToolMalFormada()))
    tools = await client.list_tools()
    assert [t["name"] for t in tools] == ["valida"]
    await client.close()


async def test_call_tool_devuelve_texto(fake_server) -> None:
    client = MCPClient(InProcessTransport(fake_server))
    await client.initialize()
    resultado = await client.call_tool("sumar", {"a": 1, "b": 2})
    assert resultado == "3"
    await client.close()


async def test_call_tool_desconocida_da_mcpclienterror(fake_server) -> None:
    client = MCPClient(InProcessTransport(fake_server))
    await client.initialize()
    with pytest.raises(MCPClientError):
        await client.call_tool("no-existe", {})
    await client.close()


async def test_call_tool_con_is_error_no_lanza_sino_que_prefija(fake_server) -> None:
    fake_server.tools["falla"] = {
        "description": "",
        "input_schema": {},
        "result": "algo salió mal",
        "is_error": True,
    }
    client = MCPClient(InProcessTransport(fake_server))
    await client.initialize()
    resultado = await client.call_tool("falla", {})
    assert resultado.startswith("Error MCP:")
    assert "algo salió mal" in resultado
    await client.close()


async def test_call_tool_trunca_resultados_largos(fake_server) -> None:
    fake_server.tools["largo"] = {
        "description": "",
        "input_schema": {},
        "result": "x" * (MAX_RESULT_CHARS + 500),
    }
    client = MCPClient(InProcessTransport(fake_server))
    await client.initialize()
    resultado = await client.call_tool("largo", {})
    assert resultado.startswith("x" * MAX_RESULT_CHARS)
    assert "truncado" in resultado
    await client.close()


class _ServidorQueSiempreFalla:
    async def handle(self, request: MCPRequest) -> MCPResponse:
        return MCPResponse.error_response(request.id, -32603, "boom interno")


async def test_error_del_servidor_da_mensaje_legible_no_traceback() -> None:
    client = MCPClient(InProcessTransport(_ServidorQueSiempreFalla()))
    with pytest.raises(MCPClientError) as exc_info:
        await client.initialize()
    assert "boom interno" in str(exc_info.value)
    assert "Traceback" not in str(exc_info.value)
    await client.close()


async def test_context_manager_cierra_el_transporte(fake_server) -> None:
    inner = InProcessTransport(fake_server)
    cerrado = {"valor": False}
    original_close = inner.close

    async def close_instrumentado() -> None:
        cerrado["valor"] = True
        await original_close()

    inner.close = close_instrumentado  # type: ignore[method-assign]

    async with MCPClient(inner) as client:
        await client.initialize()
    assert cerrado["valor"] is True


async def test_send_envuelve_fallo_de_transporte_inesperado() -> None:
    class _TransporteRoto(MCPTransport):
        async def send(self, request: MCPRequest) -> MCPResponse:
            raise RuntimeError("cable cortado")

        async def close(self) -> None:
            pass

    client = MCPClient(_TransporteRoto())
    with pytest.raises(MCPTransportError, match="cable cortado"):
        await client.initialize()
