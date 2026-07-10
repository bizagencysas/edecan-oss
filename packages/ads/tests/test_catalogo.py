"""`get_all_tools()` — catálogo de las 2 herramientas (nombres/flags/`dangerous`
pinned)."""

from __future__ import annotations

from edecan_ads import get_all_tools

_NOMBRES_ESPERADOS = {"ads_resumen", "ads_preparar_campana"}
_DANGEROUS_ESPERADAS = {"ads_preparar_campana"}


def test_get_all_tools_devuelve_los_2_nombres_pinned_sin_duplicados():
    tools = get_all_tools()
    nombres = [t.name for t in tools]
    assert set(nombres) == _NOMBRES_ESPERADOS
    assert len(nombres) == len(set(nombres))


def test_solo_ads_preparar_campana_es_dangerous():
    for tool in get_all_tools():
        assert tool.dangerous == (tool.name in _DANGEROUS_ESPERADAS)


def test_las_2_tools_requieren_el_flag_tools_ads():
    for tool in get_all_tools():
        assert "tools.ads" in tool.requires_flags


def test_ninguna_tool_menciona_linkedin():
    # Guardrail permanente (ARCHITECTURE.md §0.2) verificado localmente, igual que
    # `edecan_core.tools.ToolRegistry.register`.
    for tool in get_all_tools():
        haystack = f"{tool.name} {tool.description}".lower()
        assert "linkedin" not in haystack
