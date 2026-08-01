"""Verificación estructural de las 3 tablas nuevas de `ARCHITECTURE.md` §13
(dueño WP-V4-01) contra `edecan_db.models`.

Mismo estilo que `test_db_models.py`/`test_models_v2.py`/`test_models_v3.py`:
no abre conexión a base de datos, solo inspecciona `Base.metadata` (nombres
de tabla, columnas, nullability, constraints, defaults) que SQLAlchemy
construye en memoria al importar `edecan_db.models`.
"""

from __future__ import annotations

from edecan_db.models import (
    ALL_MODELS,
    RLS_TABLES,
    AdDraft,
    Base,
    Product,
    StockMove,
)

V4_TABLES = {"products", "stock_moves", "ad_drafts"}

V4_MODELS = (Product, StockMove, AdDraft)


def test_las_3_tablas_v4_estan_en_all_models_y_en_metadata():
    nombres_all_models = {model.__tablename__ for model in ALL_MODELS}
    assert V4_TABLES <= nombres_all_models
    assert V4_TABLES <= set(Base.metadata.tables)
    for model in V4_MODELS:
        assert model in ALL_MODELS


def test_las_3_tablas_v4_son_tenant_scoped_con_rls():
    assert V4_TABLES <= RLS_TABLES
    for tabla in V4_TABLES:
        columnas = Base.metadata.tables[tabla].columns
        assert "tenant_id" in columnas, tabla
        assert columnas["tenant_id"].nullable is False, tabla


def test_las_3_tablas_v4_tienen_id_created_at_updated_at():
    for model in V4_MODELS:
        columnas = Base.metadata.tables[model.__tablename__].columns
        for nombre in ("id", "created_at", "updated_at"):
            assert nombre in columnas, f"{model.__tablename__} sin columna {nombre!r}"
        assert columnas["id"].primary_key


# ---------------------------------------------------------------------------
# products
# ---------------------------------------------------------------------------


def test_products_columnas_obligatorias_y_tipos():
    columnas = Base.metadata.tables["products"].columns
    obligatorias = (
        "user_id",
        "sku",
        "nombre",
        "descripcion",
        "unidad",
        "stock",
        "stock_minimo",
        "activo",
    )
    for nombre in obligatorias:
        assert columnas[nombre].nullable is False, nombre
    for nombre in ("precio", "costo"):
        assert columnas[nombre].nullable is True, nombre

    fks_user = {fk.column.table.name for fk in columnas["user_id"].foreign_keys}
    assert fks_user == {"users"}


def test_products_defaults():
    columnas = Base.metadata.tables["products"].columns
    assert columnas["descripcion"].server_default.arg == ""
    assert columnas["unidad"].server_default.arg == "unidad"
    # `server_default=text(...)` -> `.arg` es un `TextClause`, se compara con
    # `str()` (mismo criterio que el resto de columnas numéricas/booleanas
    # definidas con `text(...)` en `test_models_v2.py`/`test_models_v3.py`).
    assert str(columnas["stock"].server_default.arg) == "0"
    assert str(columnas["stock_minimo"].server_default.arg) == "0"
    assert str(columnas["activo"].server_default.arg) == "true"


def test_products_unique_tenant_id_sku():
    nombres_constraints = {
        c.name for c in Product.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"
    }
    assert "uq_products_tenant_id_sku" in nombres_constraints
    unique = next(
        c
        for c in Product.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint" and c.name == "uq_products_tenant_id_sku"
    )
    assert {col.name for col in unique.columns} == {"tenant_id", "sku"}


# ---------------------------------------------------------------------------
# stock_moves
# ---------------------------------------------------------------------------


def test_stock_moves_columnas_obligatorias_y_fk_a_product():
    columnas = Base.metadata.tables["stock_moves"].columns
    for nombre in ("user_id", "product_id", "delta", "motivo", "nota"):
        assert columnas[nombre].nullable is False, nombre
    assert columnas["ref"].nullable is True

    fks_product = {fk.column.table.name for fk in columnas["product_id"].foreign_keys}
    assert fks_product == {"products"}


def test_stock_moves_nota_default_vacio():
    columnas = Base.metadata.tables["stock_moves"].columns
    assert columnas["nota"].server_default.arg == ""


def test_stock_move_product_fk_ondelete_cascade():
    fk = next(iter(StockMove.__table__.columns["product_id"].foreign_keys))
    assert fk.ondelete == "CASCADE"


# ---------------------------------------------------------------------------
# ad_drafts
# ---------------------------------------------------------------------------


def test_ad_drafts_columnas_obligatorias_y_tipos():
    columnas = Base.metadata.tables["ad_drafts"].columns
    for nombre in ("user_id", "provider", "nombre", "objetivo", "moneda", "payload", "status"):
        assert columnas[nombre].nullable is False, nombre
    for nombre in ("presupuesto_diario", "external_id", "error", "confirmed_at", "pushed_at"):
        assert columnas[nombre].nullable is True, nombre


def test_ad_drafts_defaults():
    columnas = Base.metadata.tables["ad_drafts"].columns
    assert columnas["provider"].server_default.arg == "meta"
    assert columnas["moneda"].server_default.arg == "USD"
    assert columnas["status"].server_default.arg == "draft"
    assert str(columnas["payload"].server_default.arg) == "'{}'::jsonb"


def test_ad_drafts_status_check_constraint():
    checks = [
        c for c in AdDraft.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for estado in ("draft", "confirmed", "pushed", "error", "cancelled"):
        assert estado in sqltext
