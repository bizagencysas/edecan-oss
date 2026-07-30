"""`edecan_browser` — navegador de investigación headless (`ROADMAP_V2.md`
§4 WP-V2-03, §7.7): fetch con httpx (o Playwright opcional), políticas de
seguridad de navegación (robots.txt, SSRF, bloqueo de rutas transaccionales)
y extracción de contenido legible. Jamás compra, paga ni llena formularios —
ver `docs/navegador.md` y el docstring de `edecan_browser.policy`.

`get_all_tools()` es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")` (§10.7),
declarado en `pyproject.toml` como `[project.entry-points."edecan.tools"]`.
"""

from __future__ import annotations

from edecan_core import Tool

from .extract import ExtractedPage, extract_page, render_markdown
from .fetch import FetchedPage, HttpxFetcher, PageFetcher, PlaywrightFetcher, get_fetcher
from .policy import PolicyResult, RobotsCache, check_navigation
from .tools import CompararPreciosTool, ExtraerDatosWebTool, NavegarWebTool

__all__ = [
    "CompararPreciosTool",
    "ExtractedPage",
    "ExtraerDatosWebTool",
    "FetchedPage",
    "HttpxFetcher",
    "NavegarWebTool",
    "PageFetcher",
    "PlaywrightFetcher",
    "PolicyResult",
    "RobotsCache",
    "check_navigation",
    "extract_page",
    "get_all_tools",
    "get_fetcher",
    "render_markdown",
]


def get_all_tools() -> list[Tool]:
    """Instancia las 3 herramientas del navegador (nombres exactos: `ROADMAP_V2.md` §7.7)."""
    return [NavegarWebTool(), ExtraerDatosWebTool(), CompararPreciosTool()]
