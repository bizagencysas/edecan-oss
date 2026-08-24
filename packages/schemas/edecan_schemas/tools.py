"""Contratos de herramientas del agente, compartidos entre `edecan_llm`,
`edecan_core` y `edecan_api` (§10.5, §10.6, §10.7)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    """Descripción de una herramienta tal como se envía al proveedor LLM."""

    name: str
    description: str
    input_schema: dict


class ToolMetadata(BaseModel):
    """Metadatos declarativos de una herramienta (§6 del Master Directive).

    No se envían al modelo: viven en el registry para que el orquestador,
    el router de tools y el budget manager puedan decidir sin invocar al LLM.
    Todos los campos tienen default para que las 112+ tools existentes
    funcionen sin cambios.
    """

    category: Literal[
        "read", "write", "external_comm", "destructive", "admin", "utility",
        "research", "creative", "vision", "voice", "code", "browser",
    ] = "utility"
    risk_level: Literal["none", "low", "medium", "high", "critical"] = "low"
    latency_class: Literal["instant", "interactive", "slow", "background"] = "interactive"
    cost_class: Literal["free", "cheap", "moderate", "expensive"] = "free"
    timeout_seconds: float = 60.0
    retry_policy: Literal["none", "retry_safe", "retry_idempotent"] = "none"
    idempotent: bool = False
    requires_confirmation: bool = False
    supports_streaming: bool = False


class ToolCallData(BaseModel):
    """Invocación de una herramienta solicitada por el modelo."""

    id: str
    name: str
    arguments: dict = Field(default_factory=dict)


class ToolResultData(BaseModel):
    """Resultado serializable de ejecutar una herramienta.

    Espeja `edecan_core.agent.ToolResult` (dataclass) en forma de modelo
    Pydantic, para poder persistirlo/enviarlo por la API sin importar
    `edecan_core` desde capas que no deberían depender de él.
    """

    content: str
    data: dict | None = None
    requires_confirmation: bool = False
