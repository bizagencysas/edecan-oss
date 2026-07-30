"""Grafo de memoria: aristas entre `memory_items` (tabla `memory_edges`,
ARCHITECTURE.md §10.3, §10.7)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from ._sql import sql


async def add_edge(
    session: Any, *, tenant_id: UUID, src_id: UUID, dst_id: UUID, relation: str
) -> UUID:
    """Crea una arista `src_id -[relation]-> dst_id` en `memory_edges` y devuelve su id."""
    edge_id = uuid4()
    await session.execute(
        sql(
            """
            INSERT INTO memory_edges
                (id, tenant_id, src_id, dst_id, relation, created_at, updated_at)
            VALUES (:id, :tenant_id, :src_id, :dst_id, :relation, now(), now())
            """
        ),
        {
            "id": edge_id,
            "tenant_id": tenant_id,
            "src_id": src_id,
            "dst_id": dst_id,
            "relation": relation,
        },
    )
    return edge_id


async def neighbors(
    session: Any, *, tenant_id: UUID, node_id: UUID, relation: str | None = None
) -> list[dict[str, Any]]:
    """Vecinos salientes (`src_id = node_id`) de `node_id`, opcionalmente filtrados por `relation`.

    Devuelve filas crudas (`id, src_id, dst_id, relation, created_at`) — quien
    llame decide cómo resolverlas a `memory_items` si lo necesita.
    """
    statement = """
        SELECT id, src_id, dst_id, relation, created_at
        FROM memory_edges
        WHERE tenant_id = :tenant_id AND src_id = :node_id
    """
    params: dict[str, Any] = {"tenant_id": tenant_id, "node_id": node_id}
    if relation is not None:
        statement += " AND relation = :relation"
        params["relation"] = relation
    statement += " ORDER BY created_at ASC"

    result = await session.execute(sql(statement), params)
    return [dict(row) for row in result.mappings().all()]
