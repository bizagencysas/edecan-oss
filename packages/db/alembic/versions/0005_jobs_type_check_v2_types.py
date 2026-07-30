"""0005_jobs_type_check_v2_types

Corrige un `CHECK constraint` dormido desde v2: `jobs.type` (creado en
`0001_initial`) solo permitía los 7 job types de v1 y NUNCA se actualizó
cuando `edecan_schemas.queue.JOB_TYPES` sumó 3 valores más en v2
(`run_mission`, `run_automation`, `automation_scan`) — `edecan_db.models.Job`
(`_enum_check("type", ...)`) duplicaba exactamente la misma lista stale de 7.
`edecan_worker.handlers.HANDLERS` sí registra handlers para los 10 desde v2
(`run_mission.py`/`run_automation.py`/`automation_scan.py`), así que el hueco
era solo en el CHECK de Postgres.

Mientras `QUEUE_PROVIDER="sqs"` (default histórico, `edecan_core.queue`)
esto quedaba dormido porque SQS nunca toca la tabla `jobs`. Pero
`QUEUE_PROVIDER="db"` (`edecan_core.queue._enqueue_db`, pensado para el
runner de escritorio/local de v3, `ARCHITECTURE.md` §12f/§12g) hace
`INSERT INTO jobs (tenant_id, type, payload, status, attempts) VALUES (...)`
directo contra esta tabla — con el CHECK viejo, un `INSERT` con `type` en
`{"run_mission", "run_automation", "automation_scan"}` viola el constraint
SIEMPRE. Efectos concretos verificados en el código antes de este fix:
- `edecan_local.worker_loop._run_scheduler_tick` (`SCHEDULED_JOB_TYPES`
  incluye `"automation_scan"`) se encola sin condición cada 30s; el
  `except Exception` amplio de ese tick evita que el worker se caiga, pero
  el escaneo de automatizaciones queda roto en silencio para siempre en modo
  escritorio/local.
- `POST /missions` (`edecan_api.routers.missions.create_mission`, encola
  `"run_mission"`) y `POST /automations/{id}/probar`
  (`edecan_api.routers.automations.probar_automation`, encola
  `"run_automation"`) llaman `enqueue(...)` sin `try/except` alrededor: bajo
  `QUEUE_PROVIDER="db"` el `CheckViolationError` de Postgres se propaga sin
  capturar hasta FastAPI y esos endpoints devuelven 500 siempre.

`DROP CONSTRAINT` + `ADD CONSTRAINT` porque Postgres no soporta modificar la
expresión de un CHECK existente in place. El nombre del constraint sigue
siendo literal `"type"` (NO `ck_jobs_type`): `0001_initial.py` arma la tabla
a mano vía `op.create_table(...)` con una `MetaData()` interna de Alembic que
NO trae `naming_convention` (a diferencia de `edecan_db.models.Base.metadata`,
que sí define uno con clave `"ck"` — ver `edecan_db.models._enum_check` y su
docstring), así que Alembic nunca reprocesó el `name="type"` que se le pasó
al `CheckConstraint` original en `0001_initial` y el nombre real del
constraint en Postgres quedó tal cual, sin prefijo. Confirmado compilando la
DDL real de ambos casos (migración cruda vs. `Base.metadata` con
`naming_convention`) contra el dialecto `postgresql` antes de escribir esta
migración: la tabla creada por `0001_initial` tiene el constraint nombrado
`type`, no `ck_jobs_type`.

Esta migración NO agrega tablas tenant-scoped nuevas (solo altera un CHECK
existente en `jobs`, ya cubierta por la política RLS de `0001_initial`), así
que no necesita tupla `RLS_TABLES` propia ni suma nada a
`test_migration_rls_tables.py` (ver la nota de ese archivo sobre "la PRÓXIMA
migración 0005_*": esa nota aplica solo si la migración crea tablas nuevas).

Revision ID: 0005_jobs_type_check_v2_types
Revises: 0004_v3_expansion
Create Date: 2026-07-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_jobs_type_check_v2_types"
down_revision: str | None = "0004_v3_expansion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Mismo vocabulario que `edecan_schemas.queue.JOB_TYPES` (§10.5) y
# `edecan_db.models.Job.__table_args__` (`_enum_check("type", ...)`, que esta
# migración deja en sincronía) — los 7 de v1 + los 3 nuevos de v2, en el
# mismo orden que esas dos fuentes.
_JOB_TYPES_V1: tuple[str, ...] = (
    "ingest_file",
    "sync_connector",
    "send_reminder",
    "send_reminder_scan",
    "run_campaign_step",
    "generate_content",
    "memory_consolidate",
)
_JOB_TYPES_V2: tuple[str, ...] = (
    "run_mission",
    "run_automation",
    "automation_scan",
)
_ALL_JOB_TYPES: tuple[str, ...] = _JOB_TYPES_V1 + _JOB_TYPES_V2


def _check_sql(job_types: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in job_types)
    return f"type IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _check_sql(_ALL_JOB_TYPES))


def downgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _check_sql(_JOB_TYPES_V1))
