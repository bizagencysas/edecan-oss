from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

import edecan_api.deps as deps
import edecan_api.routers.teams as teams
from edecan_api.deps import CurrentUser, TenantCtx
from edecan_api.routers.conversations import _format_sse
from edecan_api.routers.teams import TeamMessageIn


async def _body(response: Any) -> str:
    parts: list[str] = []
    async for part in response.body_iterator:
        parts.append(part.decode() if isinstance(part, bytes) else part)
    return "".join(parts)


class _Session:
    def __init__(self) -> None:
        self.executes: list[dict[str, Any]] = []

    async def execute(self, _query: Any, params: dict[str, Any] | None = None) -> Any:
        self.executes.append(params or {})
        return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None, all=lambda: []))

    async def commit(self) -> None:
        return None


def _user() -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(),
        tenant=TenantCtx(tenant_id=uuid.uuid4(), plan_key="hosted_basic", flags={}),
    )


def _workers() -> dict[str, dict[str, Any]]:
    frontend_id = uuid.uuid4()
    backend_id = uuid.uuid4()
    return {
        str(frontend_id): {
            "id": str(frontend_id),
            "name": "frontend",
            "display_name": "Frontend",
            "purpose": "UI, login, SwiftUI y pantallas.",
            "job_description": "Frontend mobile",
            "role_title": "Frontend",
        },
        str(backend_id): {
            "id": str(backend_id),
            "name": "backend",
            "display_name": "Backend",
            "purpose": "API, smoke tests y endpoints FastAPI.",
            "job_description": "Backend engineer",
            "role_title": "Backend",
        },
    }


@pytest.fixture
def routing_env(monkeypatch: pytest.MonkeyPatch, fake_redis: Any) -> dict[str, Any]:
    team_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    workers = _workers()
    worker_ids = list(workers.keys())
    chosen: list[str] = []
    assignment_events: list[dict[str, Any]] = []

    async def load_team(_session: Any, _user: Any, _team_id: uuid.UUID) -> dict[str, Any]:
        return {"id": str(team_id), "name": "Equipo", "conversation_id": conversation_id}

    async def ensure(_session: Any, _user: Any, _team: Any) -> uuid.UUID:
        return conversation_id

    async def members(_session: Any, _user: Any, _team_id: uuid.UUID) -> list[tuple[str, str]]:
        return [(worker_ids[0], "coordinator"), (worker_ids[1], "member")]

    async def load_worker(_session: Any, _user: Any, worker_id: uuid.UUID) -> dict[str, Any]:
        return workers[str(worker_id)]

    async def persist_assignment(
        session: Any,
        *,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        assignee: dict[str, Any],
        reason: str,
    ) -> None:
        assignment_events.append(
            {
                "assignee_id": str(assignee["id"]),
                "reason": reason,
                "conversation_id": str(conversation_id),
            }
        )

    async def turn(**kwargs: Any):
        chosen.append(str(kwargs["worker"]["id"]))
        yield _format_sse(
            "message.done",
            {"type": "done", "usage": {}, "message_id": str(uuid.uuid4())},
        )

    monkeypatch.setattr(teams, "_load_team", load_team)
    monkeypatch.setattr(teams, "_ensure_team_conversation", ensure)
    monkeypatch.setattr(teams, "_team_member_ids", members)
    monkeypatch.setattr(teams, "load_worker", load_worker)
    monkeypatch.setattr(teams, "persist_team_assignment_event", persist_assignment)
    monkeypatch.setattr(teams, "stream_worker_turn", turn)
    monkeypatch.setattr(deps, "get_redis", lambda _settings: fake_redis)
    return {
        "team_id": team_id,
        "workers": workers,
        "chosen": chosen,
        "assignment_events": assignment_events,
    }


async def test_team_message_smoke_api_elige_backend_no_coordinador(
    routing_env: dict[str, Any],
) -> None:
    user = _user()
    session = _Session()
    request = SimpleNamespace(headers={})
    settings = SimpleNamespace(CHAT_IDEMPOTENCY_TTL_SECONDS=120)

    response = await teams.send_team_message(
        routing_env["team_id"],
        TeamMessageIn(text="smokea la API", speaker="user"),
        request,
        user=user,
        session=session,
        settings=settings,
    )
    await _body(response)

    backend_id = [
        wid
        for wid, worker in routing_env["workers"].items()
        if worker["display_name"] == "Backend"
    ][0]
    assert routing_env["chosen"] == [backend_id]
    assert routing_env["assignment_events"]
    assert routing_env["assignment_events"][0]["assignee_id"] == backend_id
    assert response.status_code == 200
