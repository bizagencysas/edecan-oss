from __future__ import annotations

import json

import httpx
import pytest
from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_llm.workers_ai import WorkersAIProvider


@pytest.mark.asyncio
async def test_reasoning_budget_retry_on_empty_content_length() -> None:
    intentos = 0
    max_tokens_vistos: list[int] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal intentos
        intentos += 1
        body = json.loads(request.content)
        max_tokens_vistos.append(body.get("max_tokens"))

        if intentos == 1:
            # Content vacío y finish_reason length
            return httpx.Response(
                200,
                json={
                    "result": {
                        "choices": [
                            {
                                "message": {
                                    "content": "",
                                    "reasoning_content": "Pensando demasiado tiempo...",
                                },
                                "finish_reason": "length",
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 32},
                    },
                    "success": True,
                },
            )

        # Segundo intento con más presupuesto: devuelve el contenido
        return httpx.Response(
            200,
            json={
                "result": {
                    "choices": [
                        {
                            "message": {
                                "content": "Respuesta completada tras ampliar presupuesto",
                                "reasoning_content": "Pensando...",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 50},
                },
                "success": True,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    provider = WorkersAIProvider(
        account_id="account",
        api_token="token",
        http_client=client,
    )

    req = CompletionRequest(
        model="@cf/zai-org/glm-5.2",
        messages=[ChatMessage(role="user", content="Pregunta compleja")],
        max_tokens=32,
    )

    res = await provider.complete(req)

    assert intentos == 2
    assert len(max_tokens_vistos) == 2
    assert max_tokens_vistos[1] > max_tokens_vistos[0]
    assert max_tokens_vistos[1] == 32 + 512
    assert res.text == "Respuesta completada tras ampliar presupuesto"

    await provider.aclose()
