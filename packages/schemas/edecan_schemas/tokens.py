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
