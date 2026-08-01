"""Generación y publicación de contenido (`ARCHITECTURE.md` §10.6, §10.8, §10.14).

`generar_contenido` SOLO redacta texto (nunca publica nada). `publicar_social`
sí publica de verdad, únicamente en las redes registradas en
`edecan_connectors.registry.CONNECTORS` que hoy soporta esta tool: LinkedIn,
Meta, X y YouTube. Publicar siempre conserva el gate peligroso y necesita una
confirmación explícita del usuario.
"""

from __future__ import annotations

import uuid
from typing import Any

import aioboto3
import httpx
from edecan_connectors.registry import CONNECTORS
from edecan_connectors.social import linkedin, meta, x, youtube
from edecan_core import Tool, ToolContext, ToolResult
from edecan_llm.base import ChatMessage, CompletionRequest
from sqlalchemy import text

from ._conectores import buscar_cuenta_conectada, resultado_falta_conexion

_TIPOS_CONTENIDO = ("post", "guion", "email")
_REDES_SOPORTADAS = ("linkedin", "meta", "x", "youtube")
_TIMEOUT = 30.0
_MAX_SOCIAL_IMAGE_BYTES = 20 * 1024 * 1024

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
        "Redes soportadas: linkedin, meta, x, youtube. Requiere confirmación: publica algo "
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
            "image_file_id": {
                "type": "string",
                "description": (
                    "UUID de una imagen privada creada o subida a Edecán. "
                    "Hoy se usa al publicar en LinkedIn."
                ),
            },
            "alt_text": {
                "type": "string",
                "description": "Descripción accesible de la imagen, si se adjunta una.",
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
            if red == "linkedin":
                return await _publicar_en_linkedin(ctx, texto, args, bundle, http)
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
    # GAP CONOCIDO, fuera de este carril: a diferencia de `linkedin.create_post`
    # (que relee el post con `verify_post` antes de darlo por publicado, ver
    # `_mensaje_publicacion_linkedin` más arriba), `x.post_tweet` en
    # `packages/connectors/edecan_connectors/social/x.py` NO relee el tuit tras
    # crearlo -- solo propaga el error si X responde >=400 (`_raise_for_x_error`)
    # y devuelve tal cual el cuerpo 2xx. Ese cuerpo sí trae `data.id` cuando X
    # confirma la creación en la misma respuesta (a diferencia del bug real de
    # LinkedIn, la API de X no tiene un caso conocido de "201 sin crear nada"),
    # así que el riesgo aquí es menor -- pero sigue siendo "confiar en el 2xx"
    # sin una relectura independiente. Arreglarlo de raíz exigiría un
    # `verify_tweet` equivalente en `packages/connectors/`, paquete que este
    # carril tiene prohibido tocar (ver instrucciones de la tarea). Se deja
    # documentado en vez de fingir la misma garantía que LinkedIn.
    resultado = await x.post_tweet(http, bundle, texto)
    return ToolResult(content="Publicado en X.", data={"resultado": resultado})


async def _cargar_imagen_privada(
    ctx: ToolContext, raw_file_id: Any
) -> tuple[bytes, str] | None:
    value = str(raw_file_id or "").strip()
    if not value:
        return None
    try:
        file_id = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("image_file_id debe ser un UUID válido.") from exc

    result = await ctx.session.execute(
        text(
            "SELECT s3_key, mime, size_bytes FROM files "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": str(ctx.tenant_id), "id": str(file_id)},
    )
    row = result.mappings().first()
    if row is None:
        raise ValueError("No encontré esa imagen privada en tu Edecán.")
    mime = str(row.get("mime") or "")
    if not mime.startswith("image/"):
        raise ValueError("El archivo indicado no es una imagen.")
    size = int(row.get("size_bytes") or 0)
    if size > _MAX_SOCIAL_IMAGE_BYTES:
        raise ValueError("La imagen supera el límite de 20 MB para publicación.")
    s3_key = str(row.get("s3_key") or "")
    if not s3_key:
        raise ValueError("La imagen no tiene contenido almacenado.")

    session = aioboto3.Session()
    async with session.client(
        "s3",
        region_name=getattr(ctx.settings, "AWS_REGION", "us-east-1"),
        endpoint_url=getattr(ctx.settings, "AWS_ENDPOINT_URL", None),
    ) as s3:
        response = await s3.get_object(
            Bucket=getattr(ctx.settings, "S3_BUCKET", "edecan-files"),
            Key=s3_key,
        )
        content = await response["Body"].read(_MAX_SOCIAL_IMAGE_BYTES + 1)
    if len(content) > _MAX_SOCIAL_IMAGE_BYTES:
        raise ValueError("La imagen supera el límite de 20 MB para publicación.")
    return content, mime


async def _publicar_en_linkedin(
    ctx: ToolContext,
    texto_publicacion: str,
    args: dict[str, Any],
    bundle: Any,
    http: httpx.AsyncClient,
) -> ToolResult:
    try:
        image = await _cargar_imagen_privada(ctx, args.get("image_file_id"))
    except ValueError as exc:
        return ToolResult(content=str(exc))
    resultado = await linkedin.create_post(
        http,
        bundle,
        text=texto_publicacion,
        image=image[0] if image else None,
        image_content_type=image[1] if image else "image/png",
        alt_text=str(args.get("alt_text") or ""),
    )
    return ToolResult(
        content=_mensaje_publicacion_linkedin(resultado),
        data={"resultado": resultado},
    )


def _mensaje_publicacion_linkedin(resultado: dict[str, Any]) -> str:
    """Frase que ve el modelo tras publicar en LinkedIn -- lo único que ve, porque
    `data` (donde vive `resultado` completo, con `verified`/`verification_note`) es
    privado y nunca llega al LLM (ver `Tool.run`/`ToolResult` y `Agent.run_turn`). Si
    el matiz no está en `content`, no existe para el modelo.

    Este es el incidente real de hoy: el token de la página de empresa no tenía el
    scope correcto, LinkedIn respondió 201 con un `x-restli-id` con forma válida y
    NO publicó nada -- un fallo indistinguible de un éxito. `linkedin.create_post`
    ya relee el post (`verify_post`) y ahora separa tres estados; si esa relectura
    sale `"not_found"` `create_post` levanta `ConnectorError` (lo agarra `Agent.
    run_turn` y lo devuelve como "Error: ..." -- eso ya NO es este código). Lo que
    queda por decidir aquí son los otros dos:

    - "confirmed": el GET confirmó que el post existe. Éxito real -> mensaje llano.
    - "unknown": no se pudo releer (permiso, red, lo que sea) -- pudo publicarse o
      no, nadie lo sabe. Mismo patrón exacto que `_published_chat_text` en
      `apps/api/edecan_api/routers/content_studio.py`: jamás se afirma el éxito ni
      se pone un tono ambiguo que el modelo pueda redondear a "listo, publicado".
      Se dice con todas sus letras que no se confirmó y qué hacer para comprobarlo
      (entrar a LinkedIn y mirar), para que el modelo se lo repita así a la persona
      en vez de inventarle seguridad que nadie tiene.
    """
    if resultado.get("verified") == "confirmed":
        return "Publicado en LinkedIn."
    nota = str(resultado.get("verification_note") or "").strip()
    detalle = f" Detalle de LinkedIn: {nota}" if nota else ""
    return (
        "Se envió el post a LinkedIn, pero NO se pudo confirmar que quedó publicado "
        "de verdad: LinkedIn no dejó releerlo para comprobarlo."
        f"{detalle} IMPORTANTE: no le digas a la persona que ya está publicado ni marques "
        "esto como confirmado de ninguna forma -- dile explícitamente que se envió pero "
        "falta confirmar, y que entre a LinkedIn (su perfil o la página de empresa) a "
        "comprobarlo con sus propios ojos antes de darlo por hecho."
    )


async def _publicar_en_meta(texto: str, bundle: Any, http: httpx.AsyncClient) -> ToolResult:
    # MISMO GAP que `_publicar_en_x` (ver comentario ahí): `meta.publish_page_post`
    # (`packages/connectors/edecan_connectors/social/meta.py`) hace `POST .../feed`
    # y devuelve `resp.json()` tal cual, sin releer el post -- solo levanta
    # `ConnectorError` si Graph API responde >=400. Este es justo el caso más
    # parecido al incidente real (publicar EN NOMBRE de una Página/empresa, no
    # del perfil personal), pero Graph API sí exige que el `page_access_token`
    # tenga los scopes `pages_manage_posts` de esa Página específica para que el
    # `POST` siquiera responda 2xx -- no se conoce en Graph API el caso LinkedIn
    # de "201 con id válido y nada creado". Aun así, sin una relectura
    # independiente (que exigiría tocar `packages/connectors/`, fuera de este
    # carril) esta tool sigue confiando en el 2xx de Meta sin comprobarlo dos
    # veces. Queda documentado como gap, no resuelto.
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
    # MISMO GAP que `_publicar_en_x`/`_publicar_en_meta`: `youtube.upload_video`
    # (`packages/connectors/edecan_connectors/social/youtube.py`) no relee el video
    # subido -- confía en que el `PUT` resumable final respondió 2xx. La Data API
    # v3 de YouTube sí devuelve el recurso `video` completo (con su `id`) en ese
    # mismo `PUT`, así que hay menos margen para un "2xx fantasma" que en el bug
    # real de LinkedIn, pero sigue siendo cero verificación independiente tipo
    # `verify_post`. Igual que arriba: arreglarlo de raíz es trabajo de
    # `packages/connectors/`, fuera de este carril; se deja documentado.
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
