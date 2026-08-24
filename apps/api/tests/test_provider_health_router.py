import uuid

from conftest import auth_headers
from edecan_core.provider_health import ProviderHealth


async def test_provider_health_redacta_detalles_y_muestra_circuito(client, app) -> None:
    health = ProviderHealth(failure_threshold=1, recovery_seconds=60)
    health.record_success("principal", latency=0.25)
    health.record_failure("rapido", error=RuntimeError("token-super-secreto"))
    app.state.provider_health = health

    response = await client.get(
        "/v1/health/providers",
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4()),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "edecan-provider-health.v1"
    assert body["status"] == "degraded"
    assert body["providers"]["principal"]["total_successes"] == 1
    assert body["providers"]["rapido"]["available"] is False
    assert [event["status"] for event in body["recent_events"]] == ["failure", "success"]
    assert "token-super-secreto" not in response.text
    assert "error" not in body["providers"]["rapido"]


async def test_provider_health_requiere_autenticacion(client) -> None:
    response = await client.get("/v1/health/providers")
    assert response.status_code == 401
