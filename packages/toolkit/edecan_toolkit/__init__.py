"""`edecan_toolkit` — herramientas de dominio no premium del agente
(`ARCHITECTURE.md` §10.14): recordatorios, agenda, correo, contactos/CRM,
finanzas personales, documentos, investigación web, contenido y computadora.

`get_all_tools()` es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")` (§10.7) vía
el `[project.entry-points."edecan.tools"]` de `pyproject.toml`.
"""

from __future__ import annotations

from edecan_core import Tool

from .agenda import AgendaEventosTool, CrearEventoTool
from .autoconfiguracion import ConfigurarCredencialTool
from .autorreparacion import (
    DiagnosticarAutorreparacionLocalTool,
    GestionarAutorreparacionLocalTool,
)
from .codigo_local import AccederCodigoLocalTool
from .computadora import UsarComputadoraTool
from .contactos import BuscarContactosTool, GestionarContactoTool
from .contenido import GenerarContenidoTool, PublicarSocialTool
from .correo import BuscarCorreoTool, EnviarCorreoTool
from .documentos import ConsultarDocumentosTool
from .finanzas import RegistrarTransaccionTool, ResumenFinanzasTool
from .recordatorios import CrearRecordatorioTool, ListarRecordatoriosTool
from .research import (
    SEARCH_CONNECTOR_KEY,
    BuscarWebTool,
    SearchHit,
    SearchProvider,
    get_search_provider,
    get_tenant_search_provider,
)
from .utilidades import CalculadoraTool, HoraActualTool

__all__ = [
    "SEARCH_CONNECTOR_KEY",
    "AccederCodigoLocalTool",
    "AgendaEventosTool",
    "DiagnosticarAutorreparacionLocalTool",
    "BuscarContactosTool",
    "BuscarCorreoTool",
    "BuscarWebTool",
    "CalculadoraTool",
    "ConfigurarCredencialTool",
    "ConsultarDocumentosTool",
    "CrearEventoTool",
    "CrearRecordatorioTool",
    "EnviarCorreoTool",
    "GenerarContenidoTool",
    "GestionarContactoTool",
    "GestionarAutorreparacionLocalTool",
    "HoraActualTool",
    "ListarRecordatoriosTool",
    "PublicarSocialTool",
    "RegistrarTransaccionTool",
    "ResumenFinanzasTool",
    "SearchHit",
    "SearchProvider",
    "UsarComputadoraTool",
    "get_all_tools",
    "get_search_provider",
    "get_tenant_search_provider",
]


def get_all_tools() -> list[Tool]:
    """Instancia las herramientas del toolkit registradas para el agente."""
    return [
        CrearRecordatorioTool(),
        ListarRecordatoriosTool(),
        AgendaEventosTool(),
        CrearEventoTool(),
        BuscarCorreoTool(),
        EnviarCorreoTool(),
        BuscarContactosTool(),
        GestionarContactoTool(),
        RegistrarTransaccionTool(),
        ResumenFinanzasTool(),
        ConsultarDocumentosTool(),
        BuscarWebTool(),
        GenerarContenidoTool(),
        PublicarSocialTool(),
        UsarComputadoraTool(),
        HoraActualTool(),
        CalculadoraTool(),
        ConfigurarCredencialTool(),
        AccederCodigoLocalTool(),
        DiagnosticarAutorreparacionLocalTool(),
        GestionarAutorreparacionLocalTool(),
    ]
