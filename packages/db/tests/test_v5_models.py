"""Verificación estructural de las 5 tablas nuevas de `ARCHITECTURE.md` §14
(dueño WP-V5-01) contra `edecan_db.models`, más las 2 alteraciones sobre
tablas ya existentes (`devices`/`skills`).

Mismo estilo que `test_db_models.py`/`test_models_v2.py`/`test_models_v3.py`/
`test_v4_models.py`: no abre conexión a base de datos, solo inspecciona
`Base.metadata` (nombres de tabla, columnas, nullability, constraints,
defaults) que SQLAlchemy construye en memoria al importar `edecan_db.models`.
"""

from __future__ import annotations

from edecan_db.models import (
    ALL_MODELS,
    RLS_TABLES,
    Base,
    Device,
    Employee,
    PayrollItem,
    PayrollRun,
    Skill,
    TimeOff,
    VoiceConsent,
)

V5_TABLES = {"employees", "time_off", "payroll_runs", "payroll_items", "voice_consents"}

V5_MODELS = (Employee, TimeOff, PayrollRun, PayrollItem, VoiceConsent)


def test_las_5_tablas_v5_estan_en_all_models_y_en_metadata():
    nombres_all_models = {model.__tablename__ for model in ALL_MODELS}
    assert V5_TABLES <= nombres_all_models
    assert V5_TABLES <= set(Base.metadata.tables)
    for model in V5_MODELS:
        assert model in ALL_MODELS


def test_las_5_tablas_v5_son_tenant_scoped_con_rls():
    assert V5_TABLES <= RLS_TABLES
    for tabla in V5_TABLES:
        columnas = Base.metadata.tables[tabla].columns
        assert "tenant_id" in columnas, tabla
        assert columnas["tenant_id"].nullable is False, tabla


def test_las_5_tablas_v5_tienen_id_created_at_updated_at():
    for model in V5_MODELS:
        columnas = Base.metadata.tables[model.__tablename__].columns
        for nombre in ("id", "created_at", "updated_at"):
            assert nombre in columnas, f"{model.__tablename__} sin columna {nombre!r}"
        assert columnas["id"].primary_key


# ---------------------------------------------------------------------------
# employees
# ---------------------------------------------------------------------------


def test_employees_columnas_obligatorias_y_tipos():
    columnas = Base.metadata.tables["employees"].columns
    obligatorias = ("user_id", "nombre", "puesto", "moneda", "status", "meta")
    for nombre in obligatorias:
        assert columnas[nombre].nullable is False, nombre
    for nombre in ("email", "salario_mensual", "fecha_ingreso"):
        assert columnas[nombre].nullable is True, nombre

    fks_user = {fk.column.table.name for fk in columnas["user_id"].foreign_keys}
    assert fks_user == {"users"}


def test_employees_defaults():
    columnas = Base.metadata.tables["employees"].columns
    assert columnas["puesto"].server_default.arg == ""
    assert columnas["moneda"].server_default.arg == "USD"
    assert columnas["status"].server_default.arg == "active"
    assert str(columnas["meta"].server_default.arg) == "'{}'::jsonb"


def test_employees_status_sin_check_vocabulario_abierto():
    # A diferencia de `time_off`/`payroll_runs`/`voice_consents` (máquinas de
    # estado explícitas), `employees.status` queda texto abierto a propósito
    # (§14 no pinnea un vocabulario para esta columna, ver docstring del
    # módulo) — mismo criterio que `tenants.status`.
    checks = [
        c for c in Employee.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    assert checks == []


# ---------------------------------------------------------------------------
# time_off
# ---------------------------------------------------------------------------


def test_time_off_columnas_obligatorias_y_fk_a_employee():
    columnas = Base.metadata.tables["time_off"].columns
    for nombre in ("employee_id", "kind", "desde", "hasta", "status", "notas"):
        assert columnas[nombre].nullable is False, nombre

    fks_employee = {fk.column.table.name for fk in columnas["employee_id"].foreign_keys}
    assert fks_employee == {"employees"}


def test_time_off_employee_fk_ondelete_cascade():
    fk = next(iter(TimeOff.__table__.columns["employee_id"].foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_time_off_status_check_constraint():
    checks = [c for c in TimeOff.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for estado in ("pending", "approved", "rejected", "cancelled"):
        assert estado in sqltext


def test_time_off_defaults():
    columnas = Base.metadata.tables["time_off"].columns
    assert columnas["status"].server_default.arg == "pending"
    assert columnas["notas"].server_default.arg == ""


# ---------------------------------------------------------------------------
# payroll_runs
# ---------------------------------------------------------------------------


def test_payroll_runs_columnas_obligatorias_y_tipos():
    columnas = Base.metadata.tables["payroll_runs"].columns
    for nombre in ("user_id", "periodo", "status", "total", "moneda", "notas"):
        assert columnas[nombre].nullable is False, nombre
    assert columnas["approved_at"].nullable is True

    fks_user = {fk.column.table.name for fk in columnas["user_id"].foreign_keys}
    assert fks_user == {"users"}


def test_payroll_runs_nace_draft_por_default():
    # Mismo espíritu que `ad_drafts`/`orders`: nace SIEMPRE en 'draft', nunca
    # lo decide el caller (DIRECCION_ACTUAL.md: "dinero real nunca se mueve
    # solo").
    columnas = Base.metadata.tables["payroll_runs"].columns
    assert columnas["status"].server_default.arg == "draft"
    assert str(columnas["total"].server_default.arg) == "0"
    assert columnas["moneda"].server_default.arg == "USD"


def test_payroll_runs_status_check_constraint():
    checks = [
        c for c in PayrollRun.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for estado in ("draft", "approved", "paid", "cancelled"):
        assert estado in sqltext


# ---------------------------------------------------------------------------
# payroll_items
# ---------------------------------------------------------------------------


def test_payroll_items_columnas_obligatorias_y_fks():
    columnas = Base.metadata.tables["payroll_items"].columns
    for nombre in ("payroll_run_id", "employee_id", "bruto", "deducciones", "neto"):
        assert columnas[nombre].nullable is False, nombre

    fks_run = {fk.column.table.name for fk in columnas["payroll_run_id"].foreign_keys}
    assert fks_run == {"payroll_runs"}
    fks_employee = {fk.column.table.name for fk in columnas["employee_id"].foreign_keys}
    assert fks_employee == {"employees"}


def test_payroll_items_fks_ondelete_cascade():
    fk_run = next(iter(PayrollItem.__table__.columns["payroll_run_id"].foreign_keys))
    assert fk_run.ondelete == "CASCADE"
    fk_employee = next(iter(PayrollItem.__table__.columns["employee_id"].foreign_keys))
    assert fk_employee.ondelete == "CASCADE"


def test_payroll_items_deducciones_default_cero():
    columnas = Base.metadata.tables["payroll_items"].columns
    assert str(columnas["deducciones"].server_default.arg) == "0"


# ---------------------------------------------------------------------------
# voice_consents
# ---------------------------------------------------------------------------


def test_voice_consents_columnas_obligatorias_y_tipos():
    columnas = Base.metadata.tables["voice_consents"].columns
    for nombre in ("user_id", "voice_name", "attestation", "status", "meta"):
        assert columnas[nombre].nullable is False, nombre
    for nombre in ("provider_voice_id", "consent_file_id"):
        assert columnas[nombre].nullable is True, nombre

    fks_user = {fk.column.table.name for fk in columnas["user_id"].foreign_keys}
    assert fks_user == {"users"}
    # `consent_file_id` NO lleva FK a propósito (§14 no la pinnea).
    assert columnas["consent_file_id"].foreign_keys == set()


def test_voice_consents_defaults():
    columnas = Base.metadata.tables["voice_consents"].columns
    assert str(columnas["attestation"].server_default.arg) == "false"
    assert columnas["status"].server_default.arg == "attested"
    assert str(columnas["meta"].server_default.arg) == "'{}'::jsonb"


def test_voice_consents_status_check_constraint():
    checks = [
        c for c in VoiceConsent.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for estado in ("attested", "revoked"):
        assert estado in sqltext


# ---------------------------------------------------------------------------
# ALTERs sobre tablas existentes: devices (push_token/push_platform) y
# skills (trust_tier/capabilities)
# ---------------------------------------------------------------------------


def test_devices_gana_columnas_push_v5():
    columnas = Base.metadata.tables["devices"].columns
    assert "push_token" in columnas
    assert "push_platform" in columnas
    assert columnas["push_token"].nullable is True
    assert columnas["push_platform"].nullable is True


def test_devices_push_platform_sin_check_vocabulario_abierto():
    # Sin CHECK a propósito (§14 no pinnea un vocabulario de plataformas de
    # push) — a diferencia de `kind`/`status` de la misma tabla, que sí lo
    # tienen desde v2.
    checks = [c for c in Device.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    assert "push_platform" not in sqltext
    assert "push_token" not in sqltext


def test_skills_gana_columnas_trust_tier_y_capabilities_v5():
    columnas = Base.metadata.tables["skills"].columns
    assert "trust_tier" in columnas
    assert "capabilities" in columnas
    assert columnas["trust_tier"].nullable is False
    assert columnas["capabilities"].nullable is False
    assert columnas["trust_tier"].server_default.arg == "sin_revisar"
    assert str(columnas["capabilities"].server_default.arg) == "'[]'::jsonb"


def test_skills_trust_tier_sin_check_vocabulario_abierto():
    checks = [c for c in Skill.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    assert "trust_tier" not in sqltext
