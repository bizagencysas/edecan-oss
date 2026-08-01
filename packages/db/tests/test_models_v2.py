"""Verificación estructural de las 14 tablas nuevas de ROADMAP_V2.md §7.4
(dueño WP-V2-01) contra `edecan_db.models`.

Mismo estilo que `test_db_models.py`: no abre conexión a base de datos, solo
inspecciona `Base.metadata` (nombres de tabla, columnas, nullability,
constraints, defaults) que SQLAlchemy construye en memoria al importar
`edecan_db.models`.
"""

from __future__ import annotations

from edecan_db.models import (
    ALL_MODELS,
    RLS_TABLES,
    AgentMission,
    AgentStep,
    Automation,
    AutomationRun,
    Base,
    Budget,
    Device,
    HealthLog,
    Holding,
    Invoice,
    InvoiceItem,
    LearningProgress,
    Order,
    RemoteSession,
    UserProfile,
)

V2_TABLES = {
    "agent_missions",
    "agent_steps",
    "automations",
    "automation_runs",
    "devices",
    "remote_sessions",
    "orders",
    "holdings",
    "budgets",
    "invoices",
    "invoice_items",
    "health_logs",
    "learning_progress",
    "user_profiles",
}

V2_MODELS = (
    AgentMission,
    AgentStep,
    Automation,
    AutomationRun,
    Device,
    RemoteSession,
    Order,
    Holding,
    Budget,
    Invoice,
    InvoiceItem,
    HealthLog,
    LearningProgress,
    UserProfile,
)


def test_las_14_tablas_v2_estan_en_all_models_y_en_metadata():
    nombres_all_models = {model.__tablename__ for model in ALL_MODELS}
    assert V2_TABLES <= nombres_all_models
    assert V2_TABLES <= set(Base.metadata.tables)


def test_las_14_tablas_v2_son_tenant_scoped_con_rls():
    assert V2_TABLES <= RLS_TABLES
    for tabla in V2_TABLES:
        columnas = Base.metadata.tables[tabla].columns
        assert "tenant_id" in columnas, tabla
        assert columnas["tenant_id"].nullable is False, tabla


def test_las_14_tablas_v2_tienen_id_created_at_updated_at():
    for model in V2_MODELS:
        columnas = Base.metadata.tables[model.__tablename__].columns
        for nombre in ("id", "created_at", "updated_at"):
            assert nombre in columnas, f"{model.__tablename__} sin columna {nombre!r}"
        assert columnas["id"].primary_key


def test_agent_mission_status_check_y_default():
    cols = Base.metadata.tables["agent_missions"].columns
    assert cols["status"].server_default.arg == "planning"
    checks = [
        c for c in AgentMission.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for estado in ("planning", "running", "waiting_confirmation", "done", "error", "cancelled"):
        assert estado in sqltext
    assert cols["plan"].nullable is True
    assert cols["resultado"].nullable is True
    assert cols["presupuesto"].nullable is False


def test_agent_step_status_check_y_fk_a_mission():
    cols = Base.metadata.tables["agent_steps"].columns
    assert cols["status"].server_default.arg == "pending"
    checks = [
        c for c in AgentStep.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for estado in ("pending", "running", "waiting_confirmation", "done", "error", "skipped"):
        assert estado in sqltext
    fks = {fk.column.table.name for fk in cols["mission_id"].foreign_keys}
    assert fks == {"agent_missions"}


def test_automation_trigger_y_accion_son_jsonb_not_null_con_default():
    cols = Base.metadata.tables["automations"].columns
    assert cols["trigger"].nullable is False
    assert cols["accion"].nullable is False
    # `server_default=text("true")` (no un literal Python plano) -> `.arg` es
    # un `TextClause`, se compara vía `str()` (mismo motivo en los otros 3
    # asserts de este archivo que tocan columnas booleanas/numéricas
    # definidas con `text(...)`, ver `edecan_db.models`).
    assert str(cols["enabled"].server_default.arg) == "true"
    assert cols["descripcion"].server_default.arg == ""


def test_automation_run_fk_a_automation_y_status_default_running():
    cols = Base.metadata.tables["automation_runs"].columns
    assert cols["status"].server_default.arg == "running"
    fks = {fk.column.table.name for fk in cols["automation_id"].foreign_keys}
    assert fks == {"automations"}
    checks = [
        c for c in AutomationRun.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for estado in ("running", "done", "error", "waiting_confirmation"):
        assert estado in sqltext


def test_device_kind_y_status_check():
    cols = Base.metadata.tables["devices"].columns
    assert cols["status"].server_default.arg == "active"
    assert cols["pairing_secret_hash"].nullable is True
    assert cols["paired_at"].nullable is True
    checks = [c for c in Device.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    assert "companion" in sqltext
    assert "mobile" in sqltext
    assert "active" in sqltext
    assert "revoked" in sqltext


def test_remote_session_fk_a_device_es_nullable_set_null():
    cols = Base.metadata.tables["remote_sessions"].columns
    assert cols["device_id"].nullable is True
    assert cols["kind"].server_default.arg == "view"
    assert cols["status"].server_default.arg == "pending"
    assert str(cols["frames_count"].server_default.arg) == "0"
    fk = next(iter(cols["device_id"].foreign_keys))
    assert fk.column.table.name == "devices"
    assert fk.ondelete == "SET NULL"


def test_order_nace_draft_guardrail_dinero_real():
    # ROADMAP_V2.md §8.1: "dinero real nunca se mueve solo" — toda orden nace
    # `draft` a nivel de esquema, no depende de que el caller lo especifique.
    cols = Base.metadata.tables["orders"].columns
    assert cols["status"].server_default.arg == "draft"
    assert cols["moneda"].server_default.arg == "USD"
    checks = [c for c in Order.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for kind in ("payment", "purchase", "trade"):
        assert kind in sqltext
    for estado in ("draft", "confirmed", "executed_paper", "cancelled", "expired"):
        assert estado in sqltext


def test_holding_kind_default_paper_sin_check():
    cols = Base.metadata.tables["holdings"].columns
    assert cols["kind"].server_default.arg == "paper"
    checks = [c for c in Holding.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]
    assert checks == []


def test_invoice_status_check_y_fk_pdf_file_id_a_files():
    cols = Base.metadata.tables["invoices"].columns
    assert cols["status"].server_default.arg == "draft"
    assert cols["moneda"].server_default.arg == "USD"
    assert str(cols["impuestos"].server_default.arg) == "0"
    assert cols["notas"].server_default.arg == ""
    assert cols["pdf_file_id"].nullable is True
    fk = next(iter(cols["pdf_file_id"].foreign_keys))
    assert fk.column.table.name == "files"
    assert fk.ondelete == "SET NULL"
    checks = [c for c in Invoice.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for estado in ("draft", "sent", "paid", "void"):
        assert estado in sqltext


def test_invoice_item_fk_a_invoice_sin_user_id():
    cols = Base.metadata.tables["invoice_items"].columns
    assert "user_id" not in cols
    fks = {fk.column.table.name for fk in cols["invoice_id"].foreign_keys}
    assert fks == {"invoices"}


def test_health_log_kind_check_y_registrado_en_default_now():
    cols = Base.metadata.tables["health_logs"].columns
    checks = [
        c for c in HealthLog.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for kind in ("medicamento", "ejercicio", "sueno", "agua", "habito", "medida"):
        assert kind in sqltext
    assert cols["registrado_en"].nullable is False
    assert cols["registrado_en"].server_default is not None
    assert cols["notas"].nullable is True


def test_learning_progress_nivel_default_inicial():
    cols = Base.metadata.tables["learning_progress"].columns
    assert cols["nivel"].server_default.arg == "inicial"
    assert cols["leccion"].nullable is True
    assert cols["resultados"].nullable is True


def test_user_profile_unique_tenant_user_y_defaults():
    cols = Base.metadata.tables["user_profiles"].columns
    assert cols["resumen"].server_default.arg == ""
    assert str(cols["version"].server_default.arg) == "1"
    uniques = [
        c for c in UserProfile.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"
    ]
    matching = [u for u in uniques if {col.name for col in u.columns} == {"tenant_id", "user_id"}]
    assert len(matching) == 1
