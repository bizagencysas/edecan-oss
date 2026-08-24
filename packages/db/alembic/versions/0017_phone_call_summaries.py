"""0017_phone_call_summaries

Resumen estructurado e idempotente al terminar una llamada y estado de la
notificación push best-effort. También registra el job dedicado que consume
el worker después de que el resumen ya quedó confirmado en PostgreSQL.

Revision ID: 0017_phone_call_summaries
Revises: 0016_phone_agent_templates
Create Date: 2026-07-21 23:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_phone_call_summaries"
down_revision: str | None = "0016_phone_agent_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOB_TYPES_PREVIOUS: tuple[str, ...] = (
    "ingest_file",
    "sync_connector",
    "send_reminder",
    "send_reminder_scan",
    "run_campaign_step",
    "generate_content",
    "memory_consolidate",
    "run_mission",
    "run_automation",
    "automation_scan",
    "generate_podcast",
    "process_meeting",
)
_JOB_TYPES_CURRENT: tuple[str, ...] = _JOB_TYPES_PREVIOUS + ("notify_phone_call_summary",)


def _job_type_check_sql(job_types: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in job_types)
    return f"type IN ({quoted})"


def upgrade() -> None:
    op.add_column(
        "phone_calls",
        sa.Column("summary", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "phone_calls",
        sa.Column("summary_generated_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "phone_calls",
        sa.Column("summary_push_attempted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )

    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_CURRENT))


def downgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_PREVIOUS))

    op.drop_column("phone_calls", "summary_push_attempted_at")
    op.drop_column("phone_calls", "summary_generated_at")
    op.drop_column("phone_calls", "summary")
