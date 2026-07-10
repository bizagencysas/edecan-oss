"""`/v1/ide/*` — IDE embebido sobre el companion de escritorio (ARCHITECTURE.md
§10.12, ROADMAP_V2.md §7.6/§7.8, WP-V2-08).

Puente delgado entre la web y las acciones nuevas del companion
(`edecan_companion.actions`: `list_tree`, `search_files`, `apply_edit`,
`screenshot`) más dos ya existentes en v1 (`read_file`, `write_file`,
`run_command`): cada endpoint arma un mensaje `{action, params}`, lo manda
con `ConnectionManager.send_command(...)` (`edecan_api.companion_manager`,
inyectado vía `app.state.companion_manager` — mismo singleton que usa
`routers.conversations._companion_caller`) y traduce la respuesta a HTTP.
Este router NUNCA toca el sistema de archivos del servidor: todo el trabajo
real (sandbox, aprobación humana, auditoría) ocurre del lado del companion,
en la máquina del propio usuario (`edecan_companion.actions.execute`).

Mapeo de errores (`_send_or_error`):
- Sin companion conectado -> 503.
- Companion conectado pero sin respuesta a tiempo (`CompanionError`) -> 504.
- El companion respondió `{"ok": false, "error": ...}` (validación, permiso
  denegado por el usuario, `ActionError` del lado companion) -> 422 con ese
  mensaje.

Nunca se loguea contenido de archivos ni de comandos: este módulo no llama
`logging` directamente sobre datos de request/response, igual que
`RequestContextMiddleware` (`main.py`) no loguea cuerpos de request.
"""

from __future__ import annotations

import uuid
from typing import Any

from edecan_schemas.plans import FLAG_COMPANION_IDE
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from edecan_api.companion_manager import CompanionError, ConnectionManager
from edecan_api.deps import CurrentUser, TenantCtx, get_current_user, rate_limit

router = APIRouter(prefix="/v1/ide", tags=["ide"], dependencies=[Depends(rate_limit)])

# Un poco más que el timeout interno de `run_command` en el companion (30s,
# ver `edecan_companion.actions.COMMAND_TIMEOUT_SECONDS`): así es el
# companion quien reporta el `ActionError` de "se agotó el tiempo" (-> 422,
# mensaje claro) en vez de que la API corte primero la espera con un 504
# menos informativo. El resto de acciones IDE (tree/file/edit/search) son
# rápidas y no necesitan este margen extra.
IDE_RUN_TIMEOUT_SECONDS = 35.0


def _require_companion_ide(tenant: TenantCtx) -> None:
    """Flag de plan `companion.ide` (`edecan_schemas.plans`, ROADMAP_V2.md §7.2;
    `True` en los 4 planes de la matriz hoy). `tenant.flags.get(..., False)`
    es tolerante a un catálogo de planes desactualizado o a un `plan_key`
    huérfano (`flags_for_plan` devuelve `{}` si el plan no existe en
    `PLANES`): en cualquiera de esos casos, fail-closed (403), no fail-open.
    """
    if not tenant.flags.get(FLAG_COMPANION_IDE, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El IDE embebido no está disponible en tu plan.",
        )


def get_companion_manager(request: Request) -> ConnectionManager:
    """`app.state.companion_manager` (creado en `main.py:create_app()`).

    Mismo patrón que `routers.conversations._companion_caller` y
    `deps.get_tool_registry` para leer un singleton de `app.state` desde un
    handler HTTP normal (a diferencia de `routers.companion.companion_ws`,
    que es un WebSocket y lo lee de `websocket.app.state` directo).
    """
    return request.app.state.companion_manager


async def _send_or_error(
    companion_manager: ConnectionManager,
    tenant_id: uuid.UUID,
    action: str,
    params: dict[str, Any],
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Envía `{action, params}` al companion del tenant y traduce su respuesta a HTTP.

    Se verifica `is_connected` ANTES de llamar `send_command` para poder
    distinguir "no hay companion emparejado" (503) de "sí hay companion pero
    no respondió a tiempo" (504) sin depender de parsear el texto de
    `CompanionError` (que `ConnectionManager.send_command` usa para ambos
    casos, ver su docstring). Una desconexión justo en la ventana entre este
    chequeo y el envío cae en el segundo caso (504), lectura razonable
    también ("se le pidió algo y no llegó respuesta").
    """
    if not companion_manager.is_connected(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No hay companion conectado. Empareja tu equipo desde Ajustes.",
        )

    try:
        response = await companion_manager.send_command(tenant_id, action, params, timeout=timeout)
    except CompanionError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc

    if not response.get("ok", False):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(response.get("error") or "el companion rechazó la acción"),
        )
    return dict(response.get("result") or {})


# ---------------------------------------------------------------------------
# Modelos de request
# ---------------------------------------------------------------------------


class FileWriteIn(BaseModel):
    path: str = Field(min_length=1)
    content: str


class EditIn(BaseModel):
    path: str = Field(min_length=1)
    old_string: str = Field(min_length=1)
    new_string: str
    replace_all: bool = False


class RunIn(BaseModel):
    command: str = Field(min_length=1)


class SearchIn(BaseModel):
    query: str = Field(min_length=1)
    path: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_status(
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, bool]:
    """`{"connected": bool}` -- si no hay companion emparejado, el resto de rutas devuelve 503."""
    _require_companion_ide(current_user.tenant)
    return {"connected": companion_manager.is_connected(current_user.tenant_id)}


@router.get("/tree")
async def get_tree(
    path: str | None = Query(default=None),
    max_depth: int | None = Query(default=None, ge=1),
    max_entries: int | None = Query(default=None, ge=1),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Árbol recursivo del sandbox del companion (acción `list_tree`); `path` default: raíz."""
    _require_companion_ide(current_user.tenant)
    params: dict[str, Any] = {}
    if path is not None:
        params["path"] = path
    if max_depth is not None:
        params["max_depth"] = max_depth
    if max_entries is not None:
        params["max_entries"] = max_entries
    return await _send_or_error(companion_manager, current_user.tenant_id, "list_tree", params)


@router.get("/file")
async def get_file(
    path: str = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Lee un archivo del sandbox del companion (acción `read_file`, ya existente en v1)."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "read_file", {"path": path}
    )


@router.put("/file")
async def put_file(
    body: FileWriteIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Escribe (reemplaza por completo) un archivo del sandbox (acción `write_file` de v1)."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "write_file",
        {"path": body.path, "content": body.content},
    )


@router.post("/edit")
async def post_edit(
    body: EditIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Edición quirúrgica: reemplaza `old_string` por `new_string` (acción `apply_edit`)."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "apply_edit",
        {
            "path": body.path,
            "old_string": body.old_string,
            "new_string": body.new_string,
            "replace_all": body.replace_all,
        },
    )


@router.post("/run")
async def post_run(
    body: RunIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Corre `command` en el sandbox del companion (acción `run_command` de v1, ya allowlisted)."""
    _require_companion_ide(current_user.tenant)
    result = await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "run_command",
        {"command": body.command},
        timeout=IDE_RUN_TIMEOUT_SECONDS,
    )
    # El companion devuelve "returncode" (ver `edecan_companion.actions._run_command`);
    # el contrato HTTP pinned de este endpoint usa "exit_code".
    return {
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "exit_code": result.get("returncode", -1),
        "truncated": result.get("truncated", False),
    }


@router.post("/search")
async def post_search(
    body: SearchIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Busca texto en el sandbox del companion línea por línea (acción `search_files`)."""
    _require_companion_ide(current_user.tenant)
    params: dict[str, Any] = {"query": body.query}
    if body.path is not None:
        params["path"] = body.path
    return await _send_or_error(companion_manager, current_user.tenant_id, "search_files", params)
