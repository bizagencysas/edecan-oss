"""Entrada de chat y `AgentEvent` — unión discriminada del loop del agente (§10.7).

`AgentEvent` es el contrato exacto de los eventos que emite
`edecan_core.agent.Agent.run_turn` y que `edecan_api` traduce 1:1 a eventos
SSE (`message.delta`, `tool.start`, `tool.end`, `confirmation.required`,
`message.done`, `error`).
"""

from __future__ import annotations

import ipaddress
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter, model_validator


class ChatMessageIn(BaseModel):
    """Cuerpo de `POST /v1/conversations/{id}/messages`."""

    text: str = Field(default="", max_length=50_000)
    attachments: list[UUID] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_content(self) -> ChatMessageIn:
        if not self.text.strip() and not self.attachments:
            raise ValueError("el mensaje necesita texto o al menos un archivo")
        if len(self.attachments) != len(set(self.attachments)):
            raise ValueError("los archivos adjuntos no pueden repetirse")
        return self


class PendingConfirmationOut(BaseModel):
    """Acción peligrosa que la UI puede reconstruir al reabrir un chat.

    Deliberadamente contiene solo el mismo contrato público emitido por
    ``confirmation.required``. El estado serializado usado para reanudar el
    agente nunca forma parte de esta respuesta.
    """

    tool_call_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    args: dict[str, Any] = Field(default_factory=dict)


class TextDeltaEvent(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolStartEvent(BaseModel):
    type: Literal["tool_start"] = "tool_start"
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=255)
    name: str
    args: dict


class ToolProgressEvent(BaseModel):
    """Latido público mientras una herramienta continúa trabajando.

    No contiene razonamiento interno ni argumentos potencialmente sensibles:
    solo identidad de la ejecución, segundos transcurridos y un estado humano
    que los clientes pueden mostrar sin inventar progreso.
    """

    type: Literal["tool_progress"] = "tool_progress"
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=255)
    name: str
    elapsed_seconds: int = Field(default=0, ge=0)
    message: str = Field(default="Trabajando", max_length=255)


class ArtifactRef(BaseModel):
    """Archivo privado creado por una tool y descargable por su dueño."""

    file_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    mime: str | None = Field(default=None, max_length=255)


def _safe_public_url(value: str) -> str:
    """Acepta solo HTTP(S) absoluto y sin credenciales embebidas."""

    clean = value.strip()
    parsed = urlsplit(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("la URL debe ser http(s) absoluta")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("la URL no puede contener credenciales")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("la URL debe apuntar a un host público")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("la URL no puede apuntar a una red privada o reservada")
    return clean


class OpenURLAction(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$")
    label: str = Field(min_length=1, max_length=80)
    action: Literal["open_url"] = "open_url"
    url: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def validate_url(self) -> OpenURLAction:
        self.url = _safe_public_url(self.url)
        return self


class OpenScreenAction(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$")
    label: str = Field(min_length=1, max_length=80)
    action: Literal["open_screen"] = "open_screen"
    screen: Literal[
        "assistant",
        "create",
        "remote",
        "activity",
        "settings",
        "travel",
        "orders",
        "files",
        "skills",
    ]


class PrefillMessageAction(BaseModel):
    """Sugerencia visible: rellena el compositor, nunca ejecuta por sí sola."""

    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._:-]+$")
    label: str = Field(min_length=1, max_length=80)
    action: Literal["prefill_message"] = "prefill_message"
    message: str = Field(min_length=1, max_length=2000)


ChatAction = Annotated[
    OpenURLAction | OpenScreenAction | PrefillMessageAction,
    Field(discriminator="action"),
]


class ChatBlockBase(BaseModel):
    """Campos comunes para evolución y fallback de clientes antiguos."""

    schema_version: Literal[1] = 1
    fallback_text: str | None = Field(default=None, max_length=1000)


class MediaBlock(ChatBlockBase):
    type: Literal["media"] = "media"
    media_kind: Literal["image", "video", "audio"]
    artifact: ArtifactRef
    alt: str = Field(default="", max_length=1000)
    caption: str | None = Field(default=None, max_length=500)


class LinkPreviewBlock(ChatBlockBase):
    type: Literal["link_preview"] = "link_preview"
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=1000)
    site_name: str | None = Field(default=None, max_length=120)
    observed_at: str | None = Field(default=None, max_length=80)
    source_mode: Literal["demo", "live", "unknown"] = "unknown"
    actions: list[ChatAction] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_url(self) -> LinkPreviewBlock:
        self.url = _safe_public_url(self.url)
        return self


class FlightCardBlock(ChatBlockBase):
    type: Literal["flight"] = "flight"
    offer_id: str = Field(min_length=1, max_length=200)
    airline: str = Field(min_length=1, max_length=200)
    origin: str = Field(min_length=2, max_length=12)
    destination: str = Field(min_length=2, max_length=12)
    departure: str | None = Field(default=None, max_length=80)
    arrival: str | None = Field(default=None, max_length=80)
    stops: int = Field(default=0, ge=0, le=12)
    price: str = Field(min_length=1, max_length=40)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    source_mode: Literal["demo", "live", "unknown"] = "unknown"
    provider: str | None = Field(default=None, max_length=120)
    observed_at: str | None = Field(default=None, max_length=80)
    expires_at: str | None = Field(default=None, max_length=80)
    taxes: str | None = Field(default=None, max_length=40)
    cancellation: str | None = Field(default=None, max_length=500)
    actions: list[ChatAction] = Field(default_factory=list, max_length=3)


class HotelCardBlock(ChatBlockBase):
    type: Literal["hotel"] = "hotel"
    offer_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    city: str = Field(min_length=1, max_length=120)
    checkin: str | None = Field(default=None, max_length=40)
    checkout: str | None = Field(default=None, max_length=40)
    rating: str | None = Field(default=None, max_length=20)
    price: str = Field(min_length=1, max_length=40)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    source_mode: Literal["demo", "live", "unknown"] = "unknown"
    provider: str | None = Field(default=None, max_length=120)
    observed_at: str | None = Field(default=None, max_length=80)
    expires_at: str | None = Field(default=None, max_length=80)
    taxes: str | None = Field(default=None, max_length=40)
    cancellation: str | None = Field(default=None, max_length=500)
    actions: list[ChatAction] = Field(default_factory=list, max_length=3)


ChatBlock = Annotated[
    MediaBlock | LinkPreviewBlock | FlightCardBlock | HotelCardBlock,
    Field(discriminator="type"),
]
"""Bloque rico allowlisted que web, iOS y Android renderizan con el mismo contrato."""

ChatBlockAdapter: TypeAdapter[ChatBlock] = TypeAdapter(ChatBlock)


class ToolEndEvent(BaseModel):
    type: Literal["tool_end"] = "tool_end"
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=255)
    name: str
    result_preview: str
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=20)
    blocks_version: Literal[1] = 1
    blocks: list[ChatBlock] = Field(default_factory=list, max_length=30)
    mission_id: UUID | None = None
    """Misión asíncrona creada por la herramienta, si existe.

    Permite que el mismo chat siga mostrando un trabajo delegado después de
    cerrar el stream HTTP, sin exponer datos internos de ``ToolResult.data``.
    """


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
        if not {call.name for call in self.tool_calls}.issubset(set(self.operational_tool_names)):
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
    | ToolProgressEvent
    | ToolEndEvent
    | ConfirmationRequiredEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="type"),
]
"""Unión discriminada por `type` del contrato público de ejecución."""

AgentEventAdapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)
"""`TypeAdapter` listo para validar/parsear un `dict` (p. ej. JSON de un evento SSE)
hacia la variante concreta de `AgentEvent` que corresponda."""
