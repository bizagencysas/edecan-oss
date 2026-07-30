"""Helper interno de S3 para `edecan_docanalysis` (ARCHITECTURE.md §10.14, §10.3, §2).

Todas las tools de este paquete corren dentro del turno del agente: `ctx.session`
es la `AsyncSession` con Row-Level Security ya activado para `ctx.tenant_id`
(ARCHITECTURE.md §2), igual que en `edecan_toolkit.documentos`/`finanzas` — por
eso este módulo habla SQL parametrizado directo contra `files` en vez de
importar `edecan_db.models` (esa forma interna no está pinneada por el
contrato, ARCHITECTURE.md §10.1).

Dos operaciones:

- `descargar_archivo`: lee la fila `files` del tenant actual (por `id`) y baja
  su contenido de S3 con un cliente `aioboto3` construido al vuelo — un `Tool`
  no tiene un slot reservado en `ToolContext` para un cliente S3 persistente
  (§10.7), así que se abre y se cierra en cada llamada, igual que
  `apps/api/edecan_api/routers/files.py::upload_file`.
- `subir_resultado`: sube bytes nuevos (un gráfico SVG, un reporte XLSX) a
  `tenants/{tenant_id}/files/{file_id}/{filename}` y crea la fila `files` con
  `status='ready'` DIRECTO — a diferencia de una subida de usuario (que nace
  `status='uploaded'` y el worker la promueve a `ready` tras el job
  `ingest_file`, ARCHITECTURE.md §10.11), un archivo generado por una tool ya
  está completo en el momento de subirlo: no hay nada que extraer/trocear
  después, así que no tiene sentido encolar `ingest_file` para él.

Settings se leen SIEMPRE con `getattr(ctx.settings, "CAMPO", default)`
(convención dura de ROADMAP_V2.md §7.5): nunca revienta si al `ctx.settings`
de prueba le falta un campo.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from edecan_core import ToolContext
from sqlalchemy import text

_DEFAULT_BUCKET = "edecan-files"
_DEFAULT_REGION = "us-east-1"


@dataclass
class ArchivoDescargado:
    """Contenido + metadatos de un `files.id` ya descargado de S3."""

    contenido: bytes
    filename: str
    mime: str
    size_bytes: int


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
    `WHERE tenant_id = ...` de `_get_file_row` es la única barrera aquí porque
    `ctx.session` puede o no tener RLS activo según quién construya `ctx` en
    pruebas; en producción RLS ya lo garantiza también).
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


@dataclass(frozen=True)
class _CtxDescarga:
    """Shim mínimo con los 3 únicos atributos que `descargar_archivo`/`_get_file_row`/
    `_bucket`/`_client_kwargs` de verdad leen de un `ToolContext` (`tenant_id`, `session`,
    `settings`) — nunca `user_id`/`llm`/`vault`/`extras`. Uso interno exclusivo de
    `descargar_archivo_de_tenant`, no se exporta: existe para no obligar a un consumidor sin
    turno de agente (un router HTTP) a inventarse un `ToolContext` completo con campos que no
    tienen sentido fuera de `Agent.run_turn` (ARCHITECTURE.md §10.7)."""

    tenant_id: uuid.UUID
    session: Any
    settings: Any


async def descargar_archivo_de_tenant(
    session: Any, settings: Any, tenant_id: uuid.UUID, file_id: uuid.UUID
) -> ArchivoDescargado | None:
    """Superficie pública de descarga S3 (WP-V6-06, `docs/analista.md` "Pantalla Analista"):
    mismo contrato exacto que `descargar_archivo` (`None` si el archivo no existe o no
    pertenece a `tenant_id`), pensada para consumidores que NO corren dentro del loop de un
    agente y por tanto no tienen un `ToolContext` a mano — como un router HTTP de solo lectura
    (`apps/api/edecan_api/routers/analista.py`). Recibe `session`/`settings` explícitos (las
    dos piezas que un router ya tiene vía `Depends(get_tenant_session)`/`Depends(get_settings)`,
    ARCHITECTURE.md §10.12) en vez de un `ToolContext`. Delega en `descargar_archivo` sin
    cambiar su lógica — solo arma el shim de arriba y reenvía.
    """
    ctx = _CtxDescarga(tenant_id=tenant_id, session=session, settings=settings)
    return await descargar_archivo(ctx, file_id)


async def subir_resultado(
    ctx: ToolContext, *, filename: str, mime: str, contenido: bytes
) -> uuid.UUID:
    """Sube `contenido` a S3 y crea la fila `files` (`status='ready'`).

    El `file_id` se genera en Python ANTES de subir (no con
    `gen_random_uuid()` del lado de Postgres) porque forma parte de la ruta
    S3 (`tenants/{tenant_id}/files/{file_id}/{filename}`, ARCHITECTURE.md
    §10.14) y necesitamos conocerlo antes del `put_object` — mismo patrón que
    `apps/api/edecan_api/routers/files.py::upload_file` /
    `apps/api/edecan_api/repo.py::SqlRepo.create_file`. Devuelve el `file_id`.
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
