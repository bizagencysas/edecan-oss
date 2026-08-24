"""`edecan_core` — motor del agente: herramientas, persona, memoria y cola.

Ver `ARCHITECTURE.md` §9 (flujo de referencia de una conversación) y §10.7
(contrato de este paquete). `edecan_core.memory` es un subpaquete aparte
(`from edecan_core.memory import PgMemoryStore, HashEmbedder, ...`), no se
re-exporta aquí — así, quien solo necesita `Agent`/`ToolRegistry`/`persona`
no arrastra nada relacionado con la capa de datos.
"""

from __future__ import annotations

from .agent import Agent, SeleccionDeModelo
from .cards import BadgeCard, BotonCard, LineaCuerpo, construir_card_generica
from .confidence import ConfidenceTracker
from .creator_planner import (
    derive_creation_title,
    detect_artifact_kinds,
    normalize_artifact_kind,
    plan_creation,
)
from .event_bus import EventBus
from .persona import build_system_prompt
from .speech_tags import enriquecer_speech_tags
from .provider_health import ProviderHealth
from .queue import enqueue
from .safety import redact
from .session import UnifiedSessionState
from .session_store import load_unified_session, save_unified_session
from .tools.base import Tool, ToolContext, ToolResult
from .tools.registry import ToolRegistry
from .veracidad import Fidelidad, InfoFidelidad, ProveedorDeclarado
from .visual_memory import VisualMemory
from .web_security import sanitize_web_content, scan_for_injection, wrap_untrusted

__all__ = [
    "Agent",
    "SeleccionDeModelo",
    "BadgeCard",
    "BotonCard",
    "LineaCuerpo",
    "construir_card_generica",
    "ConfidenceTracker",
    "derive_creation_title",
    "detect_artifact_kinds",
    "normalize_artifact_kind",
    "plan_creation",
    "EventBus",
    "ProviderHealth",
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolRegistry",
    "VisualMemory",
    "Fidelidad",
    "InfoFidelidad",
    "ProveedorDeclarado",
    "build_system_prompt",
    "enriquecer_speech_tags",
    "enqueue",
    "redact",
    "UnifiedSessionState",
    "load_unified_session",
    "save_unified_session",
    "sanitize_web_content",
    "scan_for_injection",
    "wrap_untrusted",
]
