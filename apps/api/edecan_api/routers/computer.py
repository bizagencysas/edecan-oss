"""`/v1/computer` — plano de control de "toma de control / pausa" de la
computadora por agente y por superficie (directiva §18-24, §144-145; Ola F).

La tabla `computer_sessions` (migración `0054_agent_takeover`) es la fuente
durable de verdad: una fila por (tenant, superficie, agente-opcional) con el
`mode` (`agent`|`user`|`paused`) que gobierna quién mueve esa superficie AHORA.
La tool `usar_computadora` (`packages/toolkit/edecan_toolkit/computadora.py`)
lee esas filas ANTES de reenviar cualquier acción al companion y se niega
cuando `mode != 'agent'` — este router solo muta el plano de control; el
enforcement real vive en el tool layer (directiva §123: "enforce tool-side,
never trust prompt").

Contrato de la API (la UI web/iOS llega después; esto es el contrato limpio):

- `GET    /v1/computer/sessions` — listar sesiones del tenant.
- `POST   /v1/computer/sessions` — crear una sesión (kind, agent_id opcional,
  ephemeral, workspace_scope). Nace `mode='agent'` + `status='active'`.
- `POST   /v1/computer/sessions/{id}/takeover` — `mode='user'` (el humano toma
  el control; el agente queda suspendido de esa superficie).
- `POST   /v1/computer/sessions/{id}/return` — `mode='agent'` (devuelve el
  control al agente).
- `POST   /v1/computer/sessions/{id}/pause` — `mode='paused'` + `status='paused'`.
- `POST   /v1/computer/sessions/{id}/resume` — `mode='agent'` + `status='active'`.
- `POST   /v1/computer/sessions/{id}/end` — `status='ended'` (cierra la sesión;
  deja de gobernar la superficie).

No hay WebRTC ni streaming acá: la vista por polling ya existe en
`routers/remote.py`; esto solo administra quién puede mover cada superficie.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/computer", tags=["computer"], dependencies=[Depends(rate_limit)]
)

_COLUMNS = (
    "id, tenant_id, user_id, agent_id, kind, mode, ephemeral, status, "
    "workspace_scope, created_at, updated_at"
)
_KINDS = ("browser", "desktop", "terminal", "files")
_MODES = ("agent", "user", "paused")


class ComputerSessionCreateIn(BaseModel):
    kind: str = Field(default="desktop", max_length=40)
    agent_id: uuid.UUID | None = None
    ephemeral: bool = False
    workspace_scope: dict[str, Any] = Field(default_factory=dict)
    mode: str = Field(default="agent", max_length=40)


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """UUID/datetime/jsonb → JSON legible (mismo criterio que
    `persistent_agents._public_row`)."""
    payload: dict[str, Any] = {}
    for key, value in dict(row).items():
        if isinstance(value, uuid.UUID):
            payload[key] = str(value)
        elif isinstance(value, datetime):
            payload[key] = value.isoformat()
        elif key == "workspace_scope" and isinstance(value, str):
            try:
                payload[key] = json.loads(value)
            except ValueError:
                payload[key] = {}
        else:
            payload[key] = value
    return jsonable_encoder(payload)


async def _get_one(
    session: AsyncSession, user: CurrentUser, session_id: uuid.UUID
) -> dict[str, Any]:
    result = await session.execute(
        text(
            f"SELECT {_COLUMNS} FROM computer_sessions WHERE tenant_id = :tenant_id "
            "AND id = :id"
        ),
        {"tenant_id": str(user.tenant_id), "id": str(session_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Sesión de computadora no encontrada.")
    return _public_row(row)


async def _require_agent(
    session: AsyncSession, user: CurrentUser, agent_id: uuid.UUID | None
) -> None:
    """`agent_id`, si viene, debe ser un worker del MISMO tenant (evita cruce)."""
    if agent_id is None:
        return
    result = await session.execute(
        text(
            "SELECT id FROM persistent_agents WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": str(user.tenant_id), "id": str(agent_id)},
    )
    if result.mappings().first() is None:
        raise HTTPException(
            status_code=422, detail="El worker indicado no pertenece a este tenant."
        )


def _validate_kind(kind: str) -> str:
    kind = (kind or "").strip()
    if kind not in _KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"Superficie inválida. Usa una de: {', '.join(_KINDS)}.",
        )
    return kind


def _validate_mode(mode: str) -> str:
    mode = (mode or "").strip()
    if mode not in _MODES:
        raise HTTPException(
            status_code=422,
            detail=f"Modo inválido. Usa uno de: {', '.join(_MODES)}.",
        )
    return mode


@router.get("/sessions")
async def list_sessions(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    agent_id: uuid.UUID | None = Query(default=None),
) -> list[dict[str, Any]]:
    try:
        result = await session.execute(
            text(
                f"SELECT {_COLUMNS} FROM computer_sessions WHERE tenant_id = :tenant_id "
                "AND (CAST(:agent_id AS uuid) IS NULL OR agent_id = :agent_id) "
                "ORDER BY updated_at DESC"
            ),
            {"tenant_id": str(user.tenant_id), "agent_id": str(agent_id) if agent_id else None},
        )
        return [_public_row(row) for row in result.mappings().all()]
    except ProgrammingError:
        logger.exception("list_sessions: esquema de computer_sessions no disponible")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="El plano de control de computadora no está listo en esta instalación.",
        ) from None
    except SQLAlchemyError:
        logger.exception("list_sessions: error de base")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No pude leer las sesiones de computadora.",
        ) from None


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: ComputerSessionCreateIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    kind = _validate_kind(body.kind)
    _validate_mode(body.mode)
    await _require_agent(session, user, body.agent_id)
    scope = body.workspace_scope if isinstance(body.workspace_scope, dict) else {}
    result = await session.execute(
        text(
            "INSERT INTO computer_sessions "
            "(id, tenant_id, user_id, agent_id, kind, mode, ephemeral, workspace_scope) "
            "VALUES (gen_random_uuid(), :tenant_id, :user_id, :agent_id, :kind, :mode, "
            ":ephemeral, :workspace_scope ::jsonb) "
            f"RETURNING {_COLUMNS}"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "agent_id": str(body.agent_id) if body.agent_id else None,
            "kind": kind,
            "mode": body.mode.strip() or "agent",
            "ephemeral": bool(body.ephemeral),
            "workspace_scope": json.dumps(scope),
        },
    )
    row = result.mappings().first()
    assert row is not None
    return _public_row(row)


async def _transition(
    session: AsyncSession,
    user: CurrentUser,
    session_id: uuid.UUID,
    *,
    mode: str | None,
    status_value: str | None,
) -> dict[str, Any]:
    current = await _get_one(session, user, session_id)
    if current["status"] == "ended":
        raise HTTPException(status_code=409, detail="Esta sesión ya terminó.")
    sets = ["updated_at = now()"]
    params: dict[str, Any] = {"tenant_id": str(user.tenant_id), "id": str(session_id)}
    if mode is not None:
        sets.append("mode = :mode")
        params["mode"] = mode
    if status_value is not None:
        sets.append("status = :status")
        params["status"] = status_value
    await session.execute(
        text(
            f"UPDATE computer_sessions SET {', '.join(sets)} "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        params,
    )
    return await _get_one(session, user, session_id)


@router.post("/sessions/{session_id}/takeover")
async def takeover(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    return await _transition(session, user, session_id, mode="user", status_value="active")


@router.post("/sessions/{session_id}/return")
async def return_control(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    return await _transition(session, user, session_id, mode="agent", status_value="active")


@router.post("/sessions/{session_id}/pause")
async def pause(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    return await _transition(session, user, session_id, mode="paused", status_value="paused")


@router.post("/sessions/{session_id}/resume")
async def resume(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    return await _transition(session, user, session_id, mode="agent", status_value="active")


@router.post("/sessions/{session_id}/end")
async def end(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    current = await _get_one(session, user, session_id)
    if current["status"] == "ended":
        raise HTTPException(status_code=409, detail="Esta sesión ya terminó.")
    await session.execute(
        text(
            "UPDATE computer_sessions SET status = 'ended', updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": str(user.tenant_id), "id": str(session_id)},
    )
    return await _get_one(session, user, session_id)