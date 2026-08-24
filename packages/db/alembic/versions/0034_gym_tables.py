"""0034_gym_tables

Migración del feature "gimnasio" (`/v1/gym`), escrita a mano (mismo patrón
que `0001_initial`/`0003_v2_expansion`/`0004_v3_expansion`/
`0006_v4_expansion`/`0007_v5_expansion`/`0008_v6_expansion`):

1. Las 3 tablas nuevas (mismas columnas/tipos que las 3 clases nuevas de
   `edecan_db.models`, sección "Gimnasio"), en orden de creación
   (`workout_plans` antes que `workout_sessions` que la referencia por FK;
   `workout_sessions` antes que `gym_checkins` que la referencia por FK):
   - `workout_plans`: plan de entrenamiento de un día. `ejercicios` es JSONB
     (`'[]'` por defecto), `imagen_url` nullable (collage best-effort).
   - `workout_sessions`: máquina de estados de una sesión
     (`estado` lleva CHECK `planned|active|paused|completed|cancelled`,
     mismo vocabulario que `edecan_gym.session.ESTADOS`). `series` JSONB
     (`'[]'` por defecto); el `progreso` se deriva de `series`.
   - `gym_checkins`: check-in diario (`respuesta` lleva CHECK `si|no`,
     `session_id` nullable FK a `workout_sessions.id` con `SET NULL`).
2. Un índice por cada columna `tenant_id` — igual que las migraciones previas.
3. `GRANT` explícito a `app_user` sobre las tablas nuevas — misma red de
   seguridad idempotente que `0003`/`0004`/`0006`/`0007` documentan.
4. `ENABLE ROW LEVEL SECURITY` + política `tenant_isolation` en las 3 tablas
   nuevas (todas tenant-scoped). Sin `FORCE ROW LEVEL SECURITY`, mismo motivo
   que las migraciones previas.

Igual que las migraciones previas, este archivo NO importa `edecan_db.models`
(helpers locales duplicados a propósito) para quedar como una foto fija e
independiente de cómo evolucionen los modelos del ORM. Declara su propia
tupla `RLS_TABLES` para el cross-check de `test_migration_rls_tables.py`.

Revision ID: 0034_gym_tables
Revises: 0033_social_drafts_video_file_id
Create Date: 2026-08-16 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0034_gym_tables"
down_revision: str | None = "0033_social_drafts_video_file_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APP_ROLE = "app_user"

# Las 3 tablas del feature gimnasio, en orden de creación. Todas tenant-scoped
# con RLS — ver docstring del módulo.
RLS_TABLES: tuple[str, ...] = (
    "workout_plans",
    "workout_sessions",
    "gym_checkins",
)
ALL_TABLES_IN_ORDER: tuple[str, ...] = RLS_TABLES

_ESTADOS_SESION = ("planned", "active", "paused", "completed", "cancelled")


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


def _tenant_id_column() -> sa.Column:
    return sa.Column(
        "tenant_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def _user_id_column() -> sa.Column:
    return sa.Column(
        "user_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def _estado_check_sql(estados: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in estados)
    return f"estado IN ({quoted})"


def upgrade() -> None:
    op.create_table(
        "workout_plans",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("titulo", sa.String(), nullable=False),
        sa.Column("objetivo", sa.String(), nullable=False),
        sa.Column("duracion_min", sa.Integer(), nullable=False),
        sa.Column(
            "ejercicios", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("imagen_url", sa.String(), nullable=True),
        *_timestamp_columns(),
    )
    op.create_index("ix_workout_plans_tenant_id", "workout_plans", ["tenant_id"])

    op.create_table(
        "workout_sessions",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workout_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("estado", sa.String(), nullable=False, server_default="planned"),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ended_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "series", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        *_timestamp_columns(),
        sa.CheckConstraint(_estado_check_sql(_ESTADOS_SESION), name="estado"),
    )
    op.create_index("ix_workout_sessions_tenant_id", "workout_sessions", ["tenant_id"])

    op.create_table(
        "gym_checkins",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("respuesta", sa.String(), nullable=False),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workout_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_timestamp_columns(),
        sa.CheckConstraint("respuesta IN ('si', 'no')", name="respuesta"),
    )
    op.create_index("ix_gym_checkins_tenant_id", "gym_checkins", ["tenant_id"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")

    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    # Sin REVOKE: mismo motivo que `0003`/`0004`/`0006`/`0007` `.downgrade()` —
    # `DROP TABLE` ya retira los grants de estas tablas por sí solo.
    for table in reversed(ALL_TABLES_IN_ORDER):
        op.drop_table(table)