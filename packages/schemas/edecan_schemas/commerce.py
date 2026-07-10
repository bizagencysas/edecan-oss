"""`OrderOut` — fila pública de `orders` (ROADMAP_V2.md §7.4, §8.1, dueño WP-V2-01;
consumido por WP-V2-10).

GUARDRAIL NO NEGOCIABLE (ROADMAP_V2.md §8.1): "dinero real nunca se mueve
solo". Toda orden nace `status="draft"`; el único camino hacia `confirmed` es
una acción explícita del humano en la UI (`confirmed_at` se llena ahí, nunca
en la creación); y hoy la única ejecución posible es `executed_paper` (broker
simulado) — no existe un estado de ejecución con dinero real en este pinning.
`edecan_commerce.preparar_pago`/`preparar_orden` (§7.7) son `dangerous=True`
precisamente para pasar por el gate de confirmación de `edecan_core.agent`
(ARCHITECTURE.md §10.7) antes de llegar siquiera a `draft`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

ORDER_KINDS: tuple[str, ...] = ("payment", "purchase", "trade")
ORDER_STATUSES: tuple[str, ...] = ("draft", "confirmed", "executed_paper", "cancelled", "expired")

OrderKind = Literal["payment", "purchase", "trade"]
OrderStatus = Literal["draft", "confirmed", "executed_paper", "cancelled", "expired"]


class OrderOut(BaseModel):
    """`monto`/`simbolo`/`lado`/`cantidad` son mutuamente relevantes según
    `kind`: `payment`/`purchase` usan `monto`+`moneda`; `trade` usa
    `simbolo`+`lado` (`"buy"`/`"sell"`, vocabulario de `edecan_commerce`, no
    pinned aquí)+`cantidad`. Ninguno de los dos grupos es `NOT NULL` a nivel
    de esquema porque cuál aplica depende de `kind` (mismo patrón que
    `edecan_db.models` deja columnas nullable cuando el vocabulario completo
    no está pinned, ver su docstring)."""

    id: UUID
    tenant_id: UUID
    user_id: UUID
    kind: OrderKind
    status: OrderStatus = "draft"
    descripcion: str
    monto: Decimal | None = None
    moneda: str = "USD"
    simbolo: str | None = None
    lado: str | None = None
    cantidad: Decimal | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    confirmed_at: datetime | None = None
    executed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
