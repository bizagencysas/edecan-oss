"""Job `ingest_file`: descarga el archivo de S3, extrae texto (o, si es una
imagen, la describe por visión), lo trocea, calcula embeddings por lotes y
guarda `file_chunks` (ARCHITECTURE.md §10.3, §10.7, §10.11).

Payload: `{"file_id": "<uuid>"}`. Requiere `env.tenant_id`.

Extracción de texto según `mime`/extensión: `pdf` (`pypdf`), `docx`
(`python-docx`), `txt`/`md` (decodificado directo). Cualquier otro tipo NO
soportado marca `files.status = "error"` y termina el job SIN lanzar
excepción (es un resultado de negocio válido, no un fallo transitorio que
deba reintentarse) — salvo el caso especial de imágenes, ver abajo.

**Imágenes** (`png`/`jpeg`/`webp`/`gif`, ver `_resolver_mime_imagen`): si hay
un proveedor LLM con soporte de visión configurado (Anthropic — mismo
criterio de detección que `edecan_docanalysis.vision.AnalizarImagenTool`,
`provider.name == "anthropic"`, no se reimplementa aquí importando ese
paquete hermano a propósito, ARCHITECTURE.md §10.1), se le pide una
descripción breve (alias `"rapido"`: es un job automático que corre en CADA
imagen subida, así que se prioriza costo/latencia sobre la profundidad que sí
tiene la tool interactiva `analizar_imagen`) y se persiste como un único
`file_chunk` `seq=0` — así `consultar_documentos`
(`edecan_toolkit.documentos`) la encuentra por texto/similitud, y la imagen
queda con `status='ready'`. Si NO hay proveedor de visión configurado (o la
imagen supera `_MAX_IMAGEN_BYTES`), el comportamiento es IDÉNTICO al de
cualquier mime no soportado: `status='error'`, sin chunks — exactamente lo
que ya pasaba con imágenes ANTES de esta extensión, porque `_extract_text`
tampoco las reconocía.
"""

from __future__ import annotations

import base64
import io
import logging
import uuid
from typing import Any

from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_schemas import JobEnvelope

from edecan_worker.deps import Deps
from edecan_worker.repo import Repo, SqlRepo

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
EMBEDDING_BATCH_SIZE = 32

_TEXT_MIMES = {"text/plain", "text/markdown", "text/x-markdown"}
_PDF_MIMES = {"application/pdf"}
_DOCX_MIMES = {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}

_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_IMAGE_EXTENSION_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_MAX_IMAGEN_BYTES = 5 * 1024 * 1024  # 5 MB, mismo límite que analizar_imagen
_VISION_MAX_TOKENS = 200
_VISION_SYSTEM_PROMPT = (
    "Eres un asistente que describe imágenes de forma breve y precisa, para "
    "que ese texto sirva de índice de búsqueda. Responde en 1-2 frases, en "
    "español, sin inventar contenido que no esté en la imagen. Si la imagen "
    "tiene texto visible, inclúyelo (OCR) en la descripción."
)


def chunk_text(raw_text: str, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Trocea `raw_text` en fragmentos de hasta `size` caracteres con `overlap` de solapamiento.

    Avanza en pasos de `size - overlap`; cada fragmento se recorta (`strip`) y
    los fragmentos vacíos se descartan. Devuelve `[]` si `raw_text` está vacío.
    """
    normalized = raw_text.strip()
    if not normalized:
        return []
    step = max(1, size - overlap)
    chunks: list[str] = []
    start = 0
    n = len(normalized)
    while start < n:
        end = min(start + size, n)
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start += step
    return chunks


def _extract_text(data: bytes, *, mime: str, filename: str) -> str | None:
    """Extrae texto plano de `data` según `mime`/extensión. `None` si no está soportado."""
    normalized_mime = (mime or "").split(";")[0].strip().lower()
    lower_name = (filename or "").lower()

    if normalized_mime in _TEXT_MIMES or lower_name.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="replace")

    if normalized_mime in _PDF_MIMES or lower_name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)

    if normalized_mime in _DOCX_MIMES or lower_name.endswith(".docx"):
        import docx

        document = docx.Document(io.BytesIO(data))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)

    return None


def _resolver_mime_imagen(mime: str, filename: str) -> str | None:
    """`mime` normalizado si `data` es una imagen soportada (png/jpeg/webp/gif),
    `None` si no. Igual que `_extract_text`, primero confía en el `mime`
    declarado y cae a la extensión del archivo si es genérico
    (`application/octet-stream`) — mismo criterio que
    `edecan_docanalysis.vision.AnalizarImagenTool._resolver_mime` (paquete
    hermano, reimplementado aquí en vez de importado, ARCHITECTURE.md §10.1)."""
    normalized_mime = (mime or "").split(";")[0].strip().lower()
    if normalized_mime == "image/jpg":
        normalized_mime = "image/jpeg"
    if normalized_mime in _IMAGE_MIMES:
        return normalized_mime

    lower_name = (filename or "").lower()
    for ext, mime_for_ext in _IMAGE_EXTENSION_MIMES.items():
        if lower_name.endswith(ext):
            return mime_for_ext
    return None


async def _read_s3_object(deps: Deps, key: str) -> bytes:
    response = await deps.s3.get_object(Bucket=deps.settings.S3_BUCKET, Key=key)
    return await response["Body"].read()


async def handle(env: JobEnvelope, deps: Deps) -> None:
    if env.tenant_id is None:
        raise ValueError("ingest_file requiere tenant_id")
    file_id = uuid.UUID(str(env.payload["file_id"]))

    async with deps.session_factory(None) as session:
        repo = SqlRepo(session)

        file_row = await repo.get_file(tenant_id=env.tenant_id, file_id=file_id)
        if file_row is None:
            logger.error(
                "ingest_file: archivo no encontrado file_id=%s tenant_id=%s", file_id, env.tenant_id
            )
            return

        raw_bytes = await _read_s3_object(deps, file_row["s3_key"])

        mime_imagen = _resolver_mime_imagen(file_row["mime"], file_row["filename"])
        if mime_imagen is not None:
            # Bring-your-own por tenant (WP-V3-02, ver `Deps.llm_router_for`):
            # resuelto PEREZOSO acá adentro (no arriba, antes de saber si el
            # archivo es imagen) a propósito — solo `_ingest_image` (visión)
            # necesita el LLM, y `llm_router_for` ahora lanza
            # `TenantLLMNotConnectedError` (nunca cae a `deps.llm_router` de
            # plataforma) si el tenant no conectó su propio proveedor.
            # Resolverlo arriba rompería la ingesta de archivos NO-imagen
            # (PDF/texto/docs, que nunca tocan el LLM) para cualquier tenant
            # sin proveedor LLM conectado — un archivo de texto no debe
            # fallar solo porque el tenant no configuró visión.
            llm_router = await deps.llm_router_for(env.tenant_id)
            await _ingest_image(
                deps,
                repo,
                llm_router,
                tenant_id=env.tenant_id,
                file_id=file_id,
                mime=mime_imagen,
                raw_bytes=raw_bytes,
            )
            return

        extracted = _extract_text(raw_bytes, mime=file_row["mime"], filename=file_row["filename"])

        if extracted is None:
            logger.warning(
                "ingest_file: mime no soportado %r para file_id=%s, se marca status=error",
                file_row["mime"],
                file_id,
            )
            await repo.update_file_status(tenant_id=env.tenant_id, file_id=file_id, status="error")
            return

        pieces = chunk_text(extracted)
        seq = 0
        for batch_start in range(0, len(pieces), EMBEDDING_BATCH_SIZE):
            batch = pieces[batch_start : batch_start + EMBEDDING_BATCH_SIZE]
            embeddings = await deps.embedder.embed(batch)
            chunk_rows = [(seq + i, batch[i], embeddings[i]) for i in range(len(batch))]
            await repo.add_file_chunks(tenant_id=env.tenant_id, file_id=file_id, chunks=chunk_rows)
            seq += len(batch)

        await repo.update_file_status(tenant_id=env.tenant_id, file_id=file_id, status="ready")
        # NO registrar aquí un usage_event `storage_bytes`: la API ya lo
        # contabiliza una única vez en `upload_file` (edecan_api/routers/files.py),
        # justo después del `s3.put_object` — que es el momento real en que el
        # tenant consume storage, ocurra o no la extracción de texto. Volver a
        # registrarlo aquí duplicaría `size_bytes` en `sum_usage_since`/`GET
        # /v1/usage` y haría que las cuotas de `limits.storage_mb` se agotaran
        # a la mitad de la capacidad real del tenant.

    logger.info(
        "ingest_file completado file_id=%s tenant_id=%s chunks=%d bytes=%d",
        file_id,
        env.tenant_id,
        len(pieces),
        len(raw_bytes),
    )


async def _ingest_image(
    deps: Deps,
    repo: Repo,
    llm_router: Any,
    *,
    tenant_id: uuid.UUID,
    file_id: uuid.UUID,
    mime: str,
    raw_bytes: bytes,
) -> None:
    """Rama de `handle` para imágenes (ver docstring del módulo).

    `repo` ya vive dentro de la misma sesión/transacción que abrió `handle`
    — esta función solo decide `status='ready'` (con un `file_chunk` seq=0)
    vs. `status='error'`, nunca abre su propia sesión ni hace commit.
    """
    if len(raw_bytes) > _MAX_IMAGEN_BYTES:
        logger.warning(
            "ingest_file: imagen file_id=%s pesa %d bytes (> %d), se marca "
            "status=error sin describir",
            file_id,
            len(raw_bytes),
            _MAX_IMAGEN_BYTES,
        )
        await repo.update_file_status(tenant_id=tenant_id, file_id=file_id, status="error")
        return

    # Alias "rapido" a propósito (no "principal"): este job corre automático
    # en CADA imagen subida, así que se prioriza costo/latencia sobre la
    # profundidad de la tool interactiva `analizar_imagen` — y "rapido" nunca
    # necesita `tenant_flags` para resolverse (`LLMRouter._resolve_model`),
    # así que pasar `{}` es seguro y evita una consulta extra a `tenants`
    # solo para leer el plan.
    provider, model = llm_router.resolve("rapido", {})
    if getattr(provider, "name", "") != "anthropic":
        logger.warning(
            "ingest_file: imagen file_id=%s sin proveedor de visión (Anthropic) "
            "configurado, se marca status=error — mismo resultado que antes de "
            "esta extensión (la imagen tampoco la reconocía _extract_text)",
            file_id,
        )
        await repo.update_file_status(tenant_id=tenant_id, file_id=file_id, status="error")
        return

    b64 = base64.b64encode(raw_bytes).decode("ascii")
    request = CompletionRequest(
        model=model,
        system=_VISION_SYSTEM_PROMPT,
        messages=[
            ChatMessage(
                role="user",
                content=[
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": b64},
                    },
                    {"type": "text", "text": "Describe brevemente esta imagen."},
                ],
            )
        ],
        max_tokens=_VISION_MAX_TOKENS,
    )
    response = await provider.complete(request)
    description = response.text.strip() or "Imagen sin descripción disponible."

    embeddings = await deps.embedder.embed([description])
    vector = embeddings[0] if embeddings else []
    await repo.add_file_chunks(
        tenant_id=tenant_id, file_id=file_id, chunks=[(0, description, vector)]
    )
    await repo.update_file_status(tenant_id=tenant_id, file_id=file_id, status="ready")

    logger.info(
        "ingest_file (imagen) completado file_id=%s tenant_id=%s modelo=%s bytes=%d",
        file_id,
        tenant_id,
        model,
        len(raw_bytes),
    )
