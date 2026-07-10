"""0003_v2_expansion

Migración v2 de Edecán, escrita a mano (ROADMAP_V2.md §7.4, dueño WP-V2-01,
mismo patrón que `0001_initial` — ver el docstring de esa migración para el
razonamiento completo de cada pieza; aquí solo se anotan las diferencias):

1. Las 14 tablas nuevas de §7.4 (mismas columnas/tipos que las 14 clases
   nuevas de `edecan_db.models`, sección "v2" al final de ese módulo).
2. Un índice por cada columna `tenant_id` — igual que `0001_initial`.
3. `GRANT` explícito a `app_user` sobre estas 14 tablas. `0001_initial` ya
   dejó `ALTER DEFAULT PRIVILEGES ... GRANT ... TO app_user` corriendo para
   el rol que ejecutó esa migración (cubre automáticamente CUALQUIER tabla
   que ESE MISMO rol cree después, sin repetir el GRANT) — este `GRANT`
   explícito es solo una red de seguridad idempotente por si `0003` la
   ejecuta un rol de Postgres distinto al que corrió `0001` (en cuyo caso el
   `ALTER DEFAULT PRIVILEGES` de `0001`, atado a ESE rol, no aplicaría aquí).
4. `ENABLE ROW LEVEL SECURITY` + política `tenant_isolation` en las 14
   (ninguna trae excepción declarada en §7.4: todas tenant-scoped). Sin
   `FORCE ROW LEVEL SECURITY`, mismo motivo que `0001_initial` (§2): el
   worker se conecta como dueño y bypassa RLS a propósito, filtrando
   `tenant_id` a mano.

A diferencia de `0001_initial` (que separa `RLS_TABLES` de
`ALL_TABLES_IN_ORDER` porque 3 de sus tablas son globales, sin RLS), aquí
ambas tuplas coinciden: ninguna de las 14 tablas de esta migración es
global. Se definen las dos de todos modos (en vez de una sola) para que el
cross-check de `test_migration_rls_tables.py` (que lee el atributo
`RLS_TABLES` por nombre desde CUALQUIER migración) siga funcionando igual
que con `0001_initial`, y para que un futuro lector que compare ambas
migraciones lado a lado no tenga que preguntarse por qué esta le falta un
atributo que la otra sí tiene.

Igual que `0001_initial`, este archivo NO importa `edecan_db.models` (helpers
locales `_id_column`/`_timestamp_columns`/`_tenant_id_column` duplicados a
propósito) para quedar como una foto fija e independiente de cómo evolucionen
los modelos del ORM más adelante.

Revision ID: 0003_v2_expansion
Revises: 0002_twilio_number_global_unique
Create Date: 2026-07-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_v2_expansion"
down_revision: str | None = "0002_twilio_number_global_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APP_ROLE = "app_user"

# Las 14 tablas de ROADMAP_V2.md §7.4, en orden de creación (padres antes que
# hijos: agent_missions→agent_steps, automations→automation_runs,
# devices→remote_sessions, invoices→invoice_items). Todas tenant-scoped con
# RLS — ver docstring del módulo para por qué esta tupla hace doble uso.
RLS_TABLES: tuple[str, ...] = (
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
)
ALL_TABLES_IN_ORDER: tuple[str, ...] = RLS_TABLES


# ---------------------------------------------------------------------------
# Helpers locales (idénticos a los de `0001_initial`, duplicados a propósito
# — ver docstring del módulo).
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
    """Las 14 tablas nuevas, salvo `invoice_items`, tienen `user_id` con la
    misma forma (`FK users.id ON DELETE CASCADE`, `NOT NULL`) — evita
    repetirla 13 veces."""
    return sa.Column(
        "user_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    # ========================================================================
    # agent_missions / agent_steps (WP-V2-06)
    # ========================================================================

    op.create_table(
        "agent_missions",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("objetivo", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="planning"),
        sa.Column("plan", postgresql.JSONB(), nullable=True),
        sa.Column("resultado", sa.Text(), nullable=True),
        sa.Column(
            "presupuesto",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('planning', 'running', 'waiting_confirmation', 'done', 'error', "
            "'cancelled')",
            name="status",
        ),
    )
    op.create_index("ix_agent_missions_tenant_id", "agent_missions", ["tenant_id"])

    op.create_table(
        "agent_steps",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_missions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("agente", sa.String(), nullable=False),
        sa.Column("instruccion", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("resultado", sa.Text(), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting_confirmation', 'done', 'error', "
            "'skipped')",
            name="status",
        ),
    )
    op.create_index("ix_agent_steps_tenant_id", "agent_steps", ["tenant_id"])

    # ========================================================================
    # automations / automation_runs (WP-V2-07)
    # ========================================================================

    op.create_table(
        "automations",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False, server_default=""),
        # "trigger" es palabra reservada de SQL; SQLAlchemy la cita
        # automáticamente en el DDL generado (no requiere `"trigger"` a mano).
        sa.Column(
            "trigger", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "accion", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("next_run_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_run_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        *_timestamp_columns(),
    )
    op.create_index("ix_automations_tenant_id", "automations", ["tenant_id"])

    op.create_table(
        "automation_runs",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "automation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column(
            "detalle", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'error', 'waiting_confirmation')", name="status"
        ),
    )
    op.create_index("ix_automation_runs_tenant_id", "automation_runs", ["tenant_id"])

    # ========================================================================
    # devices / remote_sessions (WP-V2-08/WP-V2-09)
    # ========================================================================

    op.create_table(
        "devices",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column("plataforma", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("last_seen_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("fingerprint", sa.String(), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint("kind IN ('companion', 'mobile')", name="kind"),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="status"),
    )
    op.create_index("ix_devices_tenant_id", "devices", ["tenant_id"])

    op.create_table(
        "remote_sessions",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(), nullable=False, server_default="view"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ended_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("frames_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        *_timestamp_columns(),
        sa.CheckConstraint("status IN ('pending', 'active', 'ended', 'denied')", name="status"),
    )
    op.create_index("ix_remote_sessions_tenant_id", "remote_sessions", ["tenant_id"])

    # ========================================================================
    # orders / holdings / budgets (WP-V2-10 — dinero: plomería + gate
    # permanente, ROADMAP_V2.md §8.1)
    # ========================================================================

    op.create_table(
        "orders",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("monto", sa.Numeric(14, 2), nullable=True),
        sa.Column("moneda", sa.CHAR(3), nullable=False, server_default="USD"),
        sa.Column("simbolo", sa.String(), nullable=True),
        sa.Column("lado", sa.String(), nullable=True),
        sa.Column("cantidad", sa.Numeric(20, 8), nullable=True),
        sa.Column(
            "meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("confirmed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("executed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint("kind IN ('payment', 'purchase', 'trade')", name="kind"),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed', 'executed_paper', 'cancelled', 'expired')",
            name="status",
        ),
    )
    op.create_index("ix_orders_tenant_id", "orders", ["tenant_id"])

    op.create_table(
        "holdings",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("simbolo", sa.String(), nullable=False),
        sa.Column("cantidad", sa.Numeric(20, 8), nullable=False),
        sa.Column("costo_promedio", sa.Numeric(14, 4), nullable=False),
        sa.Column("moneda", sa.CHAR(3), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="paper"),
        *_timestamp_columns(),
    )
    op.create_index("ix_holdings_tenant_id", "holdings", ["tenant_id"])

    op.create_table(
        "budgets",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("categoria", sa.String(), nullable=False),
        sa.Column("monto_mensual", sa.Numeric(14, 2), nullable=False),
        sa.Column("moneda", sa.CHAR(3), nullable=False, server_default="USD"),
        *_timestamp_columns(),
    )
    op.create_index("ix_budgets_tenant_id", "budgets", ["tenant_id"])

    # ========================================================================
    # invoices / invoice_items (WP-V2-12)
    # ========================================================================

    op.create_table(
        "invoices",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("numero", sa.String(), nullable=False),
        sa.Column("cliente_nombre", sa.String(), nullable=False),
        sa.Column("cliente_email", sa.String(), nullable=True),
        sa.Column("moneda", sa.CHAR(3), nullable=False, server_default="USD"),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False),
        sa.Column("impuestos", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("total", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "pdf_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notas", sa.Text(), nullable=False, server_default=""),
        *_timestamp_columns(),
        sa.CheckConstraint("status IN ('draft', 'sent', 'paid', 'void')", name="status"),
    )
    op.create_index("ix_invoices_tenant_id", "invoices", ["tenant_id"])

    op.create_table(
        "invoice_items",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("cantidad", sa.Numeric(12, 2), nullable=False),
        sa.Column("precio_unitario", sa.Numeric(14, 2), nullable=False),
        sa.Column("total", sa.Numeric(14, 2), nullable=False),
        *_timestamp_columns(),
    )
    op.create_index("ix_invoice_items_tenant_id", "invoice_items", ["tenant_id"])

    # ========================================================================
    # health_logs / learning_progress (WP-V2-11 — asesores: disclaimers
    # obligatorios los agrega esa herramienta, no esta migración)
    # ========================================================================

    op.create_table(
        "health_logs",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column(
            "valor", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("notas", sa.Text(), nullable=True),
        sa.Column(
            "registrado_en",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "kind IN ('medicamento', 'ejercicio', 'sueno', 'agua', 'habito', 'medida')",
            name="kind",
        ),
    )
    op.create_index("ix_health_logs_tenant_id", "health_logs", ["tenant_id"])

    op.create_table(
        "learning_progress",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("tema", sa.String(), nullable=False),
        sa.Column("nivel", sa.String(), nullable=False, server_default="inicial"),
        sa.Column("leccion", postgresql.JSONB(), nullable=True),
        sa.Column("resultados", postgresql.JSONB(), nullable=True),
        *_timestamp_columns(),
    )
    op.create_index("ix_learning_progress_tenant_id", "learning_progress", ["tenant_id"])

    # ========================================================================
    # user_profiles (WP-V2-13)
    # ========================================================================

    op.create_table(
        "user_profiles",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("resumen", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "datos", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamp_columns(),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_user_profiles_tenant_id_user_id"),
    )
    op.create_index("ix_user_profiles_tenant_id", "user_profiles", ["tenant_id"])

    # ========================================================================
    # Grants + Row-Level Security — ver docstring del módulo (puntos 3 y 4).
    # ========================================================================

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")

    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    # Sin REVOKE: a diferencia de `0001_initial` (que revoca sobre TODAS las
    # tablas del schema porque está deshaciendo la instalación completa del
    # rol), aquí `DROP TABLE` ya retira los grants de estas 14 tablas por sí
    # solo — un REVOKE de "ALL TABLES IN SCHEMA public" también le quitaría
    # privilegios a las tablas de v1, que esta migración no debe tocar.
    for table in reversed(ALL_TABLES_IN_ORDER):
        op.drop_table(table)
