"""0016_phone_agent_templates

Plantillas reutilizables para agentes de llamadas salientes. Cada llamada
guarda una copia de la plantilla elegida para que una edición posterior no
cambie el comportamiento de un borrador ya revisado o de una llamada activa.

Revision ID: 0016_phone_agent_templates
Revises: 0015_memory_supersession
Create Date: 2026-07-21 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_phone_agent_templates"
down_revision: str | None = "0015_memory_supersession"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = ("phone_agent_templates",)


def upgrade() -> None:
    op.create_table(
        "phone_agent_templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
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
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("persona_prompt", sa.Text(), nullable=False),
        sa.Column("default_goal", sa.Text(), nullable=False),
        sa.Column("opening_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
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
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "name",
            name="uq_phone_agent_templates_tenant_user_name",
        ),
    )
    op.create_index(
        "ix_phone_agent_templates_tenant_id",
        "phone_agent_templates",
        ["tenant_id"],
    )
    op.create_index(
        "ix_phone_agent_templates_user_id",
        "phone_agent_templates",
        ["user_id"],
    )
    op.create_index(
        "uq_phone_agent_templates_default",
        "phone_agent_templates",
        ["tenant_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.add_column(
        "phone_calls",
        sa.Column("agent_template_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("phone_calls", sa.Column("agent_template_name", sa.Text(), nullable=True))
    op.add_column("phone_calls", sa.Column("agent_name", sa.Text(), nullable=True))
    op.add_column("phone_calls", sa.Column("agent_prompt", sa.Text(), nullable=True))
    op.add_column("phone_calls", sa.Column("opening_message", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_phone_calls_agent_template_id_phone_agent_templates",
        "phone_calls",
        "phone_agent_templates",
        ["agent_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_phone_calls_agent_template_id",
        "phone_calls",
        ["agent_template_id"],
    )

    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE phone_agent_templates TO {APP_ROLE}"
    )
    op.execute("ALTER TABLE phone_agent_templates ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON phone_agent_templates "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.drop_index("ix_phone_calls_agent_template_id", table_name="phone_calls")
    op.drop_constraint(
        "fk_phone_calls_agent_template_id_phone_agent_templates",
        "phone_calls",
        type_="foreignkey",
    )
    op.drop_column("phone_calls", "opening_message")
    op.drop_column("phone_calls", "agent_prompt")
    op.drop_column("phone_calls", "agent_name")
    op.drop_column("phone_calls", "agent_template_name")
    op.drop_column("phone_calls", "agent_template_id")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON phone_agent_templates")
    op.drop_table("phone_agent_templates")
