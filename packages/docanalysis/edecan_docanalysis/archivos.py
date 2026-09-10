"""Lectura universal y edición reversible de archivos desde el chat."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult

from . import _s3
from ._util import parse_uuid

_MAX_FILE_BYTES = _s3.MAX_DESCARGABLE_BYTES
_MAX_TEXT_CHARS = 80_000
_MAX_ZIP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
_MAX_ARCHIVE_ENTRIES = 1000
_MAX_MEMBER_TEXT_BYTES = 5 * 1024 * 1024
_ZIP_MIMES = {"application/zip", "application/x-zip-compressed", "application/x-zip"}
_ARCHIVE_TEXT_EXTS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm",
    ".css", ".js", ".jsx", ".ts", ".tsx", ".py", ".yml", ".yaml", ".toml",
    ".log", ".sh", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".hpp",
    ".sql", ".ini", ".cfg", ".conf", ".rst", ".tex", ".php", ".rb", ".swift",
}


@dataclass
class _Extraccion:
    """Resultado de `_extract_text`: texto visible + línea de cobertura opcional.

    La cobertura viaja SEPARADA del texto (no dentro) para que el corte por
    `_MAX_TEXT_CHARS` de la tool jamás la recorte: siempre se informa la
    cobertura real (BOTS-17)."""

    texto: str
    cobertura: str | None = None


def _linea_cobertura(
    leidas: int, total: int, *, unidad: str, motivo: str | None = None
) -> str:
    omitidas = max(total - leidas, 0)
    detalle = f"; omitidas: {omitidas}"
    if motivo:
        detalle += f" ({motivo})"
    return f"[Cobertura: {leidas} de {total} {unidad}{detalle}]"


def _ruta_relativa(raw: str) -> str:
    """Normaliza el nombre de un miembro ZIP/TAR a una ruta relativa legible:
    separadores Windows → `/`, sin barras iniciales ni componentes `.`/vacíos.

    No se extrae a disco, así que esto es solo identidad/display (BOTS-17): dos
    miembros `a/config.py` y `b/config.py` dejan de colapsar a `config.py`.
    """
    partes = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    return "/".join(partes) if partes else raw


def _normalized_mime(mime: str) -> str:
    return (mime or "application/octet-stream").split(";", 1)[0].strip().lower()


def _validate_office_archive(data: bytes) -> None:
    """Rechaza ZIP bombs antes de entregar OOXML a librerías de terceros."""

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        expanded = sum(info.file_size for info in archive.infolist())
    if expanded > _MAX_ZIP_UNCOMPRESSED_BYTES:
        raise ValueError("El documento expandido supera 100 MB")


def _extract_text(data: bytes, *, filename: str, mime: str) -> _Extraccion | None:
    name = filename.lower()
    normalized = _normalized_mime(mime)
    if normalized.startswith("text/") or name.endswith(
        (".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".css", ".js", ".py")
    ):
        return _Extraccion(data.decode("utf-8", errors="replace"))
    if normalized == "application/pdf" or name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        total_pages = len(reader.pages)
        pages: list[str] = []
        total_chars = 0
        motivo = None
        for index, page in enumerate(reader.pages[:200], start=1):
            text = f"[Página {index}]\n{page.extract_text() or ''}"
            pages.append(text)
            total_chars += len(text)
            if total_chars >= _MAX_TEXT_CHARS:
                motivo = "límite de caracteres"
                break
        else:
            if total_pages > 200:
                motivo = "límite de 200 páginas"
        cobertura = _linea_cobertura(
            len(pages), total_pages, unidad="páginas", motivo=motivo
        )
        return _Extraccion("\n\n".join(pages), cobertura)
    if name.endswith(".docx") or normalized.endswith("wordprocessingml.document"):
        import docx

        _validate_office_archive(data)
        document = docx.Document(io.BytesIO(data))
        total_parrafos = len(document.paragraphs)
        paragraphs: list[str] = []
        total_chars = 0
        motivo = None
        for paragraph in document.paragraphs[:20_000]:
            paragraphs.append(paragraph.text)
            total_chars += len(paragraph.text)
            if total_chars >= _MAX_TEXT_CHARS:
                motivo = "límite de caracteres"
                break
        else:
            if total_parrafos > 20_000:
                motivo = "límite de 20.000 párrafos"
        cobertura = _linea_cobertura(
            len(paragraphs), total_parrafos, unidad="párrafos", motivo=motivo
        )
        return _Extraccion("\n".join(paragraphs), cobertura)
    if name.endswith(".xlsx") or normalized.endswith("spreadsheetml.sheet"):
        from openpyxl import load_workbook

        _validate_office_archive(data)
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        total_hojas = len(workbook.worksheets)
        sheets: list[str] = []
        total_chars = 0
        motivo = None
        for sheet in workbook.worksheets[:50]:
            rows: list[str] = []
            for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                rendered = "\t".join(
                    "" if value is None else str(value) for value in row[:200]
                )
                rows.append(rendered)
                total_chars += len(rendered)
                if row_index >= 10_000 or total_chars >= _MAX_TEXT_CHARS:
                    motivo = "límite de filas/caracteres"
                    break
            sheets.append(f"[Hoja: {sheet.title}]\n" + "\n".join(rows))
            if total_chars >= _MAX_TEXT_CHARS:
                break
        if total_hojas > 50 and motivo is None:
            motivo = "límite de 50 hojas"
        cobertura = _linea_cobertura(
            len(sheets), total_hojas, unidad="hojas", motivo=motivo
        )
        return _Extraccion("\n\n".join(sheets), cobertura)
    if name.endswith(".pptx") or normalized.endswith("presentationml.presentation"):
        from pptx import Presentation

        _validate_office_archive(data)
        presentation = Presentation(io.BytesIO(data))
        total_slides = len(presentation.slides)
        slides: list[str] = []
        total_chars = 0
        motivo = None
        for index, slide in enumerate(presentation.slides, start=1):
            if index > 500:
                motivo = "límite de 500 diapositivas"
                break
            text = "\n".join(
                shape.text for shape in slide.shapes if getattr(shape, "has_text_frame", False)
            )
            slides.append(f"[Diapositiva {index}]\n{text}")
            total_chars += len(text)
            if total_chars >= _MAX_TEXT_CHARS:
                motivo = "límite de caracteres"
                break
        cobertura = _linea_cobertura(
            len(slides), total_slides, unidad="diapositivas", motivo=motivo
        )
        return _Extraccion("\n\n".join(slides), cobertura)
    if (
        name.endswith(".zip")
        or normalized in _ZIP_MIMES
        or name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".gz"))
    ):
        return _extract_archive_text(data, filename)
    return None


def _extract_archive_text(data: bytes, filename: str) -> _Extraccion | None:
    """Lee un comprimido (ZIP/TAR/GZ) sin escribirlo a disco: lista su
    contenido y extrae el texto de los miembros de texto plano, con topes
    contra bombs de compresión (expansión máxima declarada, número de
    entradas, tamaño por miembro y total de texto).

    Preserva la RUTA RELATIVA de cada miembro (`_ruta_relativa`) para que
    `a/config.py` y `b/config.py` no colapsen a `config.py` (BOTS-17) y reporta
    cobertura real (`n_extraidos` de `n_archivos`, con el motivo de omitidas)."""

    import gzip
    import tarfile

    name = filename.lower()
    listado: list[str] = []
    textos: list[str] = []
    total_text = 0
    n_archivos = 0
    n_extraidos = 0
    omitidas_razones: set[str] = set()

    def es_texto(miembro: str) -> bool:
        return PurePath(miembro.lower()).suffix in _ARCHIVE_TEXT_EXTS

    def agregar(nombre: str, tamano: int, contenido: bytes | None) -> None:
        nonlocal total_text, n_archivos, n_extraidos
        n_archivos += 1
        es_txt = es_texto(nombre)
        listado.append(f"- {nombre} ({tamano} bytes{', texto' if es_txt else ', binario'})")
        if contenido is None or not es_txt or total_text >= _MAX_TEXT_CHARS:
            if not es_txt:
                omitidas_razones.add("binarios")
            elif contenido is None:
                omitidas_razones.add("miembros grandes/no legibles")
            else:
                omitidas_razones.add("límite de caracteres")
            return
        restante = _MAX_TEXT_CHARS - total_text
        decodificado = contenido.decode("utf-8", errors="replace")[:restante]
        textos.append(f"[{nombre}]\n{decodificado}")
        total_text += len(decodificado)
        n_extraidos += 1

    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                infos = zf.infolist()
                expandido = sum(info.file_size for info in infos)
                if len(infos) > _MAX_ARCHIVE_ENTRIES or expandido > _MAX_ZIP_UNCOMPRESSED_BYTES:
                    return _Extraccion(
                        f"'{filename}' expande demasiado ({expandido} bytes) y no lo leo "
                        "completo."
                    )
                for info in infos:
                    if info.is_dir():
                        continue
                    nombre = _ruta_relativa(info.filename)
                    contenido = None
                    if es_texto(info.filename) and info.file_size <= _MAX_MEMBER_TEXT_BYTES:
                        try:
                            contenido = zf.read(info)
                        except Exception:
                            contenido = None
                    agregar(nombre, info.file_size, contenido)
        elif name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2")):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                miembros = tf.getmembers()
                expandido = sum(m.size for m in miembros if m.isfile())
                if len(miembros) > _MAX_ARCHIVE_ENTRIES or expandido > _MAX_ZIP_UNCOMPRESSED_BYTES:
                    return _Extraccion(
                        f"'{filename}' expande demasiado ({expandido} bytes) y no lo leo "
                        "completo."
                    )
                for miembro in miembros:
                    if not miembro.isfile():
                        continue
                    nombre = _ruta_relativa(miembro.name)
                    contenido = None
                    if es_texto(miembro.name) and miembro.size <= _MAX_MEMBER_TEXT_BYTES:
                        origen = tf.extractfile(miembro)
                        if origen is not None:
                            try:
                                contenido = origen.read()
                            except Exception:
                                contenido = None
                    agregar(nombre, miembro.size, contenido)
        elif name.endswith(".gz"):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                decompressed = gz.read(_MAX_MEMBER_TEXT_BYTES)
            interno = PurePath(filename[:-3]).name or filename[:-3] or "contenido"
            agregar(interno, len(decompressed), decompressed)
        else:
            return None
    except Exception as exc:
        return _Extraccion(f"No pude abrir '{filename}' como comprimido: {type(exc).__name__}.")

    if n_archivos == 0:
        return _Extraccion(f"'{filename}' está vacío (sin archivos dentro).")

    motivo = ", ".join(sorted(omitidas_razones)) if omitidas_razones else None
    cobertura = _linea_cobertura(
        n_extraidos, n_archivos, unidad="entradas", motivo=motivo
    )

    partes = [f"[Comprimido: '{filename}' con {n_archivos} archivo(s)]", *listado]
    if textos:
        partes.append("")
        partes.extend(textos)
    return _Extraccion("\n".join(partes), cobertura)


class LeerArchivoTool(Tool):
    name = "leer_archivo"
    description = (
        "Abre y lee un archivo privado ya adjunto: PDF, Word, PowerPoint, Excel, CSV, "
        "JSON, Markdown, código, texto o comprimidos (ZIP, TAR, GZ). Si es una imagen, "
        "la analiza visualmente. Úsala antes de resumir, opinar, corregir o transformar "
        "un adjunto."
    )
    category = "read"
    risk_level = "none"
    input_schema = {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "Identificador del adjunto."},
            "pregunta": {
                "type": "string",
                "description": "Qué debe buscar o responder sobre el archivo.",
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
        if archivo.size_bytes > _MAX_FILE_BYTES:
            return ToolResult(content="El archivo supera 25 MB y no puedo abrirlo completo.")
        if _normalized_mime(archivo.mime).startswith("image/"):
            from .vision import AnalizarImagenTool

            return await AnalizarImagenTool().run(
                ctx,
                {
                    "file_id": str(file_id),
                    "pregunta": str(args.get("pregunta") or "").strip()
                    or "Describe, transcribe y analiza esta imagen.",
                },
            )
        try:
            extraccion = _extract_text(
                archivo.contenido, filename=archivo.filename, mime=archivo.mime
            )
        except Exception as exc:  # noqa: BLE001 - formatos de terceros heterogéneos
            return ToolResult(
                content=f"No pude abrir '{archivo.filename}': {type(exc).__name__}."
            )
        if extraccion is None:
            return ToolResult(
                content=(
                    f"'{archivo.filename}' sí está guardado, pero su formato todavía no tiene "
                    "un lector instalado. Puedo descargarlo o convertirlo a un formato compatible."
                )
            )
        text = extraccion.texto.strip()
        truncated = len(text) > _MAX_TEXT_CHARS
        visible = text[:_MAX_TEXT_CHARS]
        partes = [visible or "El archivo no contiene texto extraíble."]
        if truncated:
            partes.append("[Contenido truncado por longitud.]")
        # La cobertura se anexa DESPUÉS del corte, para que nunca se recorte
        # (siempre se informa la cobertura real, BOTS-17).
        if extraccion.cobertura:
            partes.append(extraccion.cobertura)
        return ToolResult(
            content="\n\n".join(partes),
            data={
                "file_id": str(file_id),
                "filename": archivo.filename,
                "mime": archivo.mime,
                "truncated": truncated,
            },
        )


def _pdf_safe_text(value: str) -> str:
    return value.encode("latin-1", errors="replace").decode("latin-1")


def _render_text_pdf(title: str, paragraphs: list[str]) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.multi_cell(0, 10, _pdf_safe_text(title))
    pdf.ln(4)
    pdf.set_font("Helvetica", size=11)
    for paragraph in paragraphs:
        pdf.multi_cell(0, 6, _pdf_safe_text(paragraph))
        pdf.ln(3)
    return bytes(pdf.output())


def _safe_pdf_filename(raw: str) -> str:
    candidate = PurePath(raw.replace("\\", "/")).name
    candidate = "".join(char for char in candidate if 32 <= ord(char) != 127).strip(" .")
    if not candidate.lower().endswith(".pdf"):
        return "pdf-editado.pdf"
    return candidate[:250] or "pdf-editado.pdf"


class EditarPdfTool(Tool):
    name = "editar_pdf"
    description = (
        "Edita un PDF adjunto sin destruir el original y entrega un PDF nuevo descargable. "
        "Puede reconstruir su texto corregido, anexar contenido, seleccionar/eliminar páginas "
        "y rotarlas. Para corregir texto, llama primero a leer_archivo y luego pasa aquí el "
        "contenido final completo."
    )
    category = "write"
    risk_level = "low"
    input_schema = {
        "type": "object",
        "properties": {
            "file_id": {"type": "string"},
            "modo": {"type": "string", "enum": ["reconstruir", "anexar", "paginas"]},
            "titulo": {"type": "string"},
            "parrafos": {"type": "array", "items": {"type": "string"}, "maxItems": 300},
            "paginas_conservar": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "maxItems": 500,
            },
            "rotacion": {"type": "integer", "enum": [0, 90, 180, 270]},
            "nombre_salida": {"type": "string"},
        },
        "required": ["file_id", "modo"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        file_id = parse_uuid(args.get("file_id"))
        if file_id is None:
            return ToolResult(content="'file_id' no es un identificador válido.")
        archivo = await _s3.descargar_archivo(ctx, file_id)
        if archivo is None:
            return ToolResult(content="No encontré ese PDF.")
        is_pdf = _normalized_mime(archivo.mime) == "application/pdf"
        if not is_pdf and not archivo.filename.lower().endswith(".pdf"):
            return ToolResult(content=f"'{archivo.filename}' no es un PDF.")

        modo = str(args.get("modo") or "").strip().lower()
        paragraphs = [str(item).strip() for item in args.get("parrafos") or [] if str(item).strip()]
        title = str(args.get("titulo") or PurePath(archivo.filename).stem).strip() or "Documento"
        try:
            if modo == "reconstruir":
                if not paragraphs:
                    return ToolResult(content="Para reconstruir el PDF necesito 'parrafos'.")
                output = _render_text_pdf(title, paragraphs)
            else:
                from pypdf import PdfReader, PdfWriter

                reader = PdfReader(io.BytesIO(archivo.contenido))
                writer = PdfWriter()
                selected = args.get("paginas_conservar") or list(range(1, len(reader.pages) + 1))
                rotation = int(args.get("rotacion") or 0)
                for page_number in selected:
                    index = int(page_number) - 1
                    if index < 0 or index >= len(reader.pages):
                        return ToolResult(content=f"La página {page_number} no existe.")
                    page = reader.pages[index]
                    if rotation:
                        page.rotate(rotation)
                    writer.add_page(page)
                if modo == "anexar":
                    if not paragraphs:
                        return ToolResult(content="Para anexar contenido necesito 'parrafos'.")
                    appendix = PdfReader(io.BytesIO(_render_text_pdf(title, paragraphs)))
                    for page in appendix.pages:
                        writer.add_page(page)
                elif modo != "paginas":
                    return ToolResult(content="'modo' debe ser reconstruir, anexar o paginas.")
                buffer = io.BytesIO()
                writer.write(buffer)
                output = buffer.getvalue()
        except Exception as exc:  # noqa: BLE001 - pypdf/fpdf exponen errores distintos
            return ToolResult(content=f"No pude editar el PDF: {type(exc).__name__}.")

        filename = _safe_pdf_filename(str(args.get("nombre_salida") or ""))
        output_id = await _s3.subir_resultado(
            ctx, filename=filename, mime="application/pdf", contenido=output
        )
        return ToolResult(
            content=f"Listo. Creé '{filename}' y conservé el original sin cambios.",
            data={
                "file_id": str(output_id),
                "filename": filename,
                "mime": "application/pdf",
                "source_file_id": str(file_id),
            },
        )
