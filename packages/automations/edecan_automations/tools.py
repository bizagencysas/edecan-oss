"""`gestionar_automatizacion` — la única `Tool` de este paquete
(`ROADMAP_V2.md` §7.7).

Entry point `edecan.tools` → `edecan_automations:get_all_tools` (ver
`[project.entry-points]` en `pyproject.toml`), que
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")` descubre
automáticamente.

Igual que `packages/toolkit/edecan_toolkit/*.py`: acceso a datos con
`sqlalchemy.text()` sobre `ctx.session`, contra el esquema pinned en
`ROADMAP_V2.md` §7.4 (`automations`) — no importa `edecan_db.models` (ver
`README.md` del paquete).

**`dangerous=True` cubre las CUATRO acciones, no solo crear/activar.**
`edecan_core.tools.Tool.dangerous` es un atributo de CLASE (fijo por tool,
`ARCHITECTURE.md` §10.7): el framework no soporta "esta tool es peligrosa
solo si `args["accion"] == "crear"`" sin cambiar ese contrato, que este
paquete no posee. Entre marcar TODA la tool `dangerous` (lo que hace
`listar`/`desactivar` pasar innecesariamente por el gate de confirmación
humana) o dejar `crear`/`activar` sin ese gate (inaceptable: activar una
automatización empieza a correr instrucciones de agente sin supervisión), se
elige lo primero — el costo es una confirmación de más para dos de las
cuatro acciones, no una automatización corriendo sin que nadie la haya
aprobado nunca.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text

from .engine import compute_next_run, validate_accion, validate_trigger

FLAG_AUTOMATIONS_RULES = "automations.rules"
LIMIT_AUTOMATIONS_ACTIVE = "limits.automations_active"
_UNLIMITED = -1
_LIMITE_LISTADO = 20
_MSG_LIMITE_ALCANZADO = "Alcanzaste el límite de automatizaciones activas de tu plan."


def _tenant_flags(ctx: ToolContext) -> dict[str, Any]:
    """Mismo helper que `edecan_toolkit.contenido._tenant_flags` (duplicado a
    propósito: `packages/automations` no depende de `packages/toolkit`)."""
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    flags = extras.get("flags")
    return flags if isinstance(flags, dict) else {}


def _from_jsonb(value: Any) -> dict[str, Any]:
    """El driver puede devolver una columna `jsonb` como `str` crudo (sin el
    codec de `pgvector`/JSON registrado) — mismo gotcha que
    `edecan_toolkit.contactos._desde_jsonb` para columnas `jsonb` de lista."""
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value) if value else {}


class GestionarAutomatizacionTool(Tool):
    name = "gestionar_automatizacion"
    description = (
        "Crea, lista, activa o desactiva automatizaciones del usuario: reglas que disparan una "
        "instrucción del agente en modo headless según una agenda (rrule RFC 5545). Crear o "
        "activar requiere confirmación humana explícita antes de ejecutarse. Esta herramienta "
        "solo crea automatizaciones por agenda — las de webhook se crean desde el panel."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "accion": {
                "type": "string",
                "enum": ["crear", "listar", "activar", "desactivar"],
                "description": "Qué operación realizar.",
            },
            "nombre": {
                "type": "string",
                "description": "Nombre de la automatización (para 'crear').",
            },
            "rrule": {
                "type": "string",
                "description": (
                    "Regla de recurrencia RFC 5545, ej. 'FREQ=DAILY;BYHOUR=9' (para 'crear'). "
                    "Esta herramienta solo agenda por rrule, nunca crea disparadores webhook."
                ),
            },
            "instruccion": {
                "type": "string",
                "description": (
                    "Instrucción que el agente ejecutará en cada corrida (para 'crear')."
                ),
            },
            "automation_id": {
                "type": "string",
                "description": "ID de la automatización a activar/desactivar.",
            },
        },
        "required": ["accion"],
    }
    requires_flags = frozenset({FLAG_AUTOMATIONS_RULES})
    dangerous = True

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        accion = args.get("accion")
        if accion == "crear":
            return await self._crear(ctx, args)
        if accion == "listar":
            return await self._listar(ctx)
        if accion == "activar":
            return await self._activar(ctx, args)
        if accion == "desactivar":
            return await self._desactivar(ctx, args)
        return ToolResult(
            content=f"accion inválida: {accion!r}. Debe ser crear, listar, activar o desactivar."
        )

    async def _bajo_limite(self, ctx: ToolContext) -> bool:
        limite = _tenant_flags(ctx).get(LIMIT_AUTOMATIONS_ACTIVE, 0)
        if limite == _UNLIMITED:
            return True
        resultado = await ctx.session.execute(
            text(
                "SELECT COUNT(*) FROM automations WHERE tenant_id = :tenant_id AND enabled = true"
            ),
            {"tenant_id": str(ctx.tenant_id)},
        )
        activas = resultado.scalar_one()
        return activas < limite

    async def _crear(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        nombre = str(args.get("nombre", "")).strip()
        rrule = str(args.get("rrule", "")).strip()
        instruccion = str(args.get("instruccion", "")).strip()
        if not nombre or not rrule or not instruccion:
            return ToolResult(
                content="Para crear una automatización necesito 'nombre', 'rrule' e 'instruccion'."
            )

        trigger = {"kind": "schedule", "rrule": rrule}
        accion_def = {"kind": "agent_instruction", "instruccion": instruccion}
        try:
            validate_trigger(trigger)
            validate_accion(accion_def)
            next_run_at = compute_next_run(rrule, after=datetime.now(UTC))
        except ValueError as exc:
            return ToolResult(content=str(exc))

        if not await self._bajo_limite(ctx):
            return ToolResult(content=_MSG_LIMITE_ALCANZADO)

        fila = (
            await ctx.session.execute(
                text(
                    """
                    INSERT INTO automations (
                        id, tenant_id, user_id, nombre, descripcion, trigger, accion, enabled,
                        next_run_at
                    ) VALUES (
                        :id, :tenant_id, :user_id, :nombre, '', CAST(:trigger AS jsonb),
                        CAST(:accion AS jsonb), true, :next_run_at
                    )
                    RETURNING id
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": str(ctx.tenant_id),
                    "user_id": str(ctx.user_id),
                    "nombre": nombre,
                    "trigger": json.dumps(trigger),
                    "accion": json.dumps(accion_def),
                    "next_run_at": next_run_at,
                },
            )
        ).mappings().first()
        automation_id = fila["id"] if fila else None

        return ToolResult(
            content=f"Creé la automatización «{nombre}»: corre según '{rrule}'.",
            data={
                "id": str(automation_id) if automation_id is not None else None,
                "next_run_at": next_run_at.isoformat() if next_run_at else None,
            },
        )

    async def _listar(self, ctx: ToolContext) -> ToolResult:
        resultado = await ctx.session.execute(
            text(
                "SELECT id, nombre, enabled, trigger, next_run_at, last_run_at FROM automations "
                "WHERE tenant_id = :tenant_id ORDER BY created_at DESC LIMIT :limite"
            ),
            {"tenant_id": str(ctx.tenant_id), "limite": _LIMITE_LISTADO},
        )
        filas = resultado.mappings().all()

        if not filas:
            return ToolResult(
                content="No tienes automatizaciones todavía.", data={"automatizaciones": []}
            )

        lineas: list[str] = []
        automatizaciones: list[dict[str, Any]] = []
        for i, fila in enumerate(filas, start=1):
            trigger = _from_jsonb(fila["trigger"])
            estado = "activa" if fila["enabled"] else "desactivada"
            es_agenda = trigger.get("kind") == "schedule"
            detalle_trigger = f"agenda '{trigger.get('rrule')}'" if es_agenda else "webhook"
            lineas.append(f"{i}. [{estado}] {fila['nombre']} — {detalle_trigger}")
            automatizaciones.append(
                {
                    "id": str(fila["id"]),
                    "nombre": fila["nombre"],
                    "enabled": fila["enabled"],
                    "trigger": trigger,
                    "next_run_at": fila["next_run_at"].isoformat() if fila["next_run_at"] else None,
                    "last_run_at": fila["last_run_at"].isoformat() if fila["last_run_at"] else None,
                }
            )

        return ToolResult(content="\n".join(lineas), data={"automatizaciones": automatizaciones})

    async def _set_enabled(
        self, ctx: ToolContext, args: dict[str, Any], *, enabled: bool
    ) -> ToolResult:
        automation_id = str(args.get("automation_id", "")).strip()
        if not automation_id:
            return ToolResult(content="Falta 'automation_id'.")

        if enabled and not await self._bajo_limite(ctx):
            return ToolResult(content=_MSG_LIMITE_ALCANZADO)

        fila = (
            await ctx.session.execute(
                text(
                    "UPDATE automations SET enabled = :enabled, updated_at = now() "
                    "WHERE tenant_id = :tenant_id AND id = :id RETURNING nombre"
                ),
                {"enabled": enabled, "tenant_id": str(ctx.tenant_id), "id": automation_id},
            )
        ).mappings().first()

        if fila is None:
            return ToolResult(content="No encontré esa automatización.")

        verbo = "Activé" if enabled else "Desactivé"
        return ToolResult(
            content=f"{verbo} la automatización «{fila['nombre']}».",
            data={"id": automation_id, "enabled": enabled},
        )

    async def _activar(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        return await self._set_enabled(ctx, args, enabled=True)

    async def _desactivar(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        return await self._set_enabled(ctx, args, enabled=False)


def get_all_tools() -> list[Tool]:
    """Entry point `edecan.tools` (ver `pyproject.toml` y `ToolRegistry.load_entry_points`)."""
    return [GestionarAutomatizacionTool()]
