"""`construir_tools_mcp` — adapta las tools remotas de los servidores MCP de
un tenant a `edecan_core.tools.Tool` dinámicas (`ARCHITECTURE.md` §15, §10.7).

## Nombre de la tool

`mcp_{slug_servidor}_{slug_tool}` — `slug_servidor` sale de `nombre` (el que
el tenant escribió en `PUT /v1/mcp/servers`), `slug_tool` del `name` que
reporta el propio servidor MCP; ambos saneados a `[a-z0-9_]`
(`sanear_slug`). Si el resultado supera los 64 caracteres (límite habitual
de nombre de tool de los proveedores LLM), se trunca dejando un sufijo hash
de 6 caracteres derivado del nombre COMPLETO sin truncar, para que dos
nombres largos parecidos no terminen colisionando después de truncar. Un
test dedicado (`apps/api/tests/test_mcp_router.py`) verifica que ningún
nombre de tool "real" (`ToolRegistry.load_entry_points`) empieza con
`"mcp_"` — el prefijo por sí solo ya es la garantía de no-colisión con las
~40 tools nativas.

## `dangerous=True` SIEMPRE (decisión v1 conservadora)

Cada tool remota exige confirmación humana explícita en el chat antes de
ejecutarse (`Agent.run_turn`, `ARCHITECTURE.md` §10.7) — sin excepción, sin
importar si la tool remota "suena" de solo lectura: Edecán no tiene forma de
verificar qué hace de verdad un servidor MCP de terceros (el propio tenant
lo trae, `ARCHITECTURE.md` §0.3 "solo APIs oficiales... credenciales propias
de cada tenant"), así que se trata como potencialmente destructiva por
defecto. Consecuencia directa (y documentada, no un bug):
`edecan_automations.runner._build_safe_registry` excluye TODAS las tools
`dangerous=True` de un run headless — ninguna tool MCP corre nunca en una
automatización sin humano presente, ver `apps/worker/edecan_worker/handlers/
run_automation.py` y `apps/worker/tests/test_mcp_en_worker.py`.

## SSRF revalidado en la ejecución real, no solo en el discovery

`validar_url_mcp` (`seguridad.py`) se llamaba históricamente solo desde
`_tools_de_un_servidor` (discovery: decide qué tools se OFRECEN). Pero toda
tool MCP es `dangerous=True` (sección anterior) — la ejecución real ocurre
recién cuando un humano confirma (`POST /v1/conversations/.../confirm`),
hasta `PENDING_CONFIRMATION_TTL_SECONDS` (15 min, `apps/api/edecan_api/
routers/conversations.py`) después, y el propio discovery puede venir de una
caché por tenant de hasta 60s (`_MCP_TOOLS_CACHE_TTL_SECONDS`, `apps/api/
edecan_api/deps.py`). Un host que resolvía público al momento de validar
puede haber sido re-apuntado (DNS rebinding) a una IP privada/loopback/
metadata para cuando la conexión real ocurre — la garantía de SSRF de
`seguridad.py` ("SSRF SIEMPRE bloqueada") no se cumplía en el único momento
que de verdad importa. Por eso `_MCPRemoteTool.run` vuelve a llamar
`validar_url_mcp` con el mismo `local_mode` del discovery, espalda con
espalda con `_build_transport` — mismo patrón que `edecan_browser.tools.
_fetch_y_extraer` con `check_navigation` justo antes del fetch real.

## Escaneo heurístico de nombre/descripción (defensa en profundidad, no bloqueante)

`_tools_de_un_servidor` corre `seguridad.escanear_descripcion_tool_mcp` sobre
`"{name} {description}"` de cada tool remota y, si encuentra algo, deja un
`logger.warning` — nunca oculta la tool ni bloquea el descubrimiento (ver el
docstring de `seguridad.py`, sección "Escaneo heurístico de descripciones de
tools remotas": la mitigación primaria es `dangerous=True` sin excepción,
este escaneo es una señal extra para auditoría, no una garantía).

## v1 SIN caché de sesión (documentado, no un descuido)

`_MCPRemoteTool.run` abre transporte → `initialize` → `call_tool` → cierra
en CADA llamada — nunca reutiliza una sesión MCP entre turnos ni entre
llamadas de un mismo turno. Costo: un handshake completo (ida y vuelta de
red, o arrancar un subprocess entero para `stdio`) por cada invocación de
tool, no solo por servidor. `TODO` (fuera de alcance de v1): cachear un
`MCPClient` ya inicializado por `(tenant_id, nombre_servidor)` con una
expiración corta (algunos minutos), invalidada si el servidor deja de
responder — evaluar si el costo real lo justifica antes de construirlo.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shlex
from dataclasses import dataclass
from typing import Any

from edecan_core.tools.base import Tool, ToolContext, ToolResult

from .client import MCPClient, MCPClientError
from .seguridad import MCPSeguridadError, escanear_descripcion_tool_mcp, validar_url_mcp
from .transport import HTTPTransport, MCPTransport, MCPTransportError, StdioTransport

logger = logging.getLogger(__name__)

TOOL_NAME_PREFIX = "mcp_"
TOOL_NAME_MAX_LENGTH = 64
REQUIRES_FLAG_MCP = "tools.mcp"

_SLUG_INVALIDO_RE = re.compile(r"[^a-z0-9_]+")
_HASH_SUFFIX_LEN = 6


@dataclass(frozen=True)
class MCPServerConfig:
    """Config NO secreta de un servidor MCP del tenant — `ARCHITECTURE.md`
    §15 `provider_config {nombre, transporte, url?, comando?}`. Los headers
    (secretos) viajan aparte, ver `vault_headers_por_slug` en
    `construir_tools_mcp`."""

    nombre: str
    transporte: str  # "http" | "stdio"
    url: str | None = None
    comando: str | None = None


def sanear_slug(texto: str) -> str:
    """`[a-z0-9_]`, colapsa cualquier corrida de caracteres inválidos en un
    solo `_`, recorta `_` en los extremos — nunca devuelve vacío (cae a
    `"servidor"`/`"tool"` según el llamador si el resultado queda vacío)."""
    slug = _SLUG_INVALIDO_RE.sub("_", texto.strip().lower()).strip("_")
    return slug or "x"


def _nombre_tool(slug_servidor: str, slug_tool: str) -> str:
    """`mcp_{slug_servidor}_{slug_tool}`, truncado con hash de
    desambiguación si supera `TOOL_NAME_MAX_LENGTH` — ver docstring del
    módulo."""
    completo = f"{TOOL_NAME_PREFIX}{slug_servidor}_{slug_tool}"
    if len(completo) <= TOOL_NAME_MAX_LENGTH:
        return completo

    sufijo = hashlib.sha256(completo.encode("utf-8")).hexdigest()[:_HASH_SUFFIX_LEN]
    # Espacio disponible para las dos mitades saneadas: total − prefijo −
    # "_" (antes del sufijo) − sufijo − "_" (entre servidor y tool).
    disponible = TOOL_NAME_MAX_LENGTH - len(TOOL_NAME_PREFIX) - len(sufijo) - 2
    disponible = max(disponible, 2)
    mitad_servidor = max(1, disponible // 2)
    mitad_tool = max(1, disponible - mitad_servidor)
    return f"{TOOL_NAME_PREFIX}{slug_servidor[:mitad_servidor]}_{slug_tool[:mitad_tool]}_{sufijo}"


def _normalizar_input_schema(schema: Any) -> dict[str, Any]:
    """Passthrough si `schema` ya es un JSON Schema de objeto; si no (falta,
    no es dict, o es de otro `type`), envuelve en un objeto permisivo — la
    validación real de argumentos la sigue haciendo el propio servidor MCP
    al ejecutar la tool."""
    if isinstance(schema, dict) and schema.get("type") == "object":
        return schema
    return {"type": "object", "properties": {}, "additionalProperties": True}


def _build_transport(config: MCPServerConfig, headers: dict[str, str]) -> MCPTransport:
    if config.transporte == "stdio":
        comando = shlex.split(config.comando or "")
        return StdioTransport(comando)
    return HTTPTransport(config.url or "", headers=headers)


class _MCPRemoteTool(Tool):
    """Una tool remota de UN servidor MCP concreto — ver el docstring del
    módulo para `dangerous`/costo/nomenclatura."""

    dangerous = True

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        server_config: MCPServerConfig,
        remote_tool_name: str,
        headers: dict[str, str],
        local_mode: bool,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.requires_flags = frozenset({REQUIRES_FLAG_MCP})
        self._server_config = server_config
        self._remote_tool_name = remote_tool_name
        self._headers = headers
        self._local_mode = local_mode

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        del ctx  # no usado: la config/credenciales ya vienen cerradas por adelantado
        # Revalida SSRF acá, espalda con espalda con `_build_transport` — ver
        # "SSRF revalidado en la ejecución real" en el docstring del módulo:
        # el chequeo del discovery puede tener minutos de antigüedad (caché de
        # tools + espera de confirmación humana) y un DNS rebinding lo
        # invalidaría en silencio si no se repite acá.
        if self._server_config.transporte == "http":
            try:
                await validar_url_mcp(self._server_config.url or "", local_mode=self._local_mode)
            except MCPSeguridadError as exc:
                return ToolResult(
                    content=(
                        f"No pude ejecutar «{self._remote_tool_name}» en el servidor MCP "
                        f"«{self._server_config.nombre}»: {exc}"
                    )
                )

        try:
            transport = _build_transport(self._server_config, self._headers)
        except ValueError as exc:
            return ToolResult(
                content=(
                    f"No pude conectar con el servidor MCP «{self._server_config.nombre}»: {exc}"
                )
            )

        client = MCPClient(transport)
        try:
            await client.initialize()
            resultado = await client.call_tool(self._remote_tool_name, args)
            return ToolResult(content=resultado)
        except (MCPClientError, MCPTransportError) as exc:
            return ToolResult(
                content=(
                    f"No pude ejecutar «{self._remote_tool_name}» en el servidor MCP "
                    f"«{self._server_config.nombre}»: {exc}"
                )
            )
        finally:
            await client.close()


async def _tools_de_un_servidor(
    config: MCPServerConfig, headers: dict[str, str], *, local_mode: bool
) -> list[Tool]:
    """Descubre las tools de UN servidor — nunca lanza: cualquier problema
    (config inválida, red caída, timeout, servidor mal comportado) se
    registra con `logger.warning` y devuelve `[]`, para que
    `construir_tools_mcp` pueda seguir con los demás servidores del tenant
    (best-effort por servidor, mismo criterio que el loader de referencia de
    OpenJarvis, ver `NOTICE`)."""
    if config.transporte == "stdio":
        if not local_mode:
            logger.warning(
                "Servidor MCP «%s» configurado como stdio pero local_mode=False; se omite "
                "(defensa en profundidad — PUT /v1/mcp/servers ya debería haberlo rechazado).",
                config.nombre,
            )
            return []
    elif config.transporte == "http":
        try:
            await validar_url_mcp(config.url or "", local_mode=local_mode)
        except MCPSeguridadError as exc:
            logger.warning(
                "Servidor MCP «%s» con URL rechazada (%s); se omite.", config.nombre, exc
            )
            return []
    else:
        logger.warning(
            "Servidor MCP «%s» con transporte desconocido %r; se omite.",
            config.nombre,
            config.transporte,
        )
        return []

    try:
        transport = _build_transport(config, headers)
    except ValueError as exc:
        logger.warning("Servidor MCP «%s»: %s; se omite.", config.nombre, exc)
        return []

    slug_servidor = sanear_slug(config.nombre)
    client = MCPClient(transport)
    try:
        await client.initialize()
        remotas = await client.list_tools()
    except (MCPClientError, MCPTransportError) as exc:
        logger.warning("No se pudo listar tools del servidor MCP «%s»: %s", config.nombre, exc)
        return []
    finally:
        await client.close()

    tools: list[Tool] = []
    for remota in remotas:
        slug_tool = sanear_slug(remota["name"])
        nombre_final = _nombre_tool(slug_servidor, slug_tool)
        descripcion = f"[MCP:{config.nombre}] {remota.get('description', '')}".strip()

        # Defensa en profundidad NO bloqueante — ver "Escaneo heurístico de
        # nombre/descripción" en el docstring del módulo y `seguridad.py`. La
        # tool sigue disponible igual (dangerous=True ya exige confirmación
        # humana antes de ejecutarla); esto solo deja rastro para auditoría.
        texto_a_escanear = f"{remota.get('name', '')} {remota.get('description', '')}"
        hallazgos = escanear_descripcion_tool_mcp(texto_a_escanear)
        if hallazgos:
            logger.warning(
                "Servidor MCP «%s», tool «%s»: posible intento de manipulación en su "
                "nombre/descripción (%s) — sigue disponible (dangerous=True exige "
                "confirmación humana antes de ejecutarla de todos modos), queda "
                "registrado para auditoría.",
                config.nombre,
                remota.get("name"),
                ", ".join(sorted({h.patron for h in hallazgos})),
            )

        tools.append(
            _MCPRemoteTool(
                name=nombre_final,
                description=descripcion,
                input_schema=_normalizar_input_schema(remota.get("input_schema")),
                server_config=config,
                remote_tool_name=remota["name"],
                headers=headers,
                local_mode=local_mode,
            )
        )
    return tools


async def construir_tools_mcp(
    configs: list[MCPServerConfig],
    vault_headers_por_slug: dict[str, dict[str, str]],
    *,
    local_mode: bool,
) -> list[Tool]:
    """Descubre en vivo las tools de cada servidor MCP configurado por el
    tenant y las adapta a `Tool` de `edecan_core` — ver el docstring del
    módulo (nomenclatura, `dangerous`, costo v1 sin caché de sesión).

    `vault_headers_por_slug` mapea `sanear_slug(config.nombre) -> headers`
    (los headers SECRETOS de cada servidor, ya descifrados del `TokenVault`
    por el llamador — este módulo nunca toca el vault directamente, ver
    `apps/api/edecan_api/deps.py::get_mcp_tools_for_tenant`/
    `apps/worker/edecan_worker/deps.py::Deps.mcp_tools_para`).

    Un servidor caído/rechazado/mal configurado NUNCA rompe a los demás — se
    registra un `logger.warning` y se omite (best-effort por servidor); esta
    función en sí tampoco lanza salvo por un bug real de programación (los
    `except` de cada servidor ya cubren fallos de red/protocolo/seguridad).
    """
    tools: list[Tool] = []
    for config in configs:
        headers = vault_headers_por_slug.get(sanear_slug(config.nombre), {})
        try:
            tools.extend(await _tools_de_un_servidor(config, headers, local_mode=local_mode))
        except Exception:  # noqa: BLE001 - un servidor nunca debe tumbar a los demás
            logger.warning(
                "Error inesperado construyendo tools del servidor MCP «%s»",
                config.nombre,
                exc_info=True,
            )
    return tools


__all__ = [
    "MCPServerConfig",
    "REQUIRES_FLAG_MCP",
    "TOOL_NAME_MAX_LENGTH",
    "TOOL_NAME_PREFIX",
    "construir_tools_mcp",
    "sanear_slug",
]
