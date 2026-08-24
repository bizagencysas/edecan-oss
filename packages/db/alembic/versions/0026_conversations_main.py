"""0026_conversations_main

Frente 5 del plan de paridad con REFERENCIA: una conversación "principal" por
tenant+usuario, donde aterrizan los eventos automáticos que el dueño no
pidió (llamada entrante, automatización ejecutada, recordatorio disparado) --
el equivalente al hilo de avisos de REFERENCIA. El índice único parcial garantiza
como máximo UNA fila `is_main = true` por tenant+usuario -- mismo criterio
que `uq_personas_tenant_id_default` (`0001_initial`, "como máximo una persona
default por tenant"): Postgres no choca entre las demás filas `is_main =
false` porque el índice solo cubre las filas donde `is_main` es verdadero.

`edecan_api.repo.SqlRepo.resolve_main_conversation` es el get-or-create
atómico que usa este índice como conflict target (`ON CONFLICT (tenant_id,
user_id) WHERE is_main DO NOTHING`) -- ver su docstring para la receta
completa.

Revision ID: 0026_conversations_main
Revises: 0025_social_editorial
Create Date: 2026-07-28 22:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_conversations_main"
down_revision: str | None = "0025_social_editorial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("is_main", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "uq_conversations_tenant_user_main",
        "conversations",
        ["tenant_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("is_main"),
    )


def downgrade() -> None:
    op.drop_index("uq_conversations_tenant_user_main", table_name="conversations")
    op.drop_column("conversations", "is_main")
