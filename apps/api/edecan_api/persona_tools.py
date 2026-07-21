"""Herramientas reversibles para elegir cómo acompaña Edecan desde el chat.

Viven en la API (y se ofrecen solo en conversaciones personales) porque
persisten una preferencia del usuario mediante ``Repo.upsert_persona``. No
forman parte del registry global: una misión o automatización no debe cambiar
la relación del asistente con la persona.
"""

from __future__ import annotations

from typing import Any

from edecan_core.tools import Tool, ToolContext, ToolResult

_NON_ROMANTIC_STYLES = frozenset({"profesional", "coach", "amigo"})
_UPDATER_KEY = "persona_updater"


async def _persist(ctx: ToolContext, fields: dict[str, Any]) -> ToolResult | None:
    updater = ctx.extras.get(_UPDATER_KEY)
    if not callable(updater):
        return ToolResult(
            content=(
                "No pude guardar esa preferencia en este contexto. Puedes cambiarla desde Ajustes."
            ),
            data={"updated": False},
        )
    await updater(fields=fields)
    return None


class ConfigurarEstiloRelacionTool(Tool):
    name = "configurar_estilo_relacion"
    description = (
        "Cambia cómo Edecan acompaña a la persona entre profesional, coach o amigo. "
        "Úsala cuando la persona pida explícitamente uno de esos estilos. Es reversible."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "estilo": {
                "type": "string",
                "enum": ["profesional", "coach", "amigo"],
                "description": "Estilo solicitado explícitamente por la persona.",
            }
        },
        "required": ["estilo"],
        "additionalProperties": False,
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        estilo = str(args.get("estilo", "")).strip().lower()
        if estilo not in _NON_ROMANTIC_STYLES:
            return ToolResult(
                content=(
                    "Elige profesional, coach o amigo. El estilo romántico tiene su propio "
                    "flujo de consentimiento."
                ),
                data={"updated": False},
            )
        error = await _persist(
            ctx,
            {
                "estilo_relacion": estilo,
                "adulto_confirmado": False,
                "consentimiento_romantico": False,
            },
        )
        if error is not None:
            return error
        return ToolResult(
            content=(
                f"Listo: el estilo quedó en «{estilo}». Se aplicará desde el próximo mensaje "
                "y puedes cambiarlo cuando quieras."
            ),
            data={"updated": True, "estilo_relacion": estilo},
        )


class ActivarEstiloRomanticoTool(Tool):
    name = "activar_estilo_romantico"
    description = (
        "Activa un estilo cariñoso/coqueto de IA. Solo úsala si la persona afirma explícitamente "
        "que tiene 18 años o más y que consiente activar este estilo sabiendo que Edecan es una IA."
    )
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "adulto_confirmado": {
                "type": "boolean",
                "description": (
                    "True solo si la persona confirmó explícitamente que tiene 18 años o más."
                ),
            },
            "consentimiento_explicito": {
                "type": "boolean",
                "description": "True solo si consintió explícitamente y sabe que Edecan es una IA.",
            },
        },
        "required": ["adulto_confirmado", "consentimiento_explicito"],
        "additionalProperties": False,
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        if args.get("adulto_confirmado") is not True:
            return ToolResult(
                content=(
                    "Antes de activarlo necesito que confirmes explícitamente "
                    "que tienes 18 años o más."
                ),
                data={"updated": False},
            )
        if args.get("consentimiento_explicito") is not True:
            return ToolResult(
                content=(
                    "Antes de activarlo necesito tu consentimiento explícito y que quede claro "
                    "que Edecan es una IA, no una persona con sentimientos reales."
                ),
                data={"updated": False},
            )
        error = await _persist(
            ctx,
            {
                "estilo_relacion": "romantico",
                "adulto_confirmado": True,
                "consentimiento_romantico": True,
            },
        )
        if error is not None:
            return error
        return ToolResult(
            content=(
                "Estilo romántico activado. Es un tono afectuoso de una IA, no una relación "
                "humana ni sentimientos reales. Puedes decir «sal del estilo romántico» y se "
                "desactiva inmediatamente."
            ),
            data={"updated": True, "estilo_relacion": "romantico"},
        )


class SalirEstiloRomanticoTool(Tool):
    name = "salir_estilo_romantico"
    description = (
        "Sale inmediatamente del estilo romántico y vuelve al estilo profesional. Úsala ante "
        "cualquier petición de parar, salir, terminar o volver al trato normal; no pidas "
        "confirmación."
    )
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        error = await _persist(
            ctx,
            {
                "estilo_relacion": "profesional",
                "adulto_confirmado": False,
                "consentimiento_romantico": False,
            },
        )
        if error is not None:
            return error
        return ToolResult(
            content="Listo. El estilo romántico terminó y Edecan volvió al estilo profesional.",
            data={"updated": True, "estilo_relacion": "profesional"},
        )


def conversation_persona_tools() -> list[Tool]:
    """Instancias nuevas para un turno, sin estado compartido entre usuarios."""
    return [
        ConfigurarEstiloRelacionTool(),
        ActivarEstiloRomanticoTool(),
        SalirEstiloRomanticoTool(),
    ]
