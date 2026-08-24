"""Contratos locales del lease de workers persistentes.

La recuperación real depende de PostgreSQL, pero la normalización del presupuesto
debe ser determinista y no permitir leases indefinidos por configuración inválida.
"""

import uuid

import pytest
from edecan_worker.handlers.run_persistent_agent import (
    DEFAULT_LEASE_SECONDS,
    MAX_LEASE_SECONDS,
    _lease_seconds,
    _save_checkpoint,
)


def test_lease_por_defecto_y_limites() -> None:
    assert _lease_seconds({}) == DEFAULT_LEASE_SECONDS
    assert _lease_seconds({"lease_seconds": 1}) == 30.0
    assert _lease_seconds({"lease_seconds": MAX_LEASE_SECONDS * 2}) == MAX_LEASE_SECONDS


def test_lease_invalido_no_desactiva_la_recuperacion() -> None:
    assert _lease_seconds({"lease_seconds": True}) == DEFAULT_LEASE_SECONDS
    assert _lease_seconds({"lease_seconds": "never"}) == DEFAULT_LEASE_SECONDS


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return None


class _Session:
    def __init__(self):
        self.calls = []

    async def execute(self, statement, params):
        self.calls.append((str(statement), params))


class _Deps:
    def __init__(self, session):
        self.session = session

    def session_factory(self, _tenant):
        return _SessionContext(self.session)


@pytest.mark.asyncio
async def test_checkpoint_no_puede_sobrescribir_otra_tarea() -> None:
    session = _Session()
    tenant_id, worker_id = uuid.uuid4(), uuid.uuid4()

    await _save_checkpoint(
        _Deps(session),
        tenant_id,
        worker_id,
        task_id="task-owner",
        status="idle",
        detail={"task_id": "task-owner", "status": "done"},
    )

    sql, params = session.calls[0]
    assert "last_checkpoint->>'task_id' = :task_id" in sql
    assert params["task_id"] == "task-owner"
