from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

from edecan_design_studio.llm_bridge import EdecanLLMBridge


async def _post(url: str, token: str, payload: dict) -> tuple[int, dict]:
    parsed = urlparse(url)
    reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
    body = json.dumps(payload).encode()
    writer.write(
        f"POST {parsed.path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        f"Authorization: Bearer {token}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()
    raw = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, response_body = raw.split(b"\r\n\r\n", 1)
    status = int(head.split(b" ", 2)[1])
    return status, json.loads(response_body)


async def test_bridge_uses_edecan_router_without_exposing_provider_key(make_ctx) -> None:
    ctx = make_ctx()
    async with EdecanLLMBridge(ctx) as environment:
        status, payload = await _post(
            environment["FYDESIGN_LLM_BRIDGE_URL"],
            environment["FYDESIGN_LLM_BRIDGE_TOKEN"],
            {"prompt": "crea html", "system": "solo html", "maxTokens": 1200},
        )

    assert status == 200
    assert payload == {"text": "contenido de prueba"}
    alias, _flags, request = ctx.llm.llamadas[0]
    assert alias == "principal"
    assert request.system == "solo html"
    assert request.messages[0].content == "crea html"


async def test_bridge_rejects_wrong_capability_token(make_ctx) -> None:
    ctx = make_ctx()
    async with EdecanLLMBridge(ctx) as environment:
        status, payload = await _post(
            environment["FYDESIGN_LLM_BRIDGE_URL"],
            "incorrecto",
            {"prompt": "no ejecutar"},
        )

    assert status == 401
    assert payload == {"error": "unauthorized"}
    assert ctx.llm.llamadas == []
