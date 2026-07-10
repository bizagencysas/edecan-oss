"""Mini servidor S3-compatible respaldado en filesystem — reemplaza a
LocalStack/S3 real para el runner de escritorio de un solo usuario
(`ARCHITECTURE.md` §12f, dueño WP-V3-05). `edecan_local.runtime` lo sirve
como una app ASGI SEPARADA (uvicorn propio, en `127.0.0.1:{port+2}`, mismo
proceso) y apunta `AWS_ENDPOINT_URL` ahí antes de importar `edecan_api`.

## Por qué alcanza con esto (y qué subconjunto implementa)

Cada call site de `aioboto3` en este repo (`apps/api/edecan_api/routers/
files.py`, `apps/worker/edecan_worker/deps.py`+`handlers/ingest_file.py`,
`packages/{creative,business,docanalysis,advisory}`) SOLO usa
`put_object`/`get_object` con `Body=<bytes>` — nunca `create_bucket` antes de
subir. Este servidor implementa el subconjunto REST de S3 que esos clientes
disparan: `PUT`/`GET`/`HEAD`/`DELETE` de objeto, `PUT` de bucket (200, por si
algo sí la llama), y `GET ?list-type=2&prefix=` (ListObjectsV2) — con
`aiobotocore` apuntando a un `endpoint_url` explícito (no-AWS), la dirección
es SIEMPRE "path-style" (`/{bucket}/{key}`, ver `botocore.utils.
S3EndpointSetter._s3_addressing_handler`: con `endpoint_url` fijado nunca usa
virtual-hosted-style), así que las rutas de abajo son las únicas que hace
falta cubrir.

`PUT` de objeto auto-crea el directorio del bucket si no existe (los
call-sites de arriba nunca llaman `create_bucket`) — igual que casi
cualquier mock de S3 (moto, LocalStack) hace por conveniencia.

## Firmas AWS: completamente ignoradas

Este servidor NUNCA valida `Authorization`/`X-Amz-*` — solo escucha en
loopback (`127.0.0.1`, nunca `0.0.0.0`, mismo principio que el resto del
runner, ARCHITECTURE.md §12f) y el propio proceso es el único cliente
posible. `edecan_local.runtime` fija `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`
a un valor cualquiera (`"local"`) solo para que `aiobotocore` tenga *algo*
con qué firmar — la firma resultante nunca se revisa acá.

Sobre el body de un `PUT`: como el endpoint es `http://` (no `https://`) y
todos los call-sites mandan `Body=bytes` (nunca un stream sin longitud
conocida), `botocore` NUNCA envuelve el payload en `aws-chunked`/trailer de
checksum (esa envoltura solo se activa sobre HTTPS,
`botocore.httpchecksum.resolve_request_checksum_algorithm`) — el cuerpo que
llega acá es SIEMPRE el archivo tal cual, sin envoltura que desarmar.

## Metadata (`Content-Type`) — sidecar `.meta.json`

En vez de xattrs de filesystem (no son portables entre SO/volúmenes montados
— justo lo que un dev tool local no se puede permitir asumir), cada objeto
`bucket/key` guarda su `Content-Type` en un archivo sidecar
`bucket/key.meta.json` junto al propio archivo. `_list_objects_v2` excluye
esos sidecars de los resultados (no son keys reales de S3).

## Path traversal

`bucket`/`key` llegan de la URL (potencialmente hostiles: nada valida que un
`key` no contenga `../../etc/passwd`) — `_resolve_object_path` normaliza con
`Path.resolve()` y rechaza cualquier resultado que caiga FUERA del
directorio del bucket dentro de `objects_root` antes de tocar el filesystem.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from xml.sax.saxutils import escape

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

logger = logging.getLogger(__name__)

_META_SUFFIX = ".meta.json"
_DEFAULT_CONTENT_TYPE = "application/octet-stream"
_XML_CONTENT_TYPE = "application/xml"


class PathTraversalError(ValueError):
    """`bucket`/`key` intentaron escapar de `objects_root` (ver docstring del módulo)."""


# ---------------------------------------------------------------------------
# Filesystem: resolución segura de rutas + sidecar de metadata
# ---------------------------------------------------------------------------


def _resolve_bucket_dir(objects_root: Path, bucket: str) -> Path:
    root = objects_root.resolve()
    bucket_dir = (root / bucket).resolve()
    if bucket_dir != root and root not in bucket_dir.parents:
        raise PathTraversalError(f"bucket inválido: {bucket!r}")
    return bucket_dir


def _resolve_object_path(objects_root: Path, bucket: str, key: str) -> Path:
    bucket_dir = _resolve_bucket_dir(objects_root, bucket)
    candidate = (bucket_dir / key).resolve()
    if candidate != bucket_dir and bucket_dir not in candidate.parents:
        raise PathTraversalError(f"key con path traversal rechazada: {key!r}")
    return candidate


def _meta_path(object_path: Path) -> Path:
    return object_path.with_name(object_path.name + _META_SUFFIX)


def _write_meta(object_path: Path, *, content_type: str) -> None:
    _meta_path(object_path).write_text(json.dumps({"content_type": content_type}), encoding="utf-8")


def _read_content_type(object_path: Path) -> str:
    meta_path = _meta_path(object_path)
    if meta_path.is_file():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            content_type = data.get("content_type")
            if isinstance(content_type, str) and content_type:
                return content_type
        except (OSError, ValueError):
            logger.warning("Sidecar de metadata ilegible: %s", meta_path, exc_info=True)
    return _DEFAULT_CONTENT_TYPE


# ---------------------------------------------------------------------------
# Respuestas de error — XML REST-S3 mínimo (botocore solo necesita
# <Error><Code>/<Message> para levantar el ClientError correcto).
# ---------------------------------------------------------------------------


def _error_xml(status_code: int, code: str, message: str, *, key: str | None = None) -> Response:
    key_elem = f"<Key>{escape(key)}</Key>" if key is not None else ""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Error><Code>{escape(code)}</Code><Message>{escape(message)}</Message>"
        f"{key_elem}<RequestId>edecan-local</RequestId></Error>"
    )
    return Response(content=body, status_code=status_code, media_type=_XML_CONTENT_TYPE)


def _traversal_response(exc: PathTraversalError) -> Response:
    return _error_xml(400, "InvalidArgument", str(exc))


# ---------------------------------------------------------------------------
# Bucket: PUT (crear) / GET (ListObjectsV2)
# ---------------------------------------------------------------------------


def _list_objects_v2(objects_root: Path, bucket: str, prefix: str) -> Response:
    try:
        bucket_dir = _resolve_bucket_dir(objects_root, bucket)
    except PathTraversalError as exc:
        return _traversal_response(exc)

    entries: list[str] = []
    if bucket_dir.is_dir():
        for path in sorted(bucket_dir.rglob("*")):
            if path.is_dir() or path.name.endswith(_META_SUFFIX):
                continue
            key = path.relative_to(bucket_dir).as_posix()
            if prefix and not key.startswith(prefix):
                continue
            stat = path.stat()
            entries.append(
                "<Contents>"
                f"<Key>{escape(key)}</Key>"
                f"<Size>{stat.st_size}</Size>"
                f"<LastModified>{_iso(stat.st_mtime)}</LastModified>"
                "<StorageClass>STANDARD</StorageClass>"
                "</Contents>"
            )

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Name>{escape(bucket)}</Name>"
        f"<Prefix>{escape(prefix)}</Prefix>"
        f"<KeyCount>{len(entries)}</KeyCount>"
        "<MaxKeys>1000</MaxKeys>"
        "<IsTruncated>false</IsTruncated>" + "".join(entries) + "</ListBucketResult>"
    )
    return Response(content=body, status_code=200, media_type=_XML_CONTENT_TYPE)


def _iso(mtime: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


async def _bucket_endpoint(request: Request) -> Response:
    objects_root: Path = request.app.state.objects_root
    bucket = request.path_params["bucket"]

    if request.method == "PUT":
        try:
            bucket_dir = _resolve_bucket_dir(objects_root, bucket)
        except PathTraversalError as exc:
            return _traversal_response(exc)
        bucket_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Bucket creado (o ya existía): %s", bucket)
        return Response(status_code=200)

    prefix = request.query_params.get("prefix", "")
    return _list_objects_v2(objects_root, bucket, prefix)


# ---------------------------------------------------------------------------
# Objeto: PUT / GET / HEAD / DELETE
# ---------------------------------------------------------------------------


async def _put_object(request: Request, objects_root: Path, bucket: str, key: str) -> Response:
    try:
        object_path = _resolve_object_path(objects_root, bucket, key)
    except PathTraversalError as exc:
        return _traversal_response(exc)

    body = await request.body()
    content_type = request.headers.get("content-type") or _DEFAULT_CONTENT_TYPE

    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(body)
    _write_meta(object_path, content_type=content_type)

    etag = f'"{_fake_etag(body)}"'
    logger.info("PUT objeto bucket=%s key=%s bytes=%d", bucket, key, len(body))
    return Response(status_code=200, headers={"ETag": etag})


def _fake_etag(body: bytes) -> str:
    import hashlib

    return hashlib.md5(body, usedforsecurity=False).hexdigest()  # noqa: S324 - no es criptográfico, es un ETag local


async def _get_or_head_object(
    objects_root: Path, bucket: str, key: str, *, include_body: bool
) -> Response:
    try:
        object_path = _resolve_object_path(objects_root, bucket, key)
    except PathTraversalError as exc:
        return _traversal_response(exc)

    if not object_path.is_file():
        return _error_xml(404, "NoSuchKey", "The specified key does not exist.", key=key)

    content_type = _read_content_type(object_path)
    if not include_body:
        size = object_path.stat().st_size
        return Response(
            status_code=200,
            media_type=content_type,
            headers={"Content-Length": str(size)},
        )

    body = object_path.read_bytes()
    return Response(content=body, status_code=200, media_type=content_type)


async def _delete_object(objects_root: Path, bucket: str, key: str) -> Response:
    try:
        object_path = _resolve_object_path(objects_root, bucket, key)
    except PathTraversalError as exc:
        return _traversal_response(exc)

    object_path.unlink(missing_ok=True)
    _meta_path(object_path).unlink(missing_ok=True)
    # DeleteObject es idempotente en S3 real: 204 exista o no la key.
    return Response(status_code=204)


async def _object_endpoint(request: Request) -> Response:
    objects_root: Path = request.app.state.objects_root
    bucket = request.path_params["bucket"]
    key = request.path_params["key"]
    method = request.method

    if method == "PUT":
        return await _put_object(request, objects_root, bucket, key)
    if method in ("GET", "HEAD"):
        return await _get_or_head_object(objects_root, bucket, key, include_body=method == "GET")
    if method == "DELETE":
        return await _delete_object(objects_root, bucket, key)
    return Response(status_code=405)  # pragma: no cover - Starlette ya filtra `methods=`


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_object_store_app(objects_root: Path) -> Starlette:
    """App ASGI standalone (Starlette, no FastAPI: no necesita OpenAPI ni
    dependency injection, ver docstring del módulo). `objects_root` es el
    directorio raíz donde viven los buckets (`data_dir/objects`,
    `edecan_local.runtime`) — se guarda en `app.state` en vez de capturarlo
    por clausura para que los tests puedan construir la app apuntando a un
    `tmp_path` distinto por caso sin reimportar el módulo."""
    objects_root = Path(objects_root)
    objects_root.mkdir(parents=True, exist_ok=True)

    app = Starlette(
        routes=[
            Route("/{bucket}", _bucket_endpoint, methods=["PUT", "GET"]),
            Route(
                "/{bucket}/{key:path}",
                _object_endpoint,
                methods=["PUT", "GET", "HEAD", "DELETE"],
            ),
        ]
    )
    app.state.objects_root = objects_root
    return app
