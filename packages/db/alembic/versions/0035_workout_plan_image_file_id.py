"""0035_workout_plan_image_file_id

Añade `imagen_file_id` (nullable `String`) a `workout_plans`: el `file_id` del
collage para descargarlo con el Bearer del tenant vía
`GET /v1/files/{id}/download` — el mismo camino autenticado que usa el resto
de la app para artefactos, en vez de una URL pública firmada
(`imagen_url`, que depende de `PUBLIC_BASE_URL` y no funciona a través del
edge `e.organization.org`). La columna `imagen_url` se conserva por
compatibilidad (queda `NULL`).

Revision ID: 0035_workout_plan_image_file_id
Revises: 0034_gym_tables
Create Date: 2026-08-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035_workout_plan_image_file_id"
down_revision: str | None = "0034_gym_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workout_plans", sa.Column("imagen_file_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("workout_plans", "imagen_file_id")
