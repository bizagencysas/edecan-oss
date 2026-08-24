"""0039_automation_condition

Añade a `automations` la columna `condition` (`jsonb NULL`) de las
automatizaciones condicionales (PHASE2.md §60, §62): un filtro opcional que
decide si una corrida debida se ejecuta o se salta ("si ocurre X y además Y
pero no Z").

Forma del JSON (pinned en `edecan_schemas.automations.Condition`): UNA cláusula
`{"field": "...", "op": "...", "value": ...}` o una LISTA de cláusulas
combinadas con AND. `NULL` = sin condición = la automatización corre siempre,
así que estrenar la columna no cambia el comportamiento de NI UNA fila ya
guardada (backwards-compatible) — por eso es `nullable=True` SIN default, igual
que `next_run_at`/`last_run_at`/`disabled_at` (columnas "puede no haber
ninguna" y no "nace con un valor").

No crea tablas nuevas, así que NO necesita `RLS_TABLES` propia ni `GRANT`
nuevo: la política `tenant_isolation` de `0003_v2_expansion` (que creó
`automations`) ya cubre esta columna por construcción (RLS es por fila, no por
columna), y `app_user` ya tiene GRANT sobre la tabla desde esa migración.

Revision ID: 0039_automation_condition
Revises: 0038_action_effects
Create Date: 2026-08-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0039_automation_condition"
down_revision: str | None = "0038_action_effects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "automations",
        sa.Column("condition", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("automations", "condition")