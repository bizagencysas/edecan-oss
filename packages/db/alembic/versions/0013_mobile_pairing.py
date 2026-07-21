"""0013_mobile_pairing

Identidad durable y revocable para apps móviles emparejadas por QR. Solo se
persiste SHA-256 del secreto aleatorio; el valor que autentica queda en el
Keychain/Keystore del teléfono.

Revision ID: 0013_mobile_pairing
Revises: 0012_persona_relationship_styles
Create Date: 2026-07-21 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_mobile_pairing"
down_revision: str | None = "0012_persona_relationship_styles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("pairing_secret_hash", sa.String(64), nullable=True))
    op.add_column(
        "devices",
        sa.Column("paired_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_devices_pairing_secret_hash",
        "devices",
        ["pairing_secret_hash"],
        unique=True,
        postgresql_where=sa.text("pairing_secret_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_devices_pairing_secret_hash", table_name="devices")
    op.drop_column("devices", "paired_at")
    op.drop_column("devices", "pairing_secret_hash")
