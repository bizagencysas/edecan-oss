"""0001_initial

Migración inicial de Edecán, escrita a mano (ARCHITECTURE.md §10.3):

1. `CREATE EXTENSION IF NOT EXISTS vector` (pgvector).
2. Las 23 tablas del contrato (mismas columnas/tipos que `edecan_db.models`).
3. Un índice por cada columna `tenant_id`.
4. Rol `app_user` (`NOLOGIN`, sin `BYPASSRLS`) creado solo si no existe, con
   grants DML sobre el schema y `ALTER DEFAULT PRIVILEGES` para que tablas de
   migraciones futuras también le otorguen acceso automáticamente.
5. `ENABLE ROW LEVEL SECURITY` + política `tenant_isolation` en cada tabla
   tenant-scoped (§2). **No** se usa `FORCE ROW LEVEL SECURITY`: el dueño de
   las tablas (con el que se conecta el worker, ver ARCHITECTURE.md §2) debe
   seguir bypasseando RLS.

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APP_ROLE = "app_user"

# Tablas tenant-scoped con Row-Level Security — TODAS las tablas salvo las 3
# globales (`tenants`, `users`, `tenant_keys`). Debe coincidir exactamente con
# `edecan_db.models.RLS_TABLES`. Mantenimiento MANUAL: esta migración no
# importa `edecan_db.models` a propósito (ver "Helpers locales" más abajo).
# El cross-check automatizado de esa igualdad vive en
# `packages/db/tests/test_migration_rls_tables.py` (no acá, para no atar esta
# migración congelada a cómo evolucionen los modelos del ORM) — si esta tupla
# y `edecan_db.models.RLS_TABLES` llegan a divergir, ese test falla en vez de
# dejar pasar en silencio una tabla tenant-scoped sin política
# `tenant_isolation`.
RLS_TABLES: tuple[str, ...] = (
    "memberships",
    "personas",
    "conversations",
    "messages",
    "memory_items",
    "memory_edges",
    "connector_accounts",
    "oauth_tokens",
    "files",
    "file_chunks",
    "reminders",
    "contacts",
    "transactions",
    "campaigns",
    "campaign_targets",
    "consents",
    "jobs",
    "usage_events",
    "audit_log",
    "subscriptions",
)

# Todas las tablas en orden de creación (padres antes que hijos, para que las
# FKs siempre apunten a algo que ya existe). El downgrade las borra al revés.
ALL_TABLES_IN_ORDER: tuple[str, ...] = (
    "tenants",
    "users",
    "tenant_keys",
    "memberships",
    "personas",
    "conversations",
    "messages",
    "memory_items",
    "memory_edges",
    "connector_accounts",
    "oauth_tokens",
    "files",
    "file_chunks",
    "reminders",
    "contacts",
    "transactions",
    "campaigns",
    "campaign_targets",
    "consents",
    "jobs",
    "usage_events",
    "audit_log",
    "subscriptions",
)


# ---------------------------------------------------------------------------
# Helpers locales (solo para esta migración: no importan `edecan_db.models` a
# propósito, para que esta migración quede como una foto fija e independiente
# de cómo evolucionen los modelos del ORM más adelante).
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
    # `updated_at` no lleva "on update" a nivel de DDL: SQLAlchemy lo refresca
    # vía `onupdate=func.now()` del lado del ORM (ver `edecan_db.models`), no
    # hay una construcción portable de Postgres para eso sin un trigger.
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


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ========================================================================
    # Tablas globales (sin RLS): tenants, users, tenant_keys
    # ========================================================================

    op.create_table(
        "tenants",
        _id_column(),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("plan_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        *_timestamp_columns(),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )

    op.create_table(
        "users",
        _id_column(),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("totp_secret", sa.String(), nullable=True),
        sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_timestamp_columns(),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "tenant_keys",
        _id_column(),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("encrypted_data_key", sa.LargeBinary(), nullable=False),
        sa.Column("kms_key_id", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamp_columns(),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_keys_tenant_id"),
    )

    # ========================================================================
    # Tenant-scoped
    # ========================================================================

    op.create_table(
        "memberships",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(), nullable=False),
        *_timestamp_columns(),
        # `name=` va SIN el prefijo `ck_<tabla>_`: la convención de nombres de
        # `target_metadata` (`edecan_db.models.NAMING_CONVENTION`, propagada
        # por Alembic a `op.create_table` — ver docstring de ese módulo) lo
        # antepone automáticamente. Pasar el nombre ya completo lo duplicaría
        # (`ck_memberships_ck_memberships_role`).
        sa.CheckConstraint("role IN ('owner', 'admin', 'member')", name="role"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_id_user_id"),
    )
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"])

    op.create_table(
        "personas",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("nombre_asistente", sa.String(), nullable=False, server_default="Edecán"),
        sa.Column("idioma", sa.String(), nullable=False, server_default="es"),
        sa.Column("tono", sa.String(), nullable=False, server_default="cálido y profesional"),
        sa.Column("formalidad", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("emojis", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("instrucciones", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "rasgos", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("memoria_activada", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("voice_id", sa.String(), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint("formalidad >= 0 AND formalidad <= 3", name="formalidad_range"),
    )
    op.create_index("ix_personas_tenant_id", "personas", ["tenant_id"])
    # Como máximo una persona "default" (user_id NULL) por tenant. Postgres
    # trata cada NULL como distinto en un UNIQUE normal, por eso hace falta un
    # índice único parcial en vez de un UNIQUE(tenant_id, user_id).
    op.create_index(
        "uq_personas_tenant_id_default",
        "personas",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )

    op.create_table(
        "conversations",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("channel", sa.String(), nullable=False, server_default="web"),
        *_timestamp_columns(),
        sa.CheckConstraint("channel IN ('web', 'voice', 'phone', 'api')", name="channel"),
    )
    op.create_index("ix_conversations_tenant_id", "conversations", ["tenant_id"])

    op.create_table(
        "messages",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default=sa.text("0")),
        *_timestamp_columns(),
    )
    op.create_index("ix_messages_tenant_id", "messages", ["tenant_id"])

    op.create_table(
        "memory_items",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("importance", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("source", sa.String(), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint("kind IN ('fact', 'preference', 'event', 'entity')", name="kind"),
    )
    op.create_index("ix_memory_items_tenant_id", "memory_items", ["tenant_id"])

    op.create_table(
        "memory_edges",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "src_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dst_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation", sa.String(), nullable=False),
        *_timestamp_columns(),
    )
    op.create_index("ix_memory_edges_tenant_id", "memory_edges", ["tenant_id"])

    op.create_table(
        "connector_accounts",
        _id_column(),
        _tenant_id_column(),
        sa.Column("connector_key", sa.String(), nullable=False),
        sa.Column("external_account_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column(
            "scopes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        *_timestamp_columns(),
        # Nombre acortado a propósito: Postgres rechaza identificadores de más
        # de 63 bytes, y el nombre "obvio" completo
        # (`uq_connector_accounts_tenant_id_connector_key_external_account_id`)
        # los supera. Debe coincidir con `edecan_db.models.ConnectorAccount`.
        sa.UniqueConstraint(
            "tenant_id",
            "connector_key",
            "external_account_id",
            name="uq_connector_accounts_tenant_connector_external",
        ),
    )
    op.create_index("ix_connector_accounts_tenant_id", "connector_accounts", ["tenant_id"])

    op.create_table(
        "oauth_tokens",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "connector_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connector_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.UniqueConstraint("connector_account_id", name="uq_oauth_tokens_connector_account_id"),
    )
    op.create_index("ix_oauth_tokens_tenant_id", "oauth_tokens", ["tenant_id"])

    op.create_table(
        "files",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("s3_key", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("mime", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="uploaded"),
        *_timestamp_columns(),
        sa.CheckConstraint("status IN ('uploaded', 'processing', 'ready', 'error')", name="status"),
    )
    op.create_index("ix_files_tenant_id", "files", ["tenant_id"])

    op.create_table(
        "file_chunks",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        *_timestamp_columns(),
        sa.UniqueConstraint("file_id", "seq", name="uq_file_chunks_file_id_seq"),
    )
    op.create_index("ix_file_chunks_tenant_id", "file_chunks", ["tenant_id"])

    op.create_table(
        "reminders",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("due_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("rrule", sa.String(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        *_timestamp_columns(),
        sa.CheckConstraint("status IN ('pending', 'sent', 'cancelled')", name="status"),
    )
    op.create_index("ix_reminders_tenant_id", "reminders", ["tenant_id"])

    op.create_table(
        "contacts",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column(
            "emails", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "phones", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("empresa", sa.String(), nullable=False, server_default=""),
        sa.Column("notas", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        *_timestamp_columns(),
    )
    op.create_index("ix_contacts_tenant_id", "contacts", ["tenant_id"])

    op.create_table(
        "transactions",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("monto", sa.Numeric(14, 2), nullable=False),
        sa.Column("moneda", sa.CHAR(3), nullable=False),
        sa.Column("categoria", sa.String(), nullable=False),
        sa.Column("descripcion", sa.String(), nullable=False, server_default=""),
        sa.Column("cuenta", sa.String(), nullable=False, server_default=""),
        *_timestamp_columns(),
    )
    op.create_index("ix_transactions_tenant_id", "transactions", ["tenant_id"])

    op.create_table(
        "campaigns",
        _id_column(),
        _tenant_id_column(),
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("script", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column(
            "schedule", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        *_timestamp_columns(),
        sa.CheckConstraint("kind IN ('voice', 'sms')", name="kind"),
    )
    op.create_index("ix_campaigns_tenant_id", "campaigns", ["tenant_id"])

    op.create_table(
        "campaign_targets",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("phone_e164", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("last_attempt_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'optout', 'error')",
            name="status",
        ),
    )
    op.create_index("ix_campaign_targets_tenant_id", "campaign_targets", ["tenant_id"])

    op.create_table(
        "consents",
        _id_column(),
        _tenant_id_column(),
        sa.Column("phone_e164", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column(
            "granted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint("kind IN ('sms', 'voice')", name="kind"),
    )
    op.create_index("ix_consents_tenant_id", "consents", ["tenant_id"])

    op.create_table(
        "jobs",
        _id_column(),
        _tenant_id_column(nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint("status IN ('queued', 'running', 'done', 'error')", name="status"),
        # Mismo vocabulario que `edecan_schemas.queue.JOB_TYPES` (§10.5).
        sa.CheckConstraint(
            "type IN ('ingest_file', 'sync_connector', 'send_reminder', "
            "'send_reminder_scan', 'run_campaign_step', 'generate_content', "
            "'memory_consolidate')",
            name="type",
        ),
    )
    op.create_index("ix_jobs_tenant_id", "jobs", ["tenant_id"])

    op.create_table(
        "usage_events",
        _id_column(),
        _tenant_id_column(),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column(
            "meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "kind IN ('llm_tokens', 'voice_seconds', 'storage_bytes', 'messages')",
            name="kind",
        ),
    )
    op.create_index("ix_usage_events_tenant_id", "usage_events", ["tenant_id"])

    op.create_table(
        "audit_log",
        _id_column(),
        _tenant_id_column(nullable=True, ondelete="SET NULL"),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column(
            "meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        *_timestamp_columns(),
    )
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])

    op.create_table(
        "subscriptions",
        _id_column(),
        _tenant_id_column(),
        sa.Column("stripe_customer_id", sa.String(), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(), nullable=False),
        sa.Column("plan_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("current_period_end", postgresql.TIMESTAMP(timezone=True), nullable=False),
        *_timestamp_columns(),
        sa.UniqueConstraint(
            "stripe_subscription_id", name="uq_subscriptions_stripe_subscription_id"
        ),
    )
    op.create_index("ix_subscriptions_tenant_id", "subscriptions", ["tenant_id"])

    # ========================================================================
    # Rol `app_user` (§2, §10.3) — NOLOGIN, sin BYPASSRLS, creado solo si no
    # existe. `GRANT app_user TO CURRENT_USER` es lo que le permite a quien
    # corrió la migración (el rol con el que se conecta la API/worker en
    # DATABASE_URL) hacer `SET LOCAL ROLE app_user` (ver `edecan_db.session`).
    # ========================================================================

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT {APP_ROLE} TO CURRENT_USER")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    # Para que las tablas de MIGRACIONES FUTURAS (creadas por quien tenga los
    # mismos privilegios que quien corrió esta) también otorguen acceso a
    # `app_user` automáticamente, sin tener que acordarse de repetir el GRANT.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )

    # ========================================================================
    # Row-Level Security — política `tenant_isolation` en cada tabla
    # tenant-scoped. Sin `FORCE`: el dueño de las tablas sigue bypasseando RLS
    # (así se conecta el worker, ver ARCHITECTURE.md §2).
    #
    # `current_setting('app.tenant_id', true)` usa la forma de DOS argumentos
    # (`missing_ok=true`): si `app.tenant_id` nunca se fijó en la sesión,
    # devuelve NULL en vez de lanzar un error — la comparación `tenant_id =
    # NULL` nunca es verdadera, así que el resultado es "fail closed" (ninguna
    # fila visible) en vez de un error de SQL.
    # ========================================================================

    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}"
    )
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    # No se hace DROP ROLE app_user: podría seguir en uso por otra base de
    # datos del mismo cluster Postgres. Un downgrade completo del rol, si
    # hace falta, es una operación manual del operador.

    for table in reversed(ALL_TABLES_IN_ORDER):
        op.drop_table(table)

    op.execute("DROP EXTENSION IF EXISTS vector")
