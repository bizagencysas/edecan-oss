"""Tool `cambiar_rutina_gym`: el dueño pide otra rutina y Edecán la regenera.

"No quiero esa rutina, hazme otra de pecho y triceps" → esta tool regenera el
plan de HOY con ese objetivo (via `edecan_gym.plan.generar_plan`, el mismo
motor del check-in), reemplaza el plan del día y la sesión planeada queda
apuntando al nuevo. El collage se regenera en segundo plano (el router de gym
ya tiene ese flujo) y la card nueva llega al chat.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from edecan_core.tools.base import Tool, ToolContext, ToolResult
from sqlalchemy import text

logger = logging.getLogger(__name__)

_GYM_PLAN_FLAG = "gym"
_MENSAJE_SIN_PLAN = (
    "Aún no tienes un plan de gym activo que cambiar. Responde el check-in "
    "(«¿Vas a ir al gym hoy?» con Sí) y te armo la rutina del día; luego me "
    "dices «cámbiame la rutina» y la regeneramos."
)


def _fila_plan_de_hoy(session: Any, tenant_id: str, user_id: str) -> dict[str, Any] | None:
    fila = (
        session.execute(
            text(
                """
                SELECT wp.id, wp.titulo, wp.objetivo
                FROM workout_plans wp
                WHERE wp.tenant_id = CAST(:tenant_id AS uuid)
                  AND wp.user_id = CAST(:user_id AS uuid)
                  AND wp.fecha = :hoy
                ORDER BY wp.created_at DESC
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "hoy": date.today()},
        )
        .mappings()
        .first()
    )
    return dict(fila) if fila is not None else None


class CambiarRutinaGymTool(Tool):
    """El dueño pide otra rutina («hazme otra de pecho», «una de pierna y
    cardio») y el plan de HOY se regenera con ese objetivo."""

    name = "cambiar_rutina_gym"
    description = (
        "Regenera el plan de ENTRENAMIENTO de hoy del dueño con otro objetivo. "
        "Úsala cuando diga «no quiero esa rutina», «hazme otra de X», «cámbiame "
        "el plan por uno de pierna/cardio/fuerza…». Pide confirmación SOLO si "
        "no quedó claro qué quiere en la nueva rutina; si quedó claro, ejecuta "
        "y cuenta el plan nuevo en 2-3 frases."
    )
    category = "write"
    risk_level = "medium"
    requires_flags = frozenset({_GYM_PLAN_FLAG})
    input_schema = {
        "type": "object",
        "properties": {
            "peticion": {
                "type": "string",
                "description": (
                    "El objetivo o cambio pedido, en texto claro (ej. «pecho y "
                    "triceps», «pierna con cardio suave», «empuje más ligero, "
                    "tengo el hombro molesto»)."
                ),
            },
        },
        "required": ["peticion"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        peticion = str(args.get("peticion", "")).strip()
        if not peticion:
            return ToolResult(content="Dime qué quieres en la rutina nueva y la genero.")

        if ctx.session is None or ctx.llm is None:
            return ToolResult(content="No tengo el motor de rutinas disponible ahora mismo.")

        tenant_id = str(ctx.tenant_id)
        user_id = str(ctx.user_id)

        from edecan_gym.plan import generar_plan

        fila = _fila_plan_de_hoy(ctx.session, tenant_id, user_id)
        if fila is None:
            return ToolResult(content=_MENSAJE_SIN_PLAN)

        historial_rows = (
            ctx.session.execute(
                text(
                    """
                    SELECT wp.ejercicios AS plan_ejercicios, wp.fecha
                    FROM workout_plans wp
                    WHERE wp.tenant_id = CAST(:tenant_id AS uuid)
                      AND wp.user_id = CAST(:user_id AS uuid)
                    ORDER BY wp.fecha DESC LIMIT 6
                    """
                ),
                {"tenant_id": tenant_id, "user_id": user_id},
            )
            .mappings()
            .all()
        )
        historial = [
            {"plan": {"ejercicios": [e for e in (row["plan_ejercicios"] or [])]}}
            for row in historial_rows
        ]

        async def completar(system: str, user: str) -> str:
            from edecan_llm import CompletionRequest
            from edecan_schemas import ChatMessage

            respuesta = await ctx.llm.complete(
                "principal",
                dict(extras_flags) if (extras_flags := ctx.extras.get("flags")) else {},
                CompletionRequest(
                    model="principal",
                    system=system,
                    messages=[ChatMessage(role="user", content=user)],
                    max_tokens=2200,
                ),
            )
            return respuesta.text

        plan = await generar_plan(
            completar,
            persona=None,
            historial=historial,
            objetivo=f"{peticion}. Importante: el dueño PIDIÓ este cambio; "
            "reemplaza la rutina completa para que encaje con el pedido.",
        )

        ctx.session.execute(
            text(
                """
                UPDATE workout_plans
                SET titulo = :titulo, objetivo = :objetivo, duracion_min = :duracion,
                    ejercicios = CAST(:ejercicios AS jsonb), updated_at = now()
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {
                "titulo": plan.titulo,
                "objetivo": plan.objetivo,
                "duracion": plan.duracion_min,
                "ejercicios": __import__("json").dumps(
                    [e.to_dict() for e in plan.ejercicios], ensure_ascii=False
                ),
                "id": str(fila["id"]),
            },
        )
        await ctx.session.commit()

        resumen = "; ".join(
            f"{e.nombre} ({e.series}x{e.repeticiones})" for e in plan.ejercicios[:6]
        )
        return ToolResult(
            content=(
                f"Listo, cambié la rutina de hoy: «{plan.titulo}» "
                f"({plan.duracion_min} min). Ejercicios: {resumen}. "
                "Dile al dueño el plan nuevo en 2 frases con tu voz y "
                "recuérdale tocar 'Iniciar' en Entrenamiento."
            ),
            data={
                "plan_id": str(fila["id"]),
                "plan": plan.to_dict(),
            },
        )


TOOL = CambiarRutinaGymTool()
