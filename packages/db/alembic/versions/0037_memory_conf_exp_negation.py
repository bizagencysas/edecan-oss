"""0037_memory_conf_exp_negation

Tres mejoras de memoria (PHASE2.md §89/§92/§94):

- `confidence float NOT NULL DEFAULT 0.8`: "qué tan seguros estamos de que esto
  es cierto" (0.0-1.0), distinto de `importance` ("qué tan útil es recordarlo").
- `expires_at timestamptz NULL`: caducidad para hechos temporales (p. ej.
  eventos). `PgMemoryStore.search` filtra `expires_at IS NULL OR expires_at >
  now()`, así que una memoria caducada deja de participar del contexto activo
  sin borrarse (igual que `superseded_at`).
- `kind` gana `'negation'` en su CHECK: conocimiento negativo de primera clase
  ("el usuario NO quiere X"). Postgres no soporta modificar la expresión de un
  CHECK existente in place, así que se hace drop+create (mismo patrón que
  `0005_jobs_type_check_v2_types`/`0007_v5_expansion`).

Revision ID: 0037_memory_conf_exp_negation
Revises: 0036_automation_failure_tracking
Create Date: 2026-08-20 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_memory_conf_exp_negation"
down_revision: str | None = "0036_automation_failure_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memory_items",
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.8")),
    )
    op.add_column(
        "memory_items",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    # op.drop_constraint()/op.create_check_constraint() con el nombre ya-expandido
    # duplican el prefijo de naming_convention en este entorno (produce
    # "ck_memory_items_ck_memory_items_kind", que no existe -> UndefinedObjectError).
    # Confirmado contra la DB real: el nombre vigente es literal "ck_memory_items_kind"
    # (creado por edecan_db.models.Base.metadata, que sí aplica naming_convention UNA
    # vez). SQL crudo evita que Alembic reprocese el nombre.
    op.execute("ALTER TABLE memory_items DROP CONSTRAINT ck_memory_items_kind")
    op.execute(
        "ALTER TABLE memory_items ADD CONSTRAINT ck_memory_items_kind "
        "CHECK (kind IN ('fact', 'preference', 'event', 'entity', 'negation'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE memory_items DROP CONSTRAINT ck_memory_items_kind")
    op.execute(
        "ALTER TABLE memory_items ADD CONSTRAINT ck_memory_items_kind "
        "CHECK (kind IN ('fact', 'preference', 'event', 'entity'))"
    )
    op.drop_column("memory_items", "expires_at")
    op.drop_column("memory_items", "confidence")
