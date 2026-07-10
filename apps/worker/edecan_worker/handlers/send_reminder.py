"""Job `send_reminder`: marca el recordatorio como enviado e inserta un
mensaje del asistente con su texto en la conversación "Recordatorios" del
usuario — la crea si no existe, `channel="api"` (ARCHITECTURE.md §10.11).

El `channel` que trae el recordatorio (`web|voice|phone|api|mobile`, elegido
al crearlo con `edecan_toolkit.recordatorios.CrearRecordatorioTool` o
`POST /v1/reminders`) es una preferencia de entrega ADEMÁS del mensaje de
chat, que este job SIEMPRE crea sin importar el canal: hoy no hay número de
teléfono del usuario en el esquema pinned (`ARCHITECTURE.md` §10.3, tabla
`users`) ni este job depende de `edecan_premium`, así que `voice`/`phone` no
tienen una ruta de entrega propia todavía — para no degradarlos en silencio,
se loggea una advertencia cuando eso ocurre (ver `CANALES_SIN_ENTREGA_DEDICADA`
más abajo).

`channel="mobile"` (v5, `ARCHITECTURE.md` §14, dueño WP-V5-13) SÍ tiene ruta
de entrega dedicada: push nativo (APNs/FCM) a los dispositivos `active` del
usuario con `push_token` registrado (`edecan_worker.push.
enviar_push_a_usuario`). El push es SIEMPRE best-effort y ADEMÁS del mensaje
de chat de siempre, nunca en su lugar — el mensaje ya quedó guardado y el
recordatorio ya quedó marcado `sent` ANTES de siquiera intentar el push, así
que si el tenant no conectó APNs/FCM, el usuario no tiene ningún dispositivo
con `push_token`, o el envío falla por cualquier motivo, el recordatorio de
todos modos vive en la conversación "Recordatorios" — la entrega push nunca
puede hacer que un recordatorio "se pierda". `push.enviar_push_a_usuario` en
sí ya nunca lanza (ver su docstring), pero esta función igual envuelve la
llamada en su propio `try/except` como segunda red de seguridad: ni un bug
futuro en `push.py` puede tumbar este job.

Payload: `{"reminder_id": "<uuid>"}`. Requiere `env.tenant_id` (lo encola
`send_reminder_scan` con el `tenant_id` del propio recordatorio).
"""

from __future__ import annotations

import logging
import uuid

from edecan_schemas import JobEnvelope

from edecan_worker import push
from edecan_worker.deps import Deps
from edecan_worker.repo import SqlRepo

logger = logging.getLogger(__name__)

TITULO_CONVERSACION = "Recordatorios"
CANAL_CONVERSACION = "api"
TITULO_PUSH = "Recordatorio"

# Canales que `crear_recordatorio` acepta (recordatorios.py) pero que este
# job todavía no sabe entregar por su propia vía (llamada/SMS reales) — se
# entregan igual como mensaje de chat, pero logueando la degradación en vez
# de hacerlo en silencio. `"mobile"` NO está acá: tiene ruta de entrega
# dedicada (push, ver docstring del módulo), se maneja aparte en `handle`.
CANALES_SIN_ENTREGA_DEDICADA = ("voice", "phone")


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("send_reminder requiere tenant_id")
    reminder_id = uuid.UUID(str(env.payload["reminder_id"]))

    async with deps.session_factory(None) as session:
        repo = SqlRepo(session)

        reminder = await repo.get_reminder(tenant_id=env.tenant_id, reminder_id=reminder_id)
        if reminder is None:
            logger.error(
                "send_reminder: recordatorio no encontrado reminder_id=%s tenant_id=%s",
                reminder_id,
                env.tenant_id,
            )
            return
        if reminder["status"] != "pending":
            logger.info(
                "send_reminder: recordatorio %s ya no está pending (status=%s), se ignora",
                reminder_id,
                reminder["status"],
            )
            return

        channel = reminder.get("channel") or "web"
        if channel in CANALES_SIN_ENTREGA_DEDICADA:
            logger.warning(
                "send_reminder: recordatorio %s pidió channel=%s (sin ruta de entrega "
                "propia todavía); se entrega como mensaje de chat en su lugar",
                reminder_id,
                channel,
            )

        user_id = reminder["user_id"]
        conversation = await repo.get_conversation_by_title(
            tenant_id=env.tenant_id, user_id=user_id, title=TITULO_CONVERSACION
        )
        if conversation is None:
            conversation = await repo.create_conversation(
                tenant_id=env.tenant_id,
                user_id=user_id,
                title=TITULO_CONVERSACION,
                channel=CANAL_CONVERSACION,
            )

        await repo.add_message(
            tenant_id=env.tenant_id,
            conversation_id=conversation["id"],
            role="assistant",
            content={"text": f"Recordatorio: {reminder['message']}"},
        )
        await repo.mark_reminder_sent(tenant_id=env.tenant_id, reminder_id=reminder_id)

    # El mensaje de chat de arriba YA está guardado (fuera de la transacción,
    # que cerró/comiteó al salir del `async with`) antes de intentar el push
    # — así el push nunca puede impedir que el recordatorio quede registrado
    # (ver docstring del módulo). `push.enviar_push_a_usuario` ya nunca lanza
    # por diseño, pero el `try/except` de acá es una segunda red de
    # seguridad deliberada: ni un bug futuro en `push.py` puede tumbar este
    # job.
    if channel == "mobile":
        try:
            resultado = await push.enviar_push_a_usuario(
                deps,
                tenant_id=env.tenant_id,
                user_id=user_id,
                titulo=TITULO_PUSH,
                cuerpo=reminder["message"],
            )
            logger.info(
                "send_reminder: push mobile reminder_id=%s enviados=%d fallidos=%d",
                reminder_id,
                resultado.enviados,
                resultado.fallidos,
            )
        except Exception:
            logger.warning(
                "send_reminder: fallo inesperado enviando push mobile reminder_id=%s "
                "(el recordatorio YA quedó registrado como mensaje, esto no lo afecta)",
                reminder_id,
                exc_info=True,
            )

    logger.info("send_reminder completado reminder_id=%s tenant_id=%s", reminder_id, env.tenant_id)
