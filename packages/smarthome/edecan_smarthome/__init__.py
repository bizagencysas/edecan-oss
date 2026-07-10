"""`edecan_smarthome` — integración con Home Assistant (API REST, instancia
propia del cliente), ver `ARCHITECTURE.md` §12 y `DIRECCION_ACTUAL.md`
(WP-V3-12).

Un solo conector (Home Assistant, bring-your-own Long-Lived Access Token) da
acceso a cientos de integraciones de casa inteligente: luces, enchufes,
clima, sensores, cámaras, TVs... — ver `docs/casa-inteligente.md`. Expone 3
herramientas del agente vía el entry point `edecan.tools`
(`ARCHITECTURE.md` §10.7, `pyproject.toml`): `casa_dispositivos`/
`casa_estado` (solo lectura) y `casa_controlar` (`dangerous=True`, exige
confirmación humana antes de ejecutar; nunca controla cerraduras — ver
`edecan_smarthome.tools.DOMINIOS_BLOQUEADOS`).

`client.HomeAssistantClient` es el cliente REST puro; `tools.py` las
herramientas del agente que lo usan.
"""

from __future__ import annotations

from edecan_core import Tool

from .tools import CasaControlarTool, CasaDispositivosTool, CasaEstadoTool

__all__ = ["get_all_tools"]


def get_all_tools() -> list[Tool]:
    return [CasaDispositivosTool(), CasaEstadoTool(), CasaControlarTool()]
