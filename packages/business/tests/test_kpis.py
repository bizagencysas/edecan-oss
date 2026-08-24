"""Tests de `edecan_business.kpis.kpis_mes` — ingresos/gastos/beneficio, nuevos clientes,
facturado/cobrado (sin doble conteo con ingresos), la dona "top 6 + otros" y la actividad
reciente mezclada/ordenada. `FakeSession` programable con una respuesta por cada una de las
6 consultas que hace `kpis_mes`, en orden: transactions (monto/cuenta), contacts (count),
invoices facturado (sum), invoices cobrado (sum), invoices actividad, transactions
actividad.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from edecan_business.kpis import _top_n_mas_otros, kpis_mes


def _make_kpis_session(
    make_session,
    *,
    transacciones=None,
    nuevos_clientes=0,
    facturado=Decimal("0"),
    cobrado=Decimal("0"),
    actividad_facturas=None,
    actividad_transacciones=None,
):
    return make_session(
        [
            transacciones or [],
            [{"n": nuevos_clientes}],
            [{"total": facturado}],
            [{"total": cobrado}],
            actividad_facturas if actividad_facturas is not None else [],
            actividad_transacciones if actividad_transacciones is not None else [],
        ]
    )


# ---------------------------------------------------------------------------
# Ingresos / gastos / beneficio
# ---------------------------------------------------------------------------


async def test_kpis_mes_ingresos_gastos_beneficio(make_session):
    session = _make_kpis_session(
        make_session,
        transacciones=[
            {"monto": Decimal("1000.00"), "cuenta": "web"},
            {"monto": Decimal("-300.00"), "cuenta": "oficina"},
        ],
    )
    kpis = await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-07")
    assert kpis["ingresos"] == 1000.0
    assert kpis["gastos"] == 300.0
    assert kpis["beneficio"] == 700.0


async def test_kpis_mes_sin_transacciones_todo_en_cero(make_session):
    session = _make_kpis_session(make_session)
    kpis = await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-07")
    assert kpis["ingresos"] == kpis["gastos"] == kpis["beneficio"] == 0.0
    assert kpis["por_canal"] == []
    assert kpis["actividad"] == []


# ---------------------------------------------------------------------------
# Nuevos clientes
# ---------------------------------------------------------------------------


async def test_kpis_mes_nuevos_clientes(make_session):
    session = _make_kpis_session(make_session, nuevos_clientes=3)
    kpis = await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-07")
    assert kpis["nuevos_clientes"] == 3


# ---------------------------------------------------------------------------
# Facturado / cobrado — anti doble conteo con ingresos
# ---------------------------------------------------------------------------


async def test_kpis_mes_facturado_y_cobrado_no_duplican_ingresos(make_session):
    """Sin transacciones registradas, `ingresos` debe quedar en 0 aunque existan facturas
    'sent'/'paid' con montos grandes: la factura es un documento, no un ingreso por sí
    sola — ver el docstring del módulo, criterio anti-doble-conteo."""
    session = _make_kpis_session(
        make_session, facturado=Decimal("5000.00"), cobrado=Decimal("3000.00")
    )
    kpis = await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-07")
    assert kpis["ingresos"] == 0.0
    assert kpis["facturado"] == 5000.0
    assert kpis["cobrado"] == 3000.0


async def test_kpis_mes_facturado_filtra_por_status_sent_o_paid_en_el_sql(make_session):
    session = _make_kpis_session(make_session)
    await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-07")
    sql_facturado, _ = session.llamadas[2]
    assert "status IN ('sent', 'paid')" in sql_facturado
    assert "created_at" in sql_facturado


async def test_kpis_mes_cobrado_filtra_por_status_paid_y_updated_at_en_el_sql(make_session):
    session = _make_kpis_session(make_session)
    await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-07")
    sql_cobrado, _ = session.llamadas[3]
    assert "status = 'paid'" in sql_cobrado
    assert "updated_at" in sql_cobrado  # no created_at: se acota por cuándo se marcó paid


# ---------------------------------------------------------------------------
# Dona "ventas por canal" — top 6 + otros
# ---------------------------------------------------------------------------


async def test_kpis_mes_por_canal_top6_mas_otros(make_session):
    # 8 canales con montos descendentes: canal-0..canal-5 deben quedar en el top, y
    # canal-6/canal-7 colapsados en "otros".
    transacciones = [
        {"monto": Decimal(str(100 - i)), "cuenta": f"canal-{i}"} for i in range(8)
    ]
    session = _make_kpis_session(make_session, transacciones=transacciones)
    kpis = await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-07")

    por_canal = kpis["por_canal"]
    assert len(por_canal) == 7  # top 6 + 1 bucket "otros"
    assert [c["canal"] for c in por_canal[:6]] == [f"canal-{i}" for i in range(6)]
    assert por_canal[-1]["canal"] == "otros"
    assert por_canal[-1]["total"] == pytest.approx(94.0 + 93.0)


async def test_kpis_mes_por_canal_sin_bucket_otros_si_hay_6_o_menos(make_session):
    transacciones = [{"monto": Decimal("10"), "cuenta": f"canal-{i}"} for i in range(3)]
    session = _make_kpis_session(make_session, transacciones=transacciones)
    kpis = await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-07")
    assert len(kpis["por_canal"]) == 3
    assert all(c["canal"] != "otros" for c in kpis["por_canal"])


async def test_kpis_mes_por_canal_ignora_gastos(make_session):
    """La dona es de "ventas" (ingresos) por canal — un gasto en la misma cuenta no debe
    sumar ni restar del total del canal."""
    session = _make_kpis_session(
        make_session,
        transacciones=[
            {"monto": Decimal("500.00"), "cuenta": "web"},
            {"monto": Decimal("-200.00"), "cuenta": "web"},
        ],
    )
    kpis = await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-07")
    assert kpis["por_canal"] == [{"canal": "web", "total": 500.0}]


async def test_kpis_mes_canal_vacio_usa_etiqueta_sin_cuenta(make_session):
    session = _make_kpis_session(
        make_session, transacciones=[{"monto": Decimal("50"), "cuenta": None}]
    )
    kpis = await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-07")
    assert kpis["por_canal"] == [{"canal": "sin cuenta", "total": 50.0}]


def test_top_n_mas_otros_sin_bucket_si_no_sobra_ninguno():
    acumulado = {"a": Decimal("10"), "b": Decimal("5")}
    resultado = _top_n_mas_otros(acumulado, 6)
    assert len(resultado) == 2
    assert all(r["canal"] != "otros" for r in resultado)


# ---------------------------------------------------------------------------
# Actividad reciente
# ---------------------------------------------------------------------------


async def test_kpis_mes_actividad_mezcla_facturas_y_transacciones_ordenada_por_fecha(
    make_session,
):
    invoice_id, tx_id = uuid4(), uuid4()
    session = _make_kpis_session(
        make_session,
        actividad_facturas=[
            {
                "id": invoice_id,
                "numero": "F-2026-0001",
                "cliente_nombre": "Acme",
                "total": Decimal("100.00"),
                "moneda": "USD",
                "status": "sent",
                "created_at": datetime(2026, 7, 10, 9, 0, tzinfo=UTC),
            }
        ],
        actividad_transacciones=[
            {
                "id": tx_id,
                "descripcion": "Pago de luz",
                "categoria": "servicios",
                "monto": Decimal("-50.00"),
                "moneda": "USD",
                "fecha": date(2026, 7, 15),
            }
        ],
    )
    kpis = await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-07")
    actividad = kpis["actividad"]
    assert len(actividad) == 2
    # 15 de julio (transacción) es más reciente que 10 de julio (factura): va primero.
    assert actividad[0]["tipo"] == "transaccion"
    assert actividad[0]["id"] == str(tx_id)
    assert actividad[1]["tipo"] == "factura"
    assert actividad[1]["id"] == str(invoice_id)
    assert actividad[1]["descripcion"] == "Factura F-2026-0001 — Acme"
    # Las fechas quedan serializadas a texto ISO, no como objetos date/datetime crudos.
    assert isinstance(actividad[0]["fecha"], str)
    assert isinstance(actividad[1]["fecha"], str)


async def test_kpis_mes_actividad_pide_limite_10_en_ambas_consultas(make_session):
    session = _make_kpis_session(make_session)
    await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-07")
    limites = [params["limite"] for _, params in session.llamadas if "limite" in params]
    assert limites == [10, 10]


# ---------------------------------------------------------------------------
# Validación de `mes` / alcance por tenant+usuario
# ---------------------------------------------------------------------------


async def test_kpis_mes_default_es_mes_actual(make_session):
    session = _make_kpis_session(make_session)
    kpis = await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4())
    assert kpis["mes"] == date.today().strftime("%Y-%m")


async def test_kpis_mes_formato_invalido_raises_sin_tocar_sesion(make_session):
    session = make_session()
    with pytest.raises(ValueError):
        await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="no-es-un-mes")
    assert session.llamadas == []


async def test_kpis_mes_numero_de_mes_fuera_de_rango_raises(make_session):
    session = make_session()
    with pytest.raises(ValueError):
        await kpis_mes(session, tenant_id=uuid4(), user_id=uuid4(), mes="2026-13")
    assert session.llamadas == []


async def test_kpis_mes_todas_las_consultas_llevan_tenant_y_usuario(make_session):
    tenant_id, user_id = uuid4(), uuid4()
    session = _make_kpis_session(make_session)
    await kpis_mes(session, tenant_id=tenant_id, user_id=user_id, mes="2026-07")
    assert len(session.llamadas) == 6
    for _, params in session.llamadas:
        assert params.get("tenant_id") == str(tenant_id)
        assert params.get("user_id") == str(user_id)
