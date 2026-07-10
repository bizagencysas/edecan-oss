"""Visión sobre imágenes (`analizar_imagen`, ROADMAP_V2.md §7.7, §3 punto 18).

Descarga la imagen (PNG/JPEG/WEBP/GIF, ≤ `_MAX_BYTES`), la codifica en base64
y arma un `ChatMessage(role="user", content=[...])` con dos bloques, en el
formato común de `edecan_llm.base` (ver su docstring — "sigue la forma de los
bloques de contenido de Anthropic"):

    [
        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
        {"type": "text", "text": pregunta},
    ]

**Por qué esto exige el proveedor Anthropic**: `edecan_llm.anthropic._to_anthropic_messages`
reenvía `message.content` tal cual al body de `/v1/messages` cuando el rol no
es `system`/`tool` (`messages.append({"role": ..., "content": message.content})`,
ver `packages/llm/edecan_llm/anthropic.py`) — así que un bloque `{"type":
"image", ...}` llega intacto a la API de Anthropic, que sí lo entiende. En
cambio `edecan_llm.openai_compat._to_openai_messages` NO tiene traducción de
imágenes: para un mensaje `role="user"` con `content` en forma de lista cae a
`_text_of(...)`, que solo concatena los bloques `type="text"` e ignora
silenciosamente cualquier otro bloque (`packages/llm/edecan_llm/openai_compat.py`)
— es decir, con ese proveedor la imagen desaparecería sin aviso y el modelo
respondería solo a la pregunta de texto, a ciegas. Para no fallar en
silencio, esta tool resuelve el proveedor ANTES de llamar (`ctx.llm.resolve(...)`,
`edecan_llm.router.LLMRouter.resolve`) y si `provider.name != "anthropic"`
devuelve un `ToolResult` con un mensaje de error explícito en vez de mandar
la imagen a un proveedor que la va a descartar.
"""

from __future__ import annotations

import base64
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from edecan_llm.base import ChatMessage, CompletionRequest

from . import _s3
from ._util import parse_uuid, tenant_flags

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

_MIME_PERMITIDOS = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_EXT_A_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

_PREGUNTA_DEFECTO = "Describe y transcribe (OCR) esta imagen."

_SYSTEM_PROMPT = (
    "Eres un asistente de visión: describes imágenes con precisión y transcribes "
    "cualquier texto visible (OCR) de forma fiel, sin inventar contenido que no "
    "esté en la imagen. Responde en español salvo que la pregunta pida otro idioma."
)


class AnalizarImagenTool(Tool):
    name = "analizar_imagen"
    description = (
        "Analiza una imagen ya subida (PNG/JPEG/WEBP/GIF, máx. 5 MB): la describe y "
        "transcribe cualquier texto visible (OCR), o responde una pregunta puntual "
        "sobre ella. Requiere un proveedor LLM con visión configurado (Anthropic) — "
        "con otro proveedor devuelve un error claro en vez de fallar a ciegas."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "id de la imagen ya subida."},
            "pregunta": {
                "type": "string",
                "description": (
                    "Pregunta puntual sobre la imagen. Si se omite, describe y "
                    "transcribe (OCR) la imagen completa."
                ),
            },
        },
        "required": ["file_id"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        file_id = parse_uuid(args.get("file_id"))
        if file_id is None:
            return ToolResult(content="'file_id' no es un identificador válido.")

        archivo = await _s3.descargar_archivo(ctx, file_id)
        if archivo is None:
            return ToolResult(content="No encontré ese archivo.")

        if archivo.size_bytes > _MAX_BYTES:
            return ToolResult(
                content=(
                    f"'{archivo.filename}' pesa {archivo.size_bytes / 1_048_576:.1f} MB; "
                    f"el máximo para analizar_imagen es {_MAX_BYTES // 1_048_576} MB."
                )
            )

        mime = _resolver_mime(archivo.mime, archivo.filename)
        if mime is None:
            return ToolResult(
                content=(
                    f"'{archivo.filename}' no es una imagen soportada — solo PNG, "
                    "JPEG, WEBP o GIF."
                )
            )

        flags = tenant_flags(ctx)
        proveedor, _modelo = ctx.llm.resolve("principal", flags)
        nombre_proveedor = getattr(proveedor, "name", "")
        if nombre_proveedor != "anthropic":
            return ToolResult(
                content=(
                    "analizar_imagen necesita un proveedor LLM con soporte de visión "
                    "(Anthropic, vía ANTHROPIC_API_KEY) — el proveedor configurado "
                    f"ahora mismo ('{nombre_proveedor or 'desconocido'}') no procesa "
                    "imágenes, así que no puedo analizar esta imagen."
                )
            )

        pregunta = str(args.get("pregunta") or "").strip() or _PREGUNTA_DEFECTO

        respuesta = await ctx.llm.complete(
            "principal",
            flags,
            CompletionRequest(
                model="principal",
                system=_SYSTEM_PROMPT,
                messages=[
                    ChatMessage(
                        role="user",
                        content=[
                            _bloque_imagen(mime, archivo.contenido),
                            {"type": "text", "text": pregunta},
                        ],
                    )
                ],
                max_tokens=1536,
            ),
        )

        texto = respuesta.text.strip()
        if not texto:
            return ToolResult(
                content="No logré analizar esa imagen; intenta reformular la pregunta."
            )
        return ToolResult(
            content=texto, data={"file_id": str(file_id), "mime": mime, "pregunta": pregunta}
        )


def _bloque_imagen(mime: str, contenido: bytes) -> dict[str, Any]:
    """Bloque de contenido multimodal `{"type": "image", ...}` en el formato común
    de `edecan_llm` (ver docstring del módulo). Factorizado fuera de `run()` para
    que `edecan_docanalysis.video.construir_bloques_video` arme el mismo shape por
    cada frame de video extraído sin duplicar la codificación base64 (WP-V3-14) —
    no es parte del contrato público del paquete (por eso el prefijo `_`), pero se
    importa entre módulos hermanos del mismo paquete, mismo criterio que `_s3.py`/
    `_util.py`."""
    b64 = base64.b64encode(contenido).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}


def _resolver_mime(mime: str, filename: str) -> str | None:
    """`mime` declarado si es uno de los soportados (normalizando el alias no
    estándar `image/jpg` a `image/jpeg`); si no, cae a la extensión del
    nombre de archivo — cubre subidas con `Content-Type` genérico
    (`application/octet-stream`)."""
    normalizado = (mime or "").split(";")[0].strip().lower()
    if normalizado == "image/jpg":
        normalizado = "image/jpeg"
    if normalizado in _MIME_PERMITIDOS:
        return normalizado

    nombre = (filename or "").lower()
    for ext, mime_ext in _EXT_A_MIME.items():
        if nombre.endswith(ext):
            return mime_ext
    return None
