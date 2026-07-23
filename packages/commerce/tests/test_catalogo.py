"""Catálogo de herramientas financieras, incluido Alpaca Paper."""

from __future__ import annotations

from edecan_commerce import get_all_tools
from edecan_schemas.plans import FLAG_COMMERCE_ORDERS

_NOMBRES_ESPERADOS = {
    "cotizar_activo",
    "consultar_alpaca",
    "gestionar_presupuesto",
    "preparar_pago",
    "preparar_orden",
}
_DANGEROUS_ESPERADAS = {"preparar_pago", "preparar_orden"}


def test_get_all_tools_devuelve_los_nombres_pinned_sin_duplicados():
    tools = get_all_tools()
    nombres = [t.name for t in tools]
    assert set(nombres) == _NOMBRES_ESPERADOS
    assert len(nombres) == len(set(nombres))


def test_solo_preparar_pago_y_preparar_orden_son_dangerous():
    for tool in get_all_tools():
        assert tool.dangerous == (tool.name in _DANGEROUS_ESPERADAS)


def test_las_tools_requieren_el_flag_commerce_orders():
    for tool in get_all_tools():
        assert FLAG_COMMERCE_ORDERS in tool.requires_flags


def test_ninguna_tool_menciona_linkedin():
    # Guardrail permanente (ARCHITECTURE.md §0.2) verificado localmente, igual que
    # `edecan_core.tools.ToolRegistry.register`.
    for tool in get_all_tools():
        haystack = f"{tool.name} {tool.description}".lower()
        assert "linkedin" not in haystack
