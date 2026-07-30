"""WP-V6-02 — pin de "sin flag de plan por diseño" desde el lado del
PAQUETE de tools (`edecan_smarthome`).

`packages/smarthome/edecan_smarthome/tools.py` ya documenta la decisión
("SIN flag de plan nuevo — no toques edecan_schemas", instrucción explícita
de WP-V3-12) y `test_tools.py` ya prueba, nombre por nombre, que las tres
tools no son `dangerous`/no tienen flags (salvo `casa_controlar`, que SÍ es
`dangerous`). Este archivo agrega la mitad complementaria específica del
barrido de paridad tool<->router de WP-V6-02:

1. Un pin GENÉRICO (`get_all_tools()`, no una tool a la vez) — si mañana se
   agrega una 4ª tool a este paquete, esta prueba la cubre automáticamente
   sin que alguien tenga que acordarse de sumar un test nuevo.
2. La mitad del ROUTER (`apps/api/edecan_api/routers/smarthome.py`, mismo
   diseño "sin flag") se verifica por separado en
   `apps/api/tests/test_v6_sweep_flags.py::test_smarthome_sin_flag_de_plan_router_y_tools_coinciden`
   — este paquete no importa `edecan_api` (mismo criterio de
   `ARCHITECTURE.md` §10.1 que ya sigue el resto de `packages/smarthome/
   tests/`), así que la mitad de acá y la mitad de allá se cruzan por
   convención documentada, no por un import compartido.
"""

from __future__ import annotations

from edecan_smarthome import get_all_tools


def test_ninguna_tool_de_smarthome_declara_requires_flags() -> None:
    tools = get_all_tools()
    assert len(tools) == 3, (
        "get_all_tools() dejó de devolver 3 tools -- si se agregó una tool "
        "nueva a propósito, confirma que también decidiste (y documentaste) "
        "si necesita o no un flag de plan antes de actualizar este número."
    )
    for tool in tools:
        assert tool.requires_flags == frozenset(), (
            f"{type(tool).__name__} ahora declara requires_flags={tool.requires_flags!r} -- "
            "eso contradice la decisión de producto documentada en el docstring de "
            "edecan_smarthome/tools.py ('SIN flag de plan nuevo'). Si el cambio fue "
            "intencional, actualiza también apps/api/edecan_api/routers/smarthome.py "
            "para que gatee el mismo flag (ver ARCHITECTURE.md, patrón de "
            "HOTFIXES_PENDIENTES.md 'usar_computadora se saltaba companion.remote_input')."
        )
