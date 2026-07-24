"""`/v1/ide/*` — Estudio de código sobre el runtime de escritorio.

El router conserva los endpoints legacy del IDE web y añade proyectos
autorizados, sesiones durables de terminal/agente y operaciones Git tipadas.
Cada endpoint arma `{action, params}`, lo envía mediante
`ConnectionManager.send_command(...)` y traduce la respuesta a HTTP.

La API nunca toca el sistema de archivos ni ejecuta Git en el servidor. El
trabajo sucede en la computadora de la persona mediante
`edecan_companion.ide_runtime`: el teléfono conserva solo IDs y cursores, por
lo que una pérdida temporal de conexión no mata el proceso ni obliga a
reenviar la instrucción.

Mapeo de errores (`_send_or_error`):
- Sin companion conectado -> 503.
- Companion conectado pero sin respuesta a tiempo (`CompanionError`) -> 504.
- El companion respondió `{"ok": false, "error": ...}` -> 422 con ese mensaje.

Nunca se loguea contenido de archivos ni de comandos: este módulo no llama
`logging` directamente sobre datos de request/response, igual que
`RequestContextMiddleware` (`main.py`) no loguea cuerpos de request.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from edecan_schemas.plans import FLAG_COMPANION_IDE
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from edecan_api.companion_manager import CompanionError, ConnectionManager
from edecan_api.deps import CurrentUser, TenantCtx, get_current_user, rate_limit
from edecan_api.ide_security import require_paired_ide_device

router = APIRouter(prefix="/v1/ide", tags=["ide"], dependencies=[Depends(rate_limit)])
advanced_router = APIRouter(dependencies=[Depends(require_paired_ide_device)])

# Un poco más que el timeout interno de `run_command` en el companion (30s,
# ver `edecan_companion.actions.COMMAND_TIMEOUT_SECONDS`): así es el
# companion quien reporta el `ActionError` de "se agotó el tiempo" (-> 422,
# mensaje claro) en vez de que la API corte primero la espera con un 504
# menos informativo. El resto de acciones IDE (tree/file/edit/search) son
# rápidas y no necesitan este margen extra.
IDE_RUN_TIMEOUT_SECONDS = 95.0
IDE_APPROVAL_TIMEOUT_SECONDS = 70.0
IDE_GIT_MUTATION_TIMEOUT_SECONDS = 125.0
IDE_GIT_PUSH_TIMEOUT_SECONDS = 250.0


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


class WorkspaceCreateIn(BaseModel):
    path: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)


class WorkspaceFileWriteIn(BaseModel):
    path: str = Field(min_length=1)
    content: str


class WorkspaceEditIn(EditIn):
    pass


class TerminalStartIn(BaseModel):
    workspace_id: str = Field(min_length=1)
    argv: list[str] | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=160)


class TerminalInputIn(BaseModel):
    data: str = Field(min_length=1, max_length=64_000)


class AgentStartIn(BaseModel):
    workspace_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=200_000)
    provider: Literal["auto", "codex", "claude"] = "auto"
    title: str | None = Field(default=None, min_length=1, max_length=160)
    model: str | None = Field(default=None, min_length=1, max_length=120)


class GitPathsIn(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=1000)


class GitCommitIn(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)


class GitBranchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    checkout: bool = False


class GitCheckoutIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    create: bool = False


class GitPushIn(BaseModel):
    remote: str | None = Field(default="origin", min_length=1, max_length=200)
    branch: str | None = Field(default=None, min_length=1, max_length=200)
    set_upstream: bool = False


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
        timeout=IDE_APPROVAL_TIMEOUT_SECONDS,
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
        timeout=IDE_APPROVAL_TIMEOUT_SECONDS,
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


# ---------------------------------------------------------------------------
# Workspaces autorizados (contrato aditivo; los endpoints legacy continúan)
# ---------------------------------------------------------------------------


@advanced_router.get("/workspaces")
async def get_workspaces(
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_workspace_list", {}
    )


@advanced_router.post("/workspaces")
async def post_workspace(
    body: WorkspaceCreateIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    params: dict[str, Any] = {"path": body.path}
    if body.name is not None:
        params["name"] = body.name
    result = await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_workspace_authorize",
        params,
        timeout=IDE_APPROVAL_TIMEOUT_SECONDS,
    )
    return dict(result.get("workspace") or {})


@advanced_router.post("/workspaces/{workspace_id}/activate")
async def post_workspace_activate(
    workspace_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    result = await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_workspace_activate",
        {"workspace_id": workspace_id},
    )
    return dict(result.get("workspace") or {})


@advanced_router.get("/workspaces/{workspace_id}/tree")
async def get_workspace_tree(
    workspace_id: str,
    path: str | None = Query(default=None),
    max_depth: int | None = Query(default=None, ge=1, le=12),
    max_entries: int | None = Query(default=None, ge=1, le=2000),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    params: dict[str, Any] = {"workspace_id": workspace_id}
    if path is not None:
        params["path"] = path
    if max_depth is not None:
        params["max_depth"] = max_depth
    if max_entries is not None:
        params["max_entries"] = max_entries
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_tree", params
    )


@advanced_router.get("/workspaces/{workspace_id}/file")
async def get_workspace_file(
    workspace_id: str,
    path: str = Query(..., min_length=1),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_read_file",
        {"workspace_id": workspace_id, "path": path},
    )


@advanced_router.put("/workspaces/{workspace_id}/file")
async def put_workspace_file(
    workspace_id: str,
    body: WorkspaceFileWriteIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_write_file",
        {"workspace_id": workspace_id, "path": body.path, "content": body.content},
        timeout=IDE_APPROVAL_TIMEOUT_SECONDS,
    )


@advanced_router.post("/workspaces/{workspace_id}/edit")
async def post_workspace_edit(
    workspace_id: str,
    body: WorkspaceEditIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_apply_edit",
        {
            "workspace_id": workspace_id,
            "path": body.path,
            "old_string": body.old_string,
            "new_string": body.new_string,
            "replace_all": body.replace_all,
        },
        timeout=IDE_APPROVAL_TIMEOUT_SECONDS,
    )


@advanced_router.post("/workspaces/{workspace_id}/search")
async def post_workspace_search(
    workspace_id: str,
    body: SearchIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    params: dict[str, Any] = {"workspace_id": workspace_id, "query": body.query}
    if body.path is not None:
        params["path"] = body.path
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_search", params
    )


# ---------------------------------------------------------------------------
# Sesiones durables: el proceso vive en escritorio y el móvil lee por cursor
# ---------------------------------------------------------------------------


@advanced_router.get("/terminals")
async def get_terminals(
    workspace_id: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    params = {"workspace_id": workspace_id} if workspace_id is not None else {}
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_terminal_list", params
    )


@advanced_router.post("/terminals")
async def post_terminal(
    body: TerminalStartIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    params = body.model_dump(exclude_none=True)
    result = await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_terminal_start",
        params,
        timeout=IDE_APPROVAL_TIMEOUT_SECONDS,
    )
    return dict(result.get("session") or {})


@advanced_router.get("/terminals/{session_id}")
async def get_terminal(
    session_id: str,
    cursor: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_terminal_read",
        {"session_id": session_id, "cursor": cursor},
    )


@advanced_router.post("/terminals/{session_id}/input")
async def post_terminal_input(
    session_id: str,
    body: TerminalInputIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_terminal_input",
        {"session_id": session_id, "data": body.data},
    )


@advanced_router.delete("/terminals/{session_id}")
async def delete_terminal(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_terminal_close",
        {"session_id": session_id},
    )


@advanced_router.get("/agents")
async def get_agents(
    workspace_id: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    params = {"workspace_id": workspace_id} if workspace_id is not None else {}
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_agent_list", params
    )


@advanced_router.post("/agents")
async def post_agent(
    body: AgentStartIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    result = await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_agent_start",
        body.model_dump(exclude_none=True),
        timeout=IDE_APPROVAL_TIMEOUT_SECONDS,
    )
    return dict(result.get("session") or {})


@advanced_router.get("/agents/{session_id}")
async def get_agent(
    session_id: str,
    cursor: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_agent_read",
        {"session_id": session_id, "cursor": cursor},
    )


@advanced_router.delete("/agents/{session_id}")
async def delete_agent(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_agent_cancel",
        {"session_id": session_id},
    )


# ---------------------------------------------------------------------------
# Git tipado; ninguna ruta acepta un comando de shell
# ---------------------------------------------------------------------------


@advanced_router.get("/workspaces/{workspace_id}/git/status")
async def get_git_status(
    workspace_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_git_status",
        {"workspace_id": workspace_id},
    )


@advanced_router.get("/workspaces/{workspace_id}/git/diff")
async def get_git_diff(
    workspace_id: str,
    staged: bool = Query(default=False),
    path: str | None = Query(default=None, min_length=1),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    params: dict[str, Any] = {"workspace_id": workspace_id, "staged": staged}
    if path is not None:
        params["paths"] = [path]
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_git_diff", params
    )


@advanced_router.get("/workspaces/{workspace_id}/git/log")
async def get_git_log(
    workspace_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_git_log",
        {"workspace_id": workspace_id, "limit": limit},
    )


async def _git_mutation(
    *,
    action: str,
    workspace_id: str,
    params: dict[str, Any],
    current_user: CurrentUser,
    companion_manager: ConnectionManager,
    timeout: float = IDE_GIT_MUTATION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        action,
        {"workspace_id": workspace_id, **params},
        timeout=timeout,
    )


@advanced_router.post("/workspaces/{workspace_id}/git/stage")
async def post_git_stage(
    workspace_id: str,
    body: GitPathsIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    return await _git_mutation(
        action="ide_git_stage",
        workspace_id=workspace_id,
        params={"paths": body.paths},
        current_user=current_user,
        companion_manager=companion_manager,
    )


@advanced_router.post("/workspaces/{workspace_id}/git/unstage")
async def post_git_unstage(
    workspace_id: str,
    body: GitPathsIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    return await _git_mutation(
        action="ide_git_unstage",
        workspace_id=workspace_id,
        params={"paths": body.paths},
        current_user=current_user,
        companion_manager=companion_manager,
    )


@advanced_router.post("/workspaces/{workspace_id}/git/commit")
async def post_git_commit(
    workspace_id: str,
    body: GitCommitIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    return await _git_mutation(
        action="ide_git_commit",
        workspace_id=workspace_id,
        params={"message": body.message},
        current_user=current_user,
        companion_manager=companion_manager,
    )


@advanced_router.post("/workspaces/{workspace_id}/git/branch")
async def post_git_branch(
    workspace_id: str,
    body: GitBranchIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    return await _git_mutation(
        action="ide_git_branch",
        workspace_id=workspace_id,
        params=body.model_dump(),
        current_user=current_user,
        companion_manager=companion_manager,
    )


@advanced_router.post("/workspaces/{workspace_id}/git/checkout")
async def post_git_checkout(
    workspace_id: str,
    body: GitCheckoutIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    return await _git_mutation(
        action="ide_git_checkout",
        workspace_id=workspace_id,
        params=body.model_dump(),
        current_user=current_user,
        companion_manager=companion_manager,
    )


@advanced_router.post("/workspaces/{workspace_id}/git/push")
async def post_git_push(
    workspace_id: str,
    body: GitPushIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    params = body.model_dump(exclude_none=True)
    params.setdefault("remote", "origin")
    return await _git_mutation(
        action="ide_git_push",
        workspace_id=workspace_id,
        params=params,
        current_user=current_user,
        companion_manager=companion_manager,
        timeout=IDE_GIT_PUSH_TIMEOUT_SECONDS,
    )


# Se incluye al final para que todas las rutas avanzadas hereden el prefijo,
# rate-limit y tags del router legacy, conservando a la vez su gate adicional
# de dispositivo + transporte.
router.include_router(advanced_router)
