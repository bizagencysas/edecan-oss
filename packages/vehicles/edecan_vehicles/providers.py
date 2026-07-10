"""Proveedores de vehículos (`ARCHITECTURE.md` §13, `DIRECCION_ACTUAL.md`,
`ROADMAP_V2.md` §6.3 — WP-V4-08).

`VehicleProvider` es el protocolo intercambiable (mismo patrón que
`edecan_creative.providers.ImageProvider`/`edecan_toolkit.research.SearchProvider`):
`StubVehiclesProvider` es el proveedor por defecto, 100% offline y
determinista (un vehículo de ejemplo) — pensado para desarrollo, self-host
sin cuenta de Smartcar, y tests. `SmartcarProvider` habla con la API oficial
de **Smartcar** (https://smartcar.com), un agregador OAuth multi-marca: UNA
integración cubre Tesla, GM, Ford, Toyota, BMW, Hyundai/Kia y decenas de
marcas más, en vez de que Edecán tenga que integrar el API propietario de
cada fabricante por separado (`ROADMAP_V2.md` §6.3 ya señalaba "Tesla Fleet
API / Smartcar" como la opción bring-your-own razonable; Smartcar gana por
ser multi-marca).

## Bring-your-own, sin excepción

Igual que el resto de conectores (`DIRECCION_ACTUAL.md` "Modelo de
credenciales: TODO lo trae el cliente, siempre"): cada TENANT crea su propia
app en el dashboard de Smartcar (gratis, incluye modo de prueba con
vehículos simulados) y conecta su propio `client_id`/`client_secret` +
autoriza su propio vehículo con el flujo Connect de Smartcar para obtener un
`refresh_token` inicial — ver `docs/vehiculos.md`. Edecán nunca opera una
app de Smartcar compartida ni una credencial de plataforma.

## `get_tenant_vehicle_provider(ctx)` — "tenant → stub", sin paso de plataforma

Mismo patrón que `edecan_creative.providers.get_tenant_image_provider`
(`packages/creative/edecan_creative/providers.py`): lee `ctx.tenant_id`/
`ctx.session`/`ctx.vault` de forma defensiva (`ctx` es
`edecan_core.tools.ToolContext` en producción, pero un `Any` a propósito) y,
si el tenant conectó su propia credencial de Smartcar (`PUT
/v1/vehiculos/credentials`, `apps/api/edecan_api/routers/vehiculos.py`), la
usa; en CUALQUIER otro caso (falta `ctx.session`/`ctx.vault`, el tenant nunca
conectó nada, o cualquier paso de esa resolución falla — vault caído, JSON
corrupto, faltan campos) cae DIRECTO a `StubVehiclesProvider()`, nunca a una
credencial de plataforma: a diferencia de imágenes/búsqueda web (que sí
tuvieron alguna vez una config de plataforma legada que preservar), vehículos
es enteramente nuevo en v4 y nunca existió un `VEHICLES_PROVIDER` de
plataforma que respetar — "tenant → stub" es, aquí, el único camino posible,
no una corrección sobre algo previo.

## Refresh token: Smartcar lo ROTA en cada refresh

Smartcar emite un `refresh_token` de un solo uso: cada vez que
`SmartcarProvider` lo canjea por un `access_token` nuevo
(`POST https://auth.smartcar.com/oauth/token`), la respuesta puede traer un
`refresh_token` DISTINTO al que se mandó — y, a partir de ahí, el anterior
deja de servir. Sin persistir ese cambio, la SIGUIENTE vez que alguien
intente usar la cuenta del tenant, el refresh fallaría con la credencial
"vieja" todavía guardada, rompiendo la conexión en silencio. Por eso
`SmartcarProvider` recibe un `on_refresh_token` callable opcional (no un
`ctx`/vault directo: mantiene a esta clase testeable sin nada de
`edecan_db`/`edecan_schemas`, mismo criterio que `edecan_smarthome.client
.HomeAssistantClient` es un cliente HTTP puro) que `get_tenant_vehicle_provider`
conecta a un cierre que llama `ctx.vault.put(...)` con la config actualizada
— así la persistencia del rotado queda encapsulada en el único lugar que
sabe hablar con el vault.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
from edecan_core.safety import redact
from sqlalchemy import text as sql_text

logger = logging.getLogger(__name__)

# `connector_key` del `TokenVault` (ARCHITECTURE.md §10.4) para la credencial
# de Smartcar bring-your-own del tenant — EXACTA, pinned. Definida acá y,
# duplicada a propósito, en `apps/api/edecan_api/routers/vehiculos.py` (ese
# router NO importa este paquete — ver el docstring de ese módulo para el
# porqué — mismo criterio que `LLM_CONNECTOR_KEY` duplicado entre
# `apps/api/edecan_api/deps.py` y `apps/worker/edecan_worker/deps.py`).
VEHICLES_CONNECTOR_KEY = "vehicles"

SMARTCAR_AUTH_URL = "https://auth.smartcar.com/oauth/token"
SMARTCAR_API_BASE = "https://api.smartcar.com/v2.0"

DEFAULT_TIMEOUT_SECONDS = 15.0

# Los access tokens de Smartcar duran ~2h (7200s, ver docs/vehiculos.md).
# `_TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS` adelanta el refresh un poco antes de
# la expiración real, para no arriesgar que un token expire A MITAD de una
# request (varias llamadas GET encadenadas en `estado()`/`list_vehicles()`).
_DEFAULT_EXPIRES_IN_SECONDS = 7200
_TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS = 60

# Status HTTP que Smartcar puede devolver cuando un vehículo/marca concreto
# NO expone una capability puntual (p. ej. un auto de combustión no tiene
# `/battery`, o el scope autorizado no incluye `/location`) — se tratan como
# "este campo no está disponible", NUNCA como un error que tumbe toda la
# lectura: 403 (permiso/scope insuficiente para esa capability), 404 (el
# vehículo no tiene esa capability), 409 (VEHICLE_STATE — p. ej. el auto está
# dormido), 501 (marca/vehículo no soporta esa capability en absoluto).
_CAPABILITY_NOT_AVAILABLE_STATUSES = frozenset({403, 404, 409, 501})

# "bloquear"/"desbloquear" (español, lo que ve el modelo) → `action` que
# espera `POST /vehicles/{id}/security` de Smartcar (`LOCK`/`UNLOCK`).
ACCIONES_A_SMARTCAR: dict[str, str] = {"bloquear": "LOCK", "desbloquear": "UNLOCK"}


class VehicleProviderError(RuntimeError):
    """Error al hablar con el proveedor de vehículos — mensaje en español,
    siempre accionable (nunca una excepción cruda de `httpx`), mismo
    criterio que `edecan_smarthome.client.HomeAssistantError`. El texto
    siempre pasa por `edecan_core.safety.redact` antes de propagarse."""


@runtime_checkable
class VehicleProvider(Protocol):
    """Protocolo común de proveedor de vehículos (contrato pinned del work
    package: nombres y formas EXACTOS)."""

    async def list_vehicles(self) -> list[dict[str, Any]]:
        """Lista los vehículos del tenant: `[{"id", "marca", "modelo", "anio"}, ...]`."""
        ...

    async def estado(self, vehicle_id: str) -> dict[str, Any]:
        """Estado de un vehículo: `{"bateria", "combustible", "odometro", "ubicacion"}`.

        Los 4 campos son opcionales (`None` si no aplica/no se pudo leer) —
        no toda marca ni todo vehículo expone todo (p. ej. un auto de
        combustión no tiene `bateria`, uno eléctrico puede no tener
        `combustible`). `bateria`/`combustible`, cuando están disponibles,
        son `{"porcentaje": float, "autonomia_km": float | None}`;
        `ubicacion` es `{"lat": float, "lon": float}`; `odometro` es un
        `float` en kilómetros.
        """
        ...

    async def controlar_puertas(self, vehicle_id: str, accion: str) -> dict[str, Any]:
        """Bloquea (`accion="bloquear"`) o desbloquea (`accion="desbloquear"`)
        las puertas de un vehículo. Devuelve un `dict` con al menos
        `{"vehicle_id", "accion", "status"}`."""
        ...


# ---------------------------------------------------------------------------
# StubVehiclesProvider — determinista, 100% offline, un vehículo de ejemplo.
# ---------------------------------------------------------------------------

STUB_VEHICLE_ID = "demo-vehiculo-1"

_STUB_VEHICLE = {"id": STUB_VEHICLE_ID, "marca": "Toyota", "modelo": "Corolla", "anio": 2022}
_STUB_ESTADO = {
    "bateria": None,
    "combustible": {"porcentaje": 72.0, "autonomia_km": 480.0},
    "odometro": 18342.0,
    "ubicacion": {"lat": 19.4326, "lon": -99.1332},
}


class StubVehiclesProvider:
    """Proveedor determinista sin red — proveedor por defecto (si el tenant
    no conectó Smartcar): gratis, 100% offline, pensado para desarrollo,
    self-host sin cuenta de Smartcar y tests. Expone un único vehículo de
    ejemplo (`STUB_VEHICLE_ID`)."""

    async def list_vehicles(self) -> list[dict[str, Any]]:
        return [dict(_STUB_VEHICLE)]

    async def estado(self, vehicle_id: str) -> dict[str, Any]:
        if vehicle_id != STUB_VEHICLE_ID:
            raise VehicleProviderError(
                f"No tengo ningún vehículo con id «{vehicle_id}» en modo demo — el único "
                f"disponible es «{STUB_VEHICLE_ID}». Conecta tu cuenta de Smartcar en "
                "Configuración → Vehículos para tus vehículos reales."
            )
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in _STUB_ESTADO.items()}

    async def controlar_puertas(self, vehicle_id: str, accion: str) -> dict[str, Any]:
        accion_norm = (accion or "").strip().lower()
        if accion_norm not in ACCIONES_A_SMARTCAR:
            raise VehicleProviderError(
                f"Acción desconocida: «{accion}». Usa 'bloquear' o 'desbloquear'."
            )
        if vehicle_id != STUB_VEHICLE_ID:
            raise VehicleProviderError(
                f"No tengo ningún vehículo con id «{vehicle_id}» en modo demo — el único "
                f"disponible es «{STUB_VEHICLE_ID}»."
            )
        return {"vehicle_id": vehicle_id, "accion": accion_norm, "status": "ok", "demo": True}


# ---------------------------------------------------------------------------
# SmartcarProvider — API oficial de Smartcar, bring-your-own del tenant.
# ---------------------------------------------------------------------------


class SmartcarProvider:
    """`VehicleProvider` que habla con la API oficial de Smartcar
    (https://smartcar.com/docs/api), usando la credencial que el TENANT trajo
    (nunca una app de Smartcar de plataforma) — ver docstring del módulo.

    `on_refresh_token`, si se pasa, se llama con el `refresh_token` NUEVO
    cada vez que Smartcar rota el que se venía usando (ver docstring del
    módulo). `http`, si se pasa (tests con `respx`), se reutiliza para TODAS
    las llamadas de una invocación pública en vez de abrir un cliente nuevo.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        on_refresh_token: Callable[[str], Awaitable[None]] | None = None,
        http: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not client_id or not client_secret or not refresh_token:
            raise VehicleProviderError(
                "Faltan credenciales de Smartcar (client_id/client_secret/refresh_token)."
            )
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._on_refresh_token = on_refresh_token
        self._http = http
        self._timeout = timeout
        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0

    @property
    def refresh_token(self) -> str:
        """Refresh token VIGENTE (puede haber rotado desde el que se pasó al
        construir el proveedor) — sobre todo útil en tests."""
        return self._refresh_token

    def _new_client(self) -> tuple[httpx.AsyncClient, bool]:
        """`(cliente, owns_client)` — reutiliza `self._http` si se inyectó
        (tests), si no abre uno nuevo que el llamador debe cerrar."""
        if self._http is not None:
            return self._http, False
        return httpx.AsyncClient(timeout=self._timeout), True

    async def _ensure_access_token(self, client: httpx.AsyncClient) -> str:
        if self._access_token is not None and time.monotonic() < self._access_token_expires_at:
            return self._access_token
        return await self._refresh(client)

    async def _refresh(self, client: httpx.AsyncClient) -> str:
        """`POST {SMARTCAR_AUTH_URL}` (`grant_type=refresh_token`, Basic auth
        client_id:client_secret) → cachea el `access_token` (con margen de
        expiración) y, si Smartcar rotó el `refresh_token`, lo persiste vía
        `on_refresh_token` ANTES de devolver el `access_token` — así una
        excepción posterior en la llamada real que disparó este refresh
        nunca deja el `refresh_token` nuevo sin guardar."""
        try:
            response = await client.post(
                SMARTCAR_AUTH_URL,
                data={"grant_type": "refresh_token", "refresh_token": self._refresh_token},
                auth=(self._client_id, self._client_secret),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise VehicleProviderError(
                f"No pude conectar con Smartcar para refrescar el token: {redact(str(exc))}. "
                "¿Hay red? ¿Smartcar está caído?"
            ) from exc

        if response.status_code == 401 or response.status_code == 400:
            raise VehicleProviderError(
                "Smartcar rechazó las credenciales al refrescar el token — revisa "
                f"client_id/client_secret/refresh_token. Detalle: {redact(response.text[:300])}"
            )
        if response.status_code >= 400:
            raise VehicleProviderError(
                f"Smartcar respondió {response.status_code} al refrescar el token: "
                f"{redact(response.text[:300])}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise VehicleProviderError(
                "Smartcar devolvió una respuesta no-JSON al refrescar el token."
            ) from exc

        access_token = payload.get("access_token")
        if not access_token:
            raise VehicleProviderError("Smartcar no devolvió 'access_token' al refrescar.")

        expires_in = payload.get("expires_in") or _DEFAULT_EXPIRES_IN_SECONDS
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            expires_in = _DEFAULT_EXPIRES_IN_SECONDS
        self._access_token = access_token
        self._access_token_expires_at = time.monotonic() + max(
            0, expires_in - _TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS
        )

        nuevo_refresh_token = payload.get("refresh_token")
        if nuevo_refresh_token and nuevo_refresh_token != self._refresh_token:
            self._refresh_token = nuevo_refresh_token
            if self._on_refresh_token is not None:
                await self._on_refresh_token(nuevo_refresh_token)

        return access_token

    async def _soft_get(
        self, client: httpx.AsyncClient, token: str, path: str
    ) -> dict[str, Any] | None:
        """`GET {SMARTCAR_API_BASE}{path}` con Bearer `token`. Devuelve el
        JSON (`dict`) en 200; `None` si Smartcar respondió con un status de
        "esta capability no está disponible" (`_CAPABILITY_NOT_AVAILABLE_STATUSES`,
        ver su docstring) — NUNCA lanza por eso, es el caso normal para
        muchas combinaciones marca/vehículo. Lanza `VehicleProviderError` en
        401 (token inválido: un problema real, no de capability) o
        cualquier otro status/error de red inesperado."""
        try:
            response = await client.get(
                f"{SMARTCAR_API_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise VehicleProviderError(
                f"No pude conectar con Smartcar ({path}): {redact(str(exc))}."
            ) from exc

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
        if response.status_code == 401:
            raise VehicleProviderError(
                "Smartcar rechazó el access token (401) — vuelve a intentarlo; si persiste, "
                "reconecta tu cuenta en Configuración → Vehículos."
            )
        if response.status_code in _CAPABILITY_NOT_AVAILABLE_STATUSES:
            logger.info(
                "Smartcar: %s no disponible para este vehículo (status %d).",
                path,
                response.status_code,
            )
            return None
        raise VehicleProviderError(
            f"Smartcar respondió {response.status_code} en {path}: {redact(response.text[:300])}"
        )

    async def list_vehicles(self) -> list[dict[str, Any]]:
        client, owns_client = self._new_client()
        try:
            token = await self._ensure_access_token(client)
            listado = await self._soft_get(client, token, "/vehicles")
            ids = (listado or {}).get("vehicles") or []

            vehiculos: list[dict[str, Any]] = []
            for vehicle_id in ids:
                info = await self._soft_get(client, token, f"/vehicles/{vehicle_id}")
                vehiculos.append(
                    {
                        "id": vehicle_id,
                        "marca": (info or {}).get("make"),
                        "modelo": (info or {}).get("model"),
                        "anio": (info or {}).get("year"),
                    }
                )
            return vehiculos
        finally:
            if owns_client:
                await client.aclose()

    async def estado(self, vehicle_id: str) -> dict[str, Any]:
        client, owns_client = self._new_client()
        try:
            token = await self._ensure_access_token(client)
            bateria_data = await self._soft_get(client, token, f"/vehicles/{vehicle_id}/battery")
            combustible_data = await self._soft_get(client, token, f"/vehicles/{vehicle_id}/fuel")
            odometro_data = await self._soft_get(client, token, f"/vehicles/{vehicle_id}/odometer")
            ubicacion_data = await self._soft_get(client, token, f"/vehicles/{vehicle_id}/location")

            return {
                "bateria": _campo_porcentaje(bateria_data),
                "combustible": _campo_porcentaje(combustible_data),
                "odometro": _campo_distancia(odometro_data),
                "ubicacion": _campo_ubicacion(ubicacion_data),
            }
        finally:
            if owns_client:
                await client.aclose()

    async def controlar_puertas(self, vehicle_id: str, accion: str) -> dict[str, Any]:
        accion_norm = (accion or "").strip().lower()
        smartcar_action = ACCIONES_A_SMARTCAR.get(accion_norm)
        if smartcar_action is None:
            raise VehicleProviderError(
                f"Acción desconocida: «{accion}». Usa 'bloquear' o 'desbloquear'."
            )

        client, owns_client = self._new_client()
        try:
            token = await self._ensure_access_token(client)
            try:
                response = await client.post(
                    f"{SMARTCAR_API_BASE}/vehicles/{vehicle_id}/security",
                    json={"action": smartcar_action},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                raise VehicleProviderError(
                    f"No pude conectar con Smartcar para {accion_norm} el vehículo: "
                    f"{redact(str(exc))}."
                ) from exc

            if response.status_code == 401:
                raise VehicleProviderError(
                    "Smartcar rechazó el access token (401) al intentar controlar el vehículo."
                )
            if response.status_code >= 400:
                raise VehicleProviderError(
                    f"Smartcar respondió {response.status_code} al intentar {accion_norm} el "
                    f"vehículo «{vehicle_id}»: {redact(response.text[:300])}"
                )
            return {"vehicle_id": vehicle_id, "accion": accion_norm, "status": "ok"}
        finally:
            if owns_client:
                await client.aclose()


def _campo_porcentaje(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """`{"percentRemaining", "range"}` de Smartcar (battery/fuel) →
    `{"porcentaje", "autonomia_km"}` en español, o `None` si no hay dato."""
    if not data or data.get("percentRemaining") is None:
        return None
    campo: dict[str, Any] = {"porcentaje": round(float(data["percentRemaining"]) * 100, 1)}
    if data.get("range") is not None:
        campo["autonomia_km"] = round(float(data["range"]), 1)
    return campo


def _campo_distancia(data: dict[str, Any] | None) -> float | None:
    if not data or data.get("distance") is None:
        return None
    return float(data["distance"])


def _campo_ubicacion(data: dict[str, Any] | None) -> dict[str, float] | None:
    if not data or data.get("latitude") is None or data.get("longitude") is None:
        return None
    return {"lat": float(data["latitude"]), "lon": float(data["longitude"])}


# ---------------------------------------------------------------------------
# get_tenant_vehicle_provider — "tenant → stub" (ver docstring del módulo).
# ---------------------------------------------------------------------------


async def get_tenant_vehicle_provider(ctx: Any) -> VehicleProvider:
    """`VehicleProvider` bring-your-own del tenant, o `StubVehiclesProvider`
    si no tiene uno — ver docstring del módulo ("tenant → stub").

    Lee `ctx.tenant_id`/`ctx.session`/`ctx.vault` de forma defensiva (`ctx`
    es `edecan_core.tools.ToolContext` en producción, pero un `Any` a
    propósito — mismo criterio que `edecan_creative.providers
    .get_tenant_image_provider`): si falta cualquiera de los tres, el
    tenant nunca conectó `PUT /v1/vehiculos/credentials`, o CUALQUIER paso
    de esta resolución falla (vault caído, JSON corrupto, faltan campos),
    se degrada a `StubVehiclesProvider()` con `logger.warning` — nunca
    revienta `vehiculo_estado`/`vehiculo_controlar` por esto.
    """
    tenant_id = getattr(ctx, "tenant_id", None)
    session = getattr(ctx, "session", None)
    vault = getattr(ctx, "vault", None)
    if tenant_id is None or session is None or vault is None:
        return StubVehiclesProvider()

    try:
        row = (
            (
                await session.execute(
                    sql_text(
                        "SELECT id FROM connector_accounts WHERE tenant_id = :tenant_id "
                        "AND connector_key = :connector_key ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"tenant_id": tenant_id, "connector_key": VEHICLES_CONNECTOR_KEY},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return StubVehiclesProvider()

        account_id = row["id"]
        bundle = await vault.get(tenant_id=tenant_id, connector_account_id=account_id)
        if bundle is None:
            return StubVehiclesProvider()

        data = json.loads(bundle.access_token)
        client_id = data.get("client_id")
        client_secret = data.get("client_secret")
        refresh_token = data.get("refresh_token")
        if not (client_id and client_secret and refresh_token):
            return StubVehiclesProvider()

        return SmartcarProvider(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            on_refresh_token=_persistir_refresh_token(
                vault, tenant_id, account_id, client_id, client_secret
            ),
        )
    except Exception:
        logger.warning(
            "No se pudo resolver el VehicleProvider bring-your-own del tenant_id=%s; uso "
            "StubVehiclesProvider (conecta Smartcar en Configuración → Vehículos).",
            tenant_id,
            exc_info=True,
        )
        return StubVehiclesProvider()


@dataclass
class _VaultBundle:
    """Forma mínima "duck-typed" que `edecan_db.vault.TokenVault.put` acepta:
    esa función lee `access_token`/`refresh_token`/`expires_at`/`scopes`/
    `token_type` de su argumento "a mano" en vez de con
    `bundle.model_dump_json()`, EXPRESAMENTE para que "cualquier objeto con
    la misma forma... sirva como argumento de `TokenVault.put`" sin necesitar
    `edecan_schemas` (ver el docstring de `edecan_db.vault._serialize_bundle`).
    Usar esto en vez de `edecan_schemas.TokenBundle` evita que `edecan-vehicles`
    (`pyproject.toml`, propiedad de WP-V4-01, este paquete no lo toca) necesite
    depender de `edecan-schemas` solo por esta única escritura — mismo
    espíritu que `edecan_smarthome`, que tampoco depende de `edecan_db`/
    `edecan_schemas` ("por duck typing", ver su propio README/docstrings).
    """

    access_token: str
    refresh_token: str | None = None
    expires_at: Any | None = None
    scopes: list[str] = field(default_factory=list)
    token_type: str = "config"


def _persistir_refresh_token(
    vault: Any, tenant_id: Any, account_id: Any, client_id: str, client_secret: str
) -> Callable[[str], Awaitable[None]]:
    """Fabrica el `on_refresh_token` que `SmartcarProvider` llama cuando
    Smartcar rota el `refresh_token` (ver docstring del módulo) — persiste la
    config completa (client_id/client_secret + el refresh_token nuevo) de
    vuelta en el vault del tenant, vía `_VaultBundle` (duck typing, ver esa
    clase)."""

    async def _on_refresh_token(nuevo_refresh_token: str) -> None:
        await vault.put(
            tenant_id,
            account_id,
            _VaultBundle(
                access_token=json.dumps(
                    {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": nuevo_refresh_token,
                    }
                ),
                scopes=["smartcar"],
            ),
        )

    return _on_refresh_token
