"""0007_v5_expansion

Migración v5 de Edecán, escrita a mano (`ARCHITECTURE.md` §14, dueño
WP-V5-01, mismo patrón que `0001_initial`/`0003_v2_expansion`/
`0004_v3_expansion`/`0006_v4_expansion` — ver el docstring de `0001_initial`
para el razonamiento completo de cada pieza; aquí solo se anotan las
diferencias):

1. Las 5 tablas nuevas de §14 (mismas columnas/tipos que las 5 clases nuevas
   de `edecan_db.models`, sección "v5" al final de ese módulo), en orden de
   creación (`employees`/`payroll_runs` antes que las tablas que las
   referencian por FK):
   - `employees`: RRHH ligero.
   - `time_off`: ausencia/vacaciones de un `employee`, FK
     `employee_id -> employees.id ON DELETE CASCADE`. `status` SÍ lleva
     CHECK (máquina de estados explícita de aprobar/rechazar).
   - `payroll_runs`: corrida de nómina. Mismo espíritu que `ad_drafts`
     (0006)/`orders` (0003): nace `status='draft'`, ninguna fila paga nada
     real por sí sola (`DIRECCION_ACTUAL.md`: "dinero real nunca se mueve
     solo") — `status` lleva CHECK.
   - `payroll_items`: línea de un `employee` dentro de un `payroll_run`, FKs
     `payroll_run_id -> payroll_runs.id` y `employee_id -> employees.id`
     (ambas `ON DELETE CASCADE`).
   - `voice_consents`: consentimiento de clonación de voz. `status` lleva
     CHECK (`attested`/`revoked`).
2. Un índice por cada columna `tenant_id` — igual que las migraciones
   previas.
3. Dos `ALTER TABLE ... ADD COLUMN` sobre tablas YA existentes (no crean
   política RLS nueva, esas tablas ya la tienen desde su migración
   original): `devices` gana `push_token`/`push_platform` (nullable, sin
   CHECK — vocabulario abierto a propósito) y `skills` gana `trust_tier`
   (`NOT NULL DEFAULT 'sin_revisar'`, sin CHECK) y `capabilities`
   (`NOT NULL DEFAULT '[]'::jsonb`).
4. Actualiza el `CHECK` de `jobs.type` para sumar el 11º job type
   (`generate_podcast`, `edecan_schemas.queue.JOB_TYPES`) — mismo patrón
   `DROP CONSTRAINT` + `ADD CONSTRAINT` que ya usó `0005_jobs_type_check_v2_types`
   (Postgres no soporta modificar la expresión de un CHECK existente in
   place); mismo nombre de constraint literal `"type"` (ver el docstring de
   `0005` para por qué NO es `ck_jobs_type`).
5. `GRANT` explícito a `app_user` sobre las tablas nuevas — misma red de
   seguridad idempotente que `0003`/`0004`/`0006` documentan en su punto 3.
6. `ENABLE ROW LEVEL SECURITY` + política `tenant_isolation` en las 5 tablas
   nuevas (ninguna trae excepción declarada en §14: todas tenant-scoped).
   Sin `FORCE ROW LEVEL SECURITY`, mismo motivo que las migraciones previas.

Igual que `0001_initial`/`0003`/`0004`/`0006`, esta migración declara su
propia tupla `RLS_TABLES` en vez de importar `edecan_db.models` —
`test_migration_rls_tables.py` (packages/db/tests/) es el cross-check que
suma las `RLS_TABLES` de TODAS las migraciones y las compara contra
`edecan_db.models.RLS_TABLES`; ese archivo se extiende junto con esta
migración (mismo criterio que su propio docstring pide).

Igual que las migraciones previas, este archivo NO importa `edecan_db.models`
(helpers locales `_id_column`/`_timestamp_columns`/`_tenant_id_column`/
`_user_id_column` duplicados a propósito) para quedar como una foto fija e
independiente de cómo evolucionen los modelos del ORM más adelante.

Revision ID: 0007_v5_expansion
Revises: 0006_v4_expansion
Create Date: 2026-07-09 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007_v5_expansion"
down_revision: str | None = "0006_v4_expansion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APP_ROLE = "app_user"

# Las 5 tablas de `ARCHITECTURE.md` §14, en orden de creación (`employees`/
# `payroll_runs` antes que las tablas que las referencian por FK). Todas
# tenant-scoped con RLS — ver docstring del módulo para por qué esta tupla
# hace doble uso (grants/RLS de `upgrade()` y cross-check de
# `test_migration_rls_tables.py`).
RLS_TABLES: tuple[str, ...] = (
    "employees",
    "time_off",
    "payroll_runs",
    "payroll_items",
    "voice_consents",
)
ALL_TABLES_IN_ORDER: tuple[str, ...] = RLS_TABLES

# Mismo vocabulario que `edecan_schemas.queue.JOB_TYPES` (§10.5) y
# `edecan_db.models.Job.__table_args__` (`_enum_check("type", ...)`, que esta
# migración deja en sincronía) — los 10 de v1+v2 + el nuevo de v5, en el
# mismo orden que esas dos fuentes. Ver `0005_jobs_type_check_v2_types.py`
# para el mismo patrón sobre este constraint (ese archivo cubre v1+v2; este
# extiende su lista con el valor de v5).
_JOB_TYPES_V1_V2: tuple[str, ...] = (
    "ingest_file",
    "sync_connector",
    "send_reminder",
    "send_reminder_scan",
    "run_campaign_step",
    "generate_content",
    "memory_consolidate",
    "run_mission",
    "run_automation",
    "automation_scan",
)
_JOB_TYPES_V5: tuple[str, ...] = ("generate_podcast",)
_ALL_JOB_TYPES: tuple[str, ...] = _JOB_TYPES_V1_V2 + _JOB_TYPES_V5


def _job_type_check_sql(job_types: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in job_types)
    return f"type IN ({quoted})"


# ---------------------------------------------------------------------------
# Helpers locales (idénticos a los de `0001_initial`/`0003_v2_expansion`/
# `0004_v3_expansion`/`0006_v4_expansion`, duplicados a propósito — ver
# docstring del módulo).
# ---------------------------------------------------------------------------


def _id_column() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def _tenant_id_column(*, nullable: bool = False, ondelete: str = "CASCADE") -> sa.Column:
    return sa.Column(
        "tenant_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete=ondelete),
        nullable=nullable,
    )


def _user_id_column() -> sa.Column:
    return sa.Column(
        "user_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    # ========================================================================
    # employees / time_off / payroll_runs / payroll_items (RRHH, `erp.hr`)
    # ========================================================================

    op.create_table(
        "employees",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("puesto", sa.Text(), nullable=False, server_default=""),
        sa.Column("salario_mensual", sa.Numeric(14, 2), nullable=True),
        sa.Column("moneda", sa.CHAR(3), nullable=False, server_default="USD"),
        sa.Column("fecha_ingreso", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column(
            "meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        *_timestamp_columns(),
    )
    op.create_index("ix_employees_tenant_id", "employees", ["tenant_id"])

    op.create_table(
        "time_off",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("desde", sa.Date(), nullable=False),
        sa.Column("hasta", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("notas", sa.Text(), nullable=False, server_default=""),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')", name="status"
        ),
    )
    op.create_index("ix_time_off_tenant_id", "time_off", ["tenant_id"])

    op.create_table(
        "payroll_runs",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("periodo", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("total", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("moneda", sa.CHAR(3), nullable=False, server_default="USD"),
        sa.Column("notas", sa.Text(), nullable=False, server_default=""),
        sa.Column("approved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint("status IN ('draft', 'approved', 'paid', 'cancelled')", name="status"),
    )
    op.create_index("ix_payroll_runs_tenant_id", "payroll_runs", ["tenant_id"])

    op.create_table(
        "payroll_items",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "payroll_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payroll_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bruto", sa.Numeric(14, 2), nullable=False),
        sa.Column("deducciones", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("neto", sa.Numeric(14, 2), nullable=False),
        *_timestamp_columns(),
    )
    op.create_index("ix_payroll_items_tenant_id", "payroll_items", ["tenant_id"])

    # ========================================================================
    # voice_consents (voz avanzada, `voice.cloning`)
    # ========================================================================

    op.create_table(
        "voice_consents",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("voice_name", sa.Text(), nullable=False),
        sa.Column("provider_voice_id", sa.Text(), nullable=True),
        # Sin FK a propósito: §14 no la pinnea como referencia forzada (ver
        # docstring de `edecan_db.models.VoiceConsent`).
        sa.Column("consent_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "attestation", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="attested"),
        sa.Column(
            "meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        *_timestamp_columns(),
        sa.CheckConstraint("status IN ('attested', 'revoked')", name="status"),
    )
    op.create_index("ix_voice_consents_tenant_id", "voice_consents", ["tenant_id"])

    # ========================================================================
    # ALTER de tablas existentes (ver docstring del módulo, punto 3).
    # ========================================================================

    op.add_column("devices", sa.Column("push_token", sa.String(), nullable=True))
    op.add_column("devices", sa.Column("push_platform", sa.String(), nullable=True))

    op.add_column(
        "skills",
        sa.Column("trust_tier", sa.Text(), nullable=False, server_default="sin_revisar"),
    )
    op.add_column(
        "skills",
        sa.Column(
            "capabilities",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # ========================================================================
    # CHECK de `jobs.type` (ver docstring del módulo, punto 4).
    # ========================================================================

    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_ALL_JOB_TYPES))

    # ========================================================================
    # Grants + Row-Level Security — ver docstring del módulo (puntos 5 y 6).
    # ========================================================================

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")

    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_V1_V2))

    op.drop_column("skills", "capabilities")
    op.drop_column("skills", "trust_tier")
    op.drop_column("devices", "push_platform")
    op.drop_column("devices", "push_token")

    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    # Sin REVOKE: mismo motivo que `0003`/`0004`/`0006` `.downgrade()` —
    # `DROP TABLE` ya retira los grants de estas tablas por sí solo, y un
    # REVOKE de "ALL TABLES IN SCHEMA public" también le quitaría privilegios
    # a las tablas de v1/v2/v3/v4, que esta migración no debe tocar.
    for table in reversed(ALL_TABLES_IN_ORDER):
        op.drop_table(table)
