"""Tests de `edecan_toolkit.finanzas`: `registrar_transaccion` y `resumen_finanzas`."""

from __future__ import annotations

from decimal import Decimal

from edecan_toolkit.finanzas import RegistrarTransaccionTool, ResumenFinanzasTool


async def test_registrar_transaccion_inserta_con_moneda_default_usd(make_ctx, make_session):
    session = make_session([[{"id": "tx-1"}]])
    ctx = make_ctx(session=session)

    resultado = await RegistrarTransaccionTool().run(
        ctx, {"monto": -45.5, "categoria": "comida", "descripcion": "Almuerzo"}
    )

    assert resultado.data["moneda"] == "USD"
    assert resultado.data["categoria"] == "comida"
    assert "gasto" in resultado.content.lower()
    sql, params = session.llamadas[0]
    assert "INSERT INTO transactions" in sql
    assert params["moneda"] == "USD"
    assert params["monto"] == Decimal("-45.5")


async def test_registrar_transaccion_moneda_explicita_se_normaliza_a_mayusculas(
    make_ctx, make_session
):
    session = make_session([[{"id": "tx-2"}]])
    ctx = make_ctx(session=session)

    resultado = await RegistrarTransaccionTool().run(
        ctx, {"monto": 100, "categoria": "salario", "moneda": "mxn"}
    )

    assert resultado.data["moneda"] == "MXN"
    assert "ingreso" in resultado.content.lower()


async def test_registrar_transaccion_moneda_invalida_cae_a_default(make_ctx, make_session):
    session = make_session([[{"id": "tx-3"}]])
    ctx = make_ctx(session=session)

    resultado = await RegistrarTransaccionTool().run(
        ctx, {"monto": 10, "categoria": "otros", "moneda": "no-valida"}
    )

    assert resultado.data["moneda"] == "USD"


async def test_registrar_transaccion_monto_invalido_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    ctx = make_ctx(session=session)

    resultado = await RegistrarTransaccionTool().run(
        ctx, {"monto": "no-es-numero", "categoria": "x"}
    )

    assert "no es un monto válido" in resultado.content
    assert session.llamadas == []


async def test_resumen_finanzas_agrupa_por_categoria_y_calcula_totales(make_ctx, make_session):
    filas = [
        {"categoria": "salario", "total": Decimal("2000.00"), "n": 1},
        {"categoria": "comida", "total": Decimal("-300.00"), "n": 5},
        {"categoria": "transporte", "total": Decimal("-150.00"), "n": 3},
        {"categoria": "ocio", "total": Decimal("-50.00"), "n": 2},
        {"categoria": "servicios", "total": Decimal("-80.00"), "n": 2},
        {"categoria": "salud", "total": Decimal("-20.00"), "n": 1},
        {"categoria": "otros", "total": Decimal("-5.00"), "n": 1},
    ]
    session = make_session([filas])
    ctx = make_ctx(session=session)

    resultado = await ResumenFinanzasTool().run(ctx, {"mes": "2026-06"})

    assert resultado.data["ingresos"] == 2000.0
    assert resultado.data["gastos"] == -605.0
    assert resultado.data["balance"] == 1395.0
    assert len(resultado.data["por_categoria"]) == 7
    # Top categorías de gasto: máximo 5, ordenadas de mayor a menor gasto absoluto.
    assert "comida" in resultado.content
    assert "Top categorías de gasto" in resultado.content
    lineas_top = [
        linea for linea in resultado.content.splitlines() if "—" in linea and "$" in linea
    ]
    assert len(lineas_top) <= 5

    sql, params = session.llamadas[0]
    assert "GROUP BY categoria" in sql
    assert params["inicio"].isoformat() == "2026-06-01"
    assert params["fin"].isoformat() == "2026-07-01"


async def test_resumen_finanzas_diciembre_calcula_bien_el_fin_de_rango(make_ctx, make_session):
    session = make_session([[]])
    ctx = make_ctx(session=session)

    resultado = await ResumenFinanzasTool().run(ctx, {"mes": "2025-12"})

    assert "No hay transacciones" in resultado.content
    _, params = session.llamadas[0]
    assert params["inicio"].isoformat() == "2025-12-01"
    assert params["fin"].isoformat() == "2026-01-01"


async def test_resumen_finanzas_mes_invalido_no_toca_la_sesion(make_ctx, make_session):
    session = make_session([])
    ctx = make_ctx(session=session)

    resultado = await ResumenFinanzasTool().run(ctx, {"mes": "no-es-un-mes"})

    assert "no es un mes válido" in resultado.content
    assert session.llamadas == []


async def test_resumen_finanzas_usa_mes_actual_por_defecto(make_ctx, make_session):
    session = make_session([[]])
    ctx = make_ctx(session=session)

    await ResumenFinanzasTool().run(ctx, {})

    assert len(session.llamadas) == 1
