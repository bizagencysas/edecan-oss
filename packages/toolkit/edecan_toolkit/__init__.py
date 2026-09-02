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
from .apps_mac import EnviarMensajePersonalTool, LeerMensajesPersonalesTool
from .autoconfiguracion import ConfigurarCredencialTool
from .autorreparacion import (
    DiagnosticarAutorreparacionLocalTool,
    GestionarAutorreparacionLocalTool,
)
from .avances import AvisarAvanceTool
from .codigo_local import AccederCodigoLocalTool
from .computadora import UsarComputadoraTool
from .contactos import BuscarContactosTool, GestionarContactoTool
from .contenido import GenerarContenidoTool, PublicarSocialTool
from .correo import BuscarCorreoTool, EnviarCorreoTool
from .creator import CrearArtefactosTool
from .documentos import ConsultarDocumentosTool
from .finanzas import RegistrarTransaccionTool, ResumenFinanzasTool
from .gym import CambiarRutinaGymTool
from .ide_delegacion import DelegarAlIDETool
from .memoria import GuardarMemoriaTool
from .notificaciones import ProbarNotificacionesPushTool
from .preguntar import PreguntarAlUsuarioTool
from .recordatorios import CrearRecordatorioTool, ListarRecordatoriosTool
from .research import (
    SEARCH_CONNECTOR_KEY,
    BuscarWebTool,
    DuckDuckGoSearch,
    SearchHit,
    SearchProvider,
    get_search_provider,
    get_tenant_search_provider,
)
from .seguridad import AuditarSeguridadProyectoTool, EjecutarPentestGPTAutorizadoTool
from .utilidades import CalculadoraTool, HoraActualTool

__all__ = [
    "SEARCH_CONNECTOR_KEY",
    "AccederCodigoLocalTool",
    "AgendaEventosTool",
    "AuditarSeguridadProyectoTool",
    "DiagnosticarAutorreparacionLocalTool",
    "DuckDuckGoSearch",
    "BuscarContactosTool",
    "BuscarCorreoTool",
    "BuscarWebTool",
    "CalculadoraTool",
    "ConfigurarCredencialTool",
    "ConsultarDocumentosTool",
    "CrearEventoTool",
    "CrearArtefactosTool",
    "DelegarAlIDETool",
    "CrearRecordatorioTool",
    "EnviarMensajePersonalTool",
    "EnviarCorreoTool",
    "EjecutarPentestGPTAutorizadoTool",
    "GenerarContenidoTool",
    "GestionarContactoTool",
    "GestionarAutorreparacionLocalTool",
    "AvisarAvanceTool",
    "GuardarMemoriaTool",
    "HoraActualTool",
    "ListarRecordatoriosTool",
    "LeerMensajesPersonalesTool",
    "PublicarSocialTool",
    "PreguntarAlUsuarioTool",
    "ProbarNotificacionesPushTool",
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
        AvisarAvanceTool(),
        GuardarMemoriaTool(),
        CambiarRutinaGymTool(),
        AgendaEventosTool(),
        CrearEventoTool(),
        BuscarCorreoTool(),
        EnviarCorreoTool(),
        BuscarContactosTool(),
        GestionarContactoTool(),
        LeerMensajesPersonalesTool(),
        EnviarMensajePersonalTool(),
        RegistrarTransaccionTool(),
        ResumenFinanzasTool(),
        ConsultarDocumentosTool(),
        BuscarWebTool(),
        GenerarContenidoTool(),
        PublicarSocialTool(),
        PreguntarAlUsuarioTool(),
        ProbarNotificacionesPushTool(),
        UsarComputadoraTool(),
        HoraActualTool(),
        CalculadoraTool(),
        ConfigurarCredencialTool(),
        AccederCodigoLocalTool(),
        DelegarAlIDETool(),
        DiagnosticarAutorreparacionLocalTool(),
        GestionarAutorreparacionLocalTool(),
        AuditarSeguridadProyectoTool(),
        EjecutarPentestGPTAutorizadoTool(),
        CrearArtefactosTool(),
    ]
