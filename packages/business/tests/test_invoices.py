"""Tests de `edecan_business.invoices` — numeración, totales (`Decimal` + redondeo
bancario), PDF, el orquestador `crear_factura`, y las transiciones de estado. Offline y
determinista: `FakeSession` programable, `FakeUploader` inyectado (nunca toca S3 ni
Postgres reales).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import edecan_business.invoices as invoices_module
import pytest
from edecan_business.invoices import (
    EstadoInvalidoError,
    cambiar_estado,
    compute_totals,
    crear_factura,
    listar_facturas,
    next_numero,
    obtener_factura,
    render_pdf,
)
from pypdf import PdfReader

# ---------------------------------------------------------------------------
# compute_totals — Decimal + redondeo bancario
# ---------------------------------------------------------------------------


def test_compute_totals_redondea_cada_linea_antes_de_sumar_no_al_final():
    """`1 * 10.005` es un empate exacto (`.xx5`) que el redondeo bancario manda hacia
    ABAJO (el dígito anterior, 0, ya es par) → cada línea queda en 10.00. Si en cambio se
    sumara primero en precisión completa y se redondeara solo el subtotal
    (`20.010` -> `20.01`), el resultado sería distinto — exactamente el bug que
    `compute_totals` evita redondeando línea por línea."""
    items = [
        {"cantidad": Decimal("1"), "precio_unitario": Decimal("10.005")},
        {"cantidad": Decimal("1"), "precio_unitario": Decimal("10.005")},
    ]
    subtotal, impuestos, total = compute_totals(items, impuestos_pct=0)
    assert subtotal == Decimal("20.00")  # no "20.01" (lo que daría sumar-y-luego-redondear)
    assert impuestos == Decimal("0.00")
    assert total == Decimal("20.00")


def test_compute_totals_empate_redondea_hacia_arriba_cuando_el_digito_anterior_es_impar():
    """`3 * 0.125 = 0.375`: empate exacto entre .37 y .38; el dígito anterior (7) es impar,
    así que el redondeo bancario sube al par más cercano, .38 — la otra dirección del
    mismo mecanismo que el test de arriba, para no dejar el sesgo bajo prueba solo en un
    sentido."""
    items = [{"cantidad": Decimal("3"), "precio_unitario": Decimal("0.125")}]
    subtotal, _, _ = compute_totals(items, impuestos_pct=0)
    assert subtotal == Decimal("0.38")


def test_compute_totals_con_impuestos():
    items = [{"cantidad": Decimal("2"), "precio_unitario": Decimal("5.00")}]
    subtotal, impuestos, total = compute_totals(items, impuestos_pct=16)
    assert subtotal == Decimal("10.00")
    assert impuestos == Decimal("1.60")
    assert total == Decimal("11.60")


def test_compute_totals_sin_impuestos_pct_usa_cero():
    items = [{"cantidad": Decimal("1"), "precio_unitario": Decimal("7.50")}]
    subtotal, impuestos, total = compute_totals(items)
    assert impuestos == Decimal("0.00")
    assert total == subtotal == Decimal("7.50")


def test_compute_totals_lista_vacia_es_subtotal_cero():
    subtotal, impuestos, total = compute_totals([], impuestos_pct=10)
    assert (subtotal, impuestos, total) == (Decimal("0.00"), Decimal("0.00"), Decimal("0.00"))


# ---------------------------------------------------------------------------
# next_numero
# ---------------------------------------------------------------------------


async def test_next_numero_primera_factura_del_anio(make_session):
    session = make_session([[{"n": 0}]])
    numero = await next_numero(session, tenant_id=uuid4(), year=2026)
    assert numero == "F-2026-0001"


async def test_next_numero_incrementa_con_facturas_existentes(make_session):
    session = make_session([[{"n": 41}]])
    numero = await next_numero(session, tenant_id=uuid4(), year=2026)
    assert numero == "F-2026-0042"


async def test_next_numero_usa_anio_actual_si_no_se_especifica(make_session):
    session = make_session([[{"n": 0}]])
    numero = await next_numero(session, tenant_id=uuid4())
    anio_actual = datetime.now(UTC).year
    assert numero == f"F-{anio_actual}-0001"


async def test_next_numero_filtra_por_tenant_y_anio_en_el_sql(make_session):
    tenant_id = uuid4()
    session = make_session([[{"n": 5}]])
    await next_numero(session, tenant_id=tenant_id, year=2025)
    sql, params = session.llamadas[0]
    assert "invoices" in sql
    assert "EXTRACT(YEAR FROM created_at)" in sql
    assert params["tenant_id"] == str(tenant_id)
    assert params["year"] == 2025


async def test_next_numero_llamadas_concurrentes_con_el_mismo_conteo_colisionan_documentado(
    make_session,
):
    """Carrera teórica documentada en el docstring de `next_numero`: dos llamadas que leen
    el mismo `COUNT` (porque ninguna ha confirmado su `INSERT` todavía) producen el MISMO
    número — este test fija ese comportamiento como conocido/aceptado en P1, no como un bug
    a arreglar aquí."""
    tenant_id = uuid4()
    session_a = make_session([[{"n": 3}]])
    session_b = make_session([[{"n": 3}]])  # ninguna de las dos "vio" el insert de la otra
    numero_a = await next_numero(session_a, tenant_id=tenant_id, year=2026)
    numero_b = await next_numero(session_b, tenant_id=tenant_id, year=2026)
    assert numero_a == numero_b == "F-2026-0004"


# ---------------------------------------------------------------------------
# render_pdf
# ---------------------------------------------------------------------------


def _invoice_row(**overrides):
    base = {
        "id": uuid4(),
        "numero": "F-2026-0001",
        "cliente_nombre": "Acme SA",
        "cliente_email": "compras@acme.test",
        "moneda": "USD",
        "subtotal": Decimal("100.00"),
        "impuestos": Decimal("16.00"),
        "total": Decimal("116.00"),
        "status": "draft",
        "due_date": None,
        "notas": "",
        "created_at": datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
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


def test_render_pdf_empieza_con_pdf_y_no_esta_vacio():
    data = render_pdf(_invoice_row(), [_item_row()])
    assert isinstance(data, bytes)
    assert len(data) > 0
    assert data.startswith(b"%PDF")


def test_render_pdf_no_revienta_con_campos_faltantes():
    """`render_pdf` acepta cualquier `dict`-like con `.get(...)` — un `dict` casi vacío
    (como podría llegar en un test o una migración de datos parcial) no debe reventar."""
    data = render_pdf({}, [])
    assert data.startswith(b"%PDF")


def test_render_pdf_con_varios_items_y_notas():
    items = [_item_row(descripcion=f"Item {i}") for i in range(3)]
    data = render_pdf(_invoice_row(notas="Pago contra entrega."), items)
    assert data.startswith(b"%PDF")
    assert len(data) > 0


def test_render_pdf_una_factura_corta_cabe_en_una_sola_pagina():
    """Regresión: el pie ("Generado por Edecán") se posicionaba con `set_y(-20)` + una
    celda de 10mm de alto mientras `set_auto_page_break` seguía activo con `margin=15` —
    el borde inferior de esa celda (10mm del final de la página) caía DENTRO de la franja
    de 15mm reservada, así que fpdf2 agregaba una página 2 en blanco solo para el pie. Este
    bug apareció generando un PDF de verdad contra Postgres/S3 reales (`docs/negocios.md`)
    y NINGÚN test que solo mirara `data.startswith(b"%PDF")` lo hubiera atrapado — de ahí
    esta aserción explícita de conteo de páginas con `pypdf`."""
    data = render_pdf(_invoice_row(notas="Pago contra entrega."), [_item_row()])
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1


def test_render_pdf_sin_notas_tambien_cabe_en_una_sola_pagina():
    data = render_pdf(_invoice_row(notas=""), [_item_row()])
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1


# ---------------------------------------------------------------------------
# crear_factura (orquestador completo)
# ---------------------------------------------------------------------------


async def test_crear_factura_inserta_invoice_e_items_y_sube_pdf(make_session, make_uploader):
    tenant_id, user_id = uuid4(), uuid4()
    invoice_row = _invoice_row(id=uuid4())
    item_row = _item_row()
    session = make_session([[{"n": 0}], [invoice_row], [item_row], []])
    uploader = make_uploader()

    invoice = await crear_factura(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        settings=None,
        cliente_nombre="Acme SA",
        items=[{"descripcion": "Consultoría", "cantidad": 1, "precio_unitario": "100.00"}],
        uploader=uploader,
    )

    assert invoice["numero"] == invoice_row["numero"]
    assert invoice["file_id"] == uploader.file_id
    assert invoice["pdf_file_id"] == uploader.file_id
    assert len(invoice["items"]) == 1

    # Orden exacto: next_numero, INSERT invoices, INSERT invoice_items, UPDATE pdf_file_id.
    sqls = [sql for sql, _ in session.llamadas]
    assert "SELECT COUNT" in sqls[0]
    assert "INSERT INTO invoices" in sqls[1]
    assert "INSERT INTO invoice_items" in sqls[2]
    assert "UPDATE invoices" in sqls[3] and "pdf_file_id" in sqls[3]

    assert len(uploader.llamadas) == 1
    subida = uploader.llamadas[0]
    assert subida["filename"] == f"{invoice_row['numero']}.pdf"
    assert subida["mime"] == "application/pdf"
    assert subida["data"].startswith(b"%PDF")

    _, update_params = session.llamadas[3]
    assert update_params["file_id"] == str(uploader.file_id)
    assert update_params["id"] == str(invoice_row["id"])
    assert session.flushes == 1


async def test_crear_factura_con_multiples_items_inserta_una_fila_por_item(
    make_session, make_uploader
):
    invoice_row = _invoice_row()
    session = make_session(
        [
            [{"n": 0}],
            [invoice_row],
            [_item_row(descripcion="A")],
            [_item_row(descripcion="B")],
            [],
        ]
    )
    invoice = await crear_factura(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        settings=None,
        cliente_nombre="Acme SA",
        items=[
            {"descripcion": "A", "cantidad": 1, "precio_unitario": "10"},
            {"descripcion": "B", "cantidad": 2, "precio_unitario": "5"},
        ],
        uploader=make_uploader(),
    )
    assert len(invoice["items"]) == 2
    item_sqls = [sql for sql, _ in session.llamadas if "INSERT INTO invoice_items" in sql]
    assert len(item_sqls) == 2


async def test_crear_factura_rechaza_cliente_vacio_sin_tocar_la_sesion(make_session, make_uploader):
    session = make_session()
    with pytest.raises(ValueError, match="cliente_nombre"):
        await crear_factura(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            settings=None,
            cliente_nombre="   ",
            items=[{"descripcion": "X", "cantidad": 1, "precio_unitario": 1}],
            uploader=make_uploader(),
        )
    assert session.llamadas == []


async def test_crear_factura_rechaza_items_vacios_sin_tocar_la_sesion(make_session, make_uploader):
    session = make_session()
    with pytest.raises(ValueError, match="al menos un item"):
        await crear_factura(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            settings=None,
            cliente_nombre="Acme SA",
            items=[],
            uploader=make_uploader(),
        )
    assert session.llamadas == []


async def test_crear_factura_rechaza_cantidad_negativa(make_session, make_uploader):
    session = make_session()
    with pytest.raises(ValueError, match="cantidad del item #1.*negativa"):
        await crear_factura(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            settings=None,
            cliente_nombre="Acme SA",
            items=[{"descripcion": "X", "cantidad": -1, "precio_unitario": 1}],
            uploader=make_uploader(),
        )
    assert session.llamadas == []


async def test_crear_factura_rechaza_precio_negativo(make_session, make_uploader):
    session = make_session()
    with pytest.raises(ValueError, match="precio_unitario del item #1.*negativo"):
        await crear_factura(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            settings=None,
            cliente_nombre="Acme SA",
            items=[{"descripcion": "X", "cantidad": 1, "precio_unitario": -5}],
            uploader=make_uploader(),
        )
    assert session.llamadas == []


async def test_crear_factura_rechaza_due_date_mal_formado_sin_tocar_la_sesion(
    make_session, make_uploader
):
    session = make_session()
    with pytest.raises(ValueError, match="fecha válida"):
        await crear_factura(
            session,
            tenant_id=uuid4(),
            user_id=uuid4(),
            settings=None,
            cliente_nombre="Acme SA",
            items=[{"descripcion": "X", "cantidad": 1, "precio_unitario": 1}],
            due_date="no-es-una-fecha",
            uploader=make_uploader(),
        )
    assert session.llamadas == []


async def test_crear_factura_moneda_invalida_cae_a_default_usd(make_session, make_uploader):
    invoice_row = _invoice_row()
    session = make_session([[{"n": 0}], [invoice_row], [_item_row()], []])
    await crear_factura(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        settings=None,
        cliente_nombre="Acme SA",
        items=[{"descripcion": "X", "cantidad": 1, "precio_unitario": 1}],
        moneda="dólares",
        uploader=make_uploader(),
    )
    _, insert_params = session.llamadas[1]
    assert insert_params["moneda"] == "USD"


async def test_crear_factura_cantidad_cero_es_valida(make_session, make_uploader):
    """`ge=0`, no `gt=0`: una cantidad de cero es rara pero no está prohibida por el
    contrato pinned ("montos >= 0") — no debe rechazarse."""
    invoice_row = _invoice_row()
    session = make_session([[{"n": 0}], [invoice_row], [_item_row(cantidad=Decimal("0"))], []])
    invoice = await crear_factura(
        session,
        tenant_id=uuid4(),
        user_id=uuid4(),
        settings=None,
        cliente_nombre="Acme SA",
        items=[{"descripcion": "X", "cantidad": 0, "precio_unitario": 10}],
        uploader=make_uploader(),
    )
    assert invoice is not None


# ---------------------------------------------------------------------------
# cambiar_estado
# ---------------------------------------------------------------------------


async def test_cambiar_estado_draft_a_sent_ok(make_session):
    invoice_id = uuid4()
    session = make_session([[{"status": "draft"}], [{"id": invoice_id, "status": "sent"}]])
    row = await cambiar_estado(
        session, tenant_id=uuid4(), invoice_id=invoice_id, nuevo_status="sent"
    )
    assert row["status"] == "sent"
    assert session.flushes == 1


async def test_cambiar_estado_sent_a_paid_ok(make_session):
    invoice_id = uuid4()
    session = make_session([[{"status": "sent"}], [{"id": invoice_id, "status": "paid"}]])
    row = await cambiar_estado(
        session, tenant_id=uuid4(), invoice_id=invoice_id, nuevo_status="paid"
    )
    assert row["status"] == "paid"


async def test_cambiar_estado_draft_a_paid_es_ilegal(make_session):
    session = make_session([[{"status": "draft"}]])
    with pytest.raises(EstadoInvalidoError, match="draft.*paid"):
        await cambiar_estado(
            session, tenant_id=uuid4(), invoice_id=uuid4(), nuevo_status="paid"
        )


@pytest.mark.parametrize("origen", ["draft", "sent", "paid"])
async def test_cambiar_estado_void_desde_cualquier_estado_no_void(make_session, origen):
    invoice_id = uuid4()
    session = make_session([[{"status": origen}], [{"id": invoice_id, "status": "void"}]])
    row = await cambiar_estado(
        session, tenant_id=uuid4(), invoice_id=invoice_id, nuevo_status="void"
    )
    assert row["status"] == "void"


async def test_cambiar_estado_void_a_void_es_ilegal(make_session):
    session = make_session([[{"status": "void"}]])
    with pytest.raises(EstadoInvalidoError):
        await cambiar_estado(
            session, tenant_id=uuid4(), invoice_id=uuid4(), nuevo_status="void"
        )


async def test_cambiar_estado_status_invalido_es_valueerror_no_estadoinvalido(make_session):
    session = make_session()
    with pytest.raises(ValueError) as excinfo:
        await cambiar_estado(
            session, tenant_id=uuid4(), invoice_id=uuid4(), nuevo_status="draft"
        )
    assert not isinstance(excinfo.value, EstadoInvalidoError)
    assert session.llamadas == []  # el chequeo de vocabulario corta antes de tocar la DB


async def test_cambiar_estado_factura_inexistente_retorna_none(make_session):
    session = make_session([[]])
    row = await cambiar_estado(
        session, tenant_id=uuid4(), invoice_id=uuid4(), nuevo_status="sent"
    )
    assert row is None
    assert len(session.llamadas) == 1  # no intenta el UPDATE si no encontró la factura


# ---------------------------------------------------------------------------
# listar_facturas / obtener_factura
# ---------------------------------------------------------------------------


async def test_listar_facturas_sin_filtro(make_session):
    filas_fake = [_invoice_row(numero="F-2026-0001"), _invoice_row(numero="F-2026-0002")]
    session = make_session([filas_fake])
    filas = await listar_facturas(session, tenant_id=uuid4())
    assert len(filas) == 2


async def test_listar_facturas_filtra_por_status(make_session):
    session = make_session([[_invoice_row(status="paid")]])
    filas = await listar_facturas(session, tenant_id=uuid4(), status="paid")
    assert len(filas) == 1
    sql, params = session.llamadas[0]
    assert "status = :status" in sql
    assert params["status"] == "paid"


async def test_listar_facturas_status_invalido_raises_sin_tocar_sesion(make_session):
    session = make_session()
    with pytest.raises(ValueError):
        await listar_facturas(session, tenant_id=uuid4(), status="bogus")
    assert session.llamadas == []


async def test_obtener_factura_incluye_items(make_session):
    invoice_id = uuid4()
    session = make_session(
        [[_invoice_row(id=invoice_id)], [_item_row(), _item_row(descripcion="Otro")]]
    )
    invoice = await obtener_factura(session, tenant_id=uuid4(), invoice_id=invoice_id)
    assert invoice is not None
    assert len(invoice["items"]) == 2


async def test_obtener_factura_no_encontrada_retorna_none_sin_pedir_items(make_session):
    session = make_session([[]])
    invoice = await obtener_factura(session, tenant_id=uuid4(), invoice_id=uuid4())
    assert invoice is None
    assert len(session.llamadas) == 1


# ---------------------------------------------------------------------------
# Sanidad del módulo (constantes internas usadas también por tools.py/kpis.py)
# ---------------------------------------------------------------------------


def test_estados_pinned():
    assert invoices_module._ESTADOS == ("draft", "sent", "paid", "void")
