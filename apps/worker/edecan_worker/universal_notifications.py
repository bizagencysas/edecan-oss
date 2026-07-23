"""Entrega universal: actividad durable primero, push después y best-effort.

Los productores solo construyen :class:`ImportantNotificationEvent`. Este
módulo confirma el evento en ``audit_log`` dentro de una transacción corta;
una vez cerrada, intenta APNs/FCM. Un duplicado, una preferencia apagada o un
fallo de base de datos nunca envían push.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from edecan_core.notifications import ImportantNotificationEvent, record_notification_event

from edecan_worker import push
from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UniversalNotificationResult:
    durable: bool
    duplicate: bool
    push_enabled: bool
    pushed: int
    push_failed: int


async def notify_important_event(
    deps: Deps, event: ImportantNotificationEvent
) -> UniversalNotificationResult:
    """Entrega una transición una sola vez y nunca rompe el trabajo principal."""
    try:
        async with deps.session_factory(None) as session:
            durable = await record_notification_event(session, event)
    except Exception:
        logger.warning(
            "notifications: no se pudo persistir evento tenant_id=%s kind=%s; no se "
            "intentará push",
            event.tenant_id,
            event.kind,
            exc_info=True,
        )
        return UniversalNotificationResult(False, False, False, 0, 0)

    # La transacción terminó antes de llegar aquí. En particular, un cliente
    # nunca puede ver el push y luego descubrir que la actividad no existía.
    if not durable.created:
        return UniversalNotificationResult(True, True, durable.push_enabled, 0, 0)
    if not durable.push_enabled:
        return UniversalNotificationResult(True, False, False, 0, 0)

    try:
        result = await push.enviar_push_a_usuario(
            deps,
            tenant_id=event.tenant_id,
            user_id=event.user_id,
            titulo=event.title,
            cuerpo=event.body,
            data=event.push_data(),
        )
    except Exception:
        # ``enviar_push_a_usuario`` ya promete no lanzar. Esta segunda red
        # protege a los productores ante una regresión futura del adaptador.
        logger.warning(
            "notifications: fallo inesperado de push tenant_id=%s kind=%s; la actividad "
            "ya quedó guardada",
            event.tenant_id,
            event.kind,
            exc_info=True,
        )
        return UniversalNotificationResult(True, False, True, 0, 1)

    return UniversalNotificationResult(
        True,
        False,
        True,
        result.enviados,
        result.fallidos,
    )


__all__ = ["UniversalNotificationResult", "notify_important_event"]
