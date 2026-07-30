"""Criterio de `edecan-openai-compat-reasoning-content`.

Los modelos con razonamiento siempre activo (Kimi K2.7 en Workers AI, y todo
el resto que copió esa forma) devuelven el razonamiento en
`message.reasoning_content`, un campo APARTE de `message.content`. Cuando el
presupuesto de salida se agota razonando, llega `content: ""` con
`reasoning_content` lleno: hoy el adaptador devuelve texto vacío y quien
llama no tiene forma de distinguir "el modelo no dijo nada" de "el modelo
gastó todo el turno pensando".

Falla hoy: `CompletionResponse` no tiene dónde poner el razonamiento y el
streaming descarta `delta.reasoning_content`. Sin red: `httpx.MockTransport`.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
from edecan_llm.base import ChatMessage, CompletionRequest, CompletionResponse
from edecan_llm.openai_compat import OpenAICompatProvider

_MODELO = "@cf/moonshotai/kimi-k2.7-code"
_RAZONAMIENTO = "El usuario saluda. No necesito herramientas."


def _peticion() -> CompletionRequest:
    return CompletionRequest(model=_MODELO, messages=[ChatMessage(role="user", content="hola")])


def _provider(respuesta: httpx.Response) -> OpenAICompatProvider:
    transporte = httpx.MockTransport(lambda _req: respuesta)
    return OpenAICompatProvider(
        "https://api.invalido.test/v1",
        "clave-de-prueba",
        http_client=httpx.AsyncClient(
            transport=transporte, base_url="https://api.invalido.test/v1"
        ),
    )


async def _completar(payload: dict) -> CompletionResponse:
    provider = _provider(httpx.Response(200, json=payload))
    try:
        return await provider.complete(_peticion())
    finally:
        await provider.aclose()


async def _stream(chunks: tuple[dict, ...]) -> list[tuple[str, str]]:
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    provider = _provider(httpx.Response(200, text=sse))
    vistos: list[tuple[str, str]] = []
    try:
        async for chunk in provider.stream(_peticion()):
            if chunk.text:
                vistos.append((chunk.type, chunk.text))
    finally:
        await provider.aclose()
    return vistos


def main() -> int:
    if "reasoning" not in CompletionResponse.model_fields:
        print("CompletionResponse no declara el campo reasoning")
        return 1

    con_ambos = asyncio.run(
        _completar(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Hola.",
                            "reasoning_content": _RAZONAMIENTO,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 65},
            }
        )
    )
    if con_ambos.text != "Hola.":
        print(f"el texto visible cambió: {con_ambos.text!r}")
        return 1
    if con_ambos.reasoning != _RAZONAMIENTO:
        print(f"reasoning quedó en {con_ambos.reasoning!r}")
        return 1

    truncado = asyncio.run(
        _completar(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": _RAZONAMIENTO,
                        },
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 32},
            }
        )
    )
    if truncado.text != "":
        print(f"el razonamiento se filtró al texto visible: {truncado.text!r}")
        return 1
    if truncado.reasoning != _RAZONAMIENTO:
        print(f"con content vacío, reasoning quedó en {truncado.reasoning!r}")
        return 1
    if truncado.stop_reason != "max_tokens":
        print(f"finish_reason=length debe mapear a max_tokens, no a {truncado.stop_reason!r}")
        return 1

    sin_razonamiento = asyncio.run(
        _completar(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "Hola."}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 3},
            }
        )
    )
    if sin_razonamiento.reasoning:
        print(f"sin razonamiento el campo debe quedar vacío, no {sin_razonamiento.reasoning!r}")
        return 1

    vistos = asyncio.run(
        _stream(
            (
                {"choices": [{"delta": {"reasoning_content": "pienso..."}}]},
                {"choices": [{"delta": {"content": "Hola."}}]},
            )
        )
    )
    if ("text", "Hola.") not in vistos:
        print(f"el streaming perdió el texto visible: {vistos!r}")
        return 1
    if ("reasoning", "pienso...") not in vistos:
        print(f"el streaming no emitió un chunk de razonamiento: {vistos!r}")
        return 1
    if ("text", "pienso...") in vistos:
        print("el razonamiento se emitió como texto visible en el streaming")
        return 1

    print("ok: el razonamiento llega por su propio canal y no contamina el texto visible")
    return 0


if __name__ == "__main__":
    sys.exit(main())
