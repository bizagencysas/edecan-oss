"""`POST|GET /v1/files` (ARCHITECTURE.md §10.12, §10.14).

Sube el archivo a `s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{filename}`,
inserta la fila en `files` y encola el job `ingest_file` (`edecan_core.queue.enqueue`).
Valida `limits.storage_mb` del plan antes de aceptar el archivo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import aioboto3
from edecan_core.queue import enqueue
from edecan_schemas import UNLIMITED
from edecan_schemas.plans import LIMIT_STORAGE_MB
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, TenantCtx, get_current_user, get_repo, rate_limit
from edecan_api.repo import Repo

router = APIRouter(prefix="/v1/files", tags=["files"], dependencies=[Depends(rate_limit)])

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _file_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "filename": row.get("filename"),
        "mime": row.get("mime"),
        "size_bytes": row.get("size_bytes"),
        "status": row.get("status"),
        "s3_key": row.get("s3_key"),
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
    raw = await file.read()
    size_bytes = len(raw)

    await _check_storage_quota(repo, tenant, size_bytes)

    file_id = uuid.uuid4()
    filename = file.filename or "archivo"
    mime = file.content_type or "application/octet-stream"
    s3_key = f"tenants/{tenant.tenant_id}/files/{file_id}/{filename}"

    session = aioboto3.Session()
    async with session.client(
        "s3", region_name=settings.AWS_REGION, endpoint_url=settings.AWS_ENDPOINT_URL
    ) as s3:
        await s3.put_object(Bucket=settings.S3_BUCKET, Key=s3_key, Body=raw, ContentType=mime)

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
