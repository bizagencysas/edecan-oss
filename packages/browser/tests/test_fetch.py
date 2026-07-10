"""Tests de `edecan_browser.fetch`: `HttpxFetcher`, el guard de `PlaywrightFetcher`
y `get_fetcher`.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_browser.fetch import HttpxFetcher, PlaywrightFetcher, get_fetcher


@respx.mock
async def test_httpx_fetcher_pagina_html(fake_settings):
    respx.get("https://ejemplo.com/pagina").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><head><title>Hola</title></head><body>Mundo</body></html>",
        )
    )
    pagina = await HttpxFetcher(fake_settings()).fetch("https://ejemplo.com/pagina")
    assert pagina.status == 200
    assert pagina.content_type == "text/html"
    assert pagina.html is not None and "Hola" in pagina.html
    assert pagina.text is None
    assert pagina.url_final == "https://ejemplo.com/pagina"


@respx.mock
async def test_httpx_fetcher_contenido_no_html_va_a_text(fake_settings):
    respx.get("https://ejemplo.com/datos.json").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "application/json"}, text='{"a": 1}'
        )
    )
    pagina = await HttpxFetcher(fake_settings()).fetch("https://ejemplo.com/datos.json")
    assert pagina.html is None
    assert pagina.text == '{"a": 1}'


@respx.mock
async def test_httpx_fetcher_sigue_redirects(fake_settings):
    # El destino del redirect pasa de nuevo por `check_navigation` (guardrail
    # SSRF, ver docstring de `HttpxFetcher`) — de ahí el mock de su robots.txt,
    # igual que cualquier otra URL evaluada por policy en estos tests.
    respx.get("https://ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ejemplo.com/viejo").mock(
        return_value=httpx.Response(301, headers={"location": "https://ejemplo.com/nuevo"})
    )
    respx.get("https://ejemplo.com/nuevo").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<p>ok</p>")
    )
    pagina = await HttpxFetcher(fake_settings()).fetch("https://ejemplo.com/viejo")
    assert pagina.url_final == "https://ejemplo.com/nuevo"
    assert pagina.status == 200


@respx.mock
async def test_httpx_fetcher_no_sigue_redirect_hacia_metadata_de_nube(fake_settings):
    """Repro del hallazgo SSRF-vía-redirect: `check_navigation` solo se evaluaba
    sobre la URL original (`edecan_browser.tools`, antes de llamar a `fetch()`),
    nunca sobre a dónde redirige — un dominio público ya aprobado podía usar un
    302 para escapar hacia metadata de nube / red privada y `HttpxFetcher` lo
    seguía igual. Ahora cada salto se re-valida antes de pedirse.
    """
    respx.get("https://ejemplo.com/oferta").mock(
        return_value=httpx.Response(
            302,
            headers={
                "location": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
            },
        )
    )
    # A propósito NO se mockea la URL de metadata: si el fetcher la siguiera pese
    # a todo, este test fallaría con un error de respx en vez de devolver
    # contenido falso que oculte la regresión.
    with pytest.raises(httpx.HTTPError):
        await HttpxFetcher(fake_settings()).fetch("https://ejemplo.com/oferta")


@respx.mock
async def test_httpx_fetcher_demasiados_redirects_lanza_toomanyredirects(fake_settings):
    # `HttpxFetcher` pide como máximo 6 veces (1 original + 5 redirects) antes
    # de rendirse: si las 6 respuestas son redirects, se registran exactamente
    # esas 6 rutas (`assert_all_called` de respx exige que no sobre ninguna).
    for i in range(6):
        respx.get(f"https://ejemplo.com/paso{i}").mock(
            return_value=httpx.Response(
                302, headers={"location": f"https://ejemplo.com/paso{i + 1}"}
            )
        )
    respx.get("https://ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))

    with pytest.raises(httpx.TooManyRedirects):
        await HttpxFetcher(fake_settings()).fetch("https://ejemplo.com/paso0")


@respx.mock
async def test_httpx_fetcher_corta_al_superar_max_bytes(fake_settings):
    cuerpo = "x" * 10_000
    respx.get("https://ejemplo.com/grande").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/plain"}, text=cuerpo)
    )
    settings = fake_settings(BROWSER_MAX_FETCH_BYTES=500)

    pagina = await HttpxFetcher(settings).fetch("https://ejemplo.com/grande")

    assert pagina.bytes_len == 500
    assert pagina.text is not None
    assert len(pagina.text) == 500


@respx.mock
async def test_httpx_fetcher_no_corta_paginas_bajo_el_limite(fake_settings):
    respx.get("https://ejemplo.com/chico").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/plain"}, text="hola")
    )
    settings = fake_settings(BROWSER_MAX_FETCH_BYTES=500)

    pagina = await HttpxFetcher(settings).fetch("https://ejemplo.com/chico")

    assert pagina.bytes_len == 4
    assert pagina.text == "hola"


@respx.mock
async def test_httpx_fetcher_usa_el_user_agent_configurado_y_sin_credenciales(fake_settings):
    ruta = respx.get("https://ejemplo.com/pagina").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<p>hi</p>")
    )
    settings = fake_settings(BROWSER_USER_AGENT="MiBotDePrueba/9.9")

    await HttpxFetcher(settings).fetch("https://ejemplo.com/pagina")

    enviado = ruta.calls.last.request
    nombres_headers = {k.lower() for k in enviado.headers.keys()}
    assert enviado.headers["user-agent"] == "MiBotDePrueba/9.9"
    assert "authorization" not in nombres_headers
    assert "cookie" not in nombres_headers


@respx.mock
async def test_httpx_fetcher_no_reutiliza_cookies_entre_llamadas(fake_settings):
    respx.get("https://ejemplo.com/a").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html", "set-cookie": "sesion=abc123; Path=/"},
            text="<p>a</p>",
        )
    )
    ruta_b = respx.get("https://ejemplo.com/b").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<p>b</p>")
    )

    fetcher = HttpxFetcher(fake_settings())
    await fetcher.fetch("https://ejemplo.com/a")
    await fetcher.fetch("https://ejemplo.com/b")

    enviado_b = ruta_b.calls.last.request
    assert "cookie" not in {k.lower() for k in enviado_b.headers.keys()}


def test_get_fetcher_default_es_httpx(fake_settings):
    assert isinstance(get_fetcher(fake_settings()), HttpxFetcher)
    assert isinstance(get_fetcher(None), HttpxFetcher)


def test_get_fetcher_provider_desconocido_cae_a_httpx(fake_settings):
    assert isinstance(get_fetcher(fake_settings(BROWSER_FETCH_PROVIDER="algo-raro")), HttpxFetcher)


def test_playwright_fetcher_sin_extra_da_import_error_claro(fake_settings):
    try:
        import playwright  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("playwright está instalado en este entorno; el guard de import no aplica.")

    with pytest.raises(ImportError, match="playwright"):
        PlaywrightFetcher(fake_settings())

    with pytest.raises(ImportError, match="playwright"):
        get_fetcher(fake_settings(BROWSER_FETCH_PROVIDER="playwright"))
