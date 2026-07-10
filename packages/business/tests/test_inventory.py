"""Tests de `edecan_business.inventory` — productos (crear/editar/desactivar/listar),
movimientos de stock (con la regla de "no negativo salvo ajuste") y el resumen de
inventario. Offline y determinista: `FakeSession` programable (`conftest.py`), nunca toca
Postgres real.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from edecan_business.inventory import (
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


# ---------------------------------------------------------------------------
# crear_producto
# ---------------------------------------------------------------------------


async def test_crear_producto_normaliza_sku_a_mayusculas(make_session):
    fila = _producto_row(sku="ABC-001")
    session = make_session([[], [fila]])
    producto = await crear_producto(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        sku="  abc-001  ",
        nombre="Widget",
    )
    assert producto["sku"] == "ABC-001"
    # El SELECT de chequeo de duplicado ya debe usar el sku normalizado.
    _, params_select = session.llamadas[0]
    assert params_select["sku"] == "ABC-001"
    _, params_insert = session.llamadas[1]
    assert params_insert["sku"] == "ABC-001"


async def test_crear_producto_usa_defaults_de_descripcion_unidad_y_stock_minimo(make_session):
    fila = _producto_row()
    session = make_session([[], [fila]])
    await crear_producto(session, tenant_id=uuid4(), user_id=uuid4(), sku="X1", nombre="Widget")
    _, params_insert = session.llamadas[1]
    assert params_insert["descripcion"] == ""
    assert params_insert["unidad"] == "unidad"
    assert params_insert["stock_minimo"] == Decimal("0")
    assert params_insert["precio"] is None
    assert params_insert["costo"] is None


async def test_crear_producto_inserta_no_incluye_stock_relies_on_default(make_session):
    """`crear_producto` nunca manda `stock` en el INSERT — nace SIEMPRE en el default de la
    columna (0), ver el docstring del módulo."""
    fila = _producto_row()
    session = make_session([[], [fila]])
    await crear_producto(session, tenant_id=uuid4(), user_id=uuid4(), sku="X1", nombre="Widget")
    sql_insert, params_insert = session.llamadas[1]
    assert "stock" not in params_insert
    assert "stock_minimo" in params_insert  # sí es un campo explícito, distinto de `stock`


async def test_crear_producto_sku_duplicado_no_inserta(make_session):
    session = make_session([[{"id": uuid4()}]])  # ya existe
    with pytest.raises(SkuDuplicadoError, match="ABC-001"):
        await crear_producto(
            session, tenant_id=uuid4(), user_id=uuid4(), sku="abc-001", nombre="Widget"
        )
    assert len(session.llamadas) == 1  # nunca llegó al INSERT


async def test_crear_producto_sku_vacio_rechaza_sin_tocar_sesion(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="sku"):
        await crear_producto(
            session, tenant_id=uuid4(), user_id=uuid4(), sku="   ", nombre="Widget"
        )
    assert session.llamadas == []


async def test_crear_producto_nombre_vacio_rechaza_sin_tocar_sesion(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="nombre"):
        await crear_producto(session, tenant_id=uuid4(), user_id=uuid4(), sku="X1", nombre="   ")
    assert session.llamadas == []


async def test_crear_producto_precio_negativo_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="precio"):
        await crear_producto(
            session, tenant_id=uuid4(), user_id=uuid4(), sku="X1", nombre="Widget", precio=-1
        )
    assert session.llamadas == []


async def test_crear_producto_costo_negativo_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="costo"):
        await crear_producto(
            session, tenant_id=uuid4(), user_id=uuid4(), sku="X1", nombre="Widget", costo=-1
        )
    assert session.llamadas == []


async def test_crear_producto_stock_minimo_negativo_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="stock_minimo"):
        await crear_producto(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            sku="X1",
            nombre="Widget",
            stock_minimo=-1,
        )
    assert session.llamadas == []


async def test_crear_producto_flush_una_vez(make_session):
    fila = _producto_row()
    session = make_session([[], [fila]])
    await crear_producto(session, tenant_id=uuid4(), user_id=uuid4(), sku="X1", nombre="Widget")
    assert session.flushes == 1


# ---------------------------------------------------------------------------
# editar_producto
# ---------------------------------------------------------------------------


async def test_editar_producto_solo_toca_los_campos_presentes(make_session):
    fila = _producto_row(nombre="Widget Pro")
    session = make_session([[fila]])
    await editar_producto(session, tenant_id=uuid4(), product_id=uuid4(), nombre="Widget Pro")
    sql, params = session.llamadas[0]
    assert "nombre = :nombre" in sql
    assert "descripcion = :descripcion" not in sql
    assert "precio = :precio" not in sql
    assert "updated_at = now()" in sql
    assert params["nombre"] == "Widget Pro"


async def test_editar_producto_sin_cambios_igual_ejecuta_un_update(make_session):
    fila = _producto_row()
    session = make_session([[fila]])
    resultado = await editar_producto(session, tenant_id=uuid4(), product_id=uuid4())
    assert resultado is not None
    sql, _ = session.llamadas[0]
    assert sql.count("SET") == 1
    assert "updated_at = now()" in sql
    assert len(session.llamadas) == 1


async def test_editar_producto_nombre_vacio_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="nombre"):
        await editar_producto(session, tenant_id=uuid4(), product_id=uuid4(), nombre="   ")
    assert session.llamadas == []


async def test_editar_producto_precio_negativo_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="precio"):
        await editar_producto(session, tenant_id=uuid4(), product_id=uuid4(), precio=-5)
    assert session.llamadas == []


async def test_editar_producto_costo_negativo_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="costo"):
        await editar_producto(session, tenant_id=uuid4(), product_id=uuid4(), costo=-5)
    assert session.llamadas == []


async def test_editar_producto_precio_none_limpia_el_campo(make_session):
    """`precio`/`costo` son nullable: pasar explícitamente `None` debe LIMPIAR el campo
    (columna a NULL), no ser tratado como "campo no presente" — esa distinción ya la resuelve
    el llamador (`**cambios` solo trae la clave si de verdad vino en la request)."""
    fila = _producto_row(precio=None)
    session = make_session([[fila]])
    await editar_producto(session, tenant_id=uuid4(), product_id=uuid4(), precio=None)
    sql, params = session.llamadas[0]
    assert "precio = :precio" in sql
    assert params["precio"] is None


async def test_editar_producto_stock_minimo_negativo_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="stock_minimo"):
        await editar_producto(session, tenant_id=uuid4(), product_id=uuid4(), stock_minimo=-1)
    assert session.llamadas == []


async def test_editar_producto_activo_none_rechaza(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="activo"):
        await editar_producto(session, tenant_id=uuid4(), product_id=uuid4(), activo=None)
    assert session.llamadas == []


async def test_editar_producto_unidad_vacia_cae_a_default(make_session):
    fila = _producto_row(unidad="unidad")
    session = make_session([[fila]])
    await editar_producto(session, tenant_id=uuid4(), product_id=uuid4(), unidad="   ")
    _, params = session.llamadas[0]
    assert params["unidad"] == "unidad"


async def test_editar_producto_no_existente_retorna_none(make_session):
    session = make_session([[]])
    resultado = await editar_producto(session, tenant_id=uuid4(), product_id=uuid4(), nombre="X")
    assert resultado is None


async def test_editar_producto_ignora_claves_desconocidas(make_session):
    """`sku`/`stock`/cualquier otra clave fuera de los campos editables se ignora en
    silencio — nunca aparece en el SQL ni revienta."""
    fila = _producto_row()
    session = make_session([[fila]])
    await editar_producto(
        session, tenant_id=uuid4(), product_id=uuid4(), sku="OTRO", stock=Decimal("999")
    )
    sql, params = session.llamadas[0]
    assert "sku" not in params
    assert "stock =" not in sql or "stock_minimo" in sql  # no debe tocar `stock` (sin sufijo)


# ---------------------------------------------------------------------------
# desactivar_producto
# ---------------------------------------------------------------------------


async def test_desactivar_producto_setea_activo_false(make_session):
    fila = _producto_row(activo=False)
    session = make_session([[fila]])
    resultado = await desactivar_producto(session, tenant_id=uuid4(), product_id=uuid4())
    assert resultado["activo"] is False
    sql, params = session.llamadas[0]
    assert "activo = :activo" in sql
    assert params["activo"] is False


async def test_desactivar_producto_no_existente_retorna_none(make_session):
    session = make_session([[]])
    resultado = await desactivar_producto(session, tenant_id=uuid4(), product_id=uuid4())
    assert resultado is None


# ---------------------------------------------------------------------------
# listar_productos
# ---------------------------------------------------------------------------


async def test_listar_productos_sin_filtro(make_session):
    filas = [_producto_row(sku="A"), _producto_row(sku="B")]
    session = make_session([filas])
    productos = await listar_productos(session, tenant_id=uuid4())
    assert len(productos) == 2
    sql, params = session.llamadas[0]
    assert "activo" not in params
    assert "patron" not in params


async def test_listar_productos_filtra_por_activo(make_session):
    session = make_session([[_producto_row(activo=True)]])
    await listar_productos(session, tenant_id=uuid4(), activo=True)
    sql, params = session.llamadas[0]
    assert "activo = :activo" in sql
    assert params["activo"] is True


async def test_listar_productos_filtra_por_texto(make_session):
    session = make_session([[_producto_row()]])
    await listar_productos(session, tenant_id=uuid4(), q="widget")
    sql, params = session.llamadas[0]
    assert "sku ILIKE :patron" in sql
    assert "nombre ILIKE :patron" in sql
    assert params["patron"] == "%widget%"


async def test_listar_productos_combina_activo_y_texto(make_session):
    session = make_session([[]])
    await listar_productos(session, tenant_id=uuid4(), activo=False, q="abc")
    sql, params = session.llamadas[0]
    assert "activo = :activo" in sql
    assert "ILIKE :patron" in sql
    assert params["activo"] is False
    assert params["patron"] == "%abc%"


# ---------------------------------------------------------------------------
# obtener_producto_por_sku
# ---------------------------------------------------------------------------


async def test_obtener_producto_por_sku_normaliza_y_encuentra(make_session):
    fila = _producto_row(sku="ABC-001")
    session = make_session([[fila]])
    producto = await obtener_producto_por_sku(session, tenant_id=uuid4(), sku="  abc-001 ")
    assert producto["sku"] == "ABC-001"
    _, params = session.llamadas[0]
    assert params["sku"] == "ABC-001"


async def test_obtener_producto_por_sku_no_encontrado_retorna_none(make_session):
    session = make_session([[]])
    producto = await obtener_producto_por_sku(session, tenant_id=uuid4(), sku="NOPE")
    assert producto is None


# ---------------------------------------------------------------------------
# registrar_movimiento
# ---------------------------------------------------------------------------


async def test_registrar_movimiento_entrada_incrementa_stock(make_session):
    product_id, user_id, tenant_id = uuid4(), uuid4(), uuid4()
    producto_actual = _producto_row(id=product_id, stock=Decimal("10"))
    move_row = {"id": uuid4(), "delta": Decimal("5"), "motivo": "compra"}
    producto_actualizado = _producto_row(id=product_id, stock=Decimal("15"))
    session = make_session([[producto_actual], [move_row], [producto_actualizado]])

    resultado = await registrar_movimiento(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        product_id=product_id,
        delta=5,
        motivo="compra",
    )

    assert resultado["producto"]["stock"] == Decimal("15")
    sql_insert, params_insert = session.llamadas[1]
    assert "INSERT INTO stock_moves" in sql_insert
    assert params_insert["delta"] == Decimal("5")
    assert params_insert["motivo"] == "compra"
    assert params_insert["user_id"] == str(user_id)
    assert params_insert["tenant_id"] == str(tenant_id)
    sql_update, params_update = session.llamadas[2]
    assert "stock = stock + :delta" in sql_update
    assert session.flushes == 1


async def test_registrar_movimiento_select_inicial_usa_for_update(make_session):
    """El `SELECT` de `products` que alimenta el chequeo de `StockInsuficienteError` debe
    tomar el lock de fila (`FOR UPDATE`) ANTES de calcular `nuevo_stock` — si no, dos
    llamadas concurrentes sobre el mismo producto podrían leer el mismo `stock` obsoleto y
    ambas pasar el chequeo contra un valor que ya no es el real (ver el docstring de
    `registrar_movimiento`)."""
    product_id = uuid4()
    producto_actual = _producto_row(id=product_id, stock=Decimal("10"))
    move_row = {"id": uuid4()}
    producto_actualizado = _producto_row(id=product_id, stock=Decimal("15"))
    session = make_session([[producto_actual], [move_row], [producto_actualizado]])

    await registrar_movimiento(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        product_id=product_id,
        delta=5,
        motivo="compra",
    )

    sql_select, _ = session.llamadas[0]
    assert "FOR UPDATE" in sql_select


async def test_registrar_movimiento_salida_dentro_del_stock_disponible(make_session):
    product_id = uuid4()
    producto_actual = _producto_row(id=product_id, stock=Decimal("10"))
    move_row = {"id": uuid4()}
    producto_actualizado = _producto_row(id=product_id, stock=Decimal("7"))
    session = make_session([[producto_actual], [move_row], [producto_actualizado]])

    resultado = await registrar_movimiento(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        product_id=product_id,
        delta=-3,
        motivo="venta",
    )
    assert resultado["producto"]["stock"] == Decimal("7")


async def test_registrar_movimiento_venta_que_dejaria_stock_negativo_rechaza(make_session):
    product_id = uuid4()
    producto_actual = _producto_row(id=product_id, stock=Decimal("2"))
    session = make_session([[producto_actual]])

    with pytest.raises(StockInsuficienteError, match="ajuste"):
        await registrar_movimiento(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            product_id=product_id,
            delta=-5,
            motivo="venta",
        )
    # nunca llegó a insertar el movimiento ni a actualizar el stock.
    assert len(session.llamadas) == 1


async def test_registrar_movimiento_merma_que_dejaria_stock_negativo_rechaza(make_session):
    product_id = uuid4()
    producto_actual = _producto_row(id=product_id, stock=Decimal("1"))
    session = make_session([[producto_actual]])
    with pytest.raises(StockInsuficienteError):
        await registrar_movimiento(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            product_id=product_id,
            delta=-2,
            motivo="merma",
        )


async def test_registrar_movimiento_ajuste_permite_stock_negativo(make_session):
    product_id = uuid4()
    producto_actual = _producto_row(id=product_id, stock=Decimal("2"))
    move_row = {"id": uuid4()}
    producto_actualizado = _producto_row(id=product_id, stock=Decimal("-3"))
    session = make_session([[producto_actual], [move_row], [producto_actualizado]])

    resultado = await registrar_movimiento(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        product_id=product_id,
        delta=-5,
        motivo="ajuste",
    )
    assert resultado["producto"]["stock"] == Decimal("-3")


async def test_registrar_movimiento_motivo_invalido_rechaza_sin_tocar_sesion(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="motivo"):
        await registrar_movimiento(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            product_id=uuid4(),
            delta=1,
            motivo="no-es-un-motivo",
        )
    assert session.llamadas == []


async def test_registrar_movimiento_delta_cero_rechaza_sin_tocar_sesion(make_session):
    session = make_session()
    with pytest.raises(ValueError, match="delta"):
        await registrar_movimiento(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            product_id=uuid4(),
            delta=0,
            motivo="ajuste",
        )
    assert session.llamadas == []


async def test_registrar_movimiento_producto_inexistente_retorna_none(make_session):
    session = make_session([[]])
    resultado = await registrar_movimiento(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        product_id=uuid4(),
        delta=5,
        motivo="compra",
    )
    assert resultado is None
    assert len(session.llamadas) == 1  # no intenta insertar el movimiento


async def test_registrar_movimiento_normaliza_motivo_a_minusculas(make_session):
    product_id = uuid4()
    producto_actual = _producto_row(id=product_id, stock=Decimal("10"))
    move_row = {"id": uuid4()}
    producto_actualizado = _producto_row(id=product_id, stock=Decimal("11"))
    session = make_session([[producto_actual], [move_row], [producto_actualizado]])
    await registrar_movimiento(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        product_id=product_id,
        delta=1,
        motivo="COMPRA",
    )
    _, params_insert = session.llamadas[1]
    assert params_insert["motivo"] == "compra"


# ---------------------------------------------------------------------------
# resumen_inventario
# ---------------------------------------------------------------------------


async def test_resumen_inventario_totales_y_valorizacion(make_session):
    filas = [
        _producto_row(sku="A", stock=Decimal("10"), costo=Decimal("2.00"), precio=Decimal("5.00")),
        _producto_row(sku="B", stock=Decimal("4"), costo=Decimal("1.50"), precio=Decimal("3.00")),
    ]
    session = make_session([filas])
    resumen = await resumen_inventario(session, tenant_id=uuid4())
    assert resumen["total_skus"] == 2
    # 10*2.00 + 4*1.50 = 26.00 ; 10*5.00 + 4*3.00 = 62.00
    assert resumen["valor_costo"] == pytest.approx(26.00)
    assert resumen["valor_precio"] == pytest.approx(62.00)
    sql, params = session.llamadas[0]
    assert "activo = true" in sql
    assert params["tenant_id"]


async def test_resumen_inventario_ignora_costo_o_precio_ausente(make_session):
    filas = [_producto_row(sku="A", stock=Decimal("10"), costo=None, precio=None)]
    session = make_session([filas])
    resumen = await resumen_inventario(session, tenant_id=uuid4())
    assert resumen["valor_costo"] == 0.0
    assert resumen["valor_precio"] == 0.0


async def test_resumen_inventario_stock_bajo_incluye_empatados_en_el_minimo(make_session):
    filas = [
        _producto_row(sku="BAJO", stock=Decimal("2"), stock_minimo=Decimal("5")),
        _producto_row(sku="EMPATADO", stock=Decimal("5"), stock_minimo=Decimal("5")),
        _producto_row(sku="OK", stock=Decimal("50"), stock_minimo=Decimal("5")),
    ]
    session = make_session([filas])
    resumen = await resumen_inventario(session, tenant_id=uuid4())
    skus_bajos = {p["sku"] for p in resumen["stock_bajo"]}
    assert skus_bajos == {"BAJO", "EMPATADO"}


async def test_resumen_inventario_sin_productos_es_todo_cero(make_session):
    session = make_session([[]])
    resumen = await resumen_inventario(session, tenant_id=uuid4())
    assert resumen == {
        "total_skus": 0,
        "valor_costo": 0.0,
        "valor_precio": 0.0,
        "stock_bajo": [],
    }
