"""0045_persistent_agent_lease_index

Índice compuesto para que el scheduler y la recuperación de leases no tengan
que ordenar o escanear todos los workers de un tenant.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0045_worker_lease_index"
down_revision: str | None = "0044_persistent_agent_handoffs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_persistent_agents_tenant_status_updated",
        "persistent_agents",
        ["tenant_id", "status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_persistent_agents_tenant_status_updated",
        table_name="persistent_agents",
    )
