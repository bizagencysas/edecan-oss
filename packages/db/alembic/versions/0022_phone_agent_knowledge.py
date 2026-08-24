"""0022_phone_agent_knowledge

Separa la personalidad del agente de la información que la persona autoriza
usar frente a terceros y de los datos que el agente debe obtener. Así una
llamada puede aprovechar contexto útil sin exponer toda la memoria privada del
propietario.

Revision ID: 0022_phone_agent_knowledge
Revises: 0021_conversation_titles
Create Date: 2026-07-23 10:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_phone_agent_knowledge"
down_revision: str | None = "0021_conversation_titles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "phone_agent_templates",
        sa.Column("knowledge_context", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "phone_agent_templates",
        sa.Column("required_information", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("phone_agent_templates", "required_information")
    op.drop_column("phone_agent_templates", "knowledge_context")
