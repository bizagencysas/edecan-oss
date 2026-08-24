"""`POST|GET /v1/files` (ARCHITECTURE.md §10.12, §10.14).

Sube el archivo a `s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{filename}`,
inserta la fila en `files` y encola el job `ingest_file` (`edecan_core.queue.enqueue`).
Valida `limits.storage_mb` del plan antes de aceptar el archivo.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import aioboto3
from edecan_core.queue import enqueue
from edecan_schemas import UNLIMITED
from edecan_schemas.plans import LIMIT_STORAGE_MB
from fastapi import APIRouter, Depends, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, TenantCtx, get_current_user, get_repo, rate_limit
from edecan_api.repo import Repo

# Tokens firmados para URLs públicas temporales (IG/FB/Threads necesitan URLs
# públicas para descargar imagen/video). El token es HMAC-SHA256 del file_id
# + expires, firmado con JWT_SECRET. Válido 24 horas.
_PUBLIC_FILE_TTL_SECONDS = 24 * 60 * 60

router = APIRouter(prefix="/v1/files", tags=["files"], dependencies=[Depends(rate_limit)])

# Router público SIN auth: solo para el endpoint de archivos públicos firmados
# (IG/FB/Threads necesitan URLs públicas para descargar imagen/video). La
# seguridad la da el token HMAC firmado con JWT_SECRET, no la auth del tenant.
public_router = APIRouter(prefix="/v1/files", tags=["files"])

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_UPLOAD_SIZE_PROBE_BYTES = 64 * 1024

# Una semana; el contenido bajo un `file_id` es inmutable (ver `download_file`).
_CACHE_CONTROL_INMUTABLE = "private, max-age=604800, immutable"

# Miniaturas: formatos que ImageIO (iOS) y cualquier navegador decodifican sin
# ayuda. `image/svg+xml` queda FUERA a propósito — es contenido activo, mismo
# criterio que `_INLINE_MEDIA_MIMES`.
_THUMBNAIL_SOURCE_MIMES = frozenset(
    {
        "image/avif",
        "image/gif",
        "image/heic",
        "image/heif",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "image/webp",
    }
)
_THUMBNAIL_DEFAULT_MAX_PX = 1280
_THUMBNAIL_MIN_MAX_PX = 64
_THUMBNAIL_LIMIT_MAX_PX = 2048
# Tope de lectura en memoria para generar la miniatura. Por encima de esto no
# se intenta reducir: se devuelve el original (reparar, no descartar).
_THUMBNAIL_SOURCE_MAX_BYTES = 32 * 1024 * 1024
_INLINE_MEDIA_MIMES = frozenset(
    {
        "image/avif",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "audio/flac",
        "audio/m4a",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "video/mp4",
        "video/quicktime",
        "video/webm",
    }
)


def _safe_filename(raw_filename: str | None) -> str:
    """Devuelve un nombre de objeto portable, sin rutas ni caracteres de control."""
    candidate = unicodedata.normalize("NFKC", (raw_filename or "archivo").replace("\\", "/"))
    candidate = PurePosixPath(candidate).name
    candidate = "".join(char for char in candidate if ord(char) >= 32 and ord(char) != 127)
    candidate = candidate.strip().strip(".")
    if not candidate:
        return "archivo"

    # S3 admite keys mucho mayores, pero 255 bytes mantiene interoperabilidad
    # con filesystems y herramientas que materializan el objeto localmente.
    encoded = candidate.encode("utf-8")
    if len(encoded) <= 255:
        return candidate
    return encoded[:255].decode("utf-8", errors="ignore").rstrip(".") or "archivo"


async def _upload_size(file: UploadFile, *, max_bytes: int) -> int:
    """Mide sin cargar el archivo completo y aplica un límite duro fail-closed."""
    if max_bytes <= 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La carga de archivos está deshabilitada por configuración.",
        )

    if file.size is not None:
        size_bytes = file.size
    else:
        size_bytes = 0
        while chunk := await file.read(_UPLOAD_SIZE_PROBE_BYTES):
            size_bytes += len(chunk)
            if size_bytes > max_bytes:
                break

    await file.seek(0)
    if size_bytes > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"El archivo supera el máximo permitido de {max_bytes} bytes.",
        )
    return size_bytes


def _file_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "filename": row.get("filename"),
        "mime": row.get("mime"),
        "size_bytes": row.get("size_bytes"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
    }


async def _check_storage_quota(repo: Repo, tenant: TenantCtx, incoming_bytes: int) -> None:
    # Default `0` (fail-closed), NUNCA `UNLIMITED` (WP-V7-08, barrido v7):
    # `edecan_api.deps.flags_for_plan` devuelve `{}` para un `plan_key` huérfano
    # (catálogo de planes desactualizado, ver su docstring) -- con el default
    # anterior (`UNLIMITED`) ese caso quedaba con almacenamiento SIN NINGÚN
    # límite en vez de sin cupo, justo el fail-open que este router no tiene
    # ningún gate booleano previo (a diferencia de `voice.py`/`missions.py`, que
    # sí tienen un flag booleano fail-closed antes) para evitar. `0` es seguro
    # para los 4 planes reales: `LIMIT_STORAGE_MB` SIEMPRE viene explícito en
    # `edecan_schemas.plans.PLANES` (nunca ausente salvo plan huérfano), así que
    # este default nunca se alcanza en operación normal -- mismo criterio ya
    # aplicado en `missions.py::_check_missions_quota`.
    limit_mb = tenant.flags.get(LIMIT_STORAGE_MB, 0)
    if limit_mb == UNLIMITED:
        return
    used_bytes = await repo.sum_usage_since(
        tenant_id=tenant.tenant_id, kind="storage_bytes", since=_EPOCH
    )
    if (used_bytes + incoming_bytes) > limit_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Alcanzaste tu límite de almacenamiento de {limit_mb} MB "
                f"de tu plan '{tenant.plan_key}'."
            ),
        )


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    tenant = current_user.tenant
    size_bytes = await _upload_size(file, max_bytes=settings.MAX_UPLOAD_BYTES)

    await _check_storage_quota(repo, tenant, size_bytes)

    file_id = uuid.uuid4()
    filename = _safe_filename(file.filename)
    supplied_mime = file.content_type or "application/octet-stream"
    mime = (
        supplied_mime
        if len(supplied_mime) <= 255
        and all(ord(char) >= 32 and ord(char) != 127 for char in supplied_mime)
        else "application/octet-stream"
    )
    s3_key = f"tenants/{tenant.tenant_id}/files/{file_id}/{filename}"

    session = aioboto3.Session()
    async with session.client(
        "s3", region_name=settings.AWS_REGION, endpoint_url=settings.AWS_ENDPOINT_URL
    ) as s3:
        # `UploadFile.file` ya es un SpooledTemporaryFile: botocore lo transmite
        # desde memoria o disco sin crear una segunda copia de hasta N MiB.
        await s3.put_object(Bucket=settings.S3_BUCKET, Key=s3_key, Body=file.file, ContentType=mime)

    row = await repo.create_file(
        tenant_id=tenant.tenant_id,
        user_id=current_user.user_id,
        s3_key=s3_key,
        filename=filename,
        mime=mime,
        size_bytes=size_bytes,
        status="uploaded",
        file_id=file_id,
    )
    await repo.add_usage_event(
        tenant_id=tenant.tenant_id, kind="storage_bytes", quantity=float(size_bytes)
    )

    await enqueue(
        settings,
        "ingest_file",
        {"file_id": str(row["id"]), "tenant_id": str(tenant.tenant_id), "s3_key": s3_key},
        tenant.tenant_id,
    )
    return _file_out(row)


@router.get("")
async def list_files(
    current_user: CurrentUser = Depends(get_current_user), repo: Repo = Depends(get_repo)
) -> list[dict[str, Any]]:
    rows = await repo.list_files(tenant_id=current_user.tenant_id)
    return [_file_out(r) for r in rows]


@router.get("/{file_id}")
async def get_file(
    file_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    row = await repo.get_file(tenant_id=current_user.tenant_id, file_id=file_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Archivo no encontrado.")
    return _file_out(row)


async def _stream_s3_object(
    settings: Settings, s3_key: str, *, byte_range: str | None = None
) -> Any:
    """Transmite el objeto manteniendo vivo el cliente S3 hasta el último chunk."""

    session = aioboto3.Session()
    async with session.client(
        "s3", region_name=settings.AWS_REGION, endpoint_url=settings.AWS_ENDPOINT_URL
    ) as s3:
        request = {"Bucket": settings.S3_BUCKET, "Key": s3_key}
        if byte_range is not None:
            request["Range"] = byte_range
        response = await s3.get_object(**request)
        body = response["Body"]
        while chunk := await body.read(64 * 1024):
            yield chunk


@router.get("/{file_id}/download")
async def download_file(
    file_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Descarga autenticada y aislada por tenant de un archivo privado.

    No expone ``s3_key`` ni una URL pública/presignada. La API conserva el
    control de acceso durante toda la transferencia, lo que funciona igual
    con AWS S3, LocalStack y almacenamiento compatible self-hosted.
    """

    row = await repo.get_file(tenant_id=current_user.tenant_id, file_id=file_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Archivo no encontrado.")

    filename = _safe_filename(row.get("filename"))
    mime = str(row.get("mime") or "application/octet-stream")
    s3_key = str(row.get("s3_key") or "")
    if not s3_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El archivo no tiene contenido descargable asociado.",
        )

    # Validación previa: evita responder 200 y fallar recién después de enviar
    # headers si el objeto fue eliminado o el storage está indisponible.
    session = aioboto3.Session()
    try:
        async with session.client(
            "s3", region_name=settings.AWS_REGION, endpoint_url=settings.AWS_ENDPOINT_URL
        ) as s3:
            object_metadata = await s3.head_object(Bucket=settings.S3_BUCKET, Key=s3_key)
    except Exception as exc:  # noqa: BLE001 - proveedores S3 lanzan clases distintas
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El archivo existe en Edecan, pero el almacenamiento no pudo entregarlo.",
        ) from exc

    content_length = object_metadata.get("ContentLength")
    disposition = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
    response_headers = {
        "Content-Disposition": disposition,
        "X-Content-Type-Options": "nosniff",
        # `private` (nunca `public`): la respuesta es del tenant y no puede
        # quedar en una caché compartida ni en Cloudflare. `immutable` es
        # correcto porque el contenido bajo un `file_id` NO se reescribe: cada
        # productor de archivos crea su propio `uuid.uuid4()` antes del
        # `put_object` (`files.upload_file`, `edecan_creative._files`,
        # `edecan_business._files`, `edecan_voice.tools`,
        # `edecan_advisory._texto`, `edecan_docanalysis._s3`,
        # `generate_podcast`, `process_meeting`, `voz_avanzada`) y el único
        # `UPDATE files` del repo toca `status`, jamás `s3_key`.
        # `no-store` obligaba al teléfono a rebajar los MISMOS megas en cada
        # apertura del chat: con veinte imágenes en la conversación eran
        # decenas de MB por el túnel cada vez.
        "Cache-Control": _CACHE_CONTROL_INMUTABLE,
    }
    if isinstance(content_length, int) and content_length >= 0:
        response_headers["Content-Length"] = str(content_length)
    return StreamingResponse(
        _stream_s3_object(settings, s3_key),
        media_type=mime,
        headers=response_headers,
    )


async def _leer_objeto_s3(settings: Settings, s3_key: str, *, max_bytes: int) -> bytes | None:
    """Bytes completos del objeto, o `None` si supera `max_bytes`."""
    trozos: list[bytes] = []
    total = 0
    async for trozo in _stream_s3_object(settings, s3_key):
        total += len(trozo)
        if total > max_bytes:
            return None
        trozos.append(trozo)
    return b"".join(trozos)


def _generar_miniatura(data: bytes, max_px: int) -> tuple[bytes, str] | None:
    """Reduce la imagen a `max_px` por su lado mayor. `None` si no se puede.

    Import perezoso de Pillow: llega como dependencia transitiva de
    `edecan-creative`, pero un self-host con un checkout parcial no debe
    quedarse sin `/download` por no poder generar una miniatura — quien llama
    cae al original.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None

    import io

    try:
        with Image.open(io.BytesIO(data)) as imagen:
            imagen = ImageOps.exif_transpose(imagen) or imagen
            if imagen.width <= max_px and imagen.height <= max_px:
                # Ya es chica: reducirla no ahorra nada y recomprimir solo
                # perdería calidad.
                return None
            tiene_alfa = imagen.mode in {"RGBA", "LA", "PA"} or "transparency" in imagen.info
            imagen.thumbnail((max_px, max_px), Image.LANCZOS)
            salida = io.BytesIO()
            if tiene_alfa:
                imagen.convert("RGBA").save(salida, format="PNG", optimize=True)
                return salida.getvalue(), "image/png"
            imagen.convert("RGB").save(
                salida, format="JPEG", quality=82, optimize=True, progressive=True
            )
            return salida.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 - Pillow lanza clases distintas por formato
        return None


@router.get("/{file_id}/thumbnail")
async def thumbnail_file(
    file_id: uuid.UUID,
    max_px: int = Query(
        _THUMBNAIL_DEFAULT_MAX_PX,
        alias="max",
        ge=_THUMBNAIL_MIN_MAX_PX,
        le=_THUMBNAIL_LIMIT_MAX_PX,
    ),
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Versión reducida y CACHEABLE de una imagen ya subida.

    POR QUÉ existe: la card del chat pedía el original por `/download` —un PNG
    de 2160x2160, entre 3 y 5 MB— solo para dibujarlo a 1280 px en el
    teléfono, y con veinte imágenes en una conversación eso son decenas de MB
    por el túnel en cada apertura. El cuello no es el object store (sirve esos
    objetos en milisegundos), es el enlace de subida de la máquina; a los pocos
    segundos el cliente abandona los streams y la imagen "no llega". Reducirla
    aquí es lo que de verdad quita la causa.

    Mismas garantías que `download_file`: autenticada, aislada por tenant, sin
    URL pública ni presignada. Es el punto donde NO se debe portar literalmente
    el `/li-img/` de REFERENCIA (`app.py`), que es público y exento de auth: REFERENCIA
    es de un solo usuario y Edecán es multi-tenant con RLS.

    Repara en vez de descartar: si el archivo no es una imagen reducible, si
    Pillow no está, si el objeto es demasiado grande o si la decodificación
    falla, devuelve el ORIGINAL con los mismos headers cacheables en lugar de
    un error — quien llama nunca se queda sin imagen por culpa de la
    optimización.
    """
    row = await repo.get_file(tenant_id=current_user.tenant_id, file_id=file_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Archivo no encontrado.")

    mime = str(row.get("mime") or "application/octet-stream")
    s3_key = str(row.get("s3_key") or "")
    if not s3_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El archivo no tiene contenido descargable asociado.",
        )
    if mime.split(";", 1)[0].strip().lower() not in _THUMBNAIL_SOURCE_MIMES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Solo se pueden generar miniaturas de imágenes.",
        )

    try:
        original = await _leer_objeto_s3(settings, s3_key, max_bytes=_THUMBNAIL_SOURCE_MAX_BYTES)
    except Exception as exc:  # noqa: BLE001 - proveedores S3 lanzan clases distintas
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El archivo existe en Edecan, pero el almacenamiento no pudo entregarlo.",
        ) from exc

    cuerpo = original
    tipo = mime
    if original is not None:
        # Pillow es CPU puro: en el hilo del event loop bloquearía todas las
        # demás peticiones mientras reduce un PNG de 4,6 megapíxeles.
        reducida = await run_in_threadpool(_generar_miniatura, original, max_px)
        if reducida is not None:
            cuerpo, tipo = reducida

    if cuerpo is None:
        # Objeto por encima del tope de memoria: se transmite el original.
        return StreamingResponse(
            _stream_s3_object(settings, s3_key),
            media_type=mime,
            headers={
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": _CACHE_CONTROL_INMUTABLE,
            },
        )

    return Response(
        content=cuerpo,
        media_type=tipo,
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": _CACHE_CONTROL_INMUTABLE,
        },
    )


def _parse_single_range(value: str, total: int) -> tuple[int, int]:
    """Parsea un único byte range RFC 7233; rechaza rangos múltiples/ambiguos."""

    raw = value.strip()
    if total <= 0 or not raw.lower().startswith("bytes=") or "," in raw:
        raise ValueError("range inválido")
    spec = raw[6:].strip()
    if "-" not in spec:
        raise ValueError("range inválido")
    start_raw, end_raw = (part.strip() for part in spec.split("-", 1))
    if not start_raw:
        suffix = int(end_raw)
        if suffix <= 0:
            raise ValueError("range inválido")
        start = max(total - suffix, 0)
        return start, total - 1
    start = int(start_raw)
    if start < 0 or start >= total:
        raise ValueError("range fuera del archivo")
    end = total - 1 if not end_raw else int(end_raw)
    if end < start:
        raise ValueError("range inválido")
    return start, min(end, total - 1)


@router.get("/{file_id}/content")
async def stream_file_content(
    file_id: uuid.UUID,
    range_header: str | None = Header(default=None, alias="Range"),
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Sirve media privada inline y soporta ``Range`` para audio/video.

    Solo una allowlist de formatos pasivos puede mostrarse inline. SVG, HTML y
    cualquier tipo desconocido conservan la ruta de descarga como attachment.
    """

    row = await repo.get_file(tenant_id=current_user.tenant_id, file_id=file_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Archivo no encontrado.")

    mime = str(row.get("mime") or "application/octet-stream").split(";", 1)[0].lower().strip()
    if mime not in _INLINE_MEDIA_MIMES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Este tipo de archivo no admite vista previa inline segura.",
        )
    s3_key = str(row.get("s3_key") or "")
    if not s3_key:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Archivo sin contenido.")

    session = aioboto3.Session()
    try:
        async with session.client(
            "s3", region_name=settings.AWS_REGION, endpoint_url=settings.AWS_ENDPOINT_URL
        ) as s3:
            metadata = await s3.head_object(Bucket=settings.S3_BUCKET, Key=s3_key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El almacenamiento no pudo entregar la vista previa.",
        ) from exc

    total = int(metadata.get("ContentLength") or 0)
    start, end = 0, max(total - 1, 0)
    response_status = status.HTTP_200_OK
    s3_range: str | None = None
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "Content-Disposition": "inline",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
    }
    if range_header is not None:
        try:
            start, end = _parse_single_range(range_header, total)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                detail="Rango de bytes inválido.",
                headers={"Content-Range": f"bytes */{total}"},
            ) from None
        response_status = status.HTTP_206_PARTIAL_CONTENT
        s3_range = f"bytes={start}-{end}"
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    headers["Content-Length"] = str(max(end - start + 1, 0))

    return StreamingResponse(
        _stream_s3_object(settings, s3_key, byte_range=s3_range),
        status_code=response_status,
        media_type=mime,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Endpoint público de archivos (para IG/FB/Threads)
# ---------------------------------------------------------------------------


def make_public_file_url(
    *,
    public_base_url: str,
    file_id: str,
    jwt_secret: str,
    ttl_seconds: int = _PUBLIC_FILE_TTL_SECONDS,
) -> str:
    """Genera una URL pública firmada y temporal para un archivo.

    Instagram, Facebook y Threads exigen URLs PÚBLICAS para descargar
    imagen/video. Esta función crea una URL que el backend sirve SIN auth,
    protegida por un token HMAC firmado con ``JWT_SECRET`` y con expiración.

    La URL tiene la forma:
    ``{public_base_url}/v1/files/public/{file_id}?expires={ts}&sig={hex}``
    """
    expires = int(time.time()) + ttl_seconds
    payload = f"{file_id}:{expires}"
    sig = hmac.new(jwt_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    base = public_base_url.rstrip("/")
    return f"{base}/v1/files/public/{file_id}?expires={expires}&sig={sig}"


@public_router.get("/public/{file_id}")
async def download_public_file(
    file_id: uuid.UUID,
    expires: int = Query(..., description="Timestamp de expiración del token"),
    sig: str = Query(..., description="Firma HMAC-SHA256 del token"),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Sirve un archivo SIN auth, protegido por un token HMAC firmado.

    Es el endpoint que usan Instagram, Facebook y Threads para descargar
    imagen/video de Acme cuando se publica via multi-red. El token se
    genera con ``make_public_file_url`` y es válido por 24 horas.

    No verifica ``tenant_id``: el ``file_id`` es un UUID opaco y el token
    HMAC firma exactamente ese ``file_id`` + ``expires``, así que solo el
    backend puede generar URLs válidas. El archivo se busca por ``file_id``
    en cualquier tenant (los file_ids son UUIDs globalmente únicos).

    No usa ``get_repo`` (que requiere auth via ``get_tenant_session``):
    usa ``get_session(None)`` (sesión de plataforma, sin RLS) para poder
    buscar el archivo por ID sin aislar por tenant.
    """
    from edecan_db.session import get_session
    from sqlalchemy import text as sql_text

    # Verificar expiración
    if expires < int(time.time()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El enlace ha expirado.")

    # Verificar firma HMAC
    payload = f"{file_id}:{expires}"
    expected_sig = hmac.new(
        settings.JWT_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Firma inválida.")

    # Buscar el archivo por file_id sin RLS (sesión de plataforma)
    async with get_session(None) as session:
        result = await session.execute(
            sql_text("SELECT * FROM files WHERE id = :id"),
            {"id": file_id},
        )
        row = result.mappings().first()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Archivo no encontrado.")

    mime = str(row.get("mime") or "application/octet-stream")
    s3_key = str(row.get("s3_key") or "")
    if not s3_key:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Archivo sin contenido.")

    # Intentar rutas locales primero (modo desktop), luego S3
    import os
    from pathlib import Path

    content: bytes | None = None
    data_dir = getattr(settings, "DATA_DIR", None) or os.getenv("EDECAN_DATA_DIR")
    possible_local_paths: list[Path] = []
    if data_dir:
        possible_local_paths.append(Path(data_dir) / "objects" / "edecan-files" / s3_key)
        possible_local_paths.append(Path(data_dir) / s3_key)
    possible_local_paths.append(
        Path.home()
        / "Library"
        / "Application Support"
        / "cc.edecan.desktop"
        / "data"
        / "objects"
        / "edecan-files"
        / s3_key
    )
    possible_local_paths.append(
        Path.home() / ".edecan" / "data" / "objects" / "edecan-files" / s3_key
    )
    for p in possible_local_paths:
        if p.is_file():
            try:
                content = p.read_bytes()
                break
            except Exception:
                pass

    if content is not None:
        return Response(
            content=content, media_type=mime, headers={"Cache-Control": "private, no-store"}
        )

    # S3
    session_s3 = aioboto3.Session()
    try:
        async with session_s3.client(
            "s3", region_name=settings.AWS_REGION, endpoint_url=settings.AWS_ENDPOINT_URL
        ) as s3:
            response = await s3.get_object(Bucket=settings.S3_BUCKET, Key=s3_key)
            content = await response["Body"].read()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El almacenamiento no pudo entregar el archivo.",
        ) from exc

    return Response(
        content=content, media_type=mime, headers={"Cache-Control": "private, no-store"}
    )
