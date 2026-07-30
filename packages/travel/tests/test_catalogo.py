"""`get_all_tools()` — catálogo de las 5 herramientas de viajes (nombres/flags/`dangerous`
pinned, `ARCHITECTURE.md` §14)."""

from __future__ import annotations

from edecan_travel import get_all_tools as get_all_tools_publico
from edecan_travel.tools import get_all_tools

_NOMBRES_ESPERADOS = {
    "buscar_vuelos",
    "buscar_hoteles",
    "estado_vuelo",
    "rastrear_paquete",
    "preparar_reserva",
}
_DANGEROUS_ESPERADAS = {"preparar_reserva"}


def test_get_all_tools_devuelve_los_5_nombres_pinned_sin_duplicados():
    tools = get_all_tools()
    nombres = [t.name for t in tools]
    assert set(nombres) == _NOMBRES_ESPERADOS
    assert len(nombres) == len(set(nombres))


def test_entry_point_publico_coincide_con_tools_get_all_tools():
    # `edecan_travel.__init__.get_all_tools` es el que resuelve el entry point
    # `edecan.tools` (pyproject.toml) — debe ser el mismo catálogo.
    nombres_publico = {t.name for t in get_all_tools_publico()}
    assert nombres_publico == _NOMBRES_ESPERADOS


def test_solo_preparar_reserva_es_dangerous():
    for tool in get_all_tools():
        assert tool.dangerous == (tool.name in _DANGEROUS_ESPERADAS)


def test_las_5_tools_requieren_el_flag_tools_travel():
    for tool in get_all_tools():
        assert "tools.travel" in tool.requires_flags


def test_ninguna_tool_menciona_linkedin():
    # Guardrail permanente (ARCHITECTURE.md §0.2) verificado localmente, igual que
    # `edecan_core.tools.ToolRegistry.register`.
    for tool in get_all_tools():
        haystack = f"{tool.name} {tool.description}".lower()
        assert "linkedin" not in haystack


def test_ninguna_tool_menciona_vehiculos():
    # Fuera de alcance explícito (`DIRECCION_ACTUAL.md`) — no debería colarse nada de
    # vehículos en un paquete nuevo sin relación.
    for tool in get_all_tools():
        haystack = f"{tool.name} {tool.description}".lower()
        assert "vehicul" not in haystack
