"""`edecan_advisory` — asesores informativos del agente: legal, salud y
educación (ROADMAP_V2.md §5 WP-V2-11, §7.7).

GUARDRAIL no negociable (ROADMAP_V2.md §8.3, ARCHITECTURE.md §0): estas ocho
herramientas son SIEMPRE informativas — legal no es asesoría legal, salud no
es diagnóstico ni sustituto de un profesional, educación no es una
evaluación oficial. El disclaimer correspondiente (`_disclaimers.py`) va
EMBEBIDO en el `content` de cada respuesta del camino feliz y está
verificado por tests (`tests/test_disclaimers.py::test_disclaimers_en_todas`).

`get_all_tools()` es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`
(ARCHITECTURE.md §10.7) vía `[project.entry-points."edecan.tools"]` en
`pyproject.toml`. Ninguna de las 8 herramientas es `dangerous` ni requiere un
flag de plan — son de lectura/generación informativa, nunca actúan sobre
cuentas externas del usuario (ROADMAP_V2.md §7.7 no lista ningún flag para
`edecan_advisory`).
"""

from __future__ import annotations

from edecan_core import Tool

from .educacion import TutorEvaluarTool, TutorLeccionTool
from .legal import AnalizarContratoTool, CompararContratosTool, GenerarBorradorLegalTool
from .salud import AnalizarLaboratorioTool, RegistrarSaludTool, ResumenSaludTool

__all__ = [
    "AnalizarContratoTool",
    "AnalizarLaboratorioTool",
    "CompararContratosTool",
    "GenerarBorradorLegalTool",
    "RegistrarSaludTool",
    "ResumenSaludTool",
    "TutorEvaluarTool",
    "TutorLeccionTool",
    "get_all_tools",
]


def get_all_tools() -> list[Tool]:
    """Instancia las 8 herramientas del paquete (nombres exactos: ROADMAP_V2.md §7.7)."""
    return [
        AnalizarContratoTool(),
        CompararContratosTool(),
        GenerarBorradorLegalTool(),
        RegistrarSaludTool(),
        ResumenSaludTool(),
        AnalizarLaboratorioTool(),
        TutorLeccionTool(),
        TutorEvaluarTool(),
    ]
