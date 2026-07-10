"""Obtención de páginas web (`ARCHITECTURE.md` §10.14; `ROADMAP_V2.md` §7.5, §7.11).

`PageFetcher` es el protocolo común. `HttpxFetcher` es el fetcher por
defecto (`BROWSER_FETCH_PROVIDER=httpx`, o si el settei no existe): hace
`GET` puro con `httpx`, sin cookies persistentes ni credenciales, con
límites duros de tamaño/tiempo/redirects — nunca existe, en este módulo ni
en ningún otro de `edecan_browser`, un camino de código que envíe `POST`,
rellene un formulario o mantenga una sesión autenticada. Esa ausencia es
intencional: es precisamente el guardrail que impide que Edecán complete
compras o inicie sesión por su cuenta (ver `edecan_browser.policy`).

`PlaywrightFetcher` es un fetcher opcional (extra `playwright`, import
diferido y guardeado) que solo se activa si `BROWSER_FETCH_PROVIDER=="playwright"`
— ningún test de este paquete lo INSTANCIA con Playwright real instalado.
Sí revalida cada navegación contra el guardrail SSRF/checkout, igual que
`HttpxFetcher` (`HOTFIXES_PENDIENTES.md` punto 7): la lógica de decisión
(`_validar_navegacion`) y la del handler de `page.route()`
(`_manejar_ruta_playwright`) son funciones módulo-level puras, testeadas
directamente en `test_fetch_playwright_policy.py` sin necesitar Playwright.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from .policy import check_navigation

logger = logging.getLogger(__name__)

_USER_AGENT_DEFECTO = "EdecanBot/1.0"
_TIMEOUT_DEFECTO_SEGUNDOS = 20.0
_MAX_BYTES_DEFECTO = 2_000_000
_MAX_REDIRECTS = 5


@dataclass(frozen=True)
class FetchedPage:
    """Resultado de `PageFetcher.fetch(url)`.

    `html` está poblado únicamente si el `content-type` de la respuesta es
    HTML; en cualquier otro caso el contenido decodificado va en `text` (los
    dos nunca están poblados a la vez) — así `edecan_browser.extract` sabe si
    puede parsear con BeautifulSoup o si debe tratarlo como texto plano.
    `bytes_len` es el tamaño real descargado (ya recortado a
    `BROWSER_MAX_FETCH_BYTES` si la página era más grande).
    """

    url_final: str
    status: int
    content_type: str
    html: str | None
    text: str | None
    bytes_len: int


@runtime_checkable
class PageFetcher(Protocol):
    """Protocolo común de obtención de páginas — `HttpxFetcher` y
    `PlaywrightFetcher` (opcional) lo implementan.
    """

    async def fetch(self, url: str) -> FetchedPage: ...


def _settings_valor(settings: Any, campo: str, default: Any) -> Any:
    """Lectura defensiva de settings (convención dura de ROADMAP_V2.md §7.5):
    nunca revienta si `settings` es `None` o le falta el campo.
    """
    valor = getattr(settings, campo, None) if settings is not None else None
    return valor if valor is not None else default


class HttpxFetcher:
    """Fetcher por defecto: `GET` puro vía un `httpx.AsyncClient` efímero.

    Decisiones de diseño (no obvias, documentadas a propósito):

    - Se crea un `httpx.AsyncClient` **nuevo en cada `fetch()`** — nunca se
      reutiliza uno entre llamadas. Así nunca hay un cookie jar que sobreviva
      entre navegaciones (ni entre tenants): cumple "sin cookies
      persistentes" al pie de la letra, sin tener que gestionar limpieza
      manual de cookies.
    - Nunca se agrega ningún header de autenticación/credenciales — solo
      `User-Agent`.
    - `follow_redirects=False` a propósito: cada salto de redirect se sigue a
      mano, hasta `_MAX_REDIRECTS` (5) veces, y **cada URL de destino pasa de
      nuevo por `edecan_browser.policy.check_navigation()`** antes de
      pedirse — el `check_navigation` que hace `edecan_browser.tools` sobre
      la URL original nunca ve las URLs intermedias, así que sin este
      re-chequeo un dominio público ya aprobado podría responder con un `30x`
      hacia metadata de nube o una IP privada y `HttpxFetcher` lo seguiría
      igual (bypass del guardrail SSRF). Si algún salto no pasa la política,
      o si se superan los redirects permitidos, se lanza `httpx.HTTPError`
      (`httpx.TooManyRedirects` en ese segundo caso) — quien llama
      —`edecan_browser.tools`— lo atrapa como cualquier otro
      `httpx.HTTPError`.
    - La descarga se hace en streaming (`client.stream`) y se corta apenas se
      supera `BROWSER_MAX_FETCH_BYTES`, sin esperar a bajar la página
      completa primero — importante para páginas enormes o maliciosas.
    """

    def __init__(self, settings: Any = None) -> None:
        self._settings = settings
        self._user_agent = str(_settings_valor(settings, "BROWSER_USER_AGENT", _USER_AGENT_DEFECTO))
        self._timeout = float(
            _settings_valor(settings, "BROWSER_TIMEOUT_SECONDS", _TIMEOUT_DEFECTO_SEGUNDOS)
        )
        self._max_bytes = int(
            _settings_valor(settings, "BROWSER_MAX_FETCH_BYTES", _MAX_BYTES_DEFECTO)
        )

    async def fetch(self, url: str) -> FetchedPage:
        headers = {"User-Agent": self._user_agent}
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=self._timeout,
            headers=headers,
        ) as client:
            url_actual = url
            for _ in range(_MAX_REDIRECTS + 1):
                async with client.stream("GET", url_actual) as response:
                    if not response.has_redirect_location:
                        crudo, cortado = await _leer_hasta_limite(response, self._max_bytes)
                        content_type = response.headers.get("content-type", "")
                        status = response.status_code
                        url_final = str(response.url)
                        break
                    # `next_request` ya trae la URL de destino resuelta a absoluta por
                    # httpx (join de headers relativos incluido) — con
                    # `follow_redirects=False` httpx la calcula pero NO la pide sola.
                    destino = str(response.next_request.url)  # type: ignore[union-attr]
                # Guardrail SSRF (ver docstring de la clase): re-valida el salto ANTES
                # de pedirlo, nunca después — así el fetch al destino bloqueado nunca
                # llega a salir.
                veredicto = await check_navigation(destino, self._settings)
                if not veredicto.allowed:
                    raise httpx.HTTPError(
                        f"Redirect de «{url_actual}» bloqueado hacia «{destino}»: "
                        f"{veredicto.reason}"
                    )
                url_actual = destino
            else:
                raise httpx.TooManyRedirects(f"Demasiados redirects al pedir «{url}».")

        tipo_base = content_type.split(";")[0].strip().lower()
        charset = _charset_de_content_type(content_type)
        decodificado = crudo.decode(charset, errors="replace")
        es_html = "html" in tipo_base
        if cortado:
            logger.info(
                "Fetch de %s cortado a %d bytes (BROWSER_MAX_FETCH_BYTES=%d)",
                url,
                len(crudo),
                self._max_bytes,
            )
        return FetchedPage(
            url_final=url_final,
            status=status,
            content_type=tipo_base,
            html=decodificado if es_html else None,
            text=None if es_html else decodificado,
            bytes_len=len(crudo),
        )


async def _leer_hasta_limite(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """Lee `response` en streaming hasta acumular `max_bytes` y corta ahí —
    nunca descarga de más solo para luego recortar en memoria.
    """
    trozos: list[bytes] = []
    total = 0
    cortado = False
    async for trozo in response.aiter_bytes():
        trozos.append(trozo)
        total += len(trozo)
        if total >= max_bytes:
            cortado = True
            break
    crudo = b"".join(trozos)
    if len(crudo) > max_bytes:
        crudo = crudo[:max_bytes]
        cortado = True
    return crudo, cortado


def _charset_de_content_type(content_type: str) -> str:
    for parte in content_type.split(";")[1:]:
        parte = parte.strip()
        if parte.lower().startswith("charset="):
            return parte.split("=", 1)[1].strip().strip('"') or "utf-8"
    return "utf-8"


async def _validar_navegacion(url: str, settings: Any = None) -> str | None:
    """Envuelve `edecan_browser.policy.check_navigation`: `None` si `url` está permitida,
    si no el motivo de bloqueo (listo para mostrarle al usuario). Función pura módulo-level
    (`HOTFIXES_PENDIENTES.md` punto 7) para que sea unit-testeable SIN Playwright real
    instalado — `PlaywrightFetcher.fetch()` la usa tanto en el handler de `page.route()`
    (`_manejar_ruta_playwright`) como en la revalidación final de `page.url`/la cadena de
    redirects (`_cadena_de_redirects`).
    """
    veredicto = await check_navigation(url, settings)
    return None if veredicto.allowed else veredicto.reason


async def _manejar_ruta_playwright(route: Any, *, main_frame: Any, settings: Any) -> str | None:
    """Handler de `page.route("**/*", ...)` de `PlaywrightFetcher.fetch()`, extraído a
    función módulo-level para poder testearlo con dobles de `route`/`request` sin
    Playwright real instalado (ver `test_fetch_playwright_policy.py`).

    Solo evalúa navegaciones del frame PRINCIPAL (`request.is_navigation_request()` Y
    `request.frame == main_frame`) — cualquier sub-recurso (imagen, script, XHR) o
    navegación de un iframe se deja pasar sin evaluar, ni siquiera se llama a
    `check_navigation` (así páginas normales con decenas de sub-recursos de terceros no se
    ven afectadas). Si `_validar_navegacion` rechaza la URL (SSRF, checkout/pago/login,
    robots.txt, esquema no http/https), aborta la request (`route.abort("blockedbyclient")`)
    y devuelve el motivo; si no, la deja continuar (`route.continue_()`) y devuelve `None`.
    """
    request = route.request
    if request.is_navigation_request() and request.frame == main_frame:
        motivo = await _validar_navegacion(request.url, settings)
        if motivo is not None:
            await route.abort("blockedbyclient")
            return motivo
    await route.continue_()
    return None


def _cadena_de_redirects(page_url: str, response: Any) -> list[str]:
    """URLs a revalidar como defensa en profundidad TRAS `page.goto()` (punto (c) del fix
    SSRF de `PlaywrightFetcher.fetch()`): `page_url` (destino final de la página) más toda
    la cadena `response.request.redirected_from` — por si algún salto de un redirect JS/HTTP
    no llegó a calificar como "navigation request" ante `_manejar_ruta_playwright`. Función
    pura, sin Playwright real, para poder testearla con dobles de `response`/`request`.
    Puede repetir una URL (p. ej. cuando no hubo redirect, `page_url` y
    `response.request.url` coinciden) — inofensivo, `_validar_navegacion` es barata de
    repetir (robots.txt cacheado 10 min por origen, ver `RobotsCache`).
    """
    urls = [page_url]
    request = getattr(response, "request", None) if response is not None else None
    vistos: set[int] = set()
    while request is not None and id(request) not in vistos:
        vistos.add(id(request))
        urls.append(request.url)
        request = request.redirected_from
    return urls


def _error_navegacion_bloqueada(url: str, motivo: str) -> httpx.HTTPError:
    """Mismo tipo de excepción que `HttpxFetcher` lanza para un redirect bloqueado (ver su
    docstring) — así `edecan_browser.tools` atrapa el bloqueo de cualquiera de los dos
    fetchers con el mismo `except httpx.HTTPError`, sin ramificar por proveedor.
    """
    return httpx.HTTPError(f"Navegación bloqueada hacia «{url}»: {motivo}")


class PlaywrightFetcher:
    """Fetcher opcional que renderiza JavaScript con Playwright (Chromium).

    Requiere el extra opcional del paquete: `uv pip install
    'edecan-browser[playwright]'` seguido de `playwright install chromium`
    (la instalación del binario del navegador no la hace `pip`/`uv`, es un
    paso aparte del propio Playwright). El import de `playwright` es
    **diferido** (ocurre recién en `__init__`, no al importar este módulo) y
    **guardeado**: si el paquete no está instalado, se lanza un `ImportError`
    con un mensaje claro en vez de tumbar el import de todo `edecan_browser`
    con un traceback críptico. Ningún test de este paquete instancia esta
    clase con Playwright real instalado — `get_fetcher()` solo la construye
    si `BROWSER_FETCH_PROVIDER` está puesto explícitamente en `"playwright"`.

    Guardrail SSRF/checkout (mismo criterio que `HttpxFetcher`, `HOTFIXES_PENDIENTES.md`
    punto 7): a diferencia de un `GET` puro, Chromium puede seguir redirects HTTP/JS
    internamente sin que este módulo los vea — así que `fetch()` registra
    `page.route("**/*", ...)` ANTES de `goto()` e intercepta TODA request de la página
    (incluida la navegación inicial a `url`, que también pasa por ahí), revalidando cada
    navegación del frame PRINCIPAL contra `edecan_browser.policy.check_navigation()` (vía
    `_validar_navegacion`) antes de dejarla continuar. Cualquier salto hacia una URL de red
    privada/metadata de nube (SSRF) o de checkout/pago/login se aborta
    (`route.abort("blockedbyclient")`), igual que `HttpxFetcher` rechaza esos redirects a
    mano. Como defensa en profundidad adicional, tras `page.goto()` también se revalida
    `page.url` (destino final) y toda la cadena `response.request.redirected_from`
    (`_cadena_de_redirects`), por si algún salto no calificara como "navigation request"
    ante el handler de arriba. Cualquier bloqueo lanza `httpx.HTTPError`
    (`_error_navegacion_bloqueada`) — el mismo tipo de excepción que `HttpxFetcher`, así
    `edecan_browser.tools` atrapa el bloqueo de cualquiera de los dos fetchers igual.
    """

    def __init__(self, settings: Any = None) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - requiere el extra opcional
            raise ImportError(
                "BROWSER_FETCH_PROVIDER=playwright requiere el extra opcional de "
                "Playwright: instala con `uv pip install 'edecan-browser[playwright]'` "
                "y luego corre `playwright install chromium` (ver docs/navegador.md)."
            ) from exc

        self._async_playwright = async_playwright
        self._user_agent = str(_settings_valor(settings, "BROWSER_USER_AGENT", _USER_AGENT_DEFECTO))
        self._timeout = float(
            _settings_valor(settings, "BROWSER_TIMEOUT_SECONDS", _TIMEOUT_DEFECTO_SEGUNDOS)
        )
        self._max_bytes = int(
            _settings_valor(settings, "BROWSER_MAX_FETCH_BYTES", _MAX_BYTES_DEFECTO)
        )

    async def fetch(self, url: str) -> FetchedPage:  # pragma: no cover - requiere el extra opcional
        motivos_bloqueo: list[str] = []
        async with self._async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(user_agent=self._user_agent)

                async def _handler(route: Any) -> None:
                    motivo = await _manejar_ruta_playwright(
                        route, main_frame=page.main_frame, settings=self._settings
                    )
                    if motivo is not None:
                        motivos_bloqueo.append(motivo)

                # (a)/(b) del guardrail SSRF (ver docstring de la clase): intercepta CADA
                # request de la página (incluida la navegación inicial a `url`, que también
                # pasa por aquí) ANTES de pedir `goto()` — así ningún redirect HTTP/JS que
                # Chromium siga internamente escapa a `check_navigation()`.
                await page.route("**/*", _handler)

                try:
                    response = await page.goto(url, timeout=self._timeout * 1000)
                except Exception as exc:
                    # Si Playwright reventó la navegación justo porque NUESTRO handler
                    # abortó la request (`route.abort("blockedbyclient")`), preferimos
                    # nuestro error limpio y tipado al traceback interno de Playwright —
                    # mismo tipo de excepción que `HttpxFetcher`. Si `motivos_bloqueo`
                    # sigue vacío, la falla es de otra causa (red, timeout, DNS): se
                    # re-lanza tal cual, sin reinterpretarla.
                    if motivos_bloqueo:
                        raise _error_navegacion_bloqueada(url, motivos_bloqueo[0]) from exc
                    raise

                if motivos_bloqueo:
                    raise _error_navegacion_bloqueada(url, motivos_bloqueo[0])

                # (c) Defensa en profundidad: revalida también la URL final (`page.url`) y
                # toda la cadena `response.request.redirected_from` — por si algún salto no
                # calificó como "navigation request" ante el handler de arriba.
                for candidata in _cadena_de_redirects(page.url, response):
                    motivo = await _validar_navegacion(candidata, self._settings)
                    if motivo is not None:
                        raise _error_navegacion_bloqueada(candidata, motivo)

                html = await page.content()
                status = response.status if response is not None else 0
                url_final = page.url
            finally:
                await browser.close()
        html = html[: self._max_bytes]
        return FetchedPage(
            url_final=url_final,
            status=status,
            content_type="text/html",
            html=html,
            text=None,
            bytes_len=len(html.encode("utf-8", errors="ignore")),
        )


def get_fetcher(settings: Any = None) -> PageFetcher:
    """Elige el `PageFetcher` según `settings.BROWSER_FETCH_PROVIDER`
    (`httpx` por defecto — igual patrón que `edecan_toolkit.research.get_search_provider`).
    """
    proveedor = str(_settings_valor(settings, "BROWSER_FETCH_PROVIDER", "httpx")).strip().lower()
    if proveedor == "playwright":
        return PlaywrightFetcher(settings)
    if proveedor != "httpx":
        logger.warning("BROWSER_FETCH_PROVIDER=%r desconocido; usando 'httpx'.", proveedor)
    return HttpxFetcher(settings)
