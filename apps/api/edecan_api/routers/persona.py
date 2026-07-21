"""`GET|PUT /v1/persona`, `GET /v1/persona/preview` (ARCHITECTURE.md §10.12, §10.5)."""

from __future__ import annotations

from typing import Any

from edecan_core.persona import build_system_prompt
from edecan_schemas import PersonaConfig, RelationshipStyle
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from edecan_api.deps import CurrentUser, get_current_user, get_repo, rate_limit
from edecan_api.repo import Repo

router = APIRouter(prefix="/v1/persona", tags=["persona"], dependencies=[Depends(rate_limit)])


class PersonaIn(BaseModel):
    """Cuerpo de `PUT /v1/persona` — todos los campos opcionales (patch parcial)."""

    nombre_asistente: str | None = None
    idioma: str | None = None
    tono: str | None = None
    formalidad: int | None = Field(default=None, ge=0, le=3)
    emojis: bool | None = None
    instrucciones: str | None = None
    rasgos: list[str] | None = None
    memoria_activada: bool | None = None
    voice_id: str | None = None
    estilo_relacion: RelationshipStyle | None = None
    adulto_confirmado: bool | None = None
    consentimiento_romantico: bool | None = None

    @model_validator(mode="after")
    def validar_cambio_de_estilo(self) -> PersonaIn:
        if self.estilo_relacion == "romantico":
            if self.adulto_confirmado is not True or self.consentimiento_romantico is not True:
                raise ValueError(
                    "El estilo romántico requiere confirmar mayoría de edad "
                    "y consentimiento explícito."
                )
        elif self.estilo_relacion is not None and (
            self.adulto_confirmado is True or self.consentimiento_romantico is True
        ):
            raise ValueError("Las confirmaciones románticas solo aplican al estilo romántico.")
        elif self.estilo_relacion is None and (
            self.adulto_confirmado is True or self.consentimiento_romantico is True
        ):
            raise ValueError(
                "Para activar el estilo romántico envía el estilo y ambas confirmaciones juntas."
            )
        return self


def persona_from_row(row: dict[str, Any] | None) -> PersonaConfig:
    if row is None:
        return PersonaConfig()
    return PersonaConfig(
        nombre_asistente=row.get("nombre_asistente") or "Edecán",
        idioma=row.get("idioma") or "es",
        tono=row.get("tono") or "cálido y profesional",
        formalidad=row.get("formalidad", 1),
        emojis=bool(row.get("emojis", False)),
        instrucciones=row.get("instrucciones") or "",
        rasgos=list(row.get("rasgos") or []),
        memoria_activada=bool(row.get("memoria_activada", True)),
        voice_id=row.get("voice_id"),
        estilo_relacion=row.get("estilo_relacion") or "profesional",
        adulto_confirmado=bool(row.get("adulto_confirmado", False)),
        consentimiento_romantico=bool(row.get("consentimiento_romantico", False)),
    )


@router.get("", response_model=PersonaConfig)
async def get_persona(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> PersonaConfig:
    row = await repo.get_persona(tenant_id=current_user.tenant_id, user_id=current_user.user_id)
    return persona_from_row(row)


@router.put("", response_model=PersonaConfig)
async def put_persona(
    body: PersonaIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> PersonaConfig:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if body.estilo_relacion is not None:
        if body.estilo_relacion != "romantico":
            fields["adulto_confirmado"] = False
            fields["consentimiento_romantico"] = False
    elif body.adulto_confirmado is False or body.consentimiento_romantico is False:
        # Desmarcar cualquiera de las dos confirmaciones es una salida
        # inmediata y segura, incluso si el cliente no envió el estilo.
        fields.update(
            estilo_relacion="profesional",
            adulto_confirmado=False,
            consentimiento_romantico=False,
        )
    row = await repo.upsert_persona(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id, fields=fields
    )
    return persona_from_row(row)


@router.get("/preview")
async def preview_persona(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, str]:
    """Vista previa del system prompt, sin memorias (`edecan_core.persona.build_system_prompt`)."""
    row = await repo.get_persona(tenant_id=current_user.tenant_id, user_id=current_user.user_id)
    persona = persona_from_row(row)
    prompt = build_system_prompt(persona, memories=[])
    return {"system_prompt": prompt}
