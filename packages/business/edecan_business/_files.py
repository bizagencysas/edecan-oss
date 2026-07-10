"""Sube el PDF de una factura a S3 + inserta la fila en `files` (`ARCHITECTURE.md` §10.3,
§10.14).

Copia deliberada (no import) de `edecan_creative._files.subir_archivo`, adaptada a la
convención de este paquete: recibe `session`/`tenant_id`/`user_id`/`settings` explícitos en
vez de un `ToolContext` — igual que el resto de `edecan_business` (`invoices.py`, `kpis.py`),
para que tanto `tools.CrearFacturaTool` (que sí tiene un `ctx`) como
`apps/api/edecan_api/routers/negocios.py` (que no lo tiene) puedan llamarlo con la misma
firma. Mismo layout S3, mismo INSERT en `files`, mismo criterio `status="ready"` (nace listo,
no pasa por el job asíncrono `ingest_file`: el PDF ya está completo).

No forma parte del contrato público pinned del paquete (por eso el prefijo `_`, igual
convención que `edecan_toolkit._util`/`edecan_creative._files`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import aioboto3
from sqlalchemy import text

DEFAULT_S3_BUCKET = "edecan-files"
DEFAULT_AWS_REGION = "us-east-1"


@runtime_checkable
class Uploader(Protocol):
    """Firma que acepta `invoices.crear_factura(..., uploader=...)` para guardar el PDF
    generado — el patrón inyectable de `ROADMAP_V2.md` §7.7 ("S3 fakeable"), mismo espíritu
    que `edecan_creative._files.Uploader` pero con parámetros explícitos en vez de `ctx`."""

    async def __call__(
        self,
        session: Any,
        *,
        tenant_id: UUID,
        user_id: UUID,
        settings: Any,
        data: bytes,
        filename: str,
        mime: str,
    ) -> tuple[UUID, str]:
        """Sube `data` (bytes crudos) y devuelve `(file_id, filename)`."""
        ...


async def subir_pdf(
    session: Any,
    *,
    tenant_id: UUID,
    user_id: UUID,
    settings: Any,
    data: bytes,
    filename: str,
    mime: str = "application/pdf",
) -> tuple[UUID, str]:
    """Sube `data` a `s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{filename}`
    (mismo layout que `apps/api/edecan_api/routers/files.py`, `ARCHITECTURE.md` §10.14) e
    inserta la fila correspondiente en `files` con `status="ready"`.

    Lee `settings` de forma defensiva (`getattr(settings, "CAMPO", default)`, convención dura
    de `ROADMAP_V2.md` §7.5) para no reventar si el tenant no configuró
    `S3_BUCKET`/`AWS_REGION`/`AWS_ENDPOINT_URL` explícitos. Devuelve `(file_id, filename)`.
    """
    file_id = uuid.uuid4()
    s3_key = f"tenants/{tenant_id}/files/{file_id}/{filename}"
    bucket = getattr(settings, "S3_BUCKET", None) or DEFAULT_S3_BUCKET
    region = getattr(settings, "AWS_REGION", None) or DEFAULT_AWS_REGION
    endpoint_url = getattr(settings, "AWS_ENDPOINT_URL", None)

    # Nombrado `s3_session` a propósito: el primer parámetro posicional de esta función ya
    # se llama `session` (la sesión de base de datos) — reusar ese nombre para el
    # `aioboto3.Session` de abajo lo taparía silenciosamente.
    s3_session = aioboto3.Session()
    async with s3_session.client(
        "s3", region_name=region, endpoint_url=endpoint_url
    ) as s3:
        await s3.put_object(Bucket=bucket, Key=s3_key, Body=data, ContentType=mime)

    await session.execute(
        text(
            "INSERT INTO files "
            "(id, tenant_id, user_id, s3_key, filename, mime, size_bytes, status, "
            "created_at, updated_at) "
            "VALUES (:id, :tenant_id, :user_id, :s3_key, :filename, :mime, :size_bytes, "
            "'ready', :now, :now)"
        ),
        {
            "id": file_id,
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "s3_key": s3_key,
            "filename": filename,
            "mime": mime,
            "size_bytes": len(data),
            "now": datetime.now(UTC),
        },
    )
    return file_id, filename
