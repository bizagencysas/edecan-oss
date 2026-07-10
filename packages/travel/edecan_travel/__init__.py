"""`edecan_travel` — viajes: búsqueda de vuelos/hoteles vía Amadeus Self-Service
(bring-your-own) + rastreo de paquetes vía AfterShip (`ARCHITECTURE.md` §14, WP-V5-09).

Este paquete debe estar en `[tool.uv.workspace].members` del `pyproject.toml` raíz
(dueño WP-V5-01, mismo criterio que dejaron WP-V2-01/WP-V3-01/WP-V4-01 para sus propios
paquetes nuevos — ver `ARCHITECTURE.md` §11/§12.h/§13.h) para que `uv sync`/`uv lock`
nunca se rompan. `get_all_tools()` es el entry point que
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")` resuelve
(`pyproject.toml`, `[project.entry-points."edecan.tools"]`).

A diferencia de `edecan_ads`/`edecan_vehicles` (WP-V4-01 dejó esos dos como esqueletos
vacíos para que WP-V4-07/WP-V4-08 los completaran en paralelo, ver `ARCHITECTURE.md`
§13.h), este WP (WP-V5-09) entrega `tools.py` completo desde el primer commit del
paquete — el `try/except ImportError` de abajo queda igual, por pura consistencia
defensiva con el resto del repo (nunca revienta el import de quien dependa de este
paquete si algo en `.tools` fallara al cargar).
"""

try:
    from .tools import get_all_tools
except ImportError:  # pragma: no cover - guardia defensiva, ver docstring del módulo

    def get_all_tools():
        return []
