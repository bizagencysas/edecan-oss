"""Push best-effort cuando un resumen terminal de llamada ya está persistido.

El webhook escribe primero `phone_calls.summary` y su evento de actividad. Este
job solo reclama una vez la notificación y luego sale a APNs/FCM. El cuerpo es
deliberadamente genérico: nunca contiene teléfonos, nombres, transcripción,
objetivo, compromisos ni puntos clave visibles en una pantalla bloqueada.
"""

from __future__ import annotations

import logging
import uuid

from edecan_schemas import JobEnvelope

from edecan_worker import push
from edecan_worker.deps import Deps
from edecan_worker.repo import SqlRepo

logger = logging.getLogger(__name__)

PUSH_TITLE = "Llamada finalizada"
PUSH_BODY = "Edecan preparó un resumen. Ábrelo en Actividad."


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("notify_phone_call_summary requiere tenant_id")
    try:
        call_id = uuid.UUID(str(env.payload["call_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("notify_phone_call_summary requiere call_id UUID") from exc

    async with deps.session_factory(None) as session:
        repo = SqlRepo(session)
        call = await repo.get_phone_call(tenant_id=env.tenant_id, call_id=call_id)
        if call is None or call.get("summary") is None:
            logger.warning(
                "notify_phone_call_summary: resumen no disponible call_id=%s tenant_id=%s",
                call_id,
                env.tenant_id,
            )
            return
        if not await repo.claim_phone_call_summary_push(
            tenant_id=env.tenant_id, call_id=call_id
        ):
            return
        user_id = uuid.UUID(str(call["user_id"]))

    # El claim ya quedó committed. `enviar_push_a_usuario` nunca lanza, pero
    # esta segunda guardia conserva el carácter best-effort ante un doble roto.
    try:
        result = await push.enviar_push_a_usuario(
            deps,
            tenant_id=env.tenant_id,
            user_id=user_id,
            titulo=PUSH_TITLE,
            cuerpo=PUSH_BODY,
            data={"route": "activity", "kind": "call", "resource_id": str(call_id)},
        )
        logger.info(
            "notify_phone_call_summary: call_id=%s enviados=%d fallidos=%d",
            call_id,
            result.enviados,
            result.fallidos,
        )
    except Exception:
        logger.warning(
            "notify_phone_call_summary: push falló call_id=%s tenant_id=%s",
            call_id,
            env.tenant_id,
            exc_info=True,
        )
