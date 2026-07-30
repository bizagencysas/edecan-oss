"""Tutor educativo informativo: `tutor_leccion`, `tutor_evaluar`
(ROADMAP_V2.md §7.4/§7.7, WP-V2-11).

GUARDRAIL no negociable (ROADMAP_V2.md §8.3): contenido educativo generado
por IA, no una evaluación oficial — cada respuesta en el camino feliz termina
con `_disclaimers.DISCLAIMER_EDU` (vía `with_disclaimer("edu", ...)`).

`tutor_leccion` persiste la lección en `learning_progress.leccion` (jsonb) y
`tutor_evaluar` recupera la ÚLTIMA lección de ese `tema` (filtrando
tenant/user, `ORDER BY created_at DESC LIMIT 1`) para poder corregir sin que
el modelo tenga que repetir las respuestas correctas en cada turno — y sin
que el `content` de `tutor_leccion` las revele nunca (solo se muestran las
preguntas)."""

from __future__ import annotations

import json
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from edecan_llm.base import ChatMessage, CompletionRequest
from sqlalchemy import text

from ._disclaimers import with_disclaimer
from ._util import extraer_json_llm, tenant_flags

_NIVEL_DEFECTO = "inicial"
_MAX_EJEMPLOS = 8
_MAX_EJERCICIOS = 8

_SYSTEM_PROMPT_LECCION = (
    "Eres un tutor. Genera una lección clara sobre el tema pedido, en español, "
    "adaptada al nivel indicado. Devuelve ÚNICAMENTE un JSON con esta forma "
    'exacta, sin texto fuera del JSON:\n{"explicacion": "...", "ejemplos": '
    '["...", "..."], "ejercicios": [{"pregunta": "...", "respuesta_correcta": '
    '"..."}, ...]}\nIncluye entre 2 y 5 ejercicios variados.'
)

_SYSTEM_PROMPT_EVALUAR = (
    "Eres un tutor calificando respuestas de un estudiante. Sé tolerante a "
    "diferencias de redacción: lo que importa es si la IDEA es correcta, no "
    "las palabras exactas. Te doy una lista de (pregunta, respuesta correcta, "
    "respuesta del estudiante). Para cada una decide si es correcta y da un "
    "comentario breve (1 frase) en español, con ánimo. Devuelve ÚNICAMENTE un "
    'JSON: {"correcciones": [{"correcto": true|false, "comentario": "..."}, '
    "...]} en el MISMO ORDEN en que te di los pares."
)


class TutorLeccionTool(Tool):
    name = "tutor_leccion"
    description = (
        "Genera una lección corta (explicación + ejemplos + ejercicios) sobre un tema, "
        "y guarda el progreso. Muestra las preguntas SIN las respuestas correctas — usa "
        "tutor_evaluar después para corregir. Contenido educativo generado por IA."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "tema": {"type": "string", "description": "Tema de la lección."},
            "nivel": {
                "type": "string",
                "description": "Nivel del estudiante (ej. 'inicial', 'intermedio', 'avanzado').",
                "default": _NIVEL_DEFECTO,
            },
        },
        "required": ["tema"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        tema = str(args.get("tema") or "").strip()
        if not tema:
            return ToolResult(content="Dime sobre qué tema quieres la lección.")
        nivel = str(args.get("nivel") or "").strip() or _NIVEL_DEFECTO

        leccion_llm = await _generar_leccion_via_llm(ctx, tema, nivel)
        explicacion = str(leccion_llm.get("explicacion") or "").strip()
        ejemplos = [str(e) for e in (leccion_llm.get("ejemplos") or [])][:_MAX_EJEMPLOS]
        ejercicios = _normalizar_ejercicios(leccion_llm.get("ejercicios"))[:_MAX_EJERCICIOS]

        if not explicacion and not ejercicios:
            return ToolResult(
                content=f"No logré generar una lección de «{tema}»; intenta reformular el tema."
            )

        leccion = {
            "tema": tema,
            "nivel": nivel,
            "explicacion": explicacion,
            "ejemplos": ejemplos,
            "ejercicios": ejercicios,
        }
        await ctx.session.execute(
            text(
                "INSERT INTO learning_progress (tenant_id, user_id, tema, nivel, leccion) "
                "VALUES (:tenant_id, :user_id, :tema, :nivel, CAST(:leccion AS jsonb))"
            ),
            {
                "tenant_id": str(ctx.tenant_id),
                "user_id": str(ctx.user_id),
                "tema": tema,
                "nivel": nivel,
                "leccion": json.dumps(leccion),
            },
        )

        lineas = [f"Lección: {tema} (nivel: {nivel})", "", explicacion]
        if ejemplos:
            lineas.append("")
            lineas.append("Ejemplos:")
            lineas.extend(f"- {e}" for e in ejemplos)
        if ejercicios:
            lineas.append("")
            lineas.append("Ejercicios (piénsalos y luego pide tutor_evaluar para corregirlos):")
            lineas.extend(f"{i}. {e['pregunta']}" for i, e in enumerate(ejercicios, start=1))

        contenido = with_disclaimer("edu", "\n".join(lineas))
        return ToolResult(
            content=contenido, data={"tema": tema, "nivel": nivel, "n_ejercicios": len(ejercicios)}
        )


async def _generar_leccion_via_llm(ctx: ToolContext, tema: str, nivel: str) -> dict[str, Any]:
    respuesta = await ctx.llm.complete(
        "principal",
        tenant_flags(ctx),
        CompletionRequest(
            model="principal",
            system=_SYSTEM_PROMPT_LECCION,
            messages=[ChatMessage(role="user", content=f"Tema: {tema}\nNivel: {nivel}")],
            max_tokens=2048,
        ),
    )
    return extraer_json_llm(respuesta.text) or {}


def _normalizar_ejercicios(crudos: Any) -> list[dict[str, str]]:
    if not isinstance(crudos, list):
        return []
    normalizados = []
    for item in crudos:
        if not isinstance(item, dict):
            continue
        pregunta = str(item.get("pregunta") or "").strip()
        respuesta = str(item.get("respuesta_correcta") or "").strip()
        if pregunta and respuesta:
            normalizados.append({"pregunta": pregunta, "respuesta_correcta": respuesta})
    return normalizados


class TutorEvaluarTool(Tool):
    name = "tutor_evaluar"
    description = (
        "Corrige las respuestas del estudiante contra la última lección generada de ese "
        "tema (tutor_leccion), de forma tolerante a la redacción, y guarda el resultado. "
        "Contenido educativo generado por IA, no una evaluación oficial."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "tema": {
                "type": "string",
                "description": (
                    "Tema de la lección a evaluar (debe existir una previa de tutor_leccion)."
                ),
            },
            "respuestas": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Respuestas del estudiante, en el mismo orden que los ejercicios.",
            },
        },
        "required": ["tema", "respuestas"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        tema = str(args.get("tema") or "").strip()
        if not tema:
            return ToolResult(content="Dime de qué tema quieres que evalúe tus respuestas.")
        respuestas = args.get("respuestas")
        if not isinstance(respuestas, list) or not respuestas:
            return ToolResult(content="Mándame al menos una respuesta para evaluar.")

        fila = await _ultima_leccion(ctx, tema)
        if fila is None:
            return ToolResult(
                content=(
                    f"No encontré ninguna lección previa de «{tema}» — primero pide tutor_leccion."
                )
            )

        leccion = _a_dict(fila["leccion"])
        ejercicios = leccion.get("ejercicios") or []
        if not ejercicios:
            return ToolResult(
                content=f"La última lección de «{tema}» no tiene ejercicios para evaluar."
            )

        respuestas_str = [str(r) for r in respuestas]
        pares = list(zip(ejercicios, respuestas_str, strict=False))
        correccion = await _corregir_via_llm(ctx, tema, pares)

        aciertos = sum(1 for c in correccion if c["correcto"])
        total = len(pares)
        resultados = {"aciertos": aciertos, "total": total, "feedback": correccion}

        await ctx.session.execute(
            text(
                "UPDATE learning_progress SET resultados = CAST(:resultados AS jsonb), "
                "updated_at = now() WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {
                "resultados": json.dumps(resultados),
                "id": str(fila["id"]),
                "tenant_id": str(ctx.tenant_id),
            },
        )

        lineas = [f"Evaluación de «{tema}»: {aciertos}/{total} correcta(s).", ""]
        for i, c in enumerate(correccion, start=1):
            marca = "✅" if c["correcto"] else "❌"
            lineas.append(f"{marca} {i}. {c['comentario']}")
        lineas.append("")
        lineas.append(_animo(aciertos, total))

        contenido = with_disclaimer("edu", "\n".join(lineas))
        return ToolResult(content=contenido, data=resultados)


async def _ultima_leccion(ctx: ToolContext, tema: str) -> dict[str, Any] | None:
    resultado = await ctx.session.execute(
        text(
            "SELECT id, leccion FROM learning_progress "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND tema = :tema "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"tenant_id": str(ctx.tenant_id), "user_id": str(ctx.user_id), "tema": tema},
    )
    return resultado.mappings().first()


def _a_dict(valor: Any) -> dict[str, Any]:
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str):
        try:
            cargado = json.loads(valor)
        except json.JSONDecodeError:
            return {}
        return cargado if isinstance(cargado, dict) else {}
    return {}


async def _corregir_via_llm(
    ctx: ToolContext, tema: str, pares: list[tuple[dict[str, str], str]]
) -> list[dict[str, Any]]:
    bloques = [
        f"{i}. Pregunta: {ejercicio.get('pregunta', '')}\n"
        f"   Respuesta correcta: {ejercicio.get('respuesta_correcta', '')}\n"
        f"   Respuesta del estudiante: {respuesta_estudiante}"
        for i, (ejercicio, respuesta_estudiante) in enumerate(pares, start=1)
    ]
    respuesta = await ctx.llm.complete(
        "principal",
        tenant_flags(ctx),
        CompletionRequest(
            model="principal",
            system=_SYSTEM_PROMPT_EVALUAR,
            messages=[ChatMessage(role="user", content=f"Tema: {tema}\n\n" + "\n\n".join(bloques))],
            max_tokens=1536,
        ),
    )
    datos = extraer_json_llm(respuesta.text) or {}
    correcciones = datos.get("correcciones")
    if not isinstance(correcciones, list) or len(correcciones) != len(pares):
        # El modelo no respetó el formato pedido (o devolvió una lista de
        # longitud distinta a los pares que le dimos) — en vez de perder el
        # turno completo, cae a una comparación case-insensitive determinista.
        return [
            {
                "correcto": (
                    respuesta_estudiante.strip().lower()
                    == str(ejercicio.get("respuesta_correcta", "")).strip().lower()
                ),
                "comentario": "Comparación automática (el modelo no devolvió el formato esperado).",
            }
            for ejercicio, respuesta_estudiante in pares
        ]

    normalizadas = []
    for c in correcciones:
        if isinstance(c, dict):
            normalizadas.append(
                {"correcto": bool(c.get("correcto")), "comentario": str(c.get("comentario") or "")}
            )
        else:
            normalizadas.append({"correcto": False, "comentario": ""})
    return normalizadas


def _animo(aciertos: int, total: int) -> str:
    if total == 0:
        return "Sigue practicando."
    ratio = aciertos / total
    if ratio == 1:
        return "¡Perfecto! Dominaste este tema."
    if ratio >= 0.7:
        return "¡Muy bien! Vas por buen camino, repasa lo que falló."
    if ratio >= 0.4:
        return "Vas progresando — repasa la explicación y vuelve a intentarlo."
    return "No te desanimes, todos aprendemos a nuestro ritmo — repasemos juntos."
