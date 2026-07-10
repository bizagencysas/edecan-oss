"""Tests del entry point `edecan_advisory:get_all_tools` (`pyproject.toml`
`[project.entry-points."edecan.tools"]`, ARCHITECTURE.md §10.7): confirma que
las 8 herramientas pinned en ROADMAP_V2.md §7.7 están todas presentes, con
los nombres exactos, y que ninguna es `dangerous` ni requiere un flag de
plan (tal como documenta el propio §7.7 para `edecan_advisory`)."""

from __future__ import annotations

from edecan_advisory import get_all_tools

_NOMBRES_PINNED = (
    "analizar_contrato",
    "comparar_contratos",
    "generar_borrador_legal",
    "registrar_salud",
    "resumen_salud",
    "analizar_laboratorio",
    "tutor_leccion",
    "tutor_evaluar",
)


def test_get_all_tools_devuelve_las_8_con_nombres_exactos():
    herramientas = get_all_tools()
    assert [t.name for t in herramientas] == list(_NOMBRES_PINNED)


def test_get_all_tools_ninguna_es_dangerous_ni_requiere_flag():
    for herramienta in get_all_tools():
        assert herramienta.dangerous is False, herramienta.name
        assert herramienta.requires_flags == frozenset(), herramienta.name


def test_get_all_tools_cada_una_tiene_description_e_input_schema():
    for herramienta in get_all_tools():
        assert herramienta.description.strip()
        assert herramienta.input_schema.get("type") == "object"
        assert "properties" in herramienta.input_schema
