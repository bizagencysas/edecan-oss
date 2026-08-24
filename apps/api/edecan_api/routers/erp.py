"""`/v1/erp` — inventario (ERP básico): productos + movimientos de stock (`ARCHITECTURE.md`
§13, WP-V4-06). Ver `docs/negocios.md` (sección "Inventario (ERP básico)") para el contrato
completo (alcance, modelo de datos, ejemplos).

**Contrato pinned §13, dueño WP-V4-01**: las tablas `products`/`stock_moves` (migración
`0006_v4_expansion`) y el flag de plan `erp.inventory` (`edecan_schemas.plans.
FLAG_ERP_INVENTORY`) los aportó ese work package. `edecan_api.main` monta este router de
forma DEFENSIVA vía `V4_ROUTER_NAMES = ("devices", "erp", "ads", "vehiculos", "mensajes")`
(análogo a `V2_ROUTER_NAMES`/`V3_ROUTER_NAMES`, ver el docstring de `main.py`) — este archivo
solo necesita existir y exportar `router`.

## Gate de flag de plan `erp.inventory`

Igual patrón que `edecan_api.routers.commerce`: una dependencia `_require_erp_inventory`
sustituye a `get_current_user` en cada ruta, comparando contra `FLAG_ERP_INVENTORY` (import
directo de `edecan_schemas.plans`, ya pinned — §13.c: `True` en `free_selfhost`/`hosted_pro`/
`hosted_business`, `False` en `hosted_basic`, mismo patrón que `commerce.orders`).

## Estilo y acceso a datos

Espejo exacto de `edecan_api.routers.negocios`/`.commerce`: SQL parametrizado (`sqlalchemy.
text`) sobre la sesión de tenant (`edecan_api.deps.get_tenant_session`, RLS ya activo), TODA
la lógica de negocio real vive en `edecan_business.inventory` (funciones puras y fakeables,
`session`/`tenant_id` explícitos, nunca `ToolContext`) — este módulo solo hace el mapeo HTTP
(Pydantic + status codes + `audit_log`). Nunca toca `edecan_api/repo.py` (prohibido para este
paquete de trabajo, igual que `negocios.py`): el helper `_audit` de aquí abajo es una copia
mínima del de `commerce.py` (INSERT directo en `audit_log`, tabla v1 ya existente).

## Rutas (las 5 del contrato pinned del paquete de trabajo — ninguna ruta extra)

- `GET /productos` — lista con filtros opcionales `activo`/`q`.
- `POST /productos` — crea un producto (`201`); `409` si el `sku` ya existe en el tenant.
- `PATCH /productos/{id}` — edición parcial; el mismo cuerpo acepta `{"activo": false}` para
  desactivar (no hay una ruta HTTP dedicada de "desactivar" — ver el docstring de
  `edecan_business.inventory` para por qué).
- `POST /productos/{id}/movimientos` — registra un movimiento de stock (`201`); `400` si
  dejaría stock negativo con un motivo que no sea `'ajuste'`. La atomicidad
  INSERT-stock_moves + UPDATE-products la garantiza `edecan_business.inventory.
  registrar_movimiento` dentro de la MISMA transacción de request (`get_tenant_session` /
  `edecan_db.session.get_session`) — este router no necesita ningún manejo especial de
  transacción más allá de propagar la excepción si algo falla.
- `GET /resumen` — SKUs activos, valor a costo/precio, alertas de stock bajo.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any, Literal

from edecan_business import (
    SkuDuplicadoError,
    StockInsuficienteError,
    crear_producto,
    editar_producto,
    listar_productos,
    registrar_movimiento,
    resumen_inventario,
)
from edecan_schemas.plans import FLAG_ERP_INVENTORY
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

router = APIRouter(prefix="/v1/erp", tags=["erp"], dependencies=[Depends(rate_limit)])


# ---------------------------------------------------------------------------
# Gate de flag de plan
# ---------------------------------------------------------------------------


async def _require_erp_inventory(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current_user.tenant.flags.get(FLAG_ERP_INVENTORY, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El inventario (ERP) no está disponible en tu plan.",
        )
    return current_user


async def _audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    action: str,
    target: str,
    meta: dict[str, Any],
) -> None:
    """Copia mínima de `edecan_api.routers.commerce._audit` (mismo criterio: INSERT directo
    en `audit_log`, prohibido pasar por `Repo` en este paquete de trabajo)."""
    await session.execute(
        text(
            "INSERT INTO audit_log (tenant_id, actor_user_id, action, target, meta) "
            "VALUES (:tenant_id ::uuid, :actor_user_id ::uuid, :action, :target, "
            "CAST(:meta AS jsonb))"
        ),
        {
            "tenant_id": str(tenant_id),
            "actor_user_id": str(actor_user_id),
            "action": action,
            "target": target,
            "meta": json.dumps(meta),
        },
    )


# ---------------------------------------------------------------------------
# Esquemas de entrada
# ---------------------------------------------------------------------------


class ProductoCreateIn(BaseModel):
    sku: str = Field(min_length=1)
    nombre: str = Field(min_length=1)
    descripcion: str = ""
    unidad: str = "unidad"
    # `ge=0` (no `gt=0`): mismo criterio que `edecan_business.inventory` — un precio/costo
    # de cero es raro pero válido (p. ej. una muestra gratis), y `None` sigue permitido
    # (columnas nullable) porque `ge` de pydantic solo se evalúa sobre valores no-`None`.
    precio: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    costo: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    stock_minimo: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=3)


class ProductoUpdateIn(BaseModel):
    """Todos los campos opcionales (`PATCH` parcial, `exclude_unset` en el handler) — `sku`
    deliberadamente NO es editable (ver `edecan_business.inventory.editar_producto`).
    `activo` es la vía para desactivar/reactivar (sin ruta HTTP dedicada, ver el docstring
    del módulo)."""

    nombre: str | None = Field(default=None, min_length=1)
    descripcion: str | None = None
    unidad: str | None = None
    precio: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    costo: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    stock_minimo: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)
    activo: bool | None = None


class MovimientoIn(BaseModel):
    delta: Decimal = Field(max_digits=14, decimal_places=3)
    motivo: Literal["compra", "venta", "ajuste", "merma", "devolucion"]
    nota: str = ""
    ref: str | None = None

    @field_validator("delta")
    @classmethod
    def _delta_no_cero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("delta no puede ser cero.")
        return value


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------


@router.get("/productos")
async def list_productos(
    activo: bool | None = None,
    q: str | None = Query(default=None),
    current_user: CurrentUser = Depends(_require_erp_inventory),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    return await listar_productos(session, tenant_id=current_user.tenant_id, activo=activo, q=q)


@router.post("/productos", status_code=status.HTTP_201_CREATED)
async def create_producto(
    body: ProductoCreateIn,
    current_user: CurrentUser = Depends(_require_erp_inventory),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    try:
        producto = await crear_producto(
            session,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            sku=body.sku,
            nombre=body.nombre,
            descripcion=body.descripcion,
            unidad=body.unidad,
            precio=body.precio,
            costo=body.costo,
            stock_minimo=body.stock_minimo,
        )
    except SkuDuplicadoError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    await _audit(
        session,
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="erp.product.created",
        target=str(producto["id"]),
        meta={"sku": producto["sku"]},
    )
    return producto


@router.patch("/productos/{product_id}")
async def update_producto(
    product_id: uuid.UUID,
    body: ProductoUpdateIn,
    current_user: CurrentUser = Depends(_require_erp_inventory),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    cambios = body.model_dump(exclude_unset=True)
    try:
        producto = await editar_producto(
            session, tenant_id=current_user.tenant_id, product_id=product_id, **cambios
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if producto is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado."
        )

    await _audit(
        session,
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="erp.product.updated",
        target=str(product_id),
        meta={"campos": sorted(cambios)},
    )
    return producto


# ---------------------------------------------------------------------------
# Movimientos de stock
# ---------------------------------------------------------------------------


@router.post("/productos/{product_id}/movimientos", status_code=status.HTTP_201_CREATED)
async def create_movimiento(
    product_id: uuid.UUID,
    body: MovimientoIn,
    current_user: CurrentUser = Depends(_require_erp_inventory),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    try:
        movimiento = await registrar_movimiento(
            session,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            product_id=product_id,
            delta=body.delta,
            motivo=body.motivo,
            nota=body.nota,
            ref=body.ref,
        )
    except StockInsuficienteError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if movimiento is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado."
        )

    await _audit(
        session,
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="erp.stock_move.created",
        target=str(product_id),
        meta={"delta": str(body.delta), "motivo": body.motivo},
    )
    return movimiento


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------


@router.get("/resumen")
async def get_resumen(
    current_user: CurrentUser = Depends(_require_erp_inventory),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    return await resumen_inventario(session, tenant_id=current_user.tenant_id)
