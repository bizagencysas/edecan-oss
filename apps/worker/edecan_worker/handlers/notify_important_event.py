"""Entrega un evento importante ya reducido a enums e identificadores UUID."""

from __future__ import annotations

import uuid

from edecan_core.notifications import ImportantNotificationEvent
from edecan_schemas import JobEnvelope

from edecan_worker.deps import Deps
from edecan_worker.universal_notifications import notify_important_event


def _optional_uuid(payload: dict[str, object], name: str) -> uuid.UUID | None:
    value = payload.get(name)
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"notify_important_event requiere {name} UUID") from exc


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("notify_important_event requiere tenant_id")
    try:
        user_id = uuid.UUID(str(env.payload["user_id"]))
        event_id = uuid.UUID(str(env.payload["event_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("notify_important_event requiere user_id y event_id UUID") from exc

    event = ImportantNotificationEvent(
        tenant_id=env.tenant_id,
        user_id=user_id,
        kind=str(env.payload.get("kind") or ""),  # type: ignore[arg-type]
        event_id=event_id,
        chat_id=_optional_uuid(env.payload, "chat_id"),
        artifact_id=_optional_uuid(env.payload, "artifact_id"),
        resource_id=_optional_uuid(env.payload, "resource_id"),
    )
    await notify_important_event(deps, event)
