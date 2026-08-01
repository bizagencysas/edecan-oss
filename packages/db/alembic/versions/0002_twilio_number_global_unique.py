"""0002_twilio_number_global_unique

Cierra un hueco de aislamiento multi-tenant (ver `edecan_db.models.ConnectorAccount`
para el detalle completo): `connector_accounts` solo tenía
`UNIQUE(tenant_id, connector_key, external_account_id)` — evita duplicados
DENTRO de un mismo tenant, pero no impedía que dos tenants DISTINTOS
registraran el mismo número E.164 de Twilio, algo que en la realidad no
puede pasar (un número de Twilio pertenece a una sola cuenta de Twilio a la
vez). `edecan_api.routers.connectors.connect_twilio` ya verifica propiedad
contra la API real de Twilio antes de insertar y hace un chequeo aplicativo
previo (ambos son la defensa primaria, con mensajes de error claros); este
índice único parcial es el respaldo atómico a nivel de base de datos ante una
carrera entre dos requests concurrentes.

Parcial (no un `UniqueConstraint` de la sola columna `external_account_id`)
porque solo aplica a `connector_key = 'twilio'`: los demás conectores (OAuth)
no tienen esa garantía de unicidad real en `external_account_id` (es un hash
del access token, ver `_bundle_account_hint`).

Revision ID: 0002_twilio_number_global_unique
Revises: 0001_initial
Create Date: 2026-07-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_twilio_number_global_unique"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "uq_connector_accounts_twilio_external_account_id"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "connector_accounts",
        ["external_account_id"],
        unique=True,
        postgresql_where=sa.text("connector_key = 'twilio'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="connector_accounts")
