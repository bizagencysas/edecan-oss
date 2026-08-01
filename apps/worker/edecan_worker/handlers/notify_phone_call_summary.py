"""Push best-effort cuando un resumen terminal de llamada ya está persistido.

El webhook escribe primero `phone_calls.summary` y su evento de actividad. Este
job solo reclama una vez la notificación y luego sale a APNs/FCM. El cuerpo es
deliberadamente genérico: nunca contiene teléfonos, nombres, transcripción,
objetivo, compromisos ni puntos clave visibles en una pantalla bloqueada.

Lo que SÍ cambia (§3C del plan Aria 2.0) es a dónde lleva el toque. El webhook
ahora también escribe el resumen como mensaje del asistente en la conversación
principal (`phone.py::_write_phone_summary_to_main_chat`) y manda ese
`conversation_id` en el payload como `chat_id`. Con él, el push abre el chat con
el resumen ya escrito en vez de dejar al dueño buscándolo en Actividad. El texto
visible NO se toca: eso es exactamente lo que no debe filtrarse a la pantalla
bloqueada.
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


def _chat_id_opcional(payload: dict | None) -> uuid.UUID | None:
    """`chat_id` del payload, o `None` si no vino o no es un UUID.

    Nunca lanza: un job encolado por una versión anterior del webhook no lo trae,
    y un push que llega a Actividad es infinitamente mejor que un job que revienta
    y deja al dueño sin aviso de que su llamada terminó.
    """
    raw = str((payload or {}).get("chat_id") or "").strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        logger.warning("notify_phone_call_summary: chat_id inválido en el payload; se ignora.")
        return None


def _destino_push(call_id: uuid.UUID, chat_id: uuid.UUID | None) -> dict[str, str]:
    """A dónde lleva tocar la notificación. Solo identificadores opacos: `push.py`
    filtra por allowlist (`_PUSH_DATA_KEYS`) y aquí no entra ni un dato de la llamada.

    Con `chat_id` la ruta es `assistant`, no `activity`, y no es cosmético: iOS SOLO
    honra `chat_id` cuando la ruta es `assistant` (`NotificationDestino.parse` en
    EdecanKit; con `activity` lo descarta a propósito). Se conserva `resource_id`
    para que la llamada siga siendo rastreable desde el payload. Sin `chat_id` el
    destino queda EXACTAMENTE como antes: Actividad.
    """
    if chat_id is None:
        return {"route": "activity", "kind": "call", "resource_id": str(call_id)}
    return {
        "route": "assistant",
        "kind": "call",
        "chat_id": str(chat_id),
        "resource_id": str(call_id),
        "deeplink": f"edecan://chat/{chat_id}",
    }


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("notify_phone_call_summary requiere tenant_id")
    try:
        call_id = uuid.UUID(str(env.payload["call_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("notify_phone_call_summary requiere call_id UUID") from exc
    chat_id = _chat_id_opcional(env.payload)

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
            data=_destino_push(call_id, chat_id),
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
