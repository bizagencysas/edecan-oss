"""`GET|PUT /v1/persona`, `GET /v1/persona/preview` (ARCHITECTURE.md §10.12, §10.5)."""

from __future__ import annotations

from typing import Any

from edecan_core.persona import build_system_prompt
from edecan_schemas import PersonaConfig
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

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
