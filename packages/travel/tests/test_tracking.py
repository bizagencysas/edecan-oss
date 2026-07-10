"""Tests de `edecan_travel.tracking`: `AfterShipClient` con `respx` (sin red real) —
header `as-api-key`, detección automática de courier, parseo de checkpoints, y errores.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_travel.tracking import AFTERSHIP_BASE_URL, AfterShipClient, TrackingError

API_KEY = "mi-api-key-de-aftership"
_UN_COURIER_DHL = {"total": 1, "couriers": [{"slug": "dhl", "name": "DHL"}]}


# ---------------------------------------------------------------------------
# listar_couriers — ping barato de validación.
# ---------------------------------------------------------------------------


@respx.mock
async def test_listar_couriers_manda_el_header_as_api_key():
    ruta = respx.get(f"{AFTERSHIP_BASE_URL}/couriers").mock(
        return_value=httpx.Response(200, json={"meta": {"code": 200}, "data": _UN_COURIER_DHL})
    )
    client = AfterShipClient(API_KEY)

    couriers = await client.listar_couriers()

    assert couriers == [{"slug": "dhl", "name": "DHL"}]
    assert ruta.calls.last.request.headers["as-api-key"] == API_KEY


@respx.mock
async def test_listar_couriers_api_key_invalida_lanza_tracking_error_con_mensaje():
    respx.get(f"{AFTERSHIP_BASE_URL}/couriers").mock(
        return_value=httpx.Response(
            401,
            json={
                "meta": {"code": 401, "type": "Unauthorized", "message": "Invalid API key"},
                "data": {},
            },
        )
    )
    client = AfterShipClient(API_KEY)

    with pytest.raises(TrackingError, match="Invalid API key"):
        await client.listar_couriers()


# ---------------------------------------------------------------------------
# detectar_courier
# ---------------------------------------------------------------------------


@respx.mock
async def test_detectar_courier_manda_el_tracking_number_y_devuelve_el_slug():
    ruta = respx.post(f"{AFTERSHIP_BASE_URL}/couriers/detect").mock(
        return_value=httpx.Response(200, json={"meta": {"code": 200}, "data": _UN_COURIER_DHL})
    )
    client = AfterShipClient(API_KEY)

    slug = await client.detectar_courier("1234567890")

    assert slug == "dhl"
    body = ruta.calls.last.request.content.decode()
    assert "1234567890" in body


@respx.mock
async def test_detectar_courier_sin_coincidencias_devuelve_none():
    respx.post(f"{AFTERSHIP_BASE_URL}/couriers/detect").mock(
        return_value=httpx.Response(
            200, json={"meta": {"code": 200}, "data": {"total": 0, "couriers": []}}
        )
    )
    client = AfterShipClient(API_KEY)

    assert await client.detectar_courier("no-existe") is None


# ---------------------------------------------------------------------------
# rastrear
# ---------------------------------------------------------------------------


@respx.mock
async def test_rastrear_con_courier_slug_no_llama_a_detect():
    ruta_detect = respx.post(f"{AFTERSHIP_BASE_URL}/couriers/detect")
    respx.get(f"{AFTERSHIP_BASE_URL}/trackings/dhl/999").mock(
        return_value=httpx.Response(
            200,
            json={
                "meta": {"code": 200},
                "data": {
                    "tracking": {
                        "slug": "dhl",
                        "tag": "InTransit",
                        "expected_delivery": "2026-08-10",
                        "checkpoints": [
                            {
                                "checkpoint_time": "2026-08-01T09:00:00",
                                "message": "Paquete recogido",
                                "city": "Bogotá",
                                "country_name": "Colombia",
                            }
                        ],
                    }
                },
            },
        )
    )
    client = AfterShipClient(API_KEY)

    rastreo = await client.rastrear("999", "dhl")

    assert rastreo.estado == "InTransit"
    assert rastreo.courier == "dhl"
    assert rastreo.entrega_estimada == "2026-08-10"
    assert len(rastreo.checkpoints) == 1
    assert rastreo.checkpoints[0].mensaje == "Paquete recogido"
    assert rastreo.checkpoints[0].lugar == "Bogotá, Colombia"
    assert rastreo.checkpoints[0].fecha == "2026-08-01T09:00:00"
    assert ruta_detect.call_count == 0


@respx.mock
async def test_rastrear_sin_courier_slug_lo_detecta_primero():
    respx.post(f"{AFTERSHIP_BASE_URL}/couriers/detect").mock(
        return_value=httpx.Response(
            200, json={"meta": {"code": 200}, "data": {"total": 1, "couriers": [{"slug": "fedex"}]}}
        )
    )
    ruta_tracking = respx.get(f"{AFTERSHIP_BASE_URL}/trackings/fedex/555").mock(
        return_value=httpx.Response(
            200,
            json={
                "meta": {"code": 200},
                "data": {"tracking": {"slug": "fedex", "tag": "Delivered", "checkpoints": []}},
            },
        )
    )
    client = AfterShipClient(API_KEY)

    rastreo = await client.rastrear("555")

    assert rastreo.estado == "Delivered"
    assert rastreo.courier == "fedex"
    assert ruta_tracking.call_count == 1


@respx.mock
async def test_rastrear_courier_no_detectado_lanza_tracking_error():
    respx.post(f"{AFTERSHIP_BASE_URL}/couriers/detect").mock(
        return_value=httpx.Response(
            200, json={"meta": {"code": 200}, "data": {"total": 0, "couriers": []}}
        )
    )
    client = AfterShipClient(API_KEY)

    with pytest.raises(TrackingError, match="No pude identificar"):
        await client.rastrear("numero-desconocido")


@respx.mock
async def test_rastrear_sin_checkpoints_devuelve_lista_vacia():
    respx.get(f"{AFTERSHIP_BASE_URL}/trackings/ups/1").mock(
        return_value=httpx.Response(
            200,
            json={"meta": {"code": 200}, "data": {"tracking": {"slug": "ups", "tag": "Pending"}}},
        )
    )
    client = AfterShipClient(API_KEY)

    rastreo = await client.rastrear("1", "ups")

    assert rastreo.checkpoints == []
    assert rastreo.entrega_estimada is None


@respx.mock
async def test_rastrear_tracking_no_encontrado_lanza_tracking_error():
    respx.get(f"{AFTERSHIP_BASE_URL}/trackings/dhl/000").mock(
        return_value=httpx.Response(
            404,
            json={
                "meta": {
                    "code": 4004,
                    "type": "TrackingNotFoundError",
                    "message": "Tracking does not exist",
                },
                "data": {},
            },
        )
    )
    client = AfterShipClient(API_KEY)

    with pytest.raises(TrackingError, match="Tracking does not exist"):
        await client.rastrear("000", "dhl")


@respx.mock
async def test_rastrear_error_de_red_lanza_tracking_error():
    respx.get(f"{AFTERSHIP_BASE_URL}/trackings/dhl/1").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    client = AfterShipClient(API_KEY)

    with pytest.raises(TrackingError, match="No se pudo conectar"):
        await client.rastrear("1", "dhl")
