"""0010_tenant_lifetime_updates

Suma `tenants.lifetime_updates_purchased_at` (nullable, sin default):
marca si este tenant pagó el tier "código + actualizaciones de por vida"
($199) en vez del tier base "código completo" ($99) — nuevo modelo de
precio de pago único, reemplaza el sistema de planes por suscripción
mensual (`packages/schemas/edecan_schemas/plans.py`, que ahora concede
TODOS los flags/límites en las 4 entradas de `PLANES` por igual — se
mantienen los 4 `plan_key` existentes por compatibilidad con tests/datos
ya sembrados, pero ninguno restringe nada; el único campo de negocio real
que distingue tiers de ahora en más es esta columna).

`GET /v1/setup/status` expone `lifetime_updates` (`true` si esta columna
no es NULL) para que la app de escritorio decida si puede chequear
actualizaciones. `apps/api/edecan_api/routers/billing.py` la fija desde el
webhook de Stripe cuando el Checkout Session es de pago único (`mode=
"payment"`) para el tier de $199.

Revision ID: 0010_tenant_lifetime_updates
Revises: 0009_tenant_onboarding_completed
Create Date: 2026-07-09 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010_tenant_lifetime_updates"
down_revision: str | None = "0009_tenant_onboarding_completed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "lifetime_updates_purchased_at", postgresql.TIMESTAMP(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "lifetime_updates_purchased_at")
