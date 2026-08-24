"""Nombres y schemas de las herramientas del paquete: las 5 pinned en
ROADMAP_V2.md §7.7 más `analizar_video` (WP-V3-14, v3 — no reabre el pinned
v2, ver ARCHITECTURE.md §12) más `predecir_serie`/`detectar_anomalias`
(WP-V5-12, v5 — nombres exactos dados en el enunciado del propio WP; al
momento de escribir este test `ARCHITECTURE.md` todavía no tenía una sección
§14 con la tabla de herramientas v5, ver docstring de `forecast.py`)."""

from __future__ import annotations

from edecan_docanalysis import get_all_tools

NOMBRES_PINNED = [
    "leer_archivo",
    "editar_pdf",
    "analizar_tabla",
    "extraer_tablas_pdf",
    "analizar_imagen",
    "analizar_video",
    "generar_grafico",
    "exportar_analisis",
    "predecir_serie",
    "detectar_anomalias",
]


def test_get_all_tools_devuelve_las_10_herramientas_con_los_nombres_pinned():
    nombres = [tool.name for tool in get_all_tools()]
    assert nombres == NOMBRES_PINNED
    assert len(nombres) == 10
    assert len(set(nombres)) == 10  # sin duplicados


def test_cada_tool_tiene_name_description_e_input_schema_validos():
    for tool in get_all_tools():
        assert isinstance(tool.name, str) and tool.name
        assert isinstance(tool.description, str) and tool.description
        assert isinstance(tool.input_schema, dict)
        assert tool.input_schema.get("type") == "object"
        assert isinstance(tool.input_schema.get("properties"), dict)
        assert "linkedin" not in tool.name.lower()
        assert "linkedin" not in tool.description.lower()


def test_ninguna_tool_es_dangerous_ni_requiere_flags():
    # ROADMAP_V2.md §7.7: "ninguna dangerous" para las 5 originales;
    # `analizar_video` (WP-V3-14) sigue el mismo criterio — §7.2 tampoco lista
    # un flag para `edecan_docanalysis` — las 6 tools están siempre disponibles.
    for tool in get_all_tools():
        assert tool.dangerous is False
        assert tool.requires_flags == frozenset()


def test_required_de_cada_schema_son_propiedades_declaradas():
    for tool in get_all_tools():
        propiedades = set(tool.input_schema.get("properties", {}))
        requeridos = set(tool.input_schema.get("required", []))
        assert requeridos <= propiedades, f"{tool.name}: 'required' con claves no declaradas"


def test_todas_requieren_file_id_salvo_analizar_video_generar_grafico_y_exportar_analisis():
    # `analizar_video` también resuelve un archivo ya subido, pero con la
    # clave `archivo` en vez de `file_id` (mismo mecanismo de lookup/descarga
    # S3 que las demás, nombre de propiedad distinto — ver docstring de
    # `edecan_docanalysis.video`).
    por_nombre = {tool.name: tool for tool in get_all_tools()}
    con_file_id = {
        "analizar_tabla",
        "editar_pdf",
        "extraer_tablas_pdf",
        "leer_archivo",
        "analizar_imagen",
    }
    for nombre in con_file_id:
        assert "file_id" in por_nombre[nombre].input_schema["required"]
        assert "archivo" not in por_nombre[nombre].input_schema["properties"]

    assert por_nombre["analizar_video"].input_schema["required"] == ["archivo"]
    assert "file_id" not in por_nombre["analizar_video"].input_schema["properties"]

    # predecir_serie/detectar_anomalias (WP-V5-12) tampoco leen un archivo: los
    # datos llegan completos en 'valores', igual que generar_grafico/exportar_analisis.
    sin_archivo = {"generar_grafico", "exportar_analisis", "predecir_serie", "detectar_anomalias"}
    for nombre in sin_archivo:
        assert "file_id" not in por_nombre[nombre].input_schema["properties"]
        assert "archivo" not in por_nombre[nombre].input_schema["properties"]
