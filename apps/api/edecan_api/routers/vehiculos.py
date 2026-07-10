"""`/v1/vehiculos/*` — conector Smartcar, bring-your-own (`ARCHITECTURE.md`
§13, `DIRECCION_ACTUAL.md`, `ROADMAP_V2.md` §6.3; WP-V4-08; `docs/vehiculos.md`).

Este router NO se monta a sí mismo: `edecan_api.main` (dueño WP-V4-01, mismo
patrón que v2/v3 — `importlib.import_module` + `try/except ImportError`) lo
monta de forma defensiva — este módulo solo declara `router`.

## Qué es esto

Cada tenant crea SU PROPIA app en el dashboard de Smartcar
(https://dashboard.smartcar.com, gratis, con modo de prueba con vehículos
simulados) y conecta `client_id`/`client_secret` + un `refresh_token` inicial
obtenido con el flujo Connect de Smartcar (ver `docs/vehiculos.md`) — mismo
principio "pegar y validar" que `routers/smarthome.py`/`routers/credentials.py`
(`DIRECCION_ACTUAL.md` "Principio de UX no negociable"): `PUT
/v1/vehiculos/credentials` acepta `validate: bool = True` (default) — si es
`true`, antes de guardar nada se refresca el token contra Smartcar y se
confirma con un `GET /vehicles` real, devolviendo `400` con el detalle
exacto si Smartcar rechaza algo. `validate: false` es la escotilla de escape
(tests, o el propio dueño del proyecto sabiendo que está bien).

## Por qué este router NO importa `edecan_vehicles`

Mismo criterio, explicado con más detalle, que `routers/smarthome.py` (ver su
docstring): el ping de validación y las llamadas reales a Smartcar de este
router son, a propósito, `httpx.AsyncClient` puro y local a este módulo —
así este archivo no necesita que `apps/api` declare `edecan-vehicles` como
dependencia solo para que sus endpoints funcionen.

Nota de alcance (2026-07-08, ver `DIRECCION_ACTUAL.md` "Vehículos (Smartcar)
eliminado del alcance" y `ARCHITECTURE.md` §13.e): `edecan_vehicles` (el
paquete de tools, WP-V4-08) a propósito NO se declara dependencia de
`apps/api/pyproject.toml`, así que el entry point `edecan.tools` nunca
expone `vehiculo_estado`/`vehiculo_controlar` al agente en ningún build
real — a diferencia de `edecan-smarthome` (que sí se agregó como
dependencia real, ver ese comentario en `apps/api/pyproject.toml`), esto NO
es un problema pendiente de corregir: es una exclusión de producto
deliberada. Este router (endpoints HTTP directos) sigue funcionando
normalmente de todas formas, ver `docs/vehiculos.md`.

A diferencia de `routers/smarthome.py` (que SOLO maneja credenciales +
status, dejando list/estado/control exclusivamente como tools del agente),
este router SÍ expone list/estado/control como endpoints HTTP propios
(`GET ""`, `GET /{id}/estado`, `POST /{id}/puertas`) — por eso duplica un
poco más de la lógica de `edecan_vehicles.providers.SmartcarProvider` de lo
que duplicaba `smarthome.py` (que solo necesitaba un ping). Es la misma
compensación de siempre entre acoplamiento y una dependencia nueva de
`apps/api`, documentada aquí en vez de asumida.

## `connector_key` y forma del `TokenBundle` (mismo patrón que `"llm"`/`"images"`)

`connector_key = "vehicles"` (constante EXACTA — `edecan_vehicles.providers
.VEHICLES_CONNECTOR_KEY` usa la MISMA cadena literal, paquete hermano no
importado acá, ver arriba), **singleton por tenant** (una sola
`connector_account` por `(tenant_id, "vehicles")`, mismo criterio que
`"llm"`/`"voice_stt"`/`"images"`/`"homeassistant"`: `external_account_id` se
fija al propio `connector_key` porque no hay uno natural).
`TokenBundle.access_token` guarda el JSON `{"client_id", "client_secret",
"refresh_token"}` (`token_type="config"`, NO es un token OAuth crudo —
mismo criterio que `"llm"`/`"images"`/`"search"` en `routers/credentials.py`).

## Rotación del `refresh_token` de Smartcar

Smartcar rota el `refresh_token` en cada refresh (ver
`edecan_vehicles.providers`, docstring del módulo, y `docs/vehiculos.md`):
`_refrescar_y_persistir` es el único punto de este router que refresca el
access token fuera de `PUT /credentials` (que tiene su propio flujo, ya que
todavía no existe una `connector_account` la primera vez) — si Smartcar
devuelve un `refresh_token` distinto, lo persiste de vuelta en el vault ANTES
de devolver el `access_token`, así ninguna llamada subsiguiente usa un
`refresh_token` ya invalidado.

## `POST /{vehicle_id}/puertas` — auditoría siempre, incluso si Smartcar falla

Esta acción es la confirmación humana explícita en la UI (análoga a
confirmar una orden de `routers/commerce.py`, o a la vista remota de
`routers/remote.py`): `audit_log` se escribe SIEMPRE, éxito o error. En la
rama de error, el commit de esa evidencia ocurre EXPLÍCITAMENTE ANTES de
relanzar la excepción (`HOTFIXES_PENDIENTES.md` punto 8): `get_tenant_session`
envuelve TODA la request en una única transacción con ROLLBACK automático
ante cualquier excepción, así que sin ese commit el intento de
bloquear/desbloquear (y su motivo de fallo) se perdería justo cuando más
importa conservarlo. `repo` (`SqlRepo`) y `db_session` son la MISMA sesión
física: `get_repo` depende de `get_tenant_session`, y FastAPI cachea por
request cualquier dependencia pedida más de una vez con el mismo callable
(mismo patrón que `routers/remote.py::get_frame`/`routers/commerce.py
::confirm_order`, ver esos docstrings para el detalle completo).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx
from edecan_db.vault import TokenVault
from edecan_schemas import TokenBundle
from edecan_schemas.plans import FLAG_TOOLS_VEHICLES
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

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

router = APIRouter(prefix="/v1/vehiculos", tags=["vehiculos"], dependencies=[Depends(rate_limit)])

# Clave EXACTA pinned — `edecan_vehicles.providers.VEHICLES_CONNECTOR_KEY`
# (paquete hermano, no importado acá, ver docstring del módulo) usa la MISMA
# cadena literal para resolver la credencial desde `ctx.vault`.
VEHICLES_CONNECTOR_KEY = "vehicles"
_DISPLAY_NAME = "Smartcar"

_SMARTCAR_AUTH_URL = "https://auth.smartcar.com/oauth/token"
_SMARTCAR_API_BASE = "https://api.smartcar.com/v2.0"

# Ver `edecan_vehicles.providers._CAPABILITY_NOT_AVAILABLE_STATUSES` (mismo
# criterio, duplicado a propósito): status que significan "esta capability no
# está disponible para este vehículo/marca", nunca un error real.
_CAPABILITY_NOT_AVAILABLE = frozenset({403, 404, 409, 501})

_VALIDATE_TIMEOUT_SECONDS = 15.0
_STATUS_PING_TIMEOUT_SECONDS = 10.0

_ACCION_A_SMARTCAR = {"bloquear": "LOCK", "desbloquear": "UNLOCK"}


# ---------------------------------------------------------------------------
# Bodies / respuestas
# ---------------------------------------------------------------------------


class VehiculosCredentialsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_id: str
    client_secret: str
    refresh_token: str
    validate_: bool = Field(default=True, alias="validate")


class VehiculosStatusOut(BaseModel):
    configured: bool
    reachable: bool | None


class VehiculoOut(BaseModel):
    id: str
    marca: str | None
    modelo: str | None
    anio: int | None


class EstadoOut(BaseModel):
    vehicle_id: str
    bateria: dict[str, Any] | None
    combustible: dict[str, Any] | None
    odometro: float | None
    ubicacion: dict[str, float] | None


class PuertasIn(BaseModel):
    accion: str


class PuertasOut(BaseModel):
    vehicle_id: str
    accion: str
    status: str


# ---------------------------------------------------------------------------
# Gate de flag de plan `tools.vehicles`
# ---------------------------------------------------------------------------


async def require_vehicles_flag(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """TODO endpoint de este router pasa por acá en vez de `get_current_user`
    directo — mismo patrón que `routers/remote.py::_require_remote_view`.
    `FLAG_TOOLS_VEHICLES` (`edecan_schemas.plans`) ya está pinned (dueño
    WP-V4-01, `ARCHITECTURE.md` §13/§10.13)."""
    if not current_user.tenant.flags.get(FLAG_TOOLS_VEHICLES, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu plan no incluye vehículos (flag 'tools.vehicles').",
        )
    return current_user


# ---------------------------------------------------------------------------
# Smartcar — HTTP local y mínimo (ver docstring del módulo: por qué no se
# importa `edecan_vehicles`).
# ---------------------------------------------------------------------------


async def _smartcar_refresh(
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    *,
    timeout: float,
) -> tuple[str, str]:
    """`POST {auth}/oauth/token` (`grant_type=refresh_token`, Basic auth) →
    `(access_token, refresh_token_vigente)` — el segundo elemento es el que
    hay que guardar de ahora en adelante (Smartcar rota el `refresh_token` en
    cada refresh, ver docstring del módulo; si la respuesta no trae uno
    nuevo, es el mismo que se mandó). Lanza `HTTPException(400)` con el
    detalle exacto si Smartcar rechaza las credenciales o si la red falla.
    """
    try:
        response = await client.post(
            _SMARTCAR_AUTH_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(client_id, client_secret),
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No pudimos conectar con Smartcar: {exc}",
        ) from exc
    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Smartcar rechazó las credenciales (status {response.status_code}): "
            f"{response.text[:300]}",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Smartcar devolvió una respuesta no-JSON al refrescar el token.",
        ) from exc
    access_token = payload.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Smartcar no devolvió 'access_token' al refrescar.",
        )
    return access_token, (payload.get("refresh_token") or refresh_token)


async def _probe_smartcar(
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    *,
    timeout: float,
) -> bool | None:
    """Sonda liviana para `GET /v1/vehiculos/status`: `True` si el refresh
    responde 200, `False` si Smartcar responde con cualquier otro status
    (p. ej. credenciales revocadas), `None` si la red falla del todo — NUNCA
    lanza, así este endpoint jamás responde 500 por Smartcar caído (mismo
    contrato que `routers/smarthome.py::_probe_reachable`)."""
    try:
        response = await client.post(
            _SMARTCAR_AUTH_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(client_id, client_secret),
            timeout=timeout,
        )
    except httpx.HTTPError:
        return None
    return response.status_code == 200


async def _smartcar_get(
    client: httpx.AsyncClient, access_token: str, path: str, *, timeout: float
) -> dict[str, Any] | None:
    """`GET {SMARTCAR_API_BASE}{path}` — `dict` en 200; `None` si Smartcar
    respondió con un status de "capability no disponible"
    (`_CAPABILITY_NOT_AVAILABLE`); `HTTPException(400)` en 401 (token
    inválido); `HTTPException(502)` en cualquier otro status o error de red
    inesperado."""
    try:
        response = await client.get(
            f"{_SMARTCAR_API_BASE}{path}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No pudimos conectar con Smartcar ({path}): {exc}",
        ) from exc
    if response.status_code == 200:
        try:
            data = response.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
    if response.status_code in _CAPABILITY_NOT_AVAILABLE:
        return None
    if response.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Smartcar rechazó el access token (401) — reconecta tu cuenta en "
            "Configuración → Vehículos.",
        )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Smartcar respondió {response.status_code} en {path}: {response.text[:300]}",
    )


def _campo_porcentaje(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data or data.get("percentRemaining") is None:
        return None
    campo: dict[str, Any] = {"porcentaje": round(float(data["percentRemaining"]) * 100, 1)}
    if data.get("range") is not None:
        campo["autonomia_km"] = round(float(data["range"]), 1)
    return campo


def _campo_ubicacion(data: dict[str, Any] | None) -> dict[str, float] | None:
    if not data or data.get("latitude") is None or data.get("longitude") is None:
        return None
    return {"lat": float(data["latitude"]), "lon": float(data["longitude"])}


def _config_a_bundle(client_id: str, client_secret: str, refresh_token: str) -> TokenBundle:
    return TokenBundle(
        access_token=json.dumps(
            {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token}
        ),
        token_type="config",
        scopes=["smartcar"],
    )


# ---------------------------------------------------------------------------
# Helpers de `connector_accounts` (singleton por tenant, ver docstring del
# módulo) — mismo patrón que `routers/smarthome.py`/`routers/credentials.py`,
# duplicado a propósito (paquetes hermanos no se importan entre routers).
# ---------------------------------------------------------------------------


async def _find_account(
    repo: Repo, tenant_id: uuid.UUID, connector_key: str
) -> dict[str, Any] | None:
    accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    matches = [a for a in accounts if a["connector_key"] == connector_key]
    if not matches:
        return None
    return min(matches, key=lambda a: a["created_at"])


async def _find_or_create_account(repo: Repo, tenant_id: uuid.UUID) -> dict[str, Any]:
    existing = await _find_account(repo, tenant_id, VEHICLES_CONNECTOR_KEY)
    if existing is not None:
        return existing
    return await repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=VEHICLES_CONNECTOR_KEY,
        external_account_id=VEHICLES_CONNECTOR_KEY,
        display_name=_DISPLAY_NAME,
        scopes=[],
    )


async def _config_del_tenant(
    repo: Repo, vault: TokenVault, tenant_id: uuid.UUID
) -> tuple[dict[str, Any] | None, uuid.UUID | None]:
    """`(config, connector_account_id)` ya descifrada+parseada, o `(None,
    None)` si el tenant no conectó nada o lo guardado está corrupto/ilegible
    (nunca lanza — se registra con `logger.warning`)."""
    account = await _find_account(repo, tenant_id, VEHICLES_CONNECTOR_KEY)
    if account is None:
        return None, None
    bundle = await vault.get(tenant_id, account["id"])
    if bundle is None:
        return None, None
    try:
        data = json.loads(bundle.access_token)
    except (TypeError, ValueError):
        logger.warning("Config de Smartcar ilegible en el vault (tenant_id=%s).", tenant_id)
        return None, None
    if not isinstance(data, dict) or not all(
        data.get(campo) for campo in ("client_id", "client_secret", "refresh_token")
    ):
        return None, None
    return data, account["id"]


async def _refrescar_y_persistir(
    client: httpx.AsyncClient,
    vault: TokenVault,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    config: dict[str, Any],
    *,
    timeout: float,
) -> str:
    """Refresca el access token de Smartcar y, si el `refresh_token` rotó,
    persiste la config actualizada en el vault ANTES de devolver el access
    token (ver docstring del módulo, "Rotación del refresh_token"). Devuelve
    el `access_token` vigente."""
    client_id, client_secret, refresh_token = (
        config["client_id"],
        config["client_secret"],
        config["refresh_token"],
    )
    access_token, nuevo_refresh_token = await _smartcar_refresh(
        client, client_id, client_secret, refresh_token, timeout=timeout
    )
    if nuevo_refresh_token != refresh_token:
        await vault.put(
            tenant_id, account_id, _config_a_bundle(client_id, client_secret, nuevo_refresh_token)
        )
    return access_token


def _config_incompleta_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="No conectaste Smartcar todavía — PUT /v1/vehiculos/credentials primero.",
    )


# ---------------------------------------------------------------------------
# PUT/DELETE /v1/vehiculos/credentials, GET /v1/vehiculos/status
# ---------------------------------------------------------------------------


@router.put("/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def put_credentials(
    payload: VehiculosCredentialsIn,
    current_user: CurrentUser = Depends(require_vehicles_flag),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    client_id = payload.client_id.strip()
    client_secret = payload.client_secret.strip()
    refresh_token = payload.refresh_token.strip()
    if not client_id or not client_secret or not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="client_id, client_secret y refresh_token son obligatorios.",
        )

    refresh_token_a_guardar = refresh_token
    if payload.validate_:
        async with httpx.AsyncClient() as client:
            access_token, refresh_token_a_guardar = await _smartcar_refresh(
                client,
                client_id,
                client_secret,
                refresh_token,
                timeout=_VALIDATE_TIMEOUT_SECONDS,
            )
            # Confirma que el access token de verdad sirve contra la API real
            # (no solo que el endpoint de auth lo aceptó) — mismo espíritu que
            # el `GET {base_url}/api/` de `routers/smarthome.py`.
            await _smartcar_get(
                client, access_token, "/vehicles", timeout=_VALIDATE_TIMEOUT_SECONDS
            )

    account = await _find_or_create_account(repo, current_user.tenant_id)
    await vault.put(
        current_user.tenant_id,
        account["id"],
        _config_a_bundle(client_id, client_secret, refresh_token_a_guardar),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="vehiculos.connected",
        target=VEHICLES_CONNECTOR_KEY,
    )


@router.delete("/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credentials(
    current_user: CurrentUser = Depends(require_vehicles_flag),
    repo: Repo = Depends(get_repo),
) -> None:
    account = await _find_account(repo, current_user.tenant_id, VEHICLES_CONNECTOR_KEY)
    if account is None:
        return  # idempotente: nada que borrar ya es un estado válido de "desconectado".
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="vehiculos.disconnected",
        target=VEHICLES_CONNECTOR_KEY,
    )


@router.get("/status", response_model=VehiculosStatusOut)
async def get_status(
    current_user: CurrentUser = Depends(require_vehicles_flag),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> VehiculosStatusOut:
    config, _account_id = await _config_del_tenant(repo, vault, current_user.tenant_id)
    if config is None:
        return VehiculosStatusOut(configured=False, reachable=None)

    async with httpx.AsyncClient() as client:
        reachable = await _probe_smartcar(
            client,
            config["client_id"],
            config["client_secret"],
            config["refresh_token"],
            timeout=_STATUS_PING_TIMEOUT_SECONDS,
        )
    return VehiculosStatusOut(configured=True, reachable=reachable)


# ---------------------------------------------------------------------------
# GET /v1/vehiculos — lista
# ---------------------------------------------------------------------------


@router.get("", response_model=list[VehiculoOut])
async def listar_vehiculos(
    current_user: CurrentUser = Depends(require_vehicles_flag),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> list[VehiculoOut]:
    config, account_id = await _config_del_tenant(repo, vault, current_user.tenant_id)
    if config is None or account_id is None:
        raise _config_incompleta_error()

    timeout = _VALIDATE_TIMEOUT_SECONDS
    async with httpx.AsyncClient() as client:
        access_token = await _refrescar_y_persistir(
            client, vault, current_user.tenant_id, account_id, config, timeout=timeout
        )
        listado = await _smartcar_get(client, access_token, "/vehicles", timeout=timeout)
        ids = (listado or {}).get("vehicles") or []

        vehiculos: list[VehiculoOut] = []
        for vehicle_id in ids:
            info = await _smartcar_get(
                client, access_token, f"/vehicles/{vehicle_id}", timeout=timeout
            )
            vehiculos.append(
                VehiculoOut(
                    id=vehicle_id,
                    marca=(info or {}).get("make"),
                    modelo=(info or {}).get("model"),
                    anio=(info or {}).get("year"),
                )
            )
    return vehiculos


# ---------------------------------------------------------------------------
# GET /v1/vehiculos/{vehicle_id}/estado
# ---------------------------------------------------------------------------


@router.get("/{vehicle_id}/estado", response_model=EstadoOut)
async def obtener_estado(
    vehicle_id: str,
    current_user: CurrentUser = Depends(require_vehicles_flag),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> EstadoOut:
    config, account_id = await _config_del_tenant(repo, vault, current_user.tenant_id)
    if config is None or account_id is None:
        raise _config_incompleta_error()

    timeout = _VALIDATE_TIMEOUT_SECONDS
    async with httpx.AsyncClient() as client:
        access_token = await _refrescar_y_persistir(
            client, vault, current_user.tenant_id, account_id, config, timeout=timeout
        )
        bateria_data = await _smartcar_get(
            client, access_token, f"/vehicles/{vehicle_id}/battery", timeout=timeout
        )
        combustible_data = await _smartcar_get(
            client, access_token, f"/vehicles/{vehicle_id}/fuel", timeout=timeout
        )
        odometro_data = await _smartcar_get(
            client, access_token, f"/vehicles/{vehicle_id}/odometer", timeout=timeout
        )
        ubicacion_data = await _smartcar_get(
            client, access_token, f"/vehicles/{vehicle_id}/location", timeout=timeout
        )

    return EstadoOut(
        vehicle_id=vehicle_id,
        bateria=_campo_porcentaje(bateria_data),
        combustible=_campo_porcentaje(combustible_data),
        odometro=(odometro_data or {}).get("distance"),
        ubicacion=_campo_ubicacion(ubicacion_data),
    )


# ---------------------------------------------------------------------------
# POST /v1/vehiculos/{vehicle_id}/puertas — acción física, audit_log SIEMPRE
# ---------------------------------------------------------------------------


@router.post("/{vehicle_id}/puertas", response_model=PuertasOut)
async def controlar_puertas(
    vehicle_id: str,
    payload: PuertasIn,
    current_user: CurrentUser = Depends(require_vehicles_flag),
    repo: Repo = Depends(get_repo),
    db_session: AsyncSession = Depends(get_tenant_session),
    vault: TokenVault = Depends(get_vault),
) -> PuertasOut:
    accion = payload.accion.strip().lower()
    smartcar_action = _ACCION_A_SMARTCAR.get(accion)
    if smartcar_action is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="accion debe ser 'bloquear' o 'desbloquear'.",
        )

    config, account_id = await _config_del_tenant(repo, vault, current_user.tenant_id)
    if config is None or account_id is None:
        raise _config_incompleta_error()

    try:
        async with httpx.AsyncClient() as client:
            access_token = await _refrescar_y_persistir(
                client,
                vault,
                current_user.tenant_id,
                account_id,
                config,
                timeout=_VALIDATE_TIMEOUT_SECONDS,
            )
            try:
                response = await client.post(
                    f"{_SMARTCAR_API_BASE}/vehicles/{vehicle_id}/security",
                    json={"action": smartcar_action},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=_VALIDATE_TIMEOUT_SECONDS,
                )
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"No pudimos conectar con Smartcar: {exc}",
                ) from exc
            if response.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Smartcar respondió {response.status_code} al intentar {accion} el "
                    f"vehículo: {response.text[:300]}",
                )
    except HTTPException as exc:
        # HOTFIXES_PENDIENTES.md punto 8 (ver docstring del módulo): auditoría
        # SIEMPRE, incluso cuando el intento contra Smartcar falla — comitea
        # la evidencia ANTES de relanzar, porque `get_tenant_session` envuelve
        # TODA la request en una única transacción con rollback automático
        # ante cualquier excepción propagada (este `HTTPException` lo es).
        # `repo`/`db_session` son la MISMA sesión física (`get_repo` depende
        # de `get_tenant_session`, cacheado por FastAPI dentro de la misma
        # request) — este commit también persiste, si ocurrió, la rotación
        # del `refresh_token` que `_refrescar_y_persistir` haya escrito justo
        # antes del fallo.
        await repo.add_audit_log(
            tenant_id=current_user.tenant_id,
            actor_user_id=current_user.user_id,
            action="vehiculos.puertas.error",
            target=vehicle_id,
            meta={"accion": accion, "error": str(exc.detail)},
        )
        await db_session.commit()
        raise

    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action=f"vehiculos.puertas.{accion}",
        target=vehicle_id,
        meta={"accion": accion},
    )
    return PuertasOut(vehicle_id=vehicle_id, accion=accion, status="ok")
