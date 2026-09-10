"""Punto de entrada del worker: `python -m edecan_worker.main` (`make worker`,
ARCHITECTURE.md §10.11).

Loop asyncio sobre `SQS_QUEUE_URL`: `receive_message` de hasta 10 mensajes con
long-polling de 20s. Por mensaje, parsea un `JobEnvelope` (`edecan_schemas`),
busca el handler en `edecan_worker.handlers.HANDLERS` según `type` y lo
ejecuta:

- éxito → `delete_message`.
- excepción y `attempt < 5` → re-encola un mensaje nuevo con `attempt + 1` y
  `DelaySeconds = min(900, 2**attempt * 30)`, y borra el mensaje original.
- excepción y `attempt >= 5` → NO se borra: el mensaje vuelve a quedar visible
  tras su visibility timeout y la política de redrive de la cola (infra,
  ARCHITECTURE.md §7) termina moviéndolo a `edecan-jobs-dlq`.

Logging estructurado (job_id/type/tenant_id/attempt) en cada paso vía
`_job_ctx`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import signal
import time
import uuid
from typing import Any

from edecan_core.notifications import ImportantNotificationEvent
from edecan_schemas import JobEnvelope

from edecan_worker.config import get_settings
from edecan_worker.deps import Deps, build_deps
from edecan_worker.handlers import HANDLERS
from edecan_worker.universal_notifications import notify_important_event

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 900
BASE_BACKOFF_SECONDS = 30
WAIT_TIME_SECONDS = 20
MAX_MESSAGES = 10


def compute_backoff_seconds(attempt: int) -> int:
    """`min(900, 2**attempt * 30)` segundos (ARCHITECTURE.md §10.11)."""
    return min(MAX_BACKOFF_SECONDS, (2**attempt) * BASE_BACKOFF_SECONDS)


def _job_ctx(env: JobEnvelope) -> str:
    return f"job_id={env.job_id} type={env.type} tenant_id={env.tenant_id} attempt={env.attempt}"


async def _delete_message(deps: Deps, receipt_handle: str) -> None:
    await deps.sqs.delete_message(
        QueueUrl=deps.settings.SQS_QUEUE_URL, ReceiptHandle=receipt_handle
    )


async def _log_poison_message(deps: Deps, body: str) -> None:
    """F-3: persistir el cuerpo de un mensaje poison en `event_log` antes de
    descartarlo, para poder auditar post-mortem qué productor mandó basura.
    Fail-open: NUNCA lanza — si la tabla no existe o no hay tenant válido,
    el descarte sigue (el logger ya guardó el body en el sistema de logs)."""
    try:
        from edecan_api.event_log import log_event
    except Exception:
        return
    detalle: dict[str, Any] = {"body_len": len(body or "")}
    tenant_id = None
    try:
        raw = json.loads(body)
        if isinstance(raw, dict):
            if raw.get("tenant_id"):
                tenant_id = raw["tenant_id"]
            # H-4: NUNCA el body crudo (un poison puede traer PII/secretos):
            # solo metadatos para reconocer al productor.
            detalle["type"] = str(raw.get("type") or "?")[:100]
            detalle["job_id"] = str(raw.get("job_id") or "?")[:64]
    except Exception:
        pass
    if tenant_id is None:
        return
    try:
        async with deps.session_factory(None) as session:
            resultado = log_event(
                session,
                tenant_id=uuid.UUID(str(tenant_id)),
                categoria="jobs",
                accion="poison_message",
                detalle=detalle,
            )
            if inspect.isawaitable(resultado):
                await resultado
    except Exception:
        logger.warning("no se pudo registrar el poison message en event_log", exc_info=True)


async def _requeue(deps: Deps, env: JobEnvelope) -> None:
    """Re-encola `env` con `attempt + 1` y el backoff correspondiente al intento actual."""
    next_env = env.model_copy(update={"attempt": env.attempt + 1})
    delay = compute_backoff_seconds(env.attempt)
    await deps.sqs.send_message(
        QueueUrl=deps.settings.SQS_QUEUE_URL,
        MessageBody=next_env.model_dump_json(),
        DelaySeconds=delay,
    )
    logger.warning("job reintentado con backoff=%ss %s", delay, _job_ctx(next_env))


async def _propagate_terminal_failure_to_mission(
    deps: Deps, env: JobEnvelope, error_message: str
) -> uuid.UUID | None:
    """Mismo motivo/fix que `edecan_local.worker_loop._propagate_terminal_failure_to_mission`
    (modo escritorio) -- acá su equivalente para el worker hospedado (SQS):
    un job `run_mission` que agota sus reintentos y termina en la DLQ dejaba
    huérfana la misión asociada en `agent_missions` (se queda `status=
    'planning'` para siempre, sin error visible en `/app/misiones`), porque
    `run_mission.handle` solo persiste la transición inicial DESPUÉS de
    resolver la inferencia administrada; un fallo de inicialización antes de
    ese punto nunca toca la fila de la misión. `status NOT IN (...)` evita
    pisar una misión que ya llegó a un
    estado terminal por otro camino."""
    if env.type != "run_mission" or env.tenant_id is None:
        return None
    mission_id = env.payload.get("mission_id")
    if not mission_id:
        return None
    from sqlalchemy import text

    async with deps.session_factory(None) as session:
        result = await session.execute(
            text(
                "UPDATE agent_missions SET status = 'error', error = :error, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :id "
                "AND status NOT IN ('done', 'error', 'cancelled') RETURNING user_id"
            ),
            {
                "tenant_id": str(env.tenant_id),
                "id": str(mission_id),
                "error": error_message[:2000],
            },
        )
        row = result.mappings().first()
        return uuid.UUID(str(row["user_id"])) if row is not None else None


async def _handle_message(deps: Deps, message: dict[str, Any]) -> None:
    receipt_handle = message["ReceiptHandle"]
    body = message.get("Body", "")

    try:
        env = JobEnvelope.model_validate(json.loads(body))
    except Exception:
        logger.exception("mensaje SQS inválido, se descarta sin reintentar: %r", body)
        # F-3: antes de perderlo para siempre, el cuerpo crudo queda en
        # event_log (fail-open) para poder auditar post-mortem qué produjo
        # el poison y por qué ningún handler lo reconoció.
        await _log_poison_message(deps, body)
        await _delete_message(deps, receipt_handle)
        return

    handler = HANDLERS.get(env.type)
    if handler is None:
        logger.error("sin handler registrado, se descarta sin reintentar: %s", _job_ctx(env))
        await _log_poison_message(deps, body)
        await _delete_message(deps, receipt_handle)
        return

    logger.info("procesando %s", _job_ctx(env))
    try:
        await handler(env, deps)
    except Exception as exc:
        logger.exception("fallo procesando %s", _job_ctx(env))
        if env.attempt < MAX_ATTEMPTS:
            await _requeue(deps, env)
            await _delete_message(deps, receipt_handle)
        else:
            logger.error(
                "se agotaron los %d intentos, se deja sin borrar para que el redrive "
                "lo mande a DLQ: %s",
                MAX_ATTEMPTS,
                _job_ctx(env),
            )
            mission_user_id = await _propagate_terminal_failure_to_mission(
                deps, env, f"{type(exc).__name__}: {exc}"
            )
            if mission_user_id is not None:
                await notify_important_event(
                    deps,
                    ImportantNotificationEvent(
                        tenant_id=env.tenant_id,
                        user_id=mission_user_id,
                        kind="work_failed",
                        event_id=uuid.UUID(str(env.payload["mission_id"])),
                        resource_id=uuid.UUID(str(env.payload["mission_id"])),
                    ),
                )
        return

    await _delete_message(deps, receipt_handle)
    logger.info("completado %s", _job_ctx(env))


async def poll_once(deps: Deps) -> int:
    """Un ciclo de `receive_message` + procesamiento. Devuelve cuántos mensajes llegaron."""
    response = await deps.sqs.receive_message(
        QueueUrl=deps.settings.SQS_QUEUE_URL,
        WaitTimeSeconds=WAIT_TIME_SECONDS,
        MaxNumberOfMessages=MAX_MESSAGES,
    )
    messages = response.get("Messages") or []
    for message in messages:
        await _handle_message(deps, message)
    return len(messages)


async def run_forever(deps: Deps, *, stop_event: asyncio.Event | None = None) -> None:
    stop_event = stop_event or asyncio.Event()
    logger.info("edecan_worker escuchando SQS_QUEUE_URL=%s", deps.settings.SQS_QUEUE_URL)
    from edecan_core.queue import QueueTransport, dispatch_outbox

    outbox_transport = QueueTransport(deps.settings)
    ultimo_dispatch = time.monotonic()
    while not stop_event.is_set():
        try:
            # Auditoría F1 (HIGH): el outbox SOLO se despachaba en el loop
            # local. En despliegues SQS, cada fila quedaba 'queued' para
            # siempre (scheduler y push final muertos). Se despacha aquí,
            # cada 2s, antes del poll de mensajes.
            if time.monotonic() - ultimo_dispatch >= 2:
                ultimo_dispatch = time.monotonic()
                try:
                    await dispatch_outbox(
                        session_factory=deps.session_factory,
                        transport=outbox_transport,
                    )
                except Exception:
                    logger.exception("fallo despachando job_outbox; se reintenta en el próximo tick")
            await poll_once(deps)
        except Exception:
            logger.exception("fallo en el ciclo de poll de SQS; se reintenta")
            await asyncio.sleep(1)


async def _amain() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not settings.SQS_QUEUE_URL:
        raise RuntimeError("SQS_QUEUE_URL no está configurado (ver .env.example).")
    settings.assert_safe_for_prod()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # add_signal_handler no está disponible en algunas plataformas (p. ej. Windows).
            pass

    async with build_deps(settings) as deps:
        await run_forever(deps, stop_event=stop_event)


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
