"""Entrada de chat y `AgentEvent` — unión discriminada del loop del agente (§10.7).

`AgentEvent` es el contrato exacto de los eventos que emite
`edecan_core.agent.Agent.run_turn` y que `edecan_api` traduce 1:1 a eventos
SSE (`message.delta`, `tool.start`, `tool.end`, `confirmation.required`,
`message.done`, `error`).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter


class ChatMessageIn(BaseModel):
    """Cuerpo de `POST /v1/conversations/{id}/messages`."""

    text: str


class TextDeltaEvent(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolStartEvent(BaseModel):
    type: Literal["tool_start"] = "tool_start"
    name: str
    args: dict


class ToolEndEvent(BaseModel):
    type: Literal["tool_end"] = "tool_end"
    name: str
    result_preview: str


class ConfirmationRequiredEvent(BaseModel):
    type: Literal["confirmation_required"] = "confirmation_required"
    tool_call_id: str
    name: str
    args: dict


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    usage: dict[str, int] = Field(default_factory=dict)


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


AgentEvent = Annotated[
    TextDeltaEvent
    | ToolStartEvent
    | ToolEndEvent
    | ConfirmationRequiredEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="type"),
]
"""Unión discriminada por `type` con las 6 variantes pinned en ARCHITECTURE.md §10.7."""

AgentEventAdapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)
"""`TypeAdapter` listo para validar/parsear un `dict` (p. ej. JSON de un evento SSE)
hacia la variante concreta de `AgentEvent` que corresponda."""
