"""0032_job_create_organization_linkedin_post

Agrega el job_type `create_organization_linkedin_post` al CHECK de `jobs.type`. La
variante product-led del post de LinkedIn para la página de Acme deja de
correr inline en el turno del chat y pasa a un job en segundo plano (fydesign
elige la pantalla + brief, modelo directo escribe el cuerpo, visual de
fydesign, entregado como card + push). Sin esta migración el INSERT en `jobs`
(rama `QUEUE_PROVIDER=db`, la de la app de escritorio) viola el CHECK.

Revision ID: 0032_job_dc_linkedin_post
Revises: 0031_conv_context_cleared
Create Date: 2026-08-14 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032_job_dc_linkedin_post"
down_revision: str | None = "0031_conv_context_cleared"
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
)
_JOB_TYPES_CURRENT = _JOB_TYPES_PREVIOUS + ("create_organization_linkedin_post",)


def _job_type_check_sql(job_types: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in job_types)
    return f"type IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_CURRENT))


def downgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_PREVIOUS))
