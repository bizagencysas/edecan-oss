"""Generación y publicación de contenido (`ARCHITECTURE.md` §10.6, §10.8, §10.14).

`generar_contenido` SOLO redacta texto (nunca publica nada). `publicar_social`
sí publica de verdad, únicamente en las redes registradas en
`edecan_connectors.registry.CONNECTORS` que hoy soporta esta tool: Meta, X y
YouTube. Otras redes se rechazan en este conector directo con una alternativa
concreta; la creación multimedia y las sesiones locales autorizadas son
capacidades separadas.
"""

from __future__ import annotations

from typing import Any

import httpx
from edecan_connectors.registry import CONNECTORS
from edecan_connectors.social import meta, x, youtube
from edecan_core import Tool, ToolContext, ToolResult
from edecan_llm.base import ChatMessage, CompletionRequest

from ._conectores import buscar_cuenta_conectada, resultado_falta_conexion

_TIPOS_CONTENIDO = ("post", "guion", "email")
_REDES_SOPORTADAS = ("meta", "x", "youtube")
_TIMEOUT = 30.0

_SYSTEM_PROMPT = (
    "Eres un redactor experto en marketing de contenidos en español. Escribes "
    "SOLO el texto pedido, sin explicaciones ni comentarios sobre tu proceso, "
    "listo para revisar y publicar tal cual."
)


def _tenant_flags(ctx: ToolContext) -> dict[str, Any]:
    """Lee los flags del plan del tenant desde `ctx.extras["flags"]` si el
    agente los dejó ahí (§10.7 no reserva esta clave explícitamente, pero
    `Agent.run_turn` sí recibe `flags: dict`); si no están, se asume `{}`
    (no degrada el alias `"principal"` — ver `LLMRouter._resolve_model`).
    """
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    flags = extras.get("flags")
    return flags if isinstance(flags, dict) else {}


class GenerarContenidoTool(Tool):
    name = "generar_contenido"
    description = (
        "Redacta un borrador de contenido (post, guion o email) a partir de un brief, "
        "usando el modelo principal. Solo devuelve texto — nunca publica nada."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "brief": {
                "type": "string",
                "description": "Qué escribir: tema, objetivo y puntos clave a cubrir.",
            },
            "tipo": {
                "type": "string",
                "enum": list(_TIPOS_CONTENIDO),
                "description": "Tipo de contenido a redactar.",
                "default": "post",
            },
            "tono": {"type": "string", "description": "Tono deseado (ej. 'cercano', 'formal')."},
            "longitud": {
                "type": "string",
                "description": "Longitud aproximada deseada (ej. 'corto', 'medio', 'largo').",
            },
        },
        "required": ["brief"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        brief = str(args.get("brief", "")).strip()
        if not brief:
            return ToolResult(content="Necesito un brief para redactar el contenido.")
        tipo = args.get("tipo") or "post"
        if tipo not in _TIPOS_CONTENIDO:
            tipo = "post"
        tono = args.get("tono")
        longitud = args.get("longitud")

        instrucciones = [f"Tipo de contenido: {tipo}.", f"Brief: {brief}."]
        if tono:
            instrucciones.append(f"Tono: {tono}.")
        if longitud:
            instrucciones.append(f"Longitud aproximada: {longitud}.")

        respuesta = await ctx.llm.complete(
            "principal",
            _tenant_flags(ctx),
            CompletionRequest(
                model="principal",
                system=_SYSTEM_PROMPT,
                messages=[ChatMessage(role="user", content="\n".join(instrucciones))],
                max_tokens=1536,
            ),
        )

        texto = respuesta.text.strip()
        if not texto:
            return ToolResult(
                content="No logré redactar contenido para ese brief; intenta reformularlo."
            )
        return ToolResult(content=texto, data={"tipo": tipo, "brief": brief})


class PublicarSocialTool(Tool):
    name = "publicar_social"
    description = (
        "Publica contenido de verdad en una red social ya conectada por el tenant. "
        "Redes soportadas: meta, x, youtube. Requiere confirmación: publica algo "
        "real y visible públicamente."
    )
    requires_flags = frozenset({"connectors.social"})
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "red": {
                "type": "string",
                "enum": list(_REDES_SOPORTADAS),
                "description": "Red social donde publicar.",
            },
            "texto": {
                "type": "string",
                "description": (
                    "Texto a publicar (post/tweet, o descripción del video si 'red' es youtube)."
                ),
            },
            "titulo": {
                "type": "string",
                "description": "Título del video (solo si 'red' es youtube).",
            },
            "video_url": {
                "type": "string",
                "description": (
                    "URL pública del archivo de video a subir (solo si 'red' es youtube)."
                ),
            },
        },
        "required": ["red", "texto"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        red = str(args.get("red", "")).strip().lower()
        texto = str(args.get("texto", "")).strip()

        if red not in _REDES_SOPORTADAS or red not in CONNECTORS:
            return ToolResult(content=_mensaje_red_no_soportada(red))
        if not texto:
            return ToolResult(content="Necesito el texto a publicar.")

        cuenta = await buscar_cuenta_conectada(ctx, (red,))
        if cuenta is None:
            return resultado_falta_conexion(red)
        bundle = await ctx.vault.get(ctx.tenant_id, cuenta.connector_account_id)
        if bundle is None:
            return resultado_falta_conexion(red)

        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            if red == "x":
                return await _publicar_en_x(texto, bundle, http)
            if red == "meta":
                return await _publicar_en_meta(texto, bundle, http)
            return await _publicar_en_youtube(texto, args, bundle, http)


def _mensaje_red_no_soportada(red: str) -> str:
    return (
        f"'{red}' no tiene un conector directo configurado en esta instalación. Esta herramienta "
        f"publica hoy en {', '.join(_REDES_SOPORTADAS)}. Edecán todavía puede crear el paquete "
        "multimedia para la red pedida y, con confirmación, continuar en una sesión local ya "
        "abierta."
    )


async def _publicar_en_x(texto: str, bundle: Any, http: httpx.AsyncClient) -> ToolResult:
    resultado = await x.post_tweet(http, bundle, texto)
    return ToolResult(content="Publicado en X.", data={"resultado": resultado})


async def _publicar_en_meta(texto: str, bundle: Any, http: httpx.AsyncClient) -> ToolResult:
    paginas = await meta.list_pages(http, bundle)
    if not paginas:
        return ToolResult(
            content="No encontré ninguna Página de Facebook administrada por esta cuenta."
        )
    pagina = paginas[0]
    resultado = await meta.publish_page_post(
        http, pagina["id"], pagina["access_token"], texto
    )
    nombre_pagina = pagina.get("name", pagina["id"])
    return ToolResult(
        content=f"Publicado en la Página de Facebook «{nombre_pagina}».",
        data={"resultado": resultado},
    )


async def _publicar_en_youtube(
    texto: str, args: dict[str, Any], bundle: Any, http: httpx.AsyncClient
) -> ToolResult:
    titulo = str(args.get("titulo") or "").strip()
    video_url = str(args.get("video_url") or "").strip()
    if not titulo or not video_url:
        return ToolResult(
            content=(
                "YouTube solo publica video: falta 'titulo' y/o 'video_url' "
                "(la URL pública del archivo de video a subir)."
            )
        )
    descarga = await http.get(video_url)
    descarga.raise_for_status()
    resultado = await youtube.upload_video(http, bundle, titulo, texto, descarga.content)
    return ToolResult(
        content=f"Video «{titulo}» subido a YouTube.", data={"resultado": resultado}
    )
