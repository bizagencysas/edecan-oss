"""Smoke test REAL de `navegar_web_interactivo` contra Chromium/Playwright.

A diferencia del resto de `edecan_browser` (que jamás instancia Playwright real,
ver `test_tools_interactivo.py` y `test_fetch_playwright_policy.py`), este
archivo SÍ intenta levantar un Chromium de verdad y abrir una página. Es un
smoke honesto, no un test del contrato: si Playwright o el binario de Chromium
no están disponibles, todo el módulo se SALTEA con `pytest.mark.skipif` (nunca
se finge un éxito, `AGENTS.md` §13.1).

Instalación para correrlo:

    pip install playwright
    playwright install chromium

(En este repo con `uv`: `uv pip install 'edecan-browser[playwright]'` y luego
`playwright install chromium` — ver `docs/navegador.md`.)
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import pytest
from edecan_browser import NavegarWebInteractivoTool

try:
    import playwright  # noqa: F401

    _PLAYWRIGHT_PKG = True
except ImportError:
    _PLAYWRIGHT_PKG = False


def _ruta_cache_playwright() -> Path:
    desde_env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if desde_env:
        return Path(desde_env)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _chromium_instalado() -> bool:
    ruta = _ruta_cache_playwright()
    if not ruta.is_dir():
        return False
    return any(entrada.name.startswith("chromium") for entrada in ruta.iterdir())


_DISPONIBLE = _PLAYWRIGHT_PKG and _chromium_instalado()

pytestmark = pytest.mark.skipif(
    not _DISPONIBLE,
    reason=(
        "Playwright/Chromium no disponible (paquete: "
        f"{'sí' if _PLAYWRIGHT_PKG else 'no'}, binario: "
        f"{'sí' if _chromium_instalado() else 'no'}). Instala con "
        "`pip install playwright && playwright install chromium`."
    ),
)


async def test_smoke_navegar_web_interactivo_screenshot_real(make_ctx):
    """Abre una página simple y pide un `screenshot` de verdad.

    La verificación sigue la Ley de la Sección 5 de AGENTS.md: no basta con
    «no vacío» — se decodifica el PNG y se comprueba su firma de bytes real.
    """
    resultado = await NavegarWebInteractivoTool().run(
        make_ctx(), {"url": "https://example.com", "accion": "screenshot"}
    )

    assert resultado.data is not None, resultado.content
    b64 = resultado.data.get("screenshot_b64")
    assert b64, resultado.content

    png = base64.b64decode(b64)
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "la captura no es un PNG válido"
    assert len(png) > 100, "la captura está vacía (0 bytes o trivial)"
