"""`/v1/teams` — chats de grupo entre bots (modelo Grok Bot).

Cada mensaje visible es un turno real (`Agent.run_turn`) del bot que responde.
Prohibido ACK sintético, delegación fingida o encolar `run_persistent_agent`
como sustituto de la conversación.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.bot_turn_service import (
    list_normalized_messages,
    load_worker,
    stream_worker_turn,
    worker_display_name,
)
from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

router = APIRouter(prefix="/v1/teams", tags=["teams"], dependencies=[Depends(rate_limit)])


def _as_public(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row(row: Mapping[str, Any]) -> dict[str, Any]:
    return jsonable_encoder({k: _as_public(v) for k, v in dict(row).items()})


class TeamCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    avatar: dict[str, Any] = Field(default_factory=dict)


class TeamMemberIn(BaseModel):
    agent_id: uuid.UUID
    role: str = Field(default="member")


class TeamMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    """Quién habla: `user` (dueño) o el id del bot emisor."""
    speaker: str = Field(default="user", max_length=120)


def _current(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    return user


async def _load_team(
    session: AsyncSession, user: CurrentUser, team_id: uuid.UUID
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "SELECT id, conversation_id, name FROM teams "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id"
        ),
        {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id), "id": str(team_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Equipo no encontrado.")
    return dict(row)


async def _ensure_team_conversation(
    session: AsyncSession,
    user: CurrentUser,
    team: Mapping[str, Any],
) -> uuid.UUID:
    conversation_id = team.get("conversation_id")
    if conversation_id is not None:
        return uuid.UUID(str(conversation_id))
    created = await session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, user_id, title, channel) "
            "VALUES (gen_random_uuid(), :tenant_id, :user_id, :title, 'web') "
            "RETURNING id"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "title": f"Equipo: {team['name']}",
        },
    )
    new_id = created.mappings().first()["id"]
    await session.execute(
        text(
            "UPDATE teams SET conversation_id = :cid, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"cid": str(new_id), "tenant_id": str(user.tenant_id), "id": str(team["id"])},
    )
    return uuid.UUID(str(new_id))


async def _team_member_ids(
    session: AsyncSession, user: CurrentUser, team_id: uuid.UUID
) -> list[tuple[str, str]]:
    """Lista `(agent_id, role)` ordenada: coordinador primero."""
    result = await session.execute(
        text(
            "SELECT m.agent_id, m.role FROM team_members m "
            "WHERE m.tenant_id = :tenant_id AND m.team_id = :team_id "
            "ORDER BY (m.role = 'coordinator') DESC, m.created_at ASC"
        ),
        {"tenant_id": str(user.tenant_id), "team_id": str(team_id)},
    )
    return [(str(r["agent_id"]), str(r["role"])) for r in result.mappings().all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_team(
    body: TeamCreateIn,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "INSERT INTO teams (id, tenant_id, user_id, name, description, avatar) "
            "VALUES (gen_random_uuid(), :tenant_id, :user_id, :name, :description, "
            ":avatar ::jsonb) "
            "RETURNING id, tenant_id, user_id, name, description, avatar, conversation_id, "
            "created_at, updated_at"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "name": body.name.strip(),
            "description": body.description,
            "avatar": json.dumps(body.avatar),
        },
    )
    row = result.mappings().first()
    assert row is not None
    return _row(row)


@router.get("")
async def list_teams(
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT id, tenant_id, user_id, name, description, avatar, conversation_id, "
            "created_at, updated_at FROM teams WHERE tenant_id = :tenant_id AND user_id = :user_id "
            "ORDER BY created_at ASC"
        ),
        {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id)},
    )
    teams = [_row(r) for r in result.mappings().all()]
    members = await session.execute(
        text(
            "SELECT team_id, agent_id, role FROM team_members WHERE tenant_id = :tenant_id"
        ),
        {"tenant_id": str(user.tenant_id)},
    )
    by_team: dict[str, list[dict[str, str]]] = {}
    for m in members.mappings().all():
        by_team.setdefault(str(m["team_id"]), []).append(
            {"agent_id": str(m["agent_id"]), "role": m["role"]}
        )
    for team in teams:
        team["members"] = by_team.get(str(team["id"]), [])
    return teams


@router.post("/{team_id}/members", status_code=status.HTTP_201_CREATED)
async def add_member(
    team_id: uuid.UUID,
    body: TeamMemberIn,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    if body.role not in ("coordinator", "member"):
        raise HTTPException(status_code=422, detail="El rol debe ser 'coordinator' o 'member'.")
    team_ok = await session.execute(
        text(
            "SELECT 1 FROM teams WHERE tenant_id = :tenant_id AND user_id = :user_id "
            "AND id = :team_id"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "team_id": str(team_id),
        },
    )
    agent_ok = await session.execute(
        text(
            "SELECT 1 FROM persistent_agents WHERE tenant_id = :tenant_id AND user_id = :user_id "
            "AND id = :agent_id"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "agent_id": str(body.agent_id),
        },
    )
    if team_ok.mappings().first() is None or agent_ok.mappings().first() is None:
        raise HTTPException(status_code=404, detail="Equipo o agente no encontrado.")
    try:
        await session.execute(
            text(
                "INSERT INTO team_members (id, tenant_id, team_id, agent_id, role) "
                "VALUES (gen_random_uuid(), :tenant_id, :team_id, :agent_id, :role) "
                "ON CONFLICT (team_id, agent_id) DO UPDATE SET role = EXCLUDED.role"
            ),
            {
                "tenant_id": str(user.tenant_id),
                "team_id": str(team_id),
                "agent_id": str(body.agent_id),
                "role": body.role,
            },
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=422, detail="No existe el equipo o el agente.") from exc
    return {"team_id": str(team_id), "agent_id": str(body.agent_id), "role": body.role}


@router.delete("/{team_id}/members/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    team_id: uuid.UUID,
    agent_id: uuid.UUID,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    await session.execute(
        text(
            "DELETE FROM team_members tm USING teams t "
            "WHERE tm.tenant_id = :tenant_id AND tm.team_id = :team_id "
            "AND tm.agent_id = :agent_id AND t.id = tm.team_id AND t.user_id = :user_id"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "team_id": str(team_id),
            "agent_id": str(agent_id),
        },
    )


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: uuid.UUID,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    await session.execute(
        text("DELETE FROM teams WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id"),
        {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id), "id": str(team_id)},
    )


@router.get("/{team_id}/messages")
async def list_team_messages(
    team_id: uuid.UUID,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    team = await _load_team(session, user, team_id)
    if team.get("conversation_id") is None:
        return []
    return await list_normalized_messages(
        session,
        tenant_id=user.tenant_id,
        conversation_id=uuid.UUID(str(team["conversation_id"])),
    )


@router.post("/{team_id}/message")
async def send_team_message(
    team_id: uuid.UUID,
    body: TeamMessageIn,
    request: Request,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
):
    team = await _load_team(session, user, team_id)
    conversation_id = await _ensure_team_conversation(session, user, team)
    members = await _team_member_ids(session, user, team_id)
    if not members:
        raise HTTPException(
            status_code=422,
            detail="Este equipo no tiene bots. Agrega miembros antes de chatear.",
        )

    member_ids = [agent_id for agent_id, _role in members]
    speaker = body.speaker.strip()

    if speaker in ("user", "owner", "human"):
        responder_id = member_ids[0]
        author_role, author_id, author_name = "user", "user", "Tú"
        prompt = body.text
        run_as_user = True
    elif speaker in member_ids:
        author_role = "assistant"
        author_id = speaker
        try:
            speaker_worker = await load_worker(session, user, uuid.UUID(speaker))
            author_name = worker_display_name(speaker_worker)
        except HTTPException:
            author_name = "Bot"
        others = [mid for mid in member_ids if mid != speaker]
        if not others:
            raise HTTPException(
                status_code=422,
                detail="Se necesitan al menos dos bots para un turno bot-a-bot en el grupo.",
            )
        responder_id = others[0]
        prompt = body.text
        run_as_user = False
    else:
        raise HTTPException(
            status_code=422,
            detail="speaker debe ser user o un agent_id miembro del equipo.",
        )

    responder = await load_worker(session, user, uuid.UUID(responder_id))

    async def _stream():
        if run_as_user:
            async for chunk in stream_worker_turn(
                request=request,
                session=session,
                user=user,
                settings=settings,
                worker=responder,
                conversation_id=conversation_id,
                user_text=prompt,
            ):
                yield chunk
        else:
            async for chunk in stream_worker_turn(
                request=request,
                session=session,
                user=user,
                settings=settings,
                worker=responder,
                conversation_id=conversation_id,
                user_text=prompt,
                speaker_role=author_role,
                speaker_id=author_id,
                speaker_name=author_name,
            ):
                yield chunk

    return StreamingResponse(_stream(), media_type="text/event-stream")
