"""0048_agent_identity_rich_profile

Eleva `persistent_agents` a entidad de primer nivel para equipos de agentes:
añade el perfil rico (display name, avatar, rol, job spec, personalidad, estilo de
comunicación, instrucciones/constraints permanentes, approval policy, autonomía y
política de modelo) sin duplicar tabla. La ejecución sigue reutilizando el motor de
misiones existente.

Todas las columnas nuevas son nullable o con default para no romper workers ya creados.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048_agent_identity_rich_profile"
down_revision: str | None = "0046_provider_health_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_COLUMNS: tuple[tuple[str, sa.Column], ...] = (
    (
        "display_name",
        sa.Column("display_name", sa.String(), nullable=True),
    ),
    (
        "avatar",
        sa.Column(
            "avatar", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    ),
    ("role_title", sa.Column("role_title", sa.String(), nullable=True)),
    ("role_short", sa.Column("role_short", sa.String(), nullable=True)),
    ("job_description", sa.Column("job_description", sa.Text(), nullable=True)),
    ("personality", sa.Column("personality", sa.Text(), nullable=True)),
    ("communication_style", sa.Column("communication_style", sa.Text(), nullable=True)),
    ("instructions", sa.Column("instructions", sa.Text(), nullable=True)),
    ("constraints", sa.Column("constraints", sa.Text(), nullable=True)),
    (
        "approval_policy",
        sa.Column(
            "approval_policy",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    ),
    (
        "autonomy_level",
        sa.Column("autonomy_level", sa.String(), nullable=False, server_default="ask"),
    ),
    (
        "model_policy",
        sa.Column(
            "model_policy",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    ),
)


def upgrade() -> None:
    for _name, column in _NEW_COLUMNS:
        op.add_column("persistent_agents", column)


def downgrade() -> None:
    for name, _column in reversed(_NEW_COLUMNS):
        op.drop_column("persistent_agents", name)
