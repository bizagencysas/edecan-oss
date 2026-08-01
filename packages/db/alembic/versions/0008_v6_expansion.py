"""0008_v6_expansion

Migración v6 de Edecán, escrita a mano (`ARCHITECTURE.md` §15, dueño
WP-V6-01, mismo patrón que `0001_initial`/`0003_v2_expansion`/
`0004_v3_expansion`/`0006_v4_expansion`/`0007_v5_expansion` — ver el
docstring de `0001_initial` para el razonamiento completo de cada pieza;
aquí solo se anotan las diferencias):

1. Las 2 tablas nuevas de §15 (mismas columnas/tipos que las 2 clases nuevas
   de `edecan_db.models`, sección "v6" al final de ese módulo):
   - `meetings`: reunión resumida por la tool `resumir_reunion` (`tools.
     meetings`). `status` lleva CHECK (`pending|running|done|error`) — el job
     `process_meeting` la mueve de `pending` a `running` y luego a
     `done`/`error`.
   - `podcasts`: podcast creado vía `POST /v1/voz/podcasts` (`tools.
     podcast`). Mismo vocabulario/CHECK de `status` que `meetings`; el job
     `generate_podcast` (ya existente desde v5) la mueve igual.
   Ninguna de las dos tiene FK entre sí ni requiere orden de creación
   particular (a diferencia de `employees`/`time_off` en 0007).
2. Un índice por cada columna `tenant_id` — igual que las migraciones previas.
3. Actualiza el `CHECK` de `jobs.type` para sumar el 12º job type
   (`process_meeting`, `edecan_schemas.queue.JOB_TYPES`) — mismo patrón
   `DROP CONSTRAINT` + `ADD CONSTRAINT` que ya usaron `0005_jobs_type_check_v2_types`
   y `0007_v5_expansion` (Postgres no soporta modificar la expresión de un
   CHECK existente in place); mismo nombre de constraint literal `"type"`.
   Sin esto, `QUEUE_PROVIDER="db"` (§12.g) rechazaría cualquier `INSERT INTO
   jobs` con `type='process_meeting'` con un `CheckViolationError`.
4. `GRANT` explícito a `app_user` sobre las tablas nuevas — misma red de
   seguridad idempotente que `0003`/`0004`/`0006`/`0007` documentan.
5. `ENABLE ROW LEVEL SECURITY` + política `tenant_isolation` en las 2 tablas
   nuevas (ninguna trae excepción declarada en §15: ambas tenant-scoped).
   Sin `FORCE ROW LEVEL SECURITY`, mismo motivo que las migraciones previas.

Igual que `0001_initial`/`0003`/`0004`/`0006`/`0007`, esta migración declara
su propia tupla `RLS_TABLES` en vez de importar `edecan_db.models` —
`test_migration_rls_tables.py` (packages/db/tests/) es el cross-check que
suma las `RLS_TABLES` de TODAS las migraciones y las compara contra
`edecan_db.models.RLS_TABLES`; ese archivo se extiende junto con esta
migración (mismo criterio que su propio docstring pide).

Igual que las migraciones previas, este archivo NO importa `edecan_db.models`
(helpers locales `_id_column`/`_timestamp_columns`/`_tenant_id_column`/
`_user_id_column` duplicados a propósito) para quedar como una foto fija e
independiente de cómo evolucionen los modelos del ORM más adelante.

Revision ID: 0008_v6_expansion
Revises: 0007_v5_expansion
Create Date: 2026-07-09 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008_v6_expansion"
down_revision: str | None = "0007_v5_expansion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APP_ROLE = "app_user"

# Las 2 tablas de `ARCHITECTURE.md` §15. Todas tenant-scoped con RLS — ver
# docstring del módulo para por qué esta tupla hace doble uso (grants/RLS de
# `upgrade()` y cross-check de `test_migration_rls_tables.py`).
RLS_TABLES: tuple[str, ...] = ("meetings", "podcasts")
ALL_TABLES_IN_ORDER: tuple[str, ...] = RLS_TABLES

# Mismo vocabulario que `edecan_schemas.queue.JOB_TYPES` (§10.5) y
# `edecan_db.models.Job.__table_args__` (`_enum_check("type", ...)`, que esta
# migración deja en sincronía) — los 11 de v1+v2+v5 + el nuevo de v6, en el
# mismo orden que esas dos fuentes. Ver `0005_jobs_type_check_v2_types.py`/
# `0007_v5_expansion.py` para el mismo patrón sobre este constraint (esos
# archivos cubren v1+v2 y +v5 respectivamente; este extiende la lista con el
# valor de v6).
_JOB_TYPES_V1_V2_V5: tuple[str, ...] = (
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
)
_JOB_TYPES_V6: tuple[str, ...] = ("process_meeting",)
_ALL_JOB_TYPES: tuple[str, ...] = _JOB_TYPES_V1_V2_V5 + _JOB_TYPES_V6


def _job_type_check_sql(job_types: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in job_types)
    return f"type IN ({quoted})"


# ---------------------------------------------------------------------------
# Helpers locales (idénticos a los de `0001_initial`/`0003_v2_expansion`/
# `0004_v3_expansion`/`0006_v4_expansion`/`0007_v5_expansion`, duplicados a
# propósito — ver docstring del módulo).
# ---------------------------------------------------------------------------


def _id_column() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def _tenant_id_column(*, nullable: bool = False, ondelete: str = "CASCADE") -> sa.Column:
    return sa.Column(
        "tenant_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete=ondelete),
        nullable=nullable,
    )


def _user_id_column() -> sa.Column:
    return sa.Column(
        "user_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    # ========================================================================
    # meetings (reuniones, `tools.meetings`)
    # ========================================================================

    op.create_table(
        "meetings",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("titulo", sa.Text(), nullable=False, server_default=""),
        # Sin FK a propósito (referencia informativa a `files`, ver docstring
        # del módulo / `edecan_db.models.Meeting`).
        sa.Column("source_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("transcript_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resumen", sa.Text(), nullable=True),
        sa.Column("minutos", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duracion_segundos", sa.Integer(), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint("status IN ('pending', 'running', 'done', 'error')", name="status"),
    )
    op.create_index("ix_meetings_tenant_id", "meetings", ["tenant_id"])

    # ========================================================================
    # podcasts (voz avanzada, `tools.podcast`, router `voz_avanzada`)
    # ========================================================================

    op.create_table(
        "podcasts",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("guion", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        # Sin FK a propósito, mismo criterio que `meetings.source_file_id`.
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint("status IN ('pending', 'running', 'done', 'error')", name="status"),
    )
    op.create_index("ix_podcasts_tenant_id", "podcasts", ["tenant_id"])

    # ========================================================================
    # CHECK de `jobs.type` (ver docstring del módulo, punto 3).
    # ========================================================================

    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_ALL_JOB_TYPES))

    # ========================================================================
    # Grants + Row-Level Security — ver docstring del módulo (puntos 4 y 5).
    # ========================================================================

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")

    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    op.drop_constraint("type", "jobs", type_="check")
    op.create_check_constraint("type", "jobs", _job_type_check_sql(_JOB_TYPES_V1_V2_V5))

    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    # Sin REVOKE: mismo motivo que `0003`/`0004`/`0006`/`0007` `.downgrade()`
    # — `DROP TABLE` ya retira los grants de estas tablas por sí solo, y un
    # REVOKE de "ALL TABLES IN SCHEMA public" también le quitaría privilegios
    # a las tablas de v1-v5, que esta migración no debe tocar.
    for table in reversed(ALL_TABLES_IN_ORDER):
        op.drop_table(table)
