"""Tests de `OpenAICompatProvider` — sin red real (respx intercepta httpx)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from edecan_llm.base import ChatMessage, CompletionRequest, ToolSpec
from edecan_llm.errors import RateLimitedError
from edecan_llm.openai_compat import OpenAICompatProvider

CHAT_URL = "https://api.openai.com/v1/chat/completions"


def _req(**overrides: object) -> CompletionRequest:
    base: dict[str, object] = dict(
        model="gpt-4o-mini",
        system="Eres Edecán.",
        messages=[ChatMessage(role="user", content="¿Qué hora es?")],
        max_tokens=256,
    )
    base.update(overrides)
    return CompletionRequest(**base)


@pytest.mark.asyncio
@respx.mock
async def test_mapeo_completion_normal() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl_1",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Son las 10:00am."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 18, "completion_tokens": 7, "total_tokens": 25},
            },
        )
    )
    provider = OpenAICompatProvider(base_url="https://api.openai.com/v1", api_key="TU_API_KEY_AQUI")
    try:
        response = await provider.complete(_req())

        assert response.text == "Son las 10:00am."
        assert response.stop_reason == "end"
        assert response.usage.input_tokens == 18
        assert response.usage.output_tokens == 7

        request = respx.calls.last.request
        assert request.headers["authorization"] == "Bearer TU_API_KEY_AQUI"
        body = json.loads(request.content)
        assert body["messages"][0] == {"role": "system", "content": "Eres Edecán."}
        assert body["messages"][1] == {"role": "user", "content": "¿Qué hora es?"}
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_mapeo_tool_calls_de_respuesta() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl_2",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "agenda_eventos",
                                        "arguments": '{"dia": "hoy"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 22, "completion_tokens": 9, "total_tokens": 31},
            },
        )
    )
    tools = [
        ToolSpec(
            name="agenda_eventos", description="Lista eventos", input_schema={"type": "object"}
        )
    ]
    provider = OpenAICompatProvider(base_url="https://api.openai.com/v1", api_key="TU_API_KEY_AQUI")
    try:
        response = await provider.complete(_req(tools=tools))

        assert response.stop_reason == "tool_use"
        assert len(response.tool_calls) == 1
        call = response.tool_calls[0]
        assert call.id == "call_1"
        assert call.name == "agenda_eventos"
        assert call.arguments == {"dia": "hoy"}

        body = json.loads(respx.calls.last.request.content)
        assert body["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "agenda_eventos",
                    "description": "Lista eventos",
                    "parameters": {"type": "object"},
                },
            }
        ]
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_mapeo_mensaje_tool_result_saliente() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "Listo."}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    )
    provider = OpenAICompatProvider(base_url="https://api.openai.com/v1", api_key="TU_API_KEY_AQUI")
    try:
        req = _req(
            messages=[
                ChatMessage(role="user", content="Agenda algo"),
                ChatMessage(
                    role="assistant",
                    content=[
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "agenda_eventos",
                            "input": {"dia": "hoy"},
                        }
                    ],
                ),
                ChatMessage(
                    role="tool",
                    content=[{"type": "tool_result", "tool_use_id": "toolu_1", "content": "hecho"}],
                ),
            ]
        )
        await provider.complete(req)

        body = json.loads(respx.calls.last.request.content)
        # índice 0 = system (de req.system), 1 = user, 2 = assistant tool_use, 3 = tool result
        assistant_msg = body["messages"][2]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "agenda_eventos"
        assert json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"]) == {"dia": "hoy"}

        tool_msg = body["messages"][3]
        assert tool_msg == {"role": "tool", "tool_call_id": "toolu_1", "content": "hecho"}
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_mapeo_tool_result_con_llamadas_paralelas() -> None:
    """`edecan_core.agent.Agent` empaqueta los resultados de TODAS las tool
    calls de una vuelta (tool use paralelo) en un solo `ChatMessage(role="tool")`
    con un bloque `tool_result` por cada `tool_use_id` — cada bloque debe
    traducirse a su propio mensaje `tool` de OpenAI (uno por `tool_call_id`),
    no solo el primero.
    """
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "Listo."}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    )
    provider = OpenAICompatProvider(base_url="https://api.openai.com/v1", api_key="TU_API_KEY_AQUI")
    try:
        req = _req(
            messages=[
                ChatMessage(role="user", content="Agenda algo y busca el clima"),
                ChatMessage(
                    role="assistant",
                    content=[
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "agenda_eventos",
                            "input": {"dia": "hoy"},
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_2",
                            "name": "clima",
                            "input": {"ciudad": "CDMX"},
                        },
                    ],
                ),
                ChatMessage(
                    role="tool",
                    content=[
                        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "hecho"},
                        {"type": "tool_result", "tool_use_id": "toolu_2", "content": "soleado"},
                    ],
                ),
            ]
        )
        await provider.complete(req)

        body = json.loads(respx.calls.last.request.content)
        # índice 0 = system, 1 = user, 2 = assistant tool_use, 3 y 4 = un
        # mensaje "tool" por cada tool_call_id referenciado en el índice 2.
        assistant_msg = body["messages"][2]
        assert [tc["id"] for tc in assistant_msg["tool_calls"]] == ["toolu_1", "toolu_2"]

        assert body["messages"][3] == {
            "role": "tool",
            "tool_call_id": "toolu_1",
            "content": "hecho",
        }
        assert body["messages"][4] == {
            "role": "tool",
            "tool_call_id": "toolu_2",
            "content": "soleado",
        }
        assert len(body["messages"]) == 5
    finally:
        await provider.aclose()


SSE_BODY = (
    'data: {"choices":[{"index":0,"delta":{"content":"Hola"}}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{"content":" mundo"}}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{}}],'
    '"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
    "data: [DONE]\n\n"
)


@pytest.mark.asyncio
@respx.mock
async def test_streaming_de_texto() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=SSE_BODY
        )
    )
    provider = OpenAICompatProvider(base_url="https://api.openai.com/v1", api_key="TU_API_KEY_AQUI")
    try:
        chunks = [chunk async for chunk in provider.stream(_req())]

        text = "".join(c.text for c in chunks if c.type == "text" and c.text)
        assert text == "Hola mundo"
        assert chunks[-1].type == "stop"

        usage_chunks = [c for c in chunks if c.type == "usage"]
        assert usage_chunks[0].usage is not None
        assert usage_chunks[0].usage.input_tokens == 5
        assert usage_chunks[0].usage.output_tokens == 2
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_retry_ante_5xx_y_luego_exito() -> None:
    route = respx.post(CHAT_URL)
    route.side_effect = [
        httpx.Response(503, text="service unavailable"),
        httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        ),
    ]
    provider = OpenAICompatProvider(
        base_url="https://api.openai.com/v1", api_key="TU_API_KEY_AQUI", retry_base_delay=0.0
    )
    try:
        response = await provider.complete(_req())
        assert response.text == "ok"
        assert route.call_count == 2
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_retry_se_agota_lanza_rate_limited_error() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(429, text="rate limited"))
    provider = OpenAICompatProvider(
        base_url="https://api.openai.com/v1", api_key="TU_API_KEY_AQUI", retry_base_delay=0.0
    )
    try:
        with pytest.raises(RateLimitedError):
            await provider.complete(_req())
    finally:
        await provider.aclose()
