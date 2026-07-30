"""`add_edge`/`neighbors` sobre `memory_edges` (ARCHITECTURE.md §10.3, §10.7)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from edecan_core.memory.graph import add_edge, neighbors


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []
        self._rows = rows or []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.calls.append((statement, params or {}))
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_add_edge_inserta_y_devuelve_uuid():
    session = _FakeSession()
    tenant_id, src_id, dst_id = uuid4(), uuid4(), uuid4()

    edge_id = await add_edge(
        session, tenant_id=tenant_id, src_id=src_id, dst_id=dst_id, relation="conoce_a"
    )

    assert isinstance(edge_id, UUID)
    assert len(session.calls) == 1
    statement, params = session.calls[0]
    assert "INSERT INTO memory_edges" in str(statement)
    assert params["tenant_id"] == tenant_id
    assert params["src_id"] == src_id
    assert params["dst_id"] == dst_id
    assert params["relation"] == "conoce_a"


@pytest.mark.asyncio
async def test_neighbors_sin_filtro_de_relation():
    tenant_id, node_id = uuid4(), uuid4()
    filas = [{"id": uuid4(), "src_id": node_id, "dst_id": uuid4(), "relation": "conoce_a"}]
    session = _FakeSession(rows=filas)

    resultado = await neighbors(session, tenant_id=tenant_id, node_id=node_id)

    assert resultado == filas
    statement, params = session.calls[0]
    assert "src_id = :node_id" in str(statement)
    assert "relation" not in params
    assert params["node_id"] == node_id


@pytest.mark.asyncio
async def test_neighbors_filtra_por_relation():
    tenant_id, node_id = uuid4(), uuid4()
    session = _FakeSession(rows=[])

    await neighbors(session, tenant_id=tenant_id, node_id=node_id, relation="trabaja_con")

    statement, params = session.calls[0]
    assert "AND relation = :relation" in str(statement)
    assert params["relation"] == "trabaja_con"
