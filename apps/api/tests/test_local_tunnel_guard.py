"""La app local nunca publica login/registro/UI al activar Cloudflare Tunnel."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from edecan_api.main import LocalTunnelGuardMiddleware


def _guarded_app(*, enabled: bool = True) -> FastAPI:
    app = FastAPI()
    app.add_middleware(LocalTunnelGuardMiddleware, enabled=enabled)

    @app.api_route("/{path:path}", methods=["GET", "POST", "OPTIONS"])
    async def echo(path: str) -> dict[str, str]:
        return {"path": f"/{path}"}

    return app


async def _request(
    path: str,
    *,
    method: str = "GET",
    enabled: bool = True,
    authorization: str | None = None,
):
    headers = {"CF-Ray": "edge-test", "CF-Connecting-IP": "203.0.113.20"}
    if authorization is not None:
        headers["Authorization"] = authorization
    async with AsyncClient(
        transport=ASGITransport(app=_guarded_app(enabled=enabled)),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, headers=headers)


async def test_tunnel_permits_health_and_one_time_pairing_without_bearer() -> None:
    assert (await _request("/healthz")).status_code == 200
    assert (await _request("/v1/devices/pairing/claim", method="POST")).status_code == 200
    assert (await _request("/v1/devices/pairing/refresh", method="POST")).status_code == 200
    assert (await _request("/v1/auth/refresh", method="POST")).status_code == 200


async def test_tunnel_never_exposes_login_register_or_desktop_ui() -> None:
    for path in (
        "/",
        "/app/ajustes/",
        "/v1/auth/local",
        "/v1/auth/login",
        "/v1/auth/register",
        "/v1/setup/status",
    ):
        response = await _request(path, method="POST", authorization="Bearer even-present")
        assert response.status_code == 403


async def test_tunnel_requires_bearer_for_regular_api_routes() -> None:
    missing = await _request("/v1/me")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"

    authenticated = await _request("/v1/me", authorization="Bearer device-session")
    assert authenticated.status_code == 200


async def test_hosted_mode_and_lan_requests_keep_existing_behavior() -> None:
    hosted = await _request("/v1/auth/register", method="POST", enabled=False)
    assert hosted.status_code == 200

    app = _guarded_app(enabled=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://lan") as client:
        lan = await client.post("/v1/auth/register")
    assert lan.status_code == 200


async def test_los_webhooks_de_twilio_pasan_sin_bearer() -> None:
    """Twilio no es un dispositivo emparejado: nunca manda `Authorization: Bearer`.

    Regresión de un fallo real de producción. El guardia devolvía 401 a
    `/v1/phone/twilio/calls/{id}/voice`, Twilio no conseguía el guion de la llamada y colgaba
    a los 5 segundos: el teléfono sonaba y se cortaba sin que nadie hablara. Twilio lo
    reportó como `error 11200: Got HTTP 401`. Rompía por igual salientes y entrantes.

    Dejarlos pasar aquí NO los deja abiertos: cada endpoint valida `X-Twilio-Signature`
    contra el token del tenant y responde 403 si no cuadra. La firma es su autenticación.
    """
    rutas = (
        "/v1/phone/twilio/calls/27385888-f14a-4ca6-9df4-c29bc8aa824a/voice",
        "/v1/phone/twilio/calls/27385888-f14a-4ca6-9df4-c29bc8aa824a/gather",
        "/v1/phone/twilio/calls/27385888-f14a-4ca6-9df4-c29bc8aa824a/status",
        "/v1/phone/twilio/incoming",
        "/v1/phone/twilio/media",
        "/v1/phone/twilio/status",
    )
    for ruta in rutas:
        respuesta = await _request(ruta, method="POST")
        assert respuesta.status_code == 200, f"{ruta} quedó bloqueada: {respuesta.status_code}"


async def test_los_webhooks_de_elevenlabs_pasan_sin_bearer() -> None:
    """ConvAI post-call no es un dispositivo: HMAC es su autenticación."""
    respuesta = await _request("/v1/phone/elevenlabs/post-call", method="POST")
    assert respuesta.status_code == 200, respuesta.status_code


async def test_el_resto_de_phone_sigue_exigiendo_bearer() -> None:
    # La excepción es SOLO para el prefijo de webhooks. Las rutas de teléfono que usa la app
    # (crear llamada, listar) siguen siendo de dispositivo autenticado.
    assert (await _request("/v1/phone/calls", method="POST")).status_code == 401
    assert (await _request("/v1/phone/agent-templates")).status_code == 401
