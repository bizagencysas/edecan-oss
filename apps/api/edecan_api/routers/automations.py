"""CRUD `/v1/automations` (ARCHITECTURE.md §10.12; ROADMAP_V2.md §7.4, §7.6,
§7.10, dueño WP-V2-07).

Igual que `edecan_api.routers.consents`/`edecan_premium.compliance`: las
tablas `automations`/`automation_runs` (ROADMAP_V2.md §7.4) son nuevas de v2,
pero este router no importa `edecan_db.models` (mismo criterio que v1
`edecan_toolkit`/`edecan_premium`, ver sus README: el contrato pinnea
tabla/columna, no una forma de ORM) — habla SQL parametrizado directo con
`sqlalchemy.text()` sobre `Depends(get_tenant_session)` (RLS activa,
ARCHITECTURE.md §2), en vez de extender el `Repo`/`SqlRepo` central de
`apps/api/edecan_api/repo.py` (ese
archivo no está en la lista de rutas que este paquete de trabajo puede
tocar, y así cada WP de dominio v2 queda auto-contenido: ver ROADMAP_V2.md
§2 punto 3). `edecan_automations.engine` sí se importa a nivel de módulo
(`validate_trigger`/`validate_accion`/`compute_next_run`): es puro, sin IO,
así que no hay ningún costo de import perezoso. `edecan_core.queue.enqueue`
también se importa arriba (no perezoso dentro del handler): mismo criterio
que `edecan_api.routers.conversations` (`edecan_core` es v1, ya estable) —
así los tests pueden monkeypatchear el símbolo `enqueue` ya importado en
este módulo, igual que `test_conversations.py` hace con el suyo.

**Webhooks**: al crear (o al pasar por PATCH un trigger `kind="webhook"`
nuevo) se genera `hook_secret` server-side con `secrets.token_urlsafe(24)` —
el cliente NUNCA propone su propio secreto. Ese secreto solo viaja en la
respuesta del `POST`/`PATCH` que lo generó (`hook_secret` + `hook_url`); toda
lectura posterior (`GET`) lo redacta a `{"kind": "webhook", "has_secret":
true}` — ver `_public_automation`. `POST /v1/hooks/{id}` (`routers/hooks.py`)
es quien de verdad lo verifica en cada llamada entrante.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from edecan_automations import engine
from edecan_core.queue import enqueue
from edecan_schemas import FLAG_AUTOMATIONS_RULES, LIMIT_AUTOMATIONS_ACTIVE
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

_UNLIMITED = -1
_RUNS_LIMIT_DEFAULT = 50


async def _require_automations_flag(current_user: CurrentUser = Depends(get_current_user)) -> None:
    """Gate de plan para TODO el router (§7.2: flag `automations.rules`)."""
    if not current_user.tenant.flags.get(FLAG_AUTOMATIONS_RULES, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Las automatizaciones no están disponibles en tu plan.",
        )


router = APIRouter(
    prefix="/v1/automations",
    tags=["automations"],
    dependencies=[Depends(rate_limit), Depends(_require_automations_flag)],
)


# ---------------------------------------------------------------------------
# Cuerpos de entrada
# ---------------------------------------------------------------------------


class AutomationIn(BaseModel):
    nombre: str = Field(min_length=1)
    descripcion: str = ""
    trigger: dict[str, Any]
    accion: dict[str, Any]
    enabled: bool = True


class AutomationPatch(BaseModel):
    nombre: str | None = None
    descripcion: str | None = None
    trigger: dict[str, Any] | None = None
    accion: dict[str, Any] | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Helpers de dominio (normalización, jsonb, serialización pública)
# ---------------------------------------------------------------------------


def _from_jsonb(value: Any) -> dict[str, Any]:
    """El driver puede devolver una columna `jsonb` como `str` crudo (sin el
    codec de JSON registrado) — mismo gotcha que
    `edecan_toolkit.contactos._desde_jsonb`/`edecan_automations.tools._from_jsonb`."""
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value) if value else {}


def _normalize_accion_in(accion_in: dict[str, Any]) -> dict[str, Any]:
    """Fuerza `kind="agent_instruction"` (única variante hoy, ROADMAP_V2.md
    §7.7) sin importar lo que traiga el cliente en ese campo — el JSON
    guardado siempre lo trae explícito (edecan_schemas.automations, "kind
    siempre estuvo en el JSON")."""
    return {
        "kind": "agent_instruction",
        "instruccion": str(accion_in.get("instruccion", "")).strip(),
        "agente": accion_in.get("agente"),
    }


def _normalize_trigger_in(trigger_in: dict[str, Any]) -> dict[str, Any]:
    kind = trigger_in.get("kind")
    if kind == "schedule":
        return {"kind": "schedule", "rrule": trigger_in.get("rrule")}
    if kind == "webhook":
        return {"kind": "webhook"}  # el hook_secret lo decide _prepare_trigger, no el cliente
    return dict(trigger_in)  # kind desconocido: se deja para que validate_trigger lo rechace


def _prepare_trigger(
    trigger_in: dict[str, Any], existing: dict[str, Any] | None
) -> tuple[dict[str, Any], str | None]:
    """Normaliza `trigger_in` y decide el `hook_secret` para `kind="webhook"`.

    Devuelve `(trigger_a_guardar, secreto_nuevo_o_None)` — `secreto_nuevo`
    solo viene poblado cuando se generó AHORA (primera vez que esta
    automatización es de tipo webhook, o pasa a serlo): es la única vez que
    el secreto debe salir en una respuesta HTTP (ver docstring del módulo).
    Si `existing` ya era `kind="webhook"` con secreto, se conserva tal cual
    -PATCH nunca rota el secreto en silencio; para eso haría falta un
    endpoint explícito de rotación, fuera del alcance de este WP.
    """
    normalized = _normalize_trigger_in(trigger_in)
    if normalized.get("kind") != "webhook":
        return normalized, None
    if existing and existing.get("kind") == "webhook" and existing.get("hook_secret"):
        return {"kind": "webhook", "hook_secret": existing["hook_secret"]}, None
    secreto = secrets.token_urlsafe(24)
    return {"kind": "webhook", "hook_secret": secreto}, secreto


def _next_run_for(trigger: dict[str, Any]) -> datetime | None:
    if trigger.get("kind") != "schedule":
        return None
    return engine.compute_next_run(trigger["rrule"], after=datetime.now(UTC))


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _public_automation(row: dict[str, Any], *, settings: Settings) -> dict[str, Any]:
    """Serialización pública de una fila `automations`: nunca expone
    `trigger.hook_secret` (ver docstring del módulo)."""
    trigger = _from_jsonb(row.get("trigger"))
    if trigger.get("kind") == "webhook":
        trigger = {
            "kind": "webhook",
            "has_secret": bool(trigger.get("hook_secret")),
            "hook_url": f"{settings.PUBLIC_BASE_URL}/v1/hooks/{row['id']}",
        }
    return {
        "id": str(row["id"]),
        "nombre": row["nombre"],
        "descripcion": row.get("descripcion") or "",
        "trigger": trigger,
        "accion": _from_jsonb(row.get("accion")),
        "enabled": bool(row["enabled"]),
        "next_run_at": _iso(row.get("next_run_at")),
        "last_run_at": _iso(row.get("last_run_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _raise_validation(exc: ValueError) -> None:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Acceso a datos (SQL parametrizado directo, ver docstring del módulo)
# ---------------------------------------------------------------------------


async def _count_enabled(session: AsyncSession, *, tenant_id: uuid.UUID) -> int:
    result = await session.execute(
        text("SELECT COUNT(*) FROM automations WHERE tenant_id = :tenant_id AND enabled = true"),
        {"tenant_id": tenant_id},
    )
    return result.scalar_one()


async def _check_limit(
    session: AsyncSession, *, tenant_id: uuid.UUID, flags: dict[str, Any]
) -> None:
    limite = flags.get(LIMIT_AUTOMATIONS_ACTIVE, 0)
    if limite == _UNLIMITED:
        return
    if await _count_enabled(session, tenant_id=tenant_id) >= limite:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Alcanzaste el límite de automatizaciones activas de tu plan.",
        )


async def _get_row(
    session: AsyncSession, *, tenant_id: uuid.UUID, automation_id: uuid.UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text("SELECT * FROM automations WHERE tenant_id = :tenant_id AND id = :id"),
        {"tenant_id": tenant_id, "id": automation_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _get_row_or_404(
    session: AsyncSession, *, tenant_id: uuid.UUID, automation_id: uuid.UUID
) -> dict[str, Any]:
    row = await _get_row(session, tenant_id=tenant_id, automation_id=automation_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Automatización no encontrada."
        )
    return row


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_automation(
    body: AutomationIn,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    accion = _normalize_accion_in(body.accion)
    trigger, nuevo_secreto = _prepare_trigger(body.trigger, existing=None)

    try:
        engine.validate_trigger(trigger)
        engine.validate_accion(accion)
        next_run_at = _next_run_for(trigger)
    except ValueError as exc:
        _raise_validation(exc)

    if body.enabled:
        await _check_limit(
            session, tenant_id=current_user.tenant_id, flags=current_user.tenant.flags
        )

    automation_id = uuid.uuid4()
    result = await session.execute(
        text(
            """
            INSERT INTO automations (
                id, tenant_id, user_id, nombre, descripcion, trigger, accion, enabled,
                next_run_at
            ) VALUES (
                :id, :tenant_id, :user_id, :nombre, :descripcion, CAST(:trigger AS jsonb),
                CAST(:accion AS jsonb), :enabled, :next_run_at
            )
            RETURNING *
            """
        ),
        {
            "id": automation_id,
            "tenant_id": current_user.tenant_id,
            "user_id": current_user.user_id,
            "nombre": body.nombre,
            "descripcion": body.descripcion,
            "trigger": json.dumps(trigger),
            "accion": json.dumps(accion),
            "enabled": body.enabled,
            "next_run_at": next_run_at,
        },
    )
    row = dict(result.mappings().first())

    out = _public_automation(row, settings=settings)
    if nuevo_secreto:
        out["hook_secret"] = nuevo_secreto
    return out


@router.get("")
async def list_automations(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT * FROM automations WHERE tenant_id = :tenant_id ORDER BY created_at DESC"
        ),
        {"tenant_id": current_user.tenant_id},
    )
    rows = result.mappings().all()
    return [_public_automation(dict(row), settings=settings) for row in rows]


@router.get("/{automation_id}")
async def get_automation(
    automation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    row = await _get_row_or_404(
        session, tenant_id=current_user.tenant_id, automation_id=automation_id
    )
    return _public_automation(row, settings=settings)


@router.patch("/{automation_id}")
async def update_automation(
    automation_id: uuid.UUID,
    body: AutomationPatch,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    current = await _get_row_or_404(
        session, tenant_id=current_user.tenant_id, automation_id=automation_id
    )

    set_parts: list[str] = []
    params: dict[str, Any] = {"id": automation_id, "tenant_id": current_user.tenant_id}
    nuevo_secreto: str | None = None

    if body.nombre is not None:
        set_parts.append("nombre = :nombre")
        params["nombre"] = body.nombre
    if body.descripcion is not None:
        set_parts.append("descripcion = :descripcion")
        params["descripcion"] = body.descripcion

    accion_a_validar = _from_jsonb(current.get("accion"))
    if body.accion is not None:
        accion_a_validar = _normalize_accion_in(body.accion)
    trigger_actual = _from_jsonb(current.get("trigger"))
    trigger_a_validar = trigger_actual
    if body.trigger is not None:
        trigger_a_validar, nuevo_secreto = _prepare_trigger(body.trigger, existing=trigger_actual)

    try:
        engine.validate_trigger(trigger_a_validar)
        engine.validate_accion(accion_a_validar)
    except ValueError as exc:
        _raise_validation(exc)

    if body.accion is not None:
        set_parts.append("accion = CAST(:accion AS jsonb)")
        params["accion"] = json.dumps(accion_a_validar)
    if body.trigger is not None:
        set_parts.append("trigger = CAST(:trigger AS jsonb)")
        params["trigger"] = json.dumps(trigger_a_validar)
        set_parts.append("next_run_at = :next_run_at")
        params["next_run_at"] = _next_run_for(trigger_a_validar)

    if body.enabled is not None:
        if body.enabled and not current["enabled"]:
            await _check_limit(
                session, tenant_id=current_user.tenant_id, flags=current_user.tenant.flags
            )
        set_parts.append("enabled = :enabled")
        params["enabled"] = body.enabled

    if not set_parts:
        row = current
    else:
        result = await session.execute(
            text(
                f"UPDATE automations SET {', '.join(set_parts)}, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :id RETURNING *"
            ),
            params,
        )
        updated = result.mappings().first()
        if updated is None:
            # defensivo: ya validamos arriba (_get_row_or_404) que la fila existe, pero
            # entre esa lectura y este UPDATE no hay bloqueo -- una petición concurrente
            # (p.ej. DELETE /v1/automations/{id}) pudo borrarla en esa ventana. Mismo
            # tratamiento que `routers/commerce.py` para su UPDATE ... RETURNING *.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Automatización no encontrada."
            )
        row = dict(updated)

    out = _public_automation(row, settings=settings)
    if nuevo_secreto:
        out["hook_secret"] = nuevo_secreto
    return out


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation(
    automation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    result = await session.execute(
        text("DELETE FROM automations WHERE tenant_id = :tenant_id AND id = :id"),
        {"tenant_id": current_user.tenant_id, "id": automation_id},
    )
    if not result.rowcount:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Automatización no encontrada."
        )


@router.post("/{automation_id}/probar", status_code=status.HTTP_202_ACCEPTED)
async def probar_automation(
    automation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Encola `run_automation` ya mismo, sin esperar a su próxima corrida
    agendada ni a un webhook — sirve para probar una automatización (incluso
    desactivada: probar no altera `enabled` ni `next_run_at`, así que no
    perturba su agenda real)."""
    await _get_row_or_404(
        session, tenant_id=current_user.tenant_id, automation_id=automation_id
    )

    await enqueue(
        settings, "run_automation", {"automation_id": str(automation_id)}, current_user.tenant_id
    )
    return {"queued": True}


@router.get("/{automation_id}/runs")
async def list_automation_runs(
    automation_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    await _get_row_or_404(session, tenant_id=current_user.tenant_id, automation_id=automation_id)

    result = await session.execute(
        text(
            "SELECT * FROM automation_runs WHERE tenant_id = :tenant_id "
            "AND automation_id = :automation_id ORDER BY started_at DESC LIMIT :limite"
        ),
        {
            "tenant_id": current_user.tenant_id,
            "automation_id": automation_id,
            "limite": _RUNS_LIMIT_DEFAULT,
        },
    )
    rows = result.mappings().all()
    return [
        {
            "id": str(row["id"]),
            "status": row["status"],
            "detalle": _from_jsonb(row.get("detalle")),
            "started_at": _iso(row.get("started_at")),
            "finished_at": _iso(row.get("finished_at")),
        }
        for row in rows
    ]
