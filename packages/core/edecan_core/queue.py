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
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

import aioboto3
from edecan_schemas import JOB_TYPES, JobEnvelope

logger = logging.getLogger(__name__)

_DEFAULT_AWS_REGION = "us-east-1"
_SQS_SERVICE_NAME = "sqs"
_DB_QUEUE_PROVIDER = "db"

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
    if job_type not in JOB_TYPES:
        raise ValueError(f"job_type inválido: {job_type!r}. Debe ser uno de {JOB_TYPES}")

    if getattr(settings, "QUEUE_PROVIDER", "sqs") == _DB_QUEUE_PROVIDER:
        return await _enqueue_db(
            settings, job_type, payload, tenant_id, delay_seconds=delay_seconds
        )

    queue_url = getattr(settings, "SQS_QUEUE_URL", None)
    if not queue_url:
        raise RuntimeError(
            f"SQS_QUEUE_URL no está configurado — no se puede encolar el job {job_type!r} "
            "(ARCHITECTURE.md §10.2)."
        )

    envelope = JobEnvelope(job_id=uuid4(), tenant_id=tenant_id, type=job_type, payload=payload)

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
