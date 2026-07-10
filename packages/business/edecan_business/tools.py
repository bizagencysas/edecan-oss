"""Herramientas de negocios (`ROADMAP_V2.md` §7.7, WP-V2-12; nombres exactos: `crear_factura`,
`estado_negocio`) + inventario / ERP básico (`ARCHITECTURE.md` §13, WP-V4-06; nombres exactos:
`gestionar_inventario`, `estado_inventario`, pinned en `ARCHITECTURE.md` §13.e) + RRHH
(`ARCHITECTURE.md` §14, WP-V5-08; nombres exactos: `gestionar_empleado`, `registrar_ausencia`,
`preparar_nomina`).

`crear_factura`/`estado_negocio` NO son `dangerous`: crear un borrador de documento no mueve
dinero ni notifica a nadie (enviar la factura por correo usaría `enviar_correo`, que ya trae
su propio gate de confirmación; cobrar de verdad lo hace el usuario a mano — ver el docstring
de `kpis.kpis_mes` sobre por qué `ingresos` nunca se infiere solo de facturar).

`gestionar_inventario`/`estado_inventario` TAMPOCO son `dangerous`: modifican datos propios
del negocio del usuario (productos/movimientos de stock), no dinero real ni nada que salga
del tenant — mismo criterio que `edecan_commerce.tools.GestionarPresupuestoTool`. Ambas
requieren el flag de plan `erp.inventory` (`requires_flags`, `ARCHITECTURE.md` §13.e), pinned
en `edecan_schemas.plans.FLAG_ERP_INVENTORY` (WP-V4-01) — mismo criterio que
`edecan_api.routers.commerce` importa `FLAG_COMMERCE_ORDERS` directo.

`gestionar_empleado`/`registrar_ausencia` TAMPOCO son `dangerous` (mismo criterio que
`gestionar_inventario`: datos propios del negocio, nada que mueva dinero ni salga del
tenant). `preparar_nomina` SÍ es `dangerous=True`: aunque solo genera un borrador contable
(nunca ejecuta ni promete ejecutar un pago — ver `rrhh.calcular_nomina`), calcular una nómina
completa del negocio es una acción con peso suficiente como para exigir confirmación humana
del *tool call* antes de correrla, mismo criterio que `preparar_pago`/`preparar_orden` de
`edecan_commerce.tools` (que tampoco mueven dinero por sí solas y aun así son `dangerous`).
Las tres requieren el flag `erp.hr` (`edecan_schemas.plans.FLAG_ERP_HR`, `ARCHITECTURE.md`
§14.c — ya pinned en `PLANES`, `True` en los 4 planes).

Ninguna tool de este módulo le pide al modelo un `id`/UUID crudo (mismo criterio que el
resto del repo, ver `edecan_toolkit.contactos`): los productos se identifican por `sku`, los
empleados por `nombre` — nunca por su id interno; eso solo lo usa el router HTTP
(`/v1/erp/productos/{id}`, `/v1/rrhh/empleados/{id}`, consumidos por la web, que sí tiene el
UUID a mano tras listar).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from edecan_schemas.plans import FLAG_ERP_HR, FLAG_ERP_INVENTORY

from ._files import Uploader, subir_pdf
from .inventory import (
    crear_producto,
    desactivar_producto,
    editar_producto,
    listar_productos,
    obtener_producto_por_sku,
    registrar_movimiento,
    resumen_inventario,
)
from .invoices import crear_factura
from .kpis import kpis_mes
from .rrhh import (
    AusenciaSolapadaError,
    calcular_nomina,
    crear_empleado,
    desactivar_empleado,
    editar_empleado,
    listar_empleados,
    obtener_empleado_por_nombre,
    registrar_ausencia,
)

_CAMPOS_EDITABLES_TOOL: tuple[str, ...] = (
    "nombre",
    "descripcion",
    "unidad",
    "precio",
    "costo",
    "stock_minimo",
)

# `nombre` queda FUERA a propósito (a diferencia de `editar_empleado`, que sí lo acepta): en
# esta tool `nombre` es el identificador con el que se resuelve el empleado (`accion=
# actualizar/desactivar`), mismo rol que `sku` en `_CAMPOS_EDITABLES_TOOL` de
# `GestionarInventarioTool` — mezclar "identificador" y "campo editable" en la misma clave
# dejaría inalcanzable el mensaje de "no indicaste ningún campo" (un `nombre` presente para
# identificar SIEMPRE contaría como "cambio"). Renombrar un empleado queda como una acción de
# la pantalla RRHH (`PATCH /v1/rrhh/empleados/{id}`, que identifica por id, no por nombre),
# no del chat.
_CAMPOS_EDITABLES_EMPLEADO: tuple[str, ...] = ("email", "puesto", "salario_mensual", "status")

_AVISO_NOMINA_NO_MUEVE_DINERO = (
    "No mueve dinero: genera un borrador contable que apruebas tú en la pantalla RRHH."
)


class CrearFacturaTool(Tool):
    name = "crear_factura"
    description = (
        "Crea un borrador de factura (status='draft') para un cliente, con sus items y un "
        "PDF generado automáticamente. NO envía nada ni mueve dinero: solo deja el "
        "documento listo para revisar. Usa 'enviar_correo' si además quieres mandarla al "
        "cliente."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "cliente_nombre": {
                "type": "string",
                "description": "Nombre del cliente a facturar.",
            },
            "items": {
                "type": "array",
                "description": "Líneas de la factura.",
                "items": {
                    "type": "object",
                    "properties": {
                        "descripcion": {"type": "string"},
                        "cantidad": {"type": "number"},
                        "precio_unitario": {"type": "number"},
                    },
                    "required": ["descripcion", "cantidad", "precio_unitario"],
                },
            },
            "impuestos_pct": {
                "type": "number",
                "description": "Porcentaje de impuestos sobre el subtotal (0-100).",
                "default": 0,
            },
            "due_date": {
                "type": "string",
                "description": "Fecha límite de pago, formato YYYY-MM-DD. Opcional.",
            },
            "cliente_email": {"type": "string", "description": "Email del cliente. Opcional."},
            "notas": {"type": "string", "description": "Notas al pie de la factura. Opcional."},
        },
        "required": ["cliente_nombre", "items"],
    }

    def __init__(self, *, uploader: Uploader | None = None) -> None:
        self._uploader = uploader or subir_pdf

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            invoice = await crear_factura(
                ctx.session,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                settings=ctx.settings,
                cliente_nombre=str(args.get("cliente_nombre", "")),
                items=args.get("items") or [],
                impuestos_pct=args.get("impuestos_pct", 0) or 0,
                due_date=args.get("due_date"),
                cliente_email=args.get("cliente_email"),
                notas=args.get("notas") or "",
                uploader=self._uploader,
            )
        except ValueError as exc:
            return ToolResult(content=str(exc))

        total = Decimal(str(invoice["total"]))
        return ToolResult(
            content=(
                f"Creé la factura {invoice['numero']} para «{invoice['cliente_nombre']}» "
                f"por {invoice['moneda']} {total:,.2f} (borrador, sin enviar todavía)."
            ),
            data={
                "invoice_id": str(invoice["id"]),
                "file_id": str(invoice["file_id"]),
                "numero": invoice["numero"],
            },
        )


class EstadoNegocioTool(Tool):
    name = "estado_negocio"
    description = (
        "Resumen ejecutivo del negocio del mes indicado: ingresos, gastos, beneficio, "
        "nuevos clientes, facturado y cobrado."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "mes": {
                "type": "string",
                "description": "Mes a resumir, formato YYYY-MM. Por defecto: mes actual.",
            },
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            kpis = await kpis_mes(
                ctx.session, tenant_id=ctx.tenant_id, user_id=ctx.user_id, mes=args.get("mes")
            )
        except ValueError as exc:
            return ToolResult(content=str(exc))

        lineas = [
            f"Estado del negocio de {kpis['mes']}:",
            f"  Ingresos: ${kpis['ingresos']:,.2f}",
            f"  Gastos: ${kpis['gastos']:,.2f}",
            f"  Beneficio: ${kpis['beneficio']:,.2f}",
            f"  Nuevos clientes: {kpis['nuevos_clientes']}",
            f"  Facturado: ${kpis['facturado']:,.2f}",
            f"  Cobrado: ${kpis['cobrado']:,.2f}",
        ]
        if kpis["por_canal"]:
            lineas.append("Ventas por canal:")
            lineas.extend(f"  - {c['canal']}: ${c['total']:,.2f}" for c in kpis["por_canal"])

        return ToolResult(content="\n".join(lineas), data=kpis)


# ---------------------------------------------------------------------------
# Inventario / ERP básico (ARCHITECTURE.md §13, WP-V4-06)
# ---------------------------------------------------------------------------


class GestionarInventarioTool(Tool):
    name = "gestionar_inventario"
    description = (
        "Crea, edita o desactiva productos del inventario, y registra movimientos de stock "
        "(entradas, salidas o ajustes). El producto SIEMPRE se identifica por su 'sku'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "accion": {
                "type": "string",
                "enum": [
                    "crear_producto",
                    "editar_producto",
                    "registrar_movimiento",
                    "desactivar_producto",
                ],
                "description": "Qué operación realizar sobre el inventario.",
            },
            "sku": {
                "type": "string",
                "description": "SKU del producto. Identifica al producto en las 4 acciones.",
            },
            "nombre": {
                "type": "string",
                "description": "Nombre del producto. Requerido para accion='crear_producto'.",
            },
            "descripcion": {"type": "string", "description": "Descripción del producto. Opcional."},
            "unidad": {
                "type": "string",
                "description": "Unidad de medida (p. ej. 'unidad', 'kg', 'caja'). Opcional.",
            },
            "precio": {"type": "number", "description": "Precio de venta. Opcional."},
            "costo": {"type": "number", "description": "Costo del producto. Opcional."},
            "stock_minimo": {
                "type": "number",
                "description": "Stock mínimo antes de generar una alerta. Opcional.",
            },
            "delta": {
                "type": "number",
                "description": (
                    "Cantidad del movimiento: positiva para entradas, negativa para salidas. "
                    "Requerido para accion='registrar_movimiento'."
                ),
            },
            "motivo": {
                "type": "string",
                "enum": ["compra", "venta", "ajuste", "merma", "devolucion"],
                "description": (
                    "Motivo del movimiento. Requerido para accion='registrar_movimiento'."
                ),
            },
            "nota": {"type": "string", "description": "Nota libre sobre el movimiento. Opcional."},
            "ref": {
                "type": "string",
                "description": (
                    "Referencia externa del movimiento (p. ej. número de factura/orden). Opcional."
                ),
            },
        },
        "required": ["accion", "sku"],
    }
    requires_flags = frozenset({FLAG_ERP_INVENTORY})

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        accion = args.get("accion")
        sku = str(args.get("sku", "")).strip()
        if not sku:
            return ToolResult(content="Necesito el sku del producto.")

        if accion == "crear_producto":
            return await self._crear(ctx, sku, args)

        producto = await obtener_producto_por_sku(ctx.session, tenant_id=ctx.tenant_id, sku=sku)
        if producto is None:
            return ToolResult(content=f"No encontré ningún producto con sku «{sku}».")
        product_id = producto["id"]
        # A partir de acá se usa el sku YA normalizado (`producto["sku"]`, mayúsculas) en los
        # mensajes — no el que tecleó el modelo — para que "x1"/"X1"/" x1 " siempre respondan
        # con el mismo sku "canónico", igual que ya hace `crear_producto`.
        sku_norm = producto["sku"]

        if accion == "editar_producto":
            return await self._editar(ctx, sku_norm, product_id, args)
        if accion == "registrar_movimiento":
            return await self._registrar_movimiento(ctx, sku_norm, product_id, args)
        if accion == "desactivar_producto":
            return await self._desactivar(ctx, sku_norm, product_id)

        return ToolResult(content=f"No reconozco la acción «{accion}».")

    async def _crear(self, ctx: ToolContext, sku: str, args: dict[str, Any]) -> ToolResult:
        nombre = str(args.get("nombre", "")).strip()
        if not nombre:
            return ToolResult(content="Necesito el nombre del producto a crear.")
        try:
            producto = await crear_producto(
                ctx.session,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                sku=sku,
                nombre=nombre,
                descripcion=args.get("descripcion") or "",
                unidad=args.get("unidad") or "unidad",
                precio=args.get("precio"),
                costo=args.get("costo"),
                stock_minimo=args.get("stock_minimo", 0) or 0,
            )
        except ValueError as exc:
            return ToolResult(content=str(exc))
        return ToolResult(
            content=f"Creé el producto «{producto['nombre']}» (sku {producto['sku']}).",
            data={"id": str(producto["id"]), "sku": producto["sku"]},
        )

    async def _editar(
        self, ctx: ToolContext, sku: str, product_id: Any, args: dict[str, Any]
    ) -> ToolResult:
        cambios = {
            campo: args[campo]
            for campo in _CAMPOS_EDITABLES_TOOL
            if campo in args and args[campo] is not None
        }
        if not cambios:
            return ToolResult(content="No indicaste ningún campo para editar.")
        try:
            actualizado = await editar_producto(
                ctx.session, tenant_id=ctx.tenant_id, product_id=product_id, **cambios
            )
        except ValueError as exc:
            return ToolResult(content=str(exc))
        if actualizado is None:
            return ToolResult(content=f"El producto «{sku}» ya no existe.")
        return ToolResult(
            content=f"Actualicé el producto «{actualizado['nombre']}» (sku {actualizado['sku']}).",
            data={"id": str(actualizado["id"]), "sku": actualizado["sku"]},
        )

    async def _registrar_movimiento(
        self, ctx: ToolContext, sku: str, product_id: Any, args: dict[str, Any]
    ) -> ToolResult:
        delta = args.get("delta")
        motivo = args.get("motivo")
        if delta is None:
            return ToolResult(content="Necesito la cantidad (delta) del movimiento.")
        if not motivo:
            return ToolResult(content="Necesito el motivo del movimiento.")
        try:
            movimiento = await registrar_movimiento(
                ctx.session,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                product_id=product_id,
                delta=delta,
                motivo=motivo,
                nota=args.get("nota") or "",
                ref=args.get("ref"),
            )
        except ValueError as exc:
            return ToolResult(content=str(exc))
        if movimiento is None:
            return ToolResult(content=f"El producto «{sku}» ya no existe.")
        nuevo_stock = float(movimiento["producto"]["stock"])
        return ToolResult(
            content=(
                f"Registré el movimiento de «{sku}» (motivo: {motivo}). "
                f"Stock actual: {nuevo_stock:,.2f}."
            ),
            data={"id": str(movimiento["id"]), "sku": sku, "stock": nuevo_stock},
        )

    async def _desactivar(self, ctx: ToolContext, sku: str, product_id: Any) -> ToolResult:
        desactivado = await desactivar_producto(
            ctx.session, tenant_id=ctx.tenant_id, product_id=product_id
        )
        if desactivado is None:
            return ToolResult(content=f"El producto «{sku}» ya no existe.")
        return ToolResult(
            content=f"Desactivé el producto «{sku}».",
            data={"id": str(desactivado["id"]), "sku": desactivado["sku"]},
        )


class EstadoInventarioTool(Tool):
    name = "estado_inventario"
    description = (
        "Consulta el inventario: resumen general (SKUs activos, valor a costo/precio, "
        "alertas de stock bajo) o busca productos activos por sku/nombre."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "consulta": {
                "type": "string",
                "description": (
                    "Texto a buscar por sku o nombre. Si se omite, devuelve el resumen general."
                ),
            },
        },
        "required": [],
    }
    requires_flags = frozenset({FLAG_ERP_INVENTORY})

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        consulta = str(args.get("consulta") or "").strip()

        if consulta:
            productos = await listar_productos(
                ctx.session, tenant_id=ctx.tenant_id, activo=True, q=consulta
            )
            if not productos:
                return ToolResult(
                    content=f"No encontré productos activos para «{consulta}».",
                    data={"productos": []},
                )
            lineas = [
                f"- {p['sku']} — {p['nombre']}: stock {float(p['stock']):,.2f} {p['unidad']}"
                for p in productos
            ]
            return ToolResult(content="\n".join(lineas), data={"productos": productos})

        resumen = await resumen_inventario(ctx.session, tenant_id=ctx.tenant_id)
        lineas = [
            f"Inventario: {resumen['total_skus']} SKUs activos.",
            f"  Valor a costo: ${resumen['valor_costo']:,.2f}",
            f"  Valor a precio de venta: ${resumen['valor_precio']:,.2f}",
        ]
        if resumen["stock_bajo"]:
            lineas.append("Stock bajo:")
            lineas.extend(
                f"  - {p['sku']} ({p['nombre']}): {p['stock']:,.2f} de mínimo "
                f"{p['stock_minimo']:,.2f}"
                for p in resumen["stock_bajo"]
            )
        else:
            lineas.append("Sin alertas de stock bajo.")
        return ToolResult(content="\n".join(lineas), data=resumen)


# ---------------------------------------------------------------------------
# RRHH: empleados / ausencias / nómina en borrador (ARCHITECTURE.md §14, WP-V5-08)
# ---------------------------------------------------------------------------


class GestionarEmpleadoTool(Tool):
    name = "gestionar_empleado"
    description = (
        "Crea, actualiza, desactiva o lista empleados del negocio. El empleado SIEMPRE se "
        "identifica por su 'nombre' (nunca por un id)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "accion": {
                "type": "string",
                "enum": ["crear", "actualizar", "desactivar", "listar"],
                "description": "Qué operación realizar sobre el empleado.",
            },
            "nombre": {
                "type": "string",
                "description": (
                    "Nombre del empleado. Requerido para crear/actualizar/desactivar "
                    "(identifica al empleado en esas tres acciones; para RENOMBRAR un "
                    "empleado existente usa la pantalla RRHH, no esta tool)."
                ),
            },
            "email": {"type": "string", "description": "Email del empleado. Opcional."},
            "puesto": {"type": "string", "description": "Puesto o cargo del empleado. Opcional."},
            "salario_mensual": {
                "type": "number",
                "description": "Salario mensual del empleado. Opcional.",
            },
            "status": {
                "type": "string",
                "enum": ["active", "inactive"],
                "description": "Estado del empleado. Opcional (nace 'active' al crear).",
            },
            "consulta": {
                "type": "string",
                "description": (
                    "Texto a buscar por nombre/email/puesto. Solo para accion='listar'; si "
                    "se omite lista todos los empleados."
                ),
            },
        },
        "required": ["accion"],
    }
    requires_flags = frozenset({FLAG_ERP_HR})

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        accion = args.get("accion")

        if accion == "listar":
            consulta = str(args.get("consulta") or "").strip() or None
            empleados = await listar_empleados(ctx.session, tenant_id=ctx.tenant_id, q=consulta)
            if not empleados:
                return ToolResult(
                    content="No hay empleados registrados todavía.", data={"empleados": []}
                )
            lineas = [
                f"- {e['nombre']}"
                + (f" ({e['puesto']})" if e.get("puesto") else "")
                + f" — {e['status']}"
                for e in empleados
            ]
            return ToolResult(content="\n".join(lineas), data={"empleados": empleados})

        nombre = str(args.get("nombre", "")).strip()
        if not nombre:
            return ToolResult(content="Necesito el nombre del empleado.")

        if accion == "crear":
            return await self._crear(ctx, nombre, args)

        empleado = await obtener_empleado_por_nombre(
            ctx.session, tenant_id=ctx.tenant_id, nombre=nombre
        )
        if empleado is None:
            return ToolResult(content=f"No encontré ningún empleado llamado «{nombre}».")

        if accion == "actualizar":
            return await self._actualizar(ctx, nombre, empleado["id"], args)
        if accion == "desactivar":
            return await self._desactivar(ctx, nombre, empleado["id"])

        return ToolResult(content=f"No reconozco la acción «{accion}».")

    async def _crear(self, ctx: ToolContext, nombre: str, args: dict[str, Any]) -> ToolResult:
        try:
            empleado = await crear_empleado(
                ctx.session,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                nombre=nombre,
                email=args.get("email"),
                puesto=args.get("puesto") or "",
                salario_mensual=args.get("salario_mensual"),
                status=args.get("status") or "active",
            )
        except ValueError as exc:
            return ToolResult(content=str(exc))
        return ToolResult(
            content=f"Creé el empleado «{empleado['nombre']}».",
            data={"id": str(empleado["id"]), "nombre": empleado["nombre"]},
        )

    async def _actualizar(
        self, ctx: ToolContext, nombre: str, employee_id: Any, args: dict[str, Any]
    ) -> ToolResult:
        cambios = {
            campo: args[campo]
            for campo in _CAMPOS_EDITABLES_EMPLEADO
            if campo in args and args[campo] is not None
        }
        if not cambios:
            return ToolResult(content="No indicaste ningún campo para actualizar.")
        try:
            actualizado = await editar_empleado(
                ctx.session, tenant_id=ctx.tenant_id, employee_id=employee_id, **cambios
            )
        except ValueError as exc:
            return ToolResult(content=str(exc))
        if actualizado is None:
            return ToolResult(content=f"El empleado «{nombre}» ya no existe.")
        return ToolResult(
            content=f"Actualicé el empleado «{actualizado['nombre']}».",
            data={"id": str(actualizado["id"]), "nombre": actualizado["nombre"]},
        )

    async def _desactivar(self, ctx: ToolContext, nombre: str, employee_id: Any) -> ToolResult:
        desactivado = await desactivar_empleado(
            ctx.session, tenant_id=ctx.tenant_id, employee_id=employee_id
        )
        if desactivado is None:
            return ToolResult(content=f"El empleado «{nombre}» ya no existe.")
        return ToolResult(
            content=f"Desactivé el empleado «{nombre}».",
            data={"id": str(desactivado["id"]), "nombre": desactivado["nombre"]},
        )


class RegistrarAusenciaTool(Tool):
    name = "registrar_ausencia"
    description = (
        "Registra una ausencia (vacaciones, enfermedad, permiso u otro) de un empleado, "
        "identificado por su nombre. Queda pendiente de aprobación en la pantalla RRHH."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "empleado": {"type": "string", "description": "Nombre del empleado."},
            "kind": {
                "type": "string",
                "enum": ["vacaciones", "enfermedad", "permiso", "otro"],
                "description": "Tipo de ausencia.",
            },
            "desde": {"type": "string", "description": "Fecha de inicio, formato YYYY-MM-DD."},
            "hasta": {"type": "string", "description": "Fecha de fin, formato YYYY-MM-DD."},
            "notas": {"type": "string", "description": "Nota libre sobre la ausencia. Opcional."},
        },
        "required": ["empleado", "kind", "desde", "hasta"],
    }
    requires_flags = frozenset({FLAG_ERP_HR})

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        nombre = str(args.get("empleado", "")).strip()
        if not nombre:
            return ToolResult(content="Necesito el nombre del empleado.")

        empleado = await obtener_empleado_por_nombre(
            ctx.session, tenant_id=ctx.tenant_id, nombre=nombre
        )
        if empleado is None:
            return ToolResult(content=f"No encontré ningún empleado llamado «{nombre}».")

        try:
            ausencia = await registrar_ausencia(
                ctx.session,
                tenant_id=ctx.tenant_id,
                employee_id=empleado["id"],
                kind=args.get("kind"),
                desde=args.get("desde"),
                hasta=args.get("hasta"),
                notas=args.get("notas") or "",
            )
        except (AusenciaSolapadaError, ValueError) as exc:
            return ToolResult(content=str(exc))
        if ausencia is None:
            return ToolResult(content=f"El empleado «{nombre}» ya no existe.")

        return ToolResult(
            content=(
                f"Registré la ausencia de «{nombre}» ({ausencia['kind']}, {ausencia['desde']} a "
                f"{ausencia['hasta']}). Queda pendiente de aprobación en la pantalla RRHH."
            ),
            data={"id": str(ausencia["id"]), "status": ausencia["status"]},
        )


class PrepararNominaTool(Tool):
    name = "preparar_nomina"
    description = (
        "Genera un BORRADOR de nómina para un periodo (YYYY-MM): calcula bruto/deducciones/"
        f"neto de cada empleado activo con salario asignado. {_AVISO_NOMINA_NO_MUEVE_DINERO}"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "periodo": {"type": "string", "description": "Periodo a calcular, formato YYYY-MM."},
            "deducciones_pct": {
                "type": "number",
                "description": (
                    "Porcentaje global de deducciones sobre el bruto de cada empleado (0-50). "
                    "Opcional (por defecto 0)."
                ),
            },
        },
        "required": ["periodo"],
    }
    requires_flags = frozenset({FLAG_ERP_HR})
    dangerous = True

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            nomina = await calcular_nomina(
                ctx.session,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                periodo=args.get("periodo"),
                deducciones_pct=args.get("deducciones_pct"),
            )
        except ValueError as exc:
            return ToolResult(content=str(exc))

        cantidad = len(nomina["items"])
        # `payroll_runs.total` es la ÚNICA columna persistida de totales (el neto de la
        # corrida) — ver el docstring de `rrhh.calcular_nomina` sobre por qué no hay
        # `total_bruto`/`total_neto` separados a nivel de esquema.
        total = Decimal(str(nomina["total"]))
        return ToolResult(
            content=(
                f"Borrador de nómina de {nomina['periodo']} generado: {cantidad} empleado(s), "
                f"neto total {nomina['moneda']} {total:,.2f}. {_AVISO_NOMINA_NO_MUEVE_DINERO}"
            ),
            data={
                "id": str(nomina["id"]),
                "periodo": nomina["periodo"],
                "total": str(total),
            },
        )
