"""Contratos de creación de artefactos privados desde una sola solicitud."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ArtifactKind = Literal["post", "docx", "pdf", "pptx", "website", "app"]
ArtifactStatus = Literal["created", "failed"]
CreationStatus = Literal["completed", "partial", "failed"]


class CreationDeliverable(BaseModel):
    """Salida concreta decidida por el planner determinista."""

    kind: ArtifactKind
    title: str = Field(min_length=1, max_length=300)


class CreationPlan(BaseModel):
    """Plan estable: una solicitud puede producir varios artefactos reales."""

    request: str = Field(min_length=1, max_length=20_000)
    title: str = Field(min_length=1, max_length=300)
    deliverables: list[CreationDeliverable] = Field(min_length=1, max_length=6)


class ArtifactEvidence(BaseModel):
    """Evidencia reproducible de un archivo generado."""

    kind: ArtifactKind | Literal["manifest"]
    status: ArtifactStatus
    filename: str
    mime: str
    relative_path: str
    size_bytes: int = Field(ge=0)
    sha256: str | None = None
    validation: str
    file_id: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreationManifest(BaseModel):
    """Manifiesto persistible de una ejecución del creador universal."""

    version: Literal[1] = 1
    creation_id: str
    tenant_id: str
    user_id: str
    created_at: str
    status: CreationStatus
    request: str
    title: str
    content_source: Literal["provided", "llm", "deterministic_fallback"]
    plan: CreationPlan
    artifacts: list[ArtifactEvidence]
