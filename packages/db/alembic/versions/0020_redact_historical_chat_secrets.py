"""0020_redact_historical_chat_secrets

Elimina credenciales reconocibles que versiones anteriores pudieron guardar
en títulos, mensajes o resultados de herramientas. La migración es
deliberadamente irreversible: un secreto redactado nunca debe reconstruirse.

Revision ID: 0020_redact_chat_secrets
Revises: 0019_universal_notify
Create Date: 2026-07-22 09:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0020_redact_chat_secrets"
down_revision: str | None = "0019_universal_notify"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PATTERN = (
    r"sk[-_][A-Za-z0-9_-]{8,}"
    r"|Bearer[[:space:]]+[A-Za-z0-9._~+/=-]{8,}"
    # Evitar grupos `(?:...)`: además de no aportar nada aquí, SQLAlchemy
    # interpreta `:rk_live`/`:AKIA` como parámetros bind cuando Alembic recibe
    # SQL textual. Los grupos capturantes POSIX funcionan en PostgreSQL y
    # mantienen esta migración ejecutable en una instalación real.
    r"|(rk_live|rk_test|whsec)_[A-Za-z0-9]{8,}"
    r"|(AKIA|ASIA)[A-Z0-9]{16}"
)


def upgrade() -> None:
    # `content` y `tool_calls` son JSONB. Sustituir sobre su representación
    # JSON conserva la estructura y limpia también strings anidados sin tener
    # que conocer todas las formas históricas del payload.
    op.execute(
        f"""
        UPDATE messages
        SET content = regexp_replace(
            content::text, '{_PATTERN}', '[REDACTED]', 'gi'
        )::jsonb
        WHERE content::text ~* '{_PATTERN}'
        """
    )
    op.execute(
        f"""
        UPDATE messages
        SET tool_calls = regexp_replace(
            tool_calls::text, '{_PATTERN}', '[REDACTED]', 'gi'
        )::jsonb
        WHERE tool_calls IS NOT NULL AND tool_calls::text ~* '{_PATTERN}'
        """
    )
    op.execute(
        f"""
        UPDATE conversations
        SET title = regexp_replace(title, '{_PATTERN}', '[REDACTED]', 'gi')
        WHERE title ~* '{_PATTERN}'
        """
    )


def downgrade() -> None:
    # No-op intencional: restaurar una credencial previamente expuesta sería
    # una regresión de seguridad.
    pass
