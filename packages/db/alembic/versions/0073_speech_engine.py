"""0073_speech_engine

Voz gestionada por Speech Engine de ElevenLabs (`docs/speech-engine.md`):
tres tablas nuevas, todas tenant-scoped con RLS (mismo patrón que
`0057_agent_messages`).

- `voice_preferences`: una fila por (tenant, user) con la configuración de la
  voz gestionada (proveedor, modelos, voz TTS, duración máxima). Aditiva y
  reversible.
- `speech_engine_sessions`: mapeo durable entre la sesión de Edecán y el
  recurso del proveedor; máquina de estados explícita
  (`provisioning|active|ended|expired|failed`) y expiración.
- `speech_engine_events`: claims durables por `(session_id, event_id)` del
  proveedor para que los replays no dupliquen mensajes ni efectos de tools.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0073_speech_engine"
down_revision: str | None = "0071_gym_checkin_unique_dia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"
RLS_TABLES: tuple[str, ...] = (
    "voice_preferences",
    "speech_engine_sessions",
    "speech_engine_events",
)


def upgrade() -> None:
    op.create_table(
        "voice_preferences",
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
        sa.Column("provider", sa.String(), nullable=False, server_default="elevenlabs"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("voice_model_id", sa.String(), nullable=True),
        sa.Column("delegation_model_id", sa.String(), nullable=True),
        sa.Column("delegation_effort", sa.String(), nullable=True),
        sa.Column("voice_id", sa.String(), nullable=True),
        sa.Column("tts_model_id", sa.String(), nullable=True),
        sa.Column("max_duration_seconds", sa.Integer(), nullable=False, server_default=sa.text("900")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_voice_preferences_tenant_user"),
    )

    op.create_table(
        "speech_engine_sessions",
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
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_key", sa.String(), nullable=False, server_default=""),
        sa.Column("provider_engine_id", sa.String(), nullable=True),
        sa.Column("provider_conversation_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="provisioning"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_duration_seconds", sa.Integer(), nullable=False, server_default=sa.text("900")),
        sa.Column("generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('provisioning', 'active', 'ended', 'expired', 'failed')",
            name="status",
        ),
    )
    op.create_index(
        "ix_speech_engine_sessions_tenant_user",
        "speech_engine_sessions",
        ["tenant_id", "user_id"],
    )
    op.create_index(
        "ix_speech_engine_sessions_expires",
        "speech_engine_sessions",
        ["tenant_id", "expires_at"],
    )

    op.create_table(
        "speech_engine_events",
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
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("speech_engine_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("user_text", sa.Text(), nullable=True),
        sa.Column("assistant_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="processing"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("session_id", "event_id", name="uq_speech_engine_events_session_event"),
        sa.CheckConstraint(
            "status IN ('processing', 'persisted', 'interrupted', 'failed')",
            name="status",
        ),
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