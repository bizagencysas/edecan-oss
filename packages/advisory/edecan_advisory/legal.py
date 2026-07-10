"""Asesor legal informativo: `analizar_contrato`, `comparar_contratos`,
`generar_borrador_legal` (ROADMAP_V2.md §7.7, WP-V2-11).

GUARDRAIL no negociable (ROADMAP_V2.md §8.3): las tres herramientas son
informativas, NUNCA asesoría legal real — cada respuesta en el camino feliz
termina con `_disclaimers.DISCLAIMER_LEGAL` (vía `with_disclaimer("legal",
...)`, último paso antes de construir el `ToolResult`). `generar_borrador_legal`
además marca el archivo generado como "BORRADOR" explícito en el propio texto
de respuesta, no solo en el disclaimer final.

Ninguna de las tres es `dangerous` ni requiere un flag de plan: son de solo
lectura/generación de un archivo privado del tenant, mismo criterio que
`edecan_docanalysis`/`edecan_creative` (ROADMAP_V2.md §7.7 no lista ningún
flag para `edecan_advisory`).
"""

from __future__ import annotations

import difflib
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from edecan_llm.base import ChatMessage, CompletionRequest

from . import _texto
from ._disclaimers import with_disclaimer
from ._plantillas import ETIQUETAS_BORRADOR, TIPOS_BORRADOR, renderizar
from ._util import extraer_json_llm, parse_uuid, slugify, tenant_flags

#: Primeras N líneas de diff que se muestran/mandan al LLM (ROADMAP_V2.md
#: §7.7: "primeras 200 líneas de diff").
_MAX_LINEAS_DIFF = 200

_SYSTEM_PROMPT_CONTRATO = (
    "Eres un analista legal asistido por IA. Analizas el TEXTO de un contrato "
    "(puede venir incompleto o mal formateado por una extracción automática) "
    "y devuelves ÚNICAMENTE un JSON con esta forma exacta, sin texto ni "
    "comentarios adicionales fuera del JSON:\n"
    '{"partes": ["..."], "objeto": "...", "vigencia": "...", '
    '"obligaciones_clave": ["..."], "riesgos": [{"clausula": "...", '
    '"riesgo": "...", "severidad": "alta|media|baja"}], "resumen": "..."}\n'
    "Describe lo que el documento DICE y señala riesgos POTENCIALES en tono "
    "informativo — nunca des una conclusión legal definitiva ni sustituyas el "
    "criterio de un abogado."
)

_SYSTEM_PROMPT_DIFF = (
    "Eres un analista legal. Te doy el diff unificado (formato `diff -u`) "
    "entre dos versiones de un contrato. Resume en español, en 3-6 puntos "
    "breves, los cambios MATERIALES (obligaciones, montos, plazos, partes, "
    "penalidades, condiciones de terminación) e ignora cambios triviales de "
    "formato o redacción. Si no hay cambios materiales, dilo explícitamente."
)

_SYSTEM_PROMPT_BORRADOR = (
    "Eres un asistente de redacción legal. Recibes el borrador de un "
    "documento ya armado a partir de una plantilla y debes pulir su "
    "redacción en español (gramática, claridad, formalidad) SIN cambiar "
    "hechos, nombres, fechas, montos ni los placeholders sin rellenar (los "
    "que aparecen como '[campo]'), y sin agregar cláusulas nuevas. Devuelve "
    "SOLO el texto final del documento, sin comentarios adicionales."
)


class AnalizarContratoTool(Tool):
    name = "analizar_contrato"
    description = (
        "Analiza un contrato (ya subido como PDF/DOCX/TXT/MD, o pegado como texto "
        "directo) y devuelve partes, objeto, vigencia, obligaciones clave y riesgos "
        "potenciales por cláusula. Informativo: no es asesoría legal."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "id de un archivo ya subido (PDF/DOCX/TXT/MD) con el contrato.",
            },
            "texto": {
                "type": "string",
                "description": "Texto del contrato pegado directo (alternativa a file_id).",
            },
            "enfoque": {
                "type": "string",
                "description": (
                    "Aspecto en el que enfocar el análisis (ej. 'cláusulas de terminación')."
                ),
            },
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        texto_directo = str(args.get("texto") or "").strip()
        enfoque = str(args.get("enfoque") or "").strip()

        if texto_directo:
            texto = texto_directo[: _texto.MAX_CHARS]
            origen = "el texto que me diste"
        elif args.get("file_id"):
            file_id = parse_uuid(args.get("file_id"))
            if file_id is None:
                return ToolResult(content="'file_id' no es un identificador válido.")
            try:
                extraido = await _texto.extraer_texto_de_file_id(ctx, file_id)
            except _texto.FormatoNoSoportado as exc:
                return ToolResult(content=str(exc))
            if extraido is None:
                return ToolResult(content="No encontré ese archivo.")
            texto = extraido.texto
            origen = f"«{extraido.archivo.filename}»"
        else:
            return ToolResult(content="Necesito 'file_id' o 'texto' para analizar un contrato.")

        if not texto.strip():
            return ToolResult(content=f"No encontré texto para analizar en {origen}.")

        datos = await _analizar_contrato_via_llm(ctx, texto, enfoque)
        contenido = with_disclaimer("legal", _render_analisis(origen, datos))
        return ToolResult(content=contenido, data={"origen": origen, **datos})


async def _analizar_contrato_via_llm(ctx: ToolContext, texto: str, enfoque: str) -> dict[str, Any]:
    instrucciones = f"\n\nEnfócate especialmente en: {enfoque}." if enfoque else ""
    respuesta = await ctx.llm.complete(
        "principal",
        tenant_flags(ctx),
        CompletionRequest(
            model="principal",
            system=_SYSTEM_PROMPT_CONTRATO,
            messages=[
                ChatMessage(role="user", content=f"Texto del contrato:\n\n{texto}{instrucciones}")
            ],
            max_tokens=2048,
        ),
    )
    return extraer_json_llm(respuesta.text) or {}


def _render_analisis(origen: str, datos: dict[str, Any]) -> str:
    partes = datos.get("partes") or []
    objeto = str(datos.get("objeto") or "no especificado")
    vigencia = str(datos.get("vigencia") or "no especificada")
    obligaciones = datos.get("obligaciones_clave") or []
    riesgos = datos.get("riesgos") or []
    resumen = str(datos.get("resumen") or "")

    lineas = [
        f"Análisis de {origen}:",
        "",
        f"Partes: {', '.join(str(p) for p in partes) if partes else 'no identificadas'}",
        f"Objeto: {objeto}",
        f"Vigencia: {vigencia}",
    ]

    if obligaciones:
        lineas.append("")
        lineas.append("Obligaciones clave:")
        lineas.extend(f"- {o}" for o in obligaciones)

    if riesgos:
        lineas.append("")
        lineas.append("Riesgos detectados:")
        for riesgo in riesgos:
            if isinstance(riesgo, dict):
                clausula = riesgo.get("clausula", "?")
                detalle = riesgo.get("riesgo", "")
                severidad = str(riesgo.get("severidad") or "media").upper()
            else:
                clausula, detalle, severidad = str(riesgo), "", "MEDIA"
            lineas.append(f"- [{severidad}] {clausula}: {detalle}")

    if resumen:
        lineas.append("")
        lineas.append(f"Resumen: {resumen}")

    return "\n".join(lineas)


class CompararContratosTool(Tool):
    name = "comparar_contratos"
    description = (
        "Compara dos versiones de un contrato ya subidas (PDF/DOCX/TXT/MD) línea a "
        "línea y resume los cambios materiales entre ambas. Informativo: no es "
        "asesoría legal."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_id_a": {"type": "string", "description": "id de la versión anterior."},
            "file_id_b": {"type": "string", "description": "id de la versión nueva."},
        },
        "required": ["file_id_a", "file_id_b"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        id_a = parse_uuid(args.get("file_id_a"))
        id_b = parse_uuid(args.get("file_id_b"))
        if id_a is None or id_b is None:
            return ToolResult(
                content="'file_id_a' y 'file_id_b' deben ser identificadores válidos."
            )

        try:
            extraido_a = await _texto.extraer_texto_de_file_id(ctx, id_a)
            extraido_b = await _texto.extraer_texto_de_file_id(ctx, id_b)
        except _texto.FormatoNoSoportado as exc:
            return ToolResult(content=str(exc))

        if extraido_a is None or extraido_b is None:
            return ToolResult(content="No encontré uno de los dos archivos.")

        nombre_a, nombre_b = extraido_a.archivo.filename, extraido_b.archivo.filename
        diff_completo = list(
            difflib.unified_diff(
                extraido_a.texto.splitlines(),
                extraido_b.texto.splitlines(),
                fromfile=nombre_a,
                tofile=nombre_b,
                lineterm="",
            )
        )
        diff = diff_completo[:_MAX_LINEAS_DIFF]

        if not diff:
            contenido = with_disclaimer(
                "legal",
                f"Comparé «{nombre_a}» y «{nombre_b}»: no encontré diferencias de texto "
                "entre ambos documentos.",
            )
            return ToolResult(content=contenido, data={"diff": [], "cambios_materiales": ""})

        cambios_materiales = await _resumir_diff(ctx, diff)
        recorte = (
            f"\n\n(mostrando las primeras {len(diff)} de {len(diff_completo)} líneas de diff)"
            if len(diff_completo) > len(diff)
            else ""
        )
        cuerpo_diff = "\n".join(diff)
        contenido = with_disclaimer(
            "legal",
            f"Comparé «{nombre_a}» y «{nombre_b}»:\n\n{cuerpo_diff}{recorte}\n\n"
            f"Cambios materiales: {cambios_materiales}",
        )
        return ToolResult(
            content=contenido,
            data={"diff": diff, "cambios_materiales": cambios_materiales},
        )


async def _resumir_diff(ctx: ToolContext, diff: list[str]) -> str:
    respuesta = await ctx.llm.complete(
        "principal",
        tenant_flags(ctx),
        CompletionRequest(
            model="principal",
            system=_SYSTEM_PROMPT_DIFF,
            messages=[ChatMessage(role="user", content="\n".join(diff))],
            max_tokens=1024,
        ),
    )
    return respuesta.text.strip() or "no detecté cambios materiales."


class GenerarBorradorLegalTool(Tool):
    name = "generar_borrador_legal"
    description = (
        f"Genera el BORRADOR de un documento legal simple ({', '.join(TIPOS_BORRADOR)}) a "
        "partir de una plantilla rellenada con los campos dados, lo pule con el modelo y "
        "lo guarda como archivo .md. SIEMPRE debe revisarse con un abogado antes de usarse."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "tipo": {
                "type": "string",
                "enum": list(TIPOS_BORRADOR),
                "description": "Tipo de documento a generar.",
            },
            "campos": {
                "type": "object",
                "description": (
                    "Campos para rellenar la plantilla (ej. parte_a, parte_b, fecha, objeto, "
                    "vigencia, jurisdiccion, destinatario, asunto, cuerpo, remitente, "
                    "terminos — varían según 'tipo')."
                ),
            },
        },
        "required": ["tipo", "campos"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        tipo = str(args.get("tipo") or "").strip().lower()
        if tipo not in TIPOS_BORRADOR:
            return ToolResult(
                content=(
                    f"'{tipo}' no es un tipo de borrador válido (usa: {', '.join(TIPOS_BORRADOR)})."
                )
            )
        campos = args.get("campos")
        if not isinstance(campos, dict) or not campos:
            return ToolResult(
                content=(
                    "Necesito al menos algunos campos para armar el borrador "
                    "(ej. parte_a, parte_b, objeto)."
                )
            )

        borrador_base = renderizar(tipo, campos)
        pulido = await _pulir_redaccion(ctx, borrador_base)

        pista_nombre = str(campos.get("parte_a") or campos.get("destinatario") or tipo)
        filename = f"borrador-{tipo}-{slugify(pista_nombre)}.md"
        file_id = await _texto.subir_resultado(
            ctx, filename=filename, mime="text/markdown", contenido=pulido.encode("utf-8")
        )

        contenido = with_disclaimer(
            "legal",
            "⚠️ BORRADOR — revísalo con un abogado antes de firmarlo o enviarlo.\n\n"
            f"Guardé «{filename}» con {ETIQUETAS_BORRADOR[tipo]}.\n\n{pulido}",
        )
        return ToolResult(
            content=contenido,
            data={"file_id": str(file_id), "filename": filename, "tipo": tipo},
        )


async def _pulir_redaccion(ctx: ToolContext, borrador: str) -> str:
    respuesta = await ctx.llm.complete(
        "principal",
        tenant_flags(ctx),
        CompletionRequest(
            model="principal",
            system=_SYSTEM_PROMPT_BORRADOR,
            messages=[ChatMessage(role="user", content=borrador)],
            max_tokens=2048,
        ),
    )
    return respuesta.text.strip() or borrador
