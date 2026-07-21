"""0014_local_installation_owner

Identidad singleton de la instalación autohospedada. Las versiones anteriores
permitían crear varios tenants mediante login/register local; la migración no
borra ninguno y fija como dueño local al owner activo más antiguo. Desde este
momento la selección es explícita y estable, no un LIMIT 1 durante el login.

Revision ID: 0014_local_installation_owner
Revises: 0013_mobile_pairing
Create Date: 2026-07-21 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_local_installation_owner"
down_revision: str | None = "0013_mobile_pairing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "local_installation",
        sa.Column("installation_key", sa.Text(), primary_key=True, nullable=False),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "owner_tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("installation_key = 'local'", name="local_installation_singleton"),
    )
    # Upgrade conservador: no elimina ni desactiva cuentas viejas. La primera
    # identidad owner creada es la que representaba a la instalación antes de
    # que existiera este marcador explícito.
    op.execute(
        """
        INSERT INTO local_installation (
            installation_key, owner_user_id, owner_tenant_id, created_at, updated_at
        )
        SELECT 'local', m.user_id, m.tenant_id, now(), now()
        FROM memberships m
        JOIN tenants t ON t.id = m.tenant_id
        WHERE m.role = 'owner' AND t.status = 'active'
        ORDER BY m.created_at ASC, m.id ASC
        LIMIT 1
        ON CONFLICT (installation_key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("local_installation")
