from __future__ import annotations

import uuid
from typing import Any

from edecan_api.bot_turn_service import ensure_worker_conversation
from edecan_api.deps import CurrentUser, TenantCtx
from edecan_api.routers.teams import _ensure_team_conversation


class _Mappings:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def first(self) -> dict[str, Any] | None:
        return self.row


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def mappings(self) -> _Mappings:
        return _Mappings(self.row)


class _WinningParentSession:
    """The request snapshot is stale, but another transaction already linked the winner."""

    def __init__(self, *, parent_table: str, winner: uuid.UUID) -> None:
        self.parent_table = parent_table
        self.winner = winner
        self.calls: list[str] = []

    async def execute(self, statement: Any, _params: dict[str, Any]) -> _Result:
        sql = str(statement)
        self.calls.append(sql)
        assert self.parent_table in sql
        assert sql.lstrip().startswith("SELECT conversation_id")
        assert "FOR UPDATE" in sql
        return _Result({"conversation_id": self.winner})


def _user() -> CurrentUser:
    tenant_id = uuid.uuid4()
    return CurrentUser(
        user_id=uuid.uuid4(),
        tenant=TenantCtx(tenant_id=tenant_id, plan_key="hosted_basic", flags={}),
    )


async def test_worker_reloads_locked_parent_and_uses_concurrent_winner() -> None:
    winner = uuid.uuid4()
    session = _WinningParentSession(parent_table="persistent_agents", winner=winner)

    result = await ensure_worker_conversation(
        session,  # type: ignore[arg-type]
        _user(),
        {"id": str(uuid.uuid4()), "name": "BotAlpha", "conversation_id": None},
    )

    assert result == winner
    assert len(session.calls) == 1


async def test_team_reloads_locked_parent_and_uses_concurrent_winner() -> None:
    winner = uuid.uuid4()
    session = _WinningParentSession(parent_table="teams", winner=winner)

    result = await _ensure_team_conversation(
        session,  # type: ignore[arg-type]
        _user(),
        {"id": str(uuid.uuid4()), "name": "Producto", "conversation_id": None},
    )

    assert result == winner
    assert len(session.calls) == 1
