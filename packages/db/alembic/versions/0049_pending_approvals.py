"""0049_pending_approvals

Aterriza el respaldo durable de las confirmaciones `dangerous` del chat
(directiva §30-32: "las aprobaciones no pueden expirar y morir"). Hasta hoy
`apps/api/edecan_api/routers/conversations.py` guarda el `PendingAgentTurn`
solo en Redis con TTL de 900 s, así que una aprobación pendiente muere al
reload. Esta tabla conserva el snapshot completo del turno suspendido
(`agent_snapshot`) para que `POST /v1/approvals/{id}/approve` pueda reanudar
después de reiniciar API/desktop; Redis sigue siendo el caché rápido.

En la misma migración se enlaza tarea → agente (directiva §11-13):
`agent_missions.owner_agent_id` (FK nullable a `persistent_agents.id`) marca
qué worker es dueño de la misión, y lo escribe `DelegarMisionTool` cuando
delega a otro worker (`destino_worker_id`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0049_pending_approvals"
down_revision: str | None = "0048_agent_identity_rich_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = ("pending_approvals",)
ALL_TABLES_IN_ORDER = RLS_TABLES


def upgrade() -> None:
    op.create_table(
        "pending_approvals",
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
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_call_id", sa.String(), nullable=False),
        # Snapshot completo del turno suspendido: `{name, args, pending_turn}` —
        # la misma forma que el payload de Redis de `conversations.py`, para que
        # `_resume_approved_turn` reanude desde esta foto exacta sin relanzar la
        # orden (ver docstring del módulo).
        sa.Column(
            "agent_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "tool_call_id",
            name="uq_pending_approvals_tenant_conversation_tool",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'expired')", name="status"
        ),
    )
    op.create_index("ix_pending_approvals_tenant_id", "pending_approvals", ["tenant_id"])
    op.create_index(
        "ix_pending_approvals_tenant_status",
        "pending_approvals",
        ["tenant_id", "status"],
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON pending_approvals TO {APP_ROLE}")
    op.execute("ALTER TABLE pending_approvals ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON pending_approvals "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )

    op.add_column(
        "agent_missions",
        sa.Column(
            "owner_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persistent_agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_missions", "owner_agent_id")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON pending_approvals")
    op.drop_table("pending_approvals")