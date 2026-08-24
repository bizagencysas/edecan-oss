"""0038_action_effects

Crea la tabla `action_effects` del Action Ledger (PHASE2.md §63-71): el
registro de acciones reversibles del agente para responder "¿qué cambiaste?"
(§69) y ofrecer "deshacer" (§64).

Columnas (mismas que la clase `ActionEffect` de `edecan_db.models`, sección
"Action Ledger"): `id` UUID PK, `tenant_id`/`user_id` FK con `CASCADE`,
`tool_name` text NOT NULL, `target` text nullable, `inverse_op` JSONB NOT NULL
(`'{}'` por defecto), `reversible` bool NOT NULL (`false` por defecto),
`created_at`/`updated_at` timestamptz con `now()`.

Igual que `0034_gym_tables` y las migraciones de expansión previas, este
archivo NO importa `edecan_db.models` (helpers locales duplicados a propósito)
para quedar como una foto fija e independiente de cómo evolucionen los modelos
del ORM. Además:

1. Índice compuesto `(tenant_id, user_id)` — `last_reversible_action`/
   `describe_last_actions` (`edecan_core.action_ledger`) consultan siempre por
   tenant+usuario, y el índice de solo `tenant_id` no alcanza para ese filtro.
2. `GRANT` explícito a `app_user` — misma red de seguridad idempotente que
   `0034`/`0003`/`0004`/`0006`/`0007` documentan.
3. `ENABLE ROW LEVEL SECURITY` + política `tenant_isolation` (tabla
   tenant-scoped). Sin `FORCE ROW LEVEL SECURITY`, mismo motivo que las
   migraciones previas.

Declara su propia tupla `RLS_TABLES` para el cross-check de
`test_migration_rls_tables.py`.

Revision ID: 0038_action_effects
Revises: 0037_memory_conf_exp_negation
Create Date: 2026-08-20 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0038_action_effects"
down_revision: str | None = "0037_memory_conf_exp_negation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APP_ROLE = "app_user"

# La única tabla de esta migración, tenant-scoped con RLS.
RLS_TABLES: tuple[str, ...] = ("action_effects",)
ALL_TABLES_IN_ORDER: tuple[str, ...] = RLS_TABLES


def upgrade() -> None:
    op.create_table(
        "action_effects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
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
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column(
            "inverse_op", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("reversible", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_action_effects_tenant_id", "action_effects", ["tenant_id"])
    op.create_index(
        "ix_action_effects_tenant_id_user_id", "action_effects", ["tenant_id", "user_id"]
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")

    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    # Sin REVOKE: mismo motivo que `0034`/`0003`/`0004`/`0006`/`0007` —
    # `DROP TABLE` ya retira los grants de esta tabla por sí solo.
    for table in reversed(ALL_TABLES_IN_ORDER):
        op.drop_table(table)