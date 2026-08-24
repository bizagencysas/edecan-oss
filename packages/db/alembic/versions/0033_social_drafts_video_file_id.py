"""0033_social_drafts_video_file_id

Agrega `social_drafts.video_file_id` (UUID, nullable, FK a `files.id` con
`ondelete="SET NULL"`): el MP4 que el worker de `create_organization_linkedin_post`
sube con `subir_archivo` y que la tarjeta "Aprobar y publicar" reproduce en el
chat de iOS con `AVPlayer` (nodo `VideoNode` de la card).

NO se publica en LinkedIn en este alcance: la publicación sigue mandando
texto+imagen con `image_file_id`, así que esta columna queda `NULL` sin afectar
el flujo de publicación existente. Mismo `ondelete="SET NULL"` que
`image_file_id` (migración `0029_social_drafts_tz`): el borrador sigue
publicable como texto si el MP4 se borra.

Revision ID: 0033_social_drafts_video_file_id
Revises: 0032_job_dc_linkedin_post
Create Date: 2026-08-14 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_social_drafts_video_file_id"
down_revision: str | None = "0032_job_dc_linkedin_post"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "social_drafts",
        sa.Column(
            "video_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("social_drafts", "video_file_id")
