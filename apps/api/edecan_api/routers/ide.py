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

from edecan_llm.router import LLMRouter
from edecan_llm.task_router import modelo_ide_permitido, modelos_ide_disponibles
from edecan_schemas.ide_blocks import ide_blocks_from_presentation
from edecan_schemas.plans import (
    FLAG_COMPANION_IDE,
    FLAG_VOICE_CLONING,
    FLAG_VOICE_TELEPHONY,
    FLAG_VOICE_WEB,
    UNLIMITED,
)
from edecan_skills.store import get_by_id as get_skill_by_id
from edecan_skills.store import list_skills
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.companion_manager import CompanionError, ConnectionManager
from edecan_api.deps import (
    CurrentUser,
    TenantCtx,
    get_current_user,
    get_llm_router,
    get_mcp_tools_for_tenant,
    get_repo,
    get_tenant_session,
    ide_rate_limit,
)
from edecan_api.ide_security import require_paired_ide_device
from edecan_api.repo import Repo
from edecan_api.routers.conversations import (
    _automatic_conversation_title,
    _semantic_conversation_title,
)
from edecan_api.routers.memory import list_memory
from edecan_api.routers.usage import get_usage

router = APIRouter(prefix="/v1/ide", tags=["ide"], dependencies=[Depends(ide_rate_limit)])
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
IDE_FOLDER_PICKER_TIMEOUT_SECONDS = 330.0
IDE_CLONE_TIMEOUT_SECONDS = 330.0


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


def _sanitize_agent_events(result: dict[str, Any]) -> dict[str, Any]:
    """Deja pasar al teléfono solo bloques ricos que el contrato reconoce.

    El canal `presentation` de un evento de sesión (ver
    `edecan_companion.ide_bloques`) es lo único que puede acuñar UI en el hilo
    del IDE, y el companion ya lo valida antes de escribirlo. Esto lo valida
    OTRA VEZ acá porque el companion es un programa aparte, instalado en la
    máquina de la persona: puede ser de otra versión que este servidor, y el
    resto de este router es un pasamanos que reenvía lo que el companion diga.
    Este es el último punto bajo control del servidor antes de que algo se
    dibuje en un teléfono.

    Un bloque que no valida se descarta y el evento sigue viaje con su `text`
    intacto: el peor caso es que la persona lea el texto en vez de ver la
    tarjeta, nunca que pierda el mensaje. Los eventos sin `presentation` --
    casi todos -- pasan tal cual, sin copiarse.
    """
    events = result.get("events")
    if not isinstance(events, list):
        return result
    limpios: list[Any] = []
    for event in events:
        if not isinstance(event, dict) or "presentation" not in event:
            limpios.append(event)
            continue
        limpio = {clave: valor for clave, valor in event.items() if clave != "presentation"}
        bloques = ide_blocks_from_presentation(event.get("presentation"))
        if bloques:
            # Sin `by_alias`: el resto del contrato con iOS se serializa con
            # los nombres de campo tal cual (ver `SocialDraftBlock` en
            # `edecan_schemas.chat` sobre por qué acá no se usan alias).
            limpio["presentation"] = [bloque.model_dump(mode="json") for bloque in bloques]
        limpios.append(limpio)
    result["events"] = limpios
    return result


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


class SemanticSearchIn(BaseModel):
    query: str = Field(min_length=1)
    k: int = Field(default=10, ge=1, le=50)


class WorkspaceCreateIn(BaseModel):
    path: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)


class WorkspacePickIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    activate: bool = True


class WorkspaceCloneIn(BaseModel):
    parent_workspace_id: str = Field(min_length=1)
    url: str = Field(min_length=1, max_length=2048)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    branch: str | None = Field(default=None, min_length=1, max_length=200)
    depth: int | None = Field(default=None, ge=1, le=1000)
    activate: bool = True


class WorkspaceFileWriteIn(BaseModel):
    path: str = Field(min_length=1)
    content: str


class WorkspaceEditIn(EditIn):
    pass


class ProjectCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    workspace_id: str = Field(min_length=1)


class ProjectRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ConversationCreateIn(BaseModel):
    project_id: str | None = Field(default=None, min_length=1)
    title: str | None = Field(default=None, min_length=1, max_length=160)


class ConversationRenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class ConversationMoveIn(BaseModel):
    # ``None`` explícito (no "campo ausente") es la forma de desasignar una
    # conversación de todo proyecto; por eso no lleva default distinto de
    # None y el router lo manda siempre, nunca lo omite con exclude_none.
    project_id: str | None = None


class TerminalStartIn(BaseModel):
    workspace_id: str = Field(min_length=1)
    argv: list[str] | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=160)


class TerminalInputIn(BaseModel):
    data: str = Field(min_length=1, max_length=64_000)


class AgentAttachmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    media_type: Literal["image/jpeg", "image/png", "image/webp", "image/gif"]
    data: str = Field(min_length=1, max_length=14_000_000)


class AgentStartIn(BaseModel):
    workspace_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=200_000)
    provider: Literal["auto", "workers_ai"] = "workers_ai"
    model: str | None = Field(default=None, min_length=1, max_length=240)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    attachments: list[AgentAttachmentIn] = Field(default_factory=list, max_length=5)


class AgentMCPConfirmationIn(BaseModel):
    approved: bool


class AgentDiffRejectIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class CommandExecuteIn(BaseModel):
    """Cuerpo de `POST /commands/execute` (menú "/" del compositor).

    Todo lo que no sea `text` es opcional a propósito: cada comando pide del
    contexto de la UI solo lo que en verdad necesita (ver
    `edecan_companion.ide_runtime._despachar_comando`) -- `/help` no necesita
    ninguno, `/rename` necesita `conversation_id`, `/diff` necesita
    `session_id`, etc. Enviar de más no rompe nada.
    """

    text: str = Field(min_length=1, max_length=4000)
    workspace_id: str | None = None
    conversation_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    confirmed: bool = False


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
    return await _send_or_error(companion_manager, current_user.tenant_id, "ide_workspace_list", {})


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


@advanced_router.post("/workspaces/pick")
async def post_workspace_pick(
    body: WorkspacePickIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Abre Finder/Explorer en la computadora y autoriza la carpeta elegida."""

    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_workspace_pick",
        body.model_dump(exclude_none=True),
        timeout=IDE_FOLDER_PICKER_TIMEOUT_SECONDS,
    )


@advanced_router.post("/workspaces/clone")
async def post_workspace_clone(
    body: WorkspaceCloneIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Clona un URL Git validado dentro de un workspace ya autorizado."""

    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_workspace_clone",
        body.model_dump(exclude_none=True),
        timeout=IDE_CLONE_TIMEOUT_SECONDS,
    )


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
    return await _send_or_error(companion_manager, current_user.tenant_id, "ide_tree", params)


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
    return await _send_or_error(companion_manager, current_user.tenant_id, "ide_search", params)


@advanced_router.get("/workspaces/{workspace_id}/references")
async def get_workspace_references(
    workspace_id: str,
    prefix: str = Query(default=""),
    kinds: str | None = Query(
        default=None, description="Coma-separado: file,folder,symbol. Omitido = los tres."
    ),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Autocompletado del menú `@` del compositor (1.1 del plan de paridad):
    archivos, carpetas y símbolos que casan con `prefix` dentro del workspace."""
    _require_companion_ide(current_user.tenant)
    params: dict[str, Any] = {
        "workspace_id": workspace_id,
        "prefix": prefix,
        "limit": limit,
    }
    if kinds:
        params["kinds"] = [item.strip() for item in kinds.split(",") if item.strip()]
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_reference_search", params
    )


@advanced_router.post("/workspaces/{workspace_id}/search/semantic")
async def post_workspace_semantic_search(
    workspace_id: str,
    body: SemanticSearchIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Búsqueda por significado, no por texto literal (2.2 del plan de
    paridad): el mismo panel de búsqueda de arriba (`/search`) puede ofrecer
    este modo como alternativa. Si todavía no hay índice construido, el
    companion indexa en segundo plano y responde con el modo `texto` como
    respaldo (ver `degraded_reason` en la respuesta)."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_semantic_search",
        {"workspace_id": workspace_id, "query": body.query, "k": body.k},
    )


@advanced_router.get("/workspaces/{workspace_id}/search/semantic/status")
async def get_workspace_semantic_search_status(
    workspace_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_semantic_search_status",
        {"workspace_id": workspace_id},
    )


@advanced_router.post("/workspaces/{workspace_id}/search/semantic/reindex")
async def post_workspace_semantic_search_reindex(
    workspace_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Fuerza un reindexado (incremental: solo re-embebe lo que cambió) en
    segundo plano. Normalmente no hace falta llamarlo a mano -- la primera
    búsqueda semántica de un workspace ya lo dispara sola."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_semantic_search_reindex",
        {"workspace_id": workspace_id},
    )


@advanced_router.get("/workspaces/{workspace_id}/memory")
async def get_workspace_memory(
    workspace_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Memoria persistente del proyecto (2.3 del plan de paridad): lo que el
    agente ya descubrió de este repo en sesiones anteriores. Vista de
    transparencia -- lo que de verdad se inyecta en cada turno es un
    subconjunto relevante, calculado por `ide_sessions._memory_block_for`."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_memory_list", {"workspace_id": workspace_id}
    )


@advanced_router.delete("/workspaces/{workspace_id}/memory/{note_id}")
async def delete_workspace_memory_note(
    workspace_id: str,
    note_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Borra un recuerdo puntual -- p. ej. porque ya quedó obsoleto o resultó
    incorrecto."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_memory_forget",
        {"workspace_id": workspace_id, "note_id": note_id},
    )


# ---------------------------------------------------------------------------
# Menú de comandos "/" del compositor (``edecan_companion.ide_comandos`` +
# ``ide_runtime._despachar_comando``). Convive con el menú "@" de arriba: uno
# se dispara al escribir "/" al inicio del compositor, el otro con "@" en
# cualquier posición -- no comparten atajo ni endpoint.
#
# Cuatro comandos (``/usage``, ``/memory``, ``/mcp``, ``/voice``) son
# server-side por naturaleza: viven en datos por TENANT (plan, memoria de
# cuenta, catálogo MCP del tenant), no en la computadora de la persona --
# ``edecan_companion`` es a propósito el único paquete SIN dependencia de
# ``edecan_schemas``/SQLAlchemy (ver su `pyproject.toml`, "separado del
# servidor"), así que esos cuatro NUNCA podrían resolverse ahí de verdad.
# Por eso se interceptan AQUÍ, antes de proxiar nada al companion (que ni
# siquiera hace falta que esté conectado para responderlos) -- reusando la
# MISMA lógica que ya expone cada uno como endpoint propio (``GET /v1/usage``,
# ``GET /v1/memory``, las tools MCP del tenant), no una reimplementación
# paralela. El companion conserva su propio "informativo" para estos cuatro
# nombres (ver ``_MENSAJE_INFORMATIVO`` en ``ide_runtime.py``): sigue siendo
# la respuesta correcta para quien use el companion fuera de este API
# multi-tenant (modo local/self-host, sin plan ni memoria de cuenta).
# ---------------------------------------------------------------------------


def _slash_command_name(text: str) -> str:
    """Nombre del comando (sin la barra, en minúsculas) de un texto de
    compositor como ``"/usage"`` o ``"/memory buscar algo"``. Cadena vacía si
    ``text`` no empieza con "/" o no tiene nombre.

    Deliberadamente NO importa ``edecan_companion.ide_comandos`` para esto:
    ARCHITECTURE.md §10.1 prohíbe que los paquetes de ``apps/`` se importen
    entre sí (son servicios desplegables separados), y este parser de una
    línea es más barato que cruzar esa frontera solo para tokenizar texto.
    """
    cuerpo = text.strip()
    if not cuerpo.startswith("/"):
        return ""
    resto = cuerpo[1:].strip()
    if not resto:
        return ""
    return resto.split(maxsplit=1)[0].casefold()


def _slash_command_args(text: str) -> str | None:
    """Lo que sigue al nombre del comando, o ``None`` si no vino nada (nunca
    una cadena vacía) -- mismo criterio que ``ide_comandos.resolver_comando``
    del lado del companion."""
    cuerpo = text.strip()[1:]
    partes = cuerpo.split(maxsplit=1)
    return partes[1].strip() if len(partes) > 1 and partes[1].strip() else None


def _resumen_uso(payload: dict[str, Any]) -> str:
    limits = payload.get("limits") or {}
    usage = payload.get("usage") or {}
    detalle = []
    for key, limite in limits.items():
        etiqueta = str(key).rsplit(".", 1)[-1].replace("_", " ")
        usado = usage.get(key, 0)
        detalle.append(
            f"{etiqueta}: {usado} (sin límite)"
            if limite == UNLIMITED
            else f"{etiqueta}: {usado}/{limite}"
        )
    return (
        f"Plan «{payload.get('plan_key')}» desde {payload.get('period_start')}: "
        + "; ".join(detalle)
        + "."
    )


async def _ejecutar_usage_servidor(current_user: CurrentUser, repo: Repo) -> dict[str, Any]:
    """``/usage``: el mismo dato real que ``GET /v1/usage`` (uso del mes vs.
    límites del plan del tenant, ``edecan_schemas.plans``), no un aviso."""
    payload = await get_usage(current_user=current_user, repo=repo)
    return {"ok": True, "comando": "usage", "mensaje": _resumen_uso(payload), "data": payload}


async def _ejecutar_memory_servidor(
    current_user: CurrentUser, repo: Repo, query: str | None
) -> dict[str, Any]:
    """``/memory``: la memoria persistente de la CUENTA (``GET /v1/memory``,
    ``apps/api/routers/memory.py``) -- distinta de la memoria por PROYECTO
    del IDE (``ide_memory_list``/``ide_memoria.py``, ya real y expuesta en
    ``GET /workspaces/{id}/memory`` arriba). Argumento opcional = búsqueda
    (``q``); sin argumento, las más recientes/relevantes."""
    items = await list_memory(q=query, k=20, current_user=current_user, repo=repo)
    mensaje = (
        f"{len(items)} recuerdo(s) guardado(s)."
        if items
        else "Todavía no hay memoria persistente guardada; agrégala en Cuenta > Memoria."
    )
    return {"ok": True, "comando": "memory", "mensaje": mensaje, "data": {"items": items}}


async def _ejecutar_mcp_servidor(request: Request, current_user: CurrentUser) -> dict[str, Any]:
    """``/mcp``: las tools MCP ya resueltas para este tenant (mismo
    ``get_mcp_tools_for_tenant`` que arma el catálogo de un turno de chat,
    ``packages/mcp``/``edecan_mcp``) -- fail-open igual que esa función: un
    servidor MCP caído o mal configurado da lista vacía, nunca un error."""
    tools = await get_mcp_tools_for_tenant(request, current_user)
    herramientas = [
        {
            "name": str(getattr(tool, "name", "")),
            "description": str(getattr(tool, "description", "")),
        }
        for tool in tools
    ]
    mensaje = (
        f"{len(herramientas)} herramienta(s) MCP conectada(s)."
        if herramientas
        else "No hay servidores MCP conectados todavía; conéctalos en Ajustes > Integraciones."
    )
    return {
        "ok": True,
        "comando": "mcp",
        "mensaje": mensaje,
        "data": {"herramientas": herramientas},
    }


def _ejecutar_voice_servidor(current_user: CurrentUser) -> dict[str, Any]:
    """``/voice``: el estado REAL de los flags de voz del plan del tenant
    (``edecan_schemas.plans``) -- probar una síntesis de verdad exige un
    turno de audio completo (``packages/voice``, ``POST /v1/voice/speak``),
    que no cabe en la respuesta de texto de un comando "/"; esto reemplaza el
    aviso genérico anterior por el dato real de qué SÍ está habilitado."""
    flags = current_user.tenant.flags
    datos = {
        "voz_web": bool(flags.get(FLAG_VOICE_WEB, False)),
        "voz_telefonica": bool(flags.get(FLAG_VOICE_TELEPHONY, False)),
        "clonacion_voz": bool(flags.get(FLAG_VOICE_CLONING, False)),
    }
    habilitadas = [
        nombre
        for nombre, activo in (
            ("voz web", datos["voz_web"]),
            ("telefonía", datos["voz_telefonica"]),
            ("clonación de voz", datos["clonacion_voz"]),
        )
        if activo
    ]
    mensaje = (
        f"Habilitado en tu plan: {', '.join(habilitadas)}."
        if habilitadas
        else "La voz no está habilitada en tu plan."
    )
    mensaje += " Pruébala en Ajustes > Voz."
    return {"ok": True, "comando": "voice", "mensaje": mensaje, "data": datos}


@advanced_router.get("/commands")
async def get_commands(
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Registro completo de comandos (``ide_comandos.listar_comandos``) más el
    texto de `/help` ya generado. Se pide UNA vez por sesión de IDE (no por
    tecla): el filtrado por prefijo mientras se escribe "/algo" lo hace el
    frontend sobre esta misma lista, sin ida y vuelta al companion en cada
    tecla."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(companion_manager, current_user.tenant_id, "ide_command_list", {})


@advanced_router.post("/commands/execute")
async def post_command_execute(
    request: Request,
    body: CommandExecuteIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Resuelve y ejecuta un comando "/" completo (texto tal cual se escribió,
    p. ej. `"/rename Nuevo título"`).

    El resultado siempre llega con 200: un comando desconocido (con
    `sugerencia`), uno destructivo sin `confirmed` (con
    `requiere_confirmacion`) o uno que falló por datos inválidos son
    resultados NORMALES de este endpoint (`ok: false` adentro del cuerpo),
    no errores HTTP -- ver el docstring de `_send_or_error` sobre qué sí
    dispara un código de error real (companion caído o timeout).

    `/usage`, `/memory`, `/mcp` y `/voice` se resuelven ACÁ MISMO, con datos
    reales del tenant, sin llegar a tocar el companion (ni siquiera hace
    falta que esté conectado) -- ver el comentario arriba de esta sección
    sobre por qué esos cuatro son intrínsecamente server-side.
    """
    _require_companion_ide(current_user.tenant)

    nombre_comando = _slash_command_name(body.text)
    if nombre_comando == "usage":
        return await _ejecutar_usage_servidor(current_user, repo)
    if nombre_comando == "memory":
        return await _ejecutar_memory_servidor(
            current_user, repo, _slash_command_args(body.text)
        )
    if nombre_comando == "mcp":
        return await _ejecutar_mcp_servidor(request, current_user)
    if nombre_comando == "voice":
        return _ejecutar_voice_servidor(current_user)

    params: dict[str, Any] = {"text": body.text, "confirmed": body.confirmed}
    if body.workspace_id:
        params["workspace_id"] = body.workspace_id
    if body.conversation_id:
        params["conversation_id"] = body.conversation_id
    if body.project_id:
        params["project_id"] = body.project_id
    if body.session_id:
        params["session_id"] = body.session_id
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_command_execute",
        params,
        timeout=IDE_APPROVAL_TIMEOUT_SECONDS,
    )


# ---------------------------------------------------------------------------
# Proyectos y conversaciones: agrupan sesiones de agente bajo una carpeta
# (Forge Studio, estilo "proyectos como carpetas" de Antigravity). El registro
# vive en el companion (``edecan_companion.ide_projects.ProjectRegistry``),
# junto a ``ide-workspaces.json``/``ide-sessions.json``. Un ``conversation_id``
# creado aquí es el mismo valor que se manda como ``conversation_id`` en
# ``POST /v1/ide/agents``: la fase siguiente (UI) es la que conecta ambos.
# ---------------------------------------------------------------------------


@advanced_router.get("/projects")
async def get_projects(
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(companion_manager, current_user.tenant_id, "ide_project_list", {})


@advanced_router.post("/projects")
async def post_project(
    body: ProjectCreateIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    result = await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_project_create",
        {"name": body.name, "workspace_id": body.workspace_id},
    )
    return dict(result.get("project") or {})


@advanced_router.post("/projects/{project_id}/rename")
async def post_project_rename(
    project_id: str,
    body: ProjectRenameIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    result = await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_project_rename",
        {"project_id": project_id, "name": body.name},
    )
    return dict(result.get("project") or {})


@advanced_router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    conversations: Literal["keep", "delete"] = Query(default="keep"),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """``conversations`` es la respuesta explícita de la persona a "¿qué hago
    con sus conversaciones?": ``keep`` (default) las desasigna a "sin
    proyecto", ``delete`` las borra también. Ninguna opción toca la carpeta
    del repo en disco -- solo el registro del IDE."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_project_delete",
        {"project_id": project_id, "conversations": conversations},
        timeout=IDE_GIT_MUTATION_TIMEOUT_SECONDS,
    )


@advanced_router.get("/conversations")
async def get_conversations(
    project_id: str | None = Query(default=None),
    only_unassigned: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Sin filtros devuelve todas; ``project_id`` filtra por proyecto;
    ``only_unassigned`` devuelve solo las que no cuelgan de ningún proyecto
    ("sin proyecto" en la barra lateral). ``project_id`` se ignora si
    ``only_unassigned`` es ``true``."""
    _require_companion_ide(current_user.tenant)
    params: dict[str, Any] = {"only_unassigned": only_unassigned}
    if project_id is not None:
        params["project_id"] = project_id
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_conversation_list", params
    )


@advanced_router.post("/conversations")
async def post_conversation(
    body: ConversationCreateIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    params: dict[str, Any] = {}
    if body.project_id is not None:
        params["project_id"] = body.project_id
    if body.title is not None:
        params["title"] = body.title
    result = await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_conversation_create", params
    )
    return dict(result.get("conversation") or {})


@advanced_router.post("/conversations/{conversation_id}/rename")
async def post_conversation_rename(
    conversation_id: str,
    body: ConversationRenameIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    result = await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_conversation_rename",
        {"conversation_id": conversation_id, "title": body.title},
    )
    return dict(result.get("conversation") or {})


@advanced_router.post("/conversations/{conversation_id}/move")
async def post_conversation_move(
    conversation_id: str,
    body: ConversationMoveIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Mueve la conversación a otro proyecto, o la desasigna con
    ``{"project_id": null}``."""
    _require_companion_ide(current_user.tenant)
    result = await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_conversation_move",
        {"conversation_id": conversation_id, "project_id": body.project_id},
    )
    return dict(result.get("conversation") or {})


@advanced_router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Borra el registro de la conversación y cancela (mejor esfuerzo)
    cualquier sesión de agente todavía corriendo para ella."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_conversation_delete",
        {"conversation_id": conversation_id},
        timeout=IDE_GIT_MUTATION_TIMEOUT_SECONDS,
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
    return await _send_or_error(companion_manager, current_user.tenant_id, "ide_agent_list", params)


@advanced_router.get("/models")
async def get_models(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return {"models": modelos_ide_disponibles()}


@advanced_router.post("/agents")
async def post_agent(
    request: Request,
    body: AgentStartIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
    session: AsyncSession = Depends(get_tenant_session),
    llm_router: LLMRouter = Depends(get_llm_router),
) -> dict[str, Any]:
    """Manda un mensaje a una conversación del IDE (la abre si no existía).

    Con el agente ocupado esto ya NO es un error: el mensaje se encola y se le
    entrega al turno en curso entre dos vueltas de su ciclo de herramientas
    (ver `edecan_companion.ide_sessions`). La respuesta es 200 con la sesión de
    siempre más un objeto `queued` con la posición. Sigue habiendo 422 en los
    dos casos en que el companion dice que no: la cola llena, y un plan
    esperando aprobación en esa conversación (ahí lo que destraba es
    aprobar/editar/rechazar el plan, no otro mensaje).
    """
    _require_companion_ide(current_user.tenant)
    if body.model is not None and not modelo_ide_permitido(body.model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Ese modelo no está habilitado para Forge Studio.",
        )
    summaries = await list_skills(
        session, current_user.tenant_id, current_user.user_id
    )
    skill_sections: list[str] = []
    for summary in summaries:
        if not summary.get("enabled"):
            continue
        detail = await get_skill_by_id(
            session, current_user.tenant_id, summary["id"]
        )
        if detail is None:
            continue
        skill_sections.append(
            "\n".join(
                [
                    f"## {detail.get('nombre') or detail.get('slug')}",
                    str(detail.get("descripcion") or ""),
                    str(detail.get("contenido") or ""),
                ]
            )
        )
        if sum(len(section) for section in skill_sections) >= 100_000:
            break
    params = body.model_dump(exclude_none=True)
    if body.conversation_id is None:
        fallback_title = _automatic_conversation_title(
            body.prompt, fallback="Sesión de ingeniería"
        )
        params["title"] = await _semantic_conversation_title(
            llm_router,
            body.prompt,
            fallback=fallback_title,
        )
    if skill_sections:
        params["skill_context"] = "\n\n".join(skill_sections)[:120_000]
    mcp_tools = await get_mcp_tools_for_tenant(request, current_user)
    if mcp_tools:
        params["mcp_tools"] = [
            {
                "name": str(tool.name),
                "description": str(tool.description),
                "input_schema": dict(tool.input_schema),
            }
            for tool in mcp_tools
            if str(getattr(tool, "name", "")).startswith("mcp_")
            and isinstance(getattr(tool, "input_schema", None), dict)
        ]
    result = await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_agent_start",
        params,
        timeout=IDE_APPROVAL_TIMEOUT_SECONDS,
    )
    payload = dict(result.get("session") or {})
    encolado = result.get("queued")
    if isinstance(encolado, dict):
        # Mensaje aceptado MIENTRAS el agente trabajaba (`ide_sessions.
        # SessionManager._encolar_mensaje_para_turno_vivo`): no es un error y
        # no es un turno nuevo -- el turno en curso sigue intacto y el mensaje
        # se le entrega entre dos vueltas de su ciclo de herramientas. Se
        # responde 200 con la posición en la cola para que la interfaz pueda
        # decir "en cola (2 de 5)" en vez de dejar a la persona sin señal, que
        # es lo que hacía que reenviara el mismo mensaje una y otra vez.
        # Ausente en una respuesta normal: quien lee `status` sigue viendo lo
        # mismo de siempre.
        payload["queued"] = {
            "id": str(encolado.get("id") or ""),
            "position": int(encolado.get("position") or 0),
            "pending": int(encolado.get("pending") or 0),
            "max_pending": int(encolado.get("max_pending") or 0),
            "queued_at": str(encolado.get("queued_at") or ""),
        }
    return payload


@advanced_router.post("/agents/{session_id}/mcp/{call_id}/confirm")
async def confirm_agent_mcp(
    request: Request,
    session_id: str,
    call_id: str,
    body: AgentMCPConfirmationIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Confirma una invocación MCP concreta solicitada por el agente IDE.

    Las tools MCP son siempre peligrosas por diseño. El companion pausa el
    turno, esta ruta vuelve a resolver las tools del tenant y ejecuta
    exclusivamente la tool cuyo nombre exacto vio la persona. Así el modelo
    nunca puede saltarse la confirmación ni inyectar una tool/configuración
    distinta entre discovery y ejecución.
    """
    _require_companion_ide(current_user.tenant)
    pending = await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_agent_mcp_pending",
        {"session_id": session_id, "call_id": call_id},
    )
    if not body.approved:
        result = {
            "ok": False,
            "error": "La persona no autorizó esta acción MCP.",
        }
    else:
        tools = await get_mcp_tools_for_tenant(request, current_user)
        requested_name = str(pending.get("name") or "")
        selected = next(
            (tool for tool in tools if str(getattr(tool, "name", "")) == requested_name),
            None,
        )
        if selected is None:
            result = {
                "ok": False,
                "error": "La herramienta MCP ya no está disponible para esta cuenta.",
            }
        else:
            arguments = pending.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            tool_result = await selected.run(None, arguments)
            result = {
                "ok": True,
                "content": getattr(tool_result, "content", tool_result),
            }
    await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_agent_mcp_resolve",
        {
            "session_id": session_id,
            "call_id": call_id,
            "result": result,
        },
    )
    return {"accepted": True, "approved": body.approved}


@advanced_router.get("/agents/{session_id}")
async def get_agent(
    session_id: str,
    cursor: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    _require_companion_ide(current_user.tenant)
    return _sanitize_agent_events(
        await _send_or_error(
            companion_manager,
            current_user.tenant_id,
            "ide_agent_read",
            {"session_id": session_id, "cursor": cursor},
        )
    )


@advanced_router.get("/agents/{session_id}/diff")
async def get_agent_diff(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Archivos tocados en el último turno de este agente, con contenido
    "antes"/"después" para `DiffReview.tsx` (1.2 del plan de paridad)."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_agent_diff", {"session_id": session_id}
    )


@advanced_router.post("/agents/{session_id}/diff/reject")
async def post_agent_diff_reject(
    session_id: str,
    body: AgentDiffRejectIn,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Rechaza (deshace) un solo archivo del último turno, restaurándolo
    desde su punto de control. "Aceptar" no llama al backend: el archivo ya
    quedó como lo dejó el agente, no hay nada que restaurar."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_agent_diff_reject",
        {"session_id": session_id, "path": body.path},
        timeout=IDE_APPROVAL_TIMEOUT_SECONDS,
    )


@advanced_router.get("/agents/{session_id}/cost")
async def get_agent_cost(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Contabilidad de costo (`ide_costos.analizar_tarea`) del último turno:
    acciones, tokens (estimados salvo que el proveedor reporte uso real),
    bucles detectados y comparación contra tareas parecidas."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_agent_cost", {"session_id": session_id}
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
    return await _send_or_error(companion_manager, current_user.tenant_id, "ide_git_diff", params)


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
