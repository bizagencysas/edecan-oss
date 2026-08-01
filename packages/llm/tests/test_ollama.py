"""Tests de `OllamaProvider` — sin red real (respx intercepta httpx)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from edecan_llm.base import ChatMessage, CompletionRequest, ToolSpec
from edecan_llm.errors import LLMError
from edecan_llm.ollama import OllamaProvider

CHAT_URL = "http://localhost:11434/api/chat"


def _req(**overrides: object) -> CompletionRequest:
    base: dict[str, object] = dict(
        model="llama3.1:8b",
        system="Eres Edecán.",
        messages=[ChatMessage(role="user", content="¿Qué hora es?")],
    )
    base.update(overrides)
    return CompletionRequest(**base)


@pytest.mark.asyncio
async def test_base_url_trailing_slash_se_recorta() -> None:
    provider = OllamaProvider(base_url="http://otronodo:11434/")
    try:
        assert provider._base_url == "http://otronodo:11434"  # type: ignore[attr-defined]
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_base_url_por_defecto() -> None:
    provider = OllamaProvider()
    try:
        assert provider._base_url == "http://localhost:11434"  # type: ignore[attr-defined]
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_complete_texto_normal() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "Son las 10:00am."},
                "done": True,
                "prompt_eval_count": 12,
                "eval_count": 5,
            },
        )
    )
    provider = OllamaProvider()
    try:
        response = await provider.complete(_req())

        assert response.text == "Son las 10:00am."
        assert response.tool_calls == []
        assert response.stop_reason == "end"
        assert response.usage.input_tokens == 12
        assert response.usage.output_tokens == 5

        body = json.loads(respx.calls.last.request.content)
        assert body["model"] == "llama3.1:8b"
        assert body["stream"] is False
        assert body["messages"][0] == {"role": "system", "content": "Eres Edecán."}
        assert body["messages"][1] == {"role": "user", "content": "¿Qué hora es?"}
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_complete_modelo_por_defecto_cuando_req_no_trae_modelo() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200, json={"message": {"role": "assistant", "content": "ok"}, "done": True}
        )
    )
    provider = OllamaProvider(model_principal="llama3.1:8b")
    try:
        await provider.complete(_req(model=""))

        body = json.loads(respx.calls.last.request.content)
        assert body["model"] == "llama3.1:8b"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_complete_mapea_tools_en_el_body() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200, json={"message": {"role": "assistant", "content": "ok"}, "done": True}
        )
    )
    tools = [
        ToolSpec(
            name="agenda_eventos",
            description="Lista eventos",
            input_schema={"type": "object"},
        )
    ]
    provider = OllamaProvider()
    try:
        await provider.complete(_req(tools=tools))

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
async def test_complete_sin_tools_no_agrega_la_clave() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200, json={"message": {"role": "assistant", "content": "ok"}, "done": True}
        )
    )
    provider = OllamaProvider()
    try:
        await provider.complete(_req())

        body = json.loads(respx.calls.last.request.content)
        assert "tools" not in body
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_complete_mapea_tool_calls_de_respuesta_generando_id() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "agenda_eventos", "arguments": {"dia": "hoy"}}}
                    ],
                },
                "done": True,
                "prompt_eval_count": 20,
                "eval_count": 8,
            },
        )
    )
    provider = OllamaProvider()
    try:
        response = await provider.complete(_req())

        assert response.stop_reason == "tool_use"
        assert len(response.tool_calls) == 1
        call = response.tool_calls[0]
        assert call.name == "agenda_eventos"
        assert call.arguments == {"dia": "hoy"}
        assert call.id  # Ollama no dio id propio: se generó uno con uuid4
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_complete_tool_call_con_id_y_arguments_como_string_json() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {"name": "clima", "arguments": '{"ciudad": "CDMX"}'},
                        }
                    ],
                },
                "done": True,
            },
        )
    )
    provider = OllamaProvider()
    try:
        response = await provider.complete(_req())

        assert response.tool_calls[0].id == "call_1"
        assert response.tool_calls[0].arguments == {"ciudad": "CDMX"}
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_mensaje_tool_result_se_traduce_a_role_tool() -> None:
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200, json={"message": {"role": "assistant", "content": "Listo."}, "done": True}
        )
    )
    provider = OllamaProvider()
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
                    content=[
                        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "hecho"}
                    ],
                ),
            ]
        )
        await provider.complete(req)

        body = json.loads(respx.calls.last.request.content)
        # índice 0 = system, 1 = user, 2 = assistant tool_use, 3 = tool result
        assistant_msg = body["messages"][2]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "agenda_eventos"
        assert assistant_msg["tool_calls"][0]["function"]["arguments"] == {"dia": "hoy"}

        tool_msg = body["messages"][3]
        assert tool_msg == {"role": "tool", "content": "hecho"}
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_status_error_lanza_llm_error_con_status_code() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500, text="boom"))
    provider = OllamaProvider()
    try:
        with pytest.raises(LLMError) as excinfo:
            await provider.complete(_req())
        assert excinfo.value.status_code == 500
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_connect_error_da_mensaje_claro_en_complete() -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("refused"))
    provider = OllamaProvider()
    try:
        with pytest.raises(LLMError, match="Ollama no está corriendo"):
            await provider.complete(_req())
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_connect_error_da_mensaje_claro_en_stream() -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("refused"))
    provider = OllamaProvider()
    try:
        with pytest.raises(LLMError, match="Ollama no está corriendo"):
            async for _ in provider.stream(_req()):
                pass
    finally:
        await provider.aclose()


STREAM_LINES = "\n".join(
    [
        json.dumps({"message": {"role": "assistant", "content": "Hola"}, "done": False}),
        json.dumps({"message": {"role": "assistant", "content": " mundo"}, "done": False}),
        json.dumps(
            {
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "prompt_eval_count": 9,
                "eval_count": 3,
            }
        ),
    ]
)


@pytest.mark.asyncio
@respx.mock
async def test_streaming_de_texto() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, content=STREAM_LINES + "\n"))
    provider = OllamaProvider()
    try:
        chunks = [chunk async for chunk in provider.stream(_req())]

        text = "".join(c.text for c in chunks if c.type == "text" and c.text)
        assert text == "Hola mundo"
        assert chunks[-1].type == "stop"

        usage_chunks = [c for c in chunks if c.type == "usage"]
        assert usage_chunks[-1].usage is not None
        assert usage_chunks[-1].usage.input_tokens == 9
        assert usage_chunks[-1].usage.output_tokens == 3
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_streaming_tool_call() -> None:
    lines = "\n".join(
        [
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "agenda_eventos", "arguments": {"dia": "hoy"}}}
                        ],
                    },
                    "done": False,
                }
            ),
            json.dumps(
                {
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "prompt_eval_count": 4,
                    "eval_count": 2,
                }
            ),
        ]
    )
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, content=lines + "\n"))
    provider = OllamaProvider()
    try:
        chunks = [chunk async for chunk in provider.stream(_req())]

        tool_chunks = [c for c in chunks if c.type == "tool_call"]
        assert len(tool_chunks) == 1
        assert tool_chunks[0].tool_call is not None
        assert tool_chunks[0].tool_call.name == "agenda_eventos"
        assert tool_chunks[0].tool_call.id
        assert chunks[-1].type == "stop"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_streaming_status_error() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500, text="boom"))
    provider = OllamaProvider()
    try:
        with pytest.raises(LLMError) as excinfo:
            async for _ in provider.stream(_req()):
                pass
        assert excinfo.value.status_code == 500
    finally:
        await provider.aclose()
