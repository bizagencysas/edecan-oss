"""Protocolos de memoria del agente: `MemoryStore`, `Embedder`, `MemoryHit`
(ARCHITECTURE.md §10.7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(slots=True)
class MemoryHit:
    """Un resultado de `MemoryStore.search`.

    `score` es una similitud (más alto = más relevante); su escala depende
    de la implementación (p. ej. `PgMemoryStore` usa `1 - distancia_coseno`
    cuando hay `Embedder`, o `0.0` en el fallback por texto plano).

    `confidence` = "qué tan seguros estamos de que esto es cierto" (0.0-1.0),
    distinto de `importance` ("qué tan útil es recordarlo"). `expires_at`
    (nullable) es la caducidad del hecho temporal; `None` = no caduca.
    """

    id: UUID
    content: str
    kind: str
    importance: float
    score: float
    confidence: float = 0.8
    expires_at: datetime | None = None


@runtime_checkable
class Embedder(Protocol):
    """Genera vectores de embedding para una lista de textos.

    Implementaciones: `HashEmbedder` (determinista, 100% offline) y
    `OpenAICompatEmbedder` (vía API). Ver `edecan_core.memory.embedders`.
    """

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Devuelve un vector por cada texto de `texts`, en el mismo orden."""
        ...


@runtime_checkable
class MemoryStore(Protocol):
    """Almacén de memoria de largo plazo del agente, por tenant/usuario.

    Implementación: `PgMemoryStore` (pgvector). `Agent.run_turn` busca aquí
    (`ctx.extras["memory_store"]`) cuando `persona.memoria_activada` es
    `True`, y le pasa el resultado a `build_system_prompt` como `memories`.
    """

    async def search(
        self,
        tenant_id: UUID,
        user_id: UUID,
        query: str,
        k: int = 8,
        include_neighbors: bool = True,
        namespace: str = "user",
    ) -> list[MemoryHit]:
        """Los `k` recuerdos más relevantes para `query`, del `tenant_id`/`user_id` dados.

        `namespace` (default 'user') filtra el espacio de memoria; la búsqueda
        del agente sigue en el espacio plano 'user' salvo que se pida otro
        ('agent:<id>', 'workspace:<id>', etc.).
        """
        ...

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
        namespace: str = "user",
        source_trust: str = "trusted",
    ) -> MemoryHit:
        """Guarda un nuevo recuerdo (tabla `memory_items`, ARCHITECTURE.md §10.3).

        `kind` es uno de `fact|preference|event|entity|negation` (no forzado
        aquí a nivel de tipo — lo valida la capa de datos/CHECK constraint).
        `importance` = "qué tan útil es recordarlo"; `confidence` = "qué tan
        seguros estamos de que esto es cierto"; `expires_at` caduca el hecho
        temporal (las búsquedas ignoran las filas con `expires_at <= now()`).
        `namespace` (default 'user') y `source_trust` (default 'trusted')
        etiquetan el espacio y la confianza de procedencia (directiva §50-54).
        """
        ...
