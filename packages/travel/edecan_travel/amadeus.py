"""Cliente de la API oficial **Amadeus Self-Service** (`ARCHITECTURE.md` §14, WP-V5-09).

`AmadeusClient` habla con `https://test.api.amadeus.com` (default) o
`https://api.amadeus.com` según `environment`, usando el `api_key`/`api_secret`
**del propio tenant** — Edecán nunca opera una cuenta de Amadeus propia. Autentica con
OAuth2 `client_credentials` (`POST /v1/security/oauth2/token`) y cachea el token en
memoria por instancia, refrescándolo con un margen de 60s antes de que venza (nunca deja
que una request en vuelo use un token que está por expirar en el borde).

`environment` queda en `"test"` por defecto **a propósito**: la API de pruebas de Amadeus
no cobra ni consume la cuota de producción del tenant — así nadie gasta cuota productiva
sin querer solo por conectar sus credenciales. El tenant debe elegir `"production"`
explícitamente (`PUT /v1/viajes/credentials`, `apps/api/edecan_api/routers/viajes.py`)
cuando de verdad quiera resultados reales de producción.

Todos los métodos son de **solo lectura/información** — este módulo nunca llama a
ninguna API de booking/pago de Amadeus (`docs/viajes.md`, guardrail de dinero): reservar
de verdad es una decisión humana, fuera de Edecán por completo.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

AMADEUS_TEST_BASE_URL = "https://test.api.amadeus.com"
AMADEUS_PRODUCTION_BASE_URL = "https://api.amadeus.com"
_TOKEN_PATH = "/v1/security/oauth2/token"
# Margen de seguridad: se refresca el token ANTES de que venza de verdad, para que
# ninguna request en vuelo use uno que expira a mitad de camino.
TOKEN_EXPIRY_MARGIN_SECONDS = 60
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_ADULTOS = 1
DEFAULT_MAX_RESULTADOS = 10
# Amadeus exige como máximo unos pocos `hotelIds` por request de ofertas antes de que la
# URL se vuelva poco práctica — recortamos la lista de `by-city` a un tamaño razonable.
_MAX_HOTEL_IDS_POR_CONSULTA = 20

Environment = Literal["test", "production"]


class TravelError(RuntimeError):
    """Error legible del dominio viajes, sin secretos ni traceback crudo."""


@dataclass(frozen=True)
class VueloOferta:
    """Una oferta de `GET /v2/shopping/flight-offers`, ya aplanada."""

    id: str
    aerolinea: str
    salida: str | None
    llegada: str | None
    origen: str | None
    destino: str | None
    escalas: int
    precio_total: str
    moneda: str
    booking_url: str | None = None


@dataclass(frozen=True)
class HotelOferta:
    """Una oferta de `GET /v3/shopping/hotel-offers`, ya aplanada."""

    id: str
    nombre: str
    rating: str | None
    precio_total: str
    moneda: str
    checkin: str | None = None
    checkout: str | None = None
    booking_url: str | None = None


@dataclass(frozen=True)
class EstadoVuelo:
    """Horarios programados de `GET /v2/schedule/flights` — `terminal`/`puerta` quedan
    en `None` cuando Amadeus no los trae (p. ej. es normal en `environment="test"`,
    ver `docs/viajes.md`)."""

    carrier: str
    numero: str
    fecha: str
    origen: str | None
    destino: str | None
    salida_programada: str | None
    llegada_programada: str | None
    terminal_salida: str | None = None
    puerta_salida: str | None = None


def _extraer_error_amadeus(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"Amadeus respondió {response.status_code}: {response.text[:300]}"
    errores = data.get("errors") if isinstance(data, dict) else None
    if isinstance(errores, list) and errores:
        primero = errores[0] if isinstance(errores[0], dict) else {}
        detalle = primero.get("detail") or primero.get("title") or "Error desconocido"
        codigo = primero.get("code")
        sufijo = f" (código {codigo})" if codigo is not None else ""
        return f"{detalle}{sufijo}"
    return f"Amadeus respondió {response.status_code}: {response.text[:300]}"


def _valor_timing(seccion: dict[str, Any] | None, qualifier: str) -> str | None:
    if not seccion:
        return None
    for timing in seccion.get("timings") or []:
        if isinstance(timing, dict) and timing.get("qualifier") == qualifier:
            value = timing.get("value")
            return str(value) if value is not None else None
    return None


def _parse_vuelo_oferta(item: dict[str, Any]) -> VueloOferta:
    itinerarios = item.get("itineraries") or [{}]
    primer_itinerario = itinerarios[0] if itinerarios else {}
    segmentos = primer_itinerario.get("segments") or [{}]
    primer_segmento = segmentos[0] if segmentos else {}
    ultimo_segmento = segmentos[-1] if segmentos else {}
    salida = primer_segmento.get("departure") or {}
    llegada = ultimo_segmento.get("arrival") or {}
    precio = item.get("price") or {}
    aerolineas_validadoras = item.get("validatingAirlineCodes") or []
    aerolinea = (
        aerolineas_validadoras[0]
        if aerolineas_validadoras
        else primer_segmento.get("carrierCode", "?")
    )
    return VueloOferta(
        id=str(item.get("id", "")),
        aerolinea=str(aerolinea),
        salida=salida.get("at"),
        llegada=llegada.get("at"),
        origen=salida.get("iataCode"),
        destino=llegada.get("iataCode"),
        escalas=max(len(segmentos) - 1, 0),
        precio_total=str(precio.get("total", "0")),
        moneda=str(precio.get("currency", "")),
    )


def _parse_hotel_oferta(item: dict[str, Any]) -> HotelOferta | None:
    hotel = item.get("hotel") or {}
    ofertas = item.get("offers") or []
    if not ofertas:
        return None
    primera_oferta = ofertas[0] if isinstance(ofertas[0], dict) else {}
    precio = primera_oferta.get("price") or {}
    rating = hotel.get("rating")
    return HotelOferta(
        id=str(primera_oferta.get("id") or hotel.get("hotelId", "")),
        nombre=str(hotel.get("name") or "?"),
        rating=str(rating) if rating is not None else None,
        precio_total=str(precio.get("total", "0")),
        moneda=str(precio.get("currency", "")),
        checkin=primera_oferta.get("checkInDate"),
        checkout=primera_oferta.get("checkOutDate"),
    )


def _parse_estado_vuelo(
    item: dict[str, Any], *, carrier: str, numero: str, fecha: str
) -> EstadoVuelo:
    puntos = item.get("flightPoints") or []
    punto_salida = next((p for p in puntos if isinstance(p, dict) and p.get("departure")), {})
    punto_llegada = next(
        (p for p in reversed(puntos) if isinstance(p, dict) and p.get("arrival")), {}
    )
    salida_info = punto_salida.get("departure") or {}
    llegada_info = punto_llegada.get("arrival") or {}
    terminal = salida_info.get("terminal") or {}
    puerta = salida_info.get("gate") or {}
    return EstadoVuelo(
        carrier=carrier,
        numero=numero,
        fecha=fecha,
        origen=punto_salida.get("iataCode"),
        destino=punto_llegada.get("iataCode"),
        salida_programada=_valor_timing(salida_info, "STD"),
        llegada_programada=_valor_timing(llegada_info, "STA"),
        terminal_salida=terminal.get("code"),
        puerta_salida=puerta.get("mainGate"),
    )


class AmadeusClient:
    """Cliente OAuth2 `client_credentials` contra Amadeus Self-Service. Ver el
    docstring del módulo para el porqué de `environment="test"` por defecto."""

    name = "amadeus"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        environment: Environment = "test",
        http_client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self.environment: Environment = "production" if environment == "production" else "test"
        self._base_url = (
            AMADEUS_PRODUCTION_BASE_URL
            if self.environment == "production"
            else AMADEUS_TEST_BASE_URL
        )
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente (pool de conexiones) — solo si lo creamos
        nosotros; si nos inyectaron uno (tests), su ciclo de vida es de quien lo creó."""
        if self._owns_client:
            await self._client.aclose()

    async def _get_token(self) -> str:
        now = time.monotonic()
        if self._token and now < (self._token_expires_at - TOKEN_EXPIRY_MARGIN_SECONDS):
            return self._token
        try:
            response = await self._client.post(
                _TOKEN_PATH,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._api_key,
                    "client_secret": self._api_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise TravelError(f"No se pudo conectar con Amadeus: {exc}") from exc
        if response.status_code >= 400:
            raise TravelError(_extraer_error_amadeus(response))
        try:
            data = response.json()
        except ValueError as exc:
            raise TravelError("Amadeus devolvió una respuesta no-JSON inesperada.") from exc
        token = data.get("access_token")
        if not token:
            raise TravelError("Amadeus no devolvió un access_token válido.")
        self._token = str(token)
        try:
            expires_in = float(data.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0.0
        self._token_expires_at = now + expires_in
        return self._token

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        token = await self._get_token()
        try:
            response = await self._client.request(
                method, path, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            raise TravelError(f"No se pudo conectar con Amadeus: {exc}") from exc
        if response.status_code >= 400:
            raise TravelError(_extraer_error_amadeus(response))
        try:
            return response.json()
        except ValueError as exc:
            raise TravelError("Amadeus devolvió una respuesta no-JSON inesperada.") from exc

    async def buscar_vuelos(
        self,
        origen: str,
        destino: str,
        fecha: str,
        *,
        adultos: int = DEFAULT_ADULTOS,
        max_resultados: int = DEFAULT_MAX_RESULTADOS,
    ) -> list[VueloOferta]:
        """`GET /v2/shopping/flight-offers` — solo búsqueda, nunca reserva nada."""
        data = await self._request(
            "GET",
            "/v2/shopping/flight-offers",
            params={
                "originLocationCode": origen.upper(),
                "destinationLocationCode": destino.upper(),
                "departureDate": fecha,
                "adults": adultos,
                "max": max_resultados,
            },
        )
        filas = data.get("data") or []
        return [_parse_vuelo_oferta(item) for item in filas if isinstance(item, dict)]

    async def buscar_hoteles(
        self, ciudad: str, checkin: str, checkout: str, *, adultos: int = DEFAULT_ADULTOS
    ) -> list[HotelOferta]:
        """`GET /v1/reference-data/locations/hotels/by-city` (resuelve los hoteles de la
        ciudad) + `GET /v3/shopping/hotel-offers` (ofertas reales para esos hoteles) —
        solo búsqueda, nunca reserva nada."""
        hoteles_data = await self._request(
            "GET",
            "/v1/reference-data/locations/hotels/by-city",
            params={"cityCode": ciudad.upper()},
        )
        hotel_ids = [
            h["hotelId"]
            for h in (hoteles_data.get("data") or [])
            if isinstance(h, dict) and h.get("hotelId")
        ][:_MAX_HOTEL_IDS_POR_CONSULTA]
        if not hotel_ids:
            return []

        offers_data = await self._request(
            "GET",
            "/v3/shopping/hotel-offers",
            params={
                "hotelIds": ",".join(hotel_ids),
                "checkInDate": checkin,
                "checkOutDate": checkout,
                "adults": adultos,
            },
        )
        filas = offers_data.get("data") or []
        ofertas = [_parse_hotel_oferta(item) for item in filas if isinstance(item, dict)]
        return [o for o in ofertas if o is not None]

    async def estado_vuelo(self, carrier: str, numero: str, fecha: str) -> EstadoVuelo:
        """`GET /v2/schedule/flights` — horarios programados, nunca modifica nada."""
        data = await self._request(
            "GET",
            "/v2/schedule/flights",
            params={
                "carrierCode": carrier.upper(),
                "flightNumber": str(numero),
                "scheduledDepartureDate": fecha,
            },
        )
        filas = data.get("data") or []
        if not filas or not isinstance(filas[0], dict):
            raise TravelError(
                f"Amadeus no encontró información del vuelo {carrier.upper()}{numero} en {fecha}."
            )
        return _parse_estado_vuelo(
            filas[0], carrier=carrier.upper(), numero=str(numero), fecha=fecha
        )


def vuelo_a_dict(oferta: VueloOferta) -> dict[str, Any]:
    return asdict(oferta)


def hotel_a_dict(oferta: HotelOferta) -> dict[str, Any]:
    return asdict(oferta)


def estado_a_dict(estado: EstadoVuelo) -> dict[str, Any]:
    return asdict(estado)
