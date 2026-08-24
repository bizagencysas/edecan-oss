"""0041_mission_archival

Permite archivar una misión terminada sin eliminar su resultado, pasos ni
provenance. Las filas existentes quedan visibles por defecto hasta que el
usuario las archive explícitamente.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_mission_archival"
down_revision: str | None = "0040_unified_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_missions", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_agent_missions_tenant_user_archived_at",
        "agent_missions",
        ["tenant_id", "user_id", "archived_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_missions_tenant_user_archived_at", table_name="agent_missions")
    op.drop_column("agent_missions", "archived_at")
