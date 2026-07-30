"""Consentimiento verificable del destinatario para telefonía/SMS.

Es parte del OSS y se monta siempre. No autoriza una llamada por sí solo: una
saliente además necesita la confirmación puntual del usuario sobre destino y
objetivo.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from edecan_schemas.plans import FLAG_VOICE_TELEPHONY
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from edecan_api.deps import CurrentUser, TenantCtx, get_current_user, get_repo, rate_limit
from edecan_api.repo import Repo

router = APIRouter(prefix="/v1/consents", tags=["consents"], dependencies=[Depends(rate_limit)])

# Mismo patrón que `edecan_api.routers.connectors._E164_RE`: "+" y 2-15 dígitos,
# el primero distinto de cero. Este endpoint rechaza un `phone_e164` mal
# formado antes de persistirlo.
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
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Otorga consentimiento (`kind`: `"sms"`|`"voice"`) para `phone_e164`.

    Valida formato y fuente de evidencia; luego persiste el consentimiento y
    una fila de auditoría mediante el repositorio OSS del tenant.
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

    if not source:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source es obligatorio y debe describir la evidencia de consentimiento.",
        )

    await repo.grant_phone_consent(
        tenant_id=current_user.tenant_id,
        phone_e164=phone_e164,
        kind=body.kind,
        source=source,
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="phone.consent_granted",
        target=phone_e164,
        meta={"kind": body.kind, "source": source},
    )

    return {"phone_e164": phone_e164, "kind": body.kind, "source": source}
