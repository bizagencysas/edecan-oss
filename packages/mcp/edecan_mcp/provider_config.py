"""Serialización de la config completa de un servidor MCP (`MCPServerConfig`
+ headers) hacia/desde el `TokenVault` — `ARCHITECTURE.md` §15.g (contrato
PINNED por el linchpin de v6, WP-V6-01):

> La config no-secreta y los secretos (headers de auth del transporte HTTP,
> etc.) viajan JUNTOS en el mismo blob cifrado: `TokenBundle.access_token`
> guarda un JSON serializado `{nombre, transporte: "http"|"stdio", url?,
> comando?, headers?, env?}` — mismo criterio que `LLMProviderConfig`/`"ads"`/
> `"vehicles"` (§12.c/§13.d): `TokenBundle.token_type = "config"`.
> `connector_accounts` en sí NO lleva ninguna columna con secretos (mismo
> motivo que "llm": la config completa vive SOLO del lado cifrado).

A diferencia de una versión anterior de este módulo (que dividía la config
entre `connector_accounts.scopes`, sin secretos, y el vault, solo con
`headers`), el contrato pinned exige que TODO viva junto en el vault —
`connector_accounts` (`apps/api/edecan_api/routers/mcp.py`) queda reducida a
identidad pura (`connector_key`, `external_account_id=<slug>`,
`display_name`), sin ninguna columna de config.
"""

from __future__ import annotations

import json
from typing import Any

from .tool_adapter import MCPServerConfig

_TRANSPORTES_VALIDOS = frozenset({"http", "stdio"})


def serializar_config_mcp(config: MCPServerConfig, headers: dict[str, str]) -> str:
    """`TokenBundle.access_token` (`token_type="config"`) para `PUT
    /v1/mcp/servers` — un único JSON con TODO (`ARCHITECTURE.md` §15.g),
    incluidas las variables explícitas del subprocess local."""
    payload = {
        "nombre": config.nombre,
        "transporte": config.transporte,
        "url": config.url,
        "comando": config.comando,
        "headers": dict(headers or {}),
        "env": dict(config.env or {}),
    }
    return json.dumps(payload)


def deserializar_config_mcp(
    raw: str | None, *, nombre_fallback: str
) -> tuple[MCPServerConfig, dict[str, str]]:
    """Inversa de `serializar_config_mcp`. Tolerante: `raw` vacío/`None`/con
    JSON corrupto/con un `transporte` desconocido devuelve un
    `MCPServerConfig` con `transporte=""` y headers `{}` — el llamador
    (router/`construir_tools_mcp`) lo trata como "servidor sin config
    válida" (se omite con un warning, nunca revienta). `nombre_fallback` es
    el `external_account_id` de la fila `connector_accounts` — se usa si el
    JSON no trae `nombre` (no debería pasar en la práctica, pero nunca deja
    el nombre vacío)."""
    data: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            data = parsed

    transporte = data.get("transporte")
    if transporte not in _TRANSPORTES_VALIDOS:
        transporte = ""

    env_crudo = data.get("env")
    env = (
        {str(clave): str(valor) for clave, valor in env_crudo.items()}
        if isinstance(env_crudo, dict)
        else {}
    )
    config = MCPServerConfig(
        nombre=str(data.get("nombre") or nombre_fallback),
        transporte=str(transporte),
        url=data.get("url"),
        comando=data.get("comando"),
        env=env or None,
    )
    headers_crudos = data.get("headers")
    headers = dict(headers_crudos) if isinstance(headers_crudos, dict) else {}
    return config, headers


__all__ = ["deserializar_config_mcp", "serializar_config_mcp"]
