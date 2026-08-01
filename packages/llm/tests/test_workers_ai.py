from __future__ import annotations

import json

import httpx
import pytest
from edecan_llm.base import ChatMessage, CompletionRequest, ToolSpec
from edecan_llm.errors import LLMError
from edecan_llm.workers_ai import (
    MODELO_POR_DEFECTO,
    CredencialInvalidaError,
    PeticionInvalidaError,
    TiempoAgotadoError,
    WorkersAIProvider,
)


def _provider(handler: httpx.MockTransport) -> WorkersAIProvider:
    client = httpx.AsyncClient(
        transport=handler,
    )
    return WorkersAIProvider(
        account_id="account",
        api_token="secret-token",
        http_client=client,
        max_intentos=2,
        backoff_base_s=0.01,
    )


@pytest.mark.asyncio
async def test_complete_exito_y_parseo_cached_tokens() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/client/v4/accounts/account/ai/run/@cf/zai-org/glm-4.7-flash"
        assert request.headers["authorization"] == "Bearer secret-token"
        body = json.loads(request.content)
        assert body["messages"][0]["content"] == "Hola"
        assert body["tools"][0]["function"]["name"] == "buscar"
        return httpx.Response(
            200,
            json={
                "result": {
                    "choices": [
                        {
                            "message": {
                                "content": "Respuesta exitosa",
                                "reasoning_content": "Pensando...",
                                "tool_calls": [],
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "prompt_tokens_details": {"cached_tokens": 8},
                        "neurons": 1.25,
                    },
                },
                "success": True,
            },
        )

    provider = _provider(httpx.MockTransport(handle))
    response = await provider.complete(
        CompletionRequest(
            model="@cf/zai-org/glm-4.7-flash",
            messages=[ChatMessage(role="user", content="Hola")],
            tools=[ToolSpec(name="buscar", description="Busca", input_schema={"type": "object"})],
        )
    )
    assert response.text == "Respuesta exitosa"
    assert response.reasoning_content == "Pensando..."
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5
    assert response.cached_tokens == 8
    assert response.neurons == 1.25
    await provider.aclose()


@pytest.mark.asyncio
async def test_complete_success_false_con_http_200() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": None,
                "success": False,
                "errors": [{"code": 1000, "message": "Error interno del modelo"}],
            },
        )

    provider = _provider(httpx.MockTransport(handle))
    with pytest.raises(PeticionInvalidaError) as exc_info:
        await provider.complete(
            CompletionRequest(
                model="@cf/zai-org/glm-4.7-flash",
                messages=[ChatMessage(role="user", content="Test")],
            )
        )
    assert "success=false" in str(exc_info.value)
    await provider.aclose()


@pytest.mark.asyncio
async def test_complete_401_403_con_codigo_cloudflare() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "success": False,
                "errors": [{"code": 5018, "message": "No access to this model"}],
            },
        )

    provider = _provider(httpx.MockTransport(handle))
    with pytest.raises(CredencialInvalidaError) as exc_info:
        await provider.complete(
            CompletionRequest(
                model="@cf/moonshotai/kimi-k3",
                messages=[ChatMessage(role="user", content="Test")],
            )
        )
    assert "code=5018" in str(exc_info.value)
    await provider.aclose()


@pytest.mark.asyncio
async def test_complete_429_con_reintento() -> None:
    intentos = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal intentos
        intentos += 1
        if intentos == 1:
            return httpx.Response(
                429, json={"error": "rate limit"}, headers={"retry-after": "0.01"}
            )
        return httpx.Response(
            200,
            json={
                "result": {
                    "choices": [{"message": {"content": "Ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                },
                "success": True,
            },
        )

    provider = _provider(httpx.MockTransport(handle))
    response = await provider.complete(
        CompletionRequest(
            model="@cf/zai-org/glm-4.7-flash",
            messages=[ChatMessage(role="user", content="Hola")],
        )
    )
    assert intentos == 2
    assert response.text == "Ok"
    await provider.aclose()


@pytest.mark.asyncio
async def test_complete_timeout() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Timed out")

    provider = _provider(httpx.MockTransport(handle))
    with pytest.raises(TiempoAgotadoError):
        await provider.complete(
            CompletionRequest(
                model="@cf/zai-org/glm-4.7-flash",
                messages=[ChatMessage(role="user", content="Hola")],
            )
        )
    await provider.aclose()


@pytest.mark.asyncio
async def test_complete_tool_calls_con_bloque_de_codigo_multiline() -> None:
    codigo_multilinea = "def hello():\n    print('Hello World!')\n    return 42"

    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "escribir_codigo",
                                            "arguments": json.dumps({"code": codigo_multilinea}),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 15},
                },
                "success": True,
            },
        )

    provider = _provider(httpx.MockTransport(handle))
    response = await provider.complete(
        CompletionRequest(
            model="@cf/zai-org/glm-4.7-flash",
            messages=[ChatMessage(role="user", content="Escribe una función")],
        )
    )
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "escribir_codigo"
    assert response.tool_calls[0].arguments["code"] == codigo_multilinea
    await provider.aclose()


@pytest.mark.asyncio
async def test_stream_sse() -> None:
    usage_json = (
        '{"prompt_tokens":5,"completion_tokens":2,'
        '"prompt_tokens_details":{"cached_tokens":3}}'
    )
    async def handle(request: httpx.Request) -> httpx.Response:
        lines = [
            'data: {"choices":[{"delta":{"reasoning_content":"Pensando..."}}]}\n\n',
            'data: {"choices":[{"delta":{"content":"Hola "}}]}\n\n',
            'data: {"choices":[{"delta":{"content":"mundo"}}]}\n\n',
            f'data: {{"usage":{usage_json}}}\n\n',
            'data: [DONE]\n\n',
        ]
        return httpx.Response(200, text="".join(lines))

    provider = _provider(httpx.MockTransport(handle))
    chunks = []
    async for chunk in provider.stream(
        CompletionRequest(
            model="@cf/zai-org/glm-4.7-flash",
            messages=[ChatMessage(role="user", content="Hola")],
        )
    ):
        chunks.append(chunk)

    reasoning_chunks = [c for c in chunks if c.reasoning_text]
    text_chunks = [c for c in chunks if c.text]
    usage_chunks = [c for c in chunks if c.type == "usage"]

    assert len(reasoning_chunks) == 1
    assert reasoning_chunks[0].reasoning_text == "Pensando..."
    assert "".join(c.text for c in text_chunks) == "Hola mundo"
    assert len(usage_chunks) == 1
    assert usage_chunks[0].cached_tokens == 3
    await provider.aclose()


@pytest.mark.parametrize("account,token", [("", "token"), ("account", "")])
@pytest.mark.asyncio
async def test_credentials_are_required(account: str, token: str) -> None:
    provider = WorkersAIProvider(account_id=account, api_token=token)
    with pytest.raises(LLMError):
        await provider.complete(
            CompletionRequest(
                model="@cf/zai-org/glm-4.7-flash",
                messages=[ChatMessage(role="user", content="Hola")],
            )
        )


def test_forge_probe_model_no_decide_el_modelo_del_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`FORGE_PROBE_MODEL` es del banco de pruebas del IDE; el chat no la hereda.

    Estuvo en la cadena de respaldo de `self.model`, y como `WORKERS_AI_CHAT_MODEL`
    casi nunca está puesta, el chat terminaba corriendo en el modelo que alguien
    había apuntado para MEDIR. Con `@cf/zai-org/glm-5.2` ahí (42 s por vuelta del
    ciclo agente↔herramientas) un turno de 8 vueltas se iba a más de cinco minutos
    girando sin producir nada.

    El caso de abajo es exactamente ese: la variable de medición puesta, la del chat
    ausente. Debe ganar el default del chat, no la sonda.
    """
    monkeypatch.setenv("FORGE_PROBE_MODEL", "@cf/zai-org/glm-5.2")
    monkeypatch.delenv("WORKERS_AI_CHAT_MODEL", raising=False)

    provider = WorkersAIProvider(account_id="cuenta", api_token="token", env_file=None)

    assert provider.model == MODELO_POR_DEFECTO
    assert provider.model != "@cf/zai-org/glm-5.2"


def test_workers_ai_chat_model_si_manda_cuando_esta_puesta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quitar la sonda de la cadena no puede quitar también la forma legítima de
    cambiar el modelo del chat: `WORKERS_AI_CHAT_MODEL` sigue mandando sobre el
    default."""
    monkeypatch.setenv("WORKERS_AI_CHAT_MODEL", "@cf/meta/llama-4-scout-17b-16e-instruct")
    monkeypatch.setenv("FORGE_PROBE_MODEL", "@cf/zai-org/glm-5.2")

    provider = WorkersAIProvider(account_id="cuenta", api_token="token", env_file=None)

    assert provider.model == "@cf/meta/llama-4-scout-17b-16e-instruct"
