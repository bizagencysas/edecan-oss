"""`PgMemoryStore` — `MemoryStore` sobre PostgreSQL + pgvector.

Tabla `memory_items` (ARCHITECTURE.md §10.3): `tenant_id, user_id, kind,
content, embedding vector(1536) nullable, importance, source`. `search()`
ordena por distancia coseno (`embedding <=> :q`) cuando hay un `Embedder`; si
`embedder is None` (self-host sin `EMBEDDINGS_MODEL` configurado) cae a un
`ILIKE` de texto plano — degradado pero funcional sin proveedor de
embeddings.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from ._sql import sql
from .base import Embedder, MemoryHit
from .graph import neighbors

logger = logging.getLogger(__name__)


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
        confidence: float = 0.8,
        expires_at: datetime | None = None,
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
                    id, tenant_id, user_id, kind, content, embedding, importance,
                    confidence, source, expires_at, created_at, updated_at
                ) VALUES (
                    :id, :tenant_id, :user_id, :kind, :content, :embedding ::vector,
                    :importance, :confidence, :source, :expires_at, now(), now()
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
                "confidence": confidence,
                "source": source,
                "expires_at": expires_at,
            },
        )
        return MemoryHit(
            id=memory_id,
            content=content,
            kind=kind,
            importance=importance,
            score=1.0,
            confidence=confidence,
            expires_at=expires_at,
        )

    async def search(
        self,
        tenant_id: UUID,
        user_id: UUID,
        query: str,
        k: int = 8,
        include_neighbors: bool = True,
    ) -> list[MemoryHit]:
        """Los `k` recuerdos más relevantes para `query`, más —si
        `include_neighbors`— los vecinos del grafo de memoria de esos hits
        (aristas salientes en `memory_edges`), etiquetados como contexto
        relacionado y no como hits directos (PHASE2.md §97).

        Los vecinos se anexan al final con `kind="related"`, `score=0.0` y un
        marcador en `content` ("Relacionado con «…» (…): …") para que el
        agente distinga el hit directo del contexto traído por asociación.
        El marcador vive en `content` a propósito: `Agent._recall_memories`
        solo consume `hit.content`, y `MemoryHit` (slots, en `base.py`) está
        congelado, así que no se puede añadirle un campo `related_to` sin
        tocar `base.py` — el marcador es el único punto donde el agente lo ve.
        """
        if self._embedder is None:
            hits = await self._search_ilike(tenant_id, user_id, query, k)
        else:
            [query_embedding] = await self._embedder.embed([query])
            try:
                # El SAVEPOINT evita que un módulo pgvector ausente deje abortada
                # toda la transacción del turno. El mensaje del usuario y el resto
                # del chat permanecen intactos cuando activamos el fallback.
                async with self._session.begin_nested():
                    result = await self._session.execute(
                        sql(
                            """
                            SELECT id, content, kind, importance, confidence, expires_at,
                                   1 - (embedding <=> :q ::vector) AS score
                            FROM memory_items
                            WHERE tenant_id = :tenant_id AND user_id = :user_id
                              AND embedding IS NOT NULL AND superseded_at IS NULL
                              AND (expires_at IS NULL OR expires_at > now())
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
                    negations = await self._search_negations_vector(
                        tenant_id, user_id, query_embedding, k
                    )
            except Exception as exc:  # noqa: BLE001 - se filtra estrictamente abajo
                if not _is_vector_unavailable(exc):
                    raise
                logger.warning(
                    "pgvector no está disponible; la memoria continúa con búsqueda textual.",
                    exc_info=True,
                )
                hits = await self._search_ilike(tenant_id, user_id, query, k)
            else:
                hits = _merge_negations(
                    [_row_to_hit(row, default_score=0.0) for row in result.mappings().all()],
                    [_row_to_hit(row, default_score=0.0) for row in negations.mappings().all()],
                )

        if include_neighbors and hits:
            return await self._anexar_vecinos(tenant_id, user_id, hits)
        return hits

    async def _anexar_vecinos(
        self, tenant_id: UUID, user_id: UUID, hits: list[MemoryHit]
    ) -> list[MemoryHit]:
        """Anexa a `hits` los vecinos del grafo de memoria como contexto
        relacionado. Best-effort: cualquier fallo (tabla `memory_edges`
        ausente, fila inesperada, etc.) se registra y devuelve solo los hits
        directos — los vecinos son contexto extra y nunca deben tumbar la
        búsqueda ni el turno."""
        try:
            vecinos = await self._fetch_vecinos(tenant_id, user_id, hits)
        except Exception:  # noqa: BLE001 - los vecinos nunca degradan a error
            logger.warning(
                "No se pudieron resolver los vecinos del grafo de memoria; "
                "se devuelven solo los hits directos.",
                exc_info=True,
            )
            return hits
        if not vecinos:
            return hits
        return [*hits, *vecinos]

    async def _fetch_vecinos(
        self, tenant_id: UUID, user_id: UUID, hits: list[MemoryHit]
    ) -> list[MemoryHit]:
        """Resuelve las aristas salientes de cada hit a `memory_items` y arma
        `MemoryHit`s etiquetados `kind="related"`. Un vecino que ya apareció
        como hit directo no se repite, y las filas de aristas que no traen
        `src_id`/`dst_id` parseables se ignoran sin fallar."""
        origen_por_id = {hit.id: hit for hit in hits}
        dst_a_origen: dict[UUID, UUID] = {}
        relacion_por_dst: dict[UUID, str] = {}
        for hit in hits:
            for edge in await neighbors(self._session, tenant_id=tenant_id, node_id=hit.id):
                src = edge.get("src_id")
                dst = edge.get("dst_id")
                if not isinstance(src, UUID) or not isinstance(dst, UUID):
                    continue
                if dst not in dst_a_origen:
                    dst_a_origen[dst] = src
                    relacion_por_dst[dst] = str(edge.get("relation") or "")

        directos = {hit.id for hit in hits}
        dst_ids = [dst for dst in dst_a_origen if dst not in directos]
        if not dst_ids:
            return []

        placeholders = ", ".join(f":n{i}" for i in range(len(dst_ids)))
        params: dict[str, Any] = {"tenant_id": tenant_id, "user_id": user_id}
        for i, dst_id in enumerate(dst_ids):
            params[f"n{i}"] = dst_id
        result = await self._session.execute(
            sql(
                f"""
                SELECT id, content, kind, importance, confidence, expires_at
                FROM memory_items
                WHERE tenant_id = :tenant_id AND user_id = :user_id
                  AND superseded_at IS NULL
                  AND (expires_at IS NULL OR expires_at > now())
                  AND id IN ({placeholders})
                """
            ),
            params,
        )

        vecinos: list[MemoryHit] = []
        for row in result.mappings().all():
            row_id = row["id"]
            origen = origen_por_id.get(dst_a_origen.get(row_id))
            base = _row_to_hit(row, default_score=0.0)
            contenido = base.content
            if origen is not None:
                relacion = relacion_por_dst.get(row_id, "")
                contenido = f"Relacionado con «{origen.content}» ({relacion}): {contenido}"
            vecinos.append(
                MemoryHit(
                    id=base.id,
                    content=contenido,
                    kind="related",
                    importance=base.importance,
                    score=0.0,
                    confidence=base.confidence,
                    expires_at=base.expires_at,
                )
            )
        return vecinos

    async def _search_negations_vector(
        self, tenant_id: UUID, user_id: UUID, query_embedding: list[float], k: int
    ) -> Any:
        """Top negaciones (`kind='negation'`) por relevancia vectorial, aparte
        del `search` normal. Garantiza que el conocimiento negativo del usuario
        sea visible al agente aunque no rankee entre los `k` más parecidos a
        una query positiva (p. ej. "recomiéndame comida" vs "no quiere
        pizza"): sin este fetch dedicado, una negación poco parecida quedaría
        fuera del contexto y el agente podría recomendar justo lo que el
        usuario rechazó."""
        return await self._session.execute(
            sql(
                """
                SELECT id, content, kind, importance, confidence, expires_at,
                       1 - (embedding <=> :q ::vector) AS score
                FROM memory_items
                WHERE tenant_id = :tenant_id AND user_id = :user_id
                  AND kind = 'negation' AND embedding IS NOT NULL
                  AND superseded_at IS NULL
                  AND (expires_at IS NULL OR expires_at > now())
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

    async def _search_ilike(
        self, tenant_id: UUID, user_id: UUID, query: str, k: int
    ) -> list[MemoryHit]:
        result = await self._session.execute(
            sql(
                """
                SELECT id, content, kind, importance, confidence, expires_at
                FROM memory_items
                WHERE tenant_id = :tenant_id AND user_id = :user_id
                  AND superseded_at IS NULL
                  AND (expires_at IS NULL OR expires_at > now())
                  AND content ILIKE :q
                ORDER BY importance DESC, created_at DESC
                LIMIT :k
                """
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "q": f"%{query}%", "k": k},
        )
        hits = [_row_to_hit(row, default_score=0.0) for row in result.mappings().all()]
        neg_result = await self._session.execute(
            sql(
                """
                SELECT id, content, kind, importance, confidence, expires_at
                FROM memory_items
                WHERE tenant_id = :tenant_id AND user_id = :user_id
                  AND kind = 'negation' AND superseded_at IS NULL
                  AND (expires_at IS NULL OR expires_at > now())
                ORDER BY importance DESC, created_at DESC
                LIMIT :k
                """
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "k": k},
        )
        neg_hits = [_row_to_hit(row, default_score=0.0) for row in neg_result.mappings().all()]
        return _merge_negations(hits, neg_hits)


def _row_to_hit(row: Any, *, default_score: float) -> MemoryHit:
    return MemoryHit(
        id=row["id"],
        content=row["content"],
        kind=row["kind"],
        importance=row["importance"],
        score=float(row["score"]) if "score" in row else default_score,
        confidence=float(row["confidence"]) if "confidence" in row else 0.8,
        expires_at=row.get("expires_at") if "expires_at" in row else None,
    )


def _merge_negations(hits: list[MemoryHit], negations: list[MemoryHit]) -> list[MemoryHit]:
    """Concatena `hits` con `negations` evitando duplicados por `id`: una
    negación muy parecida a la query ya puede aparecer en `hits` (el `search`
    normal no filtra por `kind`), y no tiene sentido devolverla dos veces."""
    seen: set[object] = {hit.id for hit in hits}
    for neg in negations:
        if neg.id not in seen:
            seen.add(neg.id)
            hits.append(neg)
    return hits


def _vector_literal(values: list[float]) -> str:
    """Formatea `values` como literal de texto de pgvector: `"[0.1,0.2,...]"`.

    `asyncpg` no conoce el tipo `vector` sin un codec registrado — pasar el
    literal de texto y castear en SQL (`:embedding` en una columna `vector`,
    con `embedding <=> :q` comparando contra otro literal) es la forma
    estándar de hablarle a pgvector con SQL parametrizado puro.
    """
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _is_vector_unavailable(exc: BaseException) -> bool:
    """Reconoce únicamente fallos de instalación/carga de pgvector.

    No degrada errores arbitrarios de SQL, permisos ni conectividad: esos se
    siguen propagando para no esconder defectos reales. SQLAlchemy envuelve
    ``asyncpg`` varias veces, por eso se recorren ``__cause__`` y
    ``__context__`` además del texto exterior.
    """

    parts: list[str] = []
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        parts.append(f"{type(current).__module__}.{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    detail = "\n".join(parts).casefold()
    markers = (
        'could not access file "$libdir/vector"',
        "undefinedfileerror",
        'type "vector" does not exist',
        'extension "vector" is not available',
        "operator does not exist: vector",
    )
    return any(marker in detail for marker in markers)
