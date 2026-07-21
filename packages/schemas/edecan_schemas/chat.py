"""Entrada de chat y `AgentEvent` — unión discriminada del loop del agente (§10.7).

`AgentEvent` es el contrato exacto de los eventos que emite
`edecan_core.agent.Agent.run_turn` y que `edecan_api` traduce 1:1 a eventos
SSE (`message.delta`, `tool.start`, `tool.end`, `confirmation.required`,
`message.done`, `error`).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, model_validator


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


class PendingToolCall(BaseModel):
    """Tool call original de un lote suspendido por confirmación humana."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class PendingChatMessage(BaseModel):
    """Mensaje LLM serializable que permite continuar sin relanzar el prompt."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]]


class PendingAgentTurn(BaseModel):
    """Foto serializable de un turno suspendido antes de ejecutar un lote.

    Se guarda temporalmente en Redis. Incluye todo lo necesario para ejecutar
    los ``tool_call`` originales una sola vez y continuar el mismo loop LLM,
    sin volver a enviar la orden del usuario como si fuera un turno nuevo.
    """

    version: Literal[1] = 1
    messages: list[PendingChatMessage]
    tool_calls: list[PendingToolCall] = Field(min_length=1)
    operational_tool_names: list[str]
    usage: dict[str, int] = Field(default_factory=dict)
    iteration: int = Field(ge=0)
    accumulated_text: str = ""
    tool_log: list[dict[str, Any]] = Field(default_factory=list)
    system_prompt: str | None = None
    approved_tool_call_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_call_identity(self) -> PendingAgentTurn:
        call_ids = [call.id for call in self.tool_calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("Los tool_call_id de un lote pendiente deben ser únicos.")
        if len(self.operational_tool_names) != len(set(self.operational_tool_names)):
            raise ValueError("Los nombres operativos de un turno deben ser únicos.")
        if not {call.name for call in self.tool_calls}.issubset(
            set(self.operational_tool_names)
        ):
            raise ValueError("Cada tool del lote debe haber sido ofrecida en el turno original.")
        if len(self.approved_tool_call_ids) != len(set(self.approved_tool_call_ids)):
            raise ValueError("Una aprobación pendiente no puede estar duplicada.")
        if not set(self.approved_tool_call_ids).issubset(set(call_ids)):
            raise ValueError("Una aprobación pendiente debe pertenecer al lote original.")
        return self


class ConfirmationRequiredEvent(BaseModel):
    type: Literal["confirmation_required"] = "confirmation_required"
    tool_call_id: str
    name: str
    args: dict
    pending_turn: PendingAgentTurn | None = None


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
