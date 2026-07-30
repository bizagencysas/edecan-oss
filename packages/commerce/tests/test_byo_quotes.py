"""Regresión anti-fuga dedicada de `edecan_commerce.quotes` (Barrido de
seguridad v5).

Veredicto de la auditoría (ver `docs/credenciales.md` sección "Auditoría v5"
y `HOTFIXES_PENDIENTES.md" sección "Barrido v5"): N/A — no hay NINGÚN
`connector_key` de credencial bring-your-own para cotizaciones, ni falta
hacerlo. `CoinGeckoQuotes` habla la API pública "Simple Price" de CoinGecko,
que no exige autenticación (`docs/dinero-real.md`); `get_quote_provider`
resuelve `settings.QUOTES_PROVIDER` (`stub|coingecko`), que NO es un
secreto — es un nombre de proveedor, mismo criterio que `IMAGES_MODEL`/
`ANTHROPIC_MODEL_PRINCIPAL` (`ARCHITECTURE.md` §0, "valores NO-secretos con
default de plataforma").

Este archivo deja constancia EMPÍRICA de ese veredicto en vez de solo
documentarlo en prosa: inspecciona la request HTTP real que sale hacia
CoinGecko (capturada con `respx`) y confirma que NUNCA lleva ningún header de
autenticación ni ningún valor proveniente de `settings` — ni siquiera existe
un campo de credencial que pudiera filtrarse. Si en el futuro CoinGecko (o un
proveedor de cotizaciones nuevo) empezara a requerir una API key, este test
fallaría en cuanto alguien agregara esa key leyéndola de `settings` sin pasar
antes por el mismo patrón bring-your-own que el resto del repo (`TokenVault`
por tenant) — recordatorio explícito en el docstring de abajo.
"""

from __future__ import annotations

import inspect

import httpx
import respx
from edecan_commerce.quotes import CoinGeckoQuotes, StubQuotes, get_quote_provider

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


def test_coingecko_quotes_no_acepta_ninguna_api_key_en_su_constructor():
    """Guardrail de firma: si alguien agregara un parámetro `api_key`/
    `settings` a `CoinGeckoQuotes` en el futuro (la forma más directa de
    reintroducir el patrón de fuga de v4 acá), este test lo detecta de
    inmediato."""
    parametros = set(inspect.signature(CoinGeckoQuotes.__init__).parameters) - {"self"}
    assert parametros == {"base_url"}


@respx.mock
async def test_coingecko_quotes_request_real_no_lleva_ningun_header_de_autenticacion(
    monkeypatch,
):
    """Aunque el entorno tenga una variable "de plataforma" con pinta de
    credencial (centinela), la request real a CoinGecko no lleva NINGÚN
    header de autenticación — porque `CoinGeckoQuotes` no construye ninguno,
    nunca lee `os.environ` ni `settings`."""
    # "COINGECKO_API_KEY" ni siquiera existe como campo en Settings.
    monkeypatch.setenv("COINGECKO_API_KEY", _SENTINEL)
    route = respx.get("https://api.coingecko.com/api/v3/simple/price").mock(
        return_value=httpx.Response(200, json={"bitcoin": {"usd": 65000.5}})
    )

    quote = await CoinGeckoQuotes().quote("BTC")

    assert route.called
    request = route.calls.last.request
    assert "authorization" not in {h.lower() for h in request.headers.keys()}
    assert "x-api-key" not in {h.lower() for h in request.headers.keys()}
    assert _SENTINEL not in str(request.url)
    assert _SENTINEL not in {v for v in request.headers.values()}
    assert quote.precio == 65000.5
    assert quote.fuente == "coingecko"


def test_get_quote_provider_settings_solo_elige_nombre_no_credencial():
    """`QUOTES_PROVIDER` es un NOMBRE de proveedor (`"stub"`/`"coingecko"`),
    nunca una credencial — `get_quote_provider` no tiene ninguna rama que lea
    un campo tipo `*_API_KEY` de `settings` para construir `CoinGeckoQuotes`."""
    from types import SimpleNamespace

    provider = get_quote_provider(
        SimpleNamespace(QUOTES_PROVIDER="coingecko", COINGECKO_API_KEY_QUE_NO_EXISTE=_SENTINEL)
    )
    assert isinstance(provider, CoinGeckoQuotes)
    # Ningún atributo de la instancia construida contiene el centinela.
    assert _SENTINEL not in vars(provider).values()


def test_get_quote_provider_default_es_stub_100_por_ciento_offline():
    provider = get_quote_provider(object())  # ni siquiera un objeto con atributos
    assert isinstance(provider, StubQuotes)
