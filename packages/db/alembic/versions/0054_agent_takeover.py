"""0054_agent_takeover

Plano de control durable de "toma de control / pausa" por agente y por
superficie (directiva §18-24, §144-145; Ola F). La tabla nueva
`computer_sessions` es tenant-scoped (RLS) y registra el estado de la
"computadora" que un agente puede manejar en cada superficie:

- `agent_id` (FK NULLABLE a `persistent_agents.id`, ON DELETE SET NULL): la
  fila es por-agente. `NULL` = una sesión a nivel de tenant que gobierna a
  TODOS los agentes de esa superficie (incluido el asistente del chat, que no
  tiene `persistent_agents`).
- `kind` CHECK (`browser`/`desktop`/`terminal`/`files`): la superficie que
  gobierna esta fila.
- `mode` CHECK (`agent`/`user`/`paused`): quién maneja la superficie AHORA.
  `agent` = el agente puede; `user` = un humano tomó el control; `paused` =
  nadie la mueve (congelada). Es el campo que la tool `usar_computadora` lee
  ANTES de reenviar cualquier acción al companion y que la bloquea si no es
  `agent` (enforcement real en el tool layer, directiva §123).
- `status` CHECK (`active`/`paused`/`ended`): ciclo de vida de la sesión.
  `pause` fija `mode='paused'` + `status='paused'`; `resume`/`return` vuelven a
  `mode='agent'` + `status='active'`; `end` la cierra (`status='ended'`, que
  deja de gobernar la superficie).
- `workspace_scope` (jsonb default '{}'): carpeta a la que se confina al
  agente para acciones de archivos/terminal, con la forma `{"root":
  "/ruta/absoluta"}`. `{}` = sin confinamiento (comportamiento actual: la
  máquina del dueño vía `config.sandbox_dir` del companion).
- `ephemeral`: computadora desechable vs persistente (contracto de UI, no
  cambia la semántica de enforcement).

Enforcement (ver `packages/toolkit/edecan_toolkit/computadora.py`): la tool
`usar_computadora` consulta las filas activas (`status <> 'ended'`) de
`(tenant_id, kind, agent_id | NULL)` y, si alguna tiene `mode` en
(`user`, `paused`), devuelve `ToolResult` "El agente está pausado en esta
superficie..." SIN invocar al companion. El workspace scope se lee de la misma
consulta y se inyecta como `params["workspace_root"]` (que el companion honra
en `actions._resolve_in_sandbox`) — la tool pisa/cancela ese campo para que el
modelo nunca pueda elegirlo.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054_agent_takeover"
down_revision: str | None = "0053_skill_teach_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = ("computer_sessions",)
ALL_TABLES_IN_ORDER = RLS_TABLES


def upgrade() -> None:
    op.create_table(
        "computer_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persistent_agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(), nullable=False, server_default="desktop"),
        sa.Column("mode", sa.String(), nullable=False, server_default="agent"),
        sa.Column("ephemeral", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column(
            "workspace_scope",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('browser', 'desktop', 'terminal', 'files')", name="kind"),
        sa.CheckConstraint("mode IN ('agent', 'user', 'paused')", name="mode"),
        sa.CheckConstraint("status IN ('active', 'paused', 'ended')", name="status"),
    )
    op.create_index("ix_computer_sessions_tenant_id", "computer_sessions", ["tenant_id"])
    op.create_index(
        "ix_computer_sessions_tenant_agent_kind",
        "computer_sessions",
        ["tenant_id", "agent_id", "kind"],
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON computer_sessions TO {APP_ROLE}")
    op.execute("ALTER TABLE computer_sessions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON computer_sessions "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON computer_sessions")
    op.drop_table("computer_sessions")