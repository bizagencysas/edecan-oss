"""0006_v4_expansion

Migración v4 de Edecán, escrita a mano (`ARCHITECTURE.md` §13, dueño
WP-V4-01, mismo patrón que `0001_initial`/`0003_v2_expansion`/
`0004_v3_expansion` — ver el docstring de `0001_initial` para el razonamiento
completo de cada pieza; aquí solo se anotan las diferencias):

1. Las 3 tablas nuevas de §13 (mismas columnas/tipos que las 3 clases nuevas
   de `edecan_db.models`, sección "v4" al final de ese módulo), en orden de
   creación (`products` antes que `stock_moves`, que la referencia por FK):
   - `products`: inventario/ERP ligero (WP-V4-06/erp). `UNIQUE(tenant_id, sku)`.
   - `stock_moves`: movimientos de inventario (entradas/salidas/ajustes) de un
     `product`, FK `product_id -> products.id ON DELETE CASCADE`.
   - `ad_drafts`: borradores de campañas publicitarias (WP-V4-07/ads) antes de
     confirmarlas/empujarlas a un proveedor real — mismo espíritu que `orders`
     (WP-V2-10): nace en `status='draft'` y nunca ejecuta nada por sí sola.
2. Un índice por cada columna `tenant_id` — igual que las migraciones previas.
3. `GRANT` explícito a `app_user` sobre estas 3 tablas — misma red de
   seguridad idempotente que `0003_v2_expansion`/`0004_v3_expansion`
   documentan en su punto 3 (por si esta migración la ejecuta un rol de
   Postgres distinto al que corrió `0001_initial`, cuyo `ALTER DEFAULT
   PRIVILEGES` está atado a ESE rol).
4. `ENABLE ROW LEVEL SECURITY` + política `tenant_isolation` en las 3 (ninguna
   trae excepción declarada en §13: todas tenant-scoped). Sin `FORCE ROW
   LEVEL SECURITY`, mismo motivo que las migraciones previas (§2 de
   `ARCHITECTURE.md`): el worker se conecta como dueño y bypassa RLS a
   propósito, filtrando `tenant_id` a mano.

Igual que `0001_initial`/`0003`/`0004`, esta migración declara su propia
tupla `RLS_TABLES` en vez de importar `edecan_db.models` —
`test_migration_rls_tables.py` (packages/db/tests/) es el cross-check que
suma las `RLS_TABLES` de TODAS las migraciones y las compara contra
`edecan_db.models.RLS_TABLES`; ese archivo se extiende junto con esta
migración (mismo criterio que su propio docstring pide para "la PRÓXIMA
migración que agregue tablas tenant-scoped").

Igual que las migraciones previas, este archivo NO importa `edecan_db.models`
(helpers locales `_id_column`/`_timestamp_columns`/`_tenant_id_column`/
`_user_id_column` duplicados a propósito) para quedar como una foto fija e
independiente de cómo evolucionen los modelos del ORM más adelante.

Revision ID: 0006_v4_expansion
Revises: 0005_jobs_type_check_v2_types
Create Date: 2026-07-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_v4_expansion"
down_revision: str | None = "0005_jobs_type_check_v2_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APP_ROLE = "app_user"

# Las 3 tablas de `ARCHITECTURE.md` §13, en orden de creación (`products`
# antes que `stock_moves`, que la referencia por FK). Todas tenant-scoped con
# RLS — ver docstring del módulo para por qué esta tupla hace doble uso
# (grants/RLS de `upgrade()` y cross-check de `test_migration_rls_tables.py`).
RLS_TABLES: tuple[str, ...] = ("products", "stock_moves", "ad_drafts")
ALL_TABLES_IN_ORDER: tuple[str, ...] = RLS_TABLES


# ---------------------------------------------------------------------------
# Helpers locales (idénticos a los de `0001_initial`/`0003_v2_expansion`/
# `0004_v3_expansion`, duplicados a propósito — ver docstring del módulo).
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
    # products / stock_moves (WP-V4-06, `erp.inventory`)
    # ========================================================================

    op.create_table(
        "products",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False, server_default=""),
        sa.Column("unidad", sa.Text(), nullable=False, server_default="unidad"),
        sa.Column("precio", sa.Numeric(14, 2), nullable=True),
        sa.Column("costo", sa.Numeric(14, 2), nullable=True),
        sa.Column("stock", sa.Numeric(14, 3), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "stock_minimo", sa.Numeric(14, 3), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_timestamp_columns(),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_id_sku"),
    )
    op.create_index("ix_products_tenant_id", "products", ["tenant_id"])

    op.create_table(
        "stock_moves",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("delta", sa.Numeric(14, 3), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("nota", sa.Text(), nullable=False, server_default=""),
        sa.Column("ref", sa.Text(), nullable=True),
        *_timestamp_columns(),
    )
    op.create_index("ix_stock_moves_tenant_id", "stock_moves", ["tenant_id"])

    # ========================================================================
    # ad_drafts (WP-V4-07, `tools.ads`) — mismo espíritu que `orders`
    # (WP-V2-10): nace `status='draft'`, ninguna fila mueve/publica nada real
    # por sí sola.
    # ========================================================================

    op.create_table(
        "ad_drafts",
        _id_column(),
        _tenant_id_column(),
        _user_id_column(),
        sa.Column("provider", sa.Text(), nullable=False, server_default="meta"),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("objetivo", sa.Text(), nullable=False),
        sa.Column("presupuesto_diario", sa.Numeric(14, 2), nullable=True),
        sa.Column("moneda", sa.CHAR(3), nullable=False, server_default="USD"),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("confirmed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("pushed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed', 'pushed', 'error', 'cancelled')", name="status"
        ),
    )
    op.create_index("ix_ad_drafts_tenant_id", "ad_drafts", ["tenant_id"])

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

    # Sin REVOKE: mismo motivo que `0003_v2_expansion.downgrade()`/
    # `0004_v3_expansion.downgrade()` — `DROP TABLE` ya retira los grants de
    # estas tablas por sí solo, y un REVOKE de "ALL TABLES IN SCHEMA public"
    # también le quitaría privilegios a las tablas de v1/v2/v3, que esta
    # migración no debe tocar.
    for table in reversed(ALL_TABLES_IN_ORDER):
        op.drop_table(table)
