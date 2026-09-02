"""`agent_bus` — escritor mínimo del protocolo inter-agente (product design).

`enviar_mensaje_agente` inserta una fila en `agent_messages` (migración
`0057_agent_messages`, modelo `edecan_db.models.AgentMessage`) usando SQL
parametrizado contra los nombres de tabla/columna pinned del esquema — mismo
criterio que `tools.DelegarMisionTool`: no importa el ORM de `edecan_db.models`
(esa forma interna no está fijada por contrato), los nombres de tabla/columna
sí lo están.

## Context packaging (§12)

`context_refs`/`artifact_refs`/`dependencies` reciben SOLO referencias
(`{"kind": "...", "id": "<uuid>"}`), nunca el transcript completo. El receptor
debe resolver esas referencias con sus propias consultas (scoped por tenant vía
RLS) y armar el contexto mínimo que necesita. Volcar el transcript aquí
duplicaría contexto sensible y rompería la regla "el receptor solo recibe lo
necesario".
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text

MESSAGE_TYPES: frozenset[str] = frozenset(
    {
        "task",
        "question",
        "result",
        "blocker",
        "review_request",
        "handoff",
        "status",
        "cancel",
    }
)
"""Vocabulario de `message_type`, en minúsculas (mismo criterio que el resto de
enums pinned del esquema). El spec §12 los escribe en mayúsculas
(TASK/QUESTION/...); `enviar_mensaje_agente` normaliza a minúsculas."""

MESSAGE_STATUSES: frozenset[str] = frozenset(
    {"pending", "delivered", "acknowledged", "done", "error"}
)


def _jsonb(value: Any) -> str | None:
    """`None` → SQL NULL; cualquier otra cosa → texto JSONB."""
    return json.dumps(value, ensure_ascii=False) if value is not None else None


async def enviar_mensaje_agente(
    session: Any,
    *,
    tenant_id: str,
    sender: str | None = None,
    receiver: str | None = None,
    tipo: str,
    task_id: str | None = None,
    parent_task_id: str | None = None,
    conversation_id: str | None = None,
    goal: str | None = None,
    expected_output: str | None = None,
    priority: str | None = None,
    deadline: Any = None,
    dependencies: Any = None,
    allowed_tools: Any = None,
    approval_boundary: Any = None,
    artifact_refs: Any = None,
    context_refs: Any = None,
    status: str = "pending",
) -> str:
    """Inserta un mensaje inter-agente y devuelve su `id`.

    `sender`/`receiver` son IDs de worker persistente (texto UUID) o `None`
    (asistente principal/usuario). `tipo` se normaliza a minúsculas y se valida
    contra `MESSAGE_TYPES`; `status` contra `MESSAGE_STATUSES` — un valor
    inválido lanza `ValueError` (fail-fast en el borde del protocolo, nunca un
    CHECK de la base en runtime).

    Todos los campos JSON (`dependencies`/`allowed_tools`/`approval_boundary`/
    `artifact_refs`/`context_refs`) aceptan listas/dicts y se serializan a
    JSONB; `None` queda como SQL NULL. No se valida su contenido: el contrato
    "solo referencias" de `context_refs`/`artifact_refs` es responsabilidad del
    caller (ver docstring del módulo).
    """
    message_type = str(tipo).strip().lower()
    if message_type not in MESSAGE_TYPES:
        raise ValueError(
            f"message_type inválido {tipo!r}; usa uno de: {', '.join(sorted(MESSAGE_TYPES))}."
        )
    if status not in MESSAGE_STATUSES:
        raise ValueError(
            f"status inválido {status!r}; usa uno de: {', '.join(sorted(MESSAGE_STATUSES))}."
        )

    message_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO agent_messages "
            "(id, tenant_id, sender_agent_id, receiver_agent_id, task_id, parent_task_id, "
            "conversation_id, message_type, goal, expected_output, priority, deadline, "
            "dependencies, allowed_tools, approval_boundary, artifact_refs, context_refs, status) "
            "VALUES (:id, :tenant_id, :sender, :receiver, :task_id, :parent_task_id, "
            ":conversation_id, :message_type, :goal, :expected_output, :priority, :deadline, "
            ":dependencies ::jsonb, :allowed_tools ::jsonb, :approval_boundary ::jsonb, "
            ":artifact_refs ::jsonb, :context_refs ::jsonb, :status)"
        ),
        {
            "id": str(message_id),
            "tenant_id": tenant_id,
            "sender": sender,
            "receiver": receiver,
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "conversation_id": conversation_id,
            "message_type": message_type,
            "goal": goal,
            "expected_output": expected_output,
            "priority": priority,
            "deadline": deadline,
            "dependencies": _jsonb(dependencies),
            "allowed_tools": _jsonb(allowed_tools),
            "approval_boundary": _jsonb(approval_boundary),
            "artifact_refs": _jsonb(artifact_refs),
            "context_refs": _jsonb(context_refs),
            "status": status,
        },
    )
    return str(message_id)
