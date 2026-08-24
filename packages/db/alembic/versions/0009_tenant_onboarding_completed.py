"""0009_tenant_onboarding_completed

Suma `tenants.onboarding_completed_at` (nullable, sin default): marca cuándo
un tenant terminó el wizard de primer arranque (`apps/web/.../app/bienvenida`).

Antes de esta migración ese estado vivía SOLO en `localStorage` del
navegador/webview (`edecan_wizard_done`), sin ninguna representación en el
backend — un tenant nuevo en una máquina donde ese flag ya estaba en "1" (por
pruebas previas, otro tenant, etc.) se saltaba el wizard entero sin haber
conectado nada. `GET /v1/setup/status` ahora expone `onboarding_completed`
(true si esta columna no es NULL) y `PUT /v1/setup/complete` la fija — el
frontend consulta/escribe el backend en vez de `localStorage`.

No lleva `server_default`: un tenant recién creado (`SqlRepo.create_tenant`,
INSERT explícito sin listar esta columna) queda en NULL = "todavía no
completó el wizard", que es exactamente el estado inicial deseado.

Revision ID: 0009_tenant_onboarding_completed
Revises: 0008_v6_expansion
Create Date: 2026-07-09 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009_tenant_onboarding_completed"
down_revision: str | None = "0008_v6_expansion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("onboarding_completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "onboarding_completed_at")
