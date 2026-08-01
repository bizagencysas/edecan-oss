"""Tests de `edecan_travel.amadeus`: `AmadeusClient` con `respx` (sin red real) —
OAuth2 `client_credentials` (obtención + caché + expiración con margen), `environment`
por defecto, y el parseo de `buscar_vuelos`/`buscar_hoteles`/`estado_vuelo`.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_travel.amadeus import (
    AMADEUS_PRODUCTION_BASE_URL,
    AMADEUS_TEST_BASE_URL,
    AmadeusClient,
    TravelError,
)

API_KEY = "mi-api-key-de-prueba"
API_SECRET = "mi-api-secret-de-prueba"

_TOKEN_RESPONSE = {
    "type": "amadeusOAuth2Token",
    "access_token": "token-de-acceso-123",
    "token_type": "Bearer",
    "expires_in": 1799,
    "state": "approved",
}


def _mock_token(base_url: str, **overrides) -> respx.Route:
    body = {**_TOKEN_RESPONSE, **overrides}
    return respx.post(f"{base_url}/v1/security/oauth2/token").mock(
        return_value=httpx.Response(200, json=body)
    )


# ---------------------------------------------------------------------------
# environment / base_url
# ---------------------------------------------------------------------------


def test_environment_por_defecto_es_test():
    client = AmadeusClient(API_KEY, API_SECRET)
    assert client.environment == "test"
    assert client._base_url == AMADEUS_TEST_BASE_URL  # noqa: SLF001 - test interno


def test_environment_production_explicito():
    client = AmadeusClient(API_KEY, API_SECRET, environment="production")
    assert client.environment == "production"
    assert client._base_url == AMADEUS_PRODUCTION_BASE_URL  # noqa: SLF001


def test_environment_valor_desconocido_cae_a_test():
    client = AmadeusClient(API_KEY, API_SECRET, environment="staging")  # type: ignore[arg-type]
    assert client.environment == "test"


# ---------------------------------------------------------------------------
# OAuth2 client_credentials — obtención, caché, expiración.
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_token_pide_client_credentials_con_las_credenciales_del_tenant():
    ruta = _mock_token(AMADEUS_TEST_BASE_URL)
    client = AmadeusClient(API_KEY, API_SECRET)

    token = await client._get_token()  # noqa: SLF001 - test interno del cliente

    assert token == "token-de-acceso-123"
    enviado = ruta.calls.last.request.content.decode()
    assert "grant_type=client_credentials" in enviado
    assert f"client_id={API_KEY}" in enviado
    assert f"client_secret={API_SECRET}" in enviado


@respx.mock
async def test_get_token_se_cachea_no_repite_la_llamada():
    ruta = _mock_token(AMADEUS_TEST_BASE_URL)
    client = AmadeusClient(API_KEY, API_SECRET)

    await client._get_token()  # noqa: SLF001
    await client._get_token()  # noqa: SLF001

    assert ruta.call_count == 1


@respx.mock
async def test_get_token_expirado_se_refresca():
    ruta = _mock_token(AMADEUS_TEST_BASE_URL, expires_in=30)  # < margen de 60s
    client = AmadeusClient(API_KEY, API_SECRET)

    await client._get_token()  # noqa: SLF001 - vence "ya" por el margen de seguridad
    await client._get_token()  # noqa: SLF001

    assert ruta.call_count == 2


@respx.mock
async def test_get_token_sin_access_token_en_respuesta_lanza_travel_error():
    respx.post(f"{AMADEUS_TEST_BASE_URL}/v1/security/oauth2/token").mock(
        return_value=httpx.Response(200, json={"token_type": "Bearer"})
    )
    client = AmadeusClient(API_KEY, API_SECRET)

    with pytest.raises(TravelError, match="access_token"):
        await client._get_token()  # noqa: SLF001


@respx.mock
async def test_get_token_credenciales_invalidas_lanza_travel_error_con_mensaje_de_amadeus():
    respx.post(f"{AMADEUS_TEST_BASE_URL}/v1/security/oauth2/token").mock(
        return_value=httpx.Response(
            401,
            json={"error": "invalid_client", "error_description": "Client credentials are invalid"},
        )
    )
    client = AmadeusClient(API_KEY, API_SECRET)

    with pytest.raises(TravelError):
        await client._get_token()  # noqa: SLF001


@respx.mock
async def test_get_token_error_de_red_lanza_travel_error():
    respx.post(f"{AMADEUS_TEST_BASE_URL}/v1/security/oauth2/token").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    client = AmadeusClient(API_KEY, API_SECRET)

    with pytest.raises(TravelError, match="No se pudo conectar"):
        await client._get_token()  # noqa: SLF001


# ---------------------------------------------------------------------------
# buscar_vuelos
# ---------------------------------------------------------------------------


@respx.mock
async def test_buscar_vuelos_parsea_la_oferta_completa():
    _mock_token(AMADEUS_TEST_BASE_URL)
    ruta = respx.get(f"{AMADEUS_TEST_BASE_URL}/v2/shopping/flight-offers").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "1",
                        "validatingAirlineCodes": ["AV"],
                        "itineraries": [
                            {
                                "segments": [
                                    {
                                        "departure": {
                                            "iataCode": "BOG", "at": "2026-08-01T08:00:00"
                                        },
                                        "arrival": {
                                            "iataCode": "PTY", "at": "2026-08-01T09:30:00"
                                        },
                                        "carrierCode": "AV",
                                    },
                                    {
                                        "departure": {
                                            "iataCode": "PTY", "at": "2026-08-01T10:30:00"
                                        },
                                        "arrival": {
                                            "iataCode": "MIA", "at": "2026-08-01T13:00:00"
                                        },
                                        "carrierCode": "AV",
                                    },
                                ]
                            }
                        ],
                        "price": {"total": "245.30", "currency": "USD"},
                    }
                ]
            },
        )
    )
    client = AmadeusClient(API_KEY, API_SECRET)

    ofertas = await client.buscar_vuelos("bog", "mia", "2026-08-01")

    assert len(ofertas) == 1
    oferta = ofertas[0]
    assert oferta.id == "1"
    assert oferta.aerolinea == "AV"
    assert oferta.origen == "BOG"
    assert oferta.destino == "MIA"
    assert oferta.salida == "2026-08-01T08:00:00"
    assert oferta.llegada == "2026-08-01T13:00:00"
    assert oferta.escalas == 1
    assert oferta.precio_total == "245.30"
    assert oferta.moneda == "USD"

    request = ruta.calls.last.request
    assert request.url.params["originLocationCode"] == "BOG"
    assert request.url.params["destinationLocationCode"] == "MIA"
    assert request.url.params["departureDate"] == "2026-08-01"
    assert request.url.params["adults"] == "1"
    assert request.url.params["max"] == "10"
    assert request.headers["Authorization"] == "Bearer token-de-acceso-123"


@respx.mock
async def test_buscar_vuelos_sin_resultados_devuelve_lista_vacia():
    _mock_token(AMADEUS_TEST_BASE_URL)
    respx.get(f"{AMADEUS_TEST_BASE_URL}/v2/shopping/flight-offers").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    client = AmadeusClient(API_KEY, API_SECRET)

    assert await client.buscar_vuelos("BOG", "MIA", "2026-08-01") == []


@respx.mock
async def test_buscar_vuelos_error_de_amadeus_lanza_travel_error_con_detalle():
    _mock_token(AMADEUS_TEST_BASE_URL)
    respx.get(f"{AMADEUS_TEST_BASE_URL}/v2/shopping/flight-offers").mock(
        return_value=httpx.Response(
            400,
            json={
                "errors": [
                    {
                        "status": 400,
                        "code": 425,
                        "title": "INVALID DATE",
                        "detail": "Fecha inválida",
                    }
                ]
            },
        )
    )
    client = AmadeusClient(API_KEY, API_SECRET)

    with pytest.raises(TravelError, match="Fecha inválida"):
        await client.buscar_vuelos("BOG", "MIA", "no-es-fecha")


# ---------------------------------------------------------------------------
# buscar_hoteles
# ---------------------------------------------------------------------------


@respx.mock
async def test_buscar_hoteles_dos_pasos_by_city_y_hotel_offers():
    _mock_token(AMADEUS_TEST_BASE_URL)
    respx.get(f"{AMADEUS_TEST_BASE_URL}/v1/reference-data/locations/hotels/by-city").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"hotelId": "MCLONGHM", "name": "Hotel A"}, {"hotelId": "MCPARSHM"}]},
        )
    )
    ruta_ofertas = respx.get(f"{AMADEUS_TEST_BASE_URL}/v3/shopping/hotel-offers").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "hotel": {"hotelId": "MCLONGHM", "name": "Hotel Centro", "rating": "4"},
                        "offers": [
                            {
                                "id": "offer-1",
                                "checkInDate": "2026-09-01",
                                "checkOutDate": "2026-09-05",
                                "price": {"total": "89.00", "currency": "EUR"},
                            }
                        ],
                    }
                ]
            },
        )
    )
    client = AmadeusClient(API_KEY, API_SECRET)

    ofertas = await client.buscar_hoteles("par", "2026-09-01", "2026-09-05")

    assert len(ofertas) == 1
    oferta = ofertas[0]
    assert oferta.id == "offer-1"
    assert oferta.nombre == "Hotel Centro"
    assert oferta.rating == "4"
    assert oferta.precio_total == "89.00"
    assert oferta.moneda == "EUR"
    assert oferta.checkin == "2026-09-01"
    assert oferta.checkout == "2026-09-05"

    params = ruta_ofertas.calls.last.request.url.params
    assert params["hotelIds"] == "MCLONGHM,MCPARSHM"
    assert params["checkInDate"] == "2026-09-01"


@respx.mock
async def test_buscar_hoteles_sin_hoteles_en_la_ciudad_no_llama_a_hotel_offers():
    _mock_token(AMADEUS_TEST_BASE_URL)
    respx.get(f"{AMADEUS_TEST_BASE_URL}/v1/reference-data/locations/hotels/by-city").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    ruta_ofertas = respx.get(f"{AMADEUS_TEST_BASE_URL}/v3/shopping/hotel-offers")
    client = AmadeusClient(API_KEY, API_SECRET)

    ofertas = await client.buscar_hoteles("XXX", "2026-09-01", "2026-09-05")

    assert ofertas == []
    assert ruta_ofertas.call_count == 0


@respx.mock
async def test_buscar_hoteles_hotel_sin_ofertas_se_omite():
    _mock_token(AMADEUS_TEST_BASE_URL)
    respx.get(f"{AMADEUS_TEST_BASE_URL}/v1/reference-data/locations/hotels/by-city").mock(
        return_value=httpx.Response(200, json={"data": [{"hotelId": "ABC"}]})
    )
    respx.get(f"{AMADEUS_TEST_BASE_URL}/v3/shopping/hotel-offers").mock(
        return_value=httpx.Response(
            200, json={"data": [{"hotel": {"hotelId": "ABC"}, "offers": []}]}
        )
    )
    client = AmadeusClient(API_KEY, API_SECRET)

    assert await client.buscar_hoteles("PAR", "2026-09-01", "2026-09-05") == []


# ---------------------------------------------------------------------------
# estado_vuelo
# ---------------------------------------------------------------------------


@respx.mock
async def test_estado_vuelo_parsea_horarios_terminal_y_puerta():
    _mock_token(AMADEUS_TEST_BASE_URL)
    respx.get(f"{AMADEUS_TEST_BASE_URL}/v2/schedule/flights").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "flightPoints": [
                            {
                                "iataCode": "MAD",
                                "departure": {
                                    "timings": [
                                        {"qualifier": "STD", "value": "2026-08-01T11:40:00+01:00"}
                                    ],
                                    "terminal": {"code": "4"},
                                    "gate": {"mainGate": "B12"},
                                },
                            },
                            {
                                "iataCode": "VGO",
                                "arrival": {
                                    "timings": [
                                        {"qualifier": "STA", "value": "2026-08-01T13:00:00+01:00"}
                                    ]
                                },
                            },
                        ]
                    }
                ]
            },
        )
    )
    client = AmadeusClient(API_KEY, API_SECRET)

    estado = await client.estado_vuelo("ib", "532", "2026-08-01")

    assert estado.carrier == "IB"
    assert estado.numero == "532"
    assert estado.origen == "MAD"
    assert estado.destino == "VGO"
    assert estado.salida_programada == "2026-08-01T11:40:00+01:00"
    assert estado.llegada_programada == "2026-08-01T13:00:00+01:00"
    assert estado.terminal_salida == "4"
    assert estado.puerta_salida == "B12"


@respx.mock
async def test_estado_vuelo_sin_terminal_ni_puerta_quedan_en_none():
    _mock_token(AMADEUS_TEST_BASE_URL)
    respx.get(f"{AMADEUS_TEST_BASE_URL}/v2/schedule/flights").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "flightPoints": [
                            {
                                "iataCode": "MAD",
                                "departure": {
                                    "timings": [
                                        {"qualifier": "STD", "value": "2026-08-01T11:40:00+01:00"}
                                    ]
                                },
                            },
                            {
                                "iataCode": "VGO",
                                "arrival": {
                                    "timings": [
                                        {"qualifier": "STA", "value": "2026-08-01T13:00:00+01:00"}
                                    ]
                                },
                            },
                        ]
                    }
                ]
            },
        )
    )
    client = AmadeusClient(API_KEY, API_SECRET)

    estado = await client.estado_vuelo("IB", "532", "2026-08-01")

    assert estado.terminal_salida is None
    assert estado.puerta_salida is None


@respx.mock
async def test_estado_vuelo_sin_datos_lanza_travel_error():
    _mock_token(AMADEUS_TEST_BASE_URL)
    respx.get(f"{AMADEUS_TEST_BASE_URL}/v2/schedule/flights").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    client = AmadeusClient(API_KEY, API_SECRET)

    with pytest.raises(TravelError, match="no encontró"):
        await client.estado_vuelo("IB", "9999", "2026-08-01")
