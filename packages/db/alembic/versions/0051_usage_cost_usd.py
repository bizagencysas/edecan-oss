"""0051_usage_cost_usd

Añade `usage_events.cost_usd` (NUMERIC(18, 12), NULLABLE) — el costo en USD
reconstruido al MOMENTO de escribir el evento de uso (no después: los precios
cambian y reconstruir costo a futuro exigiría saber qué tarifa regía ese día).

- NULLABLE a propósito (mismo criterio que el campo `meta->>'estimated_cost_usd'`
  que ya escribe `llm_attribution.build_llm_usage_meta`): NULL = "no se pudo
  estimar un precio para este modelo" = honesto, en vez de fingir un `0.0` que
  confundiría "gratis" con "desconocido". Quien suma costo (`GET /v1/usage`)
  usa `COALESCE(SUM(cost_usd), 0)`.
- Los valores se calculan con la tabla de referencia de `edecan_llm.costs`
  (placeholders conservadores, marcados como tales en ese módulo) y se escriben
  desde `edecan_api.routers.conversations`/`voice_turn_service` al cerrar un
  turno, reutilizando `build_llm_usage_meta`.

No crea tablas nuevas: `usage_events` ya tiene su política RLS y su GRANT
desde `0001_initial`, así que esta migración no necesita tupla `RLS_TABLES`
propia.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_usage_cost_usd"
down_revision: str | None = "0050_automation_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_events",
        sa.Column("cost_usd", sa.Numeric(18, 12), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("usage_events", "cost_usd")