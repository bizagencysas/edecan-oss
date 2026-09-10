"""remote_sessions.machine — máquina destino del control remoto (box/VPS o Mac).

Revision ID: 0065_remote_sessions_machine
Revises: 0064_team_missions
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0065_remote_sessions_machine"
down_revision = "0064_team_missions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("remote_sessions", sa.Column("machine", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("remote_sessions", "machine")
