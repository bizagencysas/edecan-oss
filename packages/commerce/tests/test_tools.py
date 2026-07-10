"""Tests de `edecan_commerce.tools`: las 4 herramientas del paquete.

`test_preparar_pago_y_preparar_orden_nunca_ejecutan_solo_crean_draft` es el test explícito
que pide el paquete de trabajo: prueba, mirando exactamente qué SQL se ejecutó, que ninguna
de las dos tools "dangerous" hace NADA más que insertar un borrador.
"""

from __future__ import annotations

from decimal import Decimal

from edecan_commerce.tools import (
    CotizarActivoTool,
    GestionarPresupuestoTool,
    PrepararOrdenTool,
    PrepararPagoTool,
)

# ---------------------------------------------------------------------------
# cotizar_activo
# ---------------------------------------------------------------------------


async def test_cotizar_activo_devuelve_precio_y_aviso(make_ctx):
    ctx = make_ctx()
    resultado = await CotizarActivoTool().run(ctx, {"simbolo": "btc"})

    assert resultado.data["simbolo"] == "BTC"
    assert resultado.data["precio"] > 0
    assert "no consejo de inversión" in resultado.content


async def test_cotizar_activo_sin_simbolo_no_falla(make_ctx):
    ctx = make_ctx()
    resultado = await CotizarActivoTool().run(ctx, {})
    assert "símbolo" in resultado.content.lower()


# ---------------------------------------------------------------------------
# gestionar_presupuesto
# ---------------------------------------------------------------------------


async def test_gestionar_presupuesto_fijar_crea_presupuesto(make_ctx, make_session):
    fila = {"id": "b-1", "categoria": "comida", "monto_mensual": Decimal("500"), "moneda": "USD"}
    session = make_session([[], [fila]])
    ctx = make_ctx(session=session)

    resultado = await GestionarPresupuestoTool().run(
        ctx, {"accion": "fijar", "categoria": "comida", "monto": 500}
    )

    assert "fijado" in resultado.content
    assert resultado.data["categoria"] == "comida"


async def test_gestionar_presupuesto_fijar_sin_categoria_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    ctx = make_ctx(session=session)

    resultado = await GestionarPresupuestoTool().run(ctx, {"accion": "fijar", "monto": 100})

    assert "categoría" in resultado.content
    assert session.llamadas == []


async def test_gestionar_presupuesto_listar_vacio(make_ctx, make_session):
    session = make_session([[]])
    ctx = make_ctx(session=session)

    resultado = await GestionarPresupuestoTool().run(ctx, {"accion": "listar"})

    assert "no tienes presupuestos" in resultado.content.lower()
    assert resultado.data["presupuestos"] == []


async def test_gestionar_presupuesto_estado_con_alerta(make_ctx, make_session):
    filas = [
        {
            "id": "b-1",
            "categoria": "comida",
            "monto_mensual": Decimal("100.00"),
            "moneda": "USD",
            "gastado": Decimal("95.00"),
        }
    ]
    session = make_session([filas])
    ctx = make_ctx(session=session)

    resultado = await GestionarPresupuestoTool().run(ctx, {"accion": "estado"})

    assert "cerca del límite" in resultado.content
    assert resultado.data["estado"][0]["alerta"] is True


async def test_gestionar_presupuesto_accion_invalida(make_ctx, make_session):
    session = make_session([])
    ctx = make_ctx(session=session)

    resultado = await GestionarPresupuestoTool().run(ctx, {"accion": "borrar"})

    assert "fijar" in resultado.content and "listar" in resultado.content
    assert session.llamadas == []


# ---------------------------------------------------------------------------
# preparar_pago / preparar_orden — validaciones
# ---------------------------------------------------------------------------


async def test_preparar_pago_sin_descripcion_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    ctx = make_ctx(session=session)

    resultado = await PrepararPagoTool().run(ctx, {"descripcion": "  ", "monto": 100})

    assert "descripción" in resultado.content
    assert session.llamadas == []


async def test_preparar_pago_monto_no_positivo_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    ctx = make_ctx(session=session)

    resultado = await PrepararPagoTool().run(ctx, {"descripcion": "Renta", "monto": -5})

    assert "mayor que cero" in resultado.content
    assert session.llamadas == []


async def test_preparar_orden_lado_invalido_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    ctx = make_ctx(session=session)

    resultado = await PrepararOrdenTool().run(
        ctx, {"simbolo": "BTC", "lado": "hold", "cantidad": 1}
    )

    assert "'buy' o 'sell'" in resultado.content
    assert session.llamadas == []


async def test_preparar_orden_cantidad_invalida_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    ctx = make_ctx(session=session)

    resultado = await PrepararOrdenTool().run(
        ctx, {"simbolo": "BTC", "lado": "buy", "cantidad": "no-es-numero"}
    )

    assert "cantidad válida" in resultado.content
    assert session.llamadas == []


# ---------------------------------------------------------------------------
# El test explícito que pide el paquete de trabajo.
# ---------------------------------------------------------------------------


async def test_draft_never_executes_preparar_pago_y_preparar_orden(
    make_ctx, make_session
):
    session = make_session([[{"id": "order-1"}], [{"id": "order-2"}]])
    ctx = make_ctx(session=session)

    pago = await PrepararPagoTool().run(
        ctx, {"descripcion": "Factura de luz", "monto": 250, "beneficiario": "CFE"}
    )
    orden = await PrepararOrdenTool().run(ctx, {"simbolo": "BTC", "lado": "buy", "cantidad": 0.1})

    assert pago.data["order_id"] == "order-1"
    assert orden.data["order_id"] == "order-2"
    assert "NO se ha movido dinero" in pago.content
    assert "NO se ha ejecutado" in orden.content

    # La ÚNICA acción sobre la base de datos, en ambos casos, es un INSERT con status='draft'.
    assert len(session.llamadas) == 2
    for sql, params in session.llamadas:
        assert "INSERT INTO orders" in sql
        assert "'draft'" in sql
        assert params["kind"] in ("payment", "trade")
    # Ninguna palabra clave de ejecución aparece en ningún SQL ejecutado.
    ejecutado = " ".join(sql for sql, _ in session.llamadas).lower()
    for palabra_prohibida in ("update", "executed_paper", "holdings", "audit_log", "confirmed"):
        assert palabra_prohibida not in ejecutado


async def test_preparar_orden_adjunta_cotizacion_en_meta(make_ctx, make_session):
    session = make_session([[{"id": "order-3"}]])
    ctx = make_ctx(session=session)

    resultado = await PrepararOrdenTool().run(
        ctx, {"simbolo": "eth", "lado": "sell", "cantidad": 2}
    )

    assert resultado.data["cotizacion"]["fuente"] == "stub"
    sql, params = session.llamadas[0]
    assert params["simbolo"] == "ETH"
    assert params["lado"] == "sell"
    assert '"cotizacion"' in params["meta"]
