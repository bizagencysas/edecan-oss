"""Design Studio de Edecán: artefactos HTML privados, seguros y versionados.

El modelo conectado a Edecán redacta el HTML mediante tool calling. Este
paquete no conoce proveedores, modelos, credenciales ni adaptadores de IA.
"""

from __future__ import annotations

from edecan_core import Tool

from .models import DesignVersion, RenderBundle
from .render import BrowserFirstRenderer, DesignRenderer
from .sanitize import HtmlValidationError, extract_html, sanitize_html
from .storage import DesignStore, S3DesignStore
from .tools import (
    CrearDisenoVisualTool,
    ExportarDisenoVisualTool,
    HistorialDisenoVisualTool,
    ObtenerDisenoVisualTool,
    RefinarDisenoVisualTool,
)

__all__ = [
    "BrowserFirstRenderer",
    "CrearDisenoVisualTool",
    "DesignRenderer",
    "DesignStore",
    "DesignVersion",
    "ExportarDisenoVisualTool",
    "HistorialDisenoVisualTool",
    "HtmlValidationError",
    "ObtenerDisenoVisualTool",
    "RefinarDisenoVisualTool",
    "RenderBundle",
    "S3DesignStore",
    "extract_html",
    "get_all_tools",
    "sanitize_html",
]


def get_all_tools() -> list[Tool]:
    """Entry point provider-neutral descubierto por ``edecan.tools``."""
    return [
        CrearDisenoVisualTool(),
        ObtenerDisenoVisualTool(),
        RefinarDisenoVisualTool(),
        HistorialDisenoVisualTool(),
        ExportarDisenoVisualTool(),
    ]
