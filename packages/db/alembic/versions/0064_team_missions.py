"""0064_team_missions

"Encargo a equipo": un bot coordinador reparte sub-encargos entre bots de un
mismo equipo (`teams`). Cada sub-encargo es un handoff aprobado por el dueño;
cuando TODOS terminan (o fallan), el coordinador recibe un turno de MERGE con
los resultados y entrega UNA cosa final al dueño.

- `team_missions`: el encargo en curso (pedido, estados, esperados).
- `team_mission_results`: un resultado por miembro; única fila por (encargo,
  agente) — guardia de idempotencia.

RLS (tenant_isolation) igual que el resto de tablas multi-tenant:
`SET LOCAL ROLE app_user` no puede leer nada sin esto.

Revision ID: 0064_team_missions
Revises: 0063_bot_relation_handoff
Create Date: 2026-08-28 22:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064_team_missions"
down_revision: str | None = "0063_bot_relation_handoff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = ("team_missions", "team_mission_results")


def upgrade() -> None:
    op.create_table(
        "team_missions",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "coordinator_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persistent_agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pedido", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="waiting_approval"),
        sa.Column(
            "esperados",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("nota", sa.Text(), nullable=True),
        sa.Column(
            "subencargos",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_team_missions_tenant_status",
        "team_missions",
        ["tenant_id", "status"],
    )

    op.create_table(
        "team_mission_results",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "team_mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_missions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persistent_agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "handoff_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persistent_agent_handoffs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("estado", sa.String(), nullable=False, server_default="pending"),
        sa.Column("resumen", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "team_mission_id",
            "agent_id",
            name="uq_team_mission_results_mission_agent",
        ),
    )
    op.create_index(
        "ix_team_mission_results_handoff",
        "team_mission_results",
        ["tenant_id", "handoff_id"],
    )

    for table in RLS_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    op.drop_table("team_mission_results")
    op.drop_table("team_missions")
