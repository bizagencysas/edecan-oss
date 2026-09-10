"""0069_job_types_union_fix

E-INF-1: la migración 0068 reconstruyó el CHECK de `jobs.type` SIN dos tipos
reales (`run_campaign_step` y `companion_wake_scan`). Como 0068 ya se aplicó
en entornos vivos (el VPS está en head 0068 con la lista de 25), corregir su
fuente no re-corre nada: esta migración re-expande el CHECK a la unión real
de `edecan_schemas.queue.JOB_TYPES` (27 tipos).

Usa SQL directo con `IF EXISTS` porque el nombre del CHECK divergió entre
entornos (`ck_jobs_type` en el VPS; `type` en la fuente original de 0068).
"""
from __future__ import annotations

from alembic import op

revision: str = "0069_job_types_union_fix"
down_revision: str = "0067_event_log"
branch_labels = None
depends_on = None

# Unión real de JOB_TYPES (27) — pinneada por
# `packages/db/tests/test_db_models.py::test_job_type_check_del_modelo_es_la_union_real_de_job_types`.
_JOB_TYPES_UNION: tuple[str, ...] = (
    "automation_scan",
    "companion_wake_scan",
    "create_organization_linkedin_post",
    "create_linkedin_post",
    "event_log_cleanup",
    "generate_content",
    "generate_podcast",
    "ingest_file",
    "memory_consolidate",
    "notify_important_event",
    "notify_incoming_phone_call",
    "notify_phone_call_summary",
    "persistent_agent_scan",
    "proactive_scan",
    "process_meeting",
    "refresh_skills",
    "run_automation",
    "run_campaign_step",
    "run_companion_turn",
    "run_mission",
    "run_persistent_agent",
    "send_reminder",
    "send_reminder_scan",
    "sync_connector",
)


def _check_sql(job_types: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in job_types)
    return f"type IN ({quoted})"


def upgrade() -> None:
    # Dos nombres posibles por historias divergentes; IF EXISTS tolera ambos.
    op.execute('ALTER TABLE jobs DROP CONSTRAINT IF EXISTS "type"')
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_type")
    op.execute(
        f"ALTER TABLE jobs ADD CONSTRAINT ck_jobs_type CHECK ({_check_sql(_JOB_TYPES_UNION)})"
    )


def downgrade() -> None:
    # Restaura la intención original de 0068 (sin los 2 tipos que perdió).
    previos = tuple(
        t for t in _JOB_TYPES_UNION if t not in ("run_campaign_step", "companion_wake_scan")
    )
    op.execute('ALTER TABLE jobs DROP CONSTRAINT IF EXISTS "type"')
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_type")
    op.execute(f"ALTER TABLE jobs ADD CONSTRAINT ck_jobs_type CHECK ({_check_sql(previos)})")
