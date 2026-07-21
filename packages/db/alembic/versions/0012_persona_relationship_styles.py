"""0012_persona_relationship_styles

Añade estilos de acompañamiento configurables a ``personas``. El estilo
romántico exige dos decisiones persistidas (mayoría de edad y consentimiento)
y las confirmaciones se limpian al salir. No se guarda fecha de nacimiento ni
otro dato sensible innecesario.

Revision ID: 0012_persona_relationship_styles
Revises: 0011_phone_calls
Create Date: 2026-07-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_persona_relationship_styles"
down_revision: str | None = "0011_phone_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "personas",
        sa.Column("estilo_relacion", sa.String(), server_default="profesional", nullable=False),
    )
    op.add_column(
        "personas",
        sa.Column("adulto_confirmado", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "personas",
        sa.Column(
            "consentimiento_romantico", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.create_check_constraint(
        "persona_estilo_relacion_valid",
        "personas",
        "estilo_relacion IN ('profesional', 'coach', 'amigo', 'romantico')",
    )
    op.create_check_constraint(
        "persona_romantico_requires_consent",
        "personas",
        "(estilo_relacion = 'romantico' AND adulto_confirmado AND consentimiento_romantico) "
        "OR (estilo_relacion <> 'romantico' AND NOT adulto_confirmado "
        "AND NOT consentimiento_romantico)",
    )


def downgrade() -> None:
    op.drop_constraint("persona_romantico_requires_consent", "personas", type_="check")
    op.drop_constraint("persona_estilo_relacion_valid", "personas", type_="check")
    op.drop_column("personas", "consentimiento_romantico")
    op.drop_column("personas", "adulto_confirmado")
    op.drop_column("personas", "estilo_relacion")
