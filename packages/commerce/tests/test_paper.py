"""Tests de `edecan_commerce.paper.PaperBroker` (`ROADMAP_V2.md` §8.1, guardrail permanente).

Ejercita `execute(order, session)` directo (sin HTTP, sin Postgres real) con `FakeSession`
de `conftest.py` — cada test verifica tanto el resultado como, para los casos delicados
(venta insuficiente), que NO se ejecutó ninguna sentencia SQL de más (nunca toca
`holdings`/`transactions`/`audit_log` si la orden no se puede ejecutar).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from edecan_commerce.paper import PaperBroker


def _make_order(**overrides):
    base = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "user_id": uuid4(),
        "kind": "trade",
        "status": "confirmed",
        "descripcion": "Compra de 0.5 BTC",
        "monto": Decimal("30000.00"),
        "moneda": "USD",
        "simbolo": "BTC",
        "lado": "buy",
        "cantidad": Decimal("0.5"),
        "meta": {"cotizacion": {"precio": 60000.0, "moneda": "USD", "fuente": "stub"}},
    }
    base.update(overrides)
    return base


def _updated_order_row(order, **overrides):
    row = dict(order)
    row["status"] = "executed_paper"
    row["executed_at"] = "2026-01-01T00:00:00Z"
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Validación de entrada — nunca toca la sesión si la orden no es ejecutable.
# ---------------------------------------------------------------------------


async def test_execute_rechaza_orden_que_no_esta_confirmed(make_session):
    session = make_session([])
    order = _make_order(status="draft")

    with pytest.raises(ValueError, match="estado 'confirmed'"):
        await PaperBroker().execute(order, session)
    assert session.llamadas == []


async def test_execute_rechaza_orden_que_no_es_trade(make_session):
    session = make_session([])
    order = _make_order(kind="payment")

    with pytest.raises(ValueError, match="kind='trade'"):
        await PaperBroker().execute(order, session)
    assert session.llamadas == []


async def test_execute_rechaza_orden_sin_simbolo(make_session):
    session = make_session([])
    order = _make_order(simbolo="")

    with pytest.raises(ValueError, match="simbolo"):
        await PaperBroker().execute(order, session)
    assert session.llamadas == []


async def test_execute_rechaza_lado_invalido(make_session):
    session = make_session([])
    order = _make_order(lado="hold")

    with pytest.raises(ValueError, match="lado"):
        await PaperBroker().execute(order, session)
    assert session.llamadas == []


async def test_execute_rechaza_orden_sin_cotizacion_adjunta(make_session):
    session = make_session([])
    order = _make_order(meta={})

    with pytest.raises(ValueError, match="cotización adjunta"):
        await PaperBroker().execute(order, session)
    assert session.llamadas == []


# ---------------------------------------------------------------------------
# Compra: sin holding previo y con holding previo (costo promedio ponderado).
# ---------------------------------------------------------------------------


async def test_execute_compra_sin_holding_previo_inserta_holding_nuevo(make_session):
    order = _make_order(lado="buy", cantidad=Decimal("0.5"))
    session = make_session(
        [
            [],  # _get_holding: sin fila previa
            [],  # _upsert_holding (INSERT): sin RETURNING, no se usa
            [_updated_order_row(order)],  # _mark_executed RETURNING *
        ]
    )

    resultado = await PaperBroker().execute(order, session)

    assert resultado["status"] == "executed_paper"

    sql_holding, params_holding = session.llamadas[1]
    assert "INSERT INTO holdings" in sql_holding
    assert params_holding["cantidad"] == Decimal("0.5")
    assert params_holding["costo_promedio"] == Decimal("60000.0000")  # sin holding previo = precio

    sql_tx, params_tx = session.llamadas[3]
    assert "INSERT INTO transactions" in sql_tx
    assert params_tx["monto"] == Decimal("-30000.00")  # compra: negativo
    assert params_tx["categoria"] == "inversion_paper"
    assert params_tx["cuenta"] == "paper"

    sql_audit, params_audit = session.llamadas[4]
    assert "INSERT INTO audit_log" in sql_audit
    assert "commerce.order.executed_paper" in sql_audit
    assert params_audit["target"] == str(order["id"])
    assert '"lado": "buy"' in params_audit["meta"]


async def test_execute_compra_con_holding_previo_pondera_costo_promedio(make_session):
    order = _make_order(lado="buy", cantidad=Decimal("1"))
    order["meta"] = {"cotizacion": {"precio": 100.0, "moneda": "USD"}}
    holding_previo = {
        "id": uuid4(),
        "cantidad": Decimal("1"),
        "costo_promedio": Decimal("50.0000"),
        "moneda": "USD",
    }
    session = make_session(
        [
            [holding_previo],  # _get_holding
            [],  # _upsert_holding (UPDATE)
            [_updated_order_row(order)],  # _mark_executed
        ]
    )

    await PaperBroker().execute(order, session)

    sql_holding, params_holding = session.llamadas[1]
    assert "UPDATE holdings" in sql_holding
    assert params_holding["id"] == str(holding_previo["id"])
    # (1*50 + 1*100) / 2 = 75
    assert params_holding["costo_promedio"] == Decimal("75.0000")
    assert params_holding["cantidad"] == Decimal("2")


# ---------------------------------------------------------------------------
# Venta: cantidad suficiente vs. insuficiente.
# ---------------------------------------------------------------------------


async def test_execute_venta_con_cantidad_suficiente_reduce_holding_y_acredita(make_session):
    order = _make_order(lado="sell", cantidad=Decimal("0.5"))
    holding_previo = {
        "id": uuid4(),
        "cantidad": Decimal("2"),
        "costo_promedio": Decimal("40000.0000"),
        "moneda": "USD",
    }
    session = make_session(
        [
            [holding_previo],
            [],
            [_updated_order_row(order)],
        ]
    )

    await PaperBroker().execute(order, session)

    sql_holding, params_holding = session.llamadas[1]
    assert "UPDATE holdings" in sql_holding
    assert params_holding["cantidad"] == Decimal("1.5")
    # Vender no cambia el costo promedio.
    assert params_holding["costo_promedio"] == Decimal("40000.0000")

    _, params_tx = session.llamadas[3]
    assert params_tx["monto"] == Decimal("30000.00")  # venta: positivo


async def test_execute_venta_con_cantidad_insuficiente_falla_sin_tocar_holdings_ni_transacciones(
    make_session,
):
    order = _make_order(lado="sell", cantidad=Decimal("5"))
    holding_previo = {
        "id": uuid4(),
        "cantidad": Decimal("1"),
        "costo_promedio": Decimal("40000.0000"),
        "moneda": "USD",
    }
    session = make_session(
        [
            [holding_previo],  # _get_holding
            [],  # _mark_error (UPDATE orders, sin RETURNING)
        ]
    )

    with pytest.raises(ValueError, match="No hay suficiente BTC"):
        await PaperBroker().execute(order, session)

    assert len(session.llamadas) == 2
    sql_get, _ = session.llamadas[0]
    assert "SELECT * FROM holdings" in sql_get
    sql_error, params_error = session.llamadas[1]
    assert "UPDATE orders SET meta = meta ||" in sql_error
    assert "No hay suficiente BTC" in params_error["extra"]
    # Nunca se llegó a tocar holdings/transactions/audit_log.
    assert not any("holdings SET" in sql or "INTO holdings" in sql for sql, _ in session.llamadas)
    assert not any("transactions" in sql for sql, _ in session.llamadas)
    assert not any("audit_log" in sql for sql, _ in session.llamadas)


async def test_execute_venta_agota_holding_resetea_costo_promedio_a_cero(make_session):
    order = _make_order(lado="sell", cantidad=Decimal("1"))
    holding_previo = {
        "id": uuid4(),
        "cantidad": Decimal("1"),
        "costo_promedio": Decimal("40000.0000"),
        "moneda": "USD",
    }
    session = make_session([[holding_previo], [], [_updated_order_row(order)]])

    await PaperBroker().execute(order, session)

    _, params_holding = session.llamadas[1]
    assert params_holding["cantidad"] == Decimal("0")
    assert params_holding["costo_promedio"] == Decimal("0")
