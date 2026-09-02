"""`GET /v1/activity` — feed reciente de acciones observables (directiva §17, §209).

Devuelve las últimas ~50 acciones del tenant, sin chain-of-thought ni contenido
privado: solo lo que YA quedó durable en tablas observables y es seguro mostrar.

Fuentes (mejor-esfuerzo, una por tabla, luego merge y orden por tiempo):

- `messages.tool_calls` — completions con llamadas a herramientas del chat.
- `agent_steps` — pasos de misión ejecutados (con el `resultado`).
- `pending_approvals` — confirmaciones `dangerous` pedidas/decididas.
- `computer_sessions` — transiciones de superficies (toma de control).

Cada ítem: `{type, agent (name | null), summary, at (iso), status}`. El `summary`
se recorta (~180 chars) y solo expone nombres/herramientas/estado, nunca
argumentos completos, resultados crudos ni razonamiento.

Este router no se monta solo: `edecan_api.main` lo monta de forma defensiva
(igual que `gym`), por eso solo declara `router`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

router = APIRouter(prefix="/v1/activity", tags=["activity"], dependencies=[Depends(rate_limit)])

_SUMMARY_MAX_CHARS = 180


class ActivityItem(BaseModel):
    type: str
    agent: str | None = None
    summary: str
    at: str
    status: str


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return "" if value is None else str(value)


def _ts_sort(value: Any) -> Any:
    """Clave de orden: un `datetime` se compara directo; `None` va al final."""
    return value if isinstance(value, datetime) else datetime.min


def _recorte(texto: str, limite: int = _SUMMARY_MAX_CHARS) -> str:
    if not texto:
        return ""
    texto = " ".join(str(texto).split())
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


def _nombre_tool(tool_calls: Any) -> str | None:
    """Primer nombre de tool de un `tool_calls` JSONB, tolerante a dos formas
    (`{name}` o `{function: {name}}`)."""
    if not isinstance(tool_calls, list):
        return None
    for llamada in tool_calls:
        if not isinstance(llamada, dict):
            continue
        nombre = llamada.get("name")
        if not isinstance(nombre, str) or not nombre:
            funcion = llamada.get("function")
            if isinstance(funcion, dict):
                nombre = funcion.get("name")
        if isinstance(nombre, str) and nombre:
            return nombre
    return None


async def _tool_calls(session: AsyncSession, tenant_id: str, limite: int) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT tool_calls, created_at FROM messages "
            "WHERE tenant_id = :tenant_id AND tool_calls IS NOT NULL "
            "ORDER BY created_at DESC LIMIT :limite"
        ),
        {"tenant_id": tenant_id, "limite": limite},
    )
    items: list[dict[str, Any]] = []
    for row in result.mappings().all():
        nombre = _nombre_tool(row.get("tool_calls"))
        if not nombre:
            continue
        items.append(
            {
                "type": "tool_call",
                "agent": None,
                "summary": f"Usó la herramienta «{nombre}»",
                "at": _iso(row.get("created_at")),
                "status": "done",
                "_ts": row.get("created_at"),
            }
        )
    return items


async def _mission_steps(
    session: AsyncSession, tenant_id: str, limite: int
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT s.agente, s.instruccion, s.resultado, s.status, s.updated_at, "
            "pa.name AS agent "
            "FROM agent_steps s "
            "JOIN agent_missions m ON m.id = s.mission_id AND m.tenant_id = s.tenant_id "
            "LEFT JOIN persistent_agents pa ON pa.id = m.owner_agent_id "
            "WHERE s.tenant_id = :tenant_id "
            "ORDER BY s.updated_at DESC LIMIT :limite"
        ),
        {"tenant_id": tenant_id, "limite": limite},
    )
    items: list[dict[str, Any]] = []
    for row in result.mappings().all():
        cuerpo = row.get("resultado") or row.get("instruccion") or ""
        items.append(
            {
                "type": "mission_step",
                "agent": row.get("agent"),
                "summary": _recorte(str(cuerpo)),
                "at": _iso(row.get("updated_at")),
                "status": str(row.get("status") or ""),
                "_ts": row.get("updated_at"),
            }
        )
    return items


async def _approvals(session: AsyncSession, tenant_id: str, limite: int) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT agent_snapshot, status, updated_at FROM pending_approvals "
            "WHERE tenant_id = :tenant_id "
            "ORDER BY updated_at DESC LIMIT :limite"
        ),
        {"tenant_id": tenant_id, "limite": limite},
    )
    items: list[dict[str, Any]] = []
    for row in result.mappings().all():
        snapshot = row.get("agent_snapshot")
        nombre = ""
        if isinstance(snapshot, dict):
            nombre = snapshot.get("name") or ""
        estado = str(row.get("status") or "")
        resumen = f"Confirmación de «{nombre}»" if nombre else "Confirmación pendiente"
        items.append(
            {
                "type": "approval",
                "agent": None,
                "summary": resumen,
                "at": _iso(row.get("updated_at")),
                "status": estado,
                "_ts": row.get("updated_at"),
            }
        )
    return items


async def _computer_sessions(
    session: AsyncSession, tenant_id: str, limite: int
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT cs.kind, cs.mode, cs.status, cs.updated_at, pa.name AS agent "
            "FROM computer_sessions cs "
            "LEFT JOIN persistent_agents pa ON pa.id = cs.agent_id "
            "WHERE cs.tenant_id = :tenant_id "
            "ORDER BY cs.updated_at DESC LIMIT :limite"
        ),
        {"tenant_id": tenant_id, "limite": limite},
    )
    items: list[dict[str, Any]] = []
    for row in result.mappings().all():
        items.append(
            {
                "type": "computer_session",
                "agent": row.get("agent"),
                "summary": f"Superficie {row.get('kind') or 'desktop'}: {row.get('mode') or ''}",
                "at": _iso(row.get("updated_at")),
                "status": str(row.get("status") or ""),
                "_ts": row.get("updated_at"),
            }
        )
    return items


@router.get("", response_model=list[ActivityItem])
async def list_activity(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ActivityItem]:
    tenant_id = str(current_user.tenant_id)
    items: list[dict[str, Any]] = []
    try:
        items.extend(await _tool_calls(session, tenant_id, limit))
        items.extend(await _mission_steps(session, tenant_id, limit))
        items.extend(await _approvals(session, tenant_id, limit))
        items.extend(await _computer_sessions(session, tenant_id, limit))
    except ProgrammingError:
        # Alguna de las tablas fuente aún no existe en esta instalación: se
        # devuelve lo que sí se pudo leer (best-effort, directiva §17).
        items = [it for it in items if it.get("type") != ""]
    except SQLAlchemyError:
        raise

    items.sort(key=lambda item: _ts_sort(item.get("_ts")), reverse=True)
    return [
        ActivityItem(
            type=item["type"],
            agent=item.get("agent"),
            summary=item["summary"],
            at=item["at"],
            status=item["status"],
        )
        for item in items[:limit]
    ]