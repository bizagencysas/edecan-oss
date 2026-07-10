"""Worker in-process del runner local: consume la tabla `jobs` (Postgres,
`QUEUE_PROVIDER="db"`, `edecan_core.queue._enqueue_db`) en vez de SQS —
ARCHITECTURE.md §12f, dueño WP-V3-05. Reemplaza, DENTRO del mismo proceso
del runner de escritorio, el rol que `edecan_worker.main`/`edecan_worker.
scheduler` cumplen en dev/prod contra SQS + EventBridge Scheduler (ver
`edecan_local.runtime` para cómo se arma todo junto).

## Por qué no reutiliza `edecan_worker.deps.build_deps`

`build_deps(settings)` (`apps/worker/edecan_worker/deps.py`) construye sus
colaboradores a partir de un `edecan_worker.config.Settings` — una clase de
Settings DISTINTA de `edecan_api.config.Settings` (mismos nombres de campo
para los v1/v2, ARCHITECTURE.md §10.2, pero SIN los campos v3 de §12g:
`QUEUE_PROVIDER`, `EDECAN_LOCAL_MODE`, etc., que SÍ hacen falta aquí — p. ej.
para que un handler que se auto-reencola, como
`edecan_premium.campaigns.handle` vía `run_campaign_step`, llame
`enqueue(deps.settings, ...)` y tome de verdad la rama `QUEUE_PROVIDER="db"`
en vez de intentar SQS). Este módulo arma su PROPIO `edecan_worker.deps.Deps`
(SÍ reutiliza esa dataclass pública, y por tanto `Deps.llm_router_for()`) con
`settings=` el `edecan_api.config.Settings` real del runner — superset
estructural de los mismos nombres (`edecan_worker.config.Settings` documenta
que declara "los mismos nombres EXACTOS en MAYÚSCULAS" que
`edecan_api.config.Settings`), así que cualquier handler que lea
`deps.settings.<CAMPO>` (con o sin `getattr`) sigue funcionando igual que en
dev/prod, y además ve los campos v3 que este runner necesita.

`build_local_deps` reimplementa `_build_embedder`/`_build_llm_router`/
`_build_vault_factory` en vez de importar los de `edecan_worker.deps`: son
símbolos privados (prefijo `_`) de un paquete fuera del alcance de este
paquete de trabajo (`apps/worker/**` está en la lista PROHIBIDA) — la lógica
es lo bastante chica como para no justificar depender de la forma interna de
otro módulo. `sqs=None` a propósito (`ARCHITECTURE.md` §12f:
`QUEUE_PROVIDER=db` es la opción recomendada PARA NO depender de SQS/
LocalStack en la máquina del cliente): ningún handler de v1/v2/v3 llama a
`deps.sqs` directo hoy (solo `edecan_worker.main`, que este runner no usa) —
si algún handler nuevo lo necesitara alguna vez, fallaría con un
`AttributeError` claro en vez de pegarle silenciosamente a un endpoint que
no habla SQS.

## Selección de filas: `FOR UPDATE SKIP LOCKED`

Cada `POLL_INTERVAL_SECONDS` (2s): `SELECT ... FROM jobs WHERE status =
'queued' ... FOR UPDATE SKIP LOCKED LIMIT 5`, y esas filas se marcan
`running` en la MISMA transacción — así ninguna fila puede ser tomada dos
veces aunque el procesamiento de un job tarde más que el intervalo de poll.

`payload["_not_before"]` (lo guarda `edecan_core.queue._enqueue_db` cuando
`enqueue(..., delay_seconds=N)`) se filtra en Python, no en el `WHERE` SQL —
comparar un string ISO dentro de `jsonb` a mano en SQL es más frágil que
traer como mucho `BATCH_SIZE` filas de más y descartar las que no tocan
todavía (se quedan `queued`, se reevalúan en el próximo tick).

## Reintentos y scheduler local

Éxito → `status='done'`. Excepción → si `attempts` (valor PRE-incremento,
mismo criterio que `env.attempt` en `edecan_worker.main._handle_message`,
ARCHITECTURE.md §10.11) es `< MAX_ATTEMPTS` → reintenta: `attempts + 1`,
`status='queued'` con backoff `min(900, 2**attempt*30)`s guardado en
`payload['_not_before']` (mismo cálculo que
`edecan_worker.main.compute_backoff_seconds`); si no, `status='error'`
(estado terminal — no hay una tabla DLQ separada en modo local, `status=
'error'` ES el equivalente).

Cada `SCHEDULER_INTERVAL_SECONDS` (30s) se encolan `send_reminder_scan` y
`automation_scan` (mismo rol que `edecan_worker.scheduler` en dev, pero con
una sola cadencia — este runner no tiene el matiz de doble cadencia de ese
módulo, ver su docstring, porque acá ambos tipos son igual de baratos de
encolar cada 30s).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0
SCHEDULER_INTERVAL_SECONDS = 30.0
BATCH_SIZE = 5
MAX_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 900
BASE_BACKOFF_SECONDS = 30

_NOT_BEFORE_KEY = "_not_before"
SCHEDULED_JOB_TYPES: tuple[str, ...] = ("send_reminder_scan", "automation_scan")


def compute_backoff_seconds(attempt: int) -> int:
    """Mismo cálculo que `edecan_worker.main.compute_backoff_seconds`
    (ARCHITECTURE.md §10.11): `min(900, 2**attempt * 30)`."""
    return min(MAX_BACKOFF_SECONDS, (2**attempt) * BASE_BACKOFF_SECONDS)


def _to_asyncpg_dsn(database_url: str) -> str:
    """`postgresql+asyncpg://...` (SQLAlchemy) -> `postgresql://...`
    (asyncpg puro) -- mismo helper que `edecan_local.pg`/`edecan_core.queue`
    (duplicado a propósito, es un one-liner: no vale la pena una dependencia
    cruzada solo por esto)."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _job_ctx(*, job_id: Any, job_type: str, tenant_id: Any, attempt: int) -> str:
    return f"job_id={job_id} type={job_type} tenant_id={tenant_id} attempt={attempt}"


def _env_ctx(env: Any) -> str:
    """`_job_ctx` a partir de un `JobEnvelope` ya armado (mismo criterio que
    `edecan_worker.main._job_ctx`, que recibe el envelope directo)."""
    return _job_ctx(
        job_id=env.job_id, job_type=env.type, tenant_id=env.tenant_id, attempt=env.attempt
    )


# ---------------------------------------------------------------------------
# `edecan_worker.deps.Deps` duck-typed para este runner (ver docstring del
# módulo sobre por qué no se reutiliza `edecan_worker.deps.build_deps`).
# ---------------------------------------------------------------------------

# Placeholders públicos de `.env.example` (mismo criterio que
# `edecan_worker.deps`/`edecan_api.routers.conversations`, reimplementado
# acá sin importar esos símbolos privados de un paquete fuera de alcance).
_OPENAI_COMPAT_API_KEY_PLACEHOLDER = "TU_OPENAI_COMPAT_API_KEY_AQUI"
_EMBEDDINGS_MODEL_PLACEHOLDER = "TU_EMBEDDINGS_MODEL_AQUI"


def _has_real_embeddings_provider(settings: Any) -> bool:
    return bool(
        getattr(settings, "OPENAI_COMPAT_BASE_URL", None)
        and getattr(settings, "OPENAI_COMPAT_API_KEY", None)
        and getattr(settings, "OPENAI_COMPAT_API_KEY", None) != _OPENAI_COMPAT_API_KEY_PLACEHOLDER
        and getattr(settings, "EMBEDDINGS_MODEL", None)
        and getattr(settings, "EMBEDDINGS_MODEL", None) != _EMBEDDINGS_MODEL_PLACEHOLDER
    )


def _build_embedder(settings: Any) -> Any:
    if _has_real_embeddings_provider(settings):
        from edecan_core.memory import OpenAICompatEmbedder

        return OpenAICompatEmbedder(
            base_url=settings.OPENAI_COMPAT_BASE_URL,
            api_key=settings.OPENAI_COMPAT_API_KEY,
            model=settings.EMBEDDINGS_MODEL,
        )

    from edecan_core.memory import HashEmbedder

    return HashEmbedder(dim=getattr(settings, "EMBEDDINGS_DIM", 1536))


def _build_llm_router(settings: Any) -> Any:
    from edecan_llm.router import LLMRouter

    return LLMRouter(settings)


def _build_vault_factory(settings: Any) -> Callable[[AsyncSession], Any]:
    from edecan_db.settings import get_settings as get_db_settings
    from edecan_db.vault import TokenVault, get_key_provider

    key_provider = get_key_provider(get_db_settings())

    def vault_factory(session: AsyncSession) -> Any:
        return TokenVault(session, key_provider)

    return vault_factory


def build_local_deps(settings: Any) -> Any:
    """`AsyncContextManager[edecan_worker.deps.Deps]` -- arma `Deps` "de
    verdad" para este runner, mismo espíritu que `edecan_worker.deps.
    build_deps` pero SOLO con un cliente S3 (`sqs=None`, ver docstring del
    módulo), apuntando al `edecan_local.objectstore` local
    (`settings.AWS_ENDPOINT_URL`/`AWS_REGION`, ya fijados por
    `edecan_local.runtime` antes de llamar acá). Las credenciales
    (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`) NO se pasan explícitas:
    igual que el resto del repo (`apps/api/edecan_api/routers/files.py`,
    `edecan_worker.deps.build_deps`), `aioboto3` las toma de las variables de
    entorno mediante su cadena de credenciales estándar.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm():
        import aioboto3
        from edecan_worker.deps import Deps

        session_factory = _session_factory()

        boto_session = aioboto3.Session()
        async with boto_session.client(
            "s3",
            region_name=getattr(settings, "AWS_REGION", None) or "us-east-1",
            endpoint_url=getattr(settings, "AWS_ENDPOINT_URL", None),
        ) as s3:
            logger.info(
                "edecan_local.worker_loop: cliente S3 listo (endpoint_url=%s).",
                getattr(settings, "AWS_ENDPOINT_URL", None),
            )
            yield Deps(
                settings=settings,
                session_factory=session_factory,
                s3=s3,
                sqs=None,
                embedder=_build_embedder(settings),
                llm_router=_build_llm_router(settings),
                vault=_build_vault_factory(settings),
            )

    return _cm()


def _session_factory() -> Any:
    from edecan_db.session import get_session

    return get_session


# ---------------------------------------------------------------------------
# Cola sobre `jobs` (asyncpg directo) — fetch + claim + mark
# ---------------------------------------------------------------------------


def _decode_payload(raw_payload: Any) -> dict[str, Any]:
    """`row["payload"]` llega como `str` (asyncpg NO decodifica `jsonb` a
    `dict` automáticamente salvo que se registre un type codec, cosa que
    este módulo no hace) -- `json.loads` explícito. Defensivo con
    `dict`/`None` también por si algún día se registra un codec en el pool
    (dejaría de ser `str`)."""
    if isinstance(raw_payload, str):
        return json.loads(raw_payload)
    return dict(raw_payload or {})


def _is_ready(payload: dict[str, Any], *, now: datetime) -> bool:
    not_before = payload.get(_NOT_BEFORE_KEY)
    if not not_before:
        return True
    try:
        return datetime.fromisoformat(not_before) <= now
    except (TypeError, ValueError):
        logger.warning("job con _not_before ilegible (%r) -- se procesa igual.", not_before)
        return True


async def _fetch_and_claim_batch(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """`SELECT ... FOR UPDATE SKIP LOCKED` de hasta `BATCH_SIZE` filas
    `status='queued'`, filtra las que ya "tocan" (`_is_ready`) y las marca
    `running` en la MISMA transacción -- ver docstring del módulo."""
    now = datetime.now(UTC)
    async with pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            "SELECT id, tenant_id, type, payload, attempts FROM jobs "
            "WHERE status = 'queued' ORDER BY created_at LIMIT $1 FOR UPDATE SKIP LOCKED",
            BATCH_SIZE,
        )
        ready: list[dict[str, Any]] = []
        for row in rows:
            payload = _decode_payload(row["payload"])
            if not _is_ready(payload, now=now):
                continue
            ready.append(
                {
                    "id": row["id"],
                    "tenant_id": row["tenant_id"],
                    "type": row["type"],
                    "payload": payload,
                    "attempts": row["attempts"],
                }
            )

        if ready:
            ids = [job["id"] for job in ready]
            await conn.execute("UPDATE jobs SET status = 'running' WHERE id = ANY($1::uuid[])", ids)
        return ready


async def _mark_done(pool: asyncpg.Pool, job_id: UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute("UPDATE jobs SET status = 'done' WHERE id = $1", job_id)


async def _mark_missing_handler(pool: asyncpg.Pool, job_id: UUID, attempts: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET status = 'error', attempts = $2, last_error = $3 WHERE id = $1",
            job_id,
            attempts,
            "sin handler registrado para este job_type (edecan_worker.handlers.HANDLERS)",
        )


async def _propagate_terminal_failure_to_mission(
    conn: asyncpg.Connection, job_type: str, payload: dict[str, Any], error_message: str
) -> None:
    """Un job `run_mission` que agota sus reintentos dejaba huérfana la
    misión asociada: `run_mission.handle` (apps/worker) solo persiste la
    transición inicial de `agent_missions` DESPUÉS de resolver el LLM
    bring-your-own del tenant (`deps.llm_router_for`, ver docstring de ese
    módulo) -- un fallo ANTES de ese punto (típicamente
    `TenantLLMNotConnectedError`, tenant sin LLM conectado) nunca tocaba la
    fila de la misión. El `job` sí quedaba `status='error'` con
    `last_error`, pero eso es invisible para `/app/misiones` (que solo lee
    `agent_missions`): la UI se quedaba mostrando "Planificando" con
    "Todavía no hay pasos planificados" para siempre, sin ningún error
    (HOTFIXES_PENDIENTES.md).

    Este es el único lugar que sabe con certeza que YA NO habrá más
    reintentos (la rama `else` de `_mark_failure`, agotados los
    `MAX_ATTEMPTS`) -- el punto correcto para cerrar la misión con el error
    real en vez de dejarla huérfana. `status NOT IN (...)` evita pisar una
    misión que de alguna forma ya llegó a un estado terminal por otro
    camino mientras este job todavía estaba en su ventana de reintento."""
    if job_type != "run_mission":
        return
    mission_id = payload.get("mission_id")
    if not mission_id:
        return
    await conn.execute(
        "UPDATE agent_missions SET status = 'error', error = $2 "
        "WHERE id = $1 AND status NOT IN ('done', 'error', 'cancelled')",
        UUID(str(mission_id)),
        error_message,
    )


async def _mark_failure(
    pool: asyncpg.Pool,
    job_id: UUID,
    job_type: str,
    payload: dict[str, Any],
    attempts: int,
    error_message: str,
) -> None:
    """`attempts < MAX_ATTEMPTS` (valor PRE-incremento, mismo criterio que
    `env.attempt` en `edecan_worker.main._handle_message`, ARCHITECTURE.md
    §10.11) -> `queued` con `attempts + 1` y backoff en
    `payload['_not_before']`; si no, `error` (terminal, ver docstring del
    módulo) -- y, si el job era `run_mission`, también cierra la misión
    asociada (`_propagate_terminal_failure_to_mission`)."""
    next_attempts = attempts + 1
    error_message = error_message[:2000]  # `last_error` es Text, pero sin cota no vale la pena

    async with pool.acquire() as conn:
        if attempts < MAX_ATTEMPTS:
            delay = compute_backoff_seconds(attempts)
            not_before = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
            await conn.execute(
                "UPDATE jobs SET status = 'queued', attempts = $2, last_error = $3, "
                "payload = payload || $4::jsonb WHERE id = $1",
                job_id,
                next_attempts,
                error_message,
                json.dumps({_NOT_BEFORE_KEY: not_before}),
            )
            logger.warning(
                "worker_loop: job_id=%s reintentado (attempt=%s, backoff=%ss)",
                job_id,
                next_attempts,
                delay,
            )
        else:
            await conn.execute(
                "UPDATE jobs SET status = 'error', attempts = $2, last_error = $3 WHERE id = $1",
                job_id,
                next_attempts,
                error_message,
            )
            await _propagate_terminal_failure_to_mission(conn, job_type, payload, error_message)
            logger.error(
                "worker_loop: job_id=%s agotó %s intentos -- status='error'.",
                job_id,
                MAX_ATTEMPTS,
            )


async def _process_job(pool: asyncpg.Pool, deps: Any, job: dict[str, Any]) -> None:
    from edecan_schemas import JobEnvelope
    from edecan_worker.handlers import HANDLERS

    job_id = job["id"]
    job_type = job["type"]
    tenant_id = job["tenant_id"]
    attempts = job["attempts"]
    payload = {k: v for k, v in job["payload"].items() if not k.startswith("_")}

    handler = HANDLERS.get(job_type)
    if handler is None:
        logger.error(
            "worker_loop: %s sin handler registrado -- status='error'.",
            _job_ctx(job_id=job_id, job_type=job_type, tenant_id=tenant_id, attempt=attempts),
        )
        await _mark_missing_handler(pool, job_id, attempts)
        return

    env = JobEnvelope(
        job_id=job_id, tenant_id=tenant_id, type=job_type, payload=payload, attempt=attempts
    )
    logger.info("worker_loop: procesando %s", _env_ctx(env))
    try:
        await handler(env, deps)
    except Exception as exc:
        logger.exception("worker_loop: fallo procesando %s", _env_ctx(env))
        await _mark_failure(
            pool, job_id, job_type, payload, attempts, f"{type(exc).__name__}: {exc}"
        )
        return

    await _mark_done(pool, job_id)
    logger.info("worker_loop: completado %s", _env_ctx(env))


# ---------------------------------------------------------------------------
# Scheduler local (30s): send_reminder_scan + automation_scan
# ---------------------------------------------------------------------------


async def _run_scheduler_tick(settings: Any) -> None:
    from edecan_core.queue import enqueue

    for job_type in SCHEDULED_JOB_TYPES:
        try:
            job_id = await enqueue(settings, job_type, {}, None)
            logger.info("worker_loop: %s encolado (scheduler local) job_id=%s", job_type, job_id)
        except Exception:
            logger.exception("worker_loop: fallo al encolar %s (scheduler local)", job_type)


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------


async def run_forever(deps: Any, *, stop_event: asyncio.Event | None = None) -> None:
    """Loop principal: cada `POLL_INTERVAL_SECONDS` procesa hasta
    `BATCH_SIZE` jobs `queued`; cada `SCHEDULER_INTERVAL_SECONDS` encola los
    jobs de sistema periódicos. Corre hasta que `stop_event` se marque
    (`edecan_local.runtime` lo hace en SIGTERM/SIGINT) -- cierra el pool de
    `asyncpg` siempre al salir, incluso si el loop revienta.
    """
    import asyncpg

    stop_event = stop_event or asyncio.Event()
    dsn = _to_asyncpg_dsn(deps.settings.DATABASE_URL)
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=max(BATCH_SIZE, 2))
    logger.info(
        "edecan_local.worker_loop escuchando la tabla 'jobs' (poll=%ss, scheduler=%ss).",
        POLL_INTERVAL_SECONDS,
        SCHEDULER_INTERVAL_SECONDS,
    )

    # Arranca en "ahora" (no en 0), mismo criterio que
    # `edecan_worker.scheduler.run_forever`: el primer tick del loop no debe
    # disparar el scheduler de inmediato.
    ultimo_tick_scheduler = time.monotonic()
    try:
        while not stop_event.is_set():
            try:
                jobs = await _fetch_and_claim_batch(pool)
                for job in jobs:
                    await _process_job(pool, deps, job)
            except Exception:
                logger.exception("worker_loop: fallo inesperado en el ciclo de poll")

            ahora = time.monotonic()
            if ahora - ultimo_tick_scheduler >= SCHEDULER_INTERVAL_SECONDS:
                ultimo_tick_scheduler = ahora
                try:
                    await _run_scheduler_tick(deps.settings)
                except Exception:
                    logger.exception("worker_loop: fallo inesperado en el tick del scheduler local")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            except TimeoutError:
                pass
    finally:
        await pool.close()
        logger.info("edecan_local.worker_loop detenido.")
