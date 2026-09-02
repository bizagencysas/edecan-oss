"""Identidad durable de workers always-on.

Este router administra configuración y checkpoints; no encola ni ejecuta
trabajo autónomo. La autorización de ejecución queda para una ola posterior.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from edecan_agents.persistent_policy import (
    validate_handoff,
    validate_worker_budget,
    validate_worker_tools,
)
from edecan_core.queue import enqueue
from edecan_creative.avatars import avatar_para_agente
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/agents/workers", tags=["persistent-agents"], dependencies=[Depends(rate_limit)]
)

# Lock por (tenant, worker) para serializar turnos del chat 1:1: el cliente
# manda envíos inmediatos (chat humano) y el servidor garantiza el orden. Un
# dict módulo-nivel es seguro: el API local corre un solo proceso/loop.
_TURN_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _turn_lock_for(tenant_id: uuid.UUID, worker_id: uuid.UUID) -> asyncio.Lock:
    key = (str(tenant_id), str(worker_id))
    lock = _TURN_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _TURN_LOCKS[key] = lock
    return lock

# Campos del perfil rico (migración 0048, product design). Identidad de primer nivel.
_PROFILE_COLUMNS = (
    "display_name, avatar, role_title, role_short, job_description, personality, "
    "communication_style, instructions, constraints, approval_policy, autonomy_level, "
    "relation, model_policy"
)
_COLUMNS = (
    "id, tenant_id, user_id, name, purpose, workspace, conversation_id, "
    f"{_PROFILE_COLUMNS}, "
    "tools, permissions, memory, schedule, "
    "budget, status, enabled, last_checkpoint, created_at, updated_at"
)
_STATUSES = ("idle", "running", "paused", "disabled")
_AUTONOMY_LEVELS = ("ask", "read_only", "draft", "full")
_RELATIONS = ("profesional", "amigo", "coach", "romantico")


def _detalle_conflicto_nombre(exc: IntegrityError) -> str:
    try:
        constraint = str(exc.orig.diag.constraint_name or "")
    except Exception:
        constraint = ""
    if constraint:
        return f"Ya existe un bot/equipo con ese nombre (constraint: {constraint})."
    return "Ya existe un bot/equipo con ese nombre."


class PersistentAgentCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=2000)
    workspace: str | None = Field(default=None, max_length=500)
    display_name: str | None = Field(default=None, max_length=120)
    avatar: dict[str, Any] = Field(default_factory=dict)
    role_title: str | None = Field(default=None, max_length=160)
    role_short: str | None = Field(default=None, max_length=160)
    job_description: str | None = Field(default=None, max_length=4000)
    personality: str | None = Field(default=None, max_length=1000)
    communication_style: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None, max_length=6000)
    constraints: str | None = Field(default=None, max_length=6000)
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    autonomy_level: str = Field(default="ask")
    relation: str = Field(default="profesional", max_length=40)
    model_policy: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list, max_length=64)
    permissions: dict[str, Any] = Field(default_factory=dict)
    schedule: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)


class PersistentAgentPatchIn(BaseModel):
    enabled: bool | None = None
    status: str | None = None
    last_checkpoint: dict[str, Any] | None = None
    name: str | None = Field(default=None, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    purpose: str | None = Field(default=None, max_length=4000)
    avatar: dict[str, Any] | None = None
    role_title: str | None = Field(default=None, max_length=160)
    role_short: str | None = Field(default=None, max_length=160)
    job_description: str | None = Field(default=None, max_length=4000)
    personality: str | None = Field(default=None, max_length=1000)
    communication_style: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None, max_length=6000)
    constraints: str | None = Field(default=None, max_length=6000)
    approval_policy: dict[str, Any] | None = None
    autonomy_level: str | None = None
    relation: str | None = Field(default=None, max_length=40)
    model_policy: dict[str, Any] | None = None


class PersistentAgentTaskIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=20_000)
    task_id: str | None = Field(default=None, max_length=120)


class PersistentAgentMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    # Ids de archivos ya subidos a /v1/files (imágenes y documentos que el
    # dueño manda al bot). Mismo contrato que el chat principal.
    attachments: list[str] = Field(default_factory=list, max_length=10)


class PersistentAgentHandoffIn(BaseModel):
    destination_worker_id: uuid.UUID
    task_id: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=1, max_length=20_000)
    depth: int = Field(default=0, ge=0, le=4)
    visited_worker_ids: list[str] = Field(default_factory=list, max_length=4)


def _current(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.tenant.flags.get("agents.missions", False):
        raise HTTPException(status_code=403, detail="Los workers no están disponibles en tu plan.")
    return user


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """UUID/datetime/jsonb → JSON que iOS y la web ya saben leer.

    Devolver `dict(row)` crudo hace que FastAPI arme un ResponseValidationError
    (detail en lista, no en string) y el teléfono muestre «500: sin detalle».
    """
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
    session: AsyncSession, user: CurrentUser, worker_id: uuid.UUID
) -> dict[str, Any]:
    result = await session.execute(
        text(
            f"SELECT {_COLUMNS} FROM persistent_agents WHERE tenant_id = :tenant_id "
            "AND user_id = :user_id AND id = :id"
        ),
        {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id), "id": str(worker_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Worker persistente no encontrado.")
    return _public_row(row)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_worker(
    body: PersistentAgentCreateIn,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    try:
        tools = validate_worker_tools(body.tools)
        budget = validate_worker_budget(body.budget)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.autonomy_level not in _AUTONOMY_LEVELS:
        raise HTTPException(
            status_code=422,
            detail=f"Autonomía inválida. Usa una de: {', '.join(_AUTONOMY_LEVELS)}.",
        )
    if body.relation not in _RELATIONS:
        raise HTTPException(
            status_code=422,
            detail="relación no válida (profesional|amigo|coach|romantico)",
        )
    if body.relation == "romantico":
        raise HTTPException(
            status_code=422,
            detail="relación romántica requiere aprobación explícita por separado",
        )
    conv = await session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, user_id, title, channel) "
            "VALUES (gen_random_uuid(), :tenant_id, :user_id, :title, 'web') "
            "RETURNING id"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "title": f"Bot: {body.name.strip()}",
        },
    )
    conversation_id = conv.mappings().first()["id"]
    avatar_payload = body.avatar
    # TODO bot nuevo nace con CARA (grok_face por seed determinista): si el
    # cliente mandó solo un acento (o nada), se reemplaza por el descriptor
    # completo. "cualquier bot, incluso una prueba, siempre con cara".
    if not avatar_payload or (
        isinstance(avatar_payload, dict)
        and "style" not in avatar_payload
    ):
        seed = (body.display_name or body.name).strip() or body.name.strip()
        avatar_payload = avatar_para_agente(seed, style="grok_face")
    try:
        result = await session.execute(
            text(
                "INSERT INTO persistent_agents "
                "(id, tenant_id, user_id, name, purpose, workspace, conversation_id, "
                "display_name, avatar, role_title, role_short, job_description, personality, "
                "communication_style, instructions, constraints, approval_policy, autonomy_level, "
                "relation, model_policy, tools, permissions, memory, schedule, budget) "
                "VALUES (gen_random_uuid(), :tenant_id, :user_id, :name, :purpose, :workspace, "
                ":conversation_id, "
                ":display_name, :avatar ::jsonb, :role_title, :role_short, :job_description, "
                ":personality, :communication_style, :instructions, :constraints, "
                ":approval_policy ::jsonb, :autonomy_level, :relation, :model_policy ::jsonb, "
                ":tools ::jsonb, :permissions ::jsonb, '{}'::jsonb, :schedule ::jsonb, "
                ":budget ::jsonb) "
                f"RETURNING {_COLUMNS}"
            ),
            {
                "tenant_id": str(user.tenant_id),
                "user_id": str(user.user_id),
                "name": body.name.strip(),
                "purpose": body.purpose.strip(),
                "workspace": body.workspace,
                "conversation_id": str(conversation_id),
                "display_name": (body.display_name or "").strip() or None,
                "avatar": json.dumps(avatar_payload),
                "role_title": body.role_title,
                "role_short": body.role_short,
                "job_description": body.job_description,
                "personality": body.personality,
                "communication_style": body.communication_style,
                "instructions": body.instructions,
                "constraints": body.constraints,
                "approval_policy": json.dumps(body.approval_policy),
                "autonomy_level": body.autonomy_level,
                "relation": body.relation,
                "model_policy": json.dumps(body.model_policy),
                "tools": json.dumps(tools),
                "permissions": json.dumps(body.permissions),
                "schedule": json.dumps(body.schedule),
                "budget": json.dumps(budget),
            },
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=_detalle_conflicto_nombre(exc)) from exc
    row = result.mappings().first()
    assert row is not None
    return _public_row(row)


@router.get("")
async def list_workers(
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    include_disabled: bool = Query(default=False),
) -> list[dict[str, Any]]:
    try:
        result = await session.execute(
            text(
                f"SELECT {_COLUMNS} FROM persistent_agents WHERE tenant_id = :tenant_id "
                "AND user_id = :user_id AND (:include_disabled OR status <> 'disabled') "
                "ORDER BY updated_at DESC"
            ),
            {
                "tenant_id": str(user.tenant_id),
                "user_id": str(user.user_id),
                "include_disabled": include_disabled,
            },
        )
        return [_public_row(row) for row in result.mappings().all()]
    except ProgrammingError:
        logger.exception("list_workers: esquema de persistent_agents no disponible")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Los workers no están listos en esta instalación.",
        ) from None
    except SQLAlchemyError:
        logger.exception("list_workers: error de base")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No pude leer los workers.",
        ) from None


@router.patch("/{worker_id}")
async def patch_worker(
    worker_id: uuid.UUID,
    body: PersistentAgentPatchIn,
    request: Request,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if body.status is not None and body.status not in _STATUSES:
        raise HTTPException(status_code=422, detail="Estado de worker inválido.")
    if body.autonomy_level is not None and body.autonomy_level not in _AUTONOMY_LEVELS:
        raise HTTPException(
            status_code=422,
            detail=f"Autonomía inválida. Usa una de: {', '.join(_AUTONOMY_LEVELS)}.",
        )
    viejo = await _get_one(session, user, worker_id)
    sets = ["updated_at = now()"]
    params: dict[str, Any] = {
        "tenant_id": str(user.tenant_id),
        "user_id": str(user.user_id),
        "id": str(worker_id),
    }
    if body.enabled is not None:
        sets.append("enabled = :enabled")
        params["enabled"] = body.enabled
    if body.status is not None:
        sets.append("status = :status")
        params["status"] = body.status
    if body.last_checkpoint is not None:
        sets.append("last_checkpoint = :last_checkpoint ::jsonb")
        params["last_checkpoint"] = json.dumps(body.last_checkpoint)

    scalar_fields = (
        "name",
        "display_name",
        "purpose",
        "role_title",
        "role_short",
        "job_description",
        "personality",
        "communication_style",
        "instructions",
        "constraints",
    )
    for field in scalar_fields:
        value = getattr(body, field)
        if value is not None:
            sets.append(f"{field} = :{field}")
            params[field] = value.strip() if isinstance(value, str) else value
    jsonb_fields = ("avatar", "approval_policy", "model_policy")
    for field in jsonb_fields:
        value = getattr(body, field)
        if value is not None:
            sets.append(f"{field} = :{field} ::jsonb")
            params[field] = json.dumps(value)
    if body.autonomy_level is not None:
        sets.append("autonomy_level = :autonomy_level")
        params["autonomy_level"] = body.autonomy_level
    if body.relation is not None:
        if body.relation not in _RELATIONS:
            raise HTTPException(
                status_code=422,
                detail="relación no válida (profesional|amigo|coach|romantico)",
            )
        if body.relation == "romantico":
            raise HTTPException(
                status_code=422,
                detail="relación romántica requiere aprobación explícita por separado",
            )
        sets.append("relation = :relation")
        params["relation"] = body.relation
    try:
        await session.execute(
            text(
                f"UPDATE persistent_agents SET {', '.join(sets)} WHERE tenant_id = :tenant_id "
                "AND user_id = :user_id AND id = :id"
            ),
            params,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=_detalle_conflicto_nombre(exc)) from exc
    except SQLAlchemyError:
        logger.exception("patch_worker: error de base")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No pude actualizar el worker.",
        ) from None

    # Identidad: si el dueño cambió nombre o descripción, el bot confirma su
    # nueva identidad con un turno real (modelo escribe, cero copy de Python).
    # Se agenda en background para que el PATCH responda de una vez; el ack
    # aparece en el chat del bot un momento después.
    _CAMPOS_IDENTIDAD = ("name", "display_name", "purpose", "job_description", "personality")
    etiquetas = {
        "name": "tu nombre ahora es «{v}»",
        "display_name": "cómo te muestra la app ahora es «{v}»",
        "purpose": "tu descripción/expertise ahora es: «{v}»",
        "job_description": "tu rol ahora es: «{v}»",
        "personality": "tu personalidad ahora es: «{v}»",
    }
    cambios = []
    for field in _CAMPOS_IDENTIDAD:
        value = getattr(body, field)
        if (
            value is not None
            and str(value).strip()
            and str(value).strip() != str((viejo or {}).get(field) or "").strip()
        ):
            cambios.append(etiquetas[field].format(v=str(value).strip()))
    if cambios:
        from edecan_api.bot_turn_service import ack_cambio_identidad

        resumen = "En concreto: " + "; ".join(cambios) + "."
        background_tasks.add_task(
            ack_cambio_identidad,
            request,
            user=user,
            settings=settings,
            worker_id=worker_id,
            resumen=resumen,
        )
    return await _get_one(session, user, worker_id)


@router.get("/{worker_id}/messages")
async def list_worker_messages(
    worker_id: uuid.UUID,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    from edecan_api.bot_turn_service import (
        ensure_worker_conversation,
        list_normalized_messages,
        load_worker,
    )

    worker = await load_worker(session, user, worker_id)
    conversation_id = await ensure_worker_conversation(session, user, worker)
    return await list_normalized_messages(
        session, tenant_id=user.tenant_id, conversation_id=conversation_id
    )


@router.post("/{worker_id}/clear", status_code=status.HTTP_204_NO_CONTENT)
async def clear_worker_messages(
    worker_id: uuid.UUID,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Comando `/clear` en el chat de un bot: reinicia su conversación.

    Similar al `/clear` del chat principal, pero acá se BORRAN los mensajes
    del hilo 1:1 con el bot (es su chat propio, no un contexto compartido):
    al volver, el bot arranca de cero. No toca el hilo entre bots ni la
    memoria persistente del bot.
    """
    from edecan_api.bot_turn_service import ensure_worker_conversation, load_worker

    worker = await load_worker(session, user, worker_id)
    conversation_id = await ensure_worker_conversation(session, user, worker)
    await session.execute(
        text(
            "DELETE FROM messages WHERE tenant_id = :tenant_id AND conversation_id = :cid"
        ),
        {"tenant_id": str(user.tenant_id), "cid": str(conversation_id)},
    )


@router.post("/{worker_id}/message")
async def send_worker_message(
    worker_id: uuid.UUID,
    body: PersistentAgentMessageIn,
    request: Request,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
):
    from fastapi.responses import StreamingResponse

    from edecan_api.bot_turn_service import (
        ensure_worker_conversation,
        load_worker,
        stream_worker_turn,
    )

    worker = await load_worker(session, user, worker_id)
    conversation_id = await ensure_worker_conversation(session, user, worker)

    # Un lock por worker garantiza el ORDEN de los turnos aunque el cliente
    # mande varios mensajes seguidos sin esperar respuesta (chat humano): los
    # turnos corren en secuencia de llegada, cada uno ve el historial con las
    # respuestas anteriores ya persistidas. Sin esto, dos POSTs concurrentes
    # producían respuestas fuera de orden.
    lock = _turn_lock_for(user.tenant_id, worker_id)

    async def _stream():
        async with lock:
            async for chunk in stream_worker_turn(
                request=request,
                session=session,
                user=user,
                settings=settings,
                worker=worker,
                conversation_id=conversation_id,
                user_text=body.text,
                attachments=body.attachments,
            ):
                yield chunk

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.delete("/{worker_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_worker(
    worker_id: uuid.UUID,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Elimina un bot y su chat 1:1, de una vez y sin basura colgando.

    El resto de referencias ya lo resuelve el esquema: handoffs, miembros de
    equipo/workspace y chats directos caen en CASCADE; misiones, sesiones de
    computadora y mensajes entre agentes quedan con `SET NULL` (historia que
    no muere con el bot). Lo que el esquema NO limpa por sí solo es la
    conversación del bot y sus mensajes — esos se borran aquí explícitamente.
    """
    from edecan_api.bot_turn_service import load_worker

    worker = await load_worker(session, user, worker_id)
    conversation_id = worker.get("conversation_id")
    if conversation_id is not None:
        await session.execute(
            text("DELETE FROM messages WHERE tenant_id = :tenant_id AND conversation_id = :cid"),
            {"tenant_id": str(user.tenant_id), "cid": str(conversation_id)},
        )
        await session.execute(
            text("DELETE FROM conversations WHERE tenant_id = :tenant_id AND id = :cid"),
            {"tenant_id": str(user.tenant_id), "cid": str(conversation_id)},
        )
    await session.execute(
        text(
            "DELETE FROM persistent_agents "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND id = :id"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "user_id": str(user.user_id),
            "id": str(worker_id),
        },
    )


@router.post("/{worker_id}/tasks", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_worker_task(
    worker_id: uuid.UUID,
    body: PersistentAgentTaskIn,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Encola una tarea explícita; el worker valida estado y presupuesto otra vez."""
    worker = await _get_one(session, user, worker_id)
    if not worker["enabled"] or worker["status"] in ("paused", "disabled", "running"):
        raise HTTPException(status_code=409, detail="El worker no está disponible.")
    task_id = body.task_id or str(uuid.uuid4())
    await enqueue(
        settings,
        "run_persistent_agent",
        {"worker_id": str(worker_id), "task_id": task_id, "instruction": body.instruction.strip()},
        user.tenant_id,
    )
    return {"worker_id": str(worker_id), "task_id": task_id, "status": "queued"}


@router.post("/pause-all")
async def pause_all_workers(
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Freno de emergencia (§178): pausa TODOS los workers activos del tenant.

    Marca `status='paused'` a todo worker en `idle` o `running` del tenant (no
    solo del usuario actual: es un freno de tenant). Devuelve cuántos se
    pausaron. Idempotente: los ya `paused`/`disabled` no cuentan.
    """
    try:
        result = await session.execute(
            text(
                "UPDATE persistent_agents SET status = 'paused', updated_at = now() "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id "
                "AND status IN ('idle', 'running')"
            ),
            {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id)},
        )
    except (ProgrammingError, SQLAlchemyError):
        logger.exception("pause_all_workers: error de base")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No pude pausar los workers.",
        ) from None
    return {"paused": int(getattr(result, "rowcount", 0) or 0)}


@router.get("/handoffs")
async def list_worker_handoffs(
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    status_filter: str | None = Query(default="pending"),
) -> list[dict[str, Any]]:
    try:
        result = await session.execute(
            text(
                "SELECT h.id, h.destination_worker_id, h.task_id, h.envelope, h.status, "
                "h.created_at, h.updated_at, destination.name AS destination_name "
                "FROM persistent_agent_handoffs h "
                "JOIN persistent_agents destination ON destination.id = h.destination_worker_id "
                "WHERE h.tenant_id = :tenant_id AND destination.user_id = :user_id "
                "AND (CAST(:status AS text) IS NULL OR h.status = :status) "
                "ORDER BY h.created_at DESC"
            ),
            {
                "tenant_id": str(user.tenant_id),
                "user_id": str(user.user_id),
                "status": status_filter,
            },
        )
        return [_public_row(row) for row in result.mappings().all()]
    except ProgrammingError:
        logger.exception("list_worker_handoffs: esquema de handoffs no disponible")
        return []
    except SQLAlchemyError:
        logger.exception("list_worker_handoffs: error de base")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No pude leer los handoffs.",
        ) from None


@router.post("/{worker_id}/handoffs", status_code=status.HTTP_201_CREATED)
async def create_worker_handoff(
    worker_id: uuid.UUID,
    body: PersistentAgentHandoffIn,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Persiste un handoff pendiente; no lo ejecuta ni auto-aprueba."""
    await _get_one(session, user, worker_id)
    await _get_one(session, user, body.destination_worker_id)
    try:
        envelope = validate_handoff(
            source_worker_id=str(worker_id),
            destination_worker_id=str(body.destination_worker_id),
            task_id=body.task_id,
            depth=body.depth,
            visited_worker_ids=body.visited_worker_ids,
        )
        envelope["instruction"] = body.instruction.strip()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = await session.execute(
        text(
            "INSERT INTO persistent_agent_handoffs "
            "(id, tenant_id, source_worker_id, destination_worker_id, task_id, envelope) "
            "VALUES (gen_random_uuid(), :tenant_id, :source, :destination, :task_id, "
            ":envelope ::jsonb) "
            "RETURNING id, tenant_id, source_worker_id, destination_worker_id, task_id, "
            "envelope, status, result, created_at, updated_at"
        ),
        {
            "tenant_id": str(user.tenant_id),
            "source": str(worker_id),
            "destination": str(body.destination_worker_id),
            "task_id": body.task_id,
            "envelope": json.dumps(envelope),
        },
    )
    row = result.mappings().first()
    assert row is not None
    return _public_row(row)


@router.post("/handoffs/{handoff_id}/approve")
async def approve_worker_handoff(
    handoff_id: uuid.UUID,
    user: CurrentUser = Depends(_current),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Aprueba un handoff destinado al usuario y encola exactamente una tarea."""
    result = await session.execute(
        text(
            "SELECT h.id, h.destination_worker_id, h.task_id, h.envelope, h.status "
            "FROM persistent_agent_handoffs h "
            "JOIN persistent_agents destination ON destination.id = h.destination_worker_id "
            "WHERE h.tenant_id = :tenant_id AND h.id = :id AND destination.user_id = :user_id"
        ),
        {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id), "id": str(handoff_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Handoff no encontrado.")
    envelope = row["envelope"]
    if isinstance(envelope, str):
        envelope = json.loads(envelope)
    instruction = str((envelope or {}).get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="El handoff no tiene instrucción ejecutable.")
    r = await session.execute(
        text(
            "UPDATE persistent_agent_handoffs SET status = 'approved', updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :id AND status = 'pending'"
        ),
        {"tenant_id": str(user.tenant_id), "id": str(handoff_id)},
    )
    if int(getattr(r, "rowcount", 0) or 0) == 0:
        raise HTTPException(status_code=409, detail="Handoff ya no está pendiente.")
    await enqueue(
        settings,
        "run_persistent_agent",
        {
            "worker_id": str(row["destination_worker_id"]),
            "task_id": str(row["task_id"]),
            "instruction": instruction,
            "handoff_id": str(handoff_id),
        },
        user.tenant_id,
    )
    return {"handoff_id": str(handoff_id), "status": "approved", "task_id": row["task_id"]}
