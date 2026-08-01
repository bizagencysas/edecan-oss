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

## Cobertura universal para los canales sin push dedicado (frente 2,
"Push para todo")

`channel="mobile"` ya tiene su propio push nativo de arriba, con el texto
REAL del recordatorio en el cuerpo (más útil que el genérico del contrato
universal) — ese camino no cambia. Pero `web`/`api`/`voice`/`phone` no
avisaban de NADA fuera de la conversación "Recordatorios": si el dueño no
tenía la app abierta justo ahí, un recordatorio disparado en esos canales
pasaba en silencio. Para esos cuatro, además del mensaje de chat de
siempre, se avisa vía
`edecan_worker.universal_notifications.notify_important_event`
(`kind="reminder_triggered"`, actividad durable + push best-effort
respetando la preferencia de categoría, ver su docstring) — nunca los DOS
mecanismos a la vez para el mismo recordatorio: eso duplicaría el push que
`channel="mobile"` ya entrega. La clave de dedup NO es `reminder_id` a
secas: un recordatorio con `rrule` reutiliza el MISMO `reminder_id` en cada
disparo (`repo.mark_reminder_sent` lo reprograma a `'pending'` de nuevo con
un `due_at` nuevo, no lo cierra como a uno de una sola vez) — se deriva un
`event_id` determinista por OCURRENCIA (`uuid5(reminder_id, due_at)`, ver
`handle()`) para que CADA disparo real avise, no solo el primero.
"""

from __future__ import annotations

import logging
import uuid

from edecan_core.notifications import ImportantNotificationEvent
from edecan_schemas import JobEnvelope

from edecan_worker import push
from edecan_worker.deps import Deps
from edecan_worker.repo import SqlRepo
from edecan_worker.universal_notifications import notify_important_event

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
        # Clave de dedup por OCURRENCIA, no por recordatorio (ver docstring
        # del módulo): un recordatorio con `rrule` reutiliza el MISMO
        # `reminder_id` en cada disparo (`mark_reminder_sent` lo deja
        # `'pending'` de nuevo con un `due_at` reprogramado, no lo cierra) —
        # si `event_id` fuera `reminder_id` a secas, `record_notification_
        # event` trataría el segundo disparo real como duplicado del primero
        # y jamás volvería a avisar. `uuid5` derivado de `reminder_id` +
        # `due_at` de ESTA ocurrencia es determinista: un reintento del
        # mismo job (misma ocurrencia) sigue deduplicando bien, una
        # ocurrencia distinta (due_at distinto) produce un UUID distinto.
        occurrence_id = uuid.uuid5(reminder_id, str(reminder["due_at"]))
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
                data={
                    "route": "activity",
                    "kind": "reminder",
                    "resource_id": str(reminder_id),
                },
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
    else:
        # Ver docstring del módulo, "## Cobertura universal para los canales
        # sin push dedicado": `web`/`api`/`voice`/`phone` no tenían ningún
        # aviso fuera del mensaje de chat. `notify_important_event` ya es
        # best-effort por diseño (nunca lanza, ver su docstring) — no hace
        # falta un `try/except` propio acá, a diferencia del bloque `mobile`
        # de arriba, que sí llama a `push.enviar_push_a_usuario` directo.
        await notify_important_event(
            deps,
            ImportantNotificationEvent(
                tenant_id=env.tenant_id,
                user_id=user_id,
                kind="reminder_triggered",
                event_id=occurrence_id,
                resource_id=reminder_id,
            ),
        )

    logger.info("send_reminder completado reminder_id=%s tenant_id=%s", reminder_id, env.tenant_id)
