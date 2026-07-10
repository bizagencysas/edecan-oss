"""`/v1/negocios` — facturación ligera con PDF real + KPIs de negocio (`ROADMAP_V2.md` §5
P1 WP-V2-12, §7.4, §7.6, §7.7 — pantalla 4 del mockup de `REQUISITOS_V2.md`). Ver
`docs/negocios.md` para el contrato completo (alcance/disclaimer, flujo de estados,
supuestos de los KPIs).

**Sin flag de plan nuevo**: a diferencia de `edecan_api.routers.commerce`/`.remote` (que
gatean con `requires_flags`/`_require_*` sobre un flag de `ROADMAP_V2.md` §7.2), este router
está disponible en TODOS los planes — así lo pinnea el paquete de trabajo WP-V2-12
("sin flag nuevo — disponible en todos los planes"). Facturar no consume ningún recurso caro
(voz/telefonía/modelos premium); solo Postgres y el `S3_BUCKET` propio del tenant.

Este módulo hace ÚNICAMENTE el mapeo HTTP (Pydantic + status codes) sobre una sesión de
tenant (`edecan_api.deps.get_tenant_session`, la misma convención que
`edecan_api.routers.commerce`/`edecan_premium.compliance` usan desde fuera de la capa
`Repo`): toda la lógica de negocio real (totales, numeración, PDF, KPIs) vive en
`edecan_business` — funciones "puras y fakeables" (reciben `session`/`tenant_id`/`user_id`
explícitos, nunca un `ToolContext`) que también usa `edecan_business.tools.CrearFacturaTool`
dentro de una conversación del agente. Nunca se reimplementa esa orquestación aquí, y este
router tiene prohibido tocar `edecan_api/repo.py` (paquete de trabajo pinned) — por eso NO
pasa por `Repo`/`SqlRepo` como sí hace `edecan_api.routers.finance` (v1).

Los nombres importados de `edecan_business` quedan como referencias directas en el
namespace de este módulo (`from edecan_business import ...`, no `import edecan_business`) a
propósito: son exactamente los símbolos que los tests de este router sustituyen con
`monkeypatch.setattr(negocios_module, "crear_factura", ...)` — "módulos puros/fakeables",
tal como pide el paquete de trabajo. Los tests de la orquestación SQL real en sí
(`next_numero`/`compute_totals`/`crear_factura`/`kpis_mes`/`cambiar_estado`, con un
`FakeSession` programable) viven en `packages/business/tests/`, no aquí — este archivo solo
verifica el contrato HTTP (status codes, validación Pydantic, mapeo de excepciones).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from edecan_business import (
    EstadoInvalidoError,
    cambiar_estado,
    crear_factura,
    kpis_mes,
    listar_facturas,
    obtener_factura,
)
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

router = APIRouter(prefix="/v1/negocios", tags=["negocios"], dependencies=[Depends(rate_limit)])


# ---------------------------------------------------------------------------
# Esquemas de entrada
# ---------------------------------------------------------------------------


class InvoiceItemIn(BaseModel):
    """Mismo shape que el argumento `items[]` de la tool `crear_factura`
    (`edecan_business.tools.CrearFacturaTool`, `ROADMAP_V2.md` §7.7)."""

    descripcion: str = Field(min_length=1)
    # `ge=0` (no `gt=0`): el contrato pinned del paquete de trabajo es "montos >= 0" —
    # mismo criterio, ni más estricto ni más laxo, que `edecan_business.invoices._normalizar_items`
    # (única fuente de verdad de esta regla; ver su docstring).
    cantidad: Decimal = Field(ge=0)
    precio_unitario: Decimal = Field(ge=0)


class InvoiceCreateIn(BaseModel):
    """Mismo shape que los argumentos de la tool `crear_factura` (`ROADMAP_V2.md` §7.7):
    `{cliente_nombre, items, impuestos_pct?, due_date?, cliente_email?, notas?}`, más
    `moneda` (que la tool no expone porque asume USD, pero el formulario web sí lo permite
    elegir explícitamente)."""

    cliente_nombre: str = Field(min_length=1)
    items: list[InvoiceItemIn] = Field(min_length=1)
    impuestos_pct: Decimal = Decimal("0")
    due_date: date | None = None
    cliente_email: str | None = None
    notas: str = ""
    moneda: str = "USD"


class InvoiceStatusIn(BaseModel):
    """`draft` deliberadamente NO es un valor aceptado aquí: una factura nunca "vuelve" a
    borrador — mismo vocabulario que `edecan_business.invoices._TRANSICIONES_VALIDAS`."""

    status: Literal["sent", "paid", "void"]


# ---------------------------------------------------------------------------
# KPIs (pantalla 4 del mockup)
# ---------------------------------------------------------------------------


@router.get("/kpis")
async def get_kpis(
    mes: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """`GET /v1/negocios/kpis?mes=YYYY-MM` (default: mes actual UTC) → shape de
    `edecan_business.kpis.kpis_mes`."""
    try:
        return await kpis_mes(
            session, tenant_id=current_user.tenant_id, user_id=current_user.user_id, mes=mes
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


# ---------------------------------------------------------------------------
# Facturas
# ---------------------------------------------------------------------------


@router.get("/facturas")
async def list_facturas(
    status_filter: str | None = Query(default=None, alias="status"),
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    try:
        return await listar_facturas(
            session, tenant_id=current_user.tenant_id, status=status_filter
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.post("/facturas", status_code=status.HTTP_201_CREATED)
async def create_factura(
    body: InvoiceCreateIn,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Crea una factura `draft` completa (totales + numeración + PDF + subida a S3) —
    delega TODO en `edecan_business.crear_factura`. `items` no vacíos y montos `>= 0` ya
    quedan validados por Pydantic (`InvoiceItemIn`/`Field(min_length=1)`/`Field(ge=0)`)
    antes de llegar aquí; el `except ValueError` de abajo cubre las reglas de negocio que
    Pydantic no puede expresar por sí solo (p. ej. `cliente_nombre` en blanco tras
    `.strip()`, o un `due_date` técnicamente válido para Pydantic pero rechazado más abajo
    en el flujo)."""
    try:
        return await crear_factura(
            session,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            settings=settings,
            cliente_nombre=body.cliente_nombre,
            items=[item.model_dump() for item in body.items],
            impuestos_pct=body.impuestos_pct,
            due_date=body.due_date,
            cliente_email=body.cliente_email,
            notas=body.notas,
            moneda=body.moneda,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.get("/facturas/{invoice_id}")
async def get_factura(
    invoice_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    invoice = await obtener_factura(
        session, tenant_id=current_user.tenant_id, invoice_id=invoice_id
    )
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Factura no encontrada."
        )
    return invoice


@router.post("/facturas/{invoice_id}/estado")
async def set_factura_estado(
    invoice_id: uuid.UUID,
    body: InvoiceStatusIn,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """`draft -> sent -> paid`; `void` desde cualquier estado no-`void`
    (`edecan_business.invoices.cambiar_estado`). Una transición fuera de ese vocabulario es
    `409 Conflict` (hay una factura real, pero ese cambio de estado en particular no está
    permitido desde su estado actual) — distinto de un `status` que ni siquiera es uno de
    `sent`/`paid`/`void`, que Pydantic ya rechaza como `422` antes de llegar aquí."""
    try:
        invoice = await cambiar_estado(
            session,
            tenant_id=current_user.tenant_id,
            invoice_id=invoice_id,
            nuevo_status=body.status,
        )
    except EstadoInvalidoError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Factura no encontrada."
        )
    return invoice
