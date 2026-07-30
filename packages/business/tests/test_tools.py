"""Tests de `edecan_business.tools`: `CrearFacturaTool` (name="crear_factura"),
`EstadoNegocioTool` (name="estado_negocio"), `GestionarInventarioTool`
(name="gestionar_inventario"), `EstadoInventarioTool` (name="estado_inventario"),
`GestionarEmpleadoTool` (name="gestionar_empleado"), `RegistrarAusenciaTool`
(name="registrar_ausencia") y `PrepararNominaTool` (name="preparar_nomina"). Van por
`ctx.session`/`ctx.settings` (un `ToolContext` falso, `make_ctx`) — la orquestación en sí
(`crear_factura`/`kpis_mes`/`edecan_business.inventory.*`/`edecan_business.rrhh.*`) ya está
probada a fondo en `test_invoices.py`/`test_kpis.py`/`test_inventory.py`/`test_rrhh.py`; aquí
solo se verifica el mapeo Tool -> `ToolResult` (mensaje en español, `data`, y que un
`ValueError`/`AusenciaSolapadaError` de la capa de negocio se traduzca a un
`ToolResult.content` en vez de propagar la excepción — ninguna tool de este paquete es
`dangerous` SALVO `preparar_nomina`, que solo marca el gate de confirmación del *tool call*
vía el atributo de clase; no ejecuta ningún flujo de aprobación aparte).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from edecan_business import get_all_tools
from edecan_business._files import subir_pdf
from edecan_business.tools import (
    CrearFacturaTool,
    EstadoInventarioTool,
    EstadoNegocioTool,
    GestionarEmpleadoTool,
    GestionarInventarioTool,
    PrepararNominaTool,
    RegistrarAusenciaTool,
)


def _invoice_row(**overrides):
    base = {
        "id": uuid4(),
        "numero": "F-2026-0001",
        "cliente_nombre": "Acme SA",
        "cliente_email": None,
        "moneda": "USD",
        "subtotal": Decimal("100.00"),
        "impuestos": Decimal("0.00"),
        "total": Decimal("100.00"),
        "status": "draft",
        "due_date": None,
        "notas": "",
        "created_at": None,
    }
    base.update(overrides)
    return base


def _item_row(**overrides):
    base = {
        "id": uuid4(),
        "descripcion": "Consultoría",
        "cantidad": Decimal("1"),
        "precio_unitario": Decimal("100.00"),
        "total": Decimal("100.00"),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# CrearFacturaTool
# ---------------------------------------------------------------------------


def test_crear_factura_tool_metadata():
    tool = CrearFacturaTool()
    assert tool.name == "crear_factura"
    assert tool.dangerous is False


def test_crear_factura_tool_default_uploader_es_subir_pdf():
    assert CrearFacturaTool()._uploader is subir_pdf


async def test_crear_factura_tool_happy_path(make_ctx, make_session, make_uploader):
    invoice_row = _invoice_row(id=uuid4())
    session = make_session([[{"n": 0}], [invoice_row], [_item_row()], []])
    uploader = make_uploader()
    ctx = make_ctx(session=session)
    tool = CrearFacturaTool(uploader=uploader)

    result = await tool.run(
        ctx,
        {
            "cliente_nombre": "Acme SA",
            "items": [{"descripcion": "Consultoría", "cantidad": 1, "precio_unitario": 100}],
        },
    )

    assert "F-2026-0001" in result.content
    assert "Acme SA" in result.content
    assert "sin enviar" in result.content  # nunca se envía sola
    assert result.data == {
        "invoice_id": str(invoice_row["id"]),
        "file_id": str(uploader.file_id),
        "numero": "F-2026-0001",
    }


async def test_crear_factura_tool_items_vacios_devuelve_toolresult_sin_reventar(make_ctx):
    ctx = make_ctx()
    tool = CrearFacturaTool()
    result = await tool.run(ctx, {"cliente_nombre": "Acme SA", "items": []})
    assert "item" in result.content.lower()
    assert result.data is None


async def test_crear_factura_tool_cliente_vacio_devuelve_toolresult(make_ctx):
    ctx = make_ctx()
    tool = CrearFacturaTool()
    result = await tool.run(
        ctx,
        {
            "cliente_nombre": "   ",
            "items": [{"descripcion": "X", "cantidad": 1, "precio_unitario": 1}],
        },
    )
    assert "cliente_nombre" in result.content
    assert result.data is None


# ---------------------------------------------------------------------------
# EstadoNegocioTool
# ---------------------------------------------------------------------------


def test_estado_negocio_tool_metadata():
    tool = EstadoNegocioTool()
    assert tool.name == "estado_negocio"
    assert tool.dangerous is False


async def test_estado_negocio_tool_resumen(make_ctx, make_session):
    session = make_session(
        [
            [{"monto": Decimal("500.00"), "cuenta": "web"}],
            [{"n": 2}],
            [{"total": Decimal("300.00")}],
            [{"total": Decimal("100.00")}],
            [],
            [],
        ]
    )
    ctx = make_ctx(session=session)
    tool = EstadoNegocioTool()

    result = await tool.run(ctx, {"mes": "2026-07"})

    assert "2026-07" in result.content
    assert "Ingresos" in result.content
    assert "Nuevos clientes" in result.content
    assert result.data["ingresos"] == 500.0
    assert result.data["nuevos_clientes"] == 2
    assert result.data["facturado"] == 300.0
    assert result.data["cobrado"] == 100.0


async def test_estado_negocio_tool_incluye_ventas_por_canal_cuando_hay(make_ctx, make_session):
    session = make_session(
        [
            [{"monto": Decimal("500.00"), "cuenta": "web"}],
            [{"n": 0}],
            [{"total": Decimal("0")}],
            [{"total": Decimal("0")}],
            [],
            [],
        ]
    )
    ctx = make_ctx(session=session)
    result = await EstadoNegocioTool().run(ctx, {"mes": "2026-07"})
    assert "Ventas por canal" in result.content
    assert "web" in result.content


async def test_estado_negocio_tool_mes_invalido_devuelve_toolresult_sin_reventar(
    make_ctx, make_session
):
    ctx = make_ctx(session=make_session())
    result = await EstadoNegocioTool().run(ctx, {"mes": "no-es-un-mes"})
    assert "mes válido" in result.content
    assert result.data is None


# ---------------------------------------------------------------------------
# GestionarInventarioTool / EstadoInventarioTool
# ---------------------------------------------------------------------------


def _producto_row(**overrides):
    base = {
        "id": uuid4(),
        "sku": "ABC-001",
        "nombre": "Widget",
        "descripcion": "",
        "unidad": "unidad",
        "precio": Decimal("10.00"),
        "costo": Decimal("5.00"),
        "stock": Decimal("0"),
        "stock_minimo": Decimal("0"),
        "activo": True,
    }
    base.update(overrides)
    return base


def test_gestionar_inventario_tool_metadata():
    tool = GestionarInventarioTool()
    assert tool.name == "gestionar_inventario"
    assert tool.dangerous is False
    assert "erp.inventory" in tool.requires_flags


async def test_gestionar_inventario_crear_producto_happy_path(make_ctx, make_session):
    fila = _producto_row(sku="X1", nombre="Widget")
    session = make_session([[], [fila]])
    ctx = make_ctx(session=session)
    result = await GestionarInventarioTool().run(
        ctx, {"accion": "crear_producto", "sku": "x1", "nombre": "Widget"}
    )
    assert "Widget" in result.content
    assert "X1" in result.content
    assert result.data == {"id": str(fila["id"]), "sku": "X1"}


async def test_gestionar_inventario_crear_producto_sin_nombre_no_revienta(make_ctx):
    ctx = make_ctx()
    result = await GestionarInventarioTool().run(ctx, {"accion": "crear_producto", "sku": "X1"})
    assert "nombre" in result.content.lower()
    assert result.data is None


async def test_gestionar_inventario_crear_producto_sku_duplicado_devuelve_toolresult(
    make_ctx, make_session
):
    session = make_session([[{"id": uuid4()}]])
    ctx = make_ctx(session=session)
    result = await GestionarInventarioTool().run(
        ctx, {"accion": "crear_producto", "sku": "X1", "nombre": "Widget"}
    )
    assert "existe" in result.content.lower()
    assert result.data is None


async def test_gestionar_inventario_sin_sku_pide_sku(make_ctx):
    ctx = make_ctx()
    result = await GestionarInventarioTool().run(ctx, {"accion": "editar_producto"})
    assert "sku" in result.content.lower()


async def test_gestionar_inventario_editar_producto_no_encontrado(make_ctx, make_session):
    session = make_session([[]])  # obtener_producto_por_sku -> no encontrado
    ctx = make_ctx(session=session)
    result = await GestionarInventarioTool().run(
        ctx, {"accion": "editar_producto", "sku": "NOPE", "nombre": "Nuevo"}
    )
    assert "no encontré" in result.content.lower()


async def test_gestionar_inventario_editar_producto_happy_path(make_ctx, make_session):
    producto = _producto_row(sku="X1")
    actualizado = _producto_row(sku="X1", nombre="Widget Pro")
    session = make_session([[producto], [actualizado]])
    ctx = make_ctx(session=session)
    result = await GestionarInventarioTool().run(
        ctx, {"accion": "editar_producto", "sku": "x1", "nombre": "Widget Pro"}
    )
    assert "Widget Pro" in result.content
    assert result.data == {"id": str(actualizado["id"]), "sku": "X1"}


async def test_gestionar_inventario_editar_producto_sin_campos_no_revienta(make_ctx, make_session):
    producto = _producto_row(sku="X1")
    session = make_session([[producto]])
    ctx = make_ctx(session=session)
    result = await GestionarInventarioTool().run(ctx, {"accion": "editar_producto", "sku": "x1"})
    assert "ningún campo" in result.content.lower()
    assert result.data is None


async def test_gestionar_inventario_registrar_movimiento_happy_path(make_ctx, make_session):
    producto = _producto_row(sku="X1", stock=Decimal("10"))
    move_row = {"id": uuid4()}
    actualizado = _producto_row(sku="X1", stock=Decimal("15"))
    # 4 respuestas: 1) obtener_producto_por_sku (resuelve sku->id) 2) el propio SELECT
    # interno de registrar_movimiento (lee el stock actual) 3) INSERT stock_moves
    # 4) UPDATE products.
    session = make_session([[producto], [producto], [move_row], [actualizado]])
    ctx = make_ctx(session=session)
    result = await GestionarInventarioTool().run(
        ctx,
        {"accion": "registrar_movimiento", "sku": "x1", "delta": 5, "motivo": "compra"},
    )
    assert "X1" in result.content
    assert "15" in result.content
    assert result.data["stock"] == 15.0


async def test_gestionar_inventario_registrar_movimiento_sin_delta_no_revienta(
    make_ctx, make_session
):
    producto = _producto_row(sku="X1")
    session = make_session([[producto]])
    ctx = make_ctx(session=session)
    result = await GestionarInventarioTool().run(
        ctx, {"accion": "registrar_movimiento", "sku": "x1", "motivo": "compra"}
    )
    assert "delta" in result.content.lower()


async def test_gestionar_inventario_registrar_movimiento_sin_motivo_no_revienta(
    make_ctx, make_session
):
    producto = _producto_row(sku="X1")
    session = make_session([[producto]])
    ctx = make_ctx(session=session)
    result = await GestionarInventarioTool().run(
        ctx, {"accion": "registrar_movimiento", "sku": "x1", "delta": 5}
    )
    assert "motivo" in result.content.lower()


async def test_gestionar_inventario_registrar_movimiento_stock_insuficiente_devuelve_toolresult(
    make_ctx, make_session
):
    producto = _producto_row(sku="X1", stock=Decimal("1"))
    # 2 respuestas: obtener_producto_por_sku + el SELECT interno de registrar_movimiento
    # (StockInsuficienteError se lanza ANTES de llegar al INSERT/UPDATE).
    session = make_session([[producto], [producto]])
    ctx = make_ctx(session=session)
    result = await GestionarInventarioTool().run(
        ctx,
        {"accion": "registrar_movimiento", "sku": "x1", "delta": -5, "motivo": "venta"},
    )
    assert "ajuste" in result.content.lower()
    assert result.data is None


async def test_gestionar_inventario_desactivar_producto_happy_path(make_ctx, make_session):
    producto = _producto_row(sku="X1")
    desactivado = _producto_row(sku="X1", activo=False)
    session = make_session([[producto], [desactivado]])
    ctx = make_ctx(session=session)
    result = await GestionarInventarioTool().run(
        ctx, {"accion": "desactivar_producto", "sku": "x1"}
    )
    assert "X1" in result.content
    assert result.data == {"id": str(desactivado["id"]), "sku": "X1"}


async def test_gestionar_inventario_accion_desconocida(make_ctx, make_session):
    producto = _producto_row(sku="X1")
    session = make_session([[producto]])
    ctx = make_ctx(session=session)
    result = await GestionarInventarioTool().run(ctx, {"accion": "volar", "sku": "x1"})
    assert "no reconozco" in result.content.lower()


def test_estado_inventario_tool_metadata():
    tool = EstadoInventarioTool()
    assert tool.name == "estado_inventario"
    assert tool.dangerous is False
    assert "erp.inventory" in tool.requires_flags


async def test_estado_inventario_sin_consulta_devuelve_resumen(make_ctx, make_session):
    filas = [_producto_row(sku="A", stock=Decimal("2"), stock_minimo=Decimal("5"))]
    session = make_session([filas])
    ctx = make_ctx(session=session)
    result = await EstadoInventarioTool().run(ctx, {})
    assert "SKUs activos" in result.content
    assert "Stock bajo" in result.content
    assert result.data["total_skus"] == 1


async def test_estado_inventario_sin_alertas_lo_dice_explicito(make_ctx, make_session):
    filas = [_producto_row(sku="A", stock=Decimal("50"), stock_minimo=Decimal("5"))]
    session = make_session([filas])
    ctx = make_ctx(session=session)
    result = await EstadoInventarioTool().run(ctx, {})
    assert "Sin alertas" in result.content


async def test_estado_inventario_con_consulta_busca_productos(make_ctx, make_session):
    filas = [_producto_row(sku="X1", nombre="Widget", stock=Decimal("3"))]
    session = make_session([filas])
    ctx = make_ctx(session=session)
    result = await EstadoInventarioTool().run(ctx, {"consulta": "widget"})
    assert "X1" in result.content
    assert result.data["productos"] == filas
    _, params = session.llamadas[0]
    assert params["patron"] == "%widget%"


async def test_estado_inventario_con_consulta_sin_resultados(make_ctx, make_session):
    session = make_session([[]])
    ctx = make_ctx(session=session)
    result = await EstadoInventarioTool().run(ctx, {"consulta": "nada"})
    assert "no encontré" in result.content.lower()
    assert result.data == {"productos": []}


# ---------------------------------------------------------------------------
# GestionarEmpleadoTool / RegistrarAusenciaTool / PrepararNominaTool
# ---------------------------------------------------------------------------


def _empleado_row(**overrides):
    base = {
        "id": uuid4(),
        "nombre": "Ana Pérez",
        "email": None,
        "puesto": "",
        "salario_mensual": None,
        "moneda": "USD",
        "fecha_ingreso": None,
        "status": "active",
    }
    base.update(overrides)
    return base


def test_gestionar_empleado_tool_metadata():
    tool = GestionarEmpleadoTool()
    assert tool.name == "gestionar_empleado"
    assert tool.dangerous is False
    assert "erp.hr" in tool.requires_flags


async def test_gestionar_empleado_crear_happy_path(make_ctx, make_session):
    fila = _empleado_row(nombre="Ana Pérez")
    session = make_session([[fila]])
    ctx = make_ctx(session=session)
    result = await GestionarEmpleadoTool().run(ctx, {"accion": "crear", "nombre": "Ana Pérez"})
    assert "Ana Pérez" in result.content
    assert result.data == {"id": str(fila["id"]), "nombre": "Ana Pérez"}


async def test_gestionar_empleado_crear_sin_nombre_no_revienta(make_ctx):
    ctx = make_ctx()
    result = await GestionarEmpleadoTool().run(ctx, {"accion": "crear"})
    assert "nombre" in result.content.lower()
    assert result.data is None


async def test_gestionar_empleado_crear_email_invalido_devuelve_toolresult(make_ctx):
    ctx = make_ctx()
    result = await GestionarEmpleadoTool().run(
        ctx, {"accion": "crear", "nombre": "Ana", "email": "no-es-un-email"}
    )
    assert "email" in result.content.lower()
    assert result.data is None


async def test_gestionar_empleado_actualizar_no_encontrado(make_ctx, make_session):
    session = make_session([[]])  # obtener_empleado_por_nombre -> no encontrado
    ctx = make_ctx(session=session)
    result = await GestionarEmpleadoTool().run(
        ctx, {"accion": "actualizar", "nombre": "Nadie", "puesto": "Gerente"}
    )
    assert "no encontré" in result.content.lower()


async def test_gestionar_empleado_actualizar_happy_path(make_ctx, make_session):
    empleado = _empleado_row(nombre="Ana Pérez")
    actualizado = _empleado_row(nombre="Ana Pérez", puesto="Gerente")
    session = make_session([[empleado], [actualizado]])
    ctx = make_ctx(session=session)
    result = await GestionarEmpleadoTool().run(
        ctx, {"accion": "actualizar", "nombre": "ana pérez", "puesto": "Gerente"}
    )
    assert "Ana Pérez" in result.content
    assert result.data == {"id": str(actualizado["id"]), "nombre": "Ana Pérez"}


async def test_gestionar_empleado_actualizar_sin_campos_no_revienta(make_ctx, make_session):
    empleado = _empleado_row(nombre="Ana Pérez")
    session = make_session([[empleado]])
    ctx = make_ctx(session=session)
    result = await GestionarEmpleadoTool().run(ctx, {"accion": "actualizar", "nombre": "Ana Pérez"})
    assert "ningún campo" in result.content.lower()
    assert result.data is None


async def test_gestionar_empleado_desactivar_happy_path(make_ctx, make_session):
    empleado = _empleado_row(nombre="Ana Pérez")
    desactivado = _empleado_row(nombre="Ana Pérez", status="inactive")
    session = make_session([[empleado], [desactivado]])
    ctx = make_ctx(session=session)
    result = await GestionarEmpleadoTool().run(ctx, {"accion": "desactivar", "nombre": "Ana Pérez"})
    assert "Ana Pérez" in result.content
    assert result.data == {"id": str(desactivado["id"]), "nombre": "Ana Pérez"}


async def test_gestionar_empleado_sin_nombre_pide_nombre(make_ctx):
    ctx = make_ctx()
    result = await GestionarEmpleadoTool().run(ctx, {"accion": "desactivar"})
    assert "nombre" in result.content.lower()


async def test_gestionar_empleado_accion_desconocida(make_ctx, make_session):
    empleado = _empleado_row(nombre="Ana Pérez")
    session = make_session([[empleado]])
    ctx = make_ctx(session=session)
    result = await GestionarEmpleadoTool().run(ctx, {"accion": "volar", "nombre": "Ana Pérez"})
    assert "no reconozco" in result.content.lower()


async def test_gestionar_empleado_listar_sin_empleados(make_ctx, make_session):
    session = make_session([[]])
    ctx = make_ctx(session=session)
    result = await GestionarEmpleadoTool().run(ctx, {"accion": "listar"})
    assert "no hay empleados" in result.content.lower()
    assert result.data == {"empleados": []}


async def test_gestionar_empleado_listar_con_empleados(make_ctx, make_session):
    filas = [_empleado_row(nombre="Ana", puesto="Ventas"), _empleado_row(nombre="Beto")]
    session = make_session([filas])
    ctx = make_ctx(session=session)
    result = await GestionarEmpleadoTool().run(ctx, {"accion": "listar"})
    assert "Ana" in result.content
    assert "Ventas" in result.content
    assert result.data == {"empleados": filas}


def test_registrar_ausencia_tool_metadata():
    tool = RegistrarAusenciaTool()
    assert tool.name == "registrar_ausencia"
    assert tool.dangerous is False
    assert "erp.hr" in tool.requires_flags


async def test_registrar_ausencia_tool_happy_path(make_ctx, make_session):
    empleado = _empleado_row(nombre="Ana Pérez")
    ausencia = {
        "id": uuid4(),
        "kind": "vacaciones",
        "desde": "2026-08-01",
        "hasta": "2026-08-05",
        "status": "pending",
    }
    # 1) obtener_empleado_por_nombre  2) SELECT empleado (dentro de registrar_ausencia)
    # 3) SELECT solapamiento (vacío)  4) INSERT time_off
    session = make_session([[empleado], [{"id": empleado["id"]}], [], [ausencia]])
    ctx = make_ctx(session=session)
    result = await RegistrarAusenciaTool().run(
        ctx,
        {
            "empleado": "Ana Pérez",
            "kind": "vacaciones",
            "desde": "2026-08-01",
            "hasta": "2026-08-05",
        },
    )
    assert "Ana Pérez" in result.content
    assert "pendiente" in result.content.lower()
    assert result.data == {"id": str(ausencia["id"]), "status": "pending"}


async def test_registrar_ausencia_tool_sin_empleado_no_revienta(make_ctx):
    ctx = make_ctx()
    result = await RegistrarAusenciaTool().run(
        ctx, {"empleado": "", "kind": "vacaciones", "desde": "2026-08-01", "hasta": "2026-08-05"}
    )
    assert "nombre" in result.content.lower()
    assert result.data is None


async def test_registrar_ausencia_tool_empleado_no_encontrado(make_ctx, make_session):
    session = make_session([[]])
    ctx = make_ctx(session=session)
    result = await RegistrarAusenciaTool().run(
        ctx,
        {"empleado": "Nadie", "kind": "vacaciones", "desde": "2026-08-01", "hasta": "2026-08-05"},
    )
    assert "no encontré" in result.content.lower()


async def test_registrar_ausencia_tool_solapada_devuelve_toolresult(make_ctx, make_session):
    empleado = _empleado_row(nombre="Ana Pérez")
    session = make_session([[empleado], [{"id": empleado["id"]}], [{"id": uuid4()}]])
    ctx = make_ctx(session=session)
    result = await RegistrarAusenciaTool().run(
        ctx,
        {
            "empleado": "Ana Pérez",
            "kind": "vacaciones",
            "desde": "2026-08-01",
            "hasta": "2026-08-05",
        },
    )
    assert "solapa" in result.content.lower()
    assert result.data is None


async def test_registrar_ausencia_tool_kind_invalido_devuelve_toolresult(make_ctx, make_session):
    empleado = _empleado_row(nombre="Ana Pérez")
    session = make_session([[empleado]])
    ctx = make_ctx(session=session)
    result = await RegistrarAusenciaTool().run(
        ctx,
        {"empleado": "Ana Pérez", "kind": "siesta", "desde": "2026-08-01", "hasta": "2026-08-05"},
    )
    assert "kind" in result.content.lower()
    assert result.data is None


def test_preparar_nomina_tool_metadata():
    tool = PrepararNominaTool()
    assert tool.name == "preparar_nomina"
    assert tool.dangerous is True
    assert "erp.hr" in tool.requires_flags
    assert "No mueve dinero" in tool.description


async def test_preparar_nomina_tool_happy_path(make_ctx, make_session):
    e1 = uuid4()
    empleados = [{"id": e1, "nombre": "Ana", "salario_mensual": Decimal("1000.00")}]
    run_row = {
        "id": uuid4(),
        "periodo": "2026-07",
        "status": "draft",
        "total": Decimal("1000.00"),
        "moneda": "USD",
        "notas": "",
    }
    item1 = {
        "id": uuid4(),
        "employee_id": e1,
        "bruto": Decimal("1000.00"),
        "deducciones": Decimal("0.00"),
        "neto": Decimal("1000.00"),
    }
    session = make_session([empleados, [run_row], [item1]])
    ctx = make_ctx(session=session)
    result = await PrepararNominaTool().run(ctx, {"periodo": "2026-07"})
    assert "2026-07" in result.content
    assert "No mueve dinero" in result.content
    assert result.data["periodo"] == "2026-07"
    assert result.data["id"] == str(run_row["id"])
    assert result.data["total"] == "1000.00"


async def test_preparar_nomina_tool_periodo_invalido_devuelve_toolresult(make_ctx):
    ctx = make_ctx()
    result = await PrepararNominaTool().run(ctx, {"periodo": "no-es-un-periodo"})
    assert "periodo" in result.content.lower()
    assert result.data is None


# ---------------------------------------------------------------------------
# get_all_tools()
# ---------------------------------------------------------------------------


def test_get_all_tools_devuelve_las_siete_herramientas_de_negocios():
    tools = get_all_tools()
    names = {t.name for t in tools}
    assert names == {
        "crear_factura",
        "estado_negocio",
        "gestionar_inventario",
        "estado_inventario",
        "gestionar_empleado",
        "registrar_ausencia",
        "preparar_nomina",
    }
    peligrosas = {t.name for t in tools if t.dangerous}
    assert peligrosas == {"preparar_nomina"}
