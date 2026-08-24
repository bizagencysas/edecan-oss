"""0028_job_create_linkedin_post

Agrega el job_type `create_linkedin_post` al CHECK de `jobs.type`. El post de
LinkedIn deja de correr inline en el turno del chat y pasa a un job en segundo
plano (escritor "profundo"/nemotron + auditor + gates + imagen, con tiempo de
sobra), entregado como card + push. Sin esta migración el INSERT en `jobs`
(rama `QUEUE_PROVIDER=db`, la de la app de escritorio) viola el CHECK.

Revision ID: 0028_job_create_linkedin_post
Revises: 0027_conversation_chat_model
Create Date: 2026-07-30 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028_job_create_linkedin_post"
down_revision: str | None = "0027_conversation_chat_model"
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
)
_JOB_TYPES_CURRENT = _JOB_TYPES_PREVIOUS + ("create_linkedin_post",)


def _job_type_check_sql(job_types: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in job_types)
    return f"type IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_CURRENT))


def downgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_PREVIOUS))
