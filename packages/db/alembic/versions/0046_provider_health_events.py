"""0046_provider_health_events

Historia operacional global de salud de proveedores. No contiene tenant,
prompts, argumentos, respuestas ni excepciones; el acceso queda reservado a
la ruta superadmin de diagnóstico.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_provider_health_events"
down_revision: str | None = "0045_worker_lease_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_health_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("model_alias", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Numeric(12, 3), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('success', 'failure', 'rate_limited')",
            name="ck_provider_health_events_status",
        ),
        sa.CheckConstraint("latency_ms >= 0", name="ck_provider_health_events_latency"),
    )
    op.create_index(
        "ix_provider_health_events_observed_at",
        "provider_health_events",
        ["observed_at"],
    )
    op.create_index(
        "ix_provider_health_events_provider_observed",
        "provider_health_events",
        ["provider", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_health_events_provider_observed",
        table_name="provider_health_events",
    )
    op.drop_index(
        "ix_provider_health_events_observed_at",
        table_name="provider_health_events",
    )
    op.drop_table("provider_health_events")
