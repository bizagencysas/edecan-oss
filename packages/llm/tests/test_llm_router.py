from __future__ import annotations

from types import SimpleNamespace

import pytest
from edecan_llm.base import CompletionRequest, CompletionResponse, LLMProvider, StreamChunk, Usage
from edecan_llm.errors import LLMError, ProviderDownError
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
    fake_workers = FakeProvider()
    router = LLMRouter(
        _settings(), provider=fake, workers_provider_factory=lambda _s: fake_workers
    )

    provider, model = router.resolve(alias, {"models.premium": False})  # type: ignore[arg-type]

    # Los modelos @cf/ van SIEMPRE al proveedor de Workers AI (trabajadores).
    assert provider is fake_workers
    assert model == "@cf/zai-org/glm-4.7-flash"


@pytest.mark.asyncio
async def test_complete_replaces_caller_model_and_reports_real_model() -> None:
    usage_calls: list[tuple[str, Usage]] = []

    async def on_usage(model: str, usage: Usage) -> None:
        usage_calls.append((model, usage))

    fake = FakeProvider()
    fake_workers = FakeProvider()
    # El chat resuelve un modelo @cf/ → ahora va al proveedor workers (C9b).
    router = LLMRouter(
        _settings(),
        provider=fake,
        workers_provider_factory=lambda _s: fake_workers,
        on_usage=on_usage,
    )
    response = await router.complete(
        "principal",
        {},
        CompletionRequest(model="user-cannot-select-this", messages=[]),
    )

    assert response.text == "ok"
    assert fake_workers.model == "@cf/zai-org/glm-4.7-flash"
    assert fake.model is None  # el primario nunca se usó (modelo @cf/)
    assert usage_calls == [("@cf/zai-org/glm-4.7-flash", Usage(input_tokens=2, output_tokens=1))]


def test_provider_factory_is_the_only_swap_point() -> None:
    fake = FakeProvider()
    fake_workers = FakeProvider()
    calls = 0
    worker_calls = 0

    def factory(settings: object) -> LLMProvider:
        nonlocal calls
        calls += 1
        assert settings is not None
        return fake

    def workers_factory(settings: object) -> LLMProvider:
        nonlocal worker_calls
        worker_calls += 1
        assert settings is not None
        return fake_workers

    router = LLMRouter(
        _settings(), provider_factory=factory, workers_provider_factory=workers_factory
    )
    # Los alias de chat resuelven modelos @cf/ → Workers AI; el swap del
    # principal (p. ej. a Azure) no toca esa vía: cada factory es su swap.
    assert router.resolve("rapido", {})[0] is fake_workers
    assert router.resolve("worker", {})[0] is fake_workers
    assert calls == 0
    assert worker_calls == 1


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


def test_resolve_with_attribution_preserva_decision_del_task_router() -> None:
    fake = FakeProvider()
    fake_workers = FakeProvider()
    router = LLMRouter(
        _settings(), provider=fake, workers_provider_factory=lambda _s: fake_workers
    )

    provider, model, attribution = router.resolve_with_attribution("profundo", {})

    # La decisión es un modelo @cf/ → el proveedor DEBE ser Workers AI, no el
    # primario (bug E-LLM-1: antes se devolvía el primario con modelo @cf/ y
    # cada paso de misión moría con 400 en Azure).
    assert provider.name == fake_workers.name
    assert model == "@cf/zai-org/glm-4.7-flash"
    assert attribution["router"] == "task_router"
    assert attribution["router_alias"] == "profundo"
    assert attribution["task_kind"] == "background"
    assert attribution["routing_reason"]


def test_resolve_with_attribution_modelo_primario_sigue_con_el_primario() -> None:
    fake = FakeProvider()
    fake_workers = FakeProvider()
    router = LLMRouter(
        _settings(
            WORKERS_AI_CHAT_MODEL="gpt-5.6-sol-2",
            WORKERS_AI_MODEL_PROFUNDO="gpt-5.6-sol-2",
        ),
        provider=fake,
        workers_provider_factory=lambda _s: fake_workers,
    )

    provider, model, _ = router.resolve_with_attribution("profundo", {})

    assert model == "gpt-5.6-sol-2"
    assert provider.name == fake.name


@pytest.mark.asyncio
async def test_fallback_reintenta_solo_si_no_hubo_texto() -> None:
    class Flaky(FakeProvider):
        def __init__(self, partial: bool = False) -> None:
            super().__init__()
            self.models: list[str] = []
            self.partial = partial

        async def stream(self, req: CompletionRequest):
            self.models.append(req.model)
            if len(self.models) == 1:
                if self.partial:
                    yield StreamChunk(type="text", text="parcial")
                raise ProviderDownError("primary down", provider="fake")
            yield StreamChunk(type="text", text="fallback ok")

    class FakeWorkersStream(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.models: list[str] = []

        async def stream(self, req: CompletionRequest):
            self.models.append(req.model)
            yield StreamChunk(type="text", text="fallback ok")

    fake = Flaky()
    fake_workers = FakeWorkersStream()
    # Primario NO-@cf (Azure simulado) + fallback @cf/: el reintento debe ir
    # al proveedor WORKERS, no al primario (E-LLM-1/R-6).
    router = LLMRouter(
        _settings(
            WORKERS_AI_CHAT_MODEL="gpt-5.6-sol-2",
            WORKERS_AI_FALLBACK_MODEL="@cf/fallback/model",
        ),
        provider=fake,
        workers_provider_factory=lambda _s: fake_workers,
    )
    provider, model, attribution = router.resolve_with_attribution("rapido", {})
    chunks = [chunk async for chunk in provider.stream(CompletionRequest(model=model))]

    assert [chunk.text for chunk in chunks] == ["fallback ok"]
    assert fake.models == ["gpt-5.6-sol-2"]
    assert fake_workers.models == ["@cf/fallback/model"]
    assert attribution["fallback_model"] == "@cf/fallback/model"
    assert provider.last_model_used == "@cf/fallback/model"

    partial = Flaky(partial=True)
    router_partial = LLMRouter(
        _settings(
            WORKERS_AI_CHAT_MODEL="gpt-5.6-sol-2",
            WORKERS_AI_FALLBACK_MODEL="@cf/fallback/model",
        ),
        provider=partial,
        workers_provider_factory=lambda _s: FakeProvider(),
    )
    provider_partial, model_partial, _ = router_partial.resolve_with_attribution("rapido", {})
    with pytest.raises(ProviderDownError, match="primary down"):
        _ = [
            chunk async for chunk in provider_partial.stream(CompletionRequest(model=model_partial))
        ]
    assert partial.models == ["gpt-5.6-sol-2"]


@pytest.mark.asyncio
async def test_fallback_tambien_cubre_complete_y_atribuye_usage_al_modelo_real() -> None:
    class CompleteFlaky(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.models: list[str] = []

        async def complete(self, req: CompletionRequest) -> CompletionResponse:
            self.models.append(req.model)
            if len(self.models) == 1:
                raise ProviderDownError("primary completion down", provider="fake")
            return CompletionResponse(
                text="fallback completion",
                usage=Usage(input_tokens=3, output_tokens=2),
                stop_reason="end",
            )

    usage_calls: list[tuple[str, Usage]] = []

    async def on_usage(model: str, usage: Usage) -> None:
        usage_calls.append((model, usage))

    fake = CompleteFlaky()

    class FakeWorkersComplete(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.models: list[str] = []

        async def complete(self, req: CompletionRequest) -> CompletionResponse:
            self.models.append(req.model)
            return CompletionResponse(
                text="fallback completion",
                usage=Usage(input_tokens=3, output_tokens=2),
                stop_reason="end",
            )

    fake_workers = FakeWorkersComplete()
    router = LLMRouter(
        _settings(
            WORKERS_AI_CHAT_MODEL="gpt-5.6-sol-2",
            WORKERS_AI_FALLBACK_MODEL="@cf/fallback/model",
        ),
        provider=fake,
        workers_provider_factory=lambda _s: fake_workers,
        on_usage=on_usage,
    )
    response = await router.complete(
        "rapido", {}, CompletionRequest(model="caller-model", messages=[])
    )

    assert response.text == "fallback completion"
    assert fake.models == ["gpt-5.6-sol-2"]
    assert fake_workers.models == ["@cf/fallback/model"]
    assert usage_calls == [("@cf/fallback/model", Usage(input_tokens=3, output_tokens=2))]


# --------------------------------------------------------------------------- #
# C9b: route()/complete() enrutan @cf/ a Workers AI igual que resolve()
# --------------------------------------------------------------------------- #


def test_route_rutea_modelo_cf_al_proveedor_workers() -> None:
    """`route()` también enruta un modelo `@cf/` al proveedor de Workers AI.

    Antes `route()` devolvía SIEMPRE `self._get_provider()` (el primario): un
    `@cf/` decidido por `TaskRouter` (p. ej. un trabajador de los bots) moría
    400 en Azure (bug E-LLM-1 revivido para el camino `route()`/`complete()`).
    """
    fake = FakeProvider()
    fake_workers = FakeProvider()
    router = LLMRouter(
        _settings(), provider=fake, workers_provider_factory=lambda _s: fake_workers
    )

    provider, decision = router.route(CompletionRequest(model="", messages=[]), alias="worker")

    assert decision.model.startswith("@cf/")
    assert provider is fake_workers


@pytest.mark.asyncio
async def test_complete_rutea_modelo_cf_al_proveedor_workers() -> None:
    """El camino `complete()` (que usa `route()`) resuelve @cf/ en Workers AI."""
    fake = FakeProvider()
    fake_workers = FakeProvider()
    router = LLMRouter(
        _settings(), provider=fake, workers_provider_factory=lambda _s: fake_workers
    )

    response = await router.complete(
        "worker", {}, CompletionRequest(model="caller-model", messages=[])
    )

    assert response.text == "ok"
    assert fake_workers.model.startswith("@cf/")
    assert fake.model is None  # el primario nunca se usó


def test_route_con_azure_activo_rutea_worker_cf_a_workers() -> None:
    """Con Azure activo, `route()` mantiene los `@cf/` en Workers AI (C9b)."""
    fake = FakeProvider()  # primario = Azure simulado
    fake_workers = FakeProvider()
    router = LLMRouter(
        _settings(LLM_PROVIDER="azure_openai"),
        provider=fake,
        workers_provider_factory=lambda _s: fake_workers,
    )

    provider, decision = router.route(CompletionRequest(model="", messages=[]), alias="worker")

    assert decision.model.startswith("@cf/")
    assert provider is fake_workers


# --------------------------------------------------------------------------- #
# C9b: el fallback solo reintenta fallos transitorios y comparte deadline
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fallback_no_dispara_en_400() -> None:
    """Un 400/4xx/auth NO dispara el fallback: reintentar un error permanente
    no lo arregla y antes gastaba un presupuesto nuevo por intento (C9b)."""

    class Permanent400(FakeProvider):
        async def complete(self, req: CompletionRequest) -> CompletionResponse:
            raise LLMError("bad request", provider="openai_compat", status_code=400)

    fake_workers = FakeProvider()
    router = LLMRouter(
        _settings(
            WORKERS_AI_CHAT_MODEL="gpt-5.6-sol-2",
            WORKERS_AI_FALLBACK_MODEL="@cf/fallback/model",
        ),
        provider=Permanent400(),
        workers_provider_factory=lambda _s: fake_workers,
    )
    provider, model, _ = router.resolve_with_attribution("rapido", {})

    with pytest.raises(LLMError, match="bad request"):
        await provider.complete(CompletionRequest(model=model, messages=[]))

    assert fake_workers.model is None  # el fallback nunca se intentó


@pytest.mark.asyncio
async def test_fallback_comparte_deadline_unico() -> None:
    """El fallback recibe SOLO el tiempo restante del deadline compartido (C9b),
    no un presupuesto nuevo completo; y no muta el metadata del request original."""
    captured: dict[str, float | None] = {}

    class Primary(FakeProvider):
        async def complete(self, req: CompletionRequest) -> CompletionResponse:
            captured["primary_deadline"] = req.metadata.get("deadline_s")
            raise ProviderDownError("down", provider="fake")

    class WorkersFallback(FakeProvider):
        async def complete(self, req: CompletionRequest) -> CompletionResponse:
            captured["fallback_deadline"] = req.metadata.get("deadline_s")
            return CompletionResponse(
                text="ok", usage=Usage(input_tokens=1, output_tokens=1), stop_reason="end"
            )

    router = LLMRouter(
        _settings(
            WORKERS_AI_CHAT_MODEL="gpt-5.6-sol-2",
            WORKERS_AI_FALLBACK_MODEL="@cf/fallback/model",
        ),
        provider=Primary(),
        workers_provider_factory=lambda _s: WorkersFallback(),
    )
    provider, model, _ = router.resolve_with_attribution("rapido", {})
    request = CompletionRequest(model=model, messages=[], metadata={"deadline_s": 10.0})

    response = await provider.complete(request)

    assert response.text == "ok"
    assert captured["primary_deadline"] == pytest.approx(10.0)
    fallback_deadline = captured["fallback_deadline"]
    assert isinstance(fallback_deadline, float)
    assert 0.0 < fallback_deadline <= 10.0
    # El request original no fue mutado por el reintento.
    assert request.metadata["deadline_s"] == pytest.approx(10.0)
