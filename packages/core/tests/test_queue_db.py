"""`enqueue` con `QUEUE_PROVIDER="db"` — INSERT directo en la tabla `jobs`
en vez de SQS (ARCHITECTURE.md §10.7, §10.11, §12g; `edecan_core.queue._enqueue_db`,
pensado para `edecan_local`, WP-V3-05).

Mismo criterio que `test_queue.py`: sin red real (ARCHITECTURE.md §0.4). Como
`_enqueue_db` hace `import asyncpg` DENTRO de la función (import perezoso, ver
su docstring), el doble de prueba se instala con
`monkeypatch.setitem(sys.modules, "asyncpg", ...)` — así el `import asyncpg`
de dentro de `_enqueue_db` resuelve al fake sin importar si el paquete real
`asyncpg` está instalado en este entorno.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import edecan_core.queue as queue_module
import pytest
from edecan_core.queue import enqueue


class _FakeAsyncpgConnection:
    def __init__(self, *, job_id: UUID, calls: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._job_id = job_id
        self._calls = calls
        self.closed = False

    async def fetchval(self, query: str, *args: Any) -> UUID:
        self._calls.append((query, args))
        return self._job_id

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_asyncpg(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    job_id = uuid4()
    calls: list[tuple[str, tuple[Any, ...]]] = []
    dsns: list[str] = []
    connections: list[_FakeAsyncpgConnection] = []

    async def _connect(dsn: str) -> _FakeAsyncpgConnection:
        dsns.append(dsn)
        conn = _FakeAsyncpgConnection(job_id=job_id, calls=calls)
        connections.append(conn)
        return conn

    fake_module = SimpleNamespace(connect=_connect)
    monkeypatch.setitem(sys.modules, "asyncpg", fake_module)
    return SimpleNamespace(job_id=job_id, calls=calls, dsns=dsns, connections=connections)


def _settings(**overrides: Any) -> SimpleNamespace:
    base = dict(
        QUEUE_PROVIDER="db",
        DATABASE_URL="postgresql+asyncpg://edecan:edecan@localhost:5432/edecan",
        # Presentes por si algo cae por error a la rama SQS (no debería).
        SQS_QUEUE_URL=None,
        AWS_ENDPOINT_URL=None,
        AWS_REGION="us-east-1",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_enqueue_db_inserta_en_jobs_y_devuelve_el_job_id(
    fake_asyncpg: SimpleNamespace,
) -> None:
    tenant_id = uuid4()

    job_id = await enqueue(_settings(), "ingest_file", {"file_id": "f1"}, tenant_id)

    assert job_id == fake_asyncpg.job_id
    assert len(fake_asyncpg.calls) == 1
    query, args = fake_asyncpg.calls[0]
    assert "INSERT INTO jobs" in query
    assert "RETURNING id" in query
    bound_tenant_id, bound_type, bound_payload_json = args
    assert bound_tenant_id == tenant_id
    assert bound_type == "ingest_file"
    assert json.loads(bound_payload_json) == {"file_id": "f1"}


async def test_enqueue_db_convierte_database_url_a_dsn_asyncpg(
    fake_asyncpg: SimpleNamespace,
) -> None:
    await enqueue(
        _settings(DATABASE_URL="postgresql+asyncpg://u:p@host:5432/db"),
        "sync_connector",
        {},
        None,
    )
    assert fake_asyncpg.dsns == ["postgresql://u:p@host:5432/db"]


async def test_enqueue_db_tenant_id_none_para_jobs_globales(
    fake_asyncpg: SimpleNamespace,
) -> None:
    await enqueue(_settings(), "send_reminder_scan", {}, None)
    _, args = fake_asyncpg.calls[0]
    assert args[0] is None


async def test_enqueue_db_cierra_la_conexion_siempre(fake_asyncpg: SimpleNamespace) -> None:
    await enqueue(_settings(), "memory_consolidate", {}, None)
    assert len(fake_asyncpg.connections) == 1
    assert fake_asyncpg.connections[0].closed is True


async def test_enqueue_db_delay_seconds_guarda_not_before_en_payload(
    fake_asyncpg: SimpleNamespace,
) -> None:
    before = datetime.now(UTC)

    await enqueue(
        _settings(), "run_campaign_step", {"campaign_id": "c1"}, uuid4(), delay_seconds=120
    )

    _, args = fake_asyncpg.calls[0]
    payload = json.loads(args[2])
    assert payload["campaign_id"] == "c1"
    not_before = datetime.fromisoformat(payload["_not_before"])
    assert not_before > before
    # No debería pasarse de largo: 120s +/- margen generoso para el propio test.
    assert (not_before - before).total_seconds() < 130


async def test_enqueue_db_sin_delay_seconds_no_agrega_not_before(
    fake_asyncpg: SimpleNamespace,
) -> None:
    await enqueue(_settings(), "ingest_file", {"file_id": "f1"}, uuid4())
    _, args = fake_asyncpg.calls[0]
    payload = json.loads(args[2])
    assert "_not_before" not in payload


async def test_enqueue_db_job_type_invalido_lanza_value_error(
    fake_asyncpg: SimpleNamespace,
) -> None:
    with pytest.raises(ValueError):
        await enqueue(_settings(), "borrar_todo", {}, None)
    assert fake_asyncpg.calls == []  # nunca llegó a intentar conectar


async def test_enqueue_db_sin_database_url_lanza_runtime_error(
    fake_asyncpg: SimpleNamespace,
) -> None:
    with pytest.raises(RuntimeError):
        await enqueue(_settings(DATABASE_URL=None), "ingest_file", {}, None)


async def test_enqueue_default_sigue_siendo_sqs_sin_queue_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`QUEUE_PROVIDER` ausente del todo (doble de prueba que no lo declara,
    como los que ya usa `test_queue.py`) debe seguir tomando la rama SQS de
    siempre -- back-compat total, ver ARCHITECTURE.md §12g."""
    sent: list[dict[str, Any]] = []

    class _FakeSqsClient:
        async def __aenter__(self) -> _FakeSqsClient:
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def send_message(self, **kwargs: Any) -> dict[str, str]:
            sent.append(kwargs)
            return {"MessageId": "fake-message-id"}

    class _FakeSession:
        def client(self, service_name: str, **kwargs: Any) -> _FakeSqsClient:
            return _FakeSqsClient()

    monkeypatch.setattr(queue_module, "aioboto3", SimpleNamespace(Session=lambda: _FakeSession()))

    settings = SimpleNamespace(
        SQS_QUEUE_URL="http://localhost:4566/000000000000/edecan-jobs",
        AWS_ENDPOINT_URL="http://localhost:4566",
        AWS_REGION="us-east-1",
    )
    job_id = await enqueue(settings, "ingest_file", {}, None)

    assert isinstance(job_id, UUID)
    assert len(sent) == 1
