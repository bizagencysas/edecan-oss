import pytest
from edecan_core.agent import Agent, _done_attribution
from edecan_core.provider_health import ProviderHealth
from edecan_core.tools import ToolRegistry


class _FlakyProvider:
    name = "flaky"

    def __init__(self, *, fail: bool):
        self.fail = fail
        self.calls = 0

    async def stream(self, _request):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        yield type("Chunk", (), {"type": "text", "text": "ok"})()


@pytest.mark.asyncio
async def test_agent_registra_fallo_y_abre_circuito_antes_de_reintentar() -> None:
    health = ProviderHealth(failure_threshold=1, recovery_seconds=60)
    provider = _FlakyProvider(fail=True)
    agent = Agent(None, ToolRegistry(), provider_health=health)

    with pytest.raises(RuntimeError, match="provider down"):
        _ = [chunk async for chunk in agent._stream_provider(provider, object())]
    with pytest.raises(RuntimeError, match="no está disponible"):
        _ = [chunk async for chunk in agent._stream_provider(provider, object())]

    assert provider.calls == 1
    assert health.health_report()["flaky"]["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_agent_registra_exito_y_latencia() -> None:
    health = ProviderHealth()
    provider = _FlakyProvider(fail=False)
    agent = Agent(None, ToolRegistry(), provider_health=health)

    chunks = [chunk async for chunk in agent._stream_provider(provider, object())]

    assert len(chunks) == 1
    report = health.health_report()["flaky"]
    assert report["available"] is True
    assert report["total_successes"] == 1
    assert report["total_failures"] == 0


def test_provider_health_historial_no_guarda_la_excepcion_y_esta_acotado() -> None:
    health = ProviderHealth()
    health.record_success("principal", latency=0.25)
    health.record_failure("principal", error=RuntimeError("secreto-no-debe-salir"))
    eventos = health.recent_events(limit=10)

    assert [event["status"] for event in eventos] == ["failure", "success"]
    assert all("error" not in event for event in eventos)
    assert "secreto-no-debe-salir" not in repr(eventos)
    assert health.recent_events(limit=0) == []


def test_provider_health_notifica_eventos_publicos_sin_excepcion() -> None:
    events: list[dict] = []
    health = ProviderHealth(event_sink=events.append)

    health.record_success("principal", latency=0.25)
    health.record_failure("principal", error=RuntimeError("no debe persistirse"))
    health.record_rate_limit("principal", retry_after_seconds=5)

    assert [event["status"] for event in events] == [
        "success",
        "failure",
        "rate_limited",
    ]
    assert all("error" not in event for event in events)
    assert "no debe persistirse" not in repr(events)


@pytest.mark.asyncio
async def test_agent_health_atribuye_modelo_y_alias_sin_payload() -> None:
    health = ProviderHealth(event_sink=lambda event: eventos.append(event))
    eventos: list[dict] = []
    provider = _FlakyProvider(fail=False)
    agent = Agent(None, ToolRegistry(), provider_health=health, model_alias="rapido")

    awaitables = [
        chunk
        async for chunk in agent._stream_provider(
            provider, type("Request", (), {"model": "modelo-seguro"})()
        )
    ]

    assert len(awaitables) == 1
    assert eventos[0]["model"] == "modelo-seguro"
    assert eventos[0]["model_alias"] == "rapido"


def test_done_attribution_expone_fallback_y_modelo_efectivo() -> None:
    provider = type(
        "FallbackProvider",
        (),
        {"name": "workers_ai", "last_model_used": "modelo-fallback", "last_fallback_used": True},
    )()

    attribution = _done_attribution(provider, "modelo-primario", "rapido", {})

    assert attribution["model"] == "modelo-fallback"
    assert attribution["fallback_used"] == "true"
