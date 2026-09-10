"""Tool `cambiar_rutina_gym`: el dueño pide otra rutina y Edecán la regenera.

"No quiero esa rutina, hazme otra de pecho y triceps" → esta tool regenera el
plan de HOY con ese objetivo (via `edecan_gym.plan.generar_plan`, el mismo
motor del check-in), reemplaza el plan del día y la sesión planeada queda
apuntando al nuevo. El collage se regenera en segundo plano (el router de gym
ya tiene ese flujo) y la card nueva llega al chat.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
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
    # LLM interno (ctx.llm.complete) para reescribir la rutina.
    timeout_seconds = 130.0
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
        # SIN commit acá: `get_tenant_session` mantiene la transacción del
        # turno con `session.begin()`; un commit a mitad de turno la cerraba y
        # todo lo posterior (gate del dueño de la tool siguiente, add_message
        # final) reventaba con "closed transaction". El write se confirma con
        # el turno al cerrar el request.

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


def _jsonb_lista(valor: Any) -> list[Any]:
    if isinstance(valor, str):
        try:
            return json.loads(valor)
        except Exception:
            return []
    return valor if isinstance(valor, list) else []


class EditarEntrenamientoGymTool(Tool):
    """El dueño dice que HOY hizo ejercicios distintos: se cambia el ejercicio
    del plan de hoy (IA interpreta el nombre, igual que `swap-ejercicio`) y se
    anotan las series que de verdad hizo en la sesión. Todo contra el backend
    (SQL directo, tenant-scoped) — espeja el patrón de `CambiarRutinaGymTool`."""

    timeout_seconds = 130.0
    name = "editar_entrenamiento_gym"
    description = (
        "Edita lo que el dueño TRABAJÓ HOY en el gym: cambia un ejercicio del "
        "plan de hoy y anota las series reales que hizo. Úsala cuando diga "
        "«hice X en vez de Y», «cambié press inclinado por press banca», "
        "«hice 3 series de 10 con 80kg», «me salté X y en su lugar hice Z». "
        "Conecta con el backend, deja el plan y la sesión al día, y responde "
        "el cambio en 2-3 frases."
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
                    "Qué cambió o hizo el dueño hoy, en texto claro (ej. «en "
                    "vez de press inclinado hice press banca, 3 series de 10 "
                    "con 80kg»)."
                ),
            },
        },
        "required": ["peticion"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        peticion = str(args.get("peticion", "")).strip()
        if not peticion:
            return ToolResult(content="Dime qué cambió o qué hiciste hoy y lo anoto.")
        if ctx.session is None or ctx.llm is None:
            return ToolResult(content="No tengo el motor del gym disponible ahora mismo.")

        tenant_id = str(ctx.tenant_id)
        user_id = str(ctx.user_id)

        from edecan_gym.plan import Ejercicio

        fila = (
            ctx.session.execute(
                text(
                    """
                    SELECT wp.id, wp.titulo, wp.objetivo, wp.ejercicios
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
        if fila is None:
            return ToolResult(content=_MENSAJE_SIN_PLAN)
        plan_id = str(fila["id"])
        ejercicios = [Ejercicio.from_dict(e) for e in _jsonb_lista(fila["ejercicios"])]

        sesion_row = (
            ctx.session.execute(
                text(
                    """
                    SELECT ws.id, ws.estado, ws.series
                    FROM workout_sessions ws
                    WHERE ws.tenant_id = CAST(:tenant_id AS uuid)
                      AND ws.user_id = CAST(:user_id AS uuid)
                      AND ws.plan_id = CAST(:plan_id AS uuid)
                      AND ws.estado IN ('planned', 'active', 'paused')
                    ORDER BY ws.created_at DESC
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "user_id": user_id, "plan_id": plan_id},
            )
            .mappings()
            .first()
        )

        async def completar(system: str, user: str) -> str:
            from edecan_llm import CompletionRequest
            from edecan_schemas import ChatMessage

            respuesta = await ctx.llm.complete(
                "principal",
                dict(ctx.extras.get("flags") or {}),
                CompletionRequest(
                    model="principal",
                    system=system,
                    messages=[ChatMessage(role="user", content=user)],
                    max_tokens=1200,
                ),
            )
            return respuesta.text

        # Paso 1: interpretar el pedido contra el plan real de hoy.
        listado = "\n".join(
            f"#{i + 1}: {e.nombre} ({e.musculo}, {e.series}x{e.repeticiones})"
            for i, e in enumerate(ejercicios)
        )
        raw = await completar(
            _SYSTEM_INSTRUCTOR,
            (
                f"Plan de hoy del dueño:\n{listado}\n\n"
                f"El dueño dijo: «{peticion}».\n\n"
                "Decide: (a) si cambió un ejercicio por otro, y (b) las series "
                "que de verdad hizo. Devuelve ÚNICAMENTE JSON válido con esta "
                "forma:\n"
                '{"cambio": {"indice": N, "nombre": "nombre libre del nuevo '
                'ejercicio"} | null, "series": [{"indice": N, "repeticiones": '
                'int, "peso_kg": float|null}]}\n'
                "«indice» es 0-based del listado. Si no hubo cambio, usa "
                '"cambio": null. Si no hay series claras, usa "series": [].'
            ),
        )
        try:
            inicio, fin = raw.index("{"), raw.rindex("}") + 1
            datos = json.loads(raw[inicio:fin])
            cambio = datos.get("cambio")
            series = datos.get("series") or []
        except (ValueError, KeyError, TypeError):
            return ToolResult(
                content="No pude entender el cambio; dime de nuevo qué ejercicio "
                "cambiaste y cuántas series hiciste."
            )

        # Paso 2: aplicar el cambio de ejercicio (IA resuelve el nombre libre).
        resumen_cambio = ""
        if isinstance(cambio, dict) and cambio.get("nombre"):
            indice = int(cambio["indice"])
            if not 0 <= indice < len(ejercicios):
                return ToolResult(content="Ese número de ejercicio no existe en el plan de hoy.")
            anterior = ejercicios[indice].nombre
            raw_swap = await completar(
                _SYSTEM_INSTRUCTOR,
                (
                    f"El ejercicio actual es «{anterior}» "
                    f"({ejercicios[indice].musculo}, {ejercicios[indice].series} "
                    f"series x {ejercicios[indice].repeticiones}). El dueño lo "
                    f"cambió por «{cambio['nombre']}».\n"
                    "Devuelve ÚNICAMENTE JSON con un objeto ejercicio:\n"
                    '{"nombre": "...", "musculo": "...", "series": N, '
                    '"repeticiones": "N-M o N", "descanso_seg": N, '
                    '"notas": "cómo hacerlo y por qué encaja"}'
                ),
            )
            try:
                i2, f2 = raw_swap.index("{"), raw_swap.rindex("}") + 1
                nuevo = Ejercicio.from_dict(json.loads(raw_swap[i2:f2]))
            except (ValueError, KeyError, TypeError):
                return ToolResult(
                    content="El entrenador no pudo interpretar el ejercicio nuevo; "
                    "prueba con otro nombre."
                )
            ejercicios[indice] = nuevo
            ctx.session.execute(
                text(
                    """
                    UPDATE workout_plans
                    SET ejercicios = CAST(:ejercicios AS jsonb), updated_at = now()
                    WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tenant_id AS uuid)
                    """
                ),
                {
                    "ejercicios": json.dumps(
                        [e.to_dict() for e in ejercicios], ensure_ascii=False
                    ),
                    "id": plan_id,
                    "tenant_id": tenant_id,
                },
            )
            resumen_cambio = f"{anterior} → {nuevo.nombre}"

        # Paso 3: anotar las series reales en la sesión.
        anotadas = 0
        if sesion_row is not None and series:
            sesion_id = str(sesion_row["id"])
            series_existentes = _jsonb_lista(sesion_row["series"])
            for s in series:
                try:
                    idx = int(s["indice"])
                    reps = int(s["repeticiones"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not 0 <= idx < len(ejercicios) or reps <= 0:
                    continue
                series_existentes.append(
                    {
                        "ejercicio_idx": idx,
                        "repeticiones": reps,
                        "peso_kg": s.get("peso_kg"),
                        "en": datetime.now(UTC).isoformat(),
                    }
                )
                anotadas += 1
            if anotadas:
                ctx.session.execute(
                    text(
                        """
                        UPDATE workout_sessions
                        SET series = CAST(:series AS jsonb), updated_at = now()
                        WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tenant_id AS uuid)
                        """
                    ),
                    {
                        "series": json.dumps(series_existentes, ensure_ascii=False),
                        "id": sesion_id,
                        "tenant_id": tenant_id,
                    },
                )

        # SIN commit acá (mismo motivo que arriba): el write se confirma con
        # la transacción del turno al cerrar el request.

        partes = []
        if resumen_cambio:
            partes.append(f"cambié {resumen_cambio} en el plan de hoy")
        if anotadas:
            partes.append(f"anoté {anotadas} serie(s) en tu sesión")
        if not partes:
            return ToolResult(
                content="No detecté un cambio concreto. Dime qué ejercicio cambiaste "
                "o cuántas series hiciste y lo dejo al día."
            )
        return ToolResult(
            content=(
                "Listo: " + " y ".join(partes) + ". "
                "Dile al dueño el cambio en 2 frases con tu voz."
            ),
            data={"plan_id": plan_id, "cambio": resumen_cambio, "series_anotadas": anotadas},
        )


_SYSTEM_INSTRUCTOR = (
    "Eres un instructor de gimnasio profesional. Diseñas y ajustas planes de "
    "fuerza e hipertrofia, en español. No emites diagnósticos médicos; ante "
    "cualquier molestia remite al usuario a su médico. Responde ÚNICAMENTE con "
    "el JSON solicitado, sin texto adicional."
)


TOOL = CambiarRutinaGymTool()
