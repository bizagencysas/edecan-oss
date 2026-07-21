"""Creación de paquetes sociales listos para revisar y publicar.

La herramienta no publica ni suplanta al usuario. Produce artefactos privados
(copy Markdown, manifiesto JSON y visual PNG opcional) que cruzan el mismo
contrato SSE de archivos que ya consumen web, iOS y Android.
"""

from __future__ import annotations

import io
import json
import re
import textwrap
from dataclasses import dataclass
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from PIL import Image, ImageDraw, ImageFont

from ._files import Uploader, subir_archivo
from .providers import ImageProvider, StubImageProvider, get_tenant_image_provider
from .tools import _cap_str, _slug


@dataclass(frozen=True)
class PlatformSpec:
    label: str
    max_chars: int
    image_size: str
    guidance: str


PLATFORMS: dict[str, PlatformSpec] = {
    "linkedin": PlatformSpec(
        "LinkedIn", 3000, "1200x627", "criterio profesional, párrafos breves y una idea útil"
    ),
    "x": PlatformSpec("X", 280, "1600x900", "directo, específico y sin relleno"),
    "instagram": PlatformSpec(
        "Instagram", 2200, "1080x1080", "apertura clara, lectura móvil y cierre accionable"
    ),
    "facebook": PlatformSpec(
        "Facebook", 63206, "1200x630", "natural, conversacional y fácil de compartir"
    ),
    "threads": PlatformSpec("Threads", 500, "1080x1080", "conversacional y conciso"),
    "tiktok": PlatformSpec(
        "TikTok", 2200, "1080x1920", "caption breve con un gancho verificable"
    ),
}

_MAX_TOPIC_CHARS = 300
_MAX_COPY_CHARS = 64_000
_MAX_VISUAL_PROMPT_CHARS = 4_000
_MAX_ALT_CHARS = 1_000
_MAX_HASHTAGS = 20


def _clean_hashtags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw[:_MAX_HASHTAGS]:
        tag = re.sub(r"[^\wáéíóúüñÁÉÍÓÚÜÑ]", "", str(item).lstrip("#"), flags=re.UNICODE)
        if tag and tag.casefold() not in {existing.casefold() for existing in result}:
            result.append(tag[:80])
    return result


def _split_x_thread(text: str, limit: int = 280) -> list[str]:
    """Divide sin cortar palabras; reserva espacio para ``1/N`` estable."""

    compact = re.sub(r"[ \t]+", " ", text).strip()
    if len(compact) <= limit:
        return [compact]
    payload_limit = limit - 7
    words = compact.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= payload_limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(word) > payload_limit:
            chunks.append(word[:payload_limit])
            word = word[payload_limit:]
        current = word
    if current:
        chunks.append(current)
    total = len(chunks)
    return [f"{chunk} {index}/{total}" for index, chunk in enumerate(chunks, start=1)]


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/SFNS.ttf")
        if not bold
        else ("/System/Library/Fonts/SFNSDisplay-Bold.otf", "/System/Library/Fonts/SFNS.ttf")
    )
    candidates = [
        *names,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _offline_social_card(*, headline: str, platform: PlatformSpec, size: str) -> bytes:
    """Visual editorial local: útil incluso con Ollama y sin API de imágenes."""

    try:
        width, height = (int(part) for part in size.lower().split("x", 1))
    except (ValueError, AttributeError):
        width, height = 1080, 1080
    width = max(320, min(width, 2048))
    height = max(320, min(height, 2048))
    image = Image.new("RGB", (width, height), "#0A0A0F")
    draw = ImageDraw.Draw(image)

    # Fondo profundo con una única luz cobalto/teal y una red de memoria sutil.
    for y in range(height):
        progress = y / max(height - 1, 1)
        color = (
            10 + int(18 * progress),
            10 + int(15 * progress),
            15 + int(42 * progress),
        )
        draw.line((0, y, width, y), fill=color)
    accent_y = int(height * 0.14)
    draw.rounded_rectangle(
        (int(width * 0.07), accent_y, int(width * 0.21), accent_y + max(8, height // 110)),
        radius=8,
        fill="#17E0C4",
    )
    for index in range(9):
        x = int(width * (0.62 + (index % 3) * 0.12))
        y = int(height * (0.12 + (index // 3) * 0.10))
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="#17E0C4")
        if index % 3:
            draw.line((x - int(width * 0.12), y, x, y), fill="#3E37F5", width=2)
        if index >= 3:
            draw.line((x, y - int(height * 0.10), x, y), fill="#3E37F5", width=2)

    margin = int(width * 0.07)
    label_font = _font(max(18, width // 45), bold=True)
    draw.text((margin, int(height * 0.21)), platform.label.upper(), font=label_font, fill="#17E0C4")

    clean_headline = " ".join(headline.split())[:180] or "Nueva idea"
    font_size = max(34, min(width // 11, height // 7))
    while font_size >= 28:
        title_font = _font(font_size, bold=True)
        avg_char = max(font_size * 0.55, 1)
        line_width = max(9, int((width - 2 * margin) / avg_char))
        lines = textwrap.wrap(clean_headline, width=line_width)
        bbox = draw.multiline_textbbox(
            (0, 0), "\n".join(lines), font=title_font, spacing=font_size // 3
        )
        if bbox[3] - bbox[1] <= height * 0.48:
            break
        font_size -= 4
    title = "\n".join(lines[:6])
    draw.multiline_text(
        (margin, int(height * 0.32)), title, font=title_font, fill="#E7E7EF", spacing=font_size // 3
    )
    footer_font = _font(max(16, width // 55))
    draw.text(
        (margin, height - int(height * 0.09)),
        "CREADO LOCALMENTE CON EDECAN",
        font=footer_font,
        fill="#77778A",
    )

    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


class CrearContenidoSocialTool(Tool):
    name = "crear_contenido_social"
    description = (
        "Crea un paquete para una red social compatible: copy final, manifiesto reutilizable "
        "y una imagen original opcional. Entrega "
        "todos los archivos en el chat para abrirlos o compartirlos desde web, iOS y Android. "
        "No publica automáticamente ni usa APIs de publicación. Para textos cortos, puede "
        "convertir contenido largo en un hilo numerado."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "plataforma": {"type": "string", "enum": list(PLATFORMS)},
            "tema": {"type": "string", "description": "Tema o propósito del post."},
            "texto": {
                "type": "string",
                "description": "Copy final, verdadero y listo para revisión humana.",
            },
            "titular_visual": {
                "type": "string",
                "description": "Titular breve para la tarjeta local si no hay modelo de imágenes.",
            },
            "visual_prompt": {
                "type": "string",
                "description": (
                    "Descripción visual original, sin pedir texto ilegible dentro de la imagen."
                ),
            },
            "alt_text": {"type": "string", "description": "Descripción accesible de la imagen."},
            "hashtags": {"type": "array", "items": {"type": "string"}, "maxItems": _MAX_HASHTAGS},
            "con_imagen": {"type": "boolean", "default": True},
        },
        "required": ["plataforma", "tema", "texto"],
    }

    def __init__(
        self,
        *,
        image_provider: ImageProvider | None = None,
        uploader: Uploader | None = None,
    ) -> None:
        self._image_provider = image_provider
        self._uploader = uploader or subir_archivo

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        platform_key = str(args.get("plataforma") or "").strip().lower()
        spec = PLATFORMS.get(platform_key)
        if spec is None:
            return ToolResult(content=f"Plataforma inválida. Usa una de: {', '.join(PLATFORMS)}.")
        topic = _cap_str(args.get("tema"), _MAX_TOPIC_CHARS)
        copy = _cap_str(args.get("texto"), _MAX_COPY_CHARS)
        if not topic or not copy:
            return ToolResult(content="Necesito el tema y el texto final del post.")

        hashtags = _clean_hashtags(args.get("hashtags"))
        if hashtags:
            copy = f"{copy.rstrip()}\n\n" + " ".join(f"#{tag}" for tag in hashtags)
        parts = _split_x_thread(copy, spec.max_chars) if platform_key == "x" else [copy]
        if platform_key != "x" and len(copy) > spec.max_chars:
            return ToolResult(
                content=f"El copy excede el límite de {spec.label} ({len(copy)}/{spec.max_chars})."
            )

        headline = _cap_str(args.get("titular_visual"), 180) or topic
        visual_prompt = _cap_str(args.get("visual_prompt"), _MAX_VISUAL_PROMPT_CHARS) or topic
        alt_text = _cap_str(args.get("alt_text"), _MAX_ALT_CHARS)
        manifest = {
            "schema_version": 1,
            "platform": platform_key,
            "platform_label": spec.label,
            "topic": topic,
            "copy": copy,
            "parts": parts,
            "hashtags": hashtags,
            "visual": {
                "headline": headline,
                "prompt": visual_prompt,
                "alt_text": alt_text,
                "size": spec.image_size,
            },
            "publication": {"status": "draft", "requires_human_confirmation": True},
        }
        slug = _slug(f"{platform_key}-{topic}")
        markdown_lines = [f"# {topic}", "", f"Plataforma: {spec.label}", "", "## Copy", ""]
        for index, part in enumerate(parts, start=1):
            if len(parts) > 1:
                markdown_lines.extend([f"### Parte {index}", ""])
            markdown_lines.extend([part, ""])
        if alt_text:
            markdown_lines.extend(["## Texto alternativo", "", alt_text, ""])
        markdown_lines.extend(["Estado: borrador. Requiere revisión humana antes de publicar.", ""])

        artifacts: list[dict[str, str]] = []
        markdown_id, markdown_name = await self._uploader(
            ctx,
            data="\n".join(markdown_lines).encode("utf-8"),
            filename=f"{slug}.md",
            mime="text/markdown",
        )
        artifacts.append(
            {"file_id": str(markdown_id), "filename": markdown_name, "mime": "text/markdown"}
        )
        json_id, json_name = await self._uploader(
            ctx,
            data=json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            filename=f"{slug}.json",
            mime="application/json",
        )
        artifacts.append(
            {"file_id": str(json_id), "filename": json_name, "mime": "application/json"}
        )

        offline_visual = False
        if args.get("con_imagen", True) is not False:
            provider = self._image_provider or await get_tenant_image_provider(ctx)
            offline_visual = isinstance(provider, StubImageProvider)
            if offline_visual:
                image = _offline_social_card(headline=headline, platform=spec, size=spec.image_size)
            else:
                prompt = (
                    f"{visual_prompt}. Original editorial image for {spec.label}. "
                    "No words, captions, logos, watermarks or interface elements "
                    "inside the base image. "
                    "The image must communicate the specific idea, not a generic technology mood."
                )
                image = await provider.generate(prompt, size=spec.image_size)
            image_id, image_name = await self._uploader(
                ctx, data=image, filename=f"{slug}.png", mime="image/png"
            )
            artifacts.append(
                {"file_id": str(image_id), "filename": image_name, "mime": "image/png"}
            )

        mode = (
            "una tarjeta editorial local"
            if offline_visual
            else "una imagen del proveedor conectado"
        )
        return ToolResult(
            content=(
                f"Creé el paquete para {spec.label}: copy"
                f"{' en ' + str(len(parts)) + ' partes' if len(parts) > 1 else ''}, manifiesto"
                + (f" y {mode}." if len(artifacts) == 3 else ".")
                + " Quedó como borrador privado; no publiqué nada."
            ),
            data={
                "artifacts": artifacts,
                "platform": platform_key,
                "offline_visual": offline_visual,
            },
        )


__all__ = ["CrearContenidoSocialTool", "PLATFORMS"]
