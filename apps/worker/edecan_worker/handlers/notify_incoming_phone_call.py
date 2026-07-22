"""Actividad y push seguro al comenzar una llamada entrante.

El webhook ya confirmó la llamada y su evento ``incoming`` antes de encolar.
Este job relee ambos, construye un evento sin texto libre y delega en el
contrato universal: actividad durable, deduplicación, preferencia y, solo
después, push best-effort. El resumen terminal vive en un job independiente.
"""

from __future__ import annotations

import logging
import uuid

from edecan_core.notifications import ImportantNotificationEvent
from edecan_schemas import JobEnvelope

from edecan_worker.deps import Deps
from edecan_worker.repo import SqlRepo
from edecan_worker.universal_notifications import notify_important_event

logger = logging.getLogger(__name__)


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("notify_incoming_phone_call requiere tenant_id")
    try:
        call_id = uuid.UUID(str(env.payload["call_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("notify_incoming_phone_call requiere call_id UUID") from exc

    async with deps.session_factory(None) as session:
        repo = SqlRepo(session)
        call = await repo.get_phone_call(tenant_id=env.tenant_id, call_id=call_id)
        if call is None or call.get("direction") != "incoming":
            logger.warning(
                "notify_incoming_phone_call: llamada entrante no disponible "
                "call_id=%s tenant_id=%s",
                call_id,
                env.tenant_id,
            )
            return
        if not await repo.has_phone_call_event(
            tenant_id=env.tenant_id,
            call_id=call_id,
            event_type="incoming",
        ):
            logger.warning(
                "notify_incoming_phone_call: evento durable ausente "
                "call_id=%s tenant_id=%s; no se notificará",
                call_id,
                env.tenant_id,
            )
            return
        user_id = uuid.UUID(str(call["user_id"]))

    result = await notify_important_event(
        deps,
        ImportantNotificationEvent(
            tenant_id=env.tenant_id,
            user_id=user_id,
            kind="phone_call_incoming",
            event_id=call_id,
            resource_id=call_id,
        ),
    )
    logger.info(
        "notify_incoming_phone_call: call_id=%s durable=%s duplicate=%s "
        "push_enabled=%s enviados=%d fallidos=%d",
        call_id,
        result.durable,
        result.duplicate,
        result.push_enabled,
        result.pushed,
        result.push_failed,
    )
