"""Tests de `VertexAIProvider` — sin red real (respx) y sin depender del
extra opcional `google-auth` (modo `service_account` se testea inyectando un
`token_provider` fake, tal como pide `ARCHITECTURE.md` §12.c / WP-V3-03
punto 6 — este paquete de tests NO instala `edecan-llm[vertex]`).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from edecan_llm.base import ChatMessage, CompletionRequest, ToolSpec
from edecan_llm.config import LLMProviderConfig
from edecan_llm.errors import LLMError, ProviderDownError
from edecan_llm.vertex import VertexAIProvider

API_KEY_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
)
API_KEY_STREAM_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:streamGenerateContent"
)
SERVICE_ACCOUNT_URL = (
    "https://us-central1-aiplatform.googleapis.com/v1/projects/mi-proyecto/"
    "locations/us-central1/publishers/google/models/gemini-2.5-pro:generateContent"
)


def _req(**overrides: object) -> CompletionRequest:
    base: dict[str, object] = dict(
        model="gemini-2.5-pro",
        system="Eres Edecán.",
        messages=[ChatMessage(role="user", content="¿Qué hora es?")],
    )
    base.update(overrides)
    return CompletionRequest(**base)


def _api_key_config(**extra: object) -> LLMProviderConfig:
    return LLMProviderConfig(
        kind="vertex", api_key="TU_GEMINI_API_KEY_AQUI", extra={"mode": "api_key", **extra}
    )


async def _fake_token() -> str:
    return "FAKE_TOKEN"


# --- construcción / validación --------------------------------------------


def test_api_key_sin_key_lanza_llm_error() -> None:
    config = LLMProviderConfig(kind="vertex", extra={"mode": "api_key"})
    with pytest.raises(LLMError, match="api_key"):
        VertexAIProvider(config)


def test_modo_desconocido_lanza_llm_error() -> None:
    config = LLMProviderConfig(kind="vertex", extra={"mode": "raro"})
    with pytest.raises(LLMError, match="mode"):
        VertexAIProvider(config)


def test_modo_por_defecto_es_api_key() -> None:
    config = LLMProviderConfig(kind="vertex", api_key="TU_KEY_AQUI")
    provider = VertexAIProvider(config)
    assert provider._mode == "api_key"  # type: ignore[attr-defined]


def test_service_account_sin_project_id_lanza_llm_error() -> None:
    config = LLMProviderConfig(kind="vertex", extra={"mode": "service_account"})
    with pytest.raises(LLMError, match="project_id"):
        VertexAIProvider(config, token_provider=_fake_token)


def test_service_account_sin_json_lanza_llm_error_sin_requerir_google_auth() -> None:
    # No pasa `token_provider`: fuerza el camino de `_build_credentials`, que
    # revienta ANTES de intentar `import google.oauth2` porque falta
    # `service_account_json` — no depende de si el extra está instalado.
    config = LLMProviderConfig(
        kind="vertex", extra={"mode": "service_account", "project_id": "mi-proyecto"}
    )
    with pytest.raises(LLMError, match="service_account_json"):
        VertexAIProvider(config)


def test_service_account_sin_extra_google_auth_da_mensaje_claro() -> None:
    # Este paquete de tests deliberadamente NO instala `edecan-llm[vertex]`
    # (ver docstring del módulo) — así que este camino ejercita el import
    # guardeado de verdad, sin mocks.
    config = LLMProviderConfig(
        kind="vertex",
        extra={
            "mode": "service_account",
            "project_id": "mi-proyecto",
            "service_account_json": "{}",
        },
    )
    with pytest.raises(LLMError, match="google-auth"):
        VertexAIProvider(config)


# --- modo api_key -----------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_api_key_completion_normal() -> None:
    respx.post(API_KEY_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Son las 10:00am."}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4},
            },
        )
    )
    provider = VertexAIProvider(_api_key_config())
    try:
        response = await provider.complete(_req())

        assert response.text == "Son las 10:00am."
        assert response.stop_reason == "end"
        assert response.usage.input_tokens == 10
        assert response.usage.output_tokens == 4

        request = respx.calls.last.request
        assert "key=TU_GEMINI_API_KEY_AQUI" in str(request.url)
        body = json.loads(request.content)
        assert body["systemInstruction"] == {"parts": [{"text": "Eres Edecán."}]}
        assert body["contents"][0] == {"role": "user", "parts": [{"text": "¿Qué hora es?"}]}
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_api_key_mapea_tools_y_function_call() -> None:
    respx.post(API_KEY_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "agenda_eventos",
                                        "args": {"dia": "hoy"},
                                    }
                                }
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
            },
        )
    )
    tools = [
        ToolSpec(
            name="agenda_eventos", description="Lista eventos", input_schema={"type": "object"}
        )
    ]
    provider = VertexAIProvider(_api_key_config())
    try:
        response = await provider.complete(_req(tools=tools))

        assert response.stop_reason == "tool_use"
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "agenda_eventos"
        assert response.tool_calls[0].arguments == {"dia": "hoy"}
        assert response.tool_calls[0].id

        body = json.loads(respx.calls.last.request.content)
        assert body["tools"] == [
            {
                "functionDeclarations": [
                    {
                        "name": "agenda_eventos",
                        "description": "Lista eventos",
                        "parameters": {"type": "object"},
                    }
                ]
            }
        ]
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_tool_result_resuelve_nombre_por_tool_use_id() -> None:
    respx.post(API_KEY_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "Listo."}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            },
        )
    )
    provider = VertexAIProvider(_api_key_config())
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
        assistant_content = body["contents"][1]
        assert assistant_content["role"] == "model"
        assert assistant_content["parts"][0]["functionCall"]["name"] == "agenda_eventos"

        function_content = body["contents"][2]
        assert function_content["role"] == "function"
        assert function_content["parts"][0]["functionResponse"] == {
            "name": "agenda_eventos",
            "response": {"result": "hecho"},
        }
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_finish_reason_max_tokens() -> None:
    respx.post(API_KEY_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "corta"}]}, "finishReason": "MAX_TOKENS"}
                ],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            },
        )
    )
    provider = VertexAIProvider(_api_key_config())
    try:
        response = await provider.complete(_req())
        assert response.stop_reason == "max_tokens"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_status_error_lanza_llm_error_con_status_code() -> None:
    respx.post(API_KEY_URL).mock(return_value=httpx.Response(400, text="bad request"))
    provider = VertexAIProvider(_api_key_config())
    try:
        with pytest.raises(LLMError) as excinfo:
            await provider.complete(_req())
        assert excinfo.value.status_code == 400
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_transport_error_lanza_provider_down_error() -> None:
    respx.post(API_KEY_URL).mock(side_effect=httpx.ConnectError("refused"))
    provider = VertexAIProvider(_api_key_config())
    try:
        with pytest.raises(ProviderDownError):
            await provider.complete(_req())
    finally:
        await provider.aclose()


SSE_BODY = (
    'data: {"candidates":[{"content":{"parts":[{"text":"Hola"}]}}]}\n\n'
    'data: {"candidates":[{"content":{"parts":[{"text":" mundo"}]}}],'
    '"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":2}}\n\n'
)


@pytest.mark.asyncio
@respx.mock
async def test_streaming_de_texto() -> None:
    respx.post(API_KEY_STREAM_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=SSE_BODY
        )
    )
    provider = VertexAIProvider(_api_key_config())
    try:
        chunks = [chunk async for chunk in provider.stream(_req())]

        text = "".join(c.text for c in chunks if c.type == "text" and c.text)
        assert text == "Hola mundo"
        assert chunks[-1].type == "stop"

        usage_chunks = [c for c in chunks if c.type == "usage"]
        assert usage_chunks[-1].usage is not None
        assert usage_chunks[-1].usage.input_tokens == 5
        assert usage_chunks[-1].usage.output_tokens == 2

        request = respx.calls.last.request
        assert "streamGenerateContent" in str(request.url)
        assert "alt=sse" in str(request.url)
    finally:
        await provider.aclose()


SSE_TOOL_BODY = (
    'data: {"candidates":[{"content":{"parts":[{"functionCall":'
    '{"name":"agenda_eventos","args":{"dia":"hoy"}}}]}}]}\n\n'
)


@pytest.mark.asyncio
@respx.mock
async def test_streaming_tool_call() -> None:
    respx.post(API_KEY_STREAM_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=SSE_TOOL_BODY
        )
    )
    provider = VertexAIProvider(_api_key_config())
    try:
        chunks = [chunk async for chunk in provider.stream(_req())]

        tool_chunks = [c for c in chunks if c.type == "tool_call"]
        assert len(tool_chunks) == 1
        assert tool_chunks[0].tool_call is not None
        assert tool_chunks[0].tool_call.name == "agenda_eventos"
        assert tool_chunks[0].tool_call.arguments == {"dia": "hoy"}
        assert chunks[-1].type == "stop"
    finally:
        await provider.aclose()


# --- modo service_account (token_provider inyectado, sin google-auth) ------


@pytest.mark.asyncio
@respx.mock
async def test_service_account_usa_bearer_token_del_token_provider() -> None:
    respx.post(SERVICE_ACCOUNT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Son las 10:00am."}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 14, "candidatesTokenCount": 6},
            },
        )
    )
    config = LLMProviderConfig(
        kind="vertex", extra={"mode": "service_account", "project_id": "mi-proyecto"}
    )
    provider = VertexAIProvider(config, token_provider=_fake_token)
    try:
        response = await provider.complete(_req())

        assert response.text == "Son las 10:00am."
        assert response.usage.input_tokens == 14
        assert response.usage.output_tokens == 6

        request = respx.calls.last.request
        assert request.headers["authorization"] == "Bearer FAKE_TOKEN"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_service_account_respeta_region_configurada() -> None:
    url = (
        "https://europe-west1-aiplatform.googleapis.com/v1/projects/mi-proyecto/"
        "locations/europe-west1/publishers/google/models/gemini-2.5-pro:generateContent"
    )
    respx.post(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            },
        )
    )
    config = LLMProviderConfig(
        kind="vertex",
        extra={"mode": "service_account", "project_id": "mi-proyecto", "region": "europe-west1"},
    )
    provider = VertexAIProvider(config, token_provider=_fake_token)
    try:
        response = await provider.complete(_req())
        assert response.text == "ok"
    finally:
        await provider.aclose()
