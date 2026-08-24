"""`/v1/reuniones/*` — Reuniones: transcripción con el STT del tenant + minutas
por LLM del tenant (`ARCHITECTURE.md` §15, WP-V6-05; ver `docs/reuniones.md`
para el flujo completo). El esquema REAL de la tabla `meetings` es el de
`ARCHITECTURE.md` §15.b / `packages/db/alembic/versions/0008_v6_expansion.py`
/ `edecan_db.models.Meeting` — el linchpin WP-V6-01 ya aterrizó esa migración
con columnas distintas a las que `packages/meetings/README.md` documentaba
como "esquema asumido" mientras corría en paralelo; ver "Tabla `meetings`"
más abajo para la reconciliación (mismo criterio que
`apps/api/edecan_api/routers/voz_avanzada.py` ya hizo para `podcasts`).

Este router NO se monta a sí mismo: `edecan_api.main` (WP-V6-01) lo monta de
forma defensiva, igual que el resto de routers v2-v5
(`importlib.import_module` + `try/except ImportError` + `logger.warning` si
falta) — este módulo solo declara `router`. `apps/api/tests/
test_reuniones_router.py` lo monta manualmente sobre la `app` de prueba
(mismo patrón que `test_voz_avanzada.py`/`test_erp_router.py`), así que no
depende de que `V6_ROUTER_NAMES` ya incluya `"reuniones"`.

## Qué hace y qué NO hace

`POST ""` valida que el archivo exista, pertenezca al tenant y sea audio o
video, inserta la fila `meetings` con `status='pending'` (único valor
inicial que acepta el CHECK real, `ARCHITECTURE.md` §15.b:
`pending|running|done|error` — `'queued'` NO es un valor válido; el worker
la pasa a `'running'` al tomarla) y encola `process_meeting {meeting_id}` —
TODO dentro de la MISMA transacción HTTP corta (`get_tenant_session`), así
que no hay carrera de "lee tu propia escritura" con el worker (a diferencia
de la tool del agente
`resumir_reunion`, que NUNCA toca la base de datos ella misma — ver el
docstring de `packages/meetings/edecan_meetings/tools.py`, sección "Por qué
la tool NO inserta la fila `meetings` ella misma", para el porqué de esa
asimetría deliberada). Este router nunca descarga/procesa el archivo él
mismo — todo el trabajo pesado (ffmpeg, STT, LLM) vive en
`apps/worker/edecan_worker/handlers/process_meeting.py`.

`DELETE /{id}` borra SOLO la fila `meetings` — el archivo de origen
(`files`) y la transcripción (`files`, `transcript_file_id`) se quedan
intactos a propósito: son archivos normales del tenant (aparecen en
`/app/archivos`, el agente los puede seguir consultando con
`consultar_documentos`), no "propiedad" exclusiva de esta reunión. Borrarlos
en cascada sorprendería a un tenant que solo quería limpiar la lista de
reuniones, no perder sus archivos.

## Tabla `meetings` — esquema real, `ARCHITECTURE.md` §15.b

Este router se escribió inicialmente contra el "esquema asumido" que
`packages/meetings/README.md` documentaba mientras el linchpin de v6
(WP-V6-01) corría en paralelo — mismo punto de partida que `podcasts` (ver
el docstring de `edecan_api.routers.voz_avanzada`, sección "Tabla
`podcasts`"). A diferencia de `podcasts`, esa reconciliación nunca se hizo
aquí cuando la migración real aterrizó. Esquema real
(`packages/db/alembic/versions/0008_v6_expansion.py` /
`edecan_db.models.Meeting`), y las tres diferencias que importaban:

1. La columna es `source_file_id`, no `file_id` (`file_id` NO existe en la
   tabla). `ReunionIn.file_id`/`ReunionOut.file_id` (el contrato HTTP público,
   consumido por `apps/web/src/lib/api-reuniones.ts`) conservan ese nombre a
   propósito — solo cambia el mapeo interno hacia/desde la columna real.
2. `decisiones`/`acciones`/`temas` NO son columnas propias — viven anidadas
   dentro del único blob `minutos JSONB` que escribe `process_meeting`
   (`{"decisiones": [...], "acciones": [{"tarea", "responsable"}], "temas":
   [...]}`, mismo shape que `edecan_meetings.minutas.Minutas.to_dict()`).
3. `status` usa el vocabulario `pending|running|done|error` (mismo CHECK que
   `podcasts`), no `queued|running|done|error` — `'queued'` revienta el
   CHECK constraint real.

## Gate de flag de plan

`_require_tools_meetings` — mismo patrón EXACTO que
`edecan_api.routers.viajes._require_tools_travel` (import con guardia: si el
linchpin de v6 —WP-V6-01— todavía no aterrizó
`edecan_schemas.plans.FLAG_TOOLS_MEETINGS`, este router sigue funcionando
con el mismo string literal `"tools.meetings"` como fallback — nunca revienta
el import de todo el módulo por esto).

## Por qué NO hace falta un commit explícito antes de un `raise` aquí

A diferencia de `crear_clon_voz` (`apps/api/edecan_api/routers/voz_avanzada.py`,
`HOTFIXES_PENDIENTES.md` puntos 8/9: un `INSERT` de EVIDENCIA LEGAL debe
sobrevivir un `raise` posterior en la misma request), la fila `meetings` que
inserta `crear_reunion` no es evidencia de cumplimiento — es solo un ítem de
trabajo. Si `enqueue(...)` fallara DESPUÉS del `INSERT` (SQS caído,
`job_type` todavía no pinned en `JOB_TYPES` mientras WP-V6-01 no aterriza,
etc.), dejar que el `ROLLBACK` automático de `get_tenant_session` se lleve
también la fila recién insertada es el comportamiento CORRECTO: mejor "nada
pasó" que una fila `meetings` huérfana en `status='pending'` que ningún job
va a procesar nunca. Por eso `crear_reunion` no comitea a mano en ninguna
rama — se deja el comentario acá, citando el mismo patrón de
HOTFIXES_PENDIENTES.md puntos 8/9, para que quien audite este archivo vea
que se consideró explícitamente y no es un olvido.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from edecan_core.queue import enqueue
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_repo, get_tenant_session, rate_limit
from edecan_api.repo import Repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/reuniones", tags=["reuniones"], dependencies=[Depends(rate_limit)])

# Flag de plan pinned `ARCHITECTURE.md` §15 (WP-V6-05) — import con guardia,
# mismo patrón que `edecan_api.routers.viajes` con `FLAG_TOOLS_TRAVEL` (ver
# docstring del módulo).
try:
    from edecan_schemas.plans import FLAG_TOOLS_MEETINGS
except ImportError:  # pragma: no cover - linchpin de v6 todavía no aterrizó el flag
    FLAG_TOOLS_MEETINGS = "tools.meetings"

# Disclaimer de consentimiento OBLIGATORIO — string EXACTO, duplicado a
# propósito de `packages/meetings/edecan_meetings/tools.py`
# (`DISCLAIMER_CONSENTIMIENTO`, ARCHITECTURE.md §10.1: este router no importa
# `edecan_meetings`, paquete que además no aporta nada más que este router
# necesite). El banner de la UI web (`apps/web/src/app/(app)/app/reuniones/
# page.tsx`) repite el mismo string una tercera vez — los tres deben decir
# EXACTAMENTE lo mismo, byte por byte.
DISCLAIMER_CONSENTIMIENTO = (
    "Recuerda: asegúrate de contar con el consentimiento de todos los "
    "participantes para grabar y transcribir esta reunión."
)

_JOB_TYPE = "process_meeting"
_MAX_TITULO_CHARS = 200


# ---------------------------------------------------------------------------
# Gate de flag de plan
# ---------------------------------------------------------------------------


async def _require_tools_meetings(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current_user.tenant.flags.get(FLAG_TOOLS_MEETINGS, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reuniones no está disponible en tu plan.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Bodies / respuestas
# ---------------------------------------------------------------------------


class ReunionIn(BaseModel):
    file_id: uuid.UUID
    titulo: str | None = None


class AccionOut(BaseModel):
    tarea: str
    responsable: str | None = None


class ReunionOut(BaseModel):
    id: uuid.UUID
    file_id: uuid.UUID
    titulo: str
    status: str
    transcript_file_id: uuid.UUID | None = None
    resumen: str | None = None
    decisiones: list[str] = []
    acciones: list[AccionOut] = []
    temas: list[str] = []
    duracion_segundos: float | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _es_audio_o_video(mime: str | None) -> bool:
    normalizado = (mime or "").split(";")[0].strip().lower()
    return normalizado.startswith("audio/") or normalizado.startswith("video/")


def _lista_desde_jsonb(value: Any) -> list[Any]:
    """Una lista dentro de `minutos` (`decisiones`/`acciones`/`temas`) puede
    llegar como `list` ya decodificada o como texto JSON crudo según el
    driver — mismo criterio defensivo que `edecan_api.routers.ads._from_jsonb`
    (duplicado a propósito, ARCHITECTURE.md §10.1), adaptado a un valor de
    tipo ARRAY en vez de objeto."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            cargado = json.loads(value)
        except json.JSONDecodeError:
            return []
        return cargado if isinstance(cargado, list) else []
    return []


def _dict_desde_jsonb(value: Any) -> dict[str, Any]:
    """`meetings.minutos` es el único blob JSONB real (`ARCHITECTURE.md`
    §15.b — NO existen columnas `decisiones`/`acciones`/`temas` propias) y
    puede llegar como `dict` ya decodificado o como texto JSON crudo según
    el driver — mismo criterio defensivo que `_lista_desde_jsonb`, adaptado
    a un valor de tipo objeto."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            cargado = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return cargado if isinstance(cargado, dict) else {}
    return {}


def _fila_a_out(fila: dict[str, Any]) -> ReunionOut:
    minutos = _dict_desde_jsonb(fila.get("minutos"))
    return ReunionOut(
        id=fila["id"],
        file_id=fila["source_file_id"],
        titulo=fila["titulo"],
        status=fila["status"],
        transcript_file_id=fila.get("transcript_file_id"),
        resumen=fila.get("resumen"),
        decisiones=_lista_desde_jsonb(minutos.get("decisiones")),
        acciones=[AccionOut(**a) for a in _lista_desde_jsonb(minutos.get("acciones"))],
        temas=_lista_desde_jsonb(minutos.get("temas")),
        duracion_segundos=(
            float(fila["duracion_segundos"]) if fila.get("duracion_segundos") is not None else None
        ),
        error=fila.get("error"),
        created_at=fila["created_at"],
        updated_at=fila["updated_at"],
    )


# ---------------------------------------------------------------------------
# `meetings` — SQL parametrizado (tabla nueva de v6, sin método dedicado en
# `edecan_api.repo.Repo` — ese archivo está fuera de las rutas que este
# paquete de trabajo puede tocar; mismo criterio que
# `edecan_api.routers.voz_avanzada` con `voice_consents`).
# ---------------------------------------------------------------------------


async def _insertar_reunion(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    file_id: uuid.UUID,
    titulo: str,
) -> dict[str, Any]:
    fila = (
        await session.execute(
            sql_text(
                "INSERT INTO meetings "
                "(tenant_id, user_id, source_file_id, titulo, status) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, :file_id ::uuid, :titulo, 'pending') "
                "RETURNING *"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "file_id": str(file_id),
                "titulo": titulo,
            },
        )
    ).mappings().first()
    assert fila is not None
    return dict(fila)


async def _listar_reuniones(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    filas = (
        await session.execute(
            sql_text(
                "SELECT * FROM meetings WHERE tenant_id = :tenant_id ::uuid "
                "ORDER BY created_at DESC"
            ),
            {"tenant_id": str(tenant_id)},
        )
    ).mappings().all()
    return [dict(f) for f in filas]


async def _obtener_reunion(
    session: AsyncSession, *, tenant_id: uuid.UUID, reunion_id: uuid.UUID
) -> dict[str, Any] | None:
    fila = (
        await session.execute(
            sql_text(
                "SELECT * FROM meetings WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid"
            ),
            {"id": str(reunion_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    return dict(fila) if fila is not None else None


async def _borrar_reunion(
    session: AsyncSession, *, tenant_id: uuid.UUID, reunion_id: uuid.UUID
) -> bool:
    resultado = await session.execute(
        sql_text("DELETE FROM meetings WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid"),
        {"id": str(reunion_id), "tenant_id": str(tenant_id)},
    )
    return bool(resultado.rowcount)


# ---------------------------------------------------------------------------
# POST /v1/reuniones
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def crear_reunion(
    payload: ReunionIn,
    current_user: CurrentUser = Depends(_require_tools_meetings),
    repo: Repo = Depends(get_repo),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> ReunionOut:
    archivo = await repo.get_file(tenant_id=current_user.tenant_id, file_id=payload.file_id)
    if archivo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No encontramos ese archivo.")
    if not _es_audio_o_video(archivo.get("mime")):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="El archivo debe ser un audio o video (mime 'audio/*' o 'video/*').",
        )

    titulo = (
        (payload.titulo or "").strip()[:_MAX_TITULO_CHARS] or archivo.get("filename") or "Reunión"
    )

    fila = await _insertar_reunion(
        session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        file_id=payload.file_id,
        titulo=titulo,
    )

    # Ver docstring del módulo, "Por qué NO hace falta un commit explícito
    # antes de un raise aquí" (HOTFIXES_PENDIENTES.md puntos 8/9 no aplica:
    # esta fila no es evidencia de cumplimiento).
    await enqueue(
        settings, _JOB_TYPE, {"meeting_id": str(fila["id"])}, current_user.tenant_id
    )

    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="reuniones.created",
        target=str(fila["id"]),
        meta={"file_id": str(payload.file_id), "titulo": titulo},
    )

    return _fila_a_out(fila)


# ---------------------------------------------------------------------------
# GET /v1/reuniones
# ---------------------------------------------------------------------------


@router.get("")
async def listar_reuniones(
    current_user: CurrentUser = Depends(_require_tools_meetings),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ReunionOut]:
    filas = await _listar_reuniones(session, tenant_id=current_user.tenant_id)
    return [_fila_a_out(f) for f in filas]


# ---------------------------------------------------------------------------
# GET /v1/reuniones/{id}
# ---------------------------------------------------------------------------


@router.get("/{reunion_id}")
async def obtener_reunion(
    reunion_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_tools_meetings),
    session: AsyncSession = Depends(get_tenant_session),
) -> ReunionOut:
    fila = await _obtener_reunion(session, tenant_id=current_user.tenant_id, reunion_id=reunion_id)
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No encontramos esa reunión.")
    return _fila_a_out(fila)


# ---------------------------------------------------------------------------
# DELETE /v1/reuniones/{id} — ver docstring del módulo.
# ---------------------------------------------------------------------------


@router.delete("/{reunion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def borrar_reunion(
    reunion_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_tools_meetings),
    repo: Repo = Depends(get_repo),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    borrada = await _borrar_reunion(
        session, tenant_id=current_user.tenant_id, reunion_id=reunion_id
    )
    if not borrada:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No encontramos esa reunión.")

    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="reuniones.deleted",
        target=str(reunion_id),
    )
