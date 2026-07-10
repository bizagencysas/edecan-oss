"""Verifica que la superficie pública nueva de `edecan_docanalysis` (WP-V6-06, "Pantalla
Analista" — `docs/analista.md`) existe y es importable desde la RAÍZ del paquete, con las
firmas que espera consumir `apps/api/edecan_api/routers/analista.py`.

Los tests DE COMPORTAMIENTO de cada función viven junto a su módulo (`test_s3.py`,
`test_tablas.py`, `test_graficos.py`, `test_forecast.py`) — este archivo solo confirma que el
`__init__.py` del paquete re-exporta cada nombre pinned y que las 8 tools de chat existentes
(ROADMAP_V2.md §7.7 + WP-V3-14 + WP-V5-12) siguen intactas junto a la superficie nueva.
"""

from __future__ import annotations

import inspect

import edecan_docanalysis


def test_exports_de_descarga_s3():
    assert hasattr(edecan_docanalysis, "descargar_archivo_de_tenant")
    assert callable(edecan_docanalysis.descargar_archivo_de_tenant)
    assert inspect.iscoroutinefunction(edecan_docanalysis.descargar_archivo_de_tenant)
    firma = inspect.signature(edecan_docanalysis.descargar_archivo_de_tenant)
    assert list(firma.parameters) == ["session", "settings", "tenant_id", "file_id"]

    assert hasattr(edecan_docanalysis, "ArchivoDescargado")


def test_exports_de_analisis_de_tabla():
    assert callable(edecan_docanalysis.analizar_tabla_bytes)
    assert not inspect.iscoroutinefunction(edecan_docanalysis.analizar_tabla_bytes)
    firma = inspect.signature(edecan_docanalysis.analizar_tabla_bytes)
    assert list(firma.parameters) == ["contenido", "mime", "filename", "hoja"]

    assert callable(edecan_docanalysis.extraer_columnas_bytes)
    assert not inspect.iscoroutinefunction(edecan_docanalysis.extraer_columnas_bytes)


def test_exports_de_forecast_ya_eran_publicos_solo_se_reexportan():
    assert callable(edecan_docanalysis.predecir)
    assert callable(edecan_docanalysis.detectar_anomalias)
    assert isinstance(edecan_docanalysis.DISCLAIMER_FORECAST, str)
    assert edecan_docanalysis.DISCLAIMER_FORECAST  # no vacío


def test_exports_de_graficos():
    assert callable(edecan_docanalysis.generar_svg)
    assert not inspect.iscoroutinefunction(edecan_docanalysis.generar_svg)
    firma = inspect.signature(edecan_docanalysis.generar_svg)
    assert list(firma.parameters) == ["tipo", "titulo", "etiquetas", "valores", "series"]


def test_all_lista_exactamente_los_nombres_publicos_nuevos_y_viejos():
    nuevos = {
        "ArchivoDescargado",
        "DISCLAIMER_FORECAST",
        "analizar_tabla_bytes",
        "descargar_archivo_de_tenant",
        "detectar_anomalias",
        "extraer_columnas_bytes",
        "generar_svg",
        "predecir",
    }
    tools_de_siempre = {
        "AnalizarImagenTool",
        "AnalizarTablaTool",
        "AnalizarVideoTool",
        "DetectarAnomaliasTool",
        "ExportarAnalisisTool",
        "ExtraerTablasPdfTool",
        "GenerarGraficoTool",
        "PredecirSerieTool",
        "get_all_tools",
    }
    assert set(edecan_docanalysis.__all__) == nuevos | tools_de_siempre
    for nombre in edecan_docanalysis.__all__:
        assert hasattr(edecan_docanalysis, nombre), f"{nombre} está en __all__ pero no existe"


def test_get_all_tools_sigue_devolviendo_las_8_tools_de_siempre():
    """La superficie nueva es aditiva — `get_all_tools()` (lo único que consume
    `ToolRegistry.load_entry_points`, ARCHITECTURE.md §10.7) no cambia."""
    nombres = [tool.name for tool in edecan_docanalysis.get_all_tools()]
    assert len(nombres) == 8
    assert "predecir_serie" in nombres
    assert "detectar_anomalias" in nombres
