"""Herramientas de creatividad: imágenes y documentos de oficina (`ARCHITECTURE.md`
§10.7; `ROADMAP_V2.md` §7.7, tabla `edecan_creative`).

Las cuatro herramientas (`generar_imagen`, `crear_documento`,
`crear_presentacion`, `crear_pdf`) generan un archivo en memoria y lo suben
con `edecan_creative._files.subir_archivo` (S3 + fila `files`), inyectado por
constructor con ese default real — el patrón inyectable que pide
`ROADMAP_V2.md` §7.7 para que los tests puedan sustituirlo sin tocar S3 ni
Postgres. Ninguna es `dangerous`: no publican nada, solo generan y guardan un
archivo privado del tenant (`ARCHITECTURE.md` §2, prefijo S3 por tenant).
Todas devuelven `data={"file_id", "filename"}` y un `content` en español listo
para mostrar en el chat.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from typing import Any

from docx import Document
from edecan_core import Tool, ToolContext, ToolResult
from edecan_core.queue import enqueue
from fpdf import FPDF
from pptx import Presentation

from ._files import Uploader, subir_archivo
from .podcast import (
    FORMATOS_SOPORTADOS,
    MAX_SEGMENTOS,
    MIN_SEGMENTOS,
    GuionInvalidoError,
    generar_efecto,
    resolver_config_tts_tenant,
    validar_guion,
)
from .providers import DEFAULT_SIZE, ImageProvider, get_tenant_image_provider

logger = logging.getLogger(__name__)

# Límites para no generar archivos absurdos (ROADMAP_V2.md §7.7): topes sobre
# las listas de nivel superior (secciones/diapositivas/párrafos) y sobre las
# listas anidadas (párrafos por sección, bullets por diapositiva), más un tope
# de longitud por string individual. Ninguno de estos valores está pinned en
# el roadmap más allá de "máx 100 secciones/diapositivas, strings capped" — se
# eligieron generosos mientras acotados, documentados en `docs/creatividad.md`.
_MAX_TOP_ITEMS = 100
_MAX_NESTED_ITEMS = 200
_MAX_TITLE_CHARS = 300
_MAX_TEXT_CHARS = 4000
_MAX_PROMPT_PREVIEW_CHARS = 80

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_PDF_MIME = "application/pdf"


def _cap_str(value: Any, max_chars: int) -> str:
    """Convierte `value` a `str`, recorta espacios y lo acota a `max_chars`."""
    return str(value or "").strip()[:max_chars]


def _slug(texto: str) -> str:
    """Nombre de archivo seguro derivado de `texto`: minúsculas, ASCII, `-` como
    separador (quita acentos con NFKD en vez de descartar la palabra completa,
    p. ej. "Métricas Q3" → "metricas-q3"). Nunca vacío: cae a "documento".
    """
    normalizado = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalizado.strip().lower()).strip("-")
    return slug[:60] or "documento"


class GenerarImagenTool(Tool):
    name = "generar_imagen"
    description = (
        "Genera una imagen a partir de una descripción (prompt) usando el proveedor de "
        "imágenes configurado, y la guarda como archivo del usuario. El proveedor por "
        "defecto es un generador offline determinista (sin proveedor de imágenes real "
        "configurado, produce una imagen simple con el prompt como texto, no una foto "
        "realista)."
    )
    requires_flags = frozenset({"tools.images"})
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Descripción de la imagen a generar.",
            },
            "tamano": {
                "type": "string",
                "description": "Tamaño de la imagen, formato ANCHOxALTO en píxeles.",
                "default": DEFAULT_SIZE,
            },
        },
        "required": ["prompt"],
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
        prompt = _cap_str(args.get("prompt"), _MAX_TEXT_CHARS)
        if not prompt:
            return ToolResult(content="Necesito una descripción (prompt) para generar la imagen.")
        tamano = _cap_str(args.get("tamano"), 32) or DEFAULT_SIZE

        provider = self._image_provider or await get_tenant_image_provider(ctx)
        png_bytes = await provider.generate(prompt, size=tamano)

        filename = f"{_slug(prompt)}.png"
        file_id, filename = await self._uploader(
            ctx, data=png_bytes, filename=filename, mime="image/png"
        )

        preview = prompt[:_MAX_PROMPT_PREVIEW_CHARS]
        if len(prompt) > _MAX_PROMPT_PREVIEW_CHARS:
            preview += "…"
        return ToolResult(
            content=f"Creé la imagen «{preview}» ({tamano}) y la guardé como «{filename}».",
            data={
                "file_id": str(file_id),
                "filename": filename,
                "mime": "image/png",
                "alt_text": prompt,
                "caption": preview,
            },
        )


def _normalizar_secciones(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    secciones: list[dict[str, Any]] = []
    for item in raw[:_MAX_TOP_ITEMS]:
        if not isinstance(item, dict):
            continue
        encabezado = _cap_str(item.get("encabezado"), _MAX_TITLE_CHARS)
        if not encabezado:
            continue
        parrafos_raw = item.get("parrafos")
        parrafos = (
            [
                _cap_str(p, _MAX_TEXT_CHARS)
                for p in parrafos_raw[:_MAX_NESTED_ITEMS]
                if _cap_str(p, _MAX_TEXT_CHARS)
            ]
            if isinstance(parrafos_raw, list)
            else []
        )
        secciones.append({"encabezado": encabezado, "parrafos": parrafos})
    return secciones


class CrearDocumentoTool(Tool):
    name = "crear_documento"
    description = (
        "Genera un documento de Word (.docx) con un título y una o más secciones "
        "(encabezado + párrafos), y lo guarda como archivo del usuario."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "titulo": {"type": "string", "description": "Título del documento."},
            "secciones": {
                "type": "array",
                "description": "Secciones del documento, cada una con encabezado y párrafos.",
                "items": {
                    "type": "object",
                    "properties": {
                        "encabezado": {"type": "string"},
                        "parrafos": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["encabezado"],
                },
            },
        },
        "required": ["titulo", "secciones"],
    }

    def __init__(self, *, uploader: Uploader | None = None) -> None:
        self._uploader = uploader or subir_archivo

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        titulo = _cap_str(args.get("titulo"), _MAX_TITLE_CHARS)
        if not titulo:
            return ToolResult(content="Necesito un título para el documento.")
        if not isinstance(args.get("secciones"), list) or not args["secciones"]:
            return ToolResult(
                content=(
                    "Necesito al menos una sección (con encabezado y párrafos) para el documento."
                )
            )
        secciones = _normalizar_secciones(args["secciones"])
        if not secciones:
            return ToolResult(content="Ninguna de las secciones tenía un encabezado válido.")

        document = Document()
        document.add_heading(titulo, level=0)
        for seccion in secciones:
            document.add_heading(seccion["encabezado"], level=1)
            for parrafo in seccion["parrafos"]:
                document.add_paragraph(parrafo)

        buffer = io.BytesIO()
        document.save(buffer)
        data = buffer.getvalue()

        filename = f"{_slug(titulo)}.docx"
        file_id, filename = await self._uploader(ctx, data=data, filename=filename, mime=_DOCX_MIME)

        n_parrafos = sum(len(s["parrafos"]) for s in secciones)
        return ToolResult(
            content=(
                f"Creé el documento «{titulo}» con {len(secciones)} sección(es) y "
                f"{n_parrafos} párrafo(s), guardado como «{filename}»."
            ),
            data={"file_id": str(file_id), "filename": filename, "mime": _DOCX_MIME},
        )


def _normalizar_diapositivas(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    diapositivas: list[dict[str, Any]] = []
    for item in raw[:_MAX_TOP_ITEMS]:
        if not isinstance(item, dict):
            continue
        titulo = _cap_str(item.get("titulo"), _MAX_TITLE_CHARS)
        if not titulo:
            continue
        bullets_raw = item.get("bullets")
        bullets = (
            [
                _cap_str(b, _MAX_TEXT_CHARS)
                for b in bullets_raw[:_MAX_NESTED_ITEMS]
                if _cap_str(b, _MAX_TEXT_CHARS)
            ]
            if isinstance(bullets_raw, list)
            else []
        )
        diapositivas.append({"titulo": titulo, "bullets": bullets})
    return diapositivas


class CrearPresentacionTool(Tool):
    name = "crear_presentacion"
    description = (
        "Genera una presentación de PowerPoint (.pptx) con una portada de título y una "
        "diapositiva de título+contenido por cada elemento de 'diapositivas', y la "
        "guarda como archivo del usuario."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "titulo": {"type": "string", "description": "Título de la presentación (portada)."},
            "diapositivas": {
                "type": "array",
                "description": "Diapositivas de contenido, cada una con título y viñetas.",
                "items": {
                    "type": "object",
                    "properties": {
                        "titulo": {"type": "string"},
                        "bullets": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["titulo"],
                },
            },
        },
        "required": ["titulo", "diapositivas"],
    }

    def __init__(self, *, uploader: Uploader | None = None) -> None:
        self._uploader = uploader or subir_archivo

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        titulo = _cap_str(args.get("titulo"), _MAX_TITLE_CHARS)
        if not titulo:
            return ToolResult(content="Necesito un título para la presentación.")
        if not isinstance(args.get("diapositivas"), list) or not args["diapositivas"]:
            return ToolResult(
                content=(
                    "Necesito al menos una diapositiva (con título y viñetas) para la presentación."
                )
            )
        diapositivas = _normalizar_diapositivas(args["diapositivas"])
        if not diapositivas:
            return ToolResult(content="Ninguna de las diapositivas tenía un título válido.")

        presentation = Presentation()
        portada = presentation.slides.add_slide(presentation.slide_layouts[0])
        portada.shapes.title.text = titulo

        contenido_layout = presentation.slide_layouts[1]
        for diapositiva in diapositivas:
            slide = presentation.slides.add_slide(contenido_layout)
            slide.shapes.title.text = diapositiva["titulo"]
            bullets = diapositiva["bullets"] or [""]
            cuerpo = slide.placeholders[1].text_frame
            cuerpo.text = bullets[0]
            for bullet in bullets[1:]:
                cuerpo.add_paragraph().text = bullet

        buffer = io.BytesIO()
        presentation.save(buffer)
        data = buffer.getvalue()

        filename = f"{_slug(titulo)}.pptx"
        file_id, filename = await self._uploader(ctx, data=data, filename=filename, mime=_PPTX_MIME)

        return ToolResult(
            content=(
                f"Creé la presentación «{titulo}» con {len(diapositivas)} diapositiva(s) "
                f"de contenido (más la portada), guardada como «{filename}»."
            ),
            data={"file_id": str(file_id), "filename": filename, "mime": _PPTX_MIME},
        )


def _normalizar_parrafos_pdf(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [
        _cap_str(p, _MAX_TEXT_CHARS) for p in raw[:_MAX_TOP_ITEMS] if _cap_str(p, _MAX_TEXT_CHARS)
    ]


def _sanitizar_pdf(texto: str) -> str:
    """Sanea `texto` al charset latin-1 (ISO-8859-1) que soportan las fuentes core
    de fpdf2 (Helvetica/Times/Courier), sustituyendo cualquier carácter fuera de
    rango por '?'. Evita descargar una fuente TrueType solo para soportar Unicode
    completo (regla dura de la plataforma: nada de red al generar un archivo) — la
    limitación de caracteres queda documentada en `docs/creatividad.md`.
    """
    return texto.encode("latin-1", errors="replace").decode("latin-1")


def _render_pdf(titulo: str, parrafos: list[str]) -> bytes:
    """Arma un PDF simple: portada con el título, luego el cuerpo en párrafos
    (fuente core Helvetica; `set_auto_page_break` agrega páginas automáticamente
    si el cuerpo no cabe en una sola).
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.add_page()
    pdf.set_font("Helvetica", "B", 26)
    pdf.ln(70)
    pdf.multi_cell(0, 14, _sanitizar_pdf(titulo), align="C")

    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for parrafo in parrafos:
        pdf.multi_cell(0, 7, _sanitizar_pdf(parrafo))
        pdf.ln(4)

    return bytes(pdf.output())


class CrearPdfTool(Tool):
    name = "crear_pdf"
    description = (
        "Genera un PDF con una portada (título) y un cuerpo de párrafos, y lo guarda "
        "como archivo del usuario. Usa una fuente core (Helvetica): los caracteres "
        "fuera de latin-1 (la mayoría de emojis y algunos símbolos tipográficos) se "
        "sustituyen por '?'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "titulo": {"type": "string", "description": "Título / portada del PDF."},
            "parrafos": {
                "type": "array",
                "description": "Párrafos del cuerpo del documento, en orden.",
                "items": {"type": "string"},
            },
        },
        "required": ["titulo", "parrafos"],
    }

    def __init__(self, *, uploader: Uploader | None = None) -> None:
        self._uploader = uploader or subir_archivo

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        titulo = _cap_str(args.get("titulo"), _MAX_TITLE_CHARS)
        if not titulo:
            return ToolResult(content="Necesito un título para el PDF.")
        parrafos = _normalizar_parrafos_pdf(args.get("parrafos"))
        if not parrafos:
            return ToolResult(content="Necesito al menos un párrafo de contenido para el PDF.")

        data = _render_pdf(titulo, parrafos)

        filename = f"{_slug(titulo)}.pdf"
        file_id, filename = await self._uploader(ctx, data=data, filename=filename, mime=_PDF_MIME)

        return ToolResult(
            content=(
                f"Creé el PDF «{titulo}» con {len(parrafos)} párrafo(s), "
                f"guardado como «{filename}»."
            ),
            data={"file_id": str(file_id), "filename": filename, "mime": _PDF_MIME},
        )


# --- Podcasts y efectos de sonido (WP-V5-11, ARCHITECTURE.md §14) ---------------------------

_MAX_DESCRIPCION_EFECTO_CHARS = 500
_EFFECT_MIME = {"mp3": "audio/mpeg", "wav": "audio/wav"}


class CrearPodcastTool(Tool):
    name = "crear_podcast"
    description = (
        "Crea un podcast a partir de un guion (segmentos con orador + texto) y lo encola "
        "para generarse en segundo plano con el proveedor de voz (TTS) de tu cuenta "
        "(ElevenLabs si lo conectaste; si no, un audio de prueba offline) — aparece como "
        "archivo en unos minutos. El guion debe venir ya escrito en 'segmentos', esta "
        "herramienta no lo redacta."
    )
    requires_flags = frozenset({"tools.podcast"})
    input_schema = {
        "type": "object",
        "properties": {
            "titulo": {"type": "string", "description": "Título del podcast."},
            "segmentos": {
                "type": "array",
                "description": (
                    f"Guion del podcast: entre {MIN_SEGMENTOS} y {MAX_SEGMENTOS} segmentos, "
                    "cada uno con lo que dice un orador."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "orador": {
                            "type": "string",
                            "description": "Nombre del orador de este segmento.",
                        },
                        "texto": {
                            "type": "string",
                            "description": "Lo que dice el orador en este segmento.",
                        },
                        "voice_id": {
                            "type": "string",
                            "description": (
                                "voice_id de ElevenLabs para este segmento (opcional; si se "
                                "omite se usa la voz por defecto de tu credencial)."
                            ),
                        },
                    },
                    "required": ["texto"],
                },
            },
            "formato": {
                "type": "string",
                "enum": list(FORMATOS_SOPORTADOS),
                "description": (
                    "Formato de salida preferido. El formato real siempre depende del "
                    "proveedor de voz que tengas conectado: 'wav' sin credencial (audio de "
                    "prueba), 'mp3' con ElevenLabs — pedir el que no coincide no fuerza una "
                    "conversión, se usa igual el formato real de tu proveedor."
                ),
            },
        },
        "required": ["titulo", "segmentos"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        titulo = _cap_str(args.get("titulo"), _MAX_TITLE_CHARS)
        if not titulo:
            return ToolResult(content="Necesito un título para el podcast.")

        try:
            segmentos = validar_guion(args.get("segmentos"))
        except GuionInvalidoError as exc:
            return ToolResult(content=str(exc))

        formato = args.get("formato")
        if formato not in (None, *FORMATOS_SOPORTADOS):
            return ToolResult(
                content=f"'formato' debe ser {' o '.join(FORMATOS_SOPORTADOS)} (o vacío)."
            )

        payload = {
            "titulo": titulo,
            "formato": formato,
            "segmentos": [
                {"orador": s.orador, "texto": s.texto, "voice_id": s.voice_id} for s in segmentos
            ],
            # `JobEnvelope` no trae `user_id` (`ARCHITECTURE.md` §10.5) y este
            # job, a diferencia de `ingest_file`/`generate_content`, no tiene
            # ninguna fila previa (`file`/`conversation`) de la que heredarlo:
            # nace de cero e inserta una fila `files` nueva, cuya columna
            # `user_id` es `NOT NULL` (FK a `users`, migración `0001_initial`).
            # Viaja explícito en el payload para que el worker pueda atribuir
            # el archivo generado al usuario real que pidió el podcast.
            "user_id": str(ctx.user_id),
        }
        await enqueue(ctx.settings, "generate_podcast", payload, ctx.tenant_id)

        logger.info(
            "crear_podcast: encolado titulo=%r segmentos=%d tenant=%s",
            titulo,
            len(segmentos),
            ctx.tenant_id,
        )
        return ToolResult(
            content="Podcast en producción: aparecerá en Archivos en unos minutos.",
            data={"titulo": titulo, "segmentos": len(segmentos)},
        )


class GenerarEfectoSonidoTool(Tool):
    name = "generar_efecto_sonido"
    description = (
        "Genera un efecto de sonido SIN voz (ej. aplausos, lluvia, timbre, pasos) a partir "
        "de una descripción, usando el proveedor de voz (TTS) de tu cuenta (ElevenLabs si lo "
        "conectaste), y lo guarda como archivo del usuario. Sin credencial propia conectada "
        "genera un tono de prueba en vez de un efecto real."
    )
    requires_flags = frozenset({"tools.podcast"})
    input_schema = {
        "type": "object",
        "properties": {
            "descripcion": {
                "type": "string",
                "description": (
                    "Descripción del efecto de sonido a generar "
                    "(ej. 'aplausos entusiastas', 'lluvia suave sobre un techo')."
                ),
            },
        },
        "required": ["descripcion"],
    }

    def __init__(self, *, uploader: Uploader | None = None) -> None:
        self._uploader = uploader or subir_archivo

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        descripcion = _cap_str(args.get("descripcion"), _MAX_DESCRIPCION_EFECTO_CHARS)
        if not descripcion:
            return ToolResult(content="Necesito una descripción para generar el efecto de sonido.")

        cfg = await resolver_config_tts_tenant(
            session=getattr(ctx, "session", None),
            vault=getattr(ctx, "vault", None),
            tenant_id=getattr(ctx, "tenant_id", None),
        )
        audio = await generar_efecto(cfg, descripcion=descripcion, tenant_id=ctx.tenant_id)

        filename = f"{_slug(descripcion)}.{audio.formato}"
        file_id, filename = await self._uploader(
            ctx, data=audio.data, filename=filename, mime=_EFFECT_MIME[audio.formato]
        )

        aviso = (
            " (sin proveedor de voz conectado: generé un tono de prueba, no un efecto real)"
            if audio.es_stub
            else ""
        )
        return ToolResult(
            content=f"Generé el efecto «{descripcion}»{aviso} y lo guardé como «{filename}».",
            data={
                "file_id": str(file_id),
                "filename": filename,
                "mime": _EFFECT_MIME[audio.formato],
                "caption": descripcion,
                "es_stub": audio.es_stub,
            },
        )
