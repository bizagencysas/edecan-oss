"""`edecan_docanalysis` — analista total de documentos del agente
(ROADMAP_V2.md §4 WP-V2-02, §7.7; WP-V3-14 suma video; WP-V5-12 suma
predicción/anomalías): estadística sobre tablas CSV/XLSX, extracción
heurística de tablas de PDF, visión sobre imágenes (OCR/descripción),
análisis de video por frames, gráficos SVG deterministas, exportación de
reportes XLSX, predicción de series (forecast) y detección de anomalías.

`get_all_tools()` es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`
(ARCHITECTURE.md §10.7) vía el `[project.entry-points."edecan.tools"]` de
`pyproject.toml`. Ninguna de las 10 herramientas es `dangerous` ni requiere un
flag de plan (ROADMAP_V2.md §7.7: "ninguna dangerous" — `analizar_video`,
`predecir_serie` y `detectar_anomalias` siguen el mismo criterio que las 5
originales) — todas son de solo-lectura/generación, nunca modifican datos del
usuario fuera de crear archivos nuevos (`files`) que él mismo puede borrar.

## Superficie pública pura (WP-V6-06, "Pantalla Analista" — `docs/analista.md`)

Además de las 10 tools de chat de arriba, este paquete expone un puñado de
funciones PURAS (sin `ToolContext`, sin LLM) para consumidores fuera del loop
de un agente — hoy, el único consumidor real es el router HTTP de solo
lectura `apps/api/edecan_api/routers/analista.py`, que necesita la MISMA
lógica de análisis que ya usan las tools de chat pero sin pasar por
`Agent.run_turn`:

- `descargar_archivo_de_tenant(session, settings, tenant_id, file_id)` (`_s3.py`) — misma
  descarga S3 tenant-scoped que usan las tools, con `session`/`settings` explícitos en vez de
  un `ToolContext`.
- `extraer_columnas_bytes(contenido, *, mime=, filename=, hoja=)` / `analizar_tabla_bytes(...)`
  (`tablas.py`) — filas crudas y estadística/outliers de un CSV/XLSX ya descargado (mismos
  helpers que `AnalizarTablaTool`, sin la parte de `pregunta`/LLM).
- `predecir` / `detectar_anomalias` / `DISCLAIMER_FORECAST` (`forecast.py`) — YA eran
  funciones públicas (sin `_`) que sostienen `PredecirSerieTool`/`DetectarAnomaliasTool`; este
  `__init__.py` solo las re-exporta a nivel de paquete.
- `generar_svg(tipo, titulo, etiquetas, *, valores=, series=)` (`graficos.py`) — mismo render
  determinista que `GenerarGraficoTool`, sin subir el resultado a S3.

Ninguna cambia la lógica de las tools existentes (`AnalizarTablaTool`/`GenerarGraficoTool`
siguen exactamente igual) — son composiciones nuevas de los mismos helpers privados, pensadas
para no duplicar el análisis en el router.
"""

from __future__ import annotations

from edecan_core import Tool

from ._s3 import ArchivoDescargado, descargar_archivo_de_tenant
from .archivos import EditarPdfTool, LeerArchivoTool
from .forecast import (
    DISCLAIMER_FORECAST,
    DetectarAnomaliasTool,
    PredecirSerieTool,
    detectar_anomalias,
    predecir,
)
from .graficos import GenerarGraficoTool, generar_svg
from .pdf import ExtraerTablasPdfTool
from .reportes import ExportarAnalisisTool
from .tablas import AnalizarTablaTool, analizar_tabla_bytes, extraer_columnas_bytes
from .video import AnalizarVideoTool
from .vision import AnalizarImagenTool

__all__ = [
    "AnalizarImagenTool",
    "AnalizarTablaTool",
    "AnalizarVideoTool",
    "ArchivoDescargado",
    "DISCLAIMER_FORECAST",
    "DetectarAnomaliasTool",
    "EditarPdfTool",
    "ExportarAnalisisTool",
    "ExtraerTablasPdfTool",
    "GenerarGraficoTool",
    "LeerArchivoTool",
    "PredecirSerieTool",
    "analizar_tabla_bytes",
    "descargar_archivo_de_tenant",
    "detectar_anomalias",
    "extraer_columnas_bytes",
    "generar_svg",
    "get_all_tools",
    "predecir",
]


def get_all_tools() -> list[Tool]:
    """Instancia las 10 herramientas del paquete (nombres exactos: ROADMAP_V2.md
    §7.7 para las 5 originales; `analizar_video` es WP-V3-14; `predecir_serie`/
    `detectar_anomalias` son WP-V5-12)."""
    return [
        LeerArchivoTool(),
        EditarPdfTool(),
        AnalizarTablaTool(),
        ExtraerTablasPdfTool(),
        AnalizarImagenTool(),
        AnalizarVideoTool(),
        GenerarGraficoTool(),
        ExportarAnalisisTool(),
        PredecirSerieTool(),
        DetectarAnomaliasTool(),
    ]
