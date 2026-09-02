"""0052_memory_namespaces

Añade a `memory_items` dos columnas para namespaces de memoria y confianza de
procedencia (directiva §50-54):

- `namespace` (text NOT NULL DEFAULT 'user'): a qué "espacio" pertenece el
  recuerdo. 'user' es el espacio plano histórico (el default de siempre, así
  que nada existente cambia); 'agent:<id>'/'workspace:<id>'/'conversation'/
  'organization' son espacios futuros/alternos. La búsqueda del agente sigue
  por defecto en 'user'.
- `source_trust` (text NOT NULL DEFAULT 'trusted'): qué tan confiable es la
  procedencia, con CHECK `trusted|untrusted|quarantined` (fail-safe: lo que ya
  estaba queda 'trusted').

Ambas NOT NULL con `server_default` a propósito: en Postgres moderno
`ADD COLUMN ... NOT NULL DEFAULT` rellena las filas existentes con el default
sin reescritura, así que toda la memoria ya guardada queda etiquetada
'user'/'trusted' y las búsquedas que filtren `namespace='user'` no pierden
nada. Sin default las filas viejas quedarían NULL y cualquier filtro
`namespace = 'user'` las ignoraría en silencio.

No crea tablas nuevas: `memory_items` ya tiene RLS y GRANT desde `0001_initial`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_memory_namespaces"
down_revision: str | None = "0051_usage_cost_usd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memory_items",
        sa.Column("namespace", sa.String(), nullable=False, server_default="user"),
    )
    op.add_column(
        "memory_items",
        sa.Column("source_trust", sa.String(), nullable=False, server_default="trusted"),
    )
    op.create_check_constraint(
        "source_trust",
        "memory_items",
        "source_trust IN ('trusted', 'untrusted', 'quarantined')",
    )


def downgrade() -> None:
    op.drop_constraint("source_trust", "memory_items", type_="check")
    op.drop_column("memory_items", "source_trust")
    op.drop_column("memory_items", "namespace")