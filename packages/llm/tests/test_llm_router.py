"""Tests de `LLMRouter` — resolución de alias/proveedor, sin red real."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from edecan_llm.anthropic import AnthropicProvider
from edecan_llm.base import CompletionRequest, CompletionResponse, LLMProvider, Usage
from edecan_llm.errors import LLMError
from edecan_llm.openai_compat import OpenAICompatProvider
from edecan_llm.router import LLMRouter


def _settings(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = dict(
        ANTHROPIC_API_KEY="TU_ANTHROPIC_API_KEY_AQUI",
        ANTHROPIC_MODEL_PRINCIPAL="claude-sonnet-4-5",
        ANTHROPIC_MODEL_RAPIDO="claude-haiku-4-5",
        OPENAI_COMPAT_BASE_URL=None,
        OPENAI_COMPAT_API_KEY=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_resolve_principal_sin_degradar() -> None:
    router = LLMRouter(_settings())
    provider, model = router.resolve("principal", {"models.premium": True})
    assert model == "claude-sonnet-4-5"
    assert isinstance(provider, AnthropicProvider)


def test_resolve_principal_degrada_si_no_premium() -> None:
    router = LLMRouter(_settings())
    _, model = router.resolve("principal", {"models.premium": False})
    assert model == "claude-haiku-4-5"


def test_resolve_principal_no_degrada_si_flag_ausente() -> None:
    router = LLMRouter(_settings())
    _, model = router.resolve("principal", {})
    assert model == "claude-sonnet-4-5"


def test_resolve_rapido_siempre_modelo_rapido() -> None:
    router = LLMRouter(_settings())
    _, model = router.resolve("rapido", {"models.premium": True})
    assert model == "claude-haiku-4-5"
    _, model = router.resolve("rapido", {"models.premium": False})
    assert model == "claude-haiku-4-5"


def test_resolve_alias_desconocido_lanza_value_error() -> None:
    router = LLMRouter(_settings())
    with pytest.raises(ValueError):
        router.resolve("otro", {})  # type: ignore[arg-type]


def test_fallback_openai_compat_sin_anthropic_key() -> None:
    settings = _settings(
        ANTHROPIC_API_KEY=None,
        OPENAI_COMPAT_BASE_URL="https://api.openai.com/v1",
        OPENAI_COMPAT_API_KEY="TU_OPENAI_COMPAT_API_KEY_AQUI",
    )
    router = LLMRouter(settings)
    provider, _ = router.resolve("principal", {"models.premium": True})
    assert isinstance(provider, OpenAICompatProvider)


def test_sin_proveedor_configurado_lanza_llm_error() -> None:
    settings = _settings(ANTHROPIC_API_KEY=None, OPENAI_COMPAT_BASE_URL=None)
    router = LLMRouter(settings)
    with pytest.raises(LLMError):
        router.resolve("principal", {})


def test_provider_se_construye_una_sola_vez() -> None:
    router = LLMRouter(_settings())
    provider_1, _ = router.resolve("principal", {})
    provider_2, _ = router.resolve("rapido", {})
    assert provider_1 is provider_2


class _FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self) -> None:
        self.received_model: str | None = None

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.received_model = req.model
        return CompletionResponse(
            text="ok",
            tool_calls=[],
            usage=Usage(input_tokens=1, output_tokens=1),
            stop_reason="end",
        )

    async def stream(self, req: CompletionRequest):  # pragma: no cover - no usado en este test
        raise NotImplementedError


@pytest.mark.asyncio
async def test_complete_usa_modelo_resuelto_y_llama_on_usage() -> None:
    calls: list[tuple[str, Usage]] = []

    async def on_usage(model: str, usage: Usage) -> None:
        calls.append((model, usage))

    router = LLMRouter(_settings(), on_usage=on_usage)
    fake_provider = _FakeProvider()
    router._provider = fake_provider  # inyecta un fake para evitar red real

    req = CompletionRequest(model="lo-que-sea", messages=[])
    response = await router.complete("rapido", {}, req)

    assert response.text == "ok"
    assert fake_provider.received_model == "claude-haiku-4-5"
    assert calls == [("claude-haiku-4-5", Usage(input_tokens=1, output_tokens=1))]


@pytest.mark.asyncio
async def test_complete_sin_on_usage_no_falla() -> None:
    router = LLMRouter(_settings())
    router._provider = _FakeProvider()

    req = CompletionRequest(model="claude-sonnet-4-5", messages=[])
    response = await router.complete("principal", {"models.premium": True}, req)

    assert response.text == "ok"
