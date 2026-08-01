"""`PgMemoryStore` — sobre una `session` falsa (sin Postgres real).

`sqlalchemy` no es una dependencia dura de `edecan_core` (ver
`edecan_core/memory/_sql.py`): estos tests SÍ pueden usarla directamente
(es una librería de terceros, no un paquete hermano `edecan_*`) para
verificar que el SQL generado queda envuelto en `text()` quien la tenga
instalada — que es como corre en el proceso real (`apps/api`).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from edecan_core.memory.base import MemoryHit
from edecan_core.memory.embedders import HashEmbedder
from edecan_core.memory.pg import PgMemoryStore


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

    @asynccontextmanager
    async def begin_nested(self):
        yield


@pytest.mark.asyncio
async def test_add_sin_embedder_inserta_embedding_none():
    session = _FakeSession()
    store = PgMemoryStore(session, embedder=None)

    hit = await store.add(uuid4(), uuid4(), "fact", "Le gusta el café solo")

    assert isinstance(hit, MemoryHit)
    assert hit.content == "Le gusta el café solo"
    assert len(session.calls) == 1
    statement, params = session.calls[0]
    assert "INSERT INTO memory_items" in str(statement)
    assert params["embedding"] is None
    assert params["importance"] == 0.5
    assert params["source"] == ""


@pytest.mark.asyncio
async def test_add_con_embedder_calcula_y_guarda_el_vector():
    session = _FakeSession()
    store = PgMemoryStore(session, embedder=HashEmbedder(dim=8))

    await store.add(
        uuid4(), uuid4(), "preference", "odia el cilantro", importance=0.9, source="chat"
    )

    _, params = session.calls[0]
    assert params["embedding"] is not None
    assert params["embedding"].startswith("[") and params["embedding"].endswith("]")
    assert params["importance"] == 0.9
    assert params["source"] == "chat"


@pytest.mark.asyncio
async def test_search_sin_embedder_usa_fallback_ilike():
    filas = [
        {"id": uuid4(), "content": "vive en CDMX", "kind": "fact", "importance": 0.7},
    ]
    session = _FakeSession(rows=filas)
    store = PgMemoryStore(session, embedder=None)

    hits = await store.search(uuid4(), uuid4(), "CDMX")

    assert len(hits) == 1
    assert hits[0].content == "vive en CDMX"
    assert hits[0].score == 0.0
    statement, params = session.calls[0]
    assert "ILIKE" in str(statement)
    assert "superseded_at IS NULL" in str(statement)
    assert params["q"] == "%CDMX%"
    assert params["k"] == 8


@pytest.mark.asyncio
async def test_search_con_embedder_ordena_por_distancia_vectorial():
    filas = [
        {"id": uuid4(), "content": "toma café", "kind": "fact", "importance": 0.4, "score": 0.83},
    ]
    session = _FakeSession(rows=filas)
    store = PgMemoryStore(session, embedder=HashEmbedder(dim=8))

    hits = await store.search(uuid4(), uuid4(), "¿qué toma?", k=3)

    statement, params = session.calls[0]
    assert "<=>" in str(statement)
    assert "ORDER BY embedding <=> :q" in str(statement)
    assert "superseded_at IS NULL" in str(statement)
    assert params["k"] == 3
    assert hits[0].score == pytest.approx(0.83)


@pytest.mark.asyncio
async def test_search_pgvector_ausente_degrada_a_texto_sin_tumbar_el_chat():
    filas = [
        {
            "id": uuid4(),
            "content": "prefiere vuelos directos",
            "kind": "preference",
            "importance": 0.8,
        },
    ]

    class _SessionSinVector(_FakeSession):
        async def execute(
            self, statement: Any, params: dict[str, Any] | None = None
        ) -> _FakeResult:
            self.calls.append((statement, params or {}))
            if "<=>" in str(statement):
                raise RuntimeError('could not access file "$libdir/vector": No such file')
            return _FakeResult(filas)

    session = _SessionSinVector()
    store = PgMemoryStore(session, embedder=HashEmbedder(dim=8))

    hits = await store.search(uuid4(), uuid4(), "vuelos", k=4)

    assert [hit.content for hit in hits] == ["prefiere vuelos directos"]
    assert len(session.calls) == 2
    assert "<=>" in str(session.calls[0][0])
    assert "ILIKE" in str(session.calls[1][0])


@pytest.mark.asyncio
async def test_search_no_oculta_errores_sql_ajenos_a_pgvector():
    class _SessionRota(_FakeSession):
        async def execute(
            self, statement: Any, params: dict[str, Any] | None = None
        ) -> _FakeResult:
            self.calls.append((statement, params or {}))
            raise RuntimeError("permission denied for memory_items")

    store = PgMemoryStore(_SessionRota(), embedder=HashEmbedder(dim=8))

    with pytest.raises(RuntimeError, match="permission denied"):
        await store.search(uuid4(), uuid4(), "vuelos")


@pytest.mark.asyncio
async def test_search_respeta_k():
    session = _FakeSession(rows=[])
    store = PgMemoryStore(session, embedder=None)
    await store.search(uuid4(), uuid4(), "algo", k=3)
    _, params = session.calls[0]
    assert params["k"] == 3
