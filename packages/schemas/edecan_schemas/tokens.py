"""`TokenBundle` — credenciales OAuth (u otro tipo) de un conector (§10.5)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TokenBundle(BaseModel):
    """Paquete de credenciales que `edecan_db.vault.TokenVault` cifra y guarda.

    Para conectores OAuth, `access_token`/`refresh_token` son los tokens tal
    cual. Para Twilio (ver ARCHITECTURE.md §10.10), `access_token` guarda el
    Auth Token y `scopes` guarda `[ACCOUNT_SID]`.
    """

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    token_type: str = "bearer"
    # Metadatos NO secretos por conector (se cifran igual con el resto del
    # bundle). LinkedIn lo usa para `organization_urns`: los URN de las páginas
    # que el dueño administra, capturados en el callback OAuth cuando autorizó
    # los scopes de organización, para poder publicar COMO la página. Default
    # vacío -> compatible hacia atrás con bundles ya guardados.
    extra: dict = Field(default_factory=dict)
