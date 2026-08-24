"""0011_phone_calls

Persistencia OSS de llamadas como extensión de una conversación: una fila
resume el estado y otra conserva eventos/transcripción inmutables. Ambas son
tenant-scoped y llevan RLS.

Revision ID: 0011_phone_calls
Revises: 0010_tenant_lifetime_updates
Create Date: 2026-07-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_phone_calls"
down_revision: str | None = "0010_tenant_lifetime_updates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = ("phone_calls", "phone_call_events")
ALL_TABLES_IN_ORDER: tuple[str, ...] = RLS_TABLES


def _id_column() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _timestamps() -> list[sa.Column]:
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


def _tenant_id() -> sa.Column:
    return sa.Column(
        "tenant_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "phone_calls",
        _id_column(),
        _tenant_id(),
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
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("from_e164", sa.Text(), nullable=False),
        sa.Column("to_e164", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("provider", sa.Text(), nullable=False, server_default="twilio"),
        sa.Column("provider_call_sid", sa.Text(), nullable=True),
        sa.Column("confirmed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ended_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("direction IN ('incoming', 'outgoing')", name="direction"),
        sa.CheckConstraint(
            "status IN ('draft','confirmed','queued','ringing','in_progress',"
            "'completed','failed','busy','no_answer','cancelled')",
            name="status",
        ),
        sa.UniqueConstraint("provider_call_sid", name="uq_phone_calls_provider_call_sid"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_phone_calls_tenant_id_id"),
    )
    op.create_index("ix_phone_calls_tenant_id", "phone_calls", ["tenant_id"])
    op.create_index("ix_phone_calls_conversation_id", "phone_calls", ["conversation_id"])

    op.create_table(
        "phone_call_events",
        _id_column(),
        _tenant_id(),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "occurred_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["phone_calls.tenant_id", "phone_calls.id"],
            name="fk_phone_call_events_tenant_call",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_phone_call_events_tenant_id", "phone_call_events", ["tenant_id"])
    op.create_index("ix_phone_call_events_call_id", "phone_call_events", ["call_id"])

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
    for table in reversed(ALL_TABLES_IN_ORDER):
        op.drop_table(table)
