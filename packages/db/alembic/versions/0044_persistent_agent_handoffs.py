"""0044_persistent_agent_handoffs"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_persistent_agent_handoffs"
down_revision: str | None = "0043_persistent_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = ("persistent_agent_handoffs",)
ALL_TABLES_IN_ORDER = RLS_TABLES


def upgrade() -> None:
    op.create_table(
        "persistent_agent_handoffs",
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
            "source_worker_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persistent_agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "destination_worker_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persistent_agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("envelope", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'running', 'done', 'rejected', 'error')",
            name="status",
        ),
    )
    op.create_index(
        "ix_persistent_agent_handoffs_tenant_id", "persistent_agent_handoffs", ["tenant_id"]
    )
    op.create_index(
        "ix_persistent_agent_handoffs_tenant_status",
        "persistent_agent_handoffs",
        ["tenant_id", "status"],
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON persistent_agent_handoffs TO {APP_ROLE}")
    op.execute("ALTER TABLE persistent_agent_handoffs ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON persistent_agent_handoffs "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON persistent_agent_handoffs")
    op.drop_table("persistent_agent_handoffs")
