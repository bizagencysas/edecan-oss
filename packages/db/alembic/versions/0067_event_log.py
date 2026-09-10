"""0067_event_log

Plataforma de logging TOTAL: tabla `event_log` que captura cualquier movimiento
de la app (de dónde sale cada push, dónde hizo X, dónde guardó X) con
`(tenant_id, categoria, accion, detalle jsonb)`, y auto-eliminación pasados 7
días mediante el job diario `event_log_cleanup` (`edecan_worker.handlers.
event_log_cleanup`, encolado desde `edecan_local.worker_loop`). La tabla no
puede crecer infinita: el DELETE diario corta en `created_at < now() - interval
'7 days'` y el índice `ix_event_log_created_at` lo hace barato.

`tenant_id` es NULLable a propósito: algunos eventos son globales (p. ej. el
propio barrido de limpieza) y el log es un registro de hechos, no una tabla de
negocio con dueño obligatorio. Igual se aplica el mismo trío RLS que el resto
de tablas tenant-scoped (GRANT a `app_user` + `ENABLE ROW LEVEL SECURITY` +
política `tenant_isolation`): el rol de la API solo puede leer/escribir las
filas de su propio tenant, mientras que el worker (rol "dueño", bypassa RLS,
ARCHITECTURE.md §2) escribe y barre sin restricción.

El mismo patch agrega `event_log_cleanup` al CHECK de `jobs.type` — mismo
patrón drop+create que las migraciones de job types previas (0066): sin esto,
encolar el barrido diario fallaría con violación del CHECK.

Revision ID: 0067_event_log
Revises: 0066_job_refresh_skills
Create Date: 2026-09-05 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0067_event_log"
down_revision: str | None = "0066_job_refresh_skills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = ("event_log",)

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
    "refresh_skills",
)
_JOB_TYPES_CURRENT: tuple[str, ...] = _JOB_TYPES_PREVIOUS + ("event_log_cleanup",)


def _job_type_check_sql(job_types: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in job_types)
    return f"type IN ({quoted})"


def upgrade() -> None:
    op.create_table(
        "event_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("categoria", sa.Text(), nullable=False),
        sa.Column("accion", sa.Text(), nullable=False),
        sa.Column(
            "detalle",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_event_log_created_at", "event_log", ["created_at"], unique=False)

    for table in RLS_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )

    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_CURRENT))


def downgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_PREVIOUS))

    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.drop_table(table)
