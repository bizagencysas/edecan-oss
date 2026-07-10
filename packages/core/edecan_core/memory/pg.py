"""`PgMemoryStore` — `MemoryStore` sobre PostgreSQL + pgvector.

Tabla `memory_items` (ARCHITECTURE.md §10.3): `tenant_id, user_id, kind,
content, embedding vector(1536) nullable, importance, source`. `search()`
ordena por distancia coseno (`embedding <=> :q`) cuando hay un `Embedder`; si
`embedder is None` (self-host sin `EMBEDDINGS_MODEL` configurado) cae a un
`ILIKE` de texto plano — degradado pero funcional sin proveedor de
embeddings.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from ._sql import sql
from .base import Embedder, MemoryHit


class PgMemoryStore:
    """Implementación de `MemoryStore` sobre la tabla `memory_items`.

    `session` es la `AsyncSession` que entrega `edecan_db.session.get_session`
    — se recibe como `Any` para no acoplar `edecan_core` a `edecan_db`
    (ARCHITECTURE.md §10.1). `embedder` es opcional: `None` desactiva la
    búsqueda vectorial y usa el fallback `ILIKE`.
    """

    def __init__(self, session: Any, embedder: Embedder | None) -> None:
        self._session = session
        self._embedder = embedder

    async def add(
        self,
        tenant_id: UUID,
        user_id: UUID,
        kind: str,
        content: str,
        *,
        importance: float = 0.5,
        source: str = "",
    ) -> MemoryHit:
        memory_id = uuid4()
        embedding_literal = None
        if self._embedder is not None:
            [embedding] = await self._embedder.embed([content])
            embedding_literal = _vector_literal(embedding)

        await self._session.execute(
            sql(
                """
                INSERT INTO memory_items (
                    id, tenant_id, user_id, kind, content, embedding, importance, source,
                    created_at, updated_at
                ) VALUES (
                    :id, :tenant_id, :user_id, :kind, :content, :embedding ::vector, :importance,
                    :source, now(), now()
                )
                """
            ),
            {
                "id": memory_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "kind": kind,
                "content": content,
                "embedding": embedding_literal,
                "importance": importance,
                "source": source,
            },
        )
        return MemoryHit(id=memory_id, content=content, kind=kind, importance=importance, score=1.0)

    async def search(
        self, tenant_id: UUID, user_id: UUID, query: str, k: int = 8
    ) -> list[MemoryHit]:
        if self._embedder is None:
            return await self._search_ilike(tenant_id, user_id, query, k)

        [query_embedding] = await self._embedder.embed([query])
        result = await self._session.execute(
            sql(
                """
                SELECT id, content, kind, importance, 1 - (embedding <=> :q ::vector) AS score
                FROM memory_items
                WHERE tenant_id = :tenant_id AND user_id = :user_id AND embedding IS NOT NULL
                ORDER BY embedding <=> :q ::vector
                LIMIT :k
                """
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "q": _vector_literal(query_embedding),
                "k": k,
            },
        )
        return [_row_to_hit(row, default_score=0.0) for row in result.mappings().all()]

    async def _search_ilike(
        self, tenant_id: UUID, user_id: UUID, query: str, k: int
    ) -> list[MemoryHit]:
        result = await self._session.execute(
            sql(
                """
                SELECT id, content, kind, importance
                FROM memory_items
                WHERE tenant_id = :tenant_id AND user_id = :user_id AND content ILIKE :q
                ORDER BY importance DESC, created_at DESC
                LIMIT :k
                """
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "q": f"%{query}%", "k": k},
        )
        return [_row_to_hit(row, default_score=0.0) for row in result.mappings().all()]


def _row_to_hit(row: Any, *, default_score: float) -> MemoryHit:
    return MemoryHit(
        id=row["id"],
        content=row["content"],
        kind=row["kind"],
        importance=row["importance"],
        score=float(row["score"]) if "score" in row else default_score,
    )


def _vector_literal(values: list[float]) -> str:
    """Formatea `values` como literal de texto de pgvector: `"[0.1,0.2,...]"`.

    `asyncpg` no conoce el tipo `vector` sin un codec registrado — pasar el
    literal de texto y castear en SQL (`:embedding` en una columna `vector`,
    con `embedding <=> :q` comparando contra otro literal) es la forma
    estándar de hablarle a pgvector con SQL parametrizado puro.
    """
    return "[" + ",".join(repr(float(v)) for v in values) + "]"
