"""La respuesta HTTP no puede adelantarse al commit de las sesiones DB."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

from edecan_api import deps
from edecan_api.config import Settings
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


async def test_repo_and_vault_share_one_tenant_transaction() -> None:
    """Una credencial nueva necesita ver la connector_account aún no comiteada."""
    sessions: list[object] = []

    async def session() -> AsyncIterator[object]:
        value = object()
        sessions.append(value)
        yield value

    app = FastAPI()

    @app.get("/dependencies")
    async def dependencies(
        repo: Repo = Depends(deps.get_repo),  # noqa: B008
        vault=Depends(deps.get_vault),  # noqa: B008, ANN001
    ) -> dict[str, bool]:
        return {"same_session": repo._s is vault._session}  # type: ignore[attr-defined]

    app.dependency_overrides[deps.get_tenant_session] = session
    app.dependency_overrides[deps.get_settings] = lambda: Settings(
        LOCAL_MASTER_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dependencies")

    assert response.status_code == 200
    assert response.json() == {"same_session": True}
    assert len(sessions) == 1


async def test_streaming_dependencies_commit_after_the_stream_finishes() -> None:
    """La respuesta del asistente se escribe dentro del generador SSE."""
    events: list[str] = []
    sessions: list[object] = []

    async def session() -> AsyncIterator[object]:
        value = object()
        sessions.append(value)
        events.append("transaction_started")
        yield value
        events.append("transaction_committed")

    app = FastAPI()

    @app.get("/stream")
    async def stream(
        repo: Repo = Depends(deps.get_streaming_repo),  # noqa: B008
        vault=Depends(deps.get_streaming_vault),  # noqa: B008, ANN001
        raw_session=Depends(deps.get_tenant_session, scope="request"),  # noqa: B008, ANN001
    ) -> StreamingResponse:
        assert repo._s is vault._session is raw_session  # type: ignore[attr-defined]
        events.append("handler_returned")

        async def body() -> AsyncIterator[bytes]:
            events.append("stream_started")
            yield b"data: first\n\n"
            events.append("assistant_message_persisted")
            yield b"data: done\n\n"
            events.append("stream_finished")

        return StreamingResponse(body(), media_type="text/event-stream")

    app.dependency_overrides[deps.get_tenant_session] = session
    app.dependency_overrides[deps.get_settings] = lambda: Settings(
        LOCAL_MASTER_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/stream")
        events.append("client_received_response")

    assert response.status_code == 200
    assert len(sessions) == 1
    assert events == [
        "transaction_started",
        "handler_returned",
        "stream_started",
        "assistant_message_persisted",
        "stream_finished",
        "transaction_committed",
        "client_received_response",
    ]
