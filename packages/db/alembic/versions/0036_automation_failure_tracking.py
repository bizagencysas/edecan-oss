"""0036_automation_failure_tracking

Añade a `automations` el seguimiento de fallos consecutivos y la marca
temporal de auto-desactivación (PHASE2 §61-62):

- `consecutive_failures` (`int NOT NULL DEFAULT 0`): contador de corridas
  terminadas en `error` de forma seguida. Una corrida exitosa lo reinicia a 0.
  El `server_default=0` hace el backfill de las filas existentes en el mismo
  `ALTER`, así que la columna nace `NOT NULL` sin un UPDATE aparte ni ventana
  de esquema inconsistente (mismo criterio que `automations.timezone` en
  `0029`).
- `disabled_at` (`timestamptz NULL`): momento en que el auto-disable apagó la
  automatización tras alcanzar `consecutive_failures >= 3`. `NULL` = nunca
  fue auto-desactivada (o fue reactivada a mano).

El `app_user` ya tiene `GRANT` sobre `automations` desde `0003_v2_expansion`
(que la creó), y esta migración solo añade columnas a una tabla existente,
así que no hace falta un `GRANT` nuevo ni nueva política RLS: la política
`tenant_isolation` que `0003` creó cubre las columnas nuevas por construcción
(RLS es por fila, no por columna).

Revision ID: 0036_automation_failure_tracking
Revises: 0035_workout_plan_image_file_id
Create Date: 2026-08-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0036_automation_failure_tracking"
down_revision: str | None = "0035_workout_plan_image_file_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "automations",
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "automations",
        sa.Column("disabled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("automations", "disabled_at")
    op.drop_column("automations", "consecutive_failures")
