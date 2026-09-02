"""0053_skill_teach_sessions

Respaldo durable de "enseñar una tarea" (directiva §38-41): una sesión de
captura de pasos que el usuario va llenando (`POST /v1/skills/teach`,
`/teach/{id}/step`) y que al finalizar se compila en una skill DRAFT (no
activa). La tabla nueva `skill_teach_sessions` es tenant-scoped (RLS) y guarda
el mínimo necesario:

- `nombre`/`descripcion`: qué se está enseñando.
- `pasos` (jsonb default '[]'): lista de pasos capturados, cada uno con la
  forma `{action, selector, decision, input, output}`.
- `status` CHECK (`open`/`finished`/`discarded`): la sesión queda `open` hasta
  que `finish` la compila en la skill draft y la marca `finished`.
- `draft_skill_id` (FK NULLABLE a `skills.id`, ON DELETE SET NULL): apunta a
  la skill draft generada al finalizar. NULL hasta ese momento.

En la misma migración, `skills` gana `status` (text NOT NULL DEFAULT 'active',
CHECK `draft|active`): las skills instaladas del marketplace nacen 'active';
la skill generada por `finish` nace `status='draft'` y `enabled=false` (NUNCA
auto-activa), y `POST /v1/skills/{id}/approve` la promueve a 'active' +
`enabled=true`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0053_skill_teach_sessions"
down_revision: str | None = "0052_memory_namespaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = ("skill_teach_sessions",)
ALL_TABLES_IN_ORDER = RLS_TABLES


def upgrade() -> None:
    op.create_table(
        "skill_teach_sessions",
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
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column(
            "pasos",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "draft_skill_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skills.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("status IN ('open', 'finished', 'discarded')", name="status"),
    )
    op.create_index("ix_skill_teach_sessions_tenant_id", "skill_teach_sessions", ["tenant_id"])
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON skill_teach_sessions TO {APP_ROLE}")
    op.execute("ALTER TABLE skill_teach_sessions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON skill_teach_sessions "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )

    op.add_column(
        "skills",
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
    )
    op.create_check_constraint("status", "skills", "status IN ('draft', 'active')")


def downgrade() -> None:
    op.drop_constraint("status", "skills", type_="check")
    op.drop_column("skills", "status")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON skill_teach_sessions")
    op.drop_table("skill_teach_sessions")