"""`enqueue` — encola un job asíncrono en SQS, o en la tabla `jobs` de Postgres
(ARCHITECTURE.md §10.7, §10.11, §12g).

Lo usan tanto `edecan_api` (p. ej. tras subir un archivo, encola
`ingest_file`) como herramientas de `edecan_toolkit`/`premium` (p. ej.
`lanzar_campana` encola `run_campaign_step`, ver
`edecan_premium.tools.LanzarCampanaTool`).

Dos proveedores de cola, elegidos por `settings.QUEUE_PROVIDER`
(ARCHITECTURE.md §12g, default `"sqs"` — comportamiento IDÉNTICO a antes de
que existiera este campo, así que ningún caller existente cambia de
comportamiento):

- `"sqs"` (default): el camino de siempre, `aioboto3` contra `SQS_QUEUE_URL`.
- `"db"`: en vez de SQS, hace `INSERT` directo en la tabla `jobs` (Postgres)
  vía `asyncpg`. Pensado para el runner local de la app de escritorio
  (`apps/local`, WP-V3-05), que no quiere depender de LocalStack/SQS en la
  máquina del cliente — `edecan_local.worker_loop` es quien consume esa
  tabla como cola (`SELECT ... FOR UPDATE SKIP LOCKED`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

import aioboto3
from edecan_schemas import JOB_TYPES, JobEnvelope

logger = logging.getLogger(__name__)

_DEFAULT_AWS_REGION = "us-east-1"
_SQS_SERVICE_NAME = "sqs"
_DB_QUEUE_PROVIDER = "db"
_OUTBOX_BATCH_SIZE = 50
_OUTBOX_MAX_ATTEMPTS = 5
_OUTBOX_MAX_BACKOFF_SECONDS = 900
_OUTBOX_BASE_BACKOFF_SECONDS = 30

# Clave dentro de `payload` donde `_enqueue_db` guarda el equivalente de
# `delay_seconds` (ver su docstring) — `edecan_local.worker_loop` (WP-V3-05)
# es quien la lee para decidir si una fila `queued` ya "toca".
_NOT_BEFORE_PAYLOAD_KEY = "_not_before"


class QueueSettingsLike(Protocol):
    """Atributos que `enqueue` necesita de la configuración (ARCHITECTURE.md §10.2, §12g).

    Igual que `edecan_llm.router.SettingsLike`: no se importa una clase
    `Settings` concreta de `apps/*` para no acoplar este paquete a ella — se
    leen con `getattr` (con default), así que un doble de prueba puede omitir
    campos que no use. `QUEUE_PROVIDER`/`DATABASE_URL` solo hacen falta para
    la rama `QUEUE_PROVIDER="db"` (§12g) — por eso no son atributos
    "obligatorios" de este Protocol (que documenta el camino SQS de siempre),
    sino que `enqueue`/`_enqueue_db` los leen con `getattr` más abajo.
    """

    SQS_QUEUE_URL: str | None
    AWS_ENDPOINT_URL: str | None
    AWS_REGION: str


class OutboxTransport(Protocol):
    """Delivery boundary used by :func:`dispatch_outbox`."""

    async def send(self, envelope: JobEnvelope) -> Any: ...


def _sql(statement: str) -> Any:
    """Wrap textual SQL when SQLAlchemy is installed by the hosting process."""
    try:
        from sqlalchemy import text
    except ImportError:  # pragma: no cover - standalone core without SQLAlchemy
        return statement
    return text(statement)


def _validate_job_type(job_type: str) -> None:
    if job_type not in JOB_TYPES:
        raise ValueError(f"job_type inválido: {job_type!r}. Debe ser uno de {JOB_TYPES}")


async def _send_sqs_envelope(
    settings: QueueSettingsLike,
    envelope: JobEnvelope,
    *,
    delay_seconds: int | None = None,
) -> None:
    queue_url = getattr(settings, "SQS_QUEUE_URL", None)
    if not queue_url:
        raise RuntimeError(
            f"SQS_QUEUE_URL no está configurado — no se puede encolar el job "
            f"{envelope.type!r} (ARCHITECTURE.md §10.2)."
        )

    region = getattr(settings, "AWS_REGION", None) or _DEFAULT_AWS_REGION
    endpoint_url = getattr(settings, "AWS_ENDPOINT_URL", None)
    send_kwargs: dict[str, Any] = {
        "QueueUrl": queue_url,
        "MessageBody": envelope.model_dump_json(),
    }
    if delay_seconds is not None:
        send_kwargs["DelaySeconds"] = delay_seconds

    session = aioboto3.Session()
    async with session.client(
        _SQS_SERVICE_NAME, region_name=region, endpoint_url=endpoint_url
    ) as sqs:
        await sqs.send_message(**send_kwargs)


class QueueTransport:
    """Publish outbox envelopes to the configured real queue provider.

    The outbox id is also the downstream ``JobEnvelope.job_id``. In DB mode
    the explicit id plus ``ON CONFLICT DO NOTHING`` makes a retry after a
    publish/commit ambiguity idempotent at the queue boundary.
    """

    def __init__(self, settings: QueueSettingsLike) -> None:
        self._settings = settings

    async def send(self, envelope: JobEnvelope) -> None:
        if getattr(self._settings, "QUEUE_PROVIDER", "sqs") == _DB_QUEUE_PROVIDER:
            await self._send_db(envelope)
            return
        await _send_sqs_envelope(self._settings, envelope)

    async def _send_db(self, envelope: JobEnvelope) -> None:
        import asyncpg

        dsn = _to_asyncpg_dsn(getattr(self._settings, "DATABASE_URL", None))
        if not dsn:
            raise RuntimeError(
                f"DATABASE_URL no está configurado — no se puede despachar el job "
                f"{envelope.type!r} desde job_outbox."
            )

        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "INSERT INTO jobs (id, tenant_id, type, payload, status, attempts) "
                "VALUES ($1, $2, $3, $4::jsonb, 'queued', 0) "
                "ON CONFLICT (id) DO NOTHING",
                envelope.job_id,
                envelope.tenant_id,
                envelope.type,
                json.dumps(envelope.payload, default=str),
            )
        finally:
            await conn.close()


async def enqueue_outbox(
    session: Any,
    *,
    tenant_id: UUID | None,
    job_type: str,
    payload: dict[str, Any],
) -> UUID:
    """Persist a queued event in the caller's current business transaction.

    This function deliberately never commits and never opens another session:
    the business mutation and its request for asynchronous work therefore
    either commit together or roll back together.
    """
    # Auditoría H1: job_outbox.tenant_id es NOT NULL — un job GLOBAL no
    # tiene cabida aquí; falla temprano con error claro en vez de un
    # NotNullViolation a mitad de la transacción.
    if tenant_id is None:
        raise ValueError("enqueue_outbox exige tenant_id (los jobs globales usan enqueue()).")
    _validate_job_type(job_type)
    outbox_id = uuid4()
    await session.execute(
        _sql(
            "INSERT INTO job_outbox ("
            "id, tenant_id, job_type, payload, status, attempts, available_at, "
            "sent_at, last_error, created_at, updated_at"
            ") VALUES ("
            ":id, :tenant_id, :job_type, CAST(:payload AS jsonb), 'queued', 0, "
            "now(), NULL, NULL, now(), now()"
            ")"
        ),
        {
            "id": outbox_id,
            "tenant_id": tenant_id,
            "job_type": job_type,
            "payload": json.dumps(dict(payload), default=str),
        },
    )
    return outbox_id


def _decode_outbox_payload(raw_payload: Any) -> dict[str, Any]:
    payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    if not isinstance(payload, dict):
        raise ValueError("job_outbox.payload debe ser un objeto JSON")
    return dict(payload)


def _outbox_backoff_seconds(attempts: int) -> int:
    bounded_attempts = max(0, min(attempts, _OUTBOX_MAX_ATTEMPTS))
    return min(
        _OUTBOX_MAX_BACKOFF_SECONDS,
        (2**bounded_attempts) * _OUTBOX_BASE_BACKOFF_SECONDS,
    )


async def _send_outbox(
    transport: OutboxTransport | Callable[[JobEnvelope], Awaitable[Any]],
    envelope: JobEnvelope,
) -> None:
    sender = getattr(transport, "send", None)
    if sender is not None:
        await sender(envelope)
        return
    await transport(envelope)


async def dispatch_outbox(
    *,
    session_factory: Callable[[UUID | None], Any],
    transport: OutboxTransport | Callable[[JobEnvelope], Awaitable[Any]],
) -> int:
    """Publish at most 50 ready outbox rows and return the successful count.

    Rows stay locked while they are published so concurrent dispatchers skip
    them. Delivery is at-least-once: a process can die after the transport
    accepts an envelope but before this transaction marks it ``sent``. The
    stable outbox id is therefore reused as ``JobEnvelope.job_id``; the DB
    transport deduplicates that id at insert time.
    """
    sent = 0
    now = datetime.now(UTC)
    async with session_factory(None) as session:
        result = await session.execute(
            _sql(
                "SELECT id, tenant_id, job_type, payload, attempts FROM job_outbox "
                "WHERE status = 'queued' AND available_at <= :now "
                "ORDER BY available_at ASC, created_at ASC, id ASC "
                "LIMIT :limit FOR UPDATE SKIP LOCKED"
            ),
            {"now": now, "limit": _OUTBOX_BATCH_SIZE},
        )
        rows = [dict(row) for row in result.mappings().all()]
        for row in rows:
            outbox_id = UUID(str(row["id"]))
            attempts = int(row.get("attempts") or 0)
            next_attempts = attempts + 1
            try:
                payload = _decode_outbox_payload(row.get("payload"))
                envelope = JobEnvelope(
                    job_id=outbox_id,
                    tenant_id=(
                        UUID(str(row["tenant_id"])) if row.get("tenant_id") is not None else None
                    ),
                    type=str(row["job_type"]),
                    payload=payload,
                )
                await _send_outbox(transport, envelope)
            except Exception as exc:
                status = "dead" if next_attempts >= _OUTBOX_MAX_ATTEMPTS else "queued"
                available_at = now + timedelta(seconds=_outbox_backoff_seconds(attempts))
                await session.execute(
                    _sql(
                        "UPDATE job_outbox SET status = :status, attempts = :attempts, "
                        "available_at = :available_at, sent_at = NULL, last_error = :last_error, "
                        "updated_at = now() WHERE id = :id"
                    ),
                    {
                        "id": outbox_id,
                        "status": status,
                        "attempts": next_attempts,
                        "available_at": available_at,
                        "last_error": f"{type(exc).__name__}: {exc}"[:2000],
                    },
                )
                logger.warning(
                    "job_outbox publish failed id=%s attempt=%s status=%s",
                    outbox_id,
                    next_attempts,
                    status,
                    exc_info=True,
                )
                continue

            await session.execute(
                _sql(
                    "UPDATE job_outbox SET status = :status, attempts = :attempts, "
                    "sent_at = :sent_at, last_error = NULL, updated_at = now() WHERE id = :id"
                ),
                {
                    "id": outbox_id,
                    "status": "sent",
                    "attempts": next_attempts,
                    "sent_at": datetime.now(UTC),
                },
            )
            sent += 1

    return sent


async def enqueue(
    settings: QueueSettingsLike,
    job_type: str,
    payload: dict[str, Any],
    tenant_id: UUID | None,
    *,
    delay_seconds: int | None = None,
) -> UUID:
    """Valida `job_type` y encola el job — a SQS, o a la tabla `jobs` si
    `getattr(settings, "QUEUE_PROVIDER", "sqs") == "db"` (ver docstring del
    módulo). El resto de esta docstring describe el camino SQS (default):

    Usa `aioboto3`; si `settings.AWS_ENDPOINT_URL` está definido (dev →
    LocalStack, ARCHITECTURE.md §8), el cliente SQS apunta ahí en vez de a AWS
    real. `tenant_id` puede ser `None` para jobs globales (p. ej.
    `send_reminder_scan`, ARCHITECTURE.md §10.5/§10.11). Devuelve el `job_id`
    generado (también viaja dentro del `JobEnvelope`).

    `delay_seconds`, si se pasa, se reenvía como `DelaySeconds` de SQS (máx.
    900s, límite del servicio) para que un caller que se auto-reencola (p.
    ej. `run_campaign_step` cuando el cupo del plan está agotado, ver
    `edecan_premium.campaigns.handle`) pueda esperar antes de que el mensaje
    quede visible, en vez de hacer busy-loop contra Postgres/SQS. Por
    defecto `None`: no se manda `DelaySeconds` y SQS usa su default (0s),
    igual que antes de agregar este parámetro. En la rama `"db"`, el
    equivalente se guarda en `payload["_not_before"]` (ver `_enqueue_db`).
    """
    _validate_job_type(job_type)

    if getattr(settings, "QUEUE_PROVIDER", "sqs") == _DB_QUEUE_PROVIDER:
        return await _enqueue_db(
            settings, job_type, payload, tenant_id, delay_seconds=delay_seconds
        )

    envelope = JobEnvelope(job_id=uuid4(), tenant_id=tenant_id, type=job_type, payload=payload)
    await _send_sqs_envelope(settings, envelope, delay_seconds=delay_seconds)

    logger.info("Job %s encolado: type=%s tenant_id=%s", envelope.job_id, job_type, tenant_id)
    return envelope.job_id


def _to_asyncpg_dsn(database_url: str | None) -> str | None:
    """`postgresql+asyncpg://...` (formato SQLAlchemy, ARCHITECTURE.md §10.2)
    -> `postgresql://...` (DSN que entiende `asyncpg.connect` directo: no
    conoce el sufijo `+asyncpg` del dialecto). `None`/vacío -> `None`."""
    if not database_url:
        return None
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _enqueue_db(
    settings: QueueSettingsLike,
    job_type: str,
    payload: dict[str, Any],
    tenant_id: UUID | None,
    *,
    delay_seconds: int | None = None,
) -> UUID:
    """Encola `job_type` insertando una fila en la tabla `jobs` (Postgres) en
    vez de mandarla a SQS — ver "Dos proveedores de cola" en el docstring del
    módulo. Pensado para `edecan_local` (runner de la app de escritorio,
    WP-V3-05, ARCHITECTURE.md §12f): sin SQS/LocalStack en la máquina del
    cliente, `edecan_local.worker_loop` consume esta misma tabla con
    `SELECT ... FOR UPDATE SKIP LOCKED`.

    Usa una conexión `asyncpg` EFÍMERA — se abre y se cierra en esta misma
    llamada, a diferencia del pool de vida larga que usa `edecan_db.session`
    para el resto de la API/worker — porque `enqueue` se invoca desde
    lugares muy distintos (herramientas, handlers, schedulers) que no
    siempre tienen una `AsyncSession` de SQLAlchemy ya abierta a mano. Import
    perezoso de `asyncpg` (mismo criterio que el resto del repo con paquetes
    hermanos/dependencias opcionales, ARCHITECTURE.md §10.1): este paquete
    (`edecan-core`) no declara `asyncpg` como dependencia dura — solo hace
    falta en el proceso que de verdad use `QUEUE_PROVIDER="db"`
    (`edecan-local`, que sí la declara).

    `settings.DATABASE_URL` debe venir en formato SQLAlchemy
    (`postgresql+asyncpg://...`); se convierte con `_to_asyncpg_dsn` antes de
    conectar. Si falta, `RuntimeError` claro (mismo criterio que la rama SQS
    sin `SQS_QUEUE_URL`).

    `delay_seconds`: la rama SQS lo manda como `DelaySeconds` nativo; aquí no
    existe tal cosa, así que se guarda el equivalente dentro del propio
    `payload`, como `payload["_not_before"]` (ISO-8601 UTC del momento a
    partir del cual el job ya se puede tomar) — `edecan_local.worker_loop` es
    quien lee esa clave para decidir si una fila `queued` ya "toca" o
    todavía no, y la retira del `payload` antes de armar el `JobEnvelope` que
    le pasa al handler (no es parte del payload "real" del job).

    El `id` que genera Postgres (`gen_random_uuid()` por default de la
    columna, ver `edecan_db.models.Job`/migración `0001_initial`) se usa
    como `job_id` — mismo tipo (`UUID`) que devuelve la rama SQS, así que
    ningún caller de `enqueue` tiene que distinguir cuál rama corrió.
    """
    import asyncpg

    dsn = _to_asyncpg_dsn(getattr(settings, "DATABASE_URL", None))
    if not dsn:
        raise RuntimeError(
            f"DATABASE_URL no está configurado — no se puede encolar el job {job_type!r} "
            "en la tabla 'jobs' (QUEUE_PROVIDER='db', ARCHITECTURE.md §12g)."
        )

    envelope_payload = dict(payload)
    if delay_seconds is not None:
        # Auditoría F2: sin tope, un caller con delay>900s tapaba la cola
        # DB indefinidamente (head-of-line). SQS ya limita a 900s nativo;
        # acá se aplica el MISMO tope.
        delay_seconds = max(0, min(int(delay_seconds), 900))
        not_before = datetime.now(UTC) + timedelta(seconds=delay_seconds)
        envelope_payload[_NOT_BEFORE_PAYLOAD_KEY] = not_before.isoformat()

    conn = await asyncpg.connect(dsn)
    try:
        job_id = await conn.fetchval(
            "INSERT INTO jobs (tenant_id, type, payload, status, attempts) "
            "VALUES ($1, $2, $3::jsonb, 'queued', 0) RETURNING id",
            tenant_id,
            job_type,
            json.dumps(envelope_payload, default=str),
        )
    finally:
        await conn.close()

    logger.info("Job %s encolado (tabla jobs): type=%s tenant_id=%s", job_id, job_type, tenant_id)
    return job_id
