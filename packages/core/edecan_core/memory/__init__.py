"""`edecan_core.memory` — memoria de largo plazo del agente (ARCHITECTURE.md §10.7).

`MemoryStore`/`Embedder` (protocolos) + `MemoryHit`, las implementaciones
`HashEmbedder`/`OpenAICompatEmbedder`/`PgMemoryStore`, el grafo
(`add_edge`/`neighbors` sobre `memory_edges`), y `build_profile` — la función
pura que construye/actualiza el «perfil vivo» del usuario (WP-V2-13, ver
`profile.py`). `build_profile` no habla con la base de datos ni con
`edecan_llm`: el caller real (`apps/worker/edecan_worker/handlers/
memory_consolidate.py`) lee/escribe `user_profiles` y espeja el resumen en
`memory_items` para que `MemoryStore.search` lo inyecte en cada turno.

Este subpaquete se importa aparte de `edecan_core` (no se re-exporta en
`edecan_core/__init__.py`): así, usar solo `Agent`/`ToolRegistry`/`persona`
no arrastra nada relacionado con la capa de datos.
"""

from __future__ import annotations

from .base import Embedder, MemoryHit, MemoryStore
from .embedders import DEFAULT_EMBEDDINGS_DIM, HashEmbedder, OpenAICompatEmbedder
from .graph import add_edge, neighbors
from .pg import PgMemoryStore
from .profile import CAMPOS_DATOS, LISTA_MAX_ITEMS, RESUMEN_MAX_CHARS, LlmComplete, build_profile

__all__ = [
    "CAMPOS_DATOS",
    "DEFAULT_EMBEDDINGS_DIM",
    "LISTA_MAX_ITEMS",
    "RESUMEN_MAX_CHARS",
    "Embedder",
    "HashEmbedder",
    "LlmComplete",
    "MemoryHit",
    "MemoryStore",
    "OpenAICompatEmbedder",
    "PgMemoryStore",
    "add_edge",
    "build_profile",
    "neighbors",
]
