"""`POST /v1/hooks/{automation_id}` — disparo público de una automatización de
tipo webhook (ARCHITECTURE.md §10.12; ROADMAP_V2.md §7.4, §7.6, dueño WP-V2-07).

**Endpoint público, SIN `Authorization: Bearer`** — quien lo llama es un
sistema externo (Zapier, un cron de terceros, un script del propio usuario),
no un usuario autenticado del tenant. La autenticación es el secreto por
automatización (`trigger.hook_secret`, generado server-side al crear/activar
un trigger webhook en `routers/automations.py`), presentado en el header
`X-Hook-Secret`. Mismo precedente de "endpoint público con verificación
propia" que `edecan_premium.twilio_router` (valida `X-Twilio-Signature` en
vez de un JWT) — pero, a diferencia de ese router (que vive en `premium/` y
por eso recibe sus colaboradores vía `app.state`, ver su docstring), este
módulo vive en `apps/api` y puede importar `edecan_api.deps` directo
(`get_platform_session`, `get_redis`).

`get_platform_session` (= `edecan_db.session.get_session(None)`, rol dueño,
**bypassa RLS**) es obligatorio acá: el `automation_id` de la URL es lo único
que identifica la fila, y no hay ningún JWT que ya traiga el `tenant_id` —
hace falta poder leer la fila de CUALQUIER tenant para, recién después de
verificar el secreto, saber cuál es su dueño (mismo patrón que
`_resolve_tenant_by_number` en `edecan_premium.twilio_router`).

**Todo fallo de autenticación responde 404** (automatización inexistente,
secreto incorrecto, trigger que no es de tipo webhook, o automatización
desactivada) — nunca 401/403: un 403 confirmaría que el `automation_id` SÍ
existe (solo que el secreto está mal), lo que convierte la URL en un oráculo
de enumeración. 404 uniforme para las cuatro causas no le da a un atacante
ninguna señal extra más allá de "esto no funcionó".

No importa `edecan_db.models` (mismo criterio que v1 `edecan_toolkit`/
`edecan_premium`, ver sus README: el contrato pinnea tabla/columna, no una
forma de ORM) ni `edecan_automations` (no hace falta: aquí no se valida
nada, ya se validó al crear la automatización) — solo SQL parametrizado
directo, igual que `routers/automations.py` (ver su docstring).
`edecan_core.queue.enqueue` sí se importa a nivel de módulo (no perezoso):
mismo criterio que `edecan_api.routers.conversations`/`routers/automations.py`
(`edecan_core` es v1, ya estable) — así los tests pueden monkeypatchear el símbolo
`enqueue` ya importado acá.

## `trigger_hook` — commit de evidencia antes del `enqueue`

Mismo guardrail que `HOTFIXES_PENDIENTES.md` puntos 8/9
(`edecan_api.routers.remote.get_frame`, `routers.commerce.confirm_order`,
`routers.ads.confirmar_borrador`, `routers.voz_avanzada.crear_clon_voz`):
`get_platform_session` envuelve TODA la request en una única transacción con
ROLLBACK automático si una excepción se propaga fuera del handler. A
diferencia de esos cuatro (que comitean justo antes de un `raise
HTTPException` explícito), acá el riesgo es `enqueue(...)` — una operación de
red real que puede lanzar (SQS caído, `SQS_QUEUE_URL`/`DATABASE_URL` mal
configurado) sin que el handler la traduzca a un `HTTPException` propio; la
excepción cruda simplemente se propaga. Si eso pasara sin un commit explícito
de por medio, el rollback automático se llevaría puesta la fila de
`audit_log` (`automation.hook_triggered`) recién insertada por
`_audit_hook_triggered` — la única evidencia de que un tercero autenticado
(secreto correcto) disparó este webhook. `trigger_hook` hace `await
session.commit()` inmediatamente después de auditar y ANTES de llamar a
`enqueue` — es la última operación sobre `session` en el handler (ni
`enqueue` ni el `logger.info` final la tocan), así que es seguro sin importar
si lo que sigue termina en éxito o en excepción (ver el docstring de
`voz_avanzada.py::crear_clon_voz` para el detalle verificado empíricamente de
por qué el commit debe ser el último uso de la sesión).
"""

from __future__ import annotations

import hmac
import json
import logging
import time
import uuid
from typing import Any

import redis.asyncio as redis_asyncio
from edecan_core.queue import enqueue
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import get_platform_session, get_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/hooks", tags=["hooks"])

RATE_LIMIT_MAX_PER_MINUTE = 30
RATE_LIMIT_WINDOW_SECONDS = 60

_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Automatización no encontrada."
)


def _from_jsonb(value: Any) -> dict[str, Any]:
    """Mismo gotcha que `routers.automations._from_jsonb` (duplicado a
    propósito: cada router de este WP queda auto-contenido, ver su docstring)."""
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value) if value else {}


async def _load_automation(
    session: AsyncSession, automation_id: uuid.UUID
) -> dict[str, Any] | None:
    """Búsqueda CROSS-TENANT deliberada (sin `WHERE tenant_id = ...`): a esta
    altura todavía no sabemos qué tenant es dueño de `automation_id` — recién
    se determina abajo, después de validar el secreto (ver docstring del
    módulo)."""
    result = await session.execute(
        text("SELECT * FROM automations WHERE id = :id"), {"id": automation_id}
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


def _matching_hook_secret(automation: dict[str, Any], presented: str | None) -> bool:
    trigger = _from_jsonb(automation.get("trigger"))
    if trigger.get("kind") != "webhook":
        return False
    expected = trigger.get("hook_secret")
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented, expected)


async def _check_rate_limit(redis_client: Any, automation_id: uuid.UUID) -> bool:
    """Máx. `RATE_LIMIT_MAX_PER_MINUTE` llamadas por minuto POR automatización
    (no por tenant/IP) — mismo patrón INCR+EXPIRE que `edecan_api.deps.rate_limit`,
    pero con su propia clave/ventana porque este endpoint es público (no hay
    `tenant_id` de un JWT para usar como clave, y de todas formas la unidad
    correcta a limitar acá es "esta automatización", no "quien llama")."""
    window = int(time.time()) // RATE_LIMIT_WINDOW_SECONDS
    key = f"hook_ratelimit:{automation_id}:{window}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    return count <= RATE_LIMIT_MAX_PER_MINUTE


async def _audit_hook_triggered(
    session: AsyncSession, *, tenant_id: uuid.UUID, automation_id: uuid.UUID
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO audit_log (tenant_id, actor_user_id, action, target, meta)
            VALUES (:tenant_id, NULL, 'automation.hook_triggered', :target, CAST(:meta AS jsonb))
            """
        ),
        {
            "tenant_id": tenant_id,
            "target": str(automation_id),
            "meta": json.dumps({"automation_id": str(automation_id)}),
        },
    )


@router.post("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def trigger_hook(
    automation_id: uuid.UUID,
    x_hook_secret: str | None = Header(default=None, alias="X-Hook-Secret"),
    session: AsyncSession = Depends(get_platform_session),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
) -> None:
    automation = await _load_automation(session, automation_id)
    if automation is None:
        raise _NOT_FOUND
    if not _matching_hook_secret(automation, x_hook_secret):
        raise _NOT_FOUND
    if not automation["enabled"]:
        # Mismo 404 uniforme (ver docstring del módulo) — un secreto
        # correcto para una automatización desactivada no debe distinguirse
        # de un secreto incorrecto.
        raise _NOT_FOUND

    if not await _check_rate_limit(redis_client, automation_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiadas solicitudes: límite de {RATE_LIMIT_MAX_PER_MINUTE} por minuto.",
        )

    tenant_id = automation["tenant_id"]
    await _audit_hook_triggered(session, tenant_id=tenant_id, automation_id=automation_id)

    # HOTFIXES_PENDIENTES.md puntos 8/9 (mismo patrón que `remote.py::get_frame`,
    # `commerce.py::confirm_order`, `ads.py::confirmar_borrador`,
    # `voz_avanzada.py::crear_clon_voz`): `get_platform_session` envuelve TODA la
    # request en una única transacción con ROLLBACK automático si una excepción
    # se propaga fuera del handler. `enqueue(...)` de abajo es una operación de
    # red real (SQS `send_message`, o un INSERT `asyncpg` efímero si
    # `QUEUE_PROVIDER="db"`) que puede lanzar (cola caída, `SQS_QUEUE_URL`/
    # `DATABASE_URL` mal configurado, throttling) — sin este commit explícito,
    # ese rollback se llevaría puesta la fila de `audit_log` recién insertada,
    # que es la única evidencia de que un tercero autenticado (secreto
    # correcto) disparó este webhook. Es la ÚLTIMA operación sobre `session` en
    # todo el handler (ni `enqueue` ni `logger.info` la tocan), así que
    # comitear aquí y seguir es seguro — no revienta con `InvalidRequestError`
    # (ver el docstring de `voz_avanzada.py::crear_clon_voz` para el detalle
    # verificado empíricamente de por qué esta regla exige que sea el último uso).
    await session.commit()

    await enqueue(settings, "run_automation", {"automation_id": str(automation_id)}, tenant_id)

    logger.info("hook disparado automation_id=%s tenant_id=%s", automation_id, tenant_id)
