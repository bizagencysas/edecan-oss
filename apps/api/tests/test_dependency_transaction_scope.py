"""La respuesta HTTP no puede adelantarse al commit de las sesiones DB."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from edecan_api import deps
from edecan_api.repo import Repo


async def _assert_dependency_closes_before_response(repo_dependency) -> None:
    events: list[str] = []

    async def session() -> AsyncIterator[object]:
        events.append("transaction_started")
        yield object()
        events.append("transaction_committed")

    app = FastAPI()

    @app.post("/write")
    async def write(repo: Repo = Depends(repo_dependency)) -> dict[str, bool]:  # noqa: B008
        assert repo is not None
        events.append("handler_returned")
        return {"ok": True}

    if repo_dependency is deps.get_platform_repo:
        app.dependency_overrides[deps.get_platform_session] = session
    else:
        app.dependency_overrides[deps.get_tenant_session] = session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/write")
        events.append("client_received_response")

    assert response.status_code == 200
    assert events == [
        "transaction_started",
        "handler_returned",
        "transaction_committed",
        "client_received_response",
    ]


async def test_platform_repo_commits_before_register_response() -> None:
    await _assert_dependency_closes_before_response(deps.get_platform_repo)


async def test_tenant_repo_commits_before_write_response() -> None:
    await _assert_dependency_closes_before_response(deps.get_repo)
