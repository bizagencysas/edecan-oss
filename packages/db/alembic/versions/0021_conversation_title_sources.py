"""0021_conversation_title_sources

Distingue los títulos elegidos por la persona de los títulos automáticos. Las
conversaciones heredadas se revisan una sola vez desde la API: solo se resumen
si el título antiguo es claramente una copia larga del primer mensaje.

Revision ID: 0021_conversation_titles
Revises: 0020_redact_chat_secrets
Create Date: 2026-07-22 16:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_conversation_titles"
down_revision: str | None = "0020_redact_chat_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("title_source", sa.String(), nullable=False, server_default="legacy"),
    )
    op.create_check_constraint(
        "ck_conversations_title_source",
        "conversations",
        "title_source IN ('auto_pending', 'auto', 'manual', 'legacy')",
    )
    op.execute(
        "UPDATE conversations SET title_source = 'auto_pending' WHERE BTRIM(title) = ''"
    )


def downgrade() -> None:
    op.drop_constraint("ck_conversations_title_source", "conversations", type_="check")
    op.drop_column("conversations", "title_source")
