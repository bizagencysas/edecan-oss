"""0057_agent_messages

Protocolo inter-agente durable (product design): `agent_messages` es la cola
persistente de mensajes entre agentes y hacia/desde el asistente principal
(`sender_agent_id`/`receiver_agent_id` NULLABLE = "main assistant/user", sin
worker persistente en ese extremo — mismo criterio que
`agent_missions.owner_agent_id` de `0049_pending_approvals`).

Context packaging (§12): `context_refs`/`artifact_refs` guardan SOLO
referencias (`{kind, id}`), nunca el transcript completo. El receptor resuelve
esas referencias con sus propias consultas scoped por tenant vía RLS; volcar el
transcript aquí duplicaría contexto sensible y rompería "el receptor solo
recibe lo necesario".

Sigue el patrón RLS de las migraciones recientes: `GRANT` a `app_user` +
`ENABLE ROW LEVEL SECURITY` + policy `tenant_isolation`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057_agent_messages"
down_revision: str | None = "0056_mcp_server_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = ("agent_messages",)


def upgrade() -> None:
    op.create_table(
        "agent_messages",
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
            "sender_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persistent_agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "receiver_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persistent_agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("parent_task_id", sa.String(), nullable=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("message_type", sa.String(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("expected_output", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dependencies", postgresql.JSONB(), nullable=True),
        sa.Column("allowed_tools", postgresql.JSONB(), nullable=True),
        sa.Column("approval_boundary", postgresql.JSONB(), nullable=True),
        sa.Column("artifact_refs", postgresql.JSONB(), nullable=True),
        sa.Column("context_refs", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "message_type IN ('task', 'question', 'result', 'blocker', 'review_request', "
            "'handoff', 'status', 'cancel')",
            name="message_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'acknowledged', 'done', 'error')",
            name="status",
        ),
    )
    op.create_index(
        "ix_agent_messages_tenant_receiver_status",
        "agent_messages",
        ["tenant_id", "receiver_agent_id", "status"],
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
