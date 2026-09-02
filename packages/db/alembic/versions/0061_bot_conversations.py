"""0061_bot_conversations

Chat dedicado por bot (`persistent_agents.conversation_id`) y salas 1:1 entre
bots (`agent_direct_chats`) — modelo Grok Bot en Edecán.app.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061_bot_conversations"
down_revision: str | None = "0058_companion_wake_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = ("agent_direct_chats",)


def upgrade() -> None:
    op.add_column(
        "persistent_agents",
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_persistent_agents_conversation_id",
        "persistent_agents",
        ["conversation_id"],
        unique=False,
    )

    op.create_table(
        "agent_direct_chats",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_a_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persistent_agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_b_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persistent_agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("agent_a_id <> agent_b_id", name="ck_agent_direct_chats_distinct"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "agent_a_id",
            "agent_b_id",
            name="uq_agent_direct_chats_pair",
        ),
    )
    op.create_index(
        "ix_agent_direct_chats_conversation_id",
        "agent_direct_chats",
        ["conversation_id"],
        unique=False,
    )

    for table in RLS_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.drop_table(table)
    op.drop_index("ix_persistent_agents_conversation_id", table_name="persistent_agents")
    op.drop_column("persistent_agents", "conversation_id")
