"""Contratos de herramientas del agente, compartidos entre `edecan_llm`,
`edecan_core` y `edecan_api` (§10.5, §10.6, §10.7)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    """Descripción de una herramienta tal como se envía al proveedor LLM."""

    name: str
    description: str
    input_schema: dict


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
