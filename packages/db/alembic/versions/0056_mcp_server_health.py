"""0056_mcp_server_health

Salud por-servidor MCP (directiva §27): `last_latency_ms`, `last_error` y
`health` ('operational'|'degraded'|'auth_required'|'unavailable') para cada
servidor `mcp` del tenant, actualizada en handshake/tool call y devuelta por
`GET /v1/mcp/servers`.

La fila se keyea por `(tenant_id, server_name)` (el `external_account_id` /
slug del servidor), sin FK a `connector_accounts` a propósito: `PUT
/v1/mcp/servers` emula upsert borrando y recreando la cuenta, así que una FK
con `ON DELETE CASCADE` resetearía la salud en cada reconfiguración. Es una
cache derivada (igual criterio que `meetings.source_file_id` en §15: referencia
informativa, no forzada). La salud de un servidor borrado se limpia desde el
router (`DELETE /v1/mcp/servers/{nombre}`).

Sigue el patrón RLS de las migraciones recientes: `GRANT` a `app_user` +
`ENABLE ROW LEVEL SECURITY` + policy `tenant_isolation`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0056_mcp_server_health"
down_revision: str | None = "0055_teams_workspaces_reactions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = ("mcp_server_health",)


def upgrade() -> None:
    op.create_table(
        "mcp_server_health",
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
        sa.Column("server_name", sa.String(), nullable=False),
        sa.Column(
            "health",
            sa.String(),
            nullable=False,
            server_default="unavailable",
        ),
        sa.Column("last_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_checked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "health IN ('operational', 'degraded', 'auth_required', 'unavailable')",
            name="health",
        ),
        sa.UniqueConstraint("tenant_id", "server_name", name="uq_mcp_server_health_tenant_server"),
    )
    op.create_index(
        "ix_mcp_server_health_tenant_server", "mcp_server_health", ["tenant_id", "server_name"]
    )

    for table in RLS_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.drop_table(table)