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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.bot_turn_service import (
    list_normalized_messages,
    load_worker,
    persist_team_assignment_event,
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
    current = await session.execute(
        text(
            "SELECT conversation_id FROM teams "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id "
            "FOR UPDATE"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "id": str(team["id"]),
        },
    )
    current_row = current.mappings().first()
    if current_row is None:
        raise HTTPException(status_code=404, detail="Equipo no encontrado.")
    conversation_id = current_row["conversation_id"]
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
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id "
            "AND conversation_id IS NULL"
        ),
        {
            "cid": str(new_id),
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "id": str(team["id"]),
        },
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
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None),
) -> list[dict[str, Any]] | dict[str, Any]:
    team = await _load_team(session, user, team_id)
    if team.get("conversation_id") is None:
        return []
    # AUD-12a: misma semántica de cursor que `persistent_agents.list_worker_messages`.
    # Sin `before` devuelve la lista plana legacy; con `before` devuelve una
    # `HistoryPage` (`messages`, `next_cursor`, `has_more`) — iOS consume el
    # cursor en vez de ensanchar `limit`.
    return await list_normalized_messages(
        session,
        tenant_id=user.tenant_id,
        conversation_id=uuid.UUID(str(team["conversation_id"])),
        limit=limit,
        before=before,
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
    from edecan_api.deps import get_redis
    from edecan_api.routers.conversations import (
        _claim_message_idempotency,
        _message_idempotency_key,
        _message_request_hash,
        _response_for_idempotency_record,
        _stream_and_complete_idempotency,
        _stream_con_presencia,
    )
    from edecan_api.routers.persistent_agents import (
        _stream_with_committed_terminal,
        _turn_lock_for,
    )

    team = await _load_team(session, user, team_id)
    creation_lock = _turn_lock_for(user.tenant_id, team_id)
    async with creation_lock:
        conversation_id = await _ensure_team_conversation(session, user, team)
    turn_lock = _turn_lock_for(user.tenant_id, conversation_id)
    members = await _team_member_ids(session, user, team_id)
    if not members:
        raise HTTPException(
            status_code=422,
            detail="Este equipo no tiene bots. Agrega miembros antes de chatear.",
        )

    member_ids = [agent_id for agent_id, _role in members]
    coordinator_id = member_ids[0] if members else None
    speaker = body.speaker.strip()
    assignment_reason = ""

    if speaker in ("user", "owner", "human"):
        team_workers = [
            await load_worker(session, user, uuid.UUID(agent_id)) for agent_id in member_ids
        ]
        from edecan_core.bot_routing import select_responder_for_team

        routing = select_responder_for_team(
            team_workers,
            body.text,
            coordinator_id=coordinator_id,
        )
        responder_id = routing.agent_id
        assignment_reason = routing.reason
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

    clave_raw = request.headers.get("Idempotency-Key")
    if clave_raw:
        try:
            idempotency_key = uuid.UUID(clave_raw.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Idempotency-Key debe ser un UUID válido.",
            ) from exc
    else:
        idempotency_key = uuid.uuid4()

    redis_client = get_redis(settings)
    idempotency_ttl = max(60, int(settings.CHAT_IDEMPOTENCY_TTL_SECONDS))
    redis_key = _message_idempotency_key(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        conversation_id=conversation_id,
        idempotency_key=idempotency_key,
    )
    request_hash = _message_request_hash(body)
    owner_token, previous = await _claim_message_idempotency(
        redis_client,
        redis_key=redis_key,
        request_hash=request_hash,
        ttl_seconds=idempotency_ttl,
    )
    if previous is not None:
        return _response_for_idempotency_record(
            record=previous,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
        )
    if owner_token is None:  # pragma: no cover - defensive against incompatible Redis
        raise HTTPException(status_code=409, detail="No se pudo reclamar este turno idempotente.")

    async def _stream():
        if run_as_user:
            await persist_team_assignment_event(
                session,
                tenant_id=user.tenant_id,
                conversation_id=conversation_id,
                assignee=responder,
                reason=assignment_reason,
            )
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

    async def _serialized_stream():
        async with turn_lock:
            async for chunk in _stream_with_committed_terminal(_stream(), session=session):
                yield chunk

    live_stream = _stream_and_complete_idempotency(
        stream=_serialized_stream(),
        redis_client=redis_client,
        redis_key=redis_key,
        request_hash=request_hash,
        owner_token=owner_token,
        ttl_seconds=idempotency_ttl,
    )
    return StreamingResponse(
        _stream_con_presencia(live_stream, conversation_id),
        media_type="text/event-stream",
        headers={
            "Idempotency-Key": str(idempotency_key),
            "Idempotency-Replayed": "false",
        },
    )
