"""`edecan_mcp.protocol` — mensajes JSON-RPC 2.0 (sin I/O)."""

from __future__ import annotations

import json

import pytest
from edecan_mcp.protocol import MCPError, MCPRequest, MCPResponse


def test_request_con_id_incluye_id_en_json() -> None:
    req = MCPRequest(method="tools/list", params={}, id=5)
    data = json.loads(req.to_json())
    assert data == {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 5}


def test_request_con_id_none_es_notificacion_sin_id() -> None:
    req = MCPRequest(method="notifications/initialized", params={}, id=None)
    data = json.loads(req.to_json())
    assert "id" not in data
    assert data["method"] == "notifications/initialized"


def test_request_from_json_ida_y_vuelta() -> None:
    original = MCPRequest(method="tools/call", params={"name": "x", "arguments": {"a": 1}}, id=7)
    reconstruido = MCPRequest.from_json(original.to_json())
    assert reconstruido == original


def test_response_con_result() -> None:
    resp = MCPResponse(result={"tools": []}, id=1)
    data = json.loads(resp.to_json())
    assert data == {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
    assert "error" not in data


def test_response_con_error_no_incluye_result() -> None:
    resp = MCPResponse.error_response(1, -32601, "método desconocido")
    data = json.loads(resp.to_json())
    assert data["error"] == {"code": -32601, "message": "método desconocido"}
    assert "result" not in data


def test_response_error_response_con_data_opcional() -> None:
    resp = MCPResponse.error_response(2, -32602, "argumentos inválidos", data={"campo": "a"})
    assert resp.error is not None
    assert resp.error["data"] == {"campo": "a"}


def test_response_from_json_ida_y_vuelta() -> None:
    original = MCPResponse(result={"x": 1}, id=3)
    reconstruido = MCPResponse.from_json(original.to_json())
    assert reconstruido == original


def test_response_from_json_rechaza_algo_que_no_es_json_rpc() -> None:
    with pytest.raises(ValueError):
        MCPResponse.from_json(json.dumps({"foo": "bar"}))


def test_mcp_error_str() -> None:
    error = MCPError(code=-32601, message="método desconocido")
    assert str(error) == "MCPError(-32601): método desconocido"
