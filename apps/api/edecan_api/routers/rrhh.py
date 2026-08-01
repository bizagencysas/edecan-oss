"""`/v1/rrhh` — RRHH (ERP): empleados, ausencias y nómina en borrador (`ARCHITECTURE.md` §14,
WP-V5-08). Ver `docs/rrhh.md` para el contrato completo (alcance, modelo de datos, ejemplos).

**GUARDRAIL INNEGOCIABLE** (`DIRECCION_ACTUAL.md`, "dinero real nunca se mueve solo"): la
nómina de este router NUNCA ejecuta ni promete ejecutar un pago. `POST /nominas` genera SOLO
un borrador contable (`edecan_business.rrhh.calcular_nomina`, `payroll_runs.status='draft'`);
"aprobar" una nómina (`POST /nominas/{id}/aprobar`) es un acto de REGISTRO — pasa `draft →
approved` dentro del propio sistema, JAMÁS mueve dinero real. Por eso exige un cuerpo
`{"confirmar": true}` EXPLÍCITO (`400` sin él, mismo espíritu de doble confirmación que
`edecan_api.routers.commerce`/`.ads` con sus borradores de dinero real) y la respuesta repite
el aviso en texto plano: el pago en sí lo hace la persona dueña del negocio por su propio
medio, fuera de este sistema.

**Contrato pinned (`ARCHITECTURE.md` §14, dueño WP-V5-01 — migración `0007_v5_expansion` y
flag `erp.hr`, AMBOS ya aterrizados)**: este router escribe contra las tablas `employees`/
`time_off`/`payroll_runs`/`payroll_items` documentadas al detalle en el docstring de
`edecan_business.rrhh` — léelo antes de tocar este archivo, tiene varias columnas que NO son
lo primero que uno esperaría (`employees.salario_mensual`, no "salario"; `time_off` SIN
`user_id` ni `approved_at`; `payroll_runs.total` es una ÚNICA columna, sin `deducciones_pct`
propia). `edecan_api.main` monta este router de forma DEFENSIVA vía `V5_ROUTER_NAMES = ("rrhh",
"viajes", "voz_avanzada")` (mismo mecanismo que v2/v3/v4, ver el docstring de `main.py`) — este
archivo solo necesita existir y exportar `router`.

## Gate de flag de plan `erp.hr`

Igual patrón que `edecan_api.routers.erp`: una dependencia `_require_erp_hr` sustituye a
`get_current_user` en cada ruta, comparando contra `edecan_schemas.plans.FLAG_ERP_HR`
(re-exportado como `edecan_business.FLAG_ERP_HR`, ver el docstring de `edecan_business.tools`)
— `True` en los 4 planes (`ARCHITECTURE.md` §14.c: "básica", mismo criterio que `companion`).

## Estilo y acceso a datos

Espejo exacto de `edecan_api.routers.erp`/`.negocios`: SQL parametrizado vive en
`edecan_business.rrhh` (funciones puras y fakeables, `session`/`tenant_id` explícitos, nunca
`ToolContext`), este módulo SOLO hace el mapeo HTTP (Pydantic + status codes + `audit_log` en
aprobar/cancelar de nómina). Nunca toca `edecan_api/repo.py` (prohibido para este paquete de
trabajo, igual que `erp.py`/`negocios.py`): el helper `_audit` de aquí abajo es una copia
mínima del de `erp.py`/`commerce.py` (INSERT directo en `audit_log`, tabla v1 ya existente).

## Rutas (las 11 del contrato pinned del paquete de trabajo — ninguna ruta extra)

- `GET /empleados` — lista con filtros opcionales `status`/`q`.
- `POST /empleados` — crea un empleado (`201`).
- `PATCH /empleados/{id}` — edición parcial (incluye `status` para des/reactivar).
- `GET /ausencias` — lista con filtros opcionales `employee_id`/`status`.
- `POST /ausencias` — registra una ausencia `pending` (`201`); `404` si el empleado no
  existe; `409` si se solapa con otra ausencia ya `approved` del mismo empleado.
- `PATCH /ausencias/{id}` — `{"accion": "aprobar"|"rechazar"}`; `409` si no está `pending`.
- `GET /nominas` — lista corridas (sin items) con filtro opcional `status`.
- `POST /nominas` — `{periodo, deducciones_pct?, moneda?, notas?}` → borrador calculado
  (`201`).
- `GET /nominas/{id}` — corrida con sus items (JOIN a `employees` para el nombre).
- `POST /nominas/{id}/aprobar` — exige `{"confirmar": true}` (`400` sin él); `409` si la
  corrida no está en `'draft'` (evita doble aprobación). Nunca mueve dinero, ver arriba.
- `POST /nominas/{id}/cancelar` — `409` si la corrida no está en `'draft'`.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from edecan_business import (
    FLAG_ERP_HR,
    AusenciaSolapadaError,
    EstadoAusenciaError,
    EstadoNominaError,
    aprobar_nomina,
    calcular_nomina,
    cancelar_nomina,
    crear_empleado,
    editar_empleado,
    listar_ausencias,
    listar_empleados,
    listar_nominas,
    obtener_nomina,
    registrar_ausencia,
    resolver_ausencia,
)
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, rate_limit

router = APIRouter(prefix="/v1/rrhh", tags=["rrhh"], dependencies=[Depends(rate_limit)])


# ---------------------------------------------------------------------------
# Gate de flag de plan
# ---------------------------------------------------------------------------


async def _require_erp_hr(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current_user.tenant.flags.get(FLAG_ERP_HR, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="RRHH no está disponible en tu plan.",
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
    """Copia mínima de `edecan_api.routers.erp._audit`/`.commerce._audit` (mismo criterio:
    INSERT directo en `audit_log`, prohibido pasar por `Repo` en este paquete de trabajo)."""
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


class EmpleadoCreateIn(BaseModel):
    nombre: str = Field(min_length=1)
    email: str | None = None
    puesto: str = ""
    # `ge=0` (no `gt=0`): mismo criterio que `precio`/`costo` en `erp.py` — un salario de
    # cero es raro pero válido (p. ej. un puesto aún sin definir), y `None` sigue permitido
    # (columna nullable) porque `ge` de pydantic solo se evalúa sobre valores no-`None`.
    salario_mensual: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    moneda: str = "USD"
    fecha_ingreso: date | None = None
    status: Literal["active", "inactive"] = "active"


class EmpleadoUpdateIn(BaseModel):
    """Todos los campos opcionales (`PATCH` parcial, `exclude_unset` en el handler)."""

    nombre: str | None = Field(default=None, min_length=1)
    email: str | None = None
    puesto: str | None = None
    salario_mensual: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    moneda: str | None = None
    fecha_ingreso: date | None = None
    status: Literal["active", "inactive"] | None = None


class AusenciaCreateIn(BaseModel):
    employee_id: uuid.UUID
    kind: Literal["vacaciones", "enfermedad", "permiso", "otro"]
    desde: date
    hasta: date
    notas: str = ""


class AusenciaResolverIn(BaseModel):
    accion: Literal["aprobar", "rechazar"]


class NominaCreateIn(BaseModel):
    periodo: str = Field(min_length=1)
    # Redundante con la validación 0-50 de `edecan_business.rrhh._validar_deducciones_pct`
    # a propósito — mismo criterio de "defensa en profundidad" que `ProductoCreateIn.precio`
    # en `erp.py` (Pydantic Y la capa de negocio validan lo mismo, sin duplicar lógica: solo
    # el rango numérico se repite, el mensaje de error real lo arma la capa de negocio).
    # Nunca se persiste como columna propia de `payroll_runs` (no existe en el esquema
    # pinned) — solo alimenta el cálculo de cada `payroll_items.deducciones`.
    deducciones_pct: Decimal | None = Field(
        default=None, ge=0, le=50, max_digits=4, decimal_places=2
    )
    moneda: str = "USD"
    notas: str = ""


class NominaAprobarIn(BaseModel):
    """`confirmar` nace en `False`: sin un cuerpo `{"confirmar": true}` EXPLÍCITO (incluso sin
    cuerpo del todo, ver el default del parámetro en `aprobar_nomina_endpoint`), la aprobación
    se rechaza con `400` — guardrail de dinero real, ver el docstring del módulo."""

    confirmar: bool = False


# ---------------------------------------------------------------------------
# Empleados
# ---------------------------------------------------------------------------


@router.get("/empleados")
async def list_empleados(
    status_filter: Literal["active", "inactive"] | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None),
    current_user: CurrentUser = Depends(_require_erp_hr),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    return await listar_empleados(
        session, tenant_id=current_user.tenant_id, status=status_filter, q=q
    )


@router.post("/empleados", status_code=status.HTTP_201_CREATED)
async def create_empleado(
    body: EmpleadoCreateIn,
    current_user: CurrentUser = Depends(_require_erp_hr),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    try:
        return await crear_empleado(
            session,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            nombre=body.nombre,
            email=body.email,
            puesto=body.puesto,
            salario_mensual=body.salario_mensual,
            moneda=body.moneda,
            fecha_ingreso=body.fecha_ingreso,
            status=body.status,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.patch("/empleados/{employee_id}")
async def update_empleado(
    employee_id: uuid.UUID,
    body: EmpleadoUpdateIn,
    current_user: CurrentUser = Depends(_require_erp_hr),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    cambios = body.model_dump(exclude_unset=True)
    try:
        empleado = await editar_empleado(
            session, tenant_id=current_user.tenant_id, employee_id=employee_id, **cambios
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if empleado is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Empleado no encontrado."
        )
    return empleado


# ---------------------------------------------------------------------------
# Ausencias
# ---------------------------------------------------------------------------


@router.get("/ausencias")
async def list_ausencias(
    employee_id: uuid.UUID | None = None,
    status_filter: Literal["pending", "approved", "rejected", "cancelled"] | None = Query(
        default=None, alias="status"
    ),
    current_user: CurrentUser = Depends(_require_erp_hr),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    return await listar_ausencias(
        session, tenant_id=current_user.tenant_id, employee_id=employee_id, status=status_filter
    )


@router.post("/ausencias", status_code=status.HTTP_201_CREATED)
async def create_ausencia(
    body: AusenciaCreateIn,
    current_user: CurrentUser = Depends(_require_erp_hr),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    try:
        ausencia = await registrar_ausencia(
            session,
            tenant_id=current_user.tenant_id,
            employee_id=body.employee_id,
            kind=body.kind,
            desde=body.desde,
            hasta=body.hasta,
            notas=body.notas,
        )
    except AusenciaSolapadaError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if ausencia is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Empleado no encontrado."
        )
    return ausencia


@router.patch("/ausencias/{time_off_id}")
async def update_ausencia(
    time_off_id: uuid.UUID,
    body: AusenciaResolverIn,
    current_user: CurrentUser = Depends(_require_erp_hr),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    try:
        ausencia = await resolver_ausencia(
            session,
            tenant_id=current_user.tenant_id,
            time_off_id=time_off_id,
            aprobar=body.accion == "aprobar",
        )
    except EstadoAusenciaError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if ausencia is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ausencia no encontrada."
        )
    return ausencia


# ---------------------------------------------------------------------------
# Nómina
# ---------------------------------------------------------------------------


@router.get("/nominas")
async def list_nominas(
    status_filter: Literal["draft", "approved", "paid", "cancelled"] | None = Query(
        default=None, alias="status"
    ),
    current_user: CurrentUser = Depends(_require_erp_hr),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    return await listar_nominas(session, tenant_id=current_user.tenant_id, status=status_filter)


@router.post("/nominas", status_code=status.HTTP_201_CREATED)
async def create_nomina(
    body: NominaCreateIn,
    current_user: CurrentUser = Depends(_require_erp_hr),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Genera el borrador completo (`calcular_nomina`) — nunca mueve dinero, ver el
    docstring del módulo."""
    try:
        return await calcular_nomina(
            session,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            periodo=body.periodo,
            deducciones_pct=body.deducciones_pct,
            moneda=body.moneda,
            notas=body.notas,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.get("/nominas/{payroll_run_id}")
async def get_nomina(
    payroll_run_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_erp_hr),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    nomina = await obtener_nomina(
        session, tenant_id=current_user.tenant_id, payroll_run_id=payroll_run_id
    )
    if nomina is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nómina no encontrada.")
    return nomina


@router.post("/nominas/{payroll_run_id}/aprobar")
async def aprobar_nomina_endpoint(
    payroll_run_id: uuid.UUID,
    body: NominaAprobarIn = NominaAprobarIn(),
    current_user: CurrentUser = Depends(_require_erp_hr),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """`draft -> approved`. Exige `{"confirmar": true}` EXPLÍCITO en el cuerpo — `body` tiene
    un default (`NominaAprobarIn()`, `confirmar=False`) para que la ruta acepte un `POST` sin
    cuerpo del todo y lo trate igual que `{"confirmar": false}`: en ambos casos, `400` con un
    mensaje claro, nunca aprueba nada por accidente. Nunca mueve dinero real (ver el docstring
    del módulo) — el `409` de `EstadoNominaError` cubre tanto una corrida que ya no está en
    `draft` como la doble aprobación concurrente (el lock `FOR UPDATE` vive en
    `edecan_business.rrhh._lock_payroll_run`)."""
    if not body.confirmar:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Debes confirmar explícitamente (\"confirmar\": true) para aprobar la nómina. "
                "Esto NO mueve dinero: solo registra la nómina como aprobada en el sistema; "
                "el pago lo haces tú por tu propio medio."
            ),
        )
    try:
        nomina = await aprobar_nomina(
            session, tenant_id=current_user.tenant_id, payroll_run_id=payroll_run_id
        )
    except EstadoNominaError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if nomina is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nómina no encontrada.")

    await _audit(
        session,
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="rrhh.payroll.approved",
        target=str(payroll_run_id),
        meta={"periodo": nomina["periodo"]},
    )
    return {
        "nomina": nomina,
        "mensaje": (
            "Nómina aprobada (registro contable). Esto NO mueve dinero: el pago lo haces tú "
            "por tu propio medio."
        ),
    }


@router.post("/nominas/{payroll_run_id}/cancelar")
async def cancelar_nomina_endpoint(
    payroll_run_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_erp_hr),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    try:
        nomina = await cancelar_nomina(
            session, tenant_id=current_user.tenant_id, payroll_run_id=payroll_run_id
        )
    except EstadoNominaError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if nomina is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nómina no encontrada.")

    await _audit(
        session,
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="rrhh.payroll.cancelled",
        target=str(payroll_run_id),
        meta={"periodo": nomina["periodo"]},
    )
    return {"nomina": nomina, "mensaje": "Nómina cancelada."}
