"""`/v1/viajes/*` — vuelos y hoteles reales mediante Edecán Viajes.

Las búsquedas normales no requieren credenciales: el proveedor nativo consulta
Kiwi, Trivago y Skiplagged a través del cliente MCP de Edecán, independientemente
del modelo de IA conectado. AfterShip continúa disponible como integración
bring-your-own para rastreo. Las rutas Amadeus se conservan para instalaciones
anteriores con acceso Enterprise y tienen fallback automático al proveedor nativo.

El guardrail de dinero sigue siendo el centro del diseño (`ARCHITECTURE.md` §14;
ver `docs/viajes.md`): ninguna ruta reserva ni paga.

Este router NO se monta a sí mismo: `edecan_api.main` (WP-V5-01) lo monta de forma
defensiva, igual que el resto de routers v2/v3/v4 (`importlib.import_module` +
`try/except ImportError` + `logger.warning` si falta) — este módulo solo declara
`router`. `edecan-travel` todavía no es necesariamente una dependencia declarada de
`apps/api` en el momento en que este WP aterriza (mismo hueco temporal que documentó en
su momento `edecan_ads`/`test_ads_router.py`, hasta que WP-V5-01 la agregue a
`apps/api/pyproject.toml`) — eso es justo lo que el montaje defensivo de `main.py`
tolera: si `edecan_travel` no está instalada todavía, este router simplemente se omite
con un `logger.warning`, sin tumbar el resto de la API.

## Compatibilidad heredada

Una instalación que ya tenga credenciales Amadeus puede conservarlas. Edecán las
prueba y cifra como antes; si dejan de responder, la búsqueda cae al proveedor nativo
sin mostrar datos ficticios. AfterShip usa el mismo patrón
"pegar y validar" que `routers/ads.py`/`routers/smarthome.py`
(`DIRECCION_ACTUAL.md`, "Principio de UX no negociable"): `PUT /v1/viajes/credentials`
y `PUT /v1/viajes/rastreo/credentials` aceptan `validate: bool = True` (default) — si es
`true`, antes de guardar nada se valida de verdad contra la API real (Amadeus: pide un
token OAuth2; AfterShip: `GET /couriers`, la sonda más barata posible) y devuelven `400`
con el detalle EXACTO que dio el proveedor si algo falla; nunca se persiste una
credencial sin probarla. `validate: false` queda como escape para tests y migraciones.

## GUARDRAIL DE DINERO — innegociable

Ninguna ruta de este router llama jamás a ninguna API de booking/pago de Amadeus. Las
únicas acciones posibles aquí son: conectar/desconectar credenciales, consultar
estado, y **buscar** (proxies finos de solo lectura hacia `edecan_travel.providers`).
Crear un borrador de reserva (`preparar_reserva`, `dangerous=True`) vive exclusivamente
en `edecan_travel.tools` y solo inserta un `orders(status='draft')` — nunca en este
router, y nunca contra Amadeus. Reservar de verdad es, siempre, una decisión y una
acción humana fuera de Edecán por completo (ver `docs/viajes.md`).

## Contrato del vault

Dos `connector_key` distintos (`edecan_travel.providers.TRAVEL_CONNECTOR_KEY` /
`TRACKING_CONNECTOR_KEY`), cada uno **singleton por tenant** (una sola
`connector_account` por `(tenant_id, connector_key)`, igual que `"ads"`/`"homeassistant"`/
`"llm"`) — `_find_or_create_account` calca el mismo helper de `routers/ads.py`/
`routers/smarthome.py`. `TokenBundle.access_token` guarda el JSON
`{"api_key", "api_secret", "environment"}` para `"travel"` y `{"api_key"}` para
`"tracking"` (`token_type="config"`, mismo criterio que el resto de credenciales
bring-your-own de config — NO son tokens OAuth propios de Edecán, son configs que hay
que `json.loads()`).

## Por qué este router NO reutiliza los internos de `AmadeusClient`/`AfterShipClient`
   para validar

El ping de validación de `PUT /credentials`/`PUT /rastreo/credentials` es, a propósito,
`httpx.AsyncClient` puro y local a este módulo — mismo criterio exacto que
`routers/ads.py::_ping_meta`/`routers/smarthome.py::_ping_home_assistant` (ninguno de
los dos reutiliza el método privado de obtención de token/sesión de su proveedor real).
Sí importa las constantes de URL base (`AMADEUS_TEST_BASE_URL`/
`AMADEUS_PRODUCTION_BASE_URL`/`AFTERSHIP_BASE_URL`) para no duplicar ese string mágico.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict
from typing import Any

import httpx
from edecan_core import ToolContext
from edecan_db.vault import TokenVault
from edecan_schemas import TokenBundle
from edecan_travel.amadeus import AMADEUS_PRODUCTION_BASE_URL, AMADEUS_TEST_BASE_URL
from edecan_travel.providers import (
    TRACKING_CONNECTOR_KEY,
    TRAVEL_CONNECTOR_KEY,
    get_tenant_tracking_provider,
    get_tenant_travel_provider,
)
from edecan_travel.tracking import AFTERSHIP_BASE_URL
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

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

router = APIRouter(prefix="/v1/viajes", tags=["viajes"], dependencies=[Depends(rate_limit)])

# Flag de plan pinned `ARCHITECTURE.md` §14 (WP-V5-09) — import con guardia, mismo
# patrón que `edecan_api.deps` usa para `LLMProviderConfig` (WP-V3-03): si el WP dueño
# de pinnear los flags nuevos de v5 en `edecan_schemas.plans` (linchpin de v5, mismo
# rol que tuvo WP-V4-01 en v4) todavía no aterrizó esa línea, este router sigue
# funcionando con el mismo string literal como fallback — nunca revienta el import de
# todo el módulo por esto. Si más adelante `edecan_schemas.plans.FLAG_TOOLS_TRAVEL`
# existe, se usa esa constante real (mismo valor string de todos modos).
try:
    from edecan_schemas.plans import FLAG_TOOLS_TRAVEL
except ImportError:  # pragma: no cover - linchpin de v5 todavía no aterrizó el flag
    FLAG_TOOLS_TRAVEL = "tools.travel"

_DISPLAY_NAME_TRAVEL = "Amadeus (vuelos y hoteles)"
_DISPLAY_NAME_TRACKING = "AfterShip (rastreo de paquetes)"

_ENVIRONMENTS_VALIDOS = frozenset({"test", "production"})
_VALIDATE_TIMEOUT_SECONDS = 15.0

_TOKEN_PATH = "/v1/security/oauth2/token"
_COURIERS_PATH = "/couriers"
_API_KEY_HEADER = "as-api-key"


# ---------------------------------------------------------------------------
# Gate de flag de plan — sustituye a `get_current_user` en cada ruta (mismo patrón que
# `edecan_api.routers.ads._require_tools_ads`).
# ---------------------------------------------------------------------------


async def _require_tools_travel(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current_user.tenant.flags.get(FLAG_TOOLS_TRAVEL, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Viajes no está disponible en tu plan.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Bodies / respuestas
# ---------------------------------------------------------------------------


class ViajesCredentialsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    api_key: str
    api_secret: str
    environment: str = "test"
    validate_: bool = Field(default=True, alias="validate")


class RastreoCredentialsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    api_key: str
    validate_: bool = Field(default=True, alias="validate")


class ViajesTravelStatusOut(BaseModel):
    configured: bool
    environment: str | None


class ViajesTrackingStatusOut(BaseModel):
    configured: bool


class ViajesStatusOut(BaseModel):
    travel: ViajesTravelStatusOut
    tracking: ViajesTrackingStatusOut


# ---------------------------------------------------------------------------
# Ping de validación (PUT, "pegar y validar") — ver el docstring del módulo para el
# porqué de no reutilizar `AmadeusClient`/`AfterShipClient` acá.
# ---------------------------------------------------------------------------


def _detalle_error_amadeus(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"Amadeus respondió {response.status_code}."
    errores = data.get("errors") if isinstance(data, dict) else None
    if isinstance(errores, list) and errores and isinstance(errores[0], dict):
        detalle = errores[0].get("detail") or errores[0].get("title")
        if detalle:
            return str(detalle)
    return f"Amadeus respondió {response.status_code}."


async def _ping_amadeus(api_key: str, api_secret: str, environment: str, *, timeout: float) -> None:
    base_url = AMADEUS_PRODUCTION_BASE_URL if environment == "production" else AMADEUS_TEST_BASE_URL
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            response = await client.post(
                _TOKEN_PATH,
                data={
                    "grant_type": "client_credentials",
                    "client_id": api_key,
                    "client_secret": api_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No pudimos conectar con Amadeus: {exc}",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Amadeus rechazó las credenciales: {_detalle_error_amadeus(response)}",
        )
    if not response.json().get("access_token"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amadeus no devolvió un access_token válido.",
        )


def _detalle_error_aftership(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"AfterShip respondió {response.status_code}."
    meta = data.get("meta") if isinstance(data, dict) else None
    if isinstance(meta, dict) and meta.get("message"):
        return str(meta["message"])
    return f"AfterShip respondió {response.status_code}."


async def _ping_aftership(api_key: str, *, timeout: float) -> None:
    try:
        async with httpx.AsyncClient(base_url=AFTERSHIP_BASE_URL, timeout=timeout) as client:
            response = await client.get(_COURIERS_PATH, headers={_API_KEY_HEADER: api_key})
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No pudimos conectar con AfterShip: {exc}",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"AfterShip rechazó el api_key: {_detalle_error_aftership(response)}",
        )


# ---------------------------------------------------------------------------
# Helpers de `connector_accounts` (singleton por tenant) — mismo patrón que
# `routers/ads.py`/`routers/smarthome.py`, duplicado a propósito (paquetes hermanos no
# se importan entre routers para esta parte).
# ---------------------------------------------------------------------------


async def _find_account(
    repo: Repo, tenant_id: uuid.UUID, connector_key: str
) -> dict[str, Any] | None:
    accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    matches = [a for a in accounts if a["connector_key"] == connector_key]
    if not matches:
        return None
    return min(matches, key=lambda a: a["created_at"])


async def _find_or_create_account(
    repo: Repo, tenant_id: uuid.UUID, connector_key: str, display_name: str
) -> dict[str, Any]:
    existing = await _find_account(repo, tenant_id, connector_key)
    if existing is not None:
        return existing
    return await repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=connector_key,
        external_account_id=connector_key,
        display_name=display_name,
        scopes=[],
    )


def _build_viajes_ctx(
    current_user: CurrentUser, session: Any, vault: Any, settings: Any
) -> ToolContext:
    """`ToolContext` liviano para invocar `get_tenant_travel_provider(ctx)`/
    `get_tenant_tracking_provider(ctx)` desde un endpoint HTTP (fuera de un turno de
    chat) — mismo shape que `edecan_api.routers.ads._build_ads_ctx`, con `llm=None` y
    `extras={}` porque estas rutas no necesitan ninguna otra pieza."""
    return ToolContext(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        session=session,
        settings=settings,
        llm=None,
        vault=vault,
        extras={},
    )


# ---------------------------------------------------------------------------
# PUT/DELETE /v1/viajes/credentials (Amadeus)
# ---------------------------------------------------------------------------


@router.put("/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def put_credentials(
    payload: ViajesCredentialsIn,
    current_user: CurrentUser = Depends(_require_tools_travel),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    api_key = payload.api_key.strip()
    api_secret = payload.api_secret.strip()
    environment = (payload.environment or "test").strip().lower()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="El api_key no puede estar vacío."
        )
    if not api_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="El api_secret no puede estar vacío."
        )
    if environment not in _ENVIRONMENTS_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El 'environment' debe ser 'test' o 'production'.",
        )

    if payload.validate_:
        await _ping_amadeus(api_key, api_secret, environment, timeout=_VALIDATE_TIMEOUT_SECONDS)

    account = await _find_or_create_account(
        repo, current_user.tenant_id, TRAVEL_CONNECTOR_KEY, _DISPLAY_NAME_TRAVEL
    )
    config = {"api_key": api_key, "api_secret": api_secret, "environment": environment}
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=json.dumps(config), token_type="config", scopes=["amadeus"]),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="viajes.connected",
        target=TRAVEL_CONNECTOR_KEY,
    )


@router.delete("/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credentials(
    current_user: CurrentUser = Depends(_require_tools_travel),
    repo: Repo = Depends(get_repo),
) -> None:
    account = await _find_account(repo, current_user.tenant_id, TRAVEL_CONNECTOR_KEY)
    if account is None:
        return  # idempotente: nada que borrar ya es un estado válido de "desconectado".
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="viajes.disconnected",
        target=TRAVEL_CONNECTOR_KEY,
    )


# ---------------------------------------------------------------------------
# PUT/DELETE /v1/viajes/rastreo/credentials (AfterShip)
# ---------------------------------------------------------------------------


@router.put("/rastreo/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def put_rastreo_credentials(
    payload: RastreoCredentialsIn,
    current_user: CurrentUser = Depends(_require_tools_travel),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="El api_key no puede estar vacío."
        )

    if payload.validate_:
        await _ping_aftership(api_key, timeout=_VALIDATE_TIMEOUT_SECONDS)

    account = await _find_or_create_account(
        repo, current_user.tenant_id, TRACKING_CONNECTOR_KEY, _DISPLAY_NAME_TRACKING
    )
    config = {"api_key": api_key}
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=json.dumps(config), token_type="config", scopes=["aftership"]),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="viajes.rastreo.connected",
        target=TRACKING_CONNECTOR_KEY,
    )


@router.delete("/rastreo/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rastreo_credentials(
    current_user: CurrentUser = Depends(_require_tools_travel),
    repo: Repo = Depends(get_repo),
) -> None:
    account = await _find_account(repo, current_user.tenant_id, TRACKING_CONNECTOR_KEY)
    if account is None:
        return
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="viajes.rastreo.disconnected",
        target=TRACKING_CONNECTOR_KEY,
    )


# ---------------------------------------------------------------------------
# GET /v1/viajes/status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=ViajesStatusOut)
async def get_status(
    current_user: CurrentUser = Depends(_require_tools_travel),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> ViajesStatusOut:
    travel_out = ViajesTravelStatusOut(configured=False, environment=None)
    travel_account = await _find_account(repo, current_user.tenant_id, TRAVEL_CONNECTOR_KEY)
    if travel_account is not None:
        bundle = await vault.get(current_user.tenant_id, travel_account["id"])
        if bundle is not None and bundle.access_token:
            try:
                config = json.loads(bundle.access_token)
                travel_out = ViajesTravelStatusOut(
                    configured=True, environment=config.get("environment") or "test"
                )
            except (json.JSONDecodeError, TypeError):
                pass

    tracking_out = ViajesTrackingStatusOut(configured=False)
    tracking_account = await _find_account(repo, current_user.tenant_id, TRACKING_CONNECTOR_KEY)
    if tracking_account is not None:
        bundle = await vault.get(current_user.tenant_id, tracking_account["id"])
        if bundle is not None and bundle.access_token:
            tracking_out = ViajesTrackingStatusOut(configured=True)

    return ViajesStatusOut(travel=travel_out, tracking=tracking_out)


# ---------------------------------------------------------------------------
# GET /v1/viajes/buscar/vuelos, /buscar/hoteles, /rastreo/{numero} — proxies finos de
# solo lectura para la UI (mismos providers bring-your-own que usan las tools del
# agente; nunca reserva ni paga nada, ver el docstring del módulo).
# ---------------------------------------------------------------------------


@router.get("/buscar/vuelos")
async def buscar_vuelos(
    origen: str = Query(...),
    destino: str = Query(...),
    fecha: str = Query(...),
    adultos: int = Query(default=1, ge=1),
    max_resultados: int = Query(default=10, ge=1, le=50),
    current_user: CurrentUser = Depends(_require_tools_travel),
    session: Any = Depends(get_tenant_session),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    ctx = _build_viajes_ctx(current_user, session, vault, settings)
    provider = await get_tenant_travel_provider(ctx)
    try:
        ofertas = await provider.buscar_vuelos(
            origen, destino, fecha, adultos=adultos, max_resultados=max_resultados
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo buscar vuelos: {exc}",
        ) from exc
    return {"ofertas": [asdict(o) for o in ofertas]}


@router.get("/buscar/hoteles")
async def buscar_hoteles(
    ciudad: str = Query(...),
    checkin: str = Query(...),
    checkout: str = Query(...),
    adultos: int = Query(default=1, ge=1),
    current_user: CurrentUser = Depends(_require_tools_travel),
    session: Any = Depends(get_tenant_session),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    ctx = _build_viajes_ctx(current_user, session, vault, settings)
    provider = await get_tenant_travel_provider(ctx)
    try:
        ofertas = await provider.buscar_hoteles(ciudad, checkin, checkout, adultos=adultos)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo buscar hoteles: {exc}",
        ) from exc
    return {"ofertas": [asdict(o) for o in ofertas]}


@router.get("/rastreo/{numero}")
async def rastreo(
    numero: str,
    courier_slug: str | None = Query(default=None),
    current_user: CurrentUser = Depends(_require_tools_travel),
    session: Any = Depends(get_tenant_session),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    ctx = _build_viajes_ctx(current_user, session, vault, settings)
    provider = await get_tenant_tracking_provider(ctx)
    try:
        rastreo_resultado = await provider.rastrear(numero, courier_slug)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo rastrear el paquete: {exc}",
        ) from exc
    return asdict(rastreo_resultado)
