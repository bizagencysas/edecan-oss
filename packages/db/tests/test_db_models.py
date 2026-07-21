"""Import de modelos + verificación estructural contra ARCHITECTURE.md §10.3.

No abre conexión a base de datos: solo inspecciona `Base.metadata` (nombres
de tabla, columnas, nullability, constraints) que SQLAlchemy construye en
memoria al importar `edecan_db.models`.
"""

from __future__ import annotations

from edecan_db.models import (
    ALL_MODELS,
    GLOBAL_TABLES,
    RLS_TABLES,
    Base,
    ConnectorAccount,
    Job,
    Persona,
    Tenant,
)

# Las 23 tablas pinned en ARCHITECTURE.md §10.3, tal cual aparecen ahí.
EXPECTED_TABLES_V1 = {
    "tenants",
    "users",
    "memberships",
    "personas",
    "conversations",
    "messages",
    "memory_items",
    "memory_edges",
    "connector_accounts",
    "oauth_tokens",
    "tenant_keys",
    "files",
    "file_chunks",
    "reminders",
    "contacts",
    "transactions",
    "campaigns",
    "campaign_targets",
    "consents",
    "jobs",
    "usage_events",
    "audit_log",
    "subscriptions",
}

# Las 14 tablas nuevas de ROADMAP_V2.md §7.4 (dueño WP-V2-01). La cobertura
# estructural DEDICADA a estas 14 (columnas clave, defaults, CHECKs) vive en
# `test_models_v2.py` — aquí solo se extiende el inventario total para que
# los tests de esta página (pensados como guardarraíl genérico de "no se
# perdió/duplicó ninguna tabla") sigan siendo la fuente única de verdad del
# conteo total, en vez de quedar desactualizados en silencio.
EXPECTED_TABLES_V2 = {
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

# La 1 tabla nueva de `ARCHITECTURE.md` §12e (dueño WP-V3-01). La cobertura
# estructural DEDICADA vive en `test_models_v3.py` — mismo criterio que
# `EXPECTED_TABLES_V2` arriba: aquí solo se extiende el inventario total.
EXPECTED_TABLES_V3 = {
    "skills",
}

# Las 3 tablas nuevas de `ARCHITECTURE.md` §13 (dueño WP-V4-01). La cobertura
# estructural DEDICADA vive en `test_v4_models.py` — mismo criterio que
# `EXPECTED_TABLES_V2`/`EXPECTED_TABLES_V3` arriba: aquí solo se extiende el
# inventario total.
EXPECTED_TABLES_V4 = {
    "products",
    "stock_moves",
    "ad_drafts",
}

# Las 5 tablas nuevas de `ARCHITECTURE.md` §14 (dueño WP-V5-01). La cobertura
# estructural DEDICADA vive en `test_v5_models.py` — mismo criterio que
# `EXPECTED_TABLES_V2`/`EXPECTED_TABLES_V3`/`EXPECTED_TABLES_V4` arriba: aquí
# solo se extiende el inventario total.
EXPECTED_TABLES_V5 = {
    "employees",
    "time_off",
    "payroll_runs",
    "payroll_items",
    "voice_consents",
}

# Las 2 tablas nuevas de `ARCHITECTURE.md` §15 (dueño WP-V6-01). La cobertura
# estructural DEDICADA vive en `test_v6_models.py` — mismo criterio que
# `EXPECTED_TABLES_V2`/`EXPECTED_TABLES_V3`/`EXPECTED_TABLES_V4`/
# `EXPECTED_TABLES_V5` arriba: aquí solo se extiende el inventario total.
EXPECTED_TABLES_V6 = {
    "meetings",
    "podcasts",
}

EXPECTED_TABLES_PHONE = {"phone_calls", "phone_call_events"}

EXPECTED_TABLES = (
    EXPECTED_TABLES_V1
    | EXPECTED_TABLES_V2
    | EXPECTED_TABLES_V3
    | EXPECTED_TABLES_V4
    | EXPECTED_TABLES_V5
    | EXPECTED_TABLES_V6
    | EXPECTED_TABLES_PHONE
)


def test_import_no_falla_y_registra_metadata():
    # El solo hecho de haber podido importar `edecan_db.models` (arriba, a
    # nivel de módulo) ya ejercita la parte más importante de este test: que
    # construir todas las tablas/constraints/FKs no lanza ninguna excepción.
    assert len(Base.metadata.tables) == 50


def test_hay_exactamente_50_tablas_pinned():
    nombres = {model.__tablename__ for model in ALL_MODELS}
    assert nombres == EXPECTED_TABLES
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_todos_los_modelos_tienen_id_created_at_updated_at():
    for model in ALL_MODELS:
        columnas = Base.metadata.tables[model.__tablename__].columns
        for nombre in ("id", "created_at", "updated_at"):
            assert nombre in columnas, f"{model.__tablename__} sin columna {nombre!r}"
        assert columnas["id"].primary_key


def test_global_y_rls_particionan_todas_las_tablas_sin_solaparse():
    assert GLOBAL_TABLES == {"tenants", "users", "tenant_keys"}
    assert GLOBAL_TABLES | RLS_TABLES == EXPECTED_TABLES
    assert GLOBAL_TABLES.isdisjoint(RLS_TABLES)
    # 20 de v1 + 14 de v2 + 1 de v3 + 3 de v4 + 5 de v5 + 2 de v6 (ninguna de
    # las tablas nuevas es global, ver ROADMAP_V2.md §7.4/ARCHITECTURE.md
    # §12e/§13/§14/§15: todas tenant-scoped, sin excepción declarada).
    assert len(RLS_TABLES) == 47


def test_phone_call_events_fk_compuesta_impide_cruce_de_tenant():
    table = Base.metadata.tables["phone_call_events"]
    foreign_keys = [
        constraint
        for constraint in table.constraints
        if constraint.__class__.__name__ == "ForeignKeyConstraint"
    ]
    composite = next(
        constraint
        for constraint in foreign_keys
        if constraint.name == "fk_phone_call_events_tenant_call"
    )
    assert [element.parent.name for element in composite.elements] == ["tenant_id", "call_id"]
    assert [element.target_fullname for element in composite.elements] == [
        "phone_calls.tenant_id",
        "phone_calls.id",
    ]


def test_tablas_rls_tienen_columna_tenant_id():
    for tabla in RLS_TABLES:
        assert "tenant_id" in Base.metadata.tables[tabla].columns, tabla


def test_tenant_no_tiene_tenant_id_es_global():
    assert "tenant_id" not in Base.metadata.tables["tenants"].columns


def test_jobs_y_audit_log_tienen_tenant_id_nullable():
    # Excepción explícita pinned en §10.3: `jobs`/`audit_log` SÍ tienen RLS
    # pero su `tenant_id` es NULLABLE (jobs/auditoría de plataforma).
    assert Base.metadata.tables["jobs"].columns["tenant_id"].nullable is True
    assert Base.metadata.tables["audit_log"].columns["tenant_id"].nullable is True
    for tabla in RLS_TABLES - {"jobs", "audit_log"}:
        assert Base.metadata.tables[tabla].columns["tenant_id"].nullable is False, tabla


def test_persona_defaults_espejan_edecan_schemas_personaconfig():
    # Valores pinned en ARCHITECTURE.md §10.5 (`PersonaConfig`); se duplican
    # aquí como server_default de columna, sin importar `edecan_schemas`.
    cols = Base.metadata.tables["personas"].columns
    assert cols["nombre_asistente"].server_default.arg == "Edecán"
    assert cols["idioma"].server_default.arg == "es"
    assert cols["tono"].server_default.arg == "cálido y profesional"
    assert cols["formalidad"].nullable is False
    assert cols["voice_id"].nullable is True
    assert cols["estilo_relacion"].server_default.arg == "profesional"
    assert cols["adulto_confirmado"].nullable is False
    assert cols["consentimiento_romantico"].nullable is False
    constraint_names = {constraint.name for constraint in Persona.__table__.constraints}
    assert "ck_personas_persona_estilo_relacion_valid" in constraint_names
    assert "ck_personas_persona_romantico_requires_consent" in constraint_names
    assert Persona.__table__.name == "personas"


def test_job_type_check_constraint_menciona_los_7_job_types():
    # Mismo vocabulario que `edecan_schemas.queue.JOB_TYPES` (§10.5), sin
    # importar `edecan_schemas` (paquete hermano) desde este test.
    job_types = (
        "ingest_file",
        "sync_connector",
        "send_reminder",
        "send_reminder_scan",
        "run_campaign_step",
        "generate_content",
        "memory_consolidate",
    )
    checks = [c for c in Job.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for job_type in job_types:
        assert job_type in sqltext


def test_job_type_check_constraint_incluye_generate_podcast_v5():
    # ARCHITECTURE.md §14 (dueño WP-V5-01): 11º job type, agregado al final
    # del `_enum_check("type", ...)` de `Job.__table_args__` — mismo
    # vocabulario que `edecan_schemas.queue.JOB_TYPES` (§10.5).
    checks = [c for c in Job.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    assert "generate_podcast" in sqltext


def test_job_type_check_constraint_incluye_process_meeting_v6():
    # ARCHITECTURE.md §15 (dueño WP-V6-01): 12º job type, agregado al final
    # del `_enum_check("type", ...)` de `Job.__table_args__` — mismo
    # vocabulario que `edecan_schemas.queue.JOB_TYPES` (§10.5).
    checks = [c for c in Job.__table__.constraints if c.__class__.__name__ == "CheckConstraint"]
    sqltext = " ".join(str(c.sqltext) for c in checks)
    assert "process_meeting" in sqltext


def test_tenant_slug_es_unico():
    assert Tenant.__table__.columns["slug"].unique is True


def test_connector_accounts_tiene_indice_unico_parcial_por_numero_twilio():
    # Hallazgo de auditoría aislamiento-multi-tenant: `UNIQUE(tenant_id,
    # connector_key, external_account_id)` por sí solo NO evita que dos
    # tenants distintos reclamen el mismo número E.164 de Twilio — hace falta
    # además un índice único parcial (solo `connector_key='twilio'`, ver
    # docstring de `ConnectorAccount`) cruzando tenants.
    indexes = {idx.name: idx for idx in ConnectorAccount.__table__.indexes}
    idx = indexes["uq_connector_accounts_twilio_external_account_id"]
    assert idx.unique is True
    assert [c.name for c in idx.columns] == ["external_account_id"]
    # `tenant_id` deliberadamente AUSENTE de las columnas del índice: si
    # estuviera, la unicidad volvería a ser por-tenant (el mismo hueco que
    # ya cubre `uq_connector_accounts_tenant_connector_external`) en vez de
    # global.
    where_clause = str(idx.dialect_options["postgresql"]["where"])
    assert where_clause == "connector_key = 'twilio'"
