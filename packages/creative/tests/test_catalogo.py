"""Nombres y schemas de las herramientas de `edecan_creative`: las 4 de
`ROADMAP_V2.md` §7.7 (imágenes/documentos de oficina) más las 2 de podcasts
y efectos de sonido de `ARCHITECTURE.md` §14 (WP-V5-11)."""

from __future__ import annotations

from edecan_creative import get_all_tools

NOMBRES_PINNED = [
    "generar_imagen",
    "crear_contenido_social",
    "crear_documento",
    "crear_presentacion",
    "crear_pdf",
    "crear_podcast",
    "generar_efecto_sonido",
]


def test_get_all_tools_devuelve_las_herramientas_con_los_nombres_pinned():
    nombres = [tool.name for tool in get_all_tools()]
    assert nombres == NOMBRES_PINNED
    assert len(set(nombres)) == len(NOMBRES_PINNED)  # sin duplicados


def test_cada_tool_tiene_name_description_e_input_schema_validos():
    for tool in get_all_tools():
        assert isinstance(tool.name, str) and tool.name
        assert isinstance(tool.description, str) and tool.description
        assert isinstance(tool.input_schema, dict)
        assert tool.input_schema.get("type") == "object"
        assert isinstance(tool.input_schema.get("properties"), dict)


def test_ninguna_tool_es_dangerous():
    assert all(tool.dangerous is False for tool in get_all_tools())


def test_requires_flags_por_tool():
    por_nombre = {tool.name: tool for tool in get_all_tools()}
    assert por_nombre["generar_imagen"].requires_flags == frozenset({"tools.images"})
    assert por_nombre["crear_podcast"].requires_flags == frozenset({"tools.podcast"})
    assert por_nombre["generar_efecto_sonido"].requires_flags == frozenset({"tools.podcast"})
    for nombre in ("crear_contenido_social", "crear_documento", "crear_presentacion", "crear_pdf"):
        assert por_nombre[nombre].requires_flags == frozenset()


def test_required_de_cada_schema_son_propiedades_declaradas():
    for tool in get_all_tools():
        propiedades = set(tool.input_schema.get("properties", {}))
        requeridos = set(tool.input_schema.get("required", []))
        assert requeridos <= propiedades, f"{tool.name}: 'required' con claves no declaradas"


def test_get_all_tools_construye_instancias_nuevas_cada_vez():
    # `ToolRegistry.load_entry_points` llama `factory()` una vez por proceso,
    # pero nada impide llamarlo más de una vez (p. ej. tests) — no debe haber
    # estado compartido mutable entre instancias.
    primera = {tool.name: tool for tool in get_all_tools()}
    segunda = {tool.name: tool for tool in get_all_tools()}
    assert primera["crear_pdf"] is not segunda["crear_pdf"]
