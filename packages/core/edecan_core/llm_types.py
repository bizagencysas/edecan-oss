"""Tipos mínimos que `Agent` usa para hablar con el `llm_router` inyectado.

`edecan_core` NO declara `edecan_llm` como dependencia (ver README / §10.1:
"Los tests NO importan paquetes hermanos"): `Agent` recibe `llm_router` como
`Any` y solo espera de él la forma descrita en `edecan_core.agent` (un
`.resolve(alias, flags) -> (provider, model)` cuyo `provider.stream(req)`
produce trozos con `.type`/`.text`/`.tool_call`/`.usage`).

`ChatMessage`/`CompletionRequest` de este módulo tienen la MISMA forma que
`edecan_llm.base.ChatMessage`/`CompletionRequest` (ARCHITECTURE.md §10.6) a
propósito: son *duck-typed* — un `LLMProvider` real de `edecan_llm` solo les
hace acceso a atributos (nunca `isinstance`), así que los acepta sin
problema. `CompletionRequest.tools` recibe directamente los
`edecan_schemas.ToolSpec` que devuelve `ToolRegistry.specs()` (mismos
atributos `name`/`description`/`input_schema` que espera cada proveedor).

Usar aquí los tipos reales de `edecan_llm` en vez de estos rompería esa
independencia: un `edecan_llm.base.CompletionRequest` (Pydantic) validaría
estrictamente `tools: list[edecan_llm.base.ToolSpec]` y rechazaría los
`edecan_schemas.ToolSpec` que le pasa el registry (misma forma, pero no la
misma clase). Con dataclasses simples (sin validación) esa combinación
funciona tal cual.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class ChatMessage:
    """Un turno de conversación — misma forma que `edecan_llm.base.ChatMessage`.

    `content` sigue la convención de bloques de Anthropic cuando es una lista
    (documentada en `edecan_llm.base`): `{"type": "text", "text": ...}`,
    `{"type": "tool_use", "id", "name", "input"}`,
    `{"type": "tool_result", "tool_use_id", "content"}`.
    """

    role: Role
    content: str | list[dict[str, Any]]


@dataclass(slots=True)
class CompletionRequest:
    """Petición de completion — misma forma que `edecan_llm.base.CompletionRequest`."""

    model: str
    system: str | None = None
    messages: list[ChatMessage] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)
    max_tokens: int = 1024
    temperature: float = 0.7
    metadata: dict[str, Any] = field(default_factory=dict)
