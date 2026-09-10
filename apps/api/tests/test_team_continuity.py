from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

import edecan_api.deps as deps
import edecan_api.routers.teams as teams
from edecan_api.deps import CurrentUser, TenantCtx
from edecan_api.routers.conversations import _format_sse
from edecan_api.routers.teams import TeamMessageIn


class _Session:
    def __init__(self) -> None:
        self.commits = 0
        self.executes = 0

    async def commit(self) -> None:
        self.commits += 1

    async def execute(self, *_a: Any, **_k: Any) -> None:
        # persist_team_assignment_event INSERT; the stream fake does not need a result.
        self.executes += 1


def _user() -> CurrentUser:
    tenant_id = uuid.uuid4()
    return CurrentUser(
        user_id=uuid.uuid4(),
        tenant=TenantCtx(tenant_id=tenant_id, plan_key="hosted_basic", flags={}),
    )


async def _body(response: Any) -> str:
    parts: list[str] = []
    async for part in response.body_iterator:
        parts.append(part.decode() if isinstance(part, bytes) else part)
    return "".join(parts)


@pytest.fixture
def team_env(monkeypatch: pytest.MonkeyPatch, fake_redis: Any) -> dict[str, Any]:
    team_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    runs: list[str] = []

    async def load_team(_session: Any, _user: Any, _team_id: uuid.UUID) -> dict[str, Any]:
        return {"id": str(team_id), "name": "Producto", "conversation_id": conversation_id}

    async def ensure(_session: Any, _user: Any, _team: Any) -> uuid.UUID:
        return conversation_id

    async def members(_session: Any, _user: Any, _team_id: uuid.UUID) -> list[tuple[str, str]]:
        return [(str(worker_id), "coordinator")]

    async def load_worker(_session: Any, _user: Any, _worker_id: uuid.UUID) -> dict[str, Any]:
        return {"id": str(worker_id), "name": "Coordi"}

    async def turn(**kwargs: Any):
        runs.append(kwargs["user_text"])
        yield _format_sse("message.delta", {"type": "text_delta", "text": "Respuesta"})
        yield _format_sse(
            "message.done",
            {"type": "done", "usage": {}, "message_id": str(uuid.uuid4())},
        )

    monkeypatch.setattr(teams, "_load_team", load_team)
    monkeypatch.setattr(teams, "_ensure_team_conversation", ensure)
    monkeypatch.setattr(teams, "_team_member_ids", members)
    monkeypatch.setattr(teams, "load_worker", load_worker)
    monkeypatch.setattr(teams, "stream_worker_turn", turn)
    monkeypatch.setattr(deps, "get_redis", lambda _settings: fake_redis)
    return {"team_id": team_id, "conversation_id": conversation_id, "runs": runs}


async def test_team_retry_replays_exact_stream_without_second_turn(
    team_env: dict[str, Any],
) -> None:
    user = _user()
    session = _Session()
    key = str(uuid.uuid4())
    request = SimpleNamespace(headers={"Idempotency-Key": key})
    settings = SimpleNamespace(CHAT_IDEMPOTENCY_TTL_SECONDS=3600)
    body = TeamMessageIn(text="Revisen esto")

    first = await teams.send_team_message(
        team_env["team_id"], body, request, user, session, settings
    )
    first_body = await _body(first)
    replay = await teams.send_team_message(
        team_env["team_id"], body, request, user, session, settings
    )
    replay_body = await _body(replay)

    assert first_body == replay_body
    assert first.headers["Idempotency-Key"] == key
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert team_env["runs"] == ["Revisen esto"]
    # El commit a mitad de stream se eliminó (cerraba la transacción de
    # get_tenant_session y reventaba los guardados posteriores); la transacción
    # del request la confirma get_session al cerrar, no el stream.
    assert session.commits == 0


async def test_team_turn_lock_preserves_message_order(
    team_env: dict[str, Any], fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    starts: list[str] = []

    async def ordered_turn(**kwargs: Any):
        prompt = kwargs["user_text"]
        starts.append(prompt)
        if prompt == "primero":
            first_started.set()
            await release_first.wait()
        yield _format_sse(
            "message.done",
            {"type": "done", "usage": {}, "message_id": str(uuid.uuid4())},
        )

    monkeypatch.setattr(teams, "stream_worker_turn", ordered_turn)
    user = _user()
    settings = SimpleNamespace(CHAT_IDEMPOTENCY_TTL_SECONDS=3600)
    first = await teams.send_team_message(
        team_env["team_id"],
        TeamMessageIn(text="primero"),
        SimpleNamespace(headers={"Idempotency-Key": str(uuid.uuid4())}),
        user,
        _Session(),
        settings,
    )
    second = await teams.send_team_message(
        team_env["team_id"],
        TeamMessageIn(text="segundo"),
        SimpleNamespace(headers={"Idempotency-Key": str(uuid.uuid4())}),
        user,
        _Session(),
        settings,
    )

    first_task = asyncio.create_task(_body(first))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second_task = asyncio.create_task(_body(second))
    await asyncio.sleep(0)
    assert starts == ["primero"]
    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert starts == ["primero", "segundo"]
