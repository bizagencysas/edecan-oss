"""0047_linkedin_personal_one_perfect_daily

Daily editorial state for the PERSONAL LinkedIn profile ("One Perfect Daily"):
candidate pool, a single autonomous final per local date, proactive signals and
intent/feedback metadata on `social_drafts`.

Does not touch the job or the organization-page automations.

Revision ID: 0047_li_personal_daily
Revises: 0046_provider_health_events
Create Date: 2026-08-25 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_li_personal_daily"
down_revision: str | None = "0046_provider_health_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"

RLS_TABLES: tuple[str, ...] = (
    "linkedin_personal_daily_state",
    "linkedin_personal_signals",
)
ALL_TABLES_IN_ORDER: tuple[str, ...] = RLS_TABLES


def upgrade() -> None:
    op.create_table(
        "linkedin_personal_daily_state",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
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
        sa.Column("platform", sa.Text(), nullable=False, server_default="linkedin_personal"),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="UTC"),
        sa.Column(
            "candidate_pool",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("autonomous_final_post_id", sa.Text(), nullable=True),
        sa.Column("autonomous_final_status", sa.Text(), nullable=True),
        sa.Column("scouting_runs", sa.Integer(), nullable=False, server_default=sa.text("0")),
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
            "platform",
            "local_date",
            name="uq_linkedin_personal_daily_state_day",
        ),
    )
    op.create_index(
        "ix_linkedin_personal_daily_state_tenant_id",
        "linkedin_personal_daily_state",
        ["tenant_id"],
    )
    # A single autonomous final per day: two ticks cannot mark two distinct ids.
    op.create_index(
        "uq_linkedin_personal_one_auto_final",
        "linkedin_personal_daily_state",
        ["tenant_id", "user_id", "platform", "local_date"],
        unique=True,
        postgresql_where=sa.text("autonomous_final_post_id IS NOT NULL"),
    )

    op.create_table(
        "linkedin_personal_signals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
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
        sa.Column("platform", sa.Text(), nullable=False, server_default="linkedin_personal"),
        sa.Column("senal", sa.Text(), nullable=False),
        sa.Column("sensibilidad", sa.Text(), nullable=False, server_default="ask"),
        sa.Column("provenance", sa.Text(), nullable=False, server_default="user_supplied_turn"),
        sa.Column("conversation_id", sa.Text(), nullable=True),
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
    )
    op.create_index(
        "ix_linkedin_personal_signals_tenant_id",
        "linkedin_personal_signals",
        ["tenant_id"],
    )

    op.add_column("social_drafts", sa.Column("generation_mode", sa.Text(), nullable=True))
    op.add_column(
        "social_drafts",
        sa.Column("intent_lock", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "social_drafts",
        sa.Column("fact_ledger", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "social_drafts",
        sa.Column("editorial_feedback", postgresql.JSONB(), nullable=True),
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    op.drop_column("social_drafts", "editorial_feedback")
    op.drop_column("social_drafts", "fact_ledger")
    op.drop_column("social_drafts", "intent_lock")
    op.drop_column("social_drafts", "generation_mode")
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    for table in reversed(ALL_TABLES_IN_ORDER):
        op.drop_table(table)