"""Subida de archivos generados a S3 + fila en `files` (`ARCHITECTURE.md` §10.3,
§10.14; `ROADMAP_V2.md` §7.7: "archivos → S3 + fila `files`").

`subir_archivo` es el uploader REAL por defecto que usan las herramientas de
`edecan_creative.tools`. Cada `Tool` lo recibe por parámetro de constructor
(patrón inyectable: default real, pero sustituible) para que los tests puedan
pasar un doble en memoria sin tocar S3 ni Postgres — `ARCHITECTURE.md` §10.1:
"los tests NO importan paquetes hermanos", así que este módulo no se importa
directamente desde `packages/creative/tests`.

No forma parte del contrato público del paquete (por eso el prefijo `_`, igual
convención que `edecan_toolkit._util`/`_conectores`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import aioboto3
from edecan_db.session import get_session
from sqlalchemy import text

DEFAULT_S3_BUCKET = "edecan-files"
DEFAULT_AWS_REGION = "us-east-1"


@runtime_checkable
class Uploader(Protocol):
    """Firma que aceptan las tools de `edecan_creative` para guardar un archivo generado."""

    async def __call__(
        self, ctx: Any, *, data: bytes, filename: str, mime: str
    ) -> tuple[UUID, str]:
        """Sube `data` (bytes crudos) y devuelve `(file_id, filename)`."""
        ...


async def subir_archivo(ctx: Any, *, data: bytes, filename: str, mime: str) -> tuple[UUID, str]:
    """Sube `data` a `s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{filename}`
    (mismo layout que `apps/api/edecan_api/routers/files.py`, `ARCHITECTURE.md`
    §10.14) e inserta la fila correspondiente en `files` con `status="ready"`.

    A diferencia de una subida manual del usuario (`POST /v1/files`), que
    arranca en `status="uploaded"` y pasa por el job async `ingest_file` para
    extraer texto, un archivo generado por estas tools ya está completo y no
    necesita ningún procesamiento adicional — nace `"ready"` directamente.

    Lee `ctx.settings` de forma defensiva (`getattr(ctx.settings, "CAMPO",
    default)`, convención dura de `ROADMAP_V2.md` §7.5) para no reventar si
    el tenant no configuró `S3_BUCKET`/`AWS_REGION`/`AWS_ENDPOINT_URL`
    explícitos. Devuelve `(file_id, filename)`.

    CARRERA DE TRANSACCIÓN (corregida): el INSERT vive en su PROPIA
    transacción, confirmada al salir de este `async with` — nunca en
    `ctx.session`. Durante un turno de chat en streaming, `ctx.session` es la
    sesión request-scoped de `get_streaming_repo`
    (`apps/api/edecan_api/deps.py`), que solo hace COMMIT cuando el SSE
    completo termina. Si el INSERT viviera ahí, la card que pide la imagen a
    mitad del stream por una petición HTTP aparte (`GET /v1/files/{id}`) abre
    su PROPIA transacción y no ve la fila todavía → 404 en pleno chat, aunque
    la imagen ya esté en S3 (bug real: iOS marcaba `imagenCargada = true` sin
    reintentar). `edecan_db.session.get_session(ctx.tenant_id)` aplica el
    mismo `SET LOCAL ROLE app_user` + `app.tenant_id` que `ctx.session` (RLS
    lo permite igual) y confirma solo esta transacción al salir sin
    excepción — así la fila es visible para cualquier petición HTTP aparte
    apenas termina esta función, sin esperar al resto del turno.
    Contrapartida aceptada: si el turno hace luego `rollback` (p. ej. el
    modelo nunca llega a responder), esta fila de `files` queda huérfana —
    el mismo riesgo que ya existe hoy con el objeto ya subido a S3 arriba.
    """
    file_id = uuid.uuid4()
    s3_key = f"tenants/{ctx.tenant_id}/files/{file_id}/{filename}"
    bucket = getattr(ctx.settings, "S3_BUCKET", None) or DEFAULT_S3_BUCKET
    region = getattr(ctx.settings, "AWS_REGION", None) or DEFAULT_AWS_REGION
    endpoint_url = getattr(ctx.settings, "AWS_ENDPOINT_URL", None)

    session = aioboto3.Session()
    async with session.client("s3", region_name=region, endpoint_url=endpoint_url) as s3:
        await s3.put_object(Bucket=bucket, Key=s3_key, Body=data, ContentType=mime)

    insert_sql = text(
        "INSERT INTO files "
        "(id, tenant_id, user_id, s3_key, filename, mime, size_bytes, status, "
        "created_at, updated_at) "
        "VALUES (:id, :tenant_id, :user_id, :s3_key, :filename, :mime, :size_bytes, "
        "'ready', :now, :now)"
    )
    params = {
        "id": file_id,
        "tenant_id": str(ctx.tenant_id),
        "user_id": str(ctx.user_id),
        "s3_key": s3_key,
        "filename": filename,
        "mime": mime,
        "size_bytes": len(data),
        "now": datetime.now(UTC),
    }
    async with get_session(ctx.tenant_id) as fresh_session:
        await fresh_session.execute(insert_sql, params)
    return file_id, filename
