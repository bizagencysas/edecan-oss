"""Tests de `edecan_commerce.budgets`: fijar/listar/estado."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from edecan_commerce.budgets import estado_presupuestos, fijar_presupuesto, listar_presupuestos


async def test_fijar_presupuesto_inserta_cuando_no_existe(make_session):
    tenant_id, user_id = uuid4(), uuid4()
    fila_insertada = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "categoria": "comida",
        "monto_mensual": Decimal("500.00"),
        "moneda": "USD",
    }
    session = make_session([[], [fila_insertada]])  # SELECT existing (vacío) + INSERT RETURNING

    resultado = await fijar_presupuesto(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        categoria="comida",
        monto_mensual=Decimal("500.00"),
    )

    assert resultado["categoria"] == "comida"
    sql_insert, params_insert = session.llamadas[1]
    assert "INSERT INTO budgets" in sql_insert
    assert params_insert["categoria"] == "comida"
    assert params_insert["monto_mensual"] == Decimal("500.00")
    assert params_insert["moneda"] == "USD"


async def test_fijar_presupuesto_actualiza_cuando_ya_existe(make_session):
    tenant_id, user_id = uuid4(), uuid4()
    existing_id = uuid4()
    fila_actualizada = {
        "id": existing_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "categoria": "comida",
        "monto_mensual": Decimal("700.00"),
        "moneda": "USD",
    }
    session = make_session([[{"id": existing_id}], [fila_actualizada]])

    resultado = await fijar_presupuesto(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        categoria="comida",
        monto_mensual=Decimal("700.00"),
    )

    assert resultado["monto_mensual"] == Decimal("700.00")
    sql_update, params_update = session.llamadas[1]
    assert "UPDATE budgets" in sql_update
    assert params_update["id"] == str(existing_id)


async def test_fijar_presupuesto_rechaza_categoria_vacia(make_session):
    session = make_session([])
    with pytest.raises(ValueError, match="categoria"):
        await fijar_presupuesto(
            session, tenant_id=uuid4(), user_id=uuid4(), categoria="  ", monto_mensual=Decimal("10")
        )
    assert session.llamadas == []


async def test_fijar_presupuesto_rechaza_monto_no_positivo(make_session):
    session = make_session([])
    with pytest.raises(ValueError, match="monto_mensual"):
        await fijar_presupuesto(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            categoria="comida",
            monto_mensual=Decimal("0"),
        )
    assert session.llamadas == []


async def test_fijar_presupuesto_moneda_invalida_cae_a_usd(make_session):
    tenant_id, user_id = uuid4(), uuid4()
    fila = {"id": uuid4(), "categoria": "ocio", "monto_mensual": Decimal("100"), "moneda": "USD"}
    session = make_session([[], [fila]])

    await fijar_presupuesto(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        categoria="ocio",
        monto_mensual=Decimal("100"),
        moneda="no-valida",
    )

    _, params_insert = session.llamadas[1]
    assert params_insert["moneda"] == "USD"


async def test_listar_presupuestos_devuelve_filas(make_session):
    tenant_id, user_id = uuid4(), uuid4()
    filas = [
        {"id": uuid4(), "categoria": "comida", "monto_mensual": Decimal("500")},
        {"id": uuid4(), "categoria": "transporte", "monto_mensual": Decimal("200")},
    ]
    session = make_session([filas])

    resultado = await listar_presupuestos(session, tenant_id=tenant_id, user_id=user_id)

    assert len(resultado) == 2
    sql, params = session.llamadas[0]
    assert "SELECT * FROM budgets" in sql
    assert params["tenant_id"] == str(tenant_id)


async def test_estado_presupuestos_calcula_pct_y_alerta_sobre_90(make_session):
    tenant_id, user_id = uuid4(), uuid4()
    filas = [
        {
            "id": uuid4(),
            "categoria": "comida",
            "monto_mensual": Decimal("500.00"),
            "moneda": "USD",
            "gastado": Decimal("475.00"),  # 95%
        },
        {
            "id": uuid4(),
            "categoria": "transporte",
            "monto_mensual": Decimal("200.00"),
            "moneda": "USD",
            "gastado": Decimal("50.00"),  # 25%
        },
    ]
    session = make_session([filas])

    estado = await estado_presupuestos(session, tenant_id=tenant_id, user_id=user_id, mes="2026-06")

    comida = next(e for e in estado if e["categoria"] == "comida")
    transporte = next(e for e in estado if e["categoria"] == "transporte")
    assert comida["pct"] == 95.0
    assert comida["alerta"] is True
    assert transporte["pct"] == 25.0
    assert transporte["alerta"] is False

    sql, params = session.llamadas[0]
    assert "LEFT JOIN" in sql
    assert params["mes"] == "2026-06"


async def test_estado_presupuestos_categoria_sin_gasto_da_cero(make_session):
    tenant_id, user_id = uuid4(), uuid4()
    filas = [
        {
            "id": uuid4(),
            "categoria": "salud",
            "monto_mensual": Decimal("300.00"),
            "moneda": "USD",
            "gastado": Decimal("0"),
        }
    ]
    session = make_session([filas])

    estado = await estado_presupuestos(session, tenant_id=tenant_id, user_id=user_id)

    assert estado[0]["gastado"] == 0.0
    assert estado[0]["pct"] == 0.0
    assert estado[0]["alerta"] is False


async def test_estado_presupuestos_usa_mes_actual_por_defecto(make_session):
    session = make_session([[]])
    await estado_presupuestos(session, tenant_id=uuid4(), user_id=uuid4())
    assert len(session.llamadas) == 1
