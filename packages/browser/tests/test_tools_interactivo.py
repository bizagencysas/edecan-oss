"""Tests de `navegar_web_interactivo` (`edecan_browser.tools`, product design).

La navegación interactiva exige Playwright (extra opcional, NO instalado en el
entorno de tests) — igual que `test_fetch_playwright_policy.py`, estos tests no
instancian Chromium real: prueban el contrato (metadata `dangerous`/flags),
la validación de argumentos (`_validar_args_accion`, pura), el rechazo de
policy ANTES de tocar Playwright y el camino honesto "sin Playwright → error
claro, nunca un éxito falso" (`AGENTS.md` §13.1).
"""

from __future__ import annotations

import sys

import httpx
import respx
from edecan_browser import NavegarWebInteractivoTool, get_all_tools
from edecan_browser.tools import _validar_args_accion


def test_interactivo_es_dangerous_y_requiere_flag_browser():
    tool = NavegarWebInteractivoTool()
    assert tool.dangerous is True
    assert tool.requires_flags == frozenset({"tools.browser"})
    assert tool.risk_level == "high"
    assert tool.category == "browser"


def test_get_all_tools_incluye_navegar_web_interactivo():
    nombres = [t.name for t in get_all_tools()]
    assert "navegar_web_interactivo" in nombres
    # Las 3 de solo lectura siguen estando.
    assert {"navegar_web", "extraer_datos_web", "comparar_precios"} <= set(nombres)


async def test_interactivo_sin_url(make_ctx):
    resultado = await NavegarWebInteractivoTool().run(
        make_ctx(), {"url": "   ", "accion": "click"}
    )
    assert "url" in resultado.content.lower()


async def test_interactivo_accion_no_soportada(make_ctx):
    resultado = await NavegarWebInteractivoTool().run(
        make_ctx(), {"url": "https://ejemplo.com", "accion": "volar"}
    )
    assert "no soportada" in resultado.content.lower()


# --- `_validar_args_accion` (pura, sin Playwright) -------------------------


def test_validar_args_click_exige_selector():
    assert _validar_args_accion("click", selector="", texto="", opcion="") is not None
    assert _validar_args_accion("click", selector="#x", texto="", opcion="") is None


def test_validar_args_type_exige_selector_y_texto():
    assert _validar_args_accion("type", selector="", texto="hola", opcion="") is not None
    assert _validar_args_accion("type", selector="#x", texto="", opcion="") is not None
    assert _validar_args_accion("type", selector="#x", texto="hola", opcion="") is None


def test_validar_args_select_exige_selector_y_opcion():
    assert _validar_args_accion("select", selector="", texto="", opcion="a") is not None
    assert _validar_args_accion("select", selector="#s", texto="", opcion="") is not None
    assert _validar_args_accion("select", selector="#s", texto="", opcion="a") is None


def test_validar_args_search_page_exige_selector_o_texto():
    assert _validar_args_accion("search_page", selector="", texto="", opcion="") is not None
    assert _validar_args_accion("search_page", selector="#x", texto="", opcion="") is None
    assert _validar_args_accion("search_page", selector="", texto="hola", opcion="") is None


def test_validar_args_screenshot_y_scroll_sin_requisitos():
    assert _validar_args_accion("screenshot", selector="", texto="", opcion="") is None
    assert _validar_args_accion("scroll", selector="", texto="", opcion="") is None


# --- rechazo de policy ANTES de tocar Playwright ---------------------------


@respx.mock
async def test_interactivo_url_de_checkout_se_rechaza_antes_de_playwright(
    make_ctx, fake_settings
):
    # Sin ninguna ruta registrada en respx y sin Playwright instalado: si la tool
    # intentara navegar o levantar Chromium pese al rechazo de policy, este test
    # fallaría con un error de respx/ImportError en vez de devolver el motivo.
    ctx = make_ctx(settings=fake_settings())
    resultado = await NavegarWebInteractivoTool().run(
        ctx,
        {
            "url": "https://tienda.ejemplo.com/checkout/pagar",
            "accion": "click",
            "selector": "#pagar",
        },
    )
    assert resultado.data is None
    assert "compra" in resultado.content.lower() or "pago" in resultado.content.lower()


@respx.mock
async def test_interactivo_url_ssrf_se_rechaza_antes_de_playwright(make_ctx, fake_settings):
    ctx = make_ctx(settings=fake_settings())
    resultado = await NavegarWebInteractivoTool().run(
        ctx, {"url": "http://127.0.0.1:9000/interno", "accion": "screenshot"}
    )
    assert resultado.data is None
    assert "SSRF" in resultado.content


# --- sin Playwright → error claro, nunca éxito falso -----------------------


@respx.mock
async def test_interactivo_sin_playwright_devuelve_error_claro_no_exito(
    make_ctx, fake_settings, monkeypatch
):
    # Forza la ausencia de Playwright sin depender del entorno (Playwright puede
    # estar instalado, p. ej. para el smoke test real): `sys.modules["playwright"]
    # = None` hace que `from playwright.async_api import ...` lance `ImportError`.
    monkeypatch.setitem(sys.modules, "playwright", None)
    respx.get("https://tienda.ejemplo.com/robots.txt").mock(return_value=httpx.Response(404))
    ctx = make_ctx(settings=fake_settings())

    resultado = await NavegarWebInteractivoTool().run(
        ctx, {"url": "https://tienda.ejemplo.com/producto/1", "accion": "screenshot"}
    )

    assert resultado.data is None
    assert "playwright" in resultado.content.lower()
