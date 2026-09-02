"""0050_automation_lease

Añade a `automations` las columnas de arriendo durable (`lease_owner`,
`lease_until`) para que el barrido de agenda (`automation_scan`) reclame una
automatización antes de encolarla, con el mismo espíritu que
`run_persistent_agent.py` ya hace sobre `persistent_agents` (ver su patrón de
claim en `UPDATE ... WHERE (status='idle' OR updated_at < ...)`):

- `lease_owner` (uuid, NULLABLE): quién reclamó la fila — un `scan_id`
  (uuid4) por invocación del barrido.
- `lease_until` (timestamptz, NULLABLE): hasta cuándo vale el arriendo. El
  claim es un `UPDATE ... WHERE (lease_until IS NULL OR lease_until < now())`,
  así que si el worker muere a mitad de una corrida, el arriendo vence solo y
  el siguiente barrido puede reclamar sin intervención manual. `run_automation`
  limpia ambas columnas al persistir el estado terminal (`save_run`).

Ambas NULLABLE y sin default a propósito: NULL = "sin arriendo activo" = el
estado de toda fila ya existente, así que estrenar las columnas no cambia el
comportamiento de ninguna automatización guardada (backwards-compatible,
mismo criterio que `condition`/`next_run_at`/`last_run_at`/`disabled_at`).

No crea tablas nuevas: `automations` ya tiene su política RLS y su GRANT desde
`0003_v2_expansion` (RLS es por fila, no por columna), así que esta migración
no necesita tupla `RLS_TABLES` propia.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050_automation_lease"
down_revision: str | None = "0049_pending_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "automations",
        sa.Column("lease_owner", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "automations",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("automations", "lease_until")
    op.drop_column("automations", "lease_owner")