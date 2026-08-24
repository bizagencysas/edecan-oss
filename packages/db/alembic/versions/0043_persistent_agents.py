"""0043_persistent_agents

Registro durable de identidad, permisos, presupuesto y checkpoints de workers
always-on. Esta migración no crea jobs ni habilita ejecución automática.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_persistent_agents"
down_revision: str | None = "0042_mission_pause_resume"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = ("persistent_agents",)
ALL_TABLES_IN_ORDER = RLS_TABLES


def upgrade() -> None:
    op.create_table(
        "persistent_agents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("workspace", sa.Text(), nullable=True),
        sa.Column(
            "tools", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "permissions", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "memory", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "schedule", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "budget", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="idle"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "last_checkpoint",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "user_id", "name", name="uq_persistent_agents_owner_name"),
        sa.CheckConstraint("status IN ('idle', 'running', 'paused', 'disabled')", name="status"),
    )
    op.create_index("ix_persistent_agents_tenant_id", "persistent_agents", ["tenant_id"])
    op.create_index(
        "ix_persistent_agents_tenant_user", "persistent_agents", ["tenant_id", "user_id"]
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON persistent_agents TO {APP_ROLE}")
    op.execute("ALTER TABLE persistent_agents ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON persistent_agents "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON persistent_agents")
    op.drop_table("persistent_agents")
