"""Protocolo inter-agente (grokbot.md §12): cola durable de mensajes entre agentes.

Este router administra los mensajes `agent_messages`; no ejecuta trabajo
autónomo. La autorización de ejecución queda para una ola posterior.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from edecan_core.queue import enqueue
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/agents/messages", tags=["agent-messages"], dependencies=[Depends(rate_limit)]
)

_MESSAGE_TYPES = (
    "task",
    "question",
    "result",
    "blocker",
    "review_request",
    "handoff",
    "status",
    "cancel",
)
_STATUSES = ("pending", "delivered", "acknowledged", "done", "error")

# Tipos que implican trabajo real del receptor (grokbot.md §12-13): solo estos
# disparan `run_persistent_agent`; `result`/`status`/`cancel`/`blocker` son
# informativos y no ejecutan nada.
_RUNNABLE_MESSAGE_TYPES = ("task", "handoff", "question")

_COLUMNS = (
    "id, tenant_id, sender_agent_id, receiver_agent_id, task_id, parent_task_id, "
    "conversation_id, message_type, goal, expected_output, priority, deadline, "
    "dependencies, allowed_tools, approval_boundary, artifact_refs, context_refs, "
    "status, created_at, updated_at"
)


class AgentMessageCreateIn(BaseModel):
    # La UI (web/iOS) manda un mensaje simple `{recipient_id, text}`; acá se
    # aceptan alias para no romper el contrato de los clientes en paralelo.
    message_type: str = Field(default="task", min_length=1, max_length=32)
    receiver_agent_id: uuid.UUID | None = None
    recipient_id: uuid.UUID | None = None
    sender_agent_id: uuid.UUID | None = None
    task_id: str | None = Field(default=None, max_length=200)
    parent_task_id: str | None = Field(default=None, max_length=200)
    conversation_id: uuid.UUID | None = None
    goal: str | None = Field(default=None, max_length=4000)
    text: str | None = Field(default=None, max_length=4000)
    expected_output: str | None = Field(default=None, max_length=4000)
    priority: str | None = Field(default=None, max_length=32)
    deadline: datetime | None = None
    dependencies: Any = None
    allowed_tools: Any = None
    approval_boundary: Any = None
    artifact_refs: Any = None
    context_refs: Any = None


def _current(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.tenant.flags.get("agents.missions", False):
        raise HTTPException(status_code=403, detail="Los agentes no están disponibles en tu plan.")
    return user


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """UUID/datetime/jsonb → JSON que iOS y la web ya saben leer (mismo criterio
    que `persistent_agents._public_row`)."""
    payload: dict[str, Any] = {}
    for key, value in dict(row).items():
        if isinstance(value, uuid.UUID):
            payload[key] = str(value)
        elif isinstance(value, datetime):
            payload[key] = value.isoformat()
        else:
            payload[key] = value
    return jsonable_encoder(payload)


async def _get_one(
    session: AsyncSession, user: CurrentUser, message_id: uuid.UUID
) -> dict[str, Any]:
    result = await session.execute(
        text(
            f"SELECT {_COLUMNS} FROM agent_messages m WHERE m.tenant_id = :tenant_id AND m.id = :id "
            "AND (m.sender_agent_id IS NULL "
            "OR EXISTS (SELECT 1 FROM persistent_agents w WHERE w.id = m.sender_agent_id "
            "AND w.user_id = :user_id) "
            "OR EXISTS (SELECT 1 FROM persistent_agents w WHERE w.id = m.receiver_agent_id "
            "AND w.user_id = :user_id))"
        ),
        {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id), "id": str(message_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Mensaje de agente no encontrado.")
    return _public_row(row)


async def _ensure_agent_exists(
    session: AsyncSession, user: CurrentUser, agent_id: uuid.UUID
) -> None:
    result = await session.execute(
        text(
            "SELECT id FROM persistent_agents "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id"
        ),
        {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id), "id": str(agent_id)},
    )
    if result.mappings().first() is None:
        raise HTTPException(status_code=404, detail="Agente destinatario no encontrado.")


def _jsonb_or_none(value: Any) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value is not None else None


async def _lookup_agent_owner_user_id(
    session: AsyncSession, user: CurrentUser, agent_id: uuid.UUID
) -> uuid.UUID | None:
    result = await session.execute(
        text(
            "SELECT user_id FROM persistent_agents "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": str(user.tenant_id), "id": str(agent_id)},
    )
    row = result.mappings().first()
    if row is None:
        return None
    return uuid.UUID(str(row["user_id"]))


async def _enqueue_agent_message_push(
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    message_id: uuid.UUID,
) -> None:
    """Notifica al dueño del agente receptor — best-effort, fail-closed en logs."""
    try:
        await enqueue(
            settings,
            "notify_important_event",
            {
                "user_id": str(owner_user_id),
                "kind": "agent_message",
                "event_id": str(message_id),
                "resource_id": str(message_id),
            },
            tenant_id,
        )
    except Exception:  # noqa: BLE001 - el mensaje ya quedó persistido
        logger.warning(
            "send_message: no se pudo encolar notify_important_event "
            "(message_id=%s owner_user_id=%s)",
            message_id,
            owner_user_id,
            exc_info=True,
        )


async def _enqueue_receiver_work(
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    receiver_agent_id: uuid.UUID,
    message_id: uuid.UUID,
    goal: str | None,
) -> None:
    """Encola `run_persistent_agent` para el receptor de un mensaje ejecutable.

    Best-effort (grokbot.md §12-13): el insert del mensaje ya está persistido;
    un fallo de encolado NO debe tumbar la respuesta. El worker receptor vuelve
    a validar enabled/status/presupuesto al correr, y `task_id` queda ligado al
    id del mensaje para que el acknowledge/estado final pueda reconciliarse.
    """
    instruction = (goal or "").strip()
    if not instruction:
        return
    try:
        await enqueue(
            settings,
            "run_persistent_agent",
            {
                "worker_id": str(receiver_agent_id),
                "instruction": instruction,
                "task_id": str(message_id),
            },
            tenant_id,
        )
    except Exception:  # noqa: BLE001 - best-effort, el mensaje ya quedó guardado
        logger.warning(
            "send_message: no se pudo encolar run_persistent_agent para receptor=%s",
            receiver_agent_id,
            exc_info=True,
        )


@router.post("", status_code=status.HTTP_201_CREATED)
async def send_message(
    body: AgentMessageCreateIn,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    message_type = body.message_type.strip().lower()
    if message_type not in _MESSAGE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo de mensaje inválido. Usa uno de: {', '.join(_MESSAGE_TYPES)}.",
        )
    receiver = body.receiver_agent_id or body.recipient_id
    goal = body.goal or body.text
    if receiver is not None:
        await _ensure_agent_exists(session, user, receiver)
    if body.sender_agent_id is not None:
        await _ensure_agent_exists(session, user, body.sender_agent_id)
    if body.conversation_id:
        conv_check = (
            await session.execute(
                text(
                    "SELECT 1 FROM conversations c "
                    "JOIN persistent_agents a ON a.conversation_id = c.id "
                    "WHERE c.tenant_id = :tenant_id AND c.id = :cid "
                    "AND a.user_id = :user_id LIMIT 1"
                ),
                {
                    "tenant_id": str(user.tenant_id),
                    "user_id": str(user.user_id),
                    "cid": str(body.conversation_id),
                },
            )
        ).mappings().first()
        if conv_check is None:
            raise HTTPException(
                status_code=403, detail="Esa conversación no pertenece a tus bots."
            )

    try:
        result = await session.execute(
            text(
                "INSERT INTO agent_messages "
                "(id, tenant_id, sender_agent_id, receiver_agent_id, task_id, parent_task_id, "
                "conversation_id, message_type, goal, expected_output, priority, deadline, "
                "dependencies, allowed_tools, approval_boundary, artifact_refs, context_refs, "
                "status) "
                "VALUES (gen_random_uuid(), :tenant_id, :sender, :receiver, :task_id, "
                ":parent_task_id, :conversation_id, :message_type, :goal, :expected_output, "
                ":priority, :deadline, :dependencies ::jsonb, :allowed_tools ::jsonb, "
                ":approval_boundary ::jsonb, :artifact_refs ::jsonb, :context_refs ::jsonb, "
                "'pending') "
                f"RETURNING {_COLUMNS}"
            ),
            {
                "tenant_id": str(user.tenant_id),
                "sender": str(body.sender_agent_id) if body.sender_agent_id else None,
                "receiver": str(receiver) if receiver else None,
                "task_id": body.task_id,
                "parent_task_id": body.parent_task_id,
                "conversation_id": (
                    str(body.conversation_id) if body.conversation_id else None
                ),
                "message_type": message_type,
                "goal": goal,
                "expected_output": body.expected_output,
                "priority": body.priority,
                "deadline": body.deadline,
                "dependencies": _jsonb_or_none(body.dependencies),
                "allowed_tools": _jsonb_or_none(body.allowed_tools),
                "approval_boundary": _jsonb_or_none(body.approval_boundary),
                "artifact_refs": _jsonb_or_none(body.artifact_refs),
                "context_refs": _jsonb_or_none(body.context_refs),
            },
        )
        row = result.mappings().first()
        assert row is not None
        # Inter-agent runtime (grokbot.md §12-13): si el mensaje va dirigido a
        # un worker concreto y es ejecutable, el RECEPTOR hace el trabajo.
        if receiver is not None and message_type in _RUNNABLE_MESSAGE_TYPES:
            await _enqueue_receiver_work(
                settings,
                tenant_id=user.tenant_id,
                receiver_agent_id=receiver,
                message_id=uuid.UUID(str(row["id"])),
                goal=goal,
            )
        if receiver is not None:
            owner_user_id = await _lookup_agent_owner_user_id(session, user, receiver)
            if owner_user_id is not None:
                await _enqueue_agent_message_push(
                    settings,
                    tenant_id=user.tenant_id,
                    owner_user_id=owner_user_id,
                    message_id=uuid.UUID(str(row["id"])),
                )
        return _public_row(row)
    except ProgrammingError:
        logger.exception("send_message: esquema de agent_messages no disponible")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Los mensajes entre agentes no están listos en esta instalación.",
        ) from None
    except SQLAlchemyError:
        logger.exception("send_message: error de base")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No pude guardar el mensaje.",
        ) from None


@router.get("")
async def list_messages(
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    status_filter: str | None = Query(default=None, alias="status"),
    receiver: uuid.UUID | None = Query(default=None),
) -> list[dict[str, Any]]:
    try:
        result = await session.execute(
            text(
                f"SELECT {_COLUMNS} FROM agent_messages m WHERE m.tenant_id = :tenant_id "
                "AND (m.sender_agent_id IS NULL "
                "OR EXISTS (SELECT 1 FROM persistent_agents w WHERE w.id = m.sender_agent_id "
                "AND w.user_id = :user_id) "
                "OR EXISTS (SELECT 1 FROM persistent_agents w WHERE w.id = m.receiver_agent_id "
                "AND w.user_id = :user_id)) "
                "AND (CAST(:status AS text) IS NULL OR m.status = :status) "
                "AND (CAST(:receiver AS uuid) IS NULL OR m.receiver_agent_id = :receiver) "
                "ORDER BY m.created_at DESC"
            ),
            {
                "tenant_id": str(user.tenant_id),
                "user_id": str(user.user_id),
                "status": status_filter,
                "receiver": str(receiver) if receiver else None,
            },
        )
        return [_public_row(row) for row in result.mappings().all()]
    except ProgrammingError:
        logger.exception("list_messages: esquema de agent_messages no disponible")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Los mensajes entre agentes no están listos en esta instalación.",
        ) from None
    except SQLAlchemyError:
        logger.exception("list_messages: error de base")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No pude leer los mensajes.",
        ) from None


@router.get("/{message_id}")
async def get_message(
    message_id: uuid.UUID,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    return await _get_one(session, user, message_id)


@router.post("/{message_id}/acknowledge")
async def acknowledge_message(
    message_id: uuid.UUID,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    msg = await _get_one(session, user, message_id)
    if msg["status"] in ("done", "error"):
        raise HTTPException(status_code=409, detail="El mensaje ya está en estado final.")
    if msg["status"] != "acknowledged":
        await session.execute(
            text(
                "UPDATE agent_messages SET status = 'acknowledged', updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :id "
                "AND status IN ('pending', 'delivered')"
            ),
            {"tenant_id": str(user.tenant_id), "id": str(message_id)},
        )
        return await _get_one(session, user, message_id)
    return msg
