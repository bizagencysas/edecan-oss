"""0031_conv_context_cleared

Soporte para el comando local `/clear` del chat (frente: reinicio de contexto
sin borrar historial).

Ojo con el nombre: la REVISIÓN es `0031_conv_context_cleared` (≤32 caracteres,
ver el comentario junto a `revision`), mientras que el ARCHIVO conserva su
nombre largo `0031_conversation_context_cleared.py`. No son lo mismo, y el que
manda en `alembic_version` es el corto.

`context_cleared_at` marca el LÍMITE desde el que el turno siguiente vuelve a
armar el contexto que ve el modelo: `POST /{id}/clear` lo mueve a `now()` y
`GET /{id}` + `POST /{id}/messages` filtran `messages.created_at > context_cleared_at`
(ver `Repo.list_messages(..., after=...)`, `edecan_api.chat_context`). NADA se
borra -- los mensajes anteriores siguen íntegros en `messages`, solo dejan de
mandarse al LLM y de listarse por defecto. `NULL` (el valor de toda fila
existente) es "nunca se limpió": estrenar la columna no cambia ninguna
conversación ya en curso.

Revision ID: 0031_conv_context_cleared
Revises: 0030_social_drafts_verification
Create Date: 2026-08-02 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# MÁS CORTO que el nombre del archivo, a propósito: `alembic_version.version_num` es
# `varchar(32)` y "0031_conversation_context_cleared" mide 33. Con el id largo, la
# migración revienta AL ESTAMPARSE (`StringDataRightTruncationError`) y el backend
# entero muere en el arranque -- pasó el 02-ago-2026: la app instalada quedó sin motor.
# El DDL sí se revierte (transactional DDL), pero el boot no pasa de aquí. La regla
# queda fijada en `tests/test_revision_ids_caben_en_alembic.py`: ids de ≤32 SIEMPRE.
revision: str = "0031_conv_context_cleared"
down_revision: str | None = "0030_social_drafts_verification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("context_cleared_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "context_cleared_at")
