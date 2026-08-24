"""Extractor de texto compartido + acceso a S3 de `edecan_advisory`
(ARCHITECTURE.md §10.3/§10.14, ROADMAP_V2.md §7.4/§7.7).

Dos responsabilidades juntas a propósito en un solo módulo (el WP-V2-11 las
pinnea así: "extractor compartido... descarga de S3 (helper propio
fakeable)"):

1. `descargar_archivo`/`subir_resultado`: mismo patrón que
   `edecan_docanalysis._s3` y `edecan_creative._files` — lee/escribe la tabla
   `files` con `sqlalchemy.text()` sobre `ctx.session` (no se importa
   `edecan_db.models`, ARCHITECTURE.md §10.1) y sube/baja bytes con un
   cliente `aioboto3` construido al vuelo (un `Tool` no tiene un slot
   reservado en `ToolContext` para un cliente S3 persistente, así que se abre
   y cierra en cada llamada — mismo criterio que
   `apps/api/edecan_api/routers/files.py::upload_file`). Este paquete lleva
   su propia copia de este helper en vez de importar el de un hermano
   (ARCHITECTURE.md §10.1: "cada paquete lleva su propia copia").
2. `extraer_texto`/`extraer_texto_de_file_id`: texto plano desde PDF
   (`pypdf`), DOCX (`python-docx`) o TXT/MD (UTF-8), capado a `MAX_CHARS`
   caracteres — ni `analizar_contrato` ni `comparar_contratos` ni
   `analizar_laboratorio` deberían mandar un documento sin límite a un LLM.

Settings se leen SIEMPRE con `getattr(ctx.settings, "CAMPO", default)`
(convención dura de ROADMAP_V2.md §7.5): nunca revienta si al `ctx.settings`
de prueba le falta un campo.
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from edecan_core import ToolContext
from sqlalchemy import text

_DEFAULT_BUCKET = "edecan-files"
_DEFAULT_REGION = "us-east-1"

#: Tope de caracteres que devuelve `extraer_texto` (ROADMAP_V2.md §7.7:
#: "texto capped (100k chars)").
MAX_CHARS = 100_000

_PDF_MIMES = {"application/pdf"}
_DOCX_MIMES = {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
_TXT_MIMES = {"text/plain", "text/markdown"}


class FormatoNoSoportado(ValueError):
    """`extraer_texto` no reconoce el mime/extensión de un `ArchivoDescargado`
    (ni PDF, ni DOCX, ni TXT/MD). Error "de negocio": el caller la atrapa y
    arma un `ToolResult` con un mensaje claro en vez de dejarla propagar."""


@dataclass
class ArchivoDescargado:
    """Contenido + metadatos de un `files.id` ya descargado de S3."""

    contenido: bytes
    filename: str
    mime: str
    size_bytes: int


@dataclass
class TextoExtraido:
    """Resultado de `extraer_texto_de_file_id`: el texto ya capado + el
    archivo de origen (para que el caller arme mensajes citando el `filename`)."""

    texto: str
    archivo: ArchivoDescargado


def _bucket(ctx: ToolContext) -> str:
    return getattr(ctx.settings, "S3_BUCKET", None) or _DEFAULT_BUCKET


def _client_kwargs(ctx: ToolContext) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"region_name": getattr(ctx.settings, "AWS_REGION", _DEFAULT_REGION)}
    endpoint_url = getattr(ctx.settings, "AWS_ENDPOINT_URL", None)
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return kwargs


async def _get_file_row(ctx: ToolContext, file_id: uuid.UUID) -> dict[str, Any] | None:
    resultado = await ctx.session.execute(
        text(
            "SELECT id, s3_key, filename, mime, size_bytes FROM files "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": str(ctx.tenant_id), "id": str(file_id)},
    )
    fila = resultado.mappings().first()
    return dict(fila) if fila is not None else None


async def descargar_archivo(ctx: ToolContext, file_id: uuid.UUID) -> ArchivoDescargado | None:
    """Descarga el contenido de `files.id` = `file_id` del tenant actual.

    `None` si el archivo no existe (o no pertenece a `ctx.tenant_id` — el
    `WHERE tenant_id = ...` de `_get_file_row` es la única barrera aquí
    porque `ctx.session` puede o no tener RLS activo según quién construya
    `ctx` en pruebas; en producción RLS ya lo garantiza también).
    """
    fila = await _get_file_row(ctx, file_id)
    if fila is None:
        return None

    import aioboto3  # import perezoso: mismo criterio que `edecan_worker.deps`

    session = aioboto3.Session()
    async with session.client("s3", **_client_kwargs(ctx)) as s3:
        respuesta = await s3.get_object(Bucket=_bucket(ctx), Key=fila["s3_key"])
        cuerpo = await respuesta["Body"].read()

    return ArchivoDescargado(
        contenido=cuerpo,
        filename=fila["filename"],
        mime=fila.get("mime") or "application/octet-stream",
        size_bytes=fila.get("size_bytes") or len(cuerpo),
    )


async def subir_resultado(
    ctx: ToolContext, *, filename: str, mime: str, contenido: bytes
) -> uuid.UUID:
    """Sube `contenido` a S3 y crea la fila `files` (`status='ready'`) —
    usado por `generar_borrador_legal` para guardar el `.md` generado.

    El `file_id` se genera en Python ANTES de subir (forma parte de la ruta
    S3, ARCHITECTURE.md §10.14) — mismo patrón que
    `apps/api/edecan_api/routers/files.py::upload_file`. Devuelve el `file_id`.
    """
    file_id = uuid.uuid4()
    s3_key = f"tenants/{ctx.tenant_id}/files/{file_id}/{filename}"

    import aioboto3  # import perezoso: mismo criterio que `edecan_worker.deps`

    session = aioboto3.Session()
    async with session.client("s3", **_client_kwargs(ctx)) as s3:
        await s3.put_object(Bucket=_bucket(ctx), Key=s3_key, Body=contenido, ContentType=mime)

    ahora = datetime.now(UTC)
    await ctx.session.execute(
        text(
            "INSERT INTO files ("
            "  id, tenant_id, user_id, s3_key, filename, mime, size_bytes, status,"
            "  created_at, updated_at"
            ") VALUES ("
            "  :id, :tenant_id, :user_id, :s3_key, :filename, :mime, :size_bytes, 'ready',"
            "  :now, :now"
            ")"
        ),
        {
            "id": str(file_id),
            "tenant_id": str(ctx.tenant_id),
            "user_id": str(ctx.user_id),
            "s3_key": s3_key,
            "filename": filename,
            "mime": mime,
            "size_bytes": len(contenido),
            "now": ahora,
        },
    )
    return file_id


def _detectar_formato(mime: str, filename: str) -> str | None:
    mime_normalizado = (mime or "").split(";")[0].strip().lower()
    nombre = (filename or "").lower()
    if mime_normalizado in _PDF_MIMES or nombre.endswith(".pdf"):
        return "pdf"
    if mime_normalizado in _DOCX_MIMES or nombre.endswith(".docx"):
        return "docx"
    if mime_normalizado in _TXT_MIMES or nombre.endswith((".txt", ".md")):
        return "txt"
    return None


def _extraer_pdf(contenido: bytes) -> str:
    from pypdf import PdfReader  # import perezoso: solo lo necesita este formato

    lector = PdfReader(io.BytesIO(contenido))
    return "\n".join(pagina.extract_text() or "" for pagina in lector.pages)


def _extraer_docx(contenido: bytes) -> str:
    import docx  # import perezoso: solo lo necesita este formato

    documento = docx.Document(io.BytesIO(contenido))
    return "\n".join(parrafo.text for parrafo in documento.paragraphs)


def extraer_texto(archivo: ArchivoDescargado) -> str:
    """Extrae texto plano de `archivo` según su mime/extensión (PDF/DOCX/TXT/MD),
    capado a `MAX_CHARS`. Lanza `FormatoNoSoportado` si no reconoce el
    formato (el caller la atrapa y arma un `ToolResult` de error de negocio)."""
    formato = _detectar_formato(archivo.mime, archivo.filename)
    if formato is None:
        raise FormatoNoSoportado(
            f"'{archivo.filename}' no es un formato soportado (usa PDF, DOCX, TXT o MD)."
        )
    if formato == "pdf":
        texto = _extraer_pdf(archivo.contenido)
    elif formato == "docx":
        texto = _extraer_docx(archivo.contenido)
    else:
        texto = archivo.contenido.decode("utf-8", errors="replace")
    return texto[:MAX_CHARS]


async def extraer_texto_de_file_id(ctx: ToolContext, file_id: uuid.UUID) -> TextoExtraido | None:
    """Descarga `file_id` y le extrae el texto (ver `extraer_texto`).

    `None` si el archivo no existe; propaga `FormatoNoSoportado` si el
    formato no se reconoce — a diferencia de "no existe", eso sí es un caso
    que el caller normalmente distingue con un mensaje distinto.
    """
    archivo = await descargar_archivo(ctx, file_id)
    if archivo is None:
        return None
    return TextoExtraido(texto=extraer_texto(archivo), archivo=archivo)
