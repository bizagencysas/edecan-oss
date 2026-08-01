"""Guarda el RESULTADO DE LA VERIFICACIÓN de una publicación social.

Por qué hace falta (incidente del 31-jul-2026, ver `app.md`): publicar en una red
no termina cuando la API responde `2xx`. LinkedIn puede devolver `201` con un id
válido y no crear nada — pasó, con un token sin permiso de organización. Por eso
el conector RELEE el post y clasifica el resultado en `confirmed` / `not_found` /
`unknown`.

El caso `not_found` nunca llega a esta tabla (se convierte en error). Pero
`unknown` sí: el post se envió y no se pudo comprobar. Sin esta columna ese matiz
se perdía apenas terminaba el request, y el camino idempotente —el SEGUNDO toque
sobre un borrador ya marcado `publicado`— caía en el default del esquema y
respondía `confirmed`. Es decir: la app afirmaba, en el segundo toque, algo que
nadie había comprobado nunca. Justo el tipo de mentira que toda esta cadena de
arreglos existe para eliminar.

`verification` guarda lo que de verdad pasó; el default `'unknown'` es
deliberadamente el conservador: una fila publicada ANTES de esta migración no
tiene forma de saber si se verificó, y suponer que sí sería repetir el defecto.

Revision ID: 0030_social_drafts_verification
Revises: 0029_social_drafts_tz
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_social_drafts_verification"
down_revision = "0029_social_drafts_tz"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "social_drafts",
        sa.Column(
            "verification",
            sa.Text(),
            nullable=False,
            server_default="unknown",
        ),
    )
    # CHECK y no enum nativo: mismo criterio que `status` en 0029 -- es una máquina
    # de estados corta y estable, y un enum de Postgres encarece agregarle un caso.
    op.create_check_constraint(
        "ck_social_drafts_verification",
        "social_drafts",
        "verification IN ('confirmed', 'unknown')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_social_drafts_verification", "social_drafts", type_="check")
    op.drop_column("social_drafts", "verification")
