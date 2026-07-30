"""0027_conversation_chat_model

Selector de modelos del chat: la selección es propiedad de la CONVERSACIÓN
(no de la credencial del tenant -- ver el 409 deliberado de `credentials.py`,
`PATCH /v1/credentials/llm/models`; reabrir ese endpoint reviviría el bug de
tres autoridades del modelo).

NULL = automático: la cadena `WORKERS_AI_CHAT_MODEL` -> `MODELO_POR_DEFECTO`
sigue decidiendo, así que estrenar las columnas no cambia ninguna conversación
existente. `chat_model` no tiene CHECK a propósito: el catálogo es DATO
(`config/modelos.yml` -> `modelos_chat`) y cambia sin migración; la validación
vive en la API contra `modelo_chat_permitido`. `chat_effort` sí lo tiene,
mismo estilo que `title_source` en `0021`, porque su vocabulario es del
dominio (presupuesto de razonamiento) y no del catálogo de modelos.

Revision ID: 0027_conversation_chat_model
Revises: 0026_conversations_main
Create Date: 2026-07-29 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_conversation_chat_model"
down_revision: str | None = "0026_conversations_main"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("chat_model", sa.String(), nullable=True))
    op.add_column("conversations", sa.Column("chat_effort", sa.String(), nullable=True))
    # El CHECK acepta las filas existentes sin backfill: `NULL IN (...)` es
    # NULL en SQL, y un CHECK solo falla cuando evalúa a FALSE.
    op.create_check_constraint(
        "ck_conversations_chat_effort",
        "conversations",
        "chat_effort IN ('bajo', 'medio', 'alto')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_conversations_chat_effort", "conversations", type_="check")
    op.drop_column("conversations", "chat_effort")
    op.drop_column("conversations", "chat_model")
