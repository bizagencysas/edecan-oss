"""Verificación estructural de la tabla nueva de `ARCHITECTURE.md` §12e
(dueño WP-V3-01) contra `edecan_db.models`.

Mismo estilo que `test_db_models.py`/`test_models_v2.py`: no abre conexión a
base de datos, solo inspecciona `Base.metadata` (nombres de tabla, columnas,
nullability, constraints, defaults) que SQLAlchemy construye en memoria al
importar `edecan_db.models`.
"""

from __future__ import annotations

from edecan_db.models import ALL_MODELS, RLS_TABLES, Base, Skill

V3_TABLES = {"skills"}


def test_la_tabla_skills_esta_en_all_models_y_en_metadata():
    nombres_all_models = {model.__tablename__ for model in ALL_MODELS}
    assert V3_TABLES <= nombres_all_models
    assert V3_TABLES <= set(Base.metadata.tables)
    assert Skill in ALL_MODELS


def test_skills_es_tenant_scoped_con_rls():
    assert V3_TABLES <= RLS_TABLES
    columnas = Base.metadata.tables["skills"].columns
    assert "tenant_id" in columnas
    assert columnas["tenant_id"].nullable is False


def test_skills_tiene_id_created_at_updated_at():
    columnas = Base.metadata.tables["skills"].columns
    for nombre in ("id", "created_at", "updated_at"):
        assert nombre in columnas, f"skills sin columna {nombre!r}"
    assert columnas["id"].primary_key


def test_skills_columnas_obligatorias_y_tipos():
    columnas = Base.metadata.tables["skills"].columns
    for nombre in (
        "user_id",
        "nombre",
        "slug",
        "source",
        "descripcion",
        "contenido",
        "recursos",
        "enabled",
    ):
        assert columnas[nombre].nullable is False, nombre
    # `version` es la única columna de texto opcional (§12e: "version text NULL").
    assert columnas["version"].nullable is True

    fks_user = {fk.column.table.name for fk in columnas["user_id"].foreign_keys}
    assert fks_user == {"users"}


def test_skills_defaults_descripcion_recursos_enabled():
    columnas = Base.metadata.tables["skills"].columns
    assert columnas["descripcion"].server_default.arg == ""
    # `server_default=text(...)` -> `.arg` es un `TextClause`, se compara con
    # `str()` (mismo criterio que el resto de columnas booleanas/jsonb de
    # `test_models_v2.py`).
    assert str(columnas["enabled"].server_default.arg) == "true"
    assert str(columnas["recursos"].server_default.arg) == "'{}'::jsonb"


def test_skills_unique_tenant_id_slug():
    nombres_constraints = {
        c.name for c in Skill.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"
    }
    assert "uq_skills_tenant_id_slug" in nombres_constraints
    unique = next(
        c
        for c in Skill.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint" and c.name == "uq_skills_tenant_id_slug"
    )
    assert {col.name for col in unique.columns} == {"tenant_id", "slug"}
