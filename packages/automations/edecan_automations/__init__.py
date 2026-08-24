"""`edecan_automations` — reglas disparador → acción (`ROADMAP_V2.md` §4 WP-V2-07).

`get_all_tools()` es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")` (§10.7) vía
el `[project.entry-points."edecan.tools"]` de `pyproject.toml`. `engine` y
`runner` no se re-exportan aquí a propósito (mismo criterio que
`edecan_core.memory` — ver su docstring): quien solo necesita
`get_all_tools()` no arrastra `edecan_core.agent.Agent` ni `dateutil`.
"""

from __future__ import annotations

from .tools import GestionarAutomatizacionTool, get_all_tools

__all__ = ["GestionarAutomatizacionTool", "get_all_tools"]
