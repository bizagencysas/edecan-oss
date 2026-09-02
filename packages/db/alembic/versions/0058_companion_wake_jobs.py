"""0058_companion_wake_jobs

Extiende el CHECK de `jobs.type` con los job types de workers persistentes y
proactivos que ya estaban en `edecan_schemas.queue.JOB_TYPES` pero faltaban en
Postgres, más `run_companion_turn` y `companion_wake_scan` (turno proactivo REAL
del companion: el reloj solo despierta, el modelo decide si escribe).

Revision ID: 0058_companion_wake_jobs
Revises: 0057_agent_messages
Create Date: 2026-08-27 14:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0058_companion_wake_jobs"
down_revision: str | None = "0057_agent_messages"
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
    "notify_important_event",
    "create_linkedin_post",
    "create_organization_social_post",
)
_JOB_TYPES_CURRENT: tuple[str, ...] = _JOB_TYPES_PREVIOUS + (
    "run_persistent_agent",
    "persistent_agent_scan",
    "proactive_scan",
    "run_companion_turn",
    "companion_wake_scan",
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
