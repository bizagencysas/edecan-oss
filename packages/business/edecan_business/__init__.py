"""`edecan_business` — negocios: facturación con PDF real + KPIs de negocio
(`ARCHITECTURE.md` §10.7; `ROADMAP_V2.md` §5 P1 WP-V2-12, §7.4, §7.7) + inventario / ERP
básico (`ARCHITECTURE.md` §12, WP-V4-06) + RRHH: empleados, ausencias y nómina en borrador
(`ARCHITECTURE.md` §14, WP-V5-08).

`get_all_tools()` es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")` (§10.7), declarado en
`pyproject.toml` como `[project.entry-points."edecan.tools"]`.

Todo lo demás que se re-exporta aquí (`invoices`/`kpis`/`inventory`/`rrhh`/`_files`) son los
módulos "puros y fakeables" que consumen directamente `apps/api/edecan_api/routers/
negocios.py`, `apps/api/edecan_api/routers/erp.py` y `apps/api/edecan_api/routers/rrhh.py` —
ver el docstring de cada uno.
"""

from __future__ import annotations

from edecan_core import Tool

from ._files import Uploader, subir_pdf
from .inventory import (
    SkuDuplicadoError,
    StockInsuficienteError,
    crear_producto,
    desactivar_producto,
    editar_producto,
    listar_productos,
    obtener_producto_por_sku,
    registrar_movimiento,
    resumen_inventario,
)
from .invoices import (
    EstadoInvalidoError,
    cambiar_estado,
    compute_totals,
    crear_factura,
    listar_facturas,
    next_numero,
    obtener_factura,
    render_pdf,
)
from .kpis import kpis_mes
from .rrhh import (
    AusenciaSolapadaError,
    EstadoAusenciaError,
    EstadoNominaError,
    aprobar_nomina,
    calcular_nomina,
    cancelar_nomina,
    crear_empleado,
    desactivar_empleado,
    editar_empleado,
    listar_ausencias,
    listar_empleados,
    listar_nominas,
    obtener_empleado,
    obtener_empleado_por_nombre,
    obtener_nomina,
    registrar_ausencia,
    resolver_ausencia,
)
from .tools import (
    FLAG_ERP_HR,
    CrearFacturaTool,
    EstadoInventarioTool,
    EstadoNegocioTool,
    GestionarEmpleadoTool,
    GestionarInventarioTool,
    PrepararNominaTool,
    RegistrarAusenciaTool,
)

__all__ = [
    "AusenciaSolapadaError",
    "CrearFacturaTool",
    "EstadoAusenciaError",
    "EstadoInvalidoError",
    "EstadoInventarioTool",
    "EstadoNegocioTool",
    "EstadoNominaError",
    "FLAG_ERP_HR",
    "GestionarEmpleadoTool",
    "GestionarInventarioTool",
    "PrepararNominaTool",
    "RegistrarAusenciaTool",
    "SkuDuplicadoError",
    "StockInsuficienteError",
    "Uploader",
    "aprobar_nomina",
    "calcular_nomina",
    "cambiar_estado",
    "cancelar_nomina",
    "compute_totals",
    "crear_empleado",
    "crear_factura",
    "crear_producto",
    "desactivar_empleado",
    "desactivar_producto",
    "editar_empleado",
    "editar_producto",
    "get_all_tools",
    "kpis_mes",
    "listar_ausencias",
    "listar_empleados",
    "listar_facturas",
    "listar_nominas",
    "listar_productos",
    "next_numero",
    "obtener_empleado",
    "obtener_empleado_por_nombre",
    "obtener_factura",
    "obtener_nomina",
    "obtener_producto_por_sku",
    "registrar_ausencia",
    "registrar_movimiento",
    "render_pdf",
    "resolver_ausencia",
    "resumen_inventario",
    "subir_pdf",
]


def get_all_tools() -> list[Tool]:
    """Instancia las 7 herramientas del paquete (nombres exactos: `crear_factura`,
    `estado_negocio` de `ROADMAP_V2.md` §7.7; `gestionar_inventario`, `estado_inventario` de
    `ARCHITECTURE.md` §12, WP-V4-06; `gestionar_empleado`, `registrar_ausencia`,
    `preparar_nomina` de `ARCHITECTURE.md` §14, WP-V5-08)."""
    return [
        CrearFacturaTool(),
        EstadoNegocioTool(),
        GestionarInventarioTool(),
        EstadoInventarioTool(),
        GestionarEmpleadoTool(),
        RegistrarAusenciaTool(),
        PrepararNominaTool(),
    ]
