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
