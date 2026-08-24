from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from edecan_api.provider_health_store import ProviderHealthEventStore


class _FakeSession:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))


async def test_store_flush_redactado_y_retencion_son_asincronos() -> None:
    session = _FakeSession()

    @asynccontextmanager
    async def session_factory(_tenant_id):
        yield session

    store = ProviderHealthEventStore(session_factory, max_queue=2)
    store.enqueue(
        {"provider": "principal", "status": "success", "latency": 0.25, "at": 1_700_000_000}
    )
    store.enqueue(
        {
            "provider": "principal",
            "status": "failure",
            "latency": 0,
            "at": 1_700_000_001,
            "error": "secreto",
        }
    )
    store.enqueue({"provider": "bad provider", "status": "success", "latency": 1, "at": 1})

    await store.start()
    await store.stop()

    assert len(session.executed) == 2
    insert_sql, insert_params = session.executed[0]
    assert "INSERT INTO provider_health_events" in insert_sql
    assert len(insert_params) == 2
    assert all("error" not in params for params in insert_params)
    assert "DELETE FROM provider_health_events" in session.executed[1][0]
    assert store.dropped_events == 0


def test_store_acota_cola_y_no_guarda_provider_invalido() -> None:
    @asynccontextmanager
    async def session_factory(_tenant_id):
        yield _FakeSession()

    store = ProviderHealthEventStore(session_factory, max_queue=1)
    store.enqueue({"provider": "principal", "status": "success", "latency": 0, "at": 1})
    store.enqueue({"provider": "principal", "status": "failure", "latency": 0, "at": 2})
    store.enqueue({"provider": "principal", "status": "failure", "latency": 0, "at": 3})

    assert store.dropped_events == 2
