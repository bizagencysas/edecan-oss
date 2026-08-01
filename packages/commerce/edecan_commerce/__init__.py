"""`edecan_commerce` — dinero: cotizaciones, presupuestos y órdenes draft con gate
permanente (`ARCHITECTURE.md` §10.7, `ROADMAP_V2.md` §7.4/§7.5/§7.7/§8.1 — WP-V2-10).

Ver `docs/dinero-real.md` para la política de producto completa ("dinero real nunca se
mueve solo") y el README de este paquete para el detalle de cada módulo.

`get_all_tools()` es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`, vía el
`[project.entry-points."edecan.tools"]` de `pyproject.toml`.
"""

from __future__ import annotations

from edecan_core import Tool

from .alpaca import (
    ALPACA_CONNECTOR_KEY,
    AlpacaAPIError,
    AlpacaCredentials,
    AlpacaPaperBroker,
    AlpacaPaperClient,
    ConsultarAlpacaTool,
    resolve_alpaca_paper_client,
)
from .budgets import ALERTA_PCT, estado_presupuestos, fijar_presupuesto, listar_presupuestos
from .paper import PaperBroker
from .quotes import CoinGeckoQuotes, Quote, QuoteProvider, StubQuotes, get_quote_provider
from .tools import (
    CotizarActivoTool,
    GestionarPresupuestoTool,
    PrepararOrdenTool,
    PrepararPagoTool,
)

__all__ = [
    "ALERTA_PCT",
    "ALPACA_CONNECTOR_KEY",
    "AlpacaAPIError",
    "AlpacaCredentials",
    "AlpacaPaperBroker",
    "AlpacaPaperClient",
    "CoinGeckoQuotes",
    "CotizarActivoTool",
    "ConsultarAlpacaTool",
    "GestionarPresupuestoTool",
    "PaperBroker",
    "PrepararOrdenTool",
    "PrepararPagoTool",
    "Quote",
    "QuoteProvider",
    "StubQuotes",
    "estado_presupuestos",
    "fijar_presupuesto",
    "get_all_tools",
    "get_quote_provider",
    "listar_presupuestos",
    "resolve_alpaca_paper_client",
]


def get_all_tools() -> list[Tool]:
    """Instancia las herramientas financieras disponibles para el agente."""
    return [
        CotizarActivoTool(),
        ConsultarAlpacaTool(),
        GestionarPresupuestoTool(),
        PrepararPagoTool(),
        PrepararOrdenTool(),
    ]
