from __future__ import annotations

from types import SimpleNamespace

import pytest
from edecan_llm.base import CompletionRequest, CompletionResponse, LLMProvider, Usage
from edecan_llm.router import LLMRouter


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "CLOUDFLARE_ACCOUNT_ID": "account-id",
        "CLOUDFLARE_API_TOKEN": "api-token",
        "WORKERS_AI_CHAT_MODEL": "@cf/zai-org/glm-4.7-flash",
        "WORKERS_AI_TIMEOUT_SECONDS": 60.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self) -> None:
        self.model: str | None = None
        self.closed = False

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.model = req.model
        return CompletionResponse(
            text="ok",
            usage=Usage(input_tokens=2, output_tokens=1),
            stop_reason="end",
        )

    async def stream(self, req: CompletionRequest):  # pragma: no cover
        raise NotImplementedError

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("alias", ["rapido", "principal", "profundo"])
def test_all_non_ide_aliases_use_glm_automatically(alias: str) -> None:
    fake = FakeProvider()
    router = LLMRouter(_settings(), provider=fake)

    provider, model = router.resolve(alias, {"models.premium": False})  # type: ignore[arg-type]

    assert provider is fake
    assert model == "@cf/zai-org/glm-4.7-flash"


@pytest.mark.asyncio
async def test_complete_replaces_caller_model_and_reports_real_model() -> None:
    usage_calls: list[tuple[str, Usage]] = []

    async def on_usage(model: str, usage: Usage) -> None:
        usage_calls.append((model, usage))

    fake = FakeProvider()
    router = LLMRouter(_settings(), provider=fake, on_usage=on_usage)
    response = await router.complete(
        "principal",
        {},
        CompletionRequest(model="user-cannot-select-this", messages=[]),
    )

    assert response.text == "ok"
    assert fake.model == "@cf/zai-org/glm-4.7-flash"
    assert usage_calls == [
        ("@cf/zai-org/glm-4.7-flash", Usage(input_tokens=2, output_tokens=1))
    ]


def test_provider_factory_is_the_only_swap_point() -> None:
    fake = FakeProvider()
    calls = 0

    def factory(settings: object) -> LLMProvider:
        nonlocal calls
        calls += 1
        assert settings is not None
        return fake

    router = LLMRouter(_settings(), provider_factory=factory)
    assert router.resolve("rapido", {})[0] is fake
    assert router.resolve("principal", {})[0] is fake
    assert calls == 1


@pytest.mark.asyncio
async def test_aclose_releases_provider_and_is_idempotent() -> None:
    fake = FakeProvider()
    router = LLMRouter(_settings(), provider=fake)

    await router.aclose()
    await router.aclose()

    assert fake.closed is True


def test_resolve_sin_metadata_se_comporta_igual_que_siempre() -> None:
    """Retro-compatibilidad: `metadata` es kwarg con default, y sin él la
    resolución es exactamente la de antes del selector."""

    fake = FakeProvider()
    router = LLMRouter(_settings(), provider=fake)

    _, sin_kwarg = router.resolve("rapido", {})  # type: ignore[arg-type]
    _, con_none = router.resolve("rapido", {}, metadata=None)  # type: ignore[arg-type]

    assert sin_kwarg == con_none == "@cf/zai-org/glm-4.7-flash"


def test_resolve_honra_el_modelo_elegido_del_selector() -> None:
    """La elección viaja como metadata hasta `TaskRouter`, que es quien decide:
    aquí solo se comprueba que el canal existe y no se pierde en el camino."""

    from edecan_llm.task_router import modelo_chat_por_defecto

    fake = FakeProvider()
    router = LLMRouter(_settings(), provider=fake)
    elegido = modelo_chat_por_defecto()

    _, model = router.resolve(
        "rapido",  # type: ignore[arg-type]
        {},
        metadata={"modelo_elegido": elegido},
    )

    assert model == elegido


def test_resolve_ignora_un_modelo_fuera_del_catalogo_y_no_revienta() -> None:
    """Defensa en profundidad: la API ya devolvió 422, pero si algo se cuela el
    turno corre con el modelo automático en vez de hablarle a un id inexistente."""

    fake = FakeProvider()
    router = LLMRouter(_settings(), provider=fake)

    _, model = router.resolve(
        "rapido",  # type: ignore[arg-type]
        {},
        metadata={"modelo_elegido": "@cf/vendor/no-existe"},
    )

    assert model == "@cf/zai-org/glm-4.7-flash"
