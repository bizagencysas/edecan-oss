"""Criterio de `edecan-usage-tokens-cacheados`.

Workers AI (y OpenAI, y cualquier endpoint compatible moderno) reporta
`usage.prompt_tokens_details.cached_tokens`: la parte del prompt que llegó
desde la caché de prefijo y que se cobra mucho más barato —en Kimi K2.7,
0,19 USD/MTok contra 0,95. Hoy `Usage` no tiene dónde guardarlo, el adaptador
lo tira y `estimate` cobra todo el prompt a precio lleno.

Falla hoy: `Usage` no acepta `cached_input_tokens`. Sin red: `httpx.MockTransport`.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
from edecan_llm.base import Usage
from edecan_llm.costs import estimate
from edecan_llm.openai_compat import OpenAICompatProvider

_MODELO = "@cf/moonshotai/kimi-k2.7-code"

_RESPUESTA = {
    "choices": [{"message": {"role": "assistant", "content": "listo"}, "finish_reason": "stop"}],
    "usage": {
        "prompt_tokens": 10_000,
        "completion_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 8_000},
    },
}

_CHUNKS = (
    {"choices": [{"delta": {"content": "listo"}}]},
    {
        "choices": [],
        "usage": {
            "prompt_tokens": 10_000,
            "completion_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 8_000},
        },
    },
)


def _cliente(cuerpo: httpx.Response) -> OpenAICompatProvider:
    transporte = httpx.MockTransport(lambda _req: cuerpo)
    return OpenAICompatProvider(
        "https://api.invalido.test/v1",
        "clave-de-prueba",
        http_client=httpx.AsyncClient(
            transport=transporte, base_url="https://api.invalido.test/v1"
        ),
    )


async def _uso_sin_stream() -> Usage:
    provider = _cliente(httpx.Response(200, json=_RESPUESTA))
    try:
        respuesta = await provider.complete(_peticion())
    finally:
        await provider.aclose()
    return respuesta.usage


async def _uso_con_stream() -> Usage | None:
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in _CHUNKS) + "data: [DONE]\n\n"
    provider = _cliente(httpx.Response(200, text=sse))
    ultimo: Usage | None = None
    try:
        async for chunk in provider.stream(_peticion()):
            if chunk.type == "usage" and chunk.usage is not None:
                ultimo = chunk.usage
    finally:
        await provider.aclose()
    return ultimo


def _peticion():
    from edecan_llm.base import ChatMessage, CompletionRequest

    return CompletionRequest(model=_MODELO, messages=[ChatMessage(role="user", content="hola")])


def main() -> int:
    if "cached_input_tokens" not in Usage.model_fields:
        print("Usage no declara el campo cached_input_tokens")
        return 1
    uso = Usage(input_tokens=10, output_tokens=2, cached_input_tokens=6)
    if uso.cached_input_tokens != 6:
        print(f"Usage.cached_input_tokens quedó en {uso.cached_input_tokens!r}")
        return 1
    if Usage().cached_input_tokens != 0:
        print("Usage().cached_input_tokens debe valer 0 por omisión")
        return 1

    medido = asyncio.run(_uso_sin_stream())
    if (medido.input_tokens, medido.output_tokens) != (10_000, 120):
        print(f"el adaptador rompió el conteo básico: {medido!r}")
        return 1
    if medido.cached_input_tokens != 8_000:
        print(f"sin streaming, cached_input_tokens quedó en {medido.cached_input_tokens}")
        return 1

    en_stream = asyncio.run(_uso_con_stream())
    if en_stream is None or en_stream.cached_input_tokens != 8_000:
        print(f"en streaming el uso llegó como {en_stream!r}")
        return 1

    # Precio real medido contra la API el 27-07-2026: 0,95 / 4,00 / 0,19.
    tabla = {_MODELO: (0.95, 4.00)}
    cache = {_MODELO: 0.19}
    completo = estimate(_MODELO, Usage(input_tokens=1_000_000, output_tokens=0), costos=tabla)
    if abs(completo - 0.95) > 1e-9:
        print(f"un millón de tokens de entrada sin caché costó {completo}, esperado 0,95")
        return 1
    con_cache = estimate(
        _MODELO,
        Usage(input_tokens=1_000_000, output_tokens=0, cached_input_tokens=200_000),
        costos=tabla,
        costos_cache=cache,
    )
    esperado = 0.8 * 0.95 + 0.2 * 0.19
    if abs(con_cache - esperado) > 1e-9:
        print(f"con 200k tokens cacheados costó {con_cache}, esperado {esperado}")
        return 1
    sin_precio_de_cache = estimate(
        _MODELO,
        Usage(input_tokens=1_000_000, output_tokens=0, cached_input_tokens=200_000),
        costos=tabla,
    )
    if abs(sin_precio_de_cache - 0.95) > 1e-9:
        print(
            "sin precio de caché declarado, los tokens cacheados deben cobrarse al precio "
            f"de entrada normal; costó {sin_precio_de_cache}"
        )
        return 1

    print("ok: los tokens cacheados se leen del proveedor y abaratan la estimación")
    return 0


if __name__ == "__main__":
    sys.exit(main())
