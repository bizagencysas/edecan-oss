"""`/v1/preparacion` — pantalla de preparación de requisitos de Windows.

Antes de que la app de escritorio termine de abrir en Windows, si falta algo
del sistema (piso de soporte de `docs/edecan-windows.md` §9/§10: versión de
Windows, PowerShell 7, rutas largas del registro, WebView2 Runtime, Git),
esta pantalla lo lista y deja instalarlo con un botón ▶. El manifiesto de
requisitos y sus comandos fijos viven en `edecan_companion.preparacion` — ver
la regla de seguridad en el docstring de ese módulo: el cliente de este
router SOLO puede pedir "instala el requisito `X`" por su `id`, nunca mandar
un comando ni un argv. El companion resuelve ese `id` contra la lista fija
`REQUISITOS`; uno que no está ahí se rechaza (422) antes de tocar el sistema.

Mismo estilo que `routers/ide.py`: cada endpoint arma `{action, params}` y lo
manda con `ConnectionManager.send_command(...)`, traduciendo la respuesta a
HTTP con `_send_or_error`. Se duplica localmente en vez de importar el
privado de `ide.py` — mismo criterio que ya usa `routers/remote.py`, el otro
router que también habla con el companion.

En cualquier plataforma que no sea Windows, `GET /v1/preparacion` devuelve
`{"requisitos": [], "elevado": false}` (el companion ya filtra por
`os.name` dentro de `edecan_companion.preparacion.detectar`): la pantalla
nunca debe aparecer en macOS ni Linux.
"""

from __future__ import annotations

import uuid
from typing import Any

from edecan_schemas.plans import FLAG_COMPANION_IDE
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from edecan_api.companion_manager import CompanionError, ConnectionManager
from edecan_api.deps import CurrentUser, TenantCtx, get_current_user
from edecan_api.ide_security import require_paired_ide_device

router = APIRouter(
    prefix="/v1/preparacion",
    tags=["preparacion"],
    dependencies=[Depends(require_paired_ide_device)],
)

# Instalar con winget puede tardar bastante más que una consulta de estado
# (descarga real + instalación), pero el propio endpoint SOLO arranca el
# proceso y devuelve de inmediato (ver `EjecutorPreparacion.instalar`, que
# lanza un hilo lector y no espera a que termine) — mismo margen que
# `IDE_APPROVAL_TIMEOUT_SECONDS` en `ide.py` para la ida y vuelta con el
# companion, no para la instalación completa (esa se seguirá con
# `GET /v1/preparacion/{id}`, que sí es barato de repetir).
PREPARACION_TIMEOUT_SECONDS = 70.0


def _require_companion_ide(tenant: TenantCtx) -> None:
    """Mismo gate que `routers.ide._require_companion_ide` (flag de plan
    `companion.ide`, `True` en los 4 planes reales hoy) — duplicado a
    propósito, ver el docstring del módulo."""
    if not tenant.flags.get(FLAG_COMPANION_IDE, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El IDE embebido no está disponible en tu plan.",
        )


def get_companion_manager(request: Request) -> ConnectionManager:
    """`app.state.companion_manager` — mismo patrón que `routers.ide.get_companion_manager`."""
    return request.app.state.companion_manager


async def _send_or_error(
    companion_manager: ConnectionManager,
    tenant_id: uuid.UUID,
    action: str,
    params: dict[str, Any],
    *,
    timeout: float = PREPARACION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Envía `{action, params}` al companion del tenant y traduce su respuesta
    a HTTP — copia local de `routers.ide._send_or_error` (ver su docstring
    para el porqué de cada código: 503 sin companion, 504 sin respuesta a
    tiempo, 422 cuando el companion dice `{"ok": false, ...}` — que es
    exactamente lo que responde ante un `id` de requisito desconocido)."""
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


@router.get("")
async def get_preparacion(
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Estado de cada requisito del manifiesto, sin instalar nada.

    Forma: `{"requisitos": [{"id", "nombre", "por_que", "estado", "instalable",
    "requiere_admin", "obligatorio"}, ...], "elevado": bool}`. `elevado` es si
    ESTE proceso corre con permisos de administrador — así la pantalla puede
    marcar "esto necesita administrador y no lo tienes" antes de que la
    persona pulse play, no después de que falle a medias.
    """
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager, current_user.tenant_id, "ide_preparacion_list", {}
    )


@router.post("/{requisito_id}/instalar")
async def post_preparacion_instalar(
    requisito_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Ejecuta ÚNICAMENTE el `instalar` fijo del requisito `requisito_id`.

    Un `id` que no está en `edecan_companion.preparacion.REQUISITOS` nunca
    llega a `pty_compat`: el companion lo rechaza con `PreparacionError`
    (`ValueError`) antes de tocar el sistema, y este endpoint lo traduce a
    422 — el mismo camino de error que cualquier otra acción IDE rechazada.
    Idempotente: si ya hay una instalación en curso para este `id`, devuelve
    su estado actual en vez de arrancar una segunda.
    """
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_preparacion_instalar",
        {"id": requisito_id},
    )


@router.get("/{requisito_id}")
async def get_preparacion_leer(
    requisito_id: str,
    cursor: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    companion_manager: ConnectionManager = Depends(get_companion_manager),
) -> dict[str, Any]:
    """Salida incremental de una instalación — mismo patrón de cursor que
    `GET /v1/ide/terminals/{session_id}` (`events` + `next_cursor` + `has_more`)."""
    _require_companion_ide(current_user.tenant)
    return await _send_or_error(
        companion_manager,
        current_user.tenant_id,
        "ide_preparacion_leer",
        {"id": requisito_id, "cursor": cursor},
    )
