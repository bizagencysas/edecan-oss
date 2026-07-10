"""Tipos comunes de proveedor LLM (`ARCHITECTURE.md` §10.6).

Estos tipos son el contrato compartido entre `edecan_core` (el agente) y cada
adaptador de proveedor (`AnthropicProvider`, `OpenAICompatProvider`,
`BedrockProvider`). Convención de `ChatMessage.content` cuando es una lista de
bloques (en vez de `str`): sigue la forma de los bloques de contenido de
Anthropic — proveedor primario —, es decir:

- ``{"type": "text", "text": "..."}``
- ``{"type": "tool_use", "id": "...", "name": "...", "input": {...}}`` (turno
  de un `assistant` que invoca una herramienta)
- ``{"type": "tool_result", "tool_use_id": "...", "content": "..."}`` (turno
  `role="tool"` con el resultado de una herramienta)

Cada adaptador no-Anthropic (`OpenAICompatProvider`, futuro `BedrockProvider`)
traduce desde/hacia esta forma común y el formato nativo de su wire API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]
StopReason = Literal["end", "tool_use", "max_tokens"]
StreamChunkType = Literal["text", "tool_call", "usage", "stop"]


class ChatMessage(BaseModel):
    """Un turno de la conversación en el formato común de `edecan_llm`."""

    role: Role
    content: str | list[dict[str, Any]]


class ToolSpec(BaseModel):
    """Definición de una herramienta ofrecida al modelo."""

    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    """Invocación de herramienta solicitada por el modelo."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    """Tokens consumidos por una completion (para `usage_events`)."""

    input_tokens: int = 0
    output_tokens: int = 0


class CompletionRequest(BaseModel):
    """Petición de completion, agnóstica de proveedor."""

    model: str
    system: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    max_tokens: int = 1024
    temperature: float = 0.7
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompletionResponse(BaseModel):
    """Respuesta de completion sin streaming, ya normalizada."""

    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage
    stop_reason: StopReason


class StreamChunk(BaseModel):
    """Unidad emitida por `LLMProvider.stream`."""

    type: StreamChunkType
    text: str | None = None
    tool_call: ToolCall | None = None
    usage: Usage | None = None


class LLMProvider(ABC):
    """Interfaz común que implementa cada adaptador de proveedor LLM."""

    name: str

    @abstractmethod
    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        """Ejecuta una completion sin streaming."""
        raise NotImplementedError

    @abstractmethod
    def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]:
        """Ejecuta una completion en streaming, emitiendo `StreamChunk`."""
        raise NotImplementedError
