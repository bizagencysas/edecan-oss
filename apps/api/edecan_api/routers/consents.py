"""`POST /v1/consents` (ARCHITECTURE.md §10.10, §10.12; `docs/voz-telefonia.md`,
control #1 del checklist legal; `RIESGOS.md`, sección "Legales y cumplimiento").

Único punto de entrada auditado del producto hacia
`edecan_premium.compliance.grant_consent` — antes de este router, esa función
(la única que debe usarse para insertar una fila en `consents`) no tenía
ningún invocador: la tabla solo podía poblarse escribiendo SQL directo por
fuera del sistema, así que `llamar_contacto`/`enviar_sms`/`lanzar_campana`
(`edecan_premium.tools`) y `run_campaign_step` (`edecan_premium.campaigns`)
siempre fallaban el chequeo de `has_consent` para cualquier destinatario
real. Ver el "Estado operativo" que documentaba el propio
`compliance.grant_consent` antes de este cambio.

Vive en `apps/api` (no en `premium/`) porque necesita la autenticación
JWT/tenant de `edecan_api.deps` (`get_current_user`, `get_tenant_session`,
`rate_limit`), que `edecan_premium` no puede importar sin invertir la capa de
dependencias del monorepo (ARCHITECTURE.md §6/§10.1: `apps/*` depende de
`premium/`, nunca al revés) — el mismo motivo por el que
`edecan_premium.twilio_router` recibe sus colaboradores vía `app.state` en
vez de importar `edecan_api`. Por simetría con ese router, `edecan_api.main`
solo importa y monta este módulo si `edecan_premium` está instalado
(`importlib.util.find_spec`), así que el import de `edecan_premium.compliance`
de aquí abajo nunca corre en un core sin `premium/` — el open-core sigue sin
depender de él (ver `edecan_premium/__init__.py`: "Importa el submódulo
concreto que necesites, p. ej. `from edecan_premium import compliance`").

No es una `Tool` de agente: quien llama a este endpoint es el usuario
autenticado del tenant (vía el panel u otro cliente HTTP con su propio
`Authorization: Bearer`), así que la autenticación normal YA es la
confirmación humana — no aplica el flujo `dangerous=True` de
`edecan_core.tools` (pensado para que un LLM decida invocar una herramienta
de forma autónoma dentro de un turno, potencialmente sobre contenido no
confiable). Ver el docstring de `compliance.grant_consent` para el
razonamiento completo, incluida la advertencia de por qué SÍ debería llevar
ese flujo si en el futuro se expone como tool agent-facing.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from edecan_premium import compliance
from edecan_schemas.plans import FLAG_VOICE_TELEPHONY
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.deps import CurrentUser, TenantCtx, get_current_user, get_tenant_session, rate_limit

router = APIRouter(prefix="/v1/consents", tags=["consents"], dependencies=[Depends(rate_limit)])

# Mismo patrón que `edecan_api.routers.connectors._E164_RE`: "+" y 2-15 dígitos,
# el primero distinto de cero. `compliance.grant_consent` no valida formato
# (solo `kind`/`source`), así que este endpoint es quien debe rechazar un
# `phone_e164` mal formado antes de que llegue a insertarse.
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")


class ConsentIn(BaseModel):
    phone_e164: str = Field(min_length=1, description="Teléfono E.164, p. ej. +525512345678.")
    kind: Literal["sms", "voice"]
    source: str = Field(
        min_length=1,
        description=(
            "Evidencia verificable de cómo se obtuvo el consentimiento, p. ej. "
            "'formulario_web' o 'respuesta_sms:SI'."
        ),
    )


def _require_voice_telephony(tenant: TenantCtx) -> None:
    """Mismo gate que `connectors._require_voice_telephony`: sin el flag de plan
    `voice.telephony`, el tenant no puede operar voz/SMS, así que tampoco tiene
    sentido dejarlo poblar `consents` (ARCHITECTURE.md §10.13)."""
    if not tenant.flags.get(FLAG_VOICE_TELEPHONY, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La telefonía (Twilio) no está disponible en tu plan.",
        )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_consent(
    body: ConsentIn,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Otorga consentimiento (`kind`: `"sms"`|`"voice"`) para `phone_e164`.

    Delega toda la validación y la escritura (incluida la fila de
    `audit_log`) a `compliance.grant_consent` — este endpoint es
    deliberadamente un paso directo (auth + gate de plan + traducir
    `ValueError` a 400), no una reimplementación.
    """
    _require_voice_telephony(current_user.tenant)

    phone_e164 = body.phone_e164.strip()
    source = body.source.strip()
    if not phone_e164:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="phone_e164 es obligatorio."
        )
    if not _E164_RE.match(phone_e164):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Número de teléfono inválido: usa formato E.164, p. ej. +525512345678.",
        )

    try:
        await compliance.grant_consent(
            session, current_user.tenant_id, phone_e164, body.kind, source
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"phone_e164": phone_e164, "kind": body.kind, "source": source}
