"""0023_phone_agent_voices

Asigna una voz TTS opcional a cada agente y la congela en cada llamada. Las
llamadas históricas conservan la voz revisada aunque la plantilla cambie.

Revision ID: 0023_phone_agent_voices
Revises: 0022_phone_agent_knowledge
Create Date: 2026-07-23 11:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_phone_agent_voices"
down_revision: str | None = "0022_phone_agent_knowledge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "phone_agent_templates",
        sa.Column("voice_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "phone_calls",
        sa.Column("voice_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("phone_calls", "voice_id")
    op.drop_column("phone_agent_templates", "voice_id")
