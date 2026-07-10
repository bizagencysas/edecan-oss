"""Adaptador Amazon Bedrock — stub v1 (`ARCHITECTURE.md` §7, §10.6).

Bedrock queda mencionado en la arquitectura como proveedor LLM regional
opcional, pero no es prioridad para v1. Este stub deja la interfaz
`LLMProvider` implementada (para que `LLMRouter` u otro código pueda
referenciarlo por tipo) sin pretender una integración real todavía.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from .base import CompletionRequest, CompletionResponse, LLMProvider, StreamChunk


class BedrockProvider(LLMProvider):
    """Proveedor Amazon Bedrock. Sin implementar — ver TODO(v2) más abajo."""

    name = "bedrock"

    def __init__(self, *, region: str = "us-east-1", model_id: str | None = None) -> None:
        self._region = region
        self._model_id = model_id

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        # TODO(v2): integrar boto3/aioboto3 `bedrock-runtime` `invoke_model`
        # (traducir CompletionRequest -> payload del modelo Bedrock elegido,
        # y la respuesta -> CompletionResponse, igual que en anthropic.py).
        raise NotImplementedError(
            "BedrockProvider.complete no está implementado todavía (v2). "
            "Usa AnthropicProvider u OpenAICompatProvider mientras tanto."
        )

    def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]:
        # TODO(v2): integrar boto3/aioboto3
        # `bedrock-runtime` `invoke_model_with_response_stream` y traducir su
        # stream de eventos a `StreamChunk`, igual que en anthropic.py.
        raise NotImplementedError(
            "BedrockProvider.stream no está implementado todavía (v2). "
            "Usa AnthropicProvider u OpenAICompatProvider mientras tanto."
        )
