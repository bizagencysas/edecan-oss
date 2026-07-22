"""0018_incoming_phone_call_notifications

Registra el job que entrega la actividad y el push genérico de una llamada
entrante después de que el webhook ya persistió la llamada y su evento.

Revision ID: 0018_phone_incoming_push
Revises: 0017_phone_call_summaries
Create Date: 2026-07-22 00:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018_phone_incoming_push"
down_revision: str | None = "0017_phone_call_summaries"
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
)
_JOB_TYPES_CURRENT: tuple[str, ...] = _JOB_TYPES_PREVIOUS + (
    "notify_incoming_phone_call",
)


def _job_type_check_sql(job_types: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in job_types)
    return f"type IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_CURRENT))


def downgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_PREVIOUS))
