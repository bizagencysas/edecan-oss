"""`edecan_creative` — creatividad: generación de imágenes y documentos de oficina
(`ARCHITECTURE.md` §10.14; `ROADMAP_V2.md` §7.7).

`get_all_tools()` es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")` (§10.7),
declarado en `pyproject.toml` como `[project.entry-points."edecan.tools"]`.
"""

from __future__ import annotations

from edecan_core import Tool

from ._files import Uploader, subir_archivo
from .podcast import SegmentoPodcast, validar_guion
from .providers import (
    DEFAULT_SIZE,
    IMAGES_CONNECTOR_KEY,
    ImageProvider,
    OpenAICompatImagesProvider,
    StubImageProvider,
    get_image_provider,
    get_tenant_image_provider,
)
from .tools import (
    CrearDocumentoTool,
    CrearPdfTool,
    CrearPodcastTool,
    CrearPresentacionTool,
    GenerarEfectoSonidoTool,
    GenerarImagenTool,
)

__all__ = [
    "DEFAULT_SIZE",
    "IMAGES_CONNECTOR_KEY",
    "CrearDocumentoTool",
    "CrearPdfTool",
    "CrearPodcastTool",
    "CrearPresentacionTool",
    "GenerarEfectoSonidoTool",
    "GenerarImagenTool",
    "ImageProvider",
    "OpenAICompatImagesProvider",
    "SegmentoPodcast",
    "StubImageProvider",
    "Uploader",
    "get_all_tools",
    "get_image_provider",
    "get_tenant_image_provider",
    "subir_archivo",
    "validar_guion",
]


def get_all_tools() -> list[Tool]:
    """Instancia las herramientas de creatividad: las 4 de imágenes/documentos
    de oficina (nombres exactos: `ROADMAP_V2.md` §7.7) más las 2 de podcasts
    y efectos de sonido (`ARCHITECTURE.md` §14, WP-V5-11) — ambas gateadas
    por el flag de plan `tools.podcast`, ver `edecan_creative.tools`."""
    return [
        GenerarImagenTool(),
        CrearDocumentoTool(),
        CrearPresentacionTool(),
        CrearPdfTool(),
        CrearPodcastTool(),
        GenerarEfectoSonidoTool(),
    ]
