"""0063_bot_relation_y_handoff_depth

Dos piezas de "Bots" del 28-ago-2026:

- `persistent_agents.relation`: la relación que el dueño elige al crear un bot
  (`profesional`/`amigo`/`coach`) y que define su `estilo_relacion` de persona.
- `persistent_agent_handoffs.depth` + `visited_worker_ids`: la cadena de
  delegación entre bots se propaga con profundidad y vista de visitados, para
  cortar ciclos y profundidad excesiva (MAX_HANDOFF_DEPTH) en el camino real.

Revision ID: 0063_bot_relation_y_handoff_depth
Revises: 0061_bot_conversations
Create Date: 2026-08-28 21:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_bot_relation_handoff"
down_revision: str | None = "0061_bot_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "persistent_agents",
        sa.Column("relation", sa.Text(), nullable=False, server_default="profesional"),
    )
    op.add_column(
        "persistent_agent_handoffs",
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "persistent_agent_handoffs",
        sa.Column(
            "visited_worker_ids",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # Idempotencia SOBRE la carrera del relay: máximo un result por
    # (tenant, tarea, receptor) — el segundo INSERT falla por constraint, no
    # por un SELECT previo (TOCTOU).
    op.create_index(
        "uq_agent_messages_relay_result",
        "agent_messages",
        ["tenant_id", "parent_task_id", "receiver_agent_id"],
        unique=True,
        postgresql_where=sa.text("message_type = 'result'"),
    )


def downgrade() -> None:
    op.drop_column("persistent_agent_handoffs", "visited_worker_ids")
    op.drop_column("persistent_agent_handoffs", "depth")
    op.drop_column("persistent_agents", "relation")
