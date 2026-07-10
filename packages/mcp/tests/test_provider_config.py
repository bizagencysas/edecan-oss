"""`edecan_mcp.provider_config` — serialización hacia/desde el `TokenVault`
(`ARCHITECTURE.md` §15.g: `{nombre, transporte, url?, comando?, headers?}`
TODO junto en `TokenBundle.access_token`)."""

from __future__ import annotations

from edecan_mcp.provider_config import deserializar_config_mcp, serializar_config_mcp
from edecan_mcp.tool_adapter import MCPServerConfig


def test_ida_y_vuelta_http_con_headers() -> None:
    config = MCPServerConfig(nombre="Acme", transporte="http", url="https://acme.example.com/rpc")
    headers = {"Authorization": "Bearer xyz"}

    raw = serializar_config_mcp(config, headers)
    reconstruido, headers_reconstruidos = deserializar_config_mcp(raw, nombre_fallback="Acme")

    assert reconstruido == config
    assert headers_reconstruidos == headers


def test_ida_y_vuelta_stdio_sin_headers() -> None:
    config = MCPServerConfig(nombre="Local", transporte="stdio", comando="npx mi-servidor-mcp")

    raw = serializar_config_mcp(config, {})
    reconstruido, headers = deserializar_config_mcp(raw, nombre_fallback="Local")

    assert reconstruido == config
    assert headers == {}


def test_raw_none_usa_el_nombre_fallback_y_transporte_vacio() -> None:
    reconstruido, headers = deserializar_config_mcp(None, nombre_fallback="X")
    assert reconstruido.nombre == "X"
    assert reconstruido.transporte == ""
    assert reconstruido.url is None
    assert reconstruido.comando is None
    assert headers == {}


def test_raw_vacio_usa_el_nombre_fallback() -> None:
    reconstruido, headers = deserializar_config_mcp("", nombre_fallback="X")
    assert reconstruido.nombre == "X"
    assert headers == {}


def test_json_corrupto_no_revienta() -> None:
    reconstruido, headers = deserializar_config_mcp("esto no es JSON", nombre_fallback="X")
    assert reconstruido.nombre == "X"
    assert reconstruido.transporte == ""
    assert headers == {}


def test_transporte_desconocido_en_json_se_trata_como_vacio() -> None:
    reconstruido, _headers = deserializar_config_mcp(
        '{"nombre": "X", "transporte": "websocket"}', nombre_fallback="X"
    )
    assert reconstruido.transporte == ""


def test_json_que_no_es_un_objeto_no_revienta() -> None:
    reconstruido, headers = deserializar_config_mcp("[1, 2, 3]", nombre_fallback="X")
    assert reconstruido.transporte == ""
    assert headers == {}


def test_headers_con_forma_invalida_se_ignoran() -> None:
    reconstruido, headers = deserializar_config_mcp(
        '{"nombre": "X", "transporte": "http", "headers": "no-es-un-dict"}', nombre_fallback="X"
    )
    assert reconstruido.transporte == "http"
    assert headers == {}


def test_nombre_en_json_gana_sobre_el_fallback() -> None:
    config = MCPServerConfig(nombre="NombreReal", transporte="http", url="https://x/rpc")
    raw = serializar_config_mcp(config, {})
    reconstruido, _headers = deserializar_config_mcp(raw, nombre_fallback="otro-nombre-cualquiera")
    assert reconstruido.nombre == "NombreReal"
