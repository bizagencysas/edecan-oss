"""Criterio de `edecan-bedrock-stream-asynciterator`.

`LLMProvider.stream` está anotado `-> AsyncIterator[StreamChunk]` y todos los
adaptadores reales (`anthropic.py`, `ollama.py`, `openai_compat.py`) lo
cumplen: llamar devuelve un iterador asíncrono y los errores salen al iterar.
`BedrockProvider.stream` es un `def` normal que lanza en la LLAMADA, así que
un `async for` protegido con `try/except` alrededor del bucle no atrapa nada
y el tipo declarado es mentira.

Falla hoy: `provider.stream(req)` lanza `NotImplementedError` antes de
devolver nada.
"""

from __future__ import annotations

import asyncio
import inspect
import sys

from edecan_llm.base import CompletionRequest
from edecan_llm.bedrock import BedrockProvider


def _peticion() -> CompletionRequest:
    return CompletionRequest(model="anthropic.claude-3-5-sonnet-20241022-v2:0")


async def _iterar(provider: BedrockProvider) -> str:
    iterador = provider.stream(_peticion())
    if not hasattr(iterador, "__aiter__"):
        return f"stream() devolvió {type(iterador).__name__}, que no es un iterador asíncrono"
    try:
        async for _chunk in iterador:
            return "stream() emitió un chunk: el stub debería seguir sin implementar"
    except NotImplementedError:
        return ""
    return "iterar stream() terminó sin lanzar NotImplementedError"


def main() -> int:
    provider = BedrockProvider()

    if inspect.iscoroutinefunction(provider.stream):
        print("stream() es una corrutina: debe ser un generador asíncrono, como los demás")
        return 1

    try:
        provider.stream(_peticion())
    except NotImplementedError:
        print("stream() lanzó NotImplementedError en la LLAMADA, no al iterar")
        return 1

    problema = asyncio.run(_iterar(provider))
    if problema:
        print(problema)
        return 1

    try:
        asyncio.run(provider.complete(_peticion()))
    except NotImplementedError:
        print("ok: stream() es un iterador asíncrono y complete() sigue sin implementar")
        return 0
    print("complete() dejó de lanzar NotImplementedError: el stub no debía implementarse")
    return 1


if __name__ == "__main__":
    sys.exit(main())
