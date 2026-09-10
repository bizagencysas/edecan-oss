"""0066_job_refresh_skills

Agrega `refresh_skills` al CHECK de `jobs.type`: el refresco semanal de
skills de catálogos remotos se encola vía la tabla `jobs` (scheduler local,
`edecan_local.worker_loop`), y el CHECK de la tabla debe admitir el tipo
nuevo — mismo patrón drop+create que las migraciones de job types previas.

Revision ID: 0066_job_refresh_skills
Revises: 0065_remote_sessions_machine
Create Date: 2026-09-04 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0066_job_refresh_skills"
down_revision: str | None = "0065_remote_sessions_machine"
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
    "run_persistent_agent",
    "persistent_agent_scan",
    "proactive_scan",
    "run_companion_turn",
    "companion_wake_scan",
)
_JOB_TYPES_CURRENT: tuple[str, ...] = _JOB_TYPES_PREVIOUS + ("refresh_skills",)


def _job_type_check_sql(job_types: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in job_types)
    return f"type IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_CURRENT))


def downgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_PREVIOUS))
