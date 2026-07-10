"""Verificación estructural de las 2 tablas nuevas de `ARCHITECTURE.md` §15
(dueño WP-V6-01) contra `edecan_db.models`.

Mismo estilo que `test_db_models.py`/`test_models_v2.py`/`test_models_v3.py`/
`test_v4_models.py`/`test_v5_models.py`: no abre conexión a base de datos,
solo inspecciona `Base.metadata` (nombres de tabla, columnas, nullability,
constraints, defaults) que SQLAlchemy construye en memoria al importar
`edecan_db.models`.
"""

from __future__ import annotations

from edecan_db.models import ALL_MODELS, RLS_TABLES, Base, Meeting, Podcast

V6_TABLES = {"meetings", "podcasts"}

V6_MODELS = (Meeting, Podcast)

_STATUS_VOCAB = ("pending", "running", "done", "error")


def test_las_2_tablas_v6_estan_en_all_models_y_en_metadata():
    nombres_all_models = {model.__tablename__ for model in ALL_MODELS}
    assert V6_TABLES <= nombres_all_models
    assert V6_TABLES <= set(Base.metadata.tables)
    for model in V6_MODELS:
        assert model in ALL_MODELS


def test_las_2_tablas_v6_son_tenant_scoped_con_rls():
    assert V6_TABLES <= RLS_TABLES
    for tabla in V6_TABLES:
        columnas = Base.metadata.tables[tabla].columns
        assert "tenant_id" in columnas, tabla
        assert columnas["tenant_id"].nullable is False, tabla


def test_las_2_tablas_v6_tienen_id_created_at_updated_at():
    for model in V6_MODELS:
        columnas = Base.metadata.tables[model.__tablename__].columns
        for nombre in ("id", "created_at", "updated_at"):
            assert nombre in columnas, f"{model.__tablename__} sin columna {nombre!r}"
        assert columnas["id"].primary_key


# ---------------------------------------------------------------------------
# meetings
# ---------------------------------------------------------------------------


def test_meetings_columnas_obligatorias_y_tipos():
    columnas = Base.metadata.tables["meetings"].columns
    obligatorias = ("user_id", "titulo", "status")
    for nombre in obligatorias:
        assert columnas[nombre].nullable is False, nombre
    opcionales = (
        "source_file_id",
        "transcript_file_id",
        "resumen",
        "minutos",
        "error",
        "duracion_segundos",
    )
    for nombre in opcionales:
        assert columnas[nombre].nullable is True, nombre

    fks_user = {fk.column.table.name for fk in columnas["user_id"].foreign_keys}
    assert fks_user == {"users"}


def test_meetings_source_y_transcript_file_id_sin_fk():
    # Referencia informativa a `files`, sin FK a nivel de base de datos —
    # mismo criterio que `voice_consents.consent_file_id` (v5).
    columnas = Base.metadata.tables["meetings"].columns
    assert columnas["source_file_id"].foreign_keys == set()
    assert columnas["transcript_file_id"].foreign_keys == set()


def test_meetings_defaults():
    columnas = Base.metadata.tables["meetings"].columns
    assert columnas["titulo"].server_default.arg == ""
    assert columnas["status"].server_default.arg == "pending"


def test_meetings_status_check_constraint():
    checks = [c for c in Meeting.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for estado in _STATUS_VOCAB:
        assert estado in sqltext


# ---------------------------------------------------------------------------
# podcasts
# ---------------------------------------------------------------------------


def test_podcasts_columnas_obligatorias_y_tipos():
    columnas = Base.metadata.tables["podcasts"].columns
    obligatorias = ("user_id", "titulo", "status")
    for nombre in obligatorias:
        assert columnas[nombre].nullable is False, nombre
    for nombre in ("guion", "file_id", "error"):
        assert columnas[nombre].nullable is True, nombre

    fks_user = {fk.column.table.name for fk in columnas["user_id"].foreign_keys}
    assert fks_user == {"users"}


def test_podcasts_titulo_sin_default_a_diferencia_de_meetings():
    # A diferencia de `meetings.titulo` (default ''), `podcasts.titulo` no
    # trae `server_default`: el endpoint que crea la fila (`POST
    # /v1/voz/podcasts`) exige el título en el request.
    columnas = Base.metadata.tables["podcasts"].columns
    assert columnas["titulo"].server_default is None


def test_podcasts_file_id_sin_fk():
    columnas = Base.metadata.tables["podcasts"].columns
    assert columnas["file_id"].foreign_keys == set()


def test_podcasts_defaults():
    columnas = Base.metadata.tables["podcasts"].columns
    assert columnas["status"].server_default.arg == "pending"


def test_podcasts_status_check_constraint():
    checks = [c for c in Podcast.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for estado in _STATUS_VOCAB:
        assert estado in sqltext


def test_meetings_y_podcasts_comparten_vocabulario_de_status():
    # ARCHITECTURE.md §15: mismo CHECK de status (misma expresión SQL
    # literal, ninguna referencia al nombre de tabla dentro de la expresión)
    # en las dos tablas.
    checks_meetings = [
        c for c in Meeting.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    checks_podcasts = [
        c for c in Podcast.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    assert {str(c.sqltext) for c in checks_meetings} == {str(c.sqltext) for c in checks_podcasts}
