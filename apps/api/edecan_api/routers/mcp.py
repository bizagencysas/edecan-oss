"""`/v1/mcp/*` — MCP (Model Context Protocol) bring-your-own por tenant
(`ARCHITECTURE.md` §15.g, contrato PINNED por el linchpin de v6; `DIRECCION_
ACTUAL.md` "Modelo de credenciales: TODO lo trae el cliente, siempre";
`docs/mcp.md`).

Este router NO se monta a sí mismo: `edecan_api.main` (`V6_ROUTER_NAMES`,
linchpin de v6) lo monta de forma defensiva, igual que el resto de routers
v2+ (`importlib.import_module` + `try/except ImportError` + `logger.warning`
si falta) — este módulo solo declara `router`.

## Qué resuelve

Cada tenant conecta SUS PROPIOS servidores MCP (un servidor puede exponer
tools arbitrarias — buscar, consultar una base de datos propia, controlar un
sistema interno, lo que sea que ese servidor implemente) y, si el plan trae
el flag `tools.mcp`, esas tools aparecen en el chat/misiones/automatizaciones
como herramientas más del agente (`packages/mcp/edecan_mcp/tool_adapter.py`,
`apps/api/edecan_api/deps.py::get_mcp_tools_for_tenant`).

A diferencia de "llm"/"voice_stt"/"voice_tts"/"images"/"search"
(`routers/credentials.py`, singleton por tenant — UNA config activa a la
vez), `connector_key="mcp"` es MÚLTIPLE por tenant, igual que OAuth
(`ARCHITECTURE.md` §10.8): un tenant puede conectar varios servidores MCP a
la vez, cada uno identificado por su `nombre` (`external_account_id` — el
`UniqueConstraint(tenant_id, connector_key, external_account_id)` que
`connector_accounts` ya tiene desde v1 alcanza sin ninguna migración nueva).

## Dónde vive la config (§15.g, PINNED — no reabrir)

TODO (`nombre`, `transporte`, `url`, `comando`, `headers` y `env`) viaja JUNTO en un
único blob cifrado: `TokenBundle.access_token` (`token_type="config"`) guarda
el JSON `{nombre, transporte, url?, comando?, headers?, env?}`
(`edecan_mcp.provider_config.serializar_config_mcp`/`deserializar_config_mcp`)
— mismo criterio que `LLMProviderConfig`/`"ads"`/`"vehicles"` (§12.c/§13.d).
`connector_accounts` en sí NO lleva ninguna columna de config (ni siquiera la
parte no-secreta): `scopes` queda vacío. Consecuencia directa: `GET
/servers` SÍ necesita el vault (a diferencia de un diseño anterior de este
mismo router que evitaba tocarlo para listar) — pero la respuesta NUNCA
incluye `headers` de todos modos, ver `_servidor_out`.

## Upsert sin `UPDATE` en el repo

`Repo.create_connector_account` (`apps/api/edecan_api/repo.py`, fuera de las
rutas que este WP puede tocar) hace un `INSERT` liso — repetirlo con el mismo
`(tenant_id, connector_key, external_account_id)` violaría el `UniqueConstraint`
de `connector_accounts`. Como este WP tampoco puede agregar un `UPDATE` al
repo, `PUT /v1/mcp/servers` emula upsert borrando primero la fila existente
(si la hay) y creando una nueva — `ON DELETE CASCADE` en `oauth_tokens`
(`connector_account_id`) se lleva el vault viejo con ella, así que no queda
ningún residuo huérfano.

## "Pegar y validar" (`DIRECCION_ACTUAL.md`)

`PUT /v1/mcp/servers` acepta `validate: bool = true` (default): antes de
guardar nada hace el *handshake* MCP real (`initialize` + `tools/list`) contra
el servidor — si falla, `400` con el detalle exacto y nada se persiste.
`validate: false` es la escotilla de escape (tests, migraciones).

## Evidencia de auditoría

Los `repo.add_audit_log(...)` de `PUT`/`DELETE` no tienen ningún `raise`
DESPUÉS en el mismo camino de código — no aplica el patrón de commit
explícito de `HOTFIXES_PENDIENTES.md` puntos 8/9 (ese guardrail es para
cuando SÍ hay una excepción que se lanza tras escribir evidencia dentro de la
misma transacción; acá el audit log es la ÚLTIMA operación de cada handler).
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import time
import uuid
from typing import Any

from edecan_db.vault import TokenVault
from edecan_mcp import (
    HTTPTransport,
    MCPClient,
    MCPClientError,
    MCPSeguridadError,
    MCPServerConfig,
    MCPTransportError,
    StdioTransport,
    deserializar_config_mcp,
    serializar_config_mcp,
    validar_comando_mcp,
    validar_url_mcp,
)
from edecan_schemas import TokenBundle
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    CurrentUser,
    get_current_user,
    get_repo,
    get_tenant_session,
    get_vault,
    rate_limit,
)
from edecan_api.repo import Repo

logger = logging.getLogger(__name__)

# Mismo criterio que `edecan_api/routers/viajes.py` con `FLAG_TOOLS_TRAVEL`:
# `edecan_schemas.plans.FLAG_TOOLS_MCP` ya está pinned por el linchpin de v6
# (`ARCHITECTURE.md` §15.c) — el `try/except` es una red de seguridad extra
# por si este router se importa desde un checkout parcial que todavía no
# sincronizó ese cambio, nunca una fuente de verdad distinta (mismo valor
# string en ambos casos).
try:
    from edecan_schemas.plans import FLAG_TOOLS_MCP
except ImportError:  # pragma: no cover - checkout parcial
    FLAG_TOOLS_MCP = "tools.mcp"

router = APIRouter(prefix="/v1/mcp", tags=["mcp"], dependencies=[Depends(rate_limit)])

MCP_CONNECTOR_KEY = "mcp"
_DISPLAY_NAME_PREFIX = "MCP: "
_TRANSPORTES_VALIDOS = frozenset({"http", "stdio"})
_VALIDATE_TIMEOUT_SECONDS = 15.0
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_RESERVED_ENV_NAMES = frozenset({"PATH", "HOME"})
_MAX_ENV_ENTRIES = 32
_MAX_ENV_VALUE_LENGTH = 16_384
_SENSITIVE_ARGUMENT_PARTS = ("token", "secret", "password", "passwd", "api_key", "apikey")

# Salud por-servidor (directiva §27, tabla `mcp_server_health`, migración
# `0056_mcp_server_health`). Vocabulario pinned en el CHECK de la tabla.
_HEALTH_OPERATIONAL = "operational"
_HEALTH_DEGRADED = "degraded"
_HEALTH_AUTH_REQUIRED = "auth_required"
_HEALTH_UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# Gate de flag de plan — mismo patrón que `edecan_api.routers.viajes.
# _require_tools_travel`/`edecan_api.routers.ads._require_tools_ads`.
# ---------------------------------------------------------------------------


async def _require_tools_mcp(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current_user.tenant.flags.get(FLAG_TOOLS_MCP, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MCP Servers no está disponible en tu plan.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Bodies / respuestas
# ---------------------------------------------------------------------------


class MCPServerIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    nombre: str
    transporte: str
    url: str | None = None
    comando: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    validate_: bool = Field(default=True, alias="validate")


class MCPServerOut(BaseModel):
    nombre: str
    transporte: str
    url: str | None = None
    comando: str | None = None
    estado: str
    autenticacion_configurada: bool = False
    health: str = _HEALTH_UNAVAILABLE
    latency_ms: int | None = None
    last_error: str | None = None


class MCPToolOut(BaseModel):
    name: str
    description: str


class MCPToolsOut(BaseModel):
    tools: list[MCPToolOut]


# ---------------------------------------------------------------------------
# Helpers de `connector_accounts` — MÚLTIPLES por tenant (keyed por `nombre`,
# no singleton), a diferencia de `credentials.py`/`smarthome.py`/`ads.py`.
# ---------------------------------------------------------------------------


async def _find_mcp_account(repo: Repo, tenant_id: uuid.UUID, nombre: str) -> dict[str, Any] | None:
    cuentas = await repo.list_connector_accounts(tenant_id=tenant_id)
    for cuenta in cuentas:
        if cuenta["connector_key"] == MCP_CONNECTOR_KEY and cuenta["external_account_id"] == nombre:
            return cuenta
    return None


async def _list_mcp_accounts(repo: Repo, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    cuentas = await repo.list_connector_accounts(tenant_id=tenant_id)
    return [c for c in cuentas if c["connector_key"] == MCP_CONNECTOR_KEY]


async def _cargar_config(
    vault: TokenVault, tenant_id: uuid.UUID, cuenta: dict[str, Any]
) -> tuple[MCPServerConfig, dict[str, str]]:
    """Descifra y deserializa la config completa de una `connector_account`
    MCP (`ARCHITECTURE.md` §15.g) — `(config, headers)`. Tolerante: sin fila
    en el vault, `deserializar_config_mcp(None, ...)` ya devuelve un
    `MCPServerConfig` con `transporte=""` (nunca revienta)."""
    bundle = await vault.get(tenant_id, cuenta["id"])
    raw = bundle.access_token if bundle is not None else None
    return deserializar_config_mcp(raw, nombre_fallback=cuenta["external_account_id"])


def _comando_seguro_para_respuesta(comando: str | None) -> str | None:
    """Conserva un comando útil para diagnóstico sin repetir secretos de
    configuraciones antiguas que usaban `env TOKEN=...` o `--token ...`.

    Las configuraciones nuevas deben usar `MCPServerIn.env`, que nunca sale
    por API. Este redactor mantiene compatibilidad de lectura con filas
    anteriores y cierra la fuga sin borrar ni mutar su config cifrada.
    """
    if not comando:
        return comando
    try:
        argumentos = shlex.split(comando)
    except ValueError:
        return "[comando local configurado]"

    seguros: list[str] = []
    ocultar_siguiente = False
    for argumento in argumentos:
        if ocultar_siguiente:
            seguros.append("••••")
            ocultar_siguiente = False
            continue
        if "=" in argumento:
            clave, _valor = argumento.split("=", 1)
            if _ENV_NAME_RE.fullmatch(clave):
                seguros.append(f"{clave}=••••")
                continue
            flag = clave.lstrip("-").lower().replace("-", "_")
            if any(parte in flag for parte in _SENSITIVE_ARGUMENT_PARTS):
                seguros.append(f"{clave}=••••")
                continue
        flag = argumento.lstrip("-").lower().replace("-", "_")
        if argumento.startswith("-") and any(parte in flag for parte in _SENSITIVE_ARGUMENT_PARTS):
            seguros.append(argumento)
            ocultar_siguiente = True
            continue
        seguros.append(argumento)
    return shlex.join(seguros)


def _servidor_out(
    cuenta: dict[str, Any],
    config: MCPServerConfig,
    headers: dict[str, str],
    salud: dict[str, Any] | None = None,
) -> MCPServerOut:
    """`MCPServerOut` — NUNCA incluye `headers` aunque `_cargar_config` los
    haya descifrado para otro propósito (p. ej. `get_server_tools`, que sí
    los necesita para conectar pero tampoco los devuelve en su respuesta).

    `salud` es la fila de `mcp_server_health` del servidor (o `None` si no hay
    registro todavía): se proyecta a `health`/`latency_ms`/`last_error`.
    """
    salud = salud or {}
    return MCPServerOut(
        nombre=config.nombre,
        transporte=config.transporte,
        url=config.url,
        comando=_comando_seguro_para_respuesta(config.comando),
        estado=cuenta.get("status", "active"),
        autenticacion_configurada=bool(headers or config.env),
        health=str(salud.get("health") or _HEALTH_UNAVAILABLE),
        latency_ms=salud.get("last_latency_ms"),
        last_error=salud.get("last_error"),
    )


# ---------------------------------------------------------------------------
# Salud por-servidor (`mcp_server_health`, directiva §27) — helpers defensivos.
#
# Viven en la MISMA transacción del request (`get_tenant_session`): un
# handshake exitoso (PUT validate=true / GET .../tools) graba `operational` +
# latencia y comitea con el 2xx; un fallo de handshake hace rollback, igual que
# el resto del request (y para PUT validate=true ni siquiera existe un servidor
# que mostrar: la config no se persistió). `session is None` (tests con fakes)
# convierte cada helper en no-op — la salud cae a sus defaults.
# ---------------------------------------------------------------------------


async def _leer_salud(
    session: AsyncSession | None, tenant_id: uuid.UUID
) -> dict[str, dict[str, Any]]:
    if session is None:
        return {}
    try:
        result = await session.execute(
            text(
                "SELECT server_name, health, last_latency_ms, last_error, last_checked_at "
                "FROM mcp_server_health WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": str(tenant_id)},
        )
        return {str(row["server_name"]): dict(row) for row in result.mappings().all()}
    except Exception:  # noqa: BLE001 - tabla ausente en una instalación aún sin migrar
        logger.warning("mcp: no se pudo leer mcp_server_health", exc_info=True)
        return {}


async def _grabar_salud(
    session: AsyncSession | None,
    tenant_id: uuid.UUID,
    nombre: str,
    *,
    health: str,
    latency_ms: int | None = None,
    last_error: str | None = None,
) -> None:
    if session is None:
        return
    try:
        await session.execute(
            text(
                "INSERT INTO mcp_server_health "
                "(tenant_id, server_name, health, last_latency_ms, last_error, "
                "last_checked_at, updated_at) "
                "VALUES (:tenant_id, :server_name, :health, :latency, :error, now(), now()) "
                "ON CONFLICT (tenant_id, server_name) DO UPDATE SET "
                "health = EXCLUDED.health, "
                "last_latency_ms = EXCLUDED.last_latency_ms, "
                "last_error = EXCLUDED.last_error, "
                "last_checked_at = now(), updated_at = now()"
            ),
            {
                "tenant_id": str(tenant_id),
                "server_name": nombre,
                "health": health,
                "latency": latency_ms,
                "error": last_error,
            },
        )
    except Exception:  # noqa: BLE001 - la salud es best-effort, nunca rompe el request
        logger.warning("mcp: no se pudo grabar salud de «%s»", nombre, exc_info=True)


async def _borrar_salud(
    session: AsyncSession | None, tenant_id: uuid.UUID, nombre: str
) -> None:
    if session is None:
        return
    try:
        await session.execute(
            text(
                "DELETE FROM mcp_server_health "
                "WHERE tenant_id = :tenant_id AND server_name = :server_name"
            ),
            {"tenant_id": str(tenant_id), "server_name": nombre},
        )
    except Exception:  # noqa: BLE001
        logger.warning("mcp: no se pudo borrar salud de «%s»", nombre, exc_info=True)


# ---------------------------------------------------------------------------
# Resumen de salud por-servidor (directiva §27) — agregado de `mcp_server_health`
# apto para un tablero. Función pura (nada de I/O) para que la lógica de
# agregación se pruebe en determinismo, sin HTTP ni DB; el endpoint solo junta
# las dos fuentes (configurados + salud) y delega aquí.
# ---------------------------------------------------------------------------

_MCP_HEALTH_FORMAT = "edecan-mcp-health.v1"
_HEALTH_STATUSES = (
    _HEALTH_OPERATIONAL,
    _HEALTH_DEGRADED,
    _HEALTH_AUTH_REQUIRED,
    _HEALTH_UNAVAILABLE,
)


def _mcp_health_summary(
    salud: dict[str, dict[str, Any]], configured_names: list[str]
) -> dict[str, Any]:
    """Agrega `mcp_server_health` a una instantánea de salud.

    `salud` es `{server_name: row}` (lo que devuelve `_leer_salud`), normalizado
    a `str` de clave para que case con `external_account_id`. Un servidor
    CONFIGURADO que aún no tiene fila de salud cuenta como `unchecked` (nunca
    validado desde que se configuró) y degrada el resumen — la ausencia de
    muestra no es evidencia de que el servidor esté bien.

    Estatus agregado (misma semántica que el vocabulario por-servidor):
    `unavailable` si algún servidor está `unavailable`/`auth_required`;
    si no, `degraded` si alguno está `degraded` o queda algún `unchecked`;
    si no, `operational`. Nunca lanza: los datos de salud son best-effort y un
    endpoint de diagnóstico no debe caerse por una fila corrupta.
    """
    by_status: dict[str, int] = {nombre: 0 for nombre in (*_HEALTH_STATUSES, "unknown")}
    latency_values: list[int] = []
    servers: list[dict[str, Any]] = []

    for name, row in salud.items():
        name = str(name)
        health = str(row.get("health") or _HEALTH_UNAVAILABLE)
        if health not in by_status:
            health = "unknown"
        by_status[health] += 1
        latency = row.get("last_latency_ms")
        if isinstance(latency, int):
            latency_values.append(latency)
        servers.append(
            {
                "server_name": name,
                "health": health,
                "latency_ms": latency,
                "last_error": row.get("last_error"),
                "last_checked_at": row.get("last_checked_at"),
            }
        )

    checked = len(salud)
    configured = len(configured_names)
    unchecked = max(0, configured - checked)
    operational = by_status[_HEALTH_OPERATIONAL]

    if by_status[_HEALTH_UNAVAILABLE] or by_status[_HEALTH_AUTH_REQUIRED]:
        status_value = _HEALTH_UNAVAILABLE
    elif by_status[_HEALTH_DEGRADED] or by_status["unknown"] or unchecked:
        status_value = _HEALTH_DEGRADED
    else:
        status_value = _HEALTH_OPERATIONAL

    return {
        "format": _MCP_HEALTH_FORMAT,
        "status": status_value,
        "configured": configured,
        "checked": checked,
        "unchecked": unchecked,
        "by_status": by_status,
        "operational_rate": round(operational / checked, 2) if checked else None,
        "avg_latency_ms": (
            round(sum(latency_values) / len(latency_values), 1) if latency_values else None
        ),
        "max_latency_ms": max(latency_values) if latency_values else None,
        "servers": servers,
    }


# ---------------------------------------------------------------------------
# Conexión real (validar/handshake/listar) — comparte el armado del
# transporte con `edecan_mcp.tool_adapter._build_transport`, pero ese helper
# es privado del paquete (no forma parte de su API pública, ver
# `edecan_mcp/__init__.py`) — este router arma el transporte a mano con la
# misma lógica de dos líneas en vez de importar un símbolo privado ajeno.
# ---------------------------------------------------------------------------


def _build_transport(config: MCPServerConfig, headers: dict[str, str]) -> Any:
    if config.transporte == "stdio":
        return StdioTransport(shlex.split(config.comando or ""), env=config.env)
    return HTTPTransport(config.url or "", headers=headers)


async def _abrir_cliente(
    config: MCPServerConfig, headers: dict[str, str], *, local_mode: bool
) -> MCPClient:
    if config.transporte == "stdio":
        comando = shlex.split(config.comando or "")
        validar_comando_mcp(comando, local_mode=local_mode)
    else:
        await validar_url_mcp(config.url or "", local_mode=local_mode)
    return MCPClient(_build_transport(config, headers))


async def _handshake_real(
    config: MCPServerConfig, headers: dict[str, str], *, local_mode: bool
) -> None:
    """`initialize` + `tools/list` reales contra el servidor — usado por
    `PUT .../servers` (`validate=true`). Lanza `HTTPException(400)` con el
    detalle EXACTO si algo falla; nunca deja pasar una credencial/URL/comando
    sin probar (`DIRECCION_ACTUAL.md` "pegar y validar")."""
    try:
        client = await _abrir_cliente(config, headers, local_mode=local_mode)
    except (MCPSeguridadError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        await asyncio.wait_for(client.initialize(), timeout=_VALIDATE_TIMEOUT_SECONDS)
        await asyncio.wait_for(client.list_tools(), timeout=_VALIDATE_TIMEOUT_SECONDS)
    except (MCPClientError, MCPTransportError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No se pudo conectar con el servidor MCP: {exc}",
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El servidor MCP no respondió en {_VALIDATE_TIMEOUT_SECONDS:.0f}s.",
        ) from exc
    finally:
        await client.close()


async def _listar_tools_en_vivo(
    config: MCPServerConfig, headers: dict[str, str], *, local_mode: bool
) -> list[dict[str, Any]]:
    """Usado por `GET .../servers/{nombre}/tools` — deja que
    `MCPSeguridadError`/`MCPClientError`/`MCPTransportError`/`ValueError`
    suban tal cual, el endpoint las traduce a `400` (ver `get_server_tools`)."""
    client = await _abrir_cliente(config, headers, local_mode=local_mode)
    try:
        await client.initialize()
        return await client.list_tools()
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# GET /v1/mcp/servers — lista `provider_config`; NUNCA headers/secretos en
# la respuesta (aunque, a diferencia de un diseño anterior de este router,
# SÍ toca el vault para leerlos — ver docstring del módulo, §15.g pinned).
# ---------------------------------------------------------------------------


@router.get("/servers", response_model=list[MCPServerOut])
async def list_servers(
    current_user: CurrentUser = Depends(_require_tools_mcp),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
    session: AsyncSession | None = Depends(get_tenant_session),
) -> list[MCPServerOut]:
    cuentas = await _list_mcp_accounts(repo, current_user.tenant_id)
    salud_por_servidor = await _leer_salud(session, current_user.tenant_id)
    salida: list[MCPServerOut] = []
    for cuenta in cuentas:
        config, headers = await _cargar_config(vault, current_user.tenant_id, cuenta)
        salida.append(
            _servidor_out(
                cuenta,
                config,
                headers,
                salud_por_servidor.get(str(cuenta["external_account_id"])),
            )
        )
    return salida


@router.get("/health")
async def mcp_health_summary(
    current_user: CurrentUser = Depends(_require_tools_mcp),
    repo: Repo = Depends(get_repo),
    session: AsyncSession | None = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Resumen agregado de `mcp_server_health` del tenant (directiva §27).

    A diferencia de `GET /servers` (que proyecta salud por servidor), esto
    devuelve el estado consolidado para un tablero: conteo por estado,
    porcentaje operativo, latencia promedio/máxima y la lista de servidores
    con su marca de última verificación. La misma transacción del request
    comitea las lecturas; `session is None` (tests con fakes) degrada a vacío.
    """
    cuentas = await _list_mcp_accounts(repo, current_user.tenant_id)
    configured_names = [str(cuenta["external_account_id"]) for cuenta in cuentas]
    salud = await _leer_salud(session, current_user.tenant_id)
    return _mcp_health_summary(salud, configured_names)


# ---------------------------------------------------------------------------
# PUT /v1/mcp/servers
# ---------------------------------------------------------------------------


@router.put("/servers", status_code=status.HTTP_204_NO_CONTENT)
async def put_server(
    payload: MCPServerIn,
    current_user: CurrentUser = Depends(_require_tools_mcp),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
    session: AsyncSession | None = Depends(get_tenant_session),
) -> None:
    nombre = payload.nombre.strip()
    if not nombre:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="nombre no puede estar vacío."
        )
    transporte = payload.transporte.strip().lower()
    if transporte not in _TRANSPORTES_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"transporte desconocido: {payload.transporte!r}. Debe ser 'http' o 'stdio'.",
        )

    local_mode = bool(getattr(settings, "EDECAN_LOCAL_MODE", False))

    if transporte == "stdio":
        comando_str = (payload.comando or "").strip()
        try:
            validar_comando_mcp(shlex.split(comando_str), local_mode=local_mode)
        except MCPSeguridadError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        url = None
        env = dict(payload.env or {})
        if len(env) > _MAX_ENV_ENTRIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Un servidor MCP admite como máximo {_MAX_ENV_ENTRIES} variables secretas.",
            )
        for clave, valor in env.items():
            if clave in _RESERVED_ENV_NAMES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{clave} está reservada por Edecan y no se puede reemplazar.",
                )
            if not _ENV_NAME_RE.fullmatch(clave):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"«{clave}» no es un nombre válido de variable de entorno.",
                )
            if "\x00" in valor or len(valor) > _MAX_ENV_VALUE_LENGTH:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"El valor de «{clave}» no tiene un formato o tamaño permitido.",
                )
    else:
        url = (payload.url or "").strip()
        if not url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="url es obligatoria para transporte 'http'.",
            )
        try:
            await validar_url_mcp(url, local_mode=local_mode)
        except MCPSeguridadError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        comando_str = None
        if payload.env:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Las variables secretas solo aplican a servidores MCP locales por stdio.",
            )
        env = {}

    config = MCPServerConfig(
        nombre=nombre,
        transporte=transporte,
        url=url or None,
        comando=comando_str or None,
        env=env or None,
    )
    headers = dict(payload.headers or {})

    if payload.validate_:
        inicio = time.perf_counter()
        await _handshake_real(config, headers, local_mode=local_mode)
        # Handshake real exitoso → `operational` + latencia medida (directiva
        # §27). Comitea junto con el 204 (misma transacción del request).
        await _grabar_salud(
            session,
            current_user.tenant_id,
            nombre,
            health=_HEALTH_OPERATIONAL,
            latency_ms=int((time.perf_counter() - inicio) * 1000),
        )

    # Upsert emulado (ver docstring del módulo): borra la fila existente (si
    # la hay) antes de crear la nueva — `ON DELETE CASCADE` se lleva el vault
    # viejo con ella.
    existente = await _find_mcp_account(repo, current_user.tenant_id, nombre)
    if existente is not None:
        await repo.delete_connector_account(
            tenant_id=current_user.tenant_id, account_id=existente["id"]
        )

    # `scopes=[]`: la config completa (secreta y no-secreta) vive SOLO del
    # lado cifrado (§15.g, "connector_accounts en sí NO lleva ninguna columna
    # de config", mismo criterio que "llm").
    cuenta = await repo.create_connector_account(
        tenant_id=current_user.tenant_id,
        connector_key=MCP_CONNECTOR_KEY,
        external_account_id=nombre,
        display_name=f"{_DISPLAY_NAME_PREFIX}{nombre}",
        scopes=[],
    )
    await vault.put(
        current_user.tenant_id,
        cuenta["id"],
        TokenBundle(access_token=serializar_config_mcp(config, headers), token_type="config"),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="mcp.server.connected",
        target=nombre,
        meta={"transporte": transporte},
    )


# ---------------------------------------------------------------------------
# DELETE /v1/mcp/servers/{nombre}
# ---------------------------------------------------------------------------


@router.delete("/servers/{nombre}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    nombre: str,
    current_user: CurrentUser = Depends(_require_tools_mcp),
    repo: Repo = Depends(get_repo),
    session: AsyncSession | None = Depends(get_tenant_session),
) -> None:
    cuenta = await _find_mcp_account(repo, current_user.tenant_id, nombre)
    if cuenta is None:
        return  # idempotente: nada que borrar ya es un estado válido de "desconectado".
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=cuenta["id"])
    await _borrar_salud(session, current_user.tenant_id, nombre)
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="mcp.server.disconnected",
        target=nombre,
    )


# ---------------------------------------------------------------------------
# GET /v1/mcp/servers/{nombre}/tools — conecta y lista en vivo.
# ---------------------------------------------------------------------------


@router.get("/servers/{nombre}/tools", response_model=MCPToolsOut)
async def get_server_tools(
    nombre: str,
    current_user: CurrentUser = Depends(_require_tools_mcp),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
    session: AsyncSession | None = Depends(get_tenant_session),
) -> MCPToolsOut:
    cuenta = await _find_mcp_account(repo, current_user.tenant_id, nombre)
    if cuenta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No hay un servidor MCP llamado «{nombre}» conectado.",
        )
    config, headers = await _cargar_config(vault, current_user.tenant_id, cuenta)
    local_mode = bool(getattr(settings, "EDECAN_LOCAL_MODE", False))

    inicio = time.perf_counter()
    try:
        tools = await asyncio.wait_for(
            _listar_tools_en_vivo(config, headers, local_mode=local_mode),
            timeout=_VALIDATE_TIMEOUT_SECONDS,
        )
    except (MCPSeguridadError, MCPClientError, MCPTransportError, ValueError) as exc:
        # Un fallo de conexión termina en 400 (rollback de la transacción), así
        # que acá no se graba `degraded`/`unavailable` durable: el enum completo
        # sigue cubierto por `_grabar_salud` para un health-check de fondo o el
        # próximo handshake exitoso. El 400 ya le dice al cliente qué falló.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No se pudo conectar con «{nombre}»: {exc}",
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"«{nombre}» no respondió en {_VALIDATE_TIMEOUT_SECONDS:.0f}s.",
        ) from exc

    await _grabar_salud(
        session,
        current_user.tenant_id,
        nombre,
        health=_HEALTH_OPERATIONAL,
        latency_ms=int((time.perf_counter() - inicio) * 1000),
    )
    return MCPToolsOut(
        tools=[MCPToolOut(name=t["name"], description=t.get("description", "")) for t in tools]
    )
