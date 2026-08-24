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
from .redaccion import CrearPostLinkedInTool
from .social import (
    ConfigurarPerfilSocialTool,
    CrearContenidoSocialTool,
    InvestigarNoticiasFrescasTool,
    PlanificarContenidoSocialTool,
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
    "CrearContenidoSocialTool",
    "ConfigurarPerfilSocialTool",
    "CrearPdfTool",
    "CrearPodcastTool",
    "CrearPostLinkedInTool",
    "CrearPresentacionTool",
    "GenerarEfectoSonidoTool",
    "GenerarImagenTool",
    "ImageProvider",
    "InvestigarNoticiasFrescasTool",
    "OpenAICompatImagesProvider",
    "PlanificarContenidoSocialTool",
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
    """Instancia las herramientas de creatividad: imágenes, contenido social y documentos
    de oficina (nombres exactos: `ROADMAP_V2.md` §7.7) más las 2 de podcasts
    y efectos de sonido (`ARCHITECTURE.md` §14, WP-V5-11) — ambas gateadas
    por el flag de plan `tools.podcast`, ver `edecan_creative.tools`.

    `PlanificarContenidoSocialTool`/`InvestigarNoticiasFrescasTool` completan la cadena
    editorial de `crear_contenido_social` (rotación de territorios/formatos y noticias
    frescas con fecha verificada, `edecan_creative.agenda`/`investigacion`) para contenido
    social recurrente; ver el docstring de cada una. `CrearPostLinkedInTool` corre esa misma
    cadena entera en código, en una sola llamada (`edecan_creative.redaccion`): va antes de
    `crear_contenido_social` en la lista porque es la que debe usarse cuando hay que escribir
    el post, y no solo empaquetar uno ya dictado."""
    return [
        GenerarImagenTool(),
        ConfigurarPerfilSocialTool(),
        PlanificarContenidoSocialTool(),
        InvestigarNoticiasFrescasTool(),
        CrearPostLinkedInTool(),
        CrearContenidoSocialTool(),
        CrearDocumentoTool(),
        CrearPresentacionTool(),
        CrearPdfTool(),
        CrearPodcastTool(),
        GenerarEfectoSonidoTool(),
    ]
