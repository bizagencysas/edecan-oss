"""`/v1/setup/*` — estado del backend y auto-detección de proveedores LLM
locales, para la pantalla de "Configuración"/wizard de primer arranque de la
app de escritorio (`ARCHITECTURE.md` §12.a/§12.d; `DIRECCION_ACTUAL.md`
"Principio de UX no negociable: configuración de pocos clicks"; dueño
WP-V3-05, `apps/local`).

Este router NO se monta a sí mismo: `edecan_api.main` (WP-V3-01) lo monta de
forma defensiva junto al resto de routers v3 (`importlib.import_module` +
`try/except ImportError`, `V3_ROUTER_NAMES` ya incluye `"setup"`) — este
módulo solo declara `router`.

## `GET /v1/setup/status`

Lo que el frontend necesita saber ANTES de decidir qué pantalla mostrar:
`local_mode` (¿corre como app de escritorio o como servidor hospedado?),
`llm_configured` (¿el host configuró Workers AI?) y `version`.

## `PUT /v1/setup/complete`

Marca `tenants.onboarding_completed_at` (migración 0009) — el wizard de
primer arranque llama esto al terminar/saltar el último paso. Reemplaza el
flag `edecan_wizard_done` que antes vivía SOLO en `localStorage` del
navegador/webview (sin ninguna representación en el backend): un tenant
nuevo en una máquina donde ese flag ya estaba en "1" por pruebas previas se
saltaba el wizard entero. `GET /v1/setup/status` expone `onboarding_completed`
para que `register`/`login` decidan `/app` vs `/app/bienvenida` contra el
backend, no contra el navegador.

## `GET /v1/setup/detect`

Delegа en `edecan_llm.detect.detect_local_providers` (WP-V3-03, contrato
pinned en ARCHITECTURE.md §12.d) — pero SOLO si `local_mode` es verdadero.
En un servidor hospedado (`EDECAN_LOCAL_MODE=False`, el default), detectar
proveedores locales no tiene sentido: esos binarios/puertos son de la
máquina del SERVIDOR compartido, no la del cliente que está mirando la
pantalla — y peor, filtraría información de ese host a quien sea que esté
autenticado. Por eso, sin `local_mode`, esta ruta devuelve el shape vacío
tal cual SIN llamar a `detect_local_providers` en absoluto (mismo criterio
que ya aplica la configuración administrada en modo hospedado).

`edecan_llm.config`/`edecan_llm.detect` son el contrato de WP-V3-03, que se
construye EN PARALELO a este WP (ARCHITECTURE.md §12) — import con guardia
(`try/except ImportError`): si todavía no aterrizó, se devuelve el mismo
shape vacío que en modo no-local, en vez de romper la ruta.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, status

from edecan_api import __version__
from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    CurrentUser,
    get_current_user,
    get_repo,
    get_vault,
    rate_limit,
)
from edecan_api.repo import Repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/setup", tags=["setup"], dependencies=[Depends(rate_limit)])

# Shape pinned en ARCHITECTURE.md §12.d — se repite tal cual cuando no hay
# nada que detectar (modo no-local, o `edecan_llm.detect` todavía no
# aterrizó): SIEMPRE las tres claves, nunca se omite ninguna.
_EMPTY_DETECT_SHAPE: dict[str, Any] = {
    "claude_cli": {"installed": False, "path": None, "version": None},
    "ollama": {"running": False, "base_url": "", "models": []},
}


@router.get("/status")
async def get_setup_status(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: Any = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    del vault
    llm_configured = bool(settings.CLOUDFLARE_ACCOUNT_ID and settings.CLOUDFLARE_API_TOKEN)
    tenant = await repo.get_tenant(current_user.tenant_id)
    onboarding_completed = bool(tenant and tenant.get("onboarding_completed_at") is not None)
    lifetime_updates = bool(tenant and tenant.get("lifetime_updates_purchased_at") is not None)
    return {
        "local_mode": bool(getattr(settings, "EDECAN_LOCAL_MODE", False)),
        "llm_configured": llm_configured,
        "onboarding_completed": onboarding_completed,
        "lifetime_updates": lifetime_updates,
        "version": __version__,
    }


@router.put("/complete", status_code=status.HTTP_204_NO_CONTENT)
async def put_setup_complete(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    await repo.update_tenant_onboarding_completed(current_user.tenant_id)


@router.get("/detect")
async def get_setup_detect(
    current_user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    local_mode = bool(getattr(settings, "EDECAN_LOCAL_MODE", False))
    if not local_mode:
        return {"local_mode": False, **_EMPTY_DETECT_SHAPE}

    try:
        from edecan_llm.detect import detect_local_providers
    except ImportError:
        logger.debug(
            "edecan_llm.detect no disponible todavía (WP-V3-03 en paralelo); "
            "GET /v1/setup/detect devuelve el shape vacío."
        )
        return {"local_mode": True, **_EMPTY_DETECT_SHAPE}

    detected = await asyncio.to_thread(detect_local_providers, settings)
    return {"local_mode": True, **detected}
