"""0019_universal_tool_notifications

Permite encolar avisos de herramientas sincrónicas usando exclusivamente
vocabulario controlado e identificadores UUID.

Revision ID: 0019_universal_notify
Revises: 0018_phone_incoming_push
Create Date: 2026-07-22 01:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019_universal_notify"
down_revision: str | None = "0018_phone_incoming_push"
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
    "notify_phone_call_summary",
    "notify_incoming_phone_call",
)
_JOB_TYPES_CURRENT = _JOB_TYPES_PREVIOUS + ("notify_important_event",)


def _job_type_check_sql(job_types: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in job_types)
    return f"type IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_CURRENT))


def downgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_PREVIOUS))
