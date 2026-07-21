"""Contratos de dominio compartidos: tenants, usuarios y configuración de persona."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

RelationshipStyle = Literal["profesional", "coach", "amigo", "romantico"]
RELATIONSHIP_STYLES: tuple[RelationshipStyle, ...] = (
    "profesional",
    "coach",
    "amigo",
    "romantico",
)


class TenantOut(BaseModel):
    """Representación pública de un tenant (tabla `tenants`, ARCHITECTURE.md §10.3)."""

    id: UUID
    name: str
    slug: str
    plan_key: str
    status: str
    created_at: datetime


class UserOut(BaseModel):
    """Representación pública de un usuario (tabla `users`).

    Nunca incluye `password_hash` ni `totp_secret`.
    """

    id: UUID
    email: str
    is_superadmin: bool = False
    created_at: datetime


class PersonaConfig(BaseModel):
    """Configuración "nivel Dios" del asistente de un tenant/usuario (§10.5).

    `formalidad` va de 0 (tú, muy informal) a 3 (usted, muy formal) — ver
    `edecan_core.persona.build_system_prompt`.
    """

    nombre_asistente: str = "Edecán"
    idioma: str = "es"
    tono: str = "cálido y profesional"
    formalidad: int = Field(default=1, ge=0, le=3)
    emojis: bool = False
    instrucciones: str = ""
    rasgos: list[str] = Field(default_factory=list)
    memoria_activada: bool = True
    voice_id: str | None = None
    estilo_relacion: RelationshipStyle = "profesional"
    adulto_confirmado: bool = False
    consentimiento_romantico: bool = False

    @model_validator(mode="after")
    def validar_estilo_relacion(self) -> PersonaConfig:
        """Impide inferir consentimiento desde memorias o texto libre.

        Fuera del estilo romántico no conservamos las confirmaciones. Así,
        salir es inmediato y un consentimiento antiguo no puede reactivar el
        modo accidentalmente.
        """
        if self.estilo_relacion == "romantico":
            if not (self.adulto_confirmado and self.consentimiento_romantico):
                raise ValueError(
                    "El estilo romántico requiere confirmar mayoría de edad "
                    "y consentimiento explícito."
                )
        elif self.adulto_confirmado or self.consentimiento_romantico:
            raise ValueError(
                "Las confirmaciones románticas solo se guardan mientras "
                "el estilo romántico está activo."
            )
        return self
