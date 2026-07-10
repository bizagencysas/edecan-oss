"""`edecan_vehicles` — esqueleto v4 (`ARCHITECTURE.md` §13, dueño WP-V4-01).

Este paquete ya vive en `[tool.uv.workspace].members` del `pyproject.toml`
raíz para que `uv sync`/`uv lock` nunca se rompan mientras WP-V4-08 (dueño
real de las herramientas de vehículos) aterriza `tools.py` en paralelo —
mismo criterio que dejaron WP-V2-01/WP-V3-01 para sus propios paquetes
nuevos (ver `ARCHITECTURE.md` §11/§12h). `get_all_tools()` es el entry point
que `edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`
resuelve (`pyproject.toml`, `[project.entry-points."edecan.tools"]`):
mientras `.tools` no exista todavía, cae a una lista vacía en vez de
reventar el import de este paquete (y de quien dependa de él). WP-V4-08
NUNCA debe editar este archivo — solo agregar `tools.py` (y lo que haga
falta) junto a él.
"""

try:
    from .tools import get_all_tools
except ImportError:  # el módulo de tools lo aporta WP-V4-07/08 en paralelo
    def get_all_tools():
        return []
