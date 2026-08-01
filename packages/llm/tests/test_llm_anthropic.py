"""Tests de `AnthropicProvider` — sin red real (respx intercepta httpx)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from edecan_llm.anthropic import AnthropicProvider
from edecan_llm.base import ChatMessage, CompletionRequest, ToolSpec
from edecan_llm.errors import LLMError, RateLimitedError

MESSAGES_URL = "https://api.anthropic.com/v1/messages"


def _req(**overrides: object) -> CompletionRequest:
    base: dict[str, object] = dict(
        model="claude-sonnet-4-5",
        system="Eres Edecán, un mayordomo de IA.",
        messages=[ChatMessage(role="user", content="Hola, ¿qué hora es?")],
        max_tokens=512,
    )
    base.update(overrides)
    return CompletionRequest(**base)


@pytest.mark.asyncio
@respx.mock
async def test_complete_texto_normal() -> None:
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Son las 10:00am."}],
                "model": "claude-sonnet-4-5",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 20, "output_tokens": 8},
            },
        )
    )
    provider = AnthropicProvider(api_key="TU_API_KEY_AQUI")
    try:
        response = await provider.complete(_req())

        assert response.text == "Son las 10:00am."
        assert response.tool_calls == []
        assert response.stop_reason == "end"
        assert response.usage.input_tokens == 20
        assert response.usage.output_tokens == 8

        request = respx.calls.last.request
        assert request.headers["x-api-key"] == "TU_API_KEY_AQUI"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content)
        assert body["system"] == "Eres Edecán, un mayordomo de IA."
        assert body["messages"] == [{"role": "user", "content": "Hola, ¿qué hora es?"}]
        assert body["stream"] is False
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_complete_con_tool_use() -> None:
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg_2",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Voy a revisar tu agenda."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "agenda_eventos",
                        "input": {"dia": "hoy"},
                    },
                ],
                "model": "claude-sonnet-4-5",
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 30, "output_tokens": 12},
            },
        )
    )
    provider = AnthropicProvider(api_key="TU_API_KEY_AQUI")
    try:
        tools = [
            ToolSpec(
                name="agenda_eventos",
                description="Lista eventos",
                input_schema={"type": "object"},
            )
        ]
        response = await provider.complete(_req(tools=tools))

        assert response.text == "Voy a revisar tu agenda."
        assert response.stop_reason == "tool_use"
        assert len(response.tool_calls) == 1
        call = response.tool_calls[0]
        assert call.id == "toolu_1"
        assert call.name == "agenda_eventos"
        assert call.arguments == {"dia": "hoy"}

        body = json.loads(respx.calls.last.request.content)
        assert body["tools"] == [
            {
                "name": "agenda_eventos",
                "description": "Lista eventos",
                "input_schema": {"type": "object"},
            }
        ]
    finally:
        await provider.aclose()


SSE_BODY = (
    "event: message_start\n"
    'data: {"type":"message_start","message":{"id":"msg_3",'
    '"usage":{"input_tokens":15,"output_tokens":1}}}\n\n'
    "event: content_block_start\n"
    'data: {"type":"content_block_start","index":0,'
    '"content_block":{"type":"text","text":""}}\n\n'
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"Hola"}}\n\n'
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":" mundo"}}\n\n'
    "event: content_block_stop\n"
    'data: {"type":"content_block_stop","index":0}\n\n'
    "event: message_delta\n"
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    '"usage":{"output_tokens":2}}\n\n'
    "event: message_stop\n"
    'data: {"type":"message_stop"}\n\n'
)


@pytest.mark.asyncio
@respx.mock
async def test_stream_de_texto() -> None:
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=SSE_BODY
        )
    )
    provider = AnthropicProvider(api_key="TU_API_KEY_AQUI")
    try:
        chunks = [chunk async for chunk in provider.stream(_req())]

        text_chunks = [c for c in chunks if c.type == "text"]
        assert [c.text for c in text_chunks] == ["Hola", " mundo"]

        usage_chunks = [c for c in chunks if c.type == "usage"]
        assert usage_chunks[-1].usage is not None
        assert usage_chunks[-1].usage.input_tokens == 15
        assert usage_chunks[-1].usage.output_tokens == 2

        assert chunks[-1].type == "stop"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_stream_tool_use() -> None:
    sse = (
        'data: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"tool_use","id":"toolu_9","name":"agenda_eventos"}}\n\n'
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":"{\\"dia\\":"}}\n\n'
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":"\\"hoy\\"}"}}\n\n'
        'data: {"type":"content_block_stop","index":0}\n\n'
        'data: {"type":"message_stop"}\n\n'
    )
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse)
    )
    provider = AnthropicProvider(api_key="TU_API_KEY_AQUI")
    try:
        chunks = [chunk async for chunk in provider.stream(_req())]
        tool_chunks = [c for c in chunks if c.type == "tool_call"]
        assert len(tool_chunks) == 1
        assert tool_chunks[0].tool_call is not None
        assert tool_chunks[0].tool_call.id == "toolu_9"
        assert tool_chunks[0].tool_call.name == "agenda_eventos"
        assert tool_chunks[0].tool_call.arguments == {"dia": "hoy"}
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_retry_ante_429_y_luego_exito() -> None:
    route = respx.post(MESSAGES_URL)
    route.side_effect = [
        httpx.Response(
            429,
            headers={"retry-after": "0"},
            json={
                "type": "error",
                "error": {"type": "rate_limit_error", "message": "rate limited"},
            },
        ),
        httpx.Response(
            200,
            json={
                "id": "msg_4",
                "content": [{"type": "text", "text": "Ya volví."}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
        ),
    ]
    provider = AnthropicProvider(api_key="TU_API_KEY_AQUI", retry_base_delay=0.0)
    try:
        response = await provider.complete(_req())

        assert response.text == "Ya volví."
        assert route.call_count == 2
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_retry_se_agota_lanza_rate_limited_error() -> None:
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
            429, json={"type": "error", "error": {"message": "rate limited"}}
        )
    )
    provider = AnthropicProvider(api_key="TU_API_KEY_AQUI", max_retries=3, retry_base_delay=0.0)
    try:
        with pytest.raises(RateLimitedError):
            await provider.complete(_req())
        assert route.call_count == 3
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_error_4xx_no_reintenta() -> None:
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
            401, json={"type": "error", "error": {"message": "invalid api key"}}
        )
    )
    provider = AnthropicProvider(api_key="TU_API_KEY_AQUI", retry_base_delay=0.0)
    try:
        with pytest.raises(LLMError) as excinfo:
            await provider.complete(_req())
        assert route.call_count == 1
        assert "401" in str(excinfo.value)
    finally:
        await provider.aclose()
