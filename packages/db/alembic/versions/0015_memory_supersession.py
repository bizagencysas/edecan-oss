"""0015_memory_supersession

Conserva el historial de una memoria cuando el usuario corrige un dato. Una
memoria reemplazada deja de participar en búsqueda, perfil y consolidación,
pero sigue disponible en PostgreSQL para auditoría y recuperación.

Revision ID: 0015_memory_supersession
Revises: 0014_local_installation_owner
Create Date: 2026-07-21 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_memory_supersession"
down_revision: str | None = "0014_local_installation_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memory_items",
        sa.Column("superseded_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "memory_items",
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_memory_items_superseded_by",
        "memory_items",
        "memory_items",
        ["superseded_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_memory_items_active_user",
        "memory_items",
        ["tenant_id", "user_id"],
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_memory_items_active_user", table_name="memory_items")
    op.drop_constraint("fk_memory_items_superseded_by", "memory_items", type_="foreignkey")
    op.drop_column("memory_items", "superseded_by")
    op.drop_column("memory_items", "superseded_at")
