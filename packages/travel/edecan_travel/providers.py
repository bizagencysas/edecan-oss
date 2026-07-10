"""Proveedores bring-your-own de `edecan_travel` (`ARCHITECTURE.md` §14, WP-V5-09).

Calcado línea por línea de `packages/ads/edecan_ads/providers.py::get_tenant_ads_provider`
(la plantilla exacta que pide el paquete de trabajo): `TravelProvider`/`TrackingProvider`
son protocolos intercambiables (`Protocol runtime_checkable`, mismo estilo que
`edecan_ads.providers.AdsProvider`); `StubTravelProvider`/`StubTrackingProvider` son
deterministas y 100% offline (proveedor por defecto, ningún tenant conectó su cuenta de
Amadeus/AfterShip todavía); `AmadeusClient`/`AfterShipClient` (`.amadeus`/`.tracking`) son
los proveedores reales.

## Nunca una credencial de plataforma (patrón v4 de `router.py`, ver la nota de seguridad)

`get_tenant_travel_provider(ctx)`/`get_tenant_tracking_provider(ctx)` son la variante
bring-your-own real: si el tenant conectó su cuenta (`PUT /v1/viajes/credentials` /
`PUT /v1/viajes/rastreo/credentials`, `apps/api/edecan_api/routers/viajes.py`,
`TokenVault` connector_key `TRAVEL_CONNECTOR_KEY`/`TRACKING_CONNECTOR_KEY`), la usa; si
no —o si falla cualquier paso de esa resolución— cae al stub correspondiente, nunca
revienta. Igual que `edecan_ads`, no hay ningún nivel intermedio de "config de
plataforma": Edecán nunca tiene una cuenta de Amadeus/AfterShip propia que ofrecer como
segundo nivel — el patrón crítico que `packages/llm/edecan_llm/router.py::
_build_provider_from_config` tuvo que corregir en v4 (un campo vacío del tenant NUNCA
debe caer a `self._settings`/variables de entorno de plataforma) simplemente no puede
ocurrir aquí: no existe ningún `self._settings` al que este módulo pueda caer, solo el
tenant o el stub.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import text as sql_text

from .amadeus import AmadeusClient, EstadoVuelo, HotelOferta, VueloOferta
from .tracking import AfterShipClient, CheckpointRastreo, RastreoPaquete

logger = logging.getLogger(__name__)

# `connector_key` del `TokenVault` (ARCHITECTURE.md §14, pinned en el paquete de
# trabajo) — definidos acá y, por separado, importados directo en
# `apps/api/edecan_api/routers/viajes.py` (que sí depende de este paquete, mismo
# criterio que `edecan_ads.providers.ADS_CONNECTOR_KEY`/`routers/ads.py`).
TRAVEL_CONNECTOR_KEY = "travel"
TRACKING_CONNECTOR_KEY = "tracking"

_ENVIRONMENTS_VALIDOS = frozenset({"test", "production"})


@runtime_checkable
class TravelProvider(Protocol):
    """Protocolo común de proveedor de vuelos/hoteles."""

    async def buscar_vuelos(
        self, origen: str, destino: str, fecha: str, *, adultos: int = 1, max_resultados: int = 10
    ) -> list[VueloOferta]: ...

    async def buscar_hoteles(
        self, ciudad: str, checkin: str, checkout: str, *, adultos: int = 1
    ) -> list[HotelOferta]: ...

    async def estado_vuelo(self, carrier: str, numero: str, fecha: str) -> EstadoVuelo: ...


@runtime_checkable
class TrackingProvider(Protocol):
    """Protocolo común de proveedor de rastreo de paquetes."""

    async def rastrear(
        self, tracking_number: str, courier_slug: str | None = None
    ) -> RastreoPaquete: ...


class StubTravelProvider:
    """Proveedor determinista y 100% offline — proveedor por defecto (ningún tenant
    conectó su cuenta de Amadeus todavía). No hace ninguna llamada de red; los 2-3
    resultados de cada método están CLARAMENTE marcados como ejemplo en su propio texto."""

    name = "stub"

    async def buscar_vuelos(
        self, origen: str, destino: str, fecha: str, *, adultos: int = 1, max_resultados: int = 10
    ) -> list[VueloOferta]:
        origen_norm, destino_norm = origen.upper(), destino.upper()
        return [
            VueloOferta(
                id="stub-vuelo-1",
                aerolinea="XX (ejemplo, sin cuenta de Amadeus conectada)",
                salida=f"{fecha}T08:00:00",
                llegada=f"{fecha}T11:30:00",
                origen=origen_norm,
                destino=destino_norm,
                escalas=0,
                precio_total="199.00",
                moneda="USD",
            ),
            VueloOferta(
                id="stub-vuelo-2",
                aerolinea="YY (ejemplo, sin cuenta de Amadeus conectada)",
                salida=f"{fecha}T14:15:00",
                llegada=f"{fecha}T19:05:00",
                origen=origen_norm,
                destino=destino_norm,
                escalas=1,
                precio_total="149.50",
                moneda="USD",
            ),
        ]

    async def buscar_hoteles(
        self, ciudad: str, checkin: str, checkout: str, *, adultos: int = 1
    ) -> list[HotelOferta]:
        return [
            HotelOferta(
                id="stub-hotel-1",
                nombre="Hotel Centro Ejemplo (modo offline, sin cuenta de Amadeus conectada)",
                rating="4",
                precio_total="89.00",
                moneda="USD",
                checkin=checkin,
                checkout=checkout,
            ),
            HotelOferta(
                id="stub-hotel-2",
                nombre="Hotel Aeropuerto Ejemplo (modo offline, sin cuenta de Amadeus conectada)",
                rating="3",
                precio_total="65.00",
                moneda="USD",
                checkin=checkin,
                checkout=checkout,
            ),
        ]

    async def estado_vuelo(self, carrier: str, numero: str, fecha: str) -> EstadoVuelo:
        return EstadoVuelo(
            carrier=carrier.upper(),
            numero=str(numero),
            fecha=fecha,
            origen="XXX",
            destino="YYY",
            salida_programada=f"{fecha}T08:00:00",
            llegada_programada=f"{fecha}T11:30:00",
            terminal_salida="1",
            puerta_salida="B12",
        )


class StubTrackingProvider:
    """Proveedor determinista y 100% offline — proveedor por defecto (ningún tenant
    conectó su cuenta de AfterShip todavía)."""

    name = "stub"

    async def rastrear(
        self, tracking_number: str, courier_slug: str | None = None
    ) -> RastreoPaquete:
        return RastreoPaquete(
            estado="InTransit",
            courier=courier_slug or "stub-courier",
            checkpoints=[
                CheckpointRastreo(
                    fecha=None,
                    mensaje=(
                        "Paquete recibido en origen (ejemplo, sin cuenta de AfterShip conectada)"
                    ),
                    lugar="Bogotá, CO",
                ),
                CheckpointRastreo(fecha=None, mensaje="En tránsito (ejemplo)", lugar="Miami, US"),
            ],
            entrega_estimada=None,
        )


def _environment_valido(valor: Any) -> str:
    texto = str(valor or "test").strip().lower()
    return texto if texto in _ENVIRONMENTS_VALIDOS else "test"


async def get_tenant_travel_provider(ctx: Any) -> TravelProvider:
    """`TravelProvider` bring-your-own del tenant, con fallback a `StubTravelProvider`
    (ver docstring del módulo). Lee `ctx.tenant_id`/`ctx.session`/`ctx.vault` de forma
    defensiva (`ctx` es `edecan_core.tools.ToolContext` en producción, pero un `Any` a
    propósito): si falta cualquiera de los tres, o el tenant nunca hizo
    `PUT /v1/viajes/credentials`, o CUALQUIER paso falla (vault caído, JSON corrupto,
    faltan campos), se degrada a `StubTravelProvider` — nunca revienta
    `buscar_vuelos`/`buscar_hoteles`/`estado_vuelo`/`GET /v1/viajes/*` por esto, solo
    `logger.warning`.
    """
    tenant_id = getattr(ctx, "tenant_id", None)
    session = getattr(ctx, "session", None)
    vault = getattr(ctx, "vault", None)
    if tenant_id is None or session is None or vault is None:
        return StubTravelProvider()

    try:
        row = (
            await session.execute(
                sql_text(
                    "SELECT id FROM connector_accounts WHERE tenant_id = :tenant_id "
                    "AND connector_key = :connector_key ORDER BY created_at DESC LIMIT 1"
                ),
                {"tenant_id": tenant_id, "connector_key": TRAVEL_CONNECTOR_KEY},
            )
        ).mappings().first()
        if row is None:
            logger.warning(
                "El tenant_id=%s no conectó su cuenta de Amadeus (PUT /v1/viajes/credentials) — "
                "uso StubTravelProvider.",
                tenant_id,
            )
            return StubTravelProvider()

        bundle = await vault.get(tenant_id=tenant_id, connector_account_id=row["id"])
        if bundle is None or not bundle.access_token:
            return StubTravelProvider()

        data = json.loads(bundle.access_token)
        api_key = data.get("api_key")
        api_secret = data.get("api_secret")
        if not (api_key and api_secret):
            return StubTravelProvider()
        environment = _environment_valido(data.get("environment"))
        return AmadeusClient(api_key=api_key, api_secret=api_secret, environment=environment)
    except Exception:
        logger.warning(
            "No se pudo resolver el TravelProvider bring-your-own del tenant_id=%s; "
            "uso StubTravelProvider.",
            tenant_id,
            exc_info=True,
        )
        return StubTravelProvider()


async def get_tenant_tracking_provider(ctx: Any) -> TrackingProvider:
    """`TrackingProvider` bring-your-own del tenant, con fallback a
    `StubTrackingProvider` — mismo criterio exacto que `get_tenant_travel_provider`
    (ver arriba), solo que resuelve `connector_key=TRACKING_CONNECTOR_KEY` y construye
    un `AfterShipClient`."""
    tenant_id = getattr(ctx, "tenant_id", None)
    session = getattr(ctx, "session", None)
    vault = getattr(ctx, "vault", None)
    if tenant_id is None or session is None or vault is None:
        return StubTrackingProvider()

    try:
        row = (
            await session.execute(
                sql_text(
                    "SELECT id FROM connector_accounts WHERE tenant_id = :tenant_id "
                    "AND connector_key = :connector_key ORDER BY created_at DESC LIMIT 1"
                ),
                {"tenant_id": tenant_id, "connector_key": TRACKING_CONNECTOR_KEY},
            )
        ).mappings().first()
        if row is None:
            logger.warning(
                "El tenant_id=%s no conectó su cuenta de AfterShip (PUT "
                "/v1/viajes/rastreo/credentials) — uso StubTrackingProvider.",
                tenant_id,
            )
            return StubTrackingProvider()

        bundle = await vault.get(tenant_id=tenant_id, connector_account_id=row["id"])
        if bundle is None or not bundle.access_token:
            return StubTrackingProvider()

        data = json.loads(bundle.access_token)
        api_key = data.get("api_key")
        if not api_key:
            return StubTrackingProvider()
        return AfterShipClient(api_key=api_key)
    except Exception:
        logger.warning(
            "No se pudo resolver el TrackingProvider bring-your-own del tenant_id=%s; "
            "uso StubTrackingProvider.",
            tenant_id,
            exc_info=True,
        )
        return StubTrackingProvider()
