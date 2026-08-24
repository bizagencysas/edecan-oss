"""Visual memory: contexto visual persistente entre imágenes (§22, §23).

Mantiene un contexto visual estructurado de imágenes recientes en la
conversación, permitiendo que el agente recuerde qué vio en fotos
anteriores sin re-enviar los píxeles.

Estructura::

    VisualContext
    - entities: [str]     # "Tanjiro", "Demon Slayer"
    - environment: str    # "exterior, día"
    - scene: str          # "anime, acción"
    - text: str           # texto visible en la imagen
    - confidence: float   # 0.0-1.0
    - conversation_topic: str  # tema inferido

Las imágenes recientes (últimas 2) se mantienen con su contexto completo.
Las antiguas se resumen semánticamente.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

MAX_VISUAL_CONTEXTS = 5


@dataclass
class VisualContext:
    entities: list[str] = field(default_factory=list)
    environment: str = ""
    scene: str = ""
    text: str = ""
    products: list[str] = field(default_factory=list)
    confidence: float = 0.5
    conversation_topic: str = ""
    timestamp: float = field(default_factory=time.time)
    summarized: bool = False

    def to_summary(self) -> str:
        parts: list[str] = []
        if self.entities:
            parts.append(f"Entidades: {', '.join(self.entities[:5])}")
        if self.scene:
            parts.append(f"Escena: {self.scene}")
        if self.environment:
            parts.append(f"Entorno: {self.environment}")
        if self.text:
            parts.append(f"Texto: {self.text[:200]}")
        if self.products:
            parts.append(f"Productos: {', '.join(self.products[:3])}")
        if self.conversation_topic:
            parts.append(f"Tema: {self.conversation_topic}")
        return " | ".join(parts) if parts else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": list(self.entities),
            "environment": self.environment,
            "scene": self.scene,
            "text": self.text,
            "products": list(self.products),
            "confidence": self.confidence,
            "conversation_topic": self.conversation_topic,
            "timestamp": self.timestamp,
            "summarized": self.summarized,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VisualContext:
        return cls(
            entities=d.get("entities", []),
            environment=d.get("environment", ""),
            scene=d.get("scene", ""),
            text=d.get("text", ""),
            products=d.get("products", []),
            confidence=d.get("confidence", 0.5),
            conversation_topic=d.get("conversation_topic", ""),
            timestamp=d.get("timestamp", time.time()),
            summarized=d.get("summarized", False),
        )


class VisualMemory:
    """Almacena contexto visual de imágenes recientes en la conversación.

    ``context_key`` identifica a quién pertenece esta memoria (p. ej. el
    ``conversation_id``): se conserva en la serialización y ``merge`` lo usa
    para negarse a mezclar contextos de conversaciones distintas.
    """

    def __init__(
        self, max_contexts: int = MAX_VISUAL_CONTEXTS, context_key: str | None = None
    ) -> None:
        self._contexts: list[VisualContext] = []
        self._max = max_contexts
        self.context_key = context_key

    def add(self, ctx: VisualContext) -> None:
        self._contexts.append(ctx)
        if len(self._contexts) > self._max:
            old = self._contexts.pop(0)
            old.summarized = True
            self._contexts.insert(0, old)

    @property
    def recent(self) -> list[VisualContext]:
        return self._contexts[-2:] if self._contexts else []

    @property
    def all_contexts(self) -> list[VisualContext]:
        return list(self._contexts)

    def build_context_prompt(self) -> str:
        if not self._contexts:
            return ""
        parts: list[str] = []
        for i, ctx in enumerate(self._contexts):
            summary = ctx.to_summary()
            if summary:
                label = "Foto actual" if i == len(self._contexts) - 1 else f"Foto anterior {i+1}"
                parts.append(f"[{label}] {summary}")
        return "\n".join(parts)

    def clear(self) -> None:
        self._contexts.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "contexts": [ctx.to_dict() for ctx in self._contexts],
            "max_contexts": self._max,
            "context_key": self.context_key,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VisualMemory:
        memoria = cls(
            max_contexts=d.get("max_contexts", MAX_VISUAL_CONTEXTS),
            context_key=d.get("context_key"),
        )
        memoria._contexts = [
            VisualContext.from_dict(item) for item in d.get("contexts", [])
        ]
        return memoria

    def merge(self, other: VisualMemory) -> None:
        """Incorpora los contextos de ``other`` respetando ``context_key``.

        Si ambas memorias declaran ``context_key`` y difieren, no se mezcla
        nada: es el guardián que impide que una conversación herede el
        contexto visual de otra. Los contextos se copian (no se reutilizan por
        referencia) para que mutaciones posteriores de ``other`` no afecten a
        esta memoria.
        """
        if (
            self.context_key
            and other.context_key
            and self.context_key != other.context_key
        ):
            return
        for ctx in other._contexts:
            self.add(VisualContext.from_dict(ctx.to_dict()))

    def extract_from_tool_result(
        self, content: str, data: dict[str, Any] | None = None
    ) -> VisualContext | None:
        """Intenta extraer contexto visual del resultado de analizar_imagen."""
        if not content:
            return None
        d = data or {}
        ctx = VisualContext(
            text=d.get("text", ""),
            conversation_topic=d.get("topic", ""),
            confidence=d.get("confidence", 0.6),
        )
        entities = d.get("entities", [])
        if entities:
            ctx.entities = entities[:10]
        scene = d.get("scene", "")
        if scene:
            ctx.scene = scene
        environment = d.get("environment", "")
        if environment:
            ctx.environment = environment
        return ctx
