"""0004_v3_expansion

Migración v3 de Edecán, escrita a mano (`ARCHITECTURE.md` §12e, dueño
WP-V3-01, mismo patrón que `0001_initial`/`0003_v2_expansion` — ver el
docstring de `0001_initial` para el razonamiento completo de cada pieza;
aquí solo se anotan las diferencias):

1. La única tabla nueva de §12e (`skills`) con las mismas columnas/tipos que
   la clase `Skill` de `edecan_db.models` (sección "v3", al final de ese
   módulo).
2. Un índice por la columna `tenant_id` — igual que `0001_initial`/`0003`.
3. `GRANT` explícito a `app_user` sobre esta tabla — misma red de seguridad
   idempotente que `0003_v2_expansion` documenta en su punto 3 (por si esta
   migración la ejecuta un rol de Postgres distinto al que corrió
   `0001_initial`, cuyo `ALTER DEFAULT PRIVILEGES` está atado a ESE rol).
4. `ENABLE ROW LEVEL SECURITY` + política `tenant_isolation` en `skills`
   (tenant-scoped, sin excepción declarada en §12e). Sin `FORCE ROW LEVEL
   SECURITY`, mismo motivo que `0001_initial`/`0003` (§2 de
   `ARCHITECTURE.md`): el worker se conecta como dueño y bypassa RLS a
   propósito, filtrando `tenant_id` a mano.

Igual que `0001_initial`/`0003_v2_expansion`, esta migración declara su
propia tupla `RLS_TABLES` (aquí de un solo elemento) en vez de importar
`edecan_db.models` — `test_migration_rls_tables.py` (packages/db/tests/) es
el cross-check que suma las `RLS_TABLES` de TODAS las migraciones y las
compara contra `edecan_db.models.RLS_TABLES`; ese archivo ya documenta en su
propio docstring que la "próxima migración" (esta) debe sumarse ahí.

Igual que `0001_initial`/`0003`, este archivo NO importa `edecan_db.models`
(helpers locales `_id_column`/`_timestamp_columns`/`_tenant_id_column`
duplicados a propósito) para quedar como una foto fija e independiente de
cómo evolucionen los modelos del ORM más adelante.

Revision ID: 0004_v3_expansion
Revises: 0003_v2_expansion
Create Date: 2026-07-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_v3_expansion"
down_revision: str | None = "0003_v2_expansion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APP_ROLE = "app_user"

# La única tabla de `ARCHITECTURE.md` §12e. Tenant-scoped con RLS — ver
# docstring del módulo para por qué esta tupla hace doble uso (grants/RLS de
# `upgrade()` y cross-check de `test_migration_rls_tables.py`).
RLS_TABLES: tuple[str, ...] = ("skills",)
ALL_TABLES_IN_ORDER: tuple[str, ...] = RLS_TABLES


# ---------------------------------------------------------------------------
# Helpers locales (idénticos a los de `0001_initial`/`0003_v2_expansion`,
# duplicados a propósito — ver docstring del módulo).
# ---------------------------------------------------------------------------


def _id_column() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _timestamp_columns() -> list[sa.Column]:
    return [
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
    ]


def _tenant_id_column(*, nullable: bool = False, ondelete: str = "CASCADE") -> sa.Column:
    return sa.Column(
        "tenant_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete=ondelete),
        nullable=nullable,
    )


def _user_id_column() -> sa.Column:
    return sa.Column(
        "user_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    # ========================================================================
    # skills (WP-V3-04) — un "Agent Skill" instalado (marketplace abierto
    # skills.sh u origen manual) que el toolkit de Edecán puede activar/
    # desactivar. `source` guarda de dónde vino (p. ej. "owner/repo", mismo
    # formato que `npx skills add <owner/repo>`); `contenido` guarda el
    # `SKILL.md` completo tal cual.
    # ========================================================================

    op.create_table(
        "skills",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Text(), nullable=True),
        sa.Column("contenido", sa.Text(), nullable=False),
        sa.Column(
            "recursos", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_timestamp_columns(),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_skills_tenant_id_slug"),
    )
    op.create_index("ix_skills_tenant_id", "skills", ["tenant_id"])

    # ========================================================================
    # Grants + Row-Level Security — ver docstring del módulo (puntos 3 y 4).
    # ========================================================================

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

    # Sin REVOKE: mismo motivo que `0003_v2_expansion.downgrade()` — `DROP
    # TABLE` ya retira los grants de esta tabla por sí solo, y un REVOKE de
    # "ALL TABLES IN SCHEMA public" también le quitaría privilegios a las
    # tablas de v1/v2, que esta migración no debe tocar.
    for table in reversed(ALL_TABLES_IN_ORDER):
        op.drop_table(table)
