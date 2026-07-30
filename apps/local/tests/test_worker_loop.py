"""`edecan_local.worker_loop` — consumidor in-process de la tabla `jobs`
(ARCHITECTURE.md §12f, WP-V3-05).

`FakePool`/`FakeConnection` entienden (por substring del SQL + posición de
los params) las pocas sentencias que emite `worker_loop.py` — mismo espíritu
que `FakeSession` en `apps/api/tests/test_missions_router.py` — nunca
Postgres real (ARCHITECTURE.md §0.4).

## Excepción documentada a "los tests no importan paquetes hermanos"

Este módulo SÍ importa `edecan_worker.handlers` (el dict real `HANDLERS` que
`worker_loop._process_job` despacha) — misma excepción, acotada, que ya toma
`apps/api/tests/test_repo_sql_integration.py` (ver su docstring): `edecan-
worker` no es un paquete "hermano construido en paralelo que puede no
existir todavía" desde la perspectiva de `edecan-local` — es una dependencia
DURA y declarada (`apps/local/pyproject.toml`), la pieza que este runner
literalmente empaqueta (ARCHITECTURE.md §12f). No hay forma de probar que
`worker_loop` despacha de verdad al `HANDLERS` real sin referenciar ese
mismo dict. Todo lo demás (`deps`, sesión, S3, vault, cola) se mantiene con
dobles locales.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import edecan_worker.handlers as handlers_module
import pytest
from edecan_local import worker_loop

# ---------------------------------------------------------------------------
# Fakes de asyncpg (Pool/Connection) sobre una tabla `jobs` en memoria.
# ---------------------------------------------------------------------------


class _FakeTransaction:
    async def __aenter__(self) -> _FakeTransaction:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakeConnection:
    def __init__(self, jobs: dict[uuid.UUID, dict[str, Any]]) -> None:
        self._jobs = jobs
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.mission_updates: list[tuple[Any, ...]] = []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        assert "FOR UPDATE SKIP LOCKED" in query
        limit = args[0]
        queued = sorted(
            (j for j in self._jobs.values() if j["status"] == "queued"),
            key=lambda j: j["created_at"],
        )
        # `payload` viaja como `str` (JSON) igual que asyncpg de verdad
        # (ver `worker_loop._decode_payload`).
        return [
            {
                "id": j["id"],
                "tenant_id": j["tenant_id"],
                "type": j["type"],
                "payload": json.dumps(j["payload"]),
                "attempts": j["attempts"],
            }
            for j in queued[:limit]
        ]

    async def execute(self, query: str, *args: Any) -> None:
        self.executed.append((query, args))
        if "SET status = 'running'" in query:
            for job_id in args[0]:
                self._jobs[job_id]["status"] = "running"
        elif "SET status = 'done'" in query:
            (job_id,) = args
            self._jobs[job_id]["status"] = "done"
        elif "SET status = 'queued'" in query:
            job_id, attempts, last_error, payload_patch_json = args
            job = self._jobs[job_id]
            job["status"] = "queued"
            job["attempts"] = attempts
            job["last_error"] = last_error
            job["payload"] = {**job["payload"], **json.loads(payload_patch_json)}
        elif "UPDATE jobs SET status = 'error'" in query:
            job_id, attempts, last_error = args
            job = self._jobs[job_id]
            job["status"] = "error"
            job["attempts"] = attempts
            job["last_error"] = last_error
        elif "UPDATE agent_missions" in query:
            self.mission_updates.append(args)
        else:  # pragma: no cover - guardrail, no debería dispararse nunca
            raise AssertionError(f"query inesperada en FakeConnection.execute: {query!r}")


class _FakeAcquire:
    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConnection:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakePool:
    def __init__(self, jobs: dict[uuid.UUID, dict[str, Any]]) -> None:
        self.conn = FakeConnection(jobs)
        self.closed = False

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)

    async def close(self) -> None:
        self.closed = True


def _job_row(
    *,
    job_type: str = "ingest_file",
    tenant_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    attempts: int = 0,
    status: str = "queued",
    created_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id if tenant_id is not None else uuid.uuid4(),
        "type": job_type,
        "payload": payload or {},
        "attempts": attempts,
        "status": status,
        "last_error": None,
        "created_at": created_at or datetime.now(UTC),
    }


# ---------------------------------------------------------------------------
# _fetch_and_claim_batch
# ---------------------------------------------------------------------------


async def test_fetch_and_claim_batch_toma_hasta_batch_size_y_marca_running() -> None:
    jobs = {j["id"]: j for j in (_job_row() for _ in range(worker_loop.BATCH_SIZE + 2))}
    pool = FakePool(jobs)

    claimed = await worker_loop._fetch_and_claim_batch(pool)

    assert len(claimed) == worker_loop.BATCH_SIZE
    claimed_ids = {job["id"] for job in claimed}
    for job_id in claimed_ids:
        assert jobs[job_id]["status"] == "running"
    sin_reclamar = [j for j in jobs.values() if j["id"] not in claimed_ids]
    assert all(j["status"] == "queued" for j in sin_reclamar)


async def test_fetch_and_claim_batch_respeta_orden_created_at() -> None:
    older = _job_row(created_at=datetime(2020, 1, 1, tzinfo=UTC))
    newer = _job_row(created_at=datetime(2020, 1, 2, tzinfo=UTC))
    jobs = {j["id"]: j for j in (newer, older)}
    pool = FakePool(jobs)

    claimed = await worker_loop._fetch_and_claim_batch(pool)

    assert [job["id"] for job in claimed] == [older["id"], newer["id"]]


async def test_fetch_and_claim_batch_excluye_not_before_futuro() -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    delayed = _job_row(payload={"_not_before": future})
    ready = _job_row()
    jobs = {j["id"]: j for j in (delayed, ready)}
    pool = FakePool(jobs)

    claimed = await worker_loop._fetch_and_claim_batch(pool)

    assert [job["id"] for job in claimed] == [ready["id"]]
    assert jobs[delayed["id"]]["status"] == "queued"  # nunca se tocó


async def test_fetch_and_claim_batch_incluye_not_before_pasado() -> None:
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    job = _job_row(payload={"_not_before": past})
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    claimed = await worker_loop._fetch_and_claim_batch(pool)

    assert len(claimed) == 1
    assert jobs[job["id"]]["status"] == "running"


async def test_fetch_and_claim_batch_not_before_ilegible_se_procesa_igual() -> None:
    job = _job_row(payload={"_not_before": "no-es-una-fecha"})
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    claimed = await worker_loop._fetch_and_claim_batch(pool)

    assert len(claimed) == 1


async def test_fetch_and_claim_batch_ignora_jobs_no_queued() -> None:
    running = _job_row(status="running")
    done = _job_row(status="done")
    error = _job_row(status="error")
    jobs = {j["id"]: j for j in (running, done, error)}
    pool = FakePool(jobs)

    claimed = await worker_loop._fetch_and_claim_batch(pool)

    assert claimed == []


# ---------------------------------------------------------------------------
# _mark_done / _mark_missing_handler / _mark_failure
# ---------------------------------------------------------------------------


async def test_mark_done() -> None:
    job = _job_row()
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    await worker_loop._mark_done(pool, job["id"])

    assert jobs[job["id"]]["status"] == "done"


async def test_mark_missing_handler() -> None:
    job = _job_row(attempts=2)
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    await worker_loop._mark_missing_handler(pool, job["id"], 2)

    updated = jobs[job["id"]]
    assert updated["status"] == "error"
    assert updated["attempts"] == 2
    assert "sin handler" in updated["last_error"]


async def test_mark_failure_reintenta_bajo_max_attempts_con_backoff() -> None:
    job = _job_row(attempts=1)
    jobs = {job["id"]: job}
    pool = FakePool(jobs)
    antes = datetime.now(UTC)

    await worker_loop._mark_failure(pool, job["id"], job["type"], job["payload"], 1, "boom")

    updated = jobs[job["id"]]
    assert updated["status"] == "queued"
    assert updated["attempts"] == 2
    assert updated["last_error"] == "boom"
    not_before = datetime.fromisoformat(updated["payload"]["_not_before"])
    esperado_delay = worker_loop.compute_backoff_seconds(1)
    assert not_before > antes
    assert (not_before - antes).total_seconds() <= esperado_delay + 5


async def test_mark_failure_agota_intentos_y_marca_error_terminal() -> None:
    # Mismo umbral que `edecan_worker.main` (attempt PRE-incremento ==
    # MAX_ATTEMPTS agota los reintentos, ver test_main_backoff.py
    # ::test_al_agotar_intentos_no_borra_para_que_el_redrive_lo_mande_a_dlq).
    attempts_previos = worker_loop.MAX_ATTEMPTS
    job = _job_row(attempts=attempts_previos)
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    await worker_loop._mark_failure(
        pool, job["id"], job["type"], job["payload"], attempts_previos, "boom final"
    )

    updated = jobs[job["id"]]
    assert updated["status"] == "error"
    assert updated["attempts"] == worker_loop.MAX_ATTEMPTS + 1
    assert "_not_before" not in updated["payload"]


async def test_mark_failure_reintenta_en_el_ultimo_intento_permitido() -> None:
    # Paridad con `edecan_worker.main._handle_message`: `attempts ==
    # MAX_ATTEMPTS - 1` (la 5ta ejecución, 0-indexada) todavía reintenta —
    # el job se da por vencido recién en la 6ta ejecución (attempts ==
    # MAX_ATTEMPTS), no antes.
    attempts_previos = worker_loop.MAX_ATTEMPTS - 1
    job = _job_row(attempts=attempts_previos)
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    await worker_loop._mark_failure(
        pool, job["id"], job["type"], job["payload"], attempts_previos, "boom"
    )

    updated = jobs[job["id"]]
    assert updated["status"] == "queued"
    assert updated["attempts"] == worker_loop.MAX_ATTEMPTS
    assert "_not_before" in updated["payload"]


async def test_mark_failure_run_mission_agotado_cierra_la_mision_con_error() -> None:
    mission_id = uuid.uuid4()
    attempts_previos = worker_loop.MAX_ATTEMPTS
    job = _job_row(
        job_type="run_mission", payload={"mission_id": str(mission_id)}, attempts=attempts_previos
    )
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    await worker_loop._mark_failure(
        pool,
        job["id"],
        job["type"],
        job["payload"],
        attempts_previos,
        "TenantLLMNotConnectedError: sin LLM",
    )

    assert pool.conn.mission_updates == [
        (mission_id, "TenantLLMNotConnectedError: sin LLM")
    ]


async def test_mark_failure_run_mission_reintento_no_toca_la_mision_todavia() -> None:
    mission_id = uuid.uuid4()
    job = _job_row(job_type="run_mission", payload={"mission_id": str(mission_id)}, attempts=1)
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    await worker_loop._mark_failure(pool, job["id"], job["type"], job["payload"], 1, "boom")

    assert pool.conn.mission_updates == []  # todavía dentro de la ventana de reintento


async def test_mark_failure_otro_job_type_no_toca_agent_missions() -> None:
    job = _job_row(job_type="ingest_file", attempts=worker_loop.MAX_ATTEMPTS)
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    await worker_loop._mark_failure(
        pool, job["id"], job["type"], job["payload"], worker_loop.MAX_ATTEMPTS, "boom"
    )

    assert pool.conn.mission_updates == []


async def test_mark_failure_trunca_mensajes_de_error_muy_largos() -> None:
    job = _job_row()
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    await worker_loop._mark_failure(pool, job["id"], job["type"], job["payload"], 0, "x" * 5000)

    assert len(jobs[job["id"]]["last_error"]) == 2000


# ---------------------------------------------------------------------------
# _process_job — dispatch a edecan_worker.handlers.HANDLERS
# ---------------------------------------------------------------------------


async def test_process_job_exito_llama_al_handler_y_marca_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, Any]] = []

    async def fake_handler(env: Any, deps: Any) -> None:
        calls.append((env, deps))

    monkeypatch.setitem(handlers_module.HANDLERS, "ingest_file", fake_handler)

    job = _job_row(job_type="ingest_file", payload={"file_id": "f1"})
    jobs = {job["id"]: job}
    pool = FakePool(jobs)
    deps = SimpleNamespace(settings=SimpleNamespace())

    await worker_loop._process_job(pool, deps, {**job, "payload": {"file_id": "f1"}})

    assert len(calls) == 1
    env, received_deps = calls[0]
    assert env.job_id == job["id"]
    assert env.type == "ingest_file"
    assert env.payload == {"file_id": "f1"}
    assert env.attempt == 0
    assert received_deps is deps
    assert jobs[job["id"]]["status"] == "done"


async def test_process_job_pela_claves_internas_del_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recibido: dict[str, Any] = {}

    async def fake_handler(env: Any, deps: Any) -> None:
        recibido["payload"] = env.payload

    monkeypatch.setitem(handlers_module.HANDLERS, "ingest_file", fake_handler)
    job = _job_row(job_type="ingest_file", payload={"file_id": "f1", "_not_before": "x"})
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    await worker_loop._process_job(
        pool, SimpleNamespace(), {**job, "payload": {"file_id": "f1", "_not_before": "x"}}
    )

    assert recibido["payload"] == {"file_id": "f1"}


async def test_process_job_excepcion_marca_para_reintento(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_handler(env: Any, deps: Any) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setitem(handlers_module.HANDLERS, "ingest_file", fake_handler)
    job = _job_row(job_type="ingest_file", attempts=0)
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    await worker_loop._process_job(pool, SimpleNamespace(), dict(job))

    updated = jobs[job["id"]]
    assert updated["status"] == "queued"
    assert updated["attempts"] == 1
    assert "kaboom" in updated["last_error"]


async def test_process_job_sin_handler_registrado_marca_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(handlers_module.HANDLERS, "sync_connector", raising=False)
    job = _job_row(job_type="sync_connector")
    jobs = {job["id"]: job}
    pool = FakePool(jobs)

    await worker_loop._process_job(pool, SimpleNamespace(), dict(job))

    assert jobs[job["id"]]["status"] == "error"


# ---------------------------------------------------------------------------
# _run_scheduler_tick
# ---------------------------------------------------------------------------


async def test_run_scheduler_tick_encola_los_dos_tipos_de_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoladas: list[str] = []

    async def fake_enqueue(settings: Any, job_type: str, payload: dict, tenant_id: Any) -> Any:
        encoladas.append(job_type)
        return uuid.uuid4()

    monkeypatch.setitem(sys.modules, "edecan_core.queue", SimpleNamespace(enqueue=fake_enqueue))

    await worker_loop._run_scheduler_tick(SimpleNamespace())

    assert encoladas == list(worker_loop.SCHEDULED_JOB_TYPES)


async def test_run_scheduler_tick_aisla_fallos_por_tipo(monkeypatch: pytest.MonkeyPatch) -> None:
    encoladas: list[str] = []

    async def fake_enqueue(settings: Any, job_type: str, payload: dict, tenant_id: Any) -> Any:
        if job_type == "send_reminder_scan":
            raise RuntimeError("boom")
        encoladas.append(job_type)
        return uuid.uuid4()

    monkeypatch.setitem(sys.modules, "edecan_core.queue", SimpleNamespace(enqueue=fake_enqueue))

    await worker_loop._run_scheduler_tick(SimpleNamespace())  # no debe lanzar

    assert encoladas == ["automation_scan"]


# ---------------------------------------------------------------------------
# run_forever — loop completo con asyncpg fakeado
# ---------------------------------------------------------------------------


async def test_run_forever_procesa_un_job_y_se_detiene_via_stop_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job_row(job_type="ingest_file")
    jobs = {job["id"]: job}
    pool_holder: dict[str, Any] = {}

    async def fake_create_pool(dsn: str, **kwargs: Any) -> FakePool:
        pool = FakePool(jobs)
        pool_holder["pool"] = pool
        pool_holder["dsn"] = dsn
        return pool

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(create_pool=fake_create_pool))

    stop_event = asyncio.Event()
    calls = 0

    async def fake_handler(env: Any, deps: Any) -> None:
        nonlocal calls
        calls += 1
        stop_event.set()  # determinista: para el loop justo tras procesar el job

    monkeypatch.setitem(handlers_module.HANDLERS, "ingest_file", fake_handler)

    deps = SimpleNamespace(
        settings=SimpleNamespace(DATABASE_URL="postgresql+asyncpg://u:p@h:5432/d")
    )

    await asyncio.wait_for(worker_loop.run_forever(deps, stop_event=stop_event), timeout=5)

    assert calls == 1
    assert jobs[job["id"]]["status"] == "done"
    assert pool_holder["dsn"] == "postgresql://u:p@h:5432/d"
    assert pool_holder["pool"].closed is True


async def test_run_forever_cierra_el_pool_incluso_si_el_ciclo_revienta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool_holder: dict[str, Any] = {}

    class _BrokenPool(FakePool):
        def acquire(self) -> Any:
            raise RuntimeError("Postgres caído")

    async def fake_create_pool(dsn: str, **kwargs: Any) -> _BrokenPool:
        pool = _BrokenPool({})
        pool_holder["pool"] = pool
        return pool

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(create_pool=fake_create_pool))

    stop_event = asyncio.Event()

    async def _detener_tras_un_ciclo() -> None:
        await asyncio.sleep(0)
        stop_event.set()

    deps = SimpleNamespace(
        settings=SimpleNamespace(DATABASE_URL="postgresql+asyncpg://u:p@h:5432/d")
    )
    stopper = asyncio.create_task(_detener_tras_un_ciclo())
    await asyncio.wait_for(worker_loop.run_forever(deps, stop_event=stop_event), timeout=5)
    await stopper

    assert pool_holder["pool"].closed is True


# ---------------------------------------------------------------------------
# build_local_deps
# ---------------------------------------------------------------------------


@pytest.fixture
def _local_master_key_valida(monkeypatch: pytest.MonkeyPatch):
    """`_build_vault_factory` construye un `LocalKeyProvider` (Fernet) DE
    VERDAD -- necesita una clave Fernet válida en el entorno, no el
    placeholder público. Limpia el cache de `edecan_db.settings.get_settings`
    (singleton `lru_cache`) antes y después para no filtrar entre tests."""
    import edecan_db.settings as db_settings_module
    from cryptography.fernet import Fernet

    monkeypatch.setenv("LOCAL_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    db_settings_module.get_settings.cache_clear()
    yield
    db_settings_module.get_settings.cache_clear()


async def test_build_local_deps_arma_deps_con_sqs_none(
    monkeypatch: pytest.MonkeyPatch, _local_master_key_valida: None
) -> None:
    client_calls: list[tuple[str, dict[str, Any]]] = []

    class _FakeS3Client:
        async def __aenter__(self) -> _FakeS3Client:
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    class _FakeBotoSession:
        def client(self, service_name: str, **kwargs: Any) -> _FakeS3Client:
            client_calls.append((service_name, kwargs))
            return _FakeS3Client()

    monkeypatch.setitem(
        sys.modules, "aioboto3", SimpleNamespace(Session=lambda: _FakeBotoSession())
    )

    settings = SimpleNamespace(
        AWS_REGION="us-east-1",
        AWS_ENDPOINT_URL="http://127.0.0.1:18770",
        ANTHROPIC_API_KEY=None,
        ANTHROPIC_MODEL_PRINCIPAL="claude-sonnet-4-5",
        ANTHROPIC_MODEL_RAPIDO="claude-haiku-4-5",
        OPENAI_COMPAT_BASE_URL=None,
        OPENAI_COMPAT_API_KEY=None,
        EMBEDDINGS_MODEL=None,
        EMBEDDINGS_DIM=1536,
    )

    async with worker_loop.build_local_deps(settings) as deps:
        assert deps.sqs is None
        assert deps.s3 is not None
        assert deps.settings is settings
        assert deps.session_factory is not None
        assert deps.llm_router is not None
        assert deps.vault is not None

    assert client_calls == [
        ("s3", {"region_name": "us-east-1", "endpoint_url": "http://127.0.0.1:18770"})
    ]


def test_has_real_embeddings_provider_falso_con_placeholders() -> None:
    settings = SimpleNamespace(
        OPENAI_COMPAT_BASE_URL="https://api.openai.com/v1",
        OPENAI_COMPAT_API_KEY=worker_loop._OPENAI_COMPAT_API_KEY_PLACEHOLDER,
        EMBEDDINGS_MODEL="text-embedding-3-small",
    )
    assert worker_loop._has_real_embeddings_provider(settings) is False


def test_has_real_embeddings_provider_true_con_config_real() -> None:
    settings = SimpleNamespace(
        OPENAI_COMPAT_BASE_URL="https://api.openai.com/v1",
        OPENAI_COMPAT_API_KEY="sk-real",
        EMBEDDINGS_MODEL="text-embedding-3-small",
    )
    assert worker_loop._has_real_embeddings_provider(settings) is True
