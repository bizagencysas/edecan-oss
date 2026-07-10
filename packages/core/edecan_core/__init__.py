"""`edecan_core` — motor del agente: herramientas, persona, memoria y cola.

Ver `ARCHITECTURE.md` §9 (flujo de referencia de una conversación) y §10.7
(contrato de este paquete). `edecan_core.memory` es un subpaquete aparte
(`from edecan_core.memory import PgMemoryStore, HashEmbedder, ...`), no se
re-exporta aquí — así, quien solo necesita `Agent`/`ToolRegistry`/`persona`
no arrastra nada relacionado con la capa de datos.
"""

from __future__ import annotations

from .agent import Agent
from .persona import build_system_prompt
from .queue import enqueue
from .safety import redact
from .tools.base import Tool, ToolContext, ToolResult
from .tools.registry import ToolRegistry

__all__ = [
    "Agent",
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolRegistry",
    "build_system_prompt",
    "enqueue",
    "redact",
]
