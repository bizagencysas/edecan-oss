"""Persistencia opcional del snapshot de sesión unificada.

El core no depende de SQLAlchemy: recibe una sesión duck-typed y usa SQL
parametrizado cuando el proceso dispone del driver. Esto permite que desktop,
tests y workers compartan el contrato sin importar la capa de DB.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from .session import UnifiedSessionState


async def load_unified_session(
    db_session: Any,
    *,
    tenant_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
) -> UnifiedSessionState | None:
    if db_session is None or not hasattr(db_session, "execute"):
        return None
    from sqlalchemy import text

    result = await db_session.execute(
        text(
            "SELECT state FROM unified_sessions "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id "
            "AND conversation_id = :conversation_id"
        ),
        {
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
        },
    )
    row = result.mappings().first()
    if not row or not isinstance(row.get("state"), dict):
        return None
    return UnifiedSessionState.from_dict(row["state"])


async def save_unified_session(
    db_session: Any,
    state: UnifiedSessionState,
    *,
    tenant_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
) -> None:
    if db_session is None or not hasattr(db_session, "execute"):
        return
    from sqlalchemy import text

    payload = json.loads(json.dumps(state.to_dict()))
    await db_session.execute(
        text(
            "INSERT INTO unified_sessions "
            "(tenant_id, user_id, conversation_id, state) "
            "VALUES (:tenant_id, :user_id, :conversation_id, CAST(:state AS jsonb)) "
            "ON CONFLICT (tenant_id, user_id, conversation_id) DO UPDATE SET "
            "state = EXCLUDED.state, updated_at = now() "
            "WHERE COALESCE((unified_sessions.state->>'updated_at')::double precision, 0) "
            "<= COALESCE((EXCLUDED.state->>'updated_at')::double precision, 0)"
        ),
        {
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
            "state": json.dumps(payload),
        },
    )
