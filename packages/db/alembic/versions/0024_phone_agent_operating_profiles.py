"""0024_phone_agent_operating_profiles

Convierte cada agente telefónico en un perfil operativo independiente y
congela ese perfil, junto con la identidad del destinatario, en cada llamada.

Revision ID: 0024_phone_agent_operating_profiles
Revises: 0023_phone_agent_voices
Create Date: 2026-07-23 11:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_phone_agent_operating_profiles"
down_revision: str | None = "0023_phone_agent_voices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "phone_agent_templates",
        sa.Column(
            "operating_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "phone_agent_templates",
        sa.Column("handles_inbound", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "phone_agent_templates",
        sa.Column("handles_outbound", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "phone_agent_templates",
        sa.Column("is_inbound_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE phone_agent_templates
        SET is_inbound_default = is_default
        WHERE is_default
        """
    )
    op.create_index(
        "uq_phone_agent_templates_inbound_default",
        "phone_agent_templates",
        ["tenant_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("is_inbound_default"),
    )

    op.add_column("phone_calls", sa.Column("recipient_name", sa.Text(), nullable=True))
    op.add_column(
        "phone_calls",
        sa.Column(
            "agent_operating_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("phone_calls", "agent_operating_profile")
    op.drop_column("phone_calls", "recipient_name")
    op.drop_index(
        "uq_phone_agent_templates_inbound_default",
        table_name="phone_agent_templates",
    )
    op.drop_column("phone_agent_templates", "is_inbound_default")
    op.drop_column("phone_agent_templates", "handles_outbound")
    op.drop_column("phone_agent_templates", "handles_inbound")
    op.drop_column("phone_agent_templates", "operating_profile")
