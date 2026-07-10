"""Tests de la revalidación SSRF/checkout de `PlaywrightFetcher` (`HOTFIXES_PENDIENTES.md`
punto 7) — TODOS SIN Playwright real instalado (sigue siendo el extra opcional de
`ROADMAP_V2.md` §7.11): unit tests puros de las funciones módulo-level que
`PlaywrightFetcher.fetch()` usa internamente, cada una testeable con dobles/`respx` porque
ninguna depende de un `Page`/`Browser` real:

- `_validar_navegacion`: envuelve `check_navigation` — mismos casos (IPs privadas,
  localhost, esquemas raros, rutas transaccionales) que `test_policy.py`, solo que aquí se
  ejercitan a través del envoltorio de `fetch.py`.
- `_manejar_ruta_playwright`: el handler de `page.route("**/*", ...)` — se ejercita con
  dobles mínimos de `route`/`request` (`_FakeRoute`/`_FakeRequest`) que implementan
  únicamente `is_navigation_request()`/`.frame`/`.url` y los métodos `abort`/`continue_`.
- `_cadena_de_redirects`: la revalidación defensiva post-`goto()` — se ejercita con dobles
  de `response`/`request` (`_FakeResponse`/`_FakeGotoRequest`) que encadenan
  `.redirected_from`.
- `_error_navegacion_bloqueada`: el tipo/formato de excepción, para que quede fijado como
  contrato (mismo tipo que usa `HttpxFetcher`).

Ningún test de este archivo importa ni instancia `PlaywrightFetcher` — eso seguiría
requiriendo el extra opcional `playwright` (ver `test_fetch.py::
test_playwright_fetcher_sin_extra_da_import_error_claro`, que sí cubre el guard de import).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_browser.fetch import (
    _cadena_de_redirects,
    _error_navegacion_bloqueada,
    _manejar_ruta_playwright,
    _validar_navegacion,
)

# ---------------------------------------------------------------------------
# `_validar_navegacion` — envuelve `check_navigation` (mismos casos que test_policy.py)
# ---------------------------------------------------------------------------


@respx.mock
async def test_validar_navegacion_permite_url_publica(fake_settings):
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    motivo = await _validar_navegacion("https://tienda.ejemplo.com/producto/1", fake_settings())
    assert motivo is None


@respx.mock
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/interno",
        "http://169.254.169.254/latest/meta-data/",  # metadata AWS/GCP
        "http://10.1.2.3/panel",
        "http://192.168.1.1/router",
        "http://[::1]/interno",
        "http://localhost:5000/admin",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
async def test_validar_navegacion_rechaza_ips_y_hosts_privados(fake_settings, url):
    motivo = await _validar_navegacion(url, fake_settings())
    assert motivo is not None
    assert "SSRF" in motivo


@respx.mock
async def test_validar_navegacion_rechaza_esquemas_raros(fake_settings):
    for url in ("ftp://tienda.ejemplo.com/archivo", "file:///etc/passwd", "javascript:alert(1)"):
        motivo = await _validar_navegacion(url, fake_settings())
        assert motivo is not None


@respx.mock
@pytest.mark.parametrize(
    "url",
    [
        "https://tienda.ejemplo.com/checkout/pagar",
        "https://tienda.ejemplo.com/cart/123",
        "https://tienda.ejemplo.com/carrito",
        "https://banco.ejemplo.com/payment/confirmar",
        "https://tienda.ejemplo.com/pago/tarjeta",
        "https://app.ejemplo.com/login",
        "https://app.ejemplo.com/signin?next=/home",
        "https://app.ejemplo.com/account/ajustes",
    ],
)
async def test_validar_navegacion_rechaza_rutas_transaccionales(fake_settings, url):
    motivo = await _validar_navegacion(url, fake_settings())
    assert motivo is not None
    assert "compra" in motivo.lower() or "login" in motivo.lower()


@respx.mock
async def test_validar_navegacion_rechaza_robots_disallow(fake_settings):
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /privado\n")
    )
    motivo = await _validar_navegacion("https://tienda.ejemplo.com/privado/x", fake_settings())
    assert motivo is not None
    assert "robots.txt" in motivo


@respx.mock
async def test_validar_navegacion_funciona_sin_settings():
    # `settings=None` (default) no debe reventar — mismo criterio defensivo del resto del
    # paquete (ver `_settings_valor`/`check_navigation`).
    respx.get("https://ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    motivo = await _validar_navegacion("https://ejemplo.com/pagina")
    assert motivo is None


# ---------------------------------------------------------------------------
# `_manejar_ruta_playwright` — handler de `page.route()`, con dobles (sin Playwright)
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Doble mínimo de `playwright.async_api.Request`: solo lo que usa
    `_manejar_ruta_playwright` (`is_navigation_request()`, `.frame`, `.url`)."""

    def __init__(self, url: str, *, navigation: bool = True, frame: object | None = None) -> None:
        self.url = url
        self._navigation = navigation
        self.frame = frame

    def is_navigation_request(self) -> bool:
        return self._navigation


class _FakeRoute:
    """Doble mínimo de `playwright.async_api.Route`: registra si se llamó
    `abort()`/`continue_()` (y con qué motivo) para que el test lo verifique."""

    def __init__(self, request: _FakeRequest) -> None:
        self.request = request
        self.aborted_with: str | None = None
        self.continued = False

    async def abort(self, error_code: str | None = None) -> None:
        self.aborted_with = error_code

    async def continue_(self) -> None:
        self.continued = True


@respx.mock
async def test_handler_bloquea_navegacion_principal_hacia_ip_privada(fake_settings):
    main_frame = object()
    request = _FakeRequest("http://169.254.169.254/latest/meta-data/", frame=main_frame)
    route = _FakeRoute(request)

    motivo = await _manejar_ruta_playwright(route, main_frame=main_frame, settings=fake_settings())

    assert motivo is not None
    assert "SSRF" in motivo
    assert route.aborted_with == "blockedbyclient"
    assert route.continued is False


@respx.mock
async def test_handler_bloquea_navegacion_principal_hacia_checkout(fake_settings):
    main_frame = object()
    request = _FakeRequest("https://tienda.ejemplo.com/checkout/pagar", frame=main_frame)
    route = _FakeRoute(request)

    motivo = await _manejar_ruta_playwright(route, main_frame=main_frame, settings=fake_settings())

    assert motivo is not None
    assert route.aborted_with == "blockedbyclient"
    assert route.continued is False


@respx.mock
async def test_handler_permite_navegacion_principal_hacia_url_publica(fake_settings):
    respx.get("https://ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    main_frame = object()
    request = _FakeRequest("https://ejemplo.com/pagina", frame=main_frame)
    route = _FakeRoute(request)

    motivo = await _manejar_ruta_playwright(route, main_frame=main_frame, settings=fake_settings())

    assert motivo is None
    assert route.continued is True
    assert route.aborted_with is None


async def test_handler_ignora_subrecursos_sin_evaluarlos(fake_settings):
    # `is_navigation_request()=False` (imagen/script/XHR): se deja pasar SIN llamar a
    # `check_navigation` — a propósito no se mockea ningún `respx` en este test: si el
    # handler evaluara este caso de todos modos, `check_navigation` intentaría una
    # resolución DNS/SSRF real y este test fallaría o divergiría, en vez de pasar en falso.
    main_frame = object()
    request = _FakeRequest(
        "http://169.254.169.254/deberia-ignorarse.png", navigation=False, frame=main_frame
    )
    route = _FakeRoute(request)

    motivo = await _manejar_ruta_playwright(route, main_frame=main_frame, settings=fake_settings())

    assert motivo is None
    assert route.continued is True
    assert route.aborted_with is None


async def test_handler_ignora_navegaciones_de_frames_no_principales(fake_settings):
    # Navegación real (`is_navigation_request()=True`) pero de un iframe, no del frame
    # principal de la página — tampoco se evalúa (mismo motivo que el test anterior: sin
    # `respx` registrado, cualquier llamada real a `check_navigation` haría fallar el test).
    main_frame = object()
    otro_frame = object()
    request = _FakeRequest(
        "http://169.254.169.254/iframe-interno", navigation=True, frame=otro_frame
    )
    route = _FakeRoute(request)

    motivo = await _manejar_ruta_playwright(route, main_frame=main_frame, settings=fake_settings())

    assert motivo is None
    assert route.continued is True


# ---------------------------------------------------------------------------
# `_cadena_de_redirects` — defensa en profundidad tras `page.goto()` (dobles)
# ---------------------------------------------------------------------------


class _FakeGotoRequest:
    """Doble mínimo de `playwright.async_api.Request` para la cadena de redirects:
    solo `.url` y `.redirected_from` (encadenable)."""

    def __init__(self, url: str, redirected_from: _FakeGotoRequest | None = None) -> None:
        self.url = url
        self.redirected_from = redirected_from


class _FakeGotoResponse:
    def __init__(self, request: _FakeGotoRequest) -> None:
        self.request = request


def test_cadena_de_redirects_sin_redirect_repite_la_url_final():
    peticion = _FakeGotoRequest("https://ejemplo.com/pagina")
    respuesta = _FakeGotoResponse(peticion)

    urls = _cadena_de_redirects("https://ejemplo.com/pagina", respuesta)

    # `page_url` y `response.request.url` coinciden (no hubo redirect) — se repite a
    # propósito (ver docstring de `_cadena_de_redirects`), inofensivo para el llamador.
    assert urls == ["https://ejemplo.com/pagina", "https://ejemplo.com/pagina"]


def test_cadena_de_redirects_incluye_cada_salto_del_encadenado():
    original = _FakeGotoRequest("https://ejemplo.com/viejo")
    intermedio = _FakeGotoRequest("https://ejemplo.com/intermedio", redirected_from=original)
    final = _FakeGotoRequest("https://ejemplo.com/nuevo", redirected_from=intermedio)
    respuesta = _FakeGotoResponse(final)

    urls = _cadena_de_redirects("https://ejemplo.com/nuevo", respuesta)

    assert urls == [
        "https://ejemplo.com/nuevo",
        "https://ejemplo.com/nuevo",
        "https://ejemplo.com/intermedio",
        "https://ejemplo.com/viejo",
    ]


def test_cadena_de_redirects_sin_response_devuelve_solo_page_url():
    assert _cadena_de_redirects("https://ejemplo.com/pagina", None) == [
        "https://ejemplo.com/pagina"
    ]


def test_cadena_de_redirects_no_entra_en_bucle_infinito_con_ciclo_defensivo():
    # No debería ocurrir con Playwright real, pero `_cadena_de_redirects` se protege con un
    # set de `id()` vistos — si un doble/mock formara un ciclo, no debe colgarse.
    a = _FakeGotoRequest("https://ejemplo.com/a")
    b = _FakeGotoRequest("https://ejemplo.com/b", redirected_from=a)
    a.redirected_from = b  # ciclo artificial
    respuesta = _FakeGotoResponse(b)

    urls = _cadena_de_redirects("https://ejemplo.com/b", respuesta)

    assert urls[0] == "https://ejemplo.com/b"
    assert len(urls) == 3  # page_url + b + a, se corta antes de repetir b


# ---------------------------------------------------------------------------
# `_error_navegacion_bloqueada` — mismo tipo/formato que usa `HttpxFetcher`
# ---------------------------------------------------------------------------


def test_error_navegacion_bloqueada_es_httpx_http_error_con_url_y_motivo():
    error = _error_navegacion_bloqueada("http://169.254.169.254/x", "bloqueado por SSRF")
    assert isinstance(error, httpx.HTTPError)
    assert "169.254.169.254" in str(error)
    assert "bloqueado por SSRF" in str(error)
