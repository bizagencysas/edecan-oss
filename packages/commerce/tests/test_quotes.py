"""Tests de `edecan_commerce.quotes`: `StubQuotes`, `CoinGeckoQuotes`, `get_quote_provider`."""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_commerce.quotes import CoinGeckoQuotes, StubQuotes, get_quote_provider


async def test_stub_quotes_es_determinista_y_no_hace_red():
    proveedor = StubQuotes()
    primera = await proveedor.quote("BTC")
    segunda = await proveedor.quote("BTC")

    assert primera.precio == segunda.precio
    assert primera.simbolo == "BTC"
    assert primera.moneda == "USD"
    assert primera.fuente == "stub"
    assert primera.precio > 0


async def test_stub_quotes_simbolos_distintos_dan_precios_distintos():
    proveedor = StubQuotes()
    btc = await proveedor.quote("BTC")
    eth = await proveedor.quote("ETH")
    assert btc.precio != eth.precio


async def test_stub_quotes_normaliza_simbolo_a_mayusculas():
    proveedor = StubQuotes()
    minuscula = await proveedor.quote("btc")
    mayuscula = await proveedor.quote("BTC")
    assert minuscula.simbolo == "BTC"
    assert minuscula.precio == mayuscula.precio


async def test_stub_quotes_simbolo_vacio_lanza_valueerror():
    with pytest.raises(ValueError, match="Símbolo vacío"):
        await StubQuotes().quote("   ")


def test_get_quote_provider_resuelve_segun_settings(fake_settings):
    assert isinstance(get_quote_provider(fake_settings(QUOTES_PROVIDER="stub")), StubQuotes)
    assert isinstance(
        get_quote_provider(fake_settings(QUOTES_PROVIDER="coingecko")), CoinGeckoQuotes
    )
    # Proveedor desconocido: cae a stub sin reventar.
    assert isinstance(get_quote_provider(fake_settings(QUOTES_PROVIDER="algo-raro")), StubQuotes)
    # Campo ausente por completo (settings real todavía sin QUOTES_PROVIDER, ROADMAP_V2.md §7.5).
    assert isinstance(get_quote_provider(object()), StubQuotes)


@respx.mock
async def test_coingecko_quotes_parsea_precio_real():
    respx.get("https://api.coingecko.com/api/v3/simple/price").mock(
        return_value=httpx.Response(200, json={"bitcoin": {"usd": 65432.1}})
    )

    cotizacion = await CoinGeckoQuotes().quote("btc")

    assert cotizacion.simbolo == "BTC"
    assert cotizacion.precio == 65432.1
    assert cotizacion.moneda == "USD"
    assert cotizacion.fuente == "coingecko"

    # La llamada real llevó los params correctos (id de CoinGecko para BTC, vs_currencies=usd).
    request = respx.calls.last.request
    assert request.url.params["ids"] == "bitcoin"
    assert request.url.params["vs_currencies"] == "usd"


async def test_coingecko_quotes_simbolo_desconocido_lanza_valueerror_sin_llamar_red():
    # Sin ningún route de respx registrado: si el código intentara una llamada HTTP igual,
    # este test fallaría con el error de "solicitud no simulada" de respx en vez de con el
    # ValueError esperado — así que este test también prueba "cero llamadas de red".
    with pytest.raises(ValueError, match="no reconocido"):
        await CoinGeckoQuotes().quote("SIMBOLO-INVENTADO")


@respx.mock
async def test_coingecko_quotes_sin_precio_en_la_respuesta_lanza_valueerror():
    respx.get("https://api.coingecko.com/api/v3/simple/price").mock(
        return_value=httpx.Response(200, json={"bitcoin": {}})
    )
    with pytest.raises(ValueError, match="no devolvió precio"):
        await CoinGeckoQuotes().quote("BTC")


@respx.mock
async def test_coingecko_quotes_propaga_error_http():
    respx.get("https://api.coingecko.com/api/v3/simple/price").mock(
        return_value=httpx.Response(500, text="internal error")
    )
    with pytest.raises(httpx.HTTPStatusError):
        await CoinGeckoQuotes().quote("ETH")
