"""`preguntar_al_usuario` — modal de opciones en el chat en vez de adivinar.

Por qué existe: la mayoría de los resultados malos no vienen de que el modelo escriba mal,
sino de que ADIVINA un dato que solo el usuario sabe -- a qué cuenta va un post, qué tono
quiere, cuál de dos archivos -- y se equivoca en silencio. Cuesta muchísimo menos preguntar
una vez que producir un entregable entero con la suposición equivocada.

El bloque `question` (`edecan_schemas.chat.QuestionBlock`) lo renderizan web, iOS y Android
con el mismo contrato, igual que las demás cards ricas.

Contrato de flujo, deliberadamente el simple: emitir la pregunta TERMINA el turno. La
respuesta llega como un mensaje normal del usuario en el turno siguiente. No hay estado
suspendido en el servidor ni un turno bloqueado ocupando conexión, así que si el usuario
cierra la app, ignora el modal o contesta cualquier otra cosa, no queda nada colgado.
"""

from __future__ import annotations

from typing import Any

from edecan_core.tools.base import Tool, ToolContext, ToolResult
from edecan_schemas import QuestionBlock

_MAX_OPCIONES = 4
_MIN_OPCIONES = 2


class PreguntarAlUsuarioTool(Tool):
    name = "preguntar_al_usuario"
    description = (
        "Muestra una pregunta con 2 a 4 opciones para que el usuario elija tocando, en vez "
        "de adivinar. ÚSALA cuando falte un dato que solo el usuario puede dar y en el que "
        "equivocarse arruinaría el trabajo: a qué cuenta o marca va una publicación, cuál de "
        "varios archivos o destinatarios, qué tono, o cuando su pedido admite dos lecturas "
        "muy distintas. Preguntar es MUCHO más barato que entregar algo entero con la "
        "suposición equivocada. Después de llamarla, TERMINA tu turno sin hacer nada más: la "
        "respuesta te llega como un mensaje nuevo. NO la uses para pedir permiso de algo que "
        "ya te pidieron, para confirmar lo que ya está claro, ni para preguntas de sí/no que "
        "puedas resolver leyendo el contexto de la conversación."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pregunta": {
                "type": "string",
                "description": "La pregunta, concreta y en una sola oración.",
            },
            "encabezado": {
                "type": "string",
                "description": "Etiqueta corta del tema, 1-2 palabras ('Destino', 'Tono').",
            },
            "opciones": {
                "type": "array",
                "minItems": _MIN_OPCIONES,
                "maxItems": _MAX_OPCIONES,
                "description": (
                    "Entre 2 y 4 opciones distintas entre sí. Deben cubrir las respuestas "
                    "realmente probables, no rellenar."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "etiqueta": {
                            "type": "string",
                            "description": "Texto del botón, corto (1-4 palabras).",
                        },
                        "descripcion": {
                            "type": "string",
                            "description": "Qué implica elegir esta opción. Opcional.",
                        },
                    },
                    "required": ["etiqueta"],
                },
            },
            "varias": {
                "type": "boolean",
                "default": False,
                "description": "true si el usuario puede marcar más de una opción.",
            },
        },
        "required": ["pregunta", "opciones"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        pregunta = str(args.get("pregunta") or "").strip()
        if not pregunta:
            return ToolResult(content="Necesito la pregunta para poder mostrarla.")

        opciones: list[dict[str, Any]] = []
        vistas: set[str] = set()
        for item in args.get("opciones") or []:
            if not isinstance(item, dict):
                continue
            etiqueta = str(item.get("etiqueta") or "").strip()[:80]
            # Dos botones con el mismo texto son indistinguibles al tocarlos: mejor perder la
            # opción repetida que mostrar un modal donde la elección no significa nada.
            if not etiqueta or etiqueta.casefold() in vistas:
                continue
            vistas.add(etiqueta.casefold())
            descripcion = str(item.get("descripcion") or "").strip()[:200] or None
            opciones.append({"label": etiqueta, "description": descripcion})
            if len(opciones) == _MAX_OPCIONES:
                break

        if len(opciones) < _MIN_OPCIONES:
            return ToolResult(
                content=(
                    "Necesito al menos 2 opciones distintas entre sí para armar la pregunta. "
                    "Si en realidad no hay alternativas que ofrecer, no uses esta herramienta: "
                    "pregúntaselo al usuario en texto normal."
                )
            )

        bloque = QuestionBlock(
            question=pregunta[:500],
            header=(str(args.get("encabezado") or "").strip()[:40] or None),
            options=opciones,
            multi_select=bool(args.get("varias")),
            # El usuario SIEMPRE puede escribir lo suyo: obligarlo a escoger entre opciones que
            # no incluyen su respuesta real es justo el error que esta herramienta evita.
            allow_free_text=True,
            fallback_text=(
                f"{pregunta} Opciones: " + ", ".join(o["label"] for o in opciones) + "."
            ),
        )

        return ToolResult(
            # Lo ve el modelo, no el usuario: le dice que ya no tiene nada que hacer este turno.
            content=(
                "Le mostré la pregunta al usuario con sus opciones. Termina tu turno aquí, sin "
                "texto adicional y sin suponer una respuesta: te llegará como un mensaje nuevo."
            ),
            data={"question": pregunta, "options": [o["label"] for o in opciones]},
            presentation=[bloque.model_dump(mode="json")],
        )


__all__ = ["PreguntarAlUsuarioTool"]
