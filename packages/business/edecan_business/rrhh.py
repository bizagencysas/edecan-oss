"""RRHH (ERP básico): empleados, ausencias y nómina en borrador — trabajo de WP-V5-08 sobre
el mismo paquete `edecan_business` que ya extendieron WP-V2-12 (`invoices.py`/`kpis.py`) y
WP-V4-06 (`inventory.py`). Mismo criterio EXACTO que esos dos módulos: funciones puras o con
`session` explícito (nunca `ToolContext`), SQL parametrizado con `sqlalchemy.text` (el
contrato pinnea tabla/columna, no un ORM), para que las use tanto `tools.py` (con
`ctx.session`/`ctx.tenant_id`/...) como `apps/api/edecan_api/routers/rrhh.py` (con su propia
sesión de tenant) sin duplicar la lógica de negocio en dos sitios.

**Contrato pinned de las cuatro tablas (`ARCHITECTURE.md` §14.b, migración `0007_v5_expansion`,
dueño WP-V5-01 — columnas EXACTAS, ya aterrizadas)**:

- `employees(tenant_id, user_id, nombre, email nullable, puesto default '', salario_mensual
  numeric(14,2) nullable, moneda char(3) default 'USD', fecha_ingreso date nullable, status
  text default 'active', meta jsonb default '{}')` — el roster del negocio. `salario_mensual`
  es NULLABLE a propósito: un empleado puede existir sin compensación asignada todavía
  (`calcular_nomina` solo toma empleados `active` CON `salario_mensual` — ver más abajo).
  `status` queda TEXTO ABIERTO a nivel de base de datos (sin `CHECK`, §14.b: "mismo criterio
  que `tenants.status`") — este módulo SÍ valida `active`/`inactive` en la capa de negocio
  (contrato pinned de este work package), un subconjunto razonable del vocabulario abierto de
  la tabla. `meta` (jsonb libre) NO se expone en ningún CRUD de este módulo — sin un caso de
  uso pinned todavía, queda en su default `{}` siempre (ver `docs/rrhh.md`, "Qué queda para
  P2").
- `time_off(tenant_id, employee_id FK → employees ON DELETE CASCADE, kind, desde date, hasta
  date, status default 'pending', notas default '')` — una ausencia nace SIEMPRE `pending`
  (nunca se infiere un estado de arranque distinto, mismo principio que `invoices`/
  `stock_moves`). `status` SÍ lleva `CHECK IN ('pending','approved','rejected','cancelled')` a
  nivel de base de datos (máquina de estados explícita) — este módulo implementa las
  transiciones `pending → approved`/`pending → rejected` (`resolver_ausencia`); `cancelled`
  existe en el esquema pinned pero ningún camino de este work package lo alcanza todavía (ver
  `docs/rrhh.md`, "Qué queda para P2"). **Sin `user_id` ni `approved_at`** — a diferencia de
  `stock_moves`/`invoices`, esta tabla no registra quién la creó ni cuándo se aprobó, solo su
  `status` actual y `updated_at`.
- `payroll_runs(tenant_id, user_id, periodo, status default 'draft', total numeric(14,2)
  default 0, moneda char(3) default 'USD', notas default '', approved_at nullable)` — una
  corrida de nómina nace SIEMPRE `draft` (calculada por `calcular_nomina`); `aprobar_nomina`/
  `cancelar_nomina` son los ÚNICOS caminos que este work package implementa para salir de
  `draft`. `status` lleva `CHECK IN ('draft','approved','paid','cancelled')` — `'paid'` existe
  en el esquema pinned (para cuando un WP futuro registre "ya se pagó de verdad, fuera de este
  sistema") pero este work package NUNCA lo escribe (ver `docs/rrhh.md`). **`total` es una
  ÚNICA columna** (no hay `total_bruto`/`total_deducciones` separados en la tabla): representa
  el NETO final de la corrida (`Σ payroll_items.neto`) — `calcular_nomina`/`obtener_nomina`
  SÍ devuelven `total_bruto`/`total_deducciones` como campos CALCULADOS (sumando `payroll_items`
  en Python), nunca persistidos, para que el frontend pueda mostrar el desglose sin una query
  aparte. **Sin `deducciones_pct` propia**: el porcentaje de deducciones es un parámetro de
  CÁLCULO de `calcular_nomina` (afecta cada `payroll_items.deducciones`), no una columna que
  se recuerde en la corrida.
- `payroll_items(tenant_id, payroll_run_id FK → payroll_runs ON DELETE CASCADE, employee_id FK
  → employees, bruto numeric(14,2), deducciones numeric(14,2) default 0, neto numeric(14,2))`
  — una línea por empleado incluido en la corrida, tabla "hija" igual que `invoice_items`/
  `stock_moves`: también carga `tenant_id` (RLS + scoping directo).

**GUARDRAIL INNEGOCIABLE (`DIRECCION_ACTUAL.md`, "dinero real nunca se mueve solo")**: nada
de este módulo mueve ni promete mover un pago. `calcular_nomina` SOLO inserta un borrador
contable (`payroll_runs.status='draft'` + sus `payroll_items`) — es aritmética y persistencia,
nunca una transferencia. `aprobar_nomina` es un acto de REGISTRO (pasa `draft → approved`,
dentro del propio sistema): jamás dispara ningún pago real, ninguna llamada a un proveedor de
pagos, nada que salga del tenant. El pago en sí lo hace la persona dueña del negocio por su
propio medio, fuera de este sistema — `apps/api/edecan_api/routers/rrhh.py` exige una
confirmación explícita (`{"confirmar": true}`) antes de aprobar, y tanto la tool
`preparar_nomina` como cada respuesta HTTP relevante repiten el aviso en texto plano.

El flag de plan `erp.hr` (`edecan_schemas.plans.FLAG_ERP_HR`, `ARCHITECTURE.md` §14.c) SÍ está
pinned y en `True` en los 4 planes ("básica", mismo criterio que `companion`) — se importa
directo desde `edecan_business.tools` (ver su docstring), sin ningún string local.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_DOS_DECIMALES = Decimal("0.01")
# Validación simple (no RFC 5322 completo a propósito — mismo criterio "suficiente, no
# perfecto" que el resto del repo): rechaza los casos obviamente mal formados sin intentar
# cubrir cada borde legal de la RFC.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PERIODO_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

_MONEDA_DEFECTO = "USD"
_EMPLEADO_STATUS_VALIDOS: tuple[str, ...] = ("active", "inactive")
_EMPLEADO_STATUS_DEFECTO = "active"
_AUSENCIA_KINDS_VALIDOS: tuple[str, ...] = ("vacaciones", "enfermedad", "permiso", "otro")
# Vocabulario COMPLETO del CHECK de `time_off.status` (`ARCHITECTURE.md` §14.b) — usado para
# validar el filtro `status` de `listar_ausencias`. `resolver_ausencia` en sí solo produce
# `approved`/`rejected` (ver su docstring); `cancelled` queda fuera de alcance de este work
# package pero sigue siendo un valor válido para FILTRAR (p. ej. si otra vía llega a fijarlo).
_AUSENCIA_STATUS_VALIDOS: tuple[str, ...] = ("pending", "approved", "rejected", "cancelled")
_AUSENCIA_STATUS_PENDING = "pending"
_AUSENCIA_STATUS_APPROVED = "approved"
_AUSENCIA_STATUS_REJECTED = "rejected"
# Vocabulario COMPLETO del CHECK de `payroll_runs.status` (`ARCHITECTURE.md` §14.b) — mismo
# criterio que arriba: `listar_nominas` puede filtrar por `'paid'` aunque este work package
# nunca lo escriba (ver el docstring del módulo).
_PAYROLL_STATUS_VALIDOS: tuple[str, ...] = ("draft", "approved", "paid", "cancelled")
_PAYROLL_STATUS_DRAFT = "draft"
_DEDUCCIONES_PCT_MIN = Decimal("0")
_DEDUCCIONES_PCT_MAX = Decimal("50")


class AusenciaSolapadaError(ValueError):
    """`registrar_ausencia` rechaza un rango que se solapa con OTRA ausencia ya `approved`
    del MISMO empleado. Subclase de `ValueError` (mismo criterio que `SkuDuplicadoError` en
    `inventory.py`) para que `apps/api/edecan_api/routers/rrhh.py` la distinga y responda
    `409 Conflict` (hay un recurso real en conflicto) en vez de `422`.

    Solo bloquea contra ausencias YA `approved` — dos solicitudes `pending` que se solapan
    entre sí NO se rechazan aquí (ambas son válidas de registrar; quien las aprueba/rechaza
    decide cuál prevalece al resolverlas, ver `resolver_ausencia`)."""


class EstadoAusenciaError(ValueError):
    """`resolver_ausencia` exige que la ausencia esté en `'pending'` — aprobar/rechazar una
    que ya se resolvió antes es un conflicto de estado, no un cuerpo mal formado. Subclase de
    `ValueError` para que el router la distinga y responda `409 Conflict`."""


class EstadoNominaError(ValueError):
    """`aprobar_nomina`/`cancelar_nomina` exigen que la corrida esté en `'draft'` — mismo
    criterio que `EstadoInvalidoError` en `invoices.py`: una transición no permitida desde el
    estado actual es `409 Conflict`, no `422`. Cubre también la idempotencia de aprobar: una
    segunda aprobación sobre una corrida ya `'approved'` cae en esta misma rama."""


# ---------------------------------------------------------------------------
# Helpers de validación/formato (Decimal, nunca float — mismo criterio que invoices.py)
# ---------------------------------------------------------------------------


def _to_decimal(valor: Any, campo: str) -> Decimal:
    if valor is None or valor == "":
        raise ValueError(f"{campo} es obligatorio.")
    try:
        return Decimal(str(valor))
    except InvalidOperation as exc:
        raise ValueError(f"'{valor}' no es un valor numérico válido para {campo}.") from exc


def _to_decimal_opcional(valor: Any, campo: str) -> Decimal | None:
    """Como `_to_decimal`, pero `None`/`''` es válido (columna nullable: `salario_mensual`) —
    en vez de "obligatorio", significa "sin valor asignado todavía"."""
    if valor is None or valor == "":
        return None
    return _to_decimal(valor, campo)


def _round2(valor: Decimal) -> Decimal:
    """Redondeo bancario (half-even) a 2 decimales — mismo criterio contable que
    `invoices._round2`: evita el sesgo sistemático hacia arriba de "half-up" en una serie
    larga de líneas de nómina."""
    return valor.quantize(_DOS_DECIMALES, rounding=ROUND_HALF_EVEN)


def _normalizar_moneda(moneda: str | None) -> str:
    """Mismo criterio que `invoices._normalizar_moneda`: código ISO-4217 de 3 letras,
    mayúsculas; cualquier otra cosa cae al default `'USD'` (columna `char(3) default 'USD'`,
    nunca vale la pena rechazar toda la operación por una moneda mal tecleada)."""
    moneda_norm = (moneda or _MONEDA_DEFECTO).strip().upper()
    return moneda_norm if len(moneda_norm) == 3 and moneda_norm.isalpha() else _MONEDA_DEFECTO


def _validar_email(email: Any) -> str | None:
    email_norm = str(email or "").strip()
    if not email_norm:
        return None
    if not _EMAIL_RE.match(email_norm):
        raise ValueError(f"'{email_norm}' no es un email válido.")
    return email_norm


def _validar_status_empleado(valor: Any) -> str:
    status_norm = str(valor or _EMPLEADO_STATUS_DEFECTO).strip().lower()
    if status_norm not in _EMPLEADO_STATUS_VALIDOS:
        raise ValueError(f"status debe ser uno de {_EMPLEADO_STATUS_VALIDOS}.")
    return status_norm


def _parse_fecha(valor: Any, campo: str) -> date:
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"'{valor}' no es una fecha válida para {campo} (usa YYYY-MM-DD)."
        ) from exc


def _parse_fecha_opcional(valor: Any, campo: str) -> date | None:
    if valor is None or valor == "":
        return None
    return _parse_fecha(valor, campo)


def _validar_periodo(periodo: Any) -> str:
    periodo_norm = str(periodo or "").strip()
    if not _PERIODO_RE.match(periodo_norm):
        raise ValueError(f"'{periodo}' no es un periodo válido (usa YYYY-MM).")
    return periodo_norm


def _validar_deducciones_pct(valor: Any) -> Decimal:
    """`None`/`''` (parámetro omitido) equivale a 0% — a diferencia de `salario_mensual` este
    porcentaje NUNCA se persiste en `payroll_runs` (no existe esa columna en el esquema
    pinned, ver el docstring del módulo): solo alimenta el cálculo de cada
    `payroll_items.deducciones`."""
    if valor is None or valor == "":
        return Decimal("0")
    pct = _to_decimal(valor, "deducciones_pct")
    if pct < _DEDUCCIONES_PCT_MIN or pct > _DEDUCCIONES_PCT_MAX:
        raise ValueError(
            f"deducciones_pct debe estar entre {_DEDUCCIONES_PCT_MIN} y {_DEDUCCIONES_PCT_MAX}."
        )
    return pct


# ---------------------------------------------------------------------------
# Empleados
# ---------------------------------------------------------------------------


async def crear_empleado(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    nombre: str,
    email: str | None = None,
    puesto: str = "",
    salario_mensual: Decimal | float | int | str | None = None,
    moneda: str = _MONEDA_DEFECTO,
    fecha_ingreso: date | str | None = None,
    status: str = _EMPLEADO_STATUS_DEFECTO,
) -> dict[str, Any]:
    """Crea un empleado nuevo. `email` (si viene) debe ser un email bien formado;
    `salario_mensual` (si viene) debe ser un `Decimal` válido y no negativo — ambos opcionales
    (`salario_mensual` nulo significa "sin compensación asignada todavía", ver el docstring
    del módulo). Lanza `ValueError` ante cualquier dato inválido."""
    nombre_norm = str(nombre or "").strip()
    if not nombre_norm:
        raise ValueError("nombre es obligatorio.")
    email_norm = _validar_email(email)
    puesto_norm = str(puesto or "").strip()
    salario_dec = _to_decimal_opcional(salario_mensual, "salario_mensual")
    if salario_dec is not None and salario_dec < 0:
        raise ValueError("salario_mensual no puede ser negativo.")
    moneda_norm = _normalizar_moneda(moneda)
    fecha_ingreso_parsed = _parse_fecha_opcional(fecha_ingreso, "fecha_ingreso")
    status_norm = _validar_status_empleado(status)

    row = (
        await session.execute(
            text(
                "INSERT INTO employees "
                "(tenant_id, user_id, nombre, email, puesto, salario_mensual, moneda, "
                "fecha_ingreso, status) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, :nombre, :email, :puesto, "
                ":salario_mensual, :moneda, :fecha_ingreso, :status) RETURNING *"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "nombre": nombre_norm,
                "email": email_norm,
                "puesto": puesto_norm,
                "salario_mensual": salario_dec,
                "moneda": moneda_norm,
                "fecha_ingreso": fecha_ingreso_parsed,
                "status": status_norm,
            },
        )
    ).mappings().first()
    if row is None:  # defensivo: no debería pasar nunca (acabamos de insertar).
        raise RuntimeError("No se pudo crear el empleado (fila no devuelta por Postgres).")
    await session.flush()
    return dict(row)


async def editar_empleado(
    session: AsyncSession, *, tenant_id: UUID, employee_id: UUID, **cambios: Any
) -> dict[str, Any] | None:
    """Actualiza parcialmente un empleado (por `id`). Solo toca los campos EXPLÍCITAMENTE
    presentes en `cambios` (mismo criterio que `inventory.editar_producto`: una clave ausente
    significa "no la toques", nunca "bórrala") — editables: `nombre`/`email`/`puesto`/
    `salario_mensual`/`moneda`/`fecha_ingreso`/`status`; cualquier otra clave se ignora en
    silencio.

    Siempre ejecuta un único `UPDATE` (aunque `cambios` esté vacío: en ese caso solo toca
    `updated_at`). Devuelve `None` si el empleado no existe (o no es de este tenant) — el
    llamador HTTP lo traduce a `404`. Lanza `ValueError` ante un dato inválido."""
    set_parts: list[str] = []
    params: dict[str, Any] = {"id": str(employee_id), "tenant_id": str(tenant_id)}

    if "nombre" in cambios:
        nombre_norm = str(cambios["nombre"] or "").strip()
        if not nombre_norm:
            raise ValueError("nombre no puede quedar vacío.")
        set_parts.append("nombre = :nombre")
        params["nombre"] = nombre_norm
    if "email" in cambios:
        set_parts.append("email = :email")
        params["email"] = _validar_email(cambios["email"])
    if "puesto" in cambios:
        set_parts.append("puesto = :puesto")
        params["puesto"] = str(cambios["puesto"] or "").strip()
    if "salario_mensual" in cambios:
        salario_dec = _to_decimal_opcional(cambios["salario_mensual"], "salario_mensual")
        if salario_dec is not None and salario_dec < 0:
            raise ValueError("salario_mensual no puede ser negativo.")
        set_parts.append("salario_mensual = :salario_mensual")
        params["salario_mensual"] = salario_dec
    if "moneda" in cambios:
        set_parts.append("moneda = :moneda")
        params["moneda"] = _normalizar_moneda(cambios["moneda"])
    if "fecha_ingreso" in cambios:
        set_parts.append("fecha_ingreso = :fecha_ingreso")
        params["fecha_ingreso"] = _parse_fecha_opcional(cambios["fecha_ingreso"], "fecha_ingreso")
    if "status" in cambios:
        set_parts.append("status = :status")
        params["status"] = _validar_status_empleado(cambios["status"])

    set_sql = ", ".join([*set_parts, "updated_at = now()"])
    row = (
        await session.execute(
            text(
                f"UPDATE employees SET {set_sql} "
                "WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid RETURNING *"
            ),
            params,
        )
    ).mappings().first()
    await session.flush()
    return dict(row) if row is not None else None


async def desactivar_empleado(
    session: AsyncSession, *, tenant_id: UUID, employee_id: UUID
) -> dict[str, Any] | None:
    """`status = 'inactive'` — atajo sobre `editar_empleado(..., status='inactive')` para que
    la tool `gestionar_empleado` (acción `desactivar`) tenga una función dedicada, mismo
    criterio que `inventory.desactivar_producto`. Idempotente."""
    return await editar_empleado(
        session, tenant_id=tenant_id, employee_id=employee_id, status="inactive"
    )


async def listar_empleados(
    session: AsyncSession, *, tenant_id: UUID, status: str | None = None, q: str | None = None
) -> list[dict[str, Any]]:
    """Empleados del tenant, alfabético por nombre. `status` filtra por `active`/`inactive`
    (por defecto, ambos) — `ValueError` si no es uno de esos dos. `q` busca por nombre, email
    o puesto (`ILIKE`, sin distinguir mayúsculas)."""
    if status is not None and status not in _EMPLEADO_STATUS_VALIDOS:
        raise ValueError(f"status debe ser uno de {_EMPLEADO_STATUS_VALIDOS}.")

    params: dict[str, Any] = {"tenant_id": str(tenant_id)}
    filtro_sql = ""
    if status is not None:
        filtro_sql += " AND status = :status"
        params["status"] = status
    if q:
        filtro_sql += " AND (nombre ILIKE :patron OR email ILIKE :patron OR puesto ILIKE :patron)"
        params["patron"] = f"%{q}%"

    result = await session.execute(
        text(
            "SELECT * FROM employees WHERE tenant_id = :tenant_id ::uuid"
            f"{filtro_sql} ORDER BY nombre ASC"
        ),
        params,
    )
    return [dict(r) for r in result.mappings().all()]


async def obtener_empleado(
    session: AsyncSession, *, tenant_id: UUID, employee_id: UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text("SELECT * FROM employees WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid"),
            {"id": str(employee_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def obtener_empleado_por_nombre(
    session: AsyncSession, *, tenant_id: UUID, nombre: str
) -> dict[str, Any] | None:
    """Un empleado por nombre (case-insensitive), o `None` si no existe — usado por
    `tools.py` para que `gestionar_empleado`/`registrar_ausencia` identifiquen empleados por
    nombre (nunca le piden un UUID crudo al modelo, mismo criterio que
    `inventory.obtener_producto_por_sku`/`edecan_toolkit.contactos`). Si hay más de un
    empleado con el mismo nombre, devuelve el más antiguo (`ORDER BY created_at ASC`) de
    forma determinista."""
    nombre_norm = str(nombre or "").strip()
    row = (
        await session.execute(
            text(
                "SELECT * FROM employees WHERE tenant_id = :tenant_id ::uuid "
                "AND lower(nombre) = lower(:nombre) ORDER BY created_at ASC LIMIT 1"
            ),
            {"tenant_id": str(tenant_id), "nombre": nombre_norm},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Ausencias (time_off)
# ---------------------------------------------------------------------------


async def registrar_ausencia(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    employee_id: UUID,
    kind: str,
    desde: date | str,
    hasta: date | str,
    notas: str = "",
) -> dict[str, Any] | None:
    """Registra una ausencia nueva, SIEMPRE `status='pending'` (ver el docstring del módulo).
    `kind` debe ser uno de `vacaciones`/`enfermedad`/`permiso`/`otro`; `desde` no puede ser
    posterior a `hasta`. Rechaza con `AusenciaSolapadaError` si el rango se solapa con OTRA
    ausencia `approved` del MISMO empleado. Devuelve `None` si el empleado no existe (o no es
    de este tenant) — el llamador HTTP lo traduce a `404`.

    `time_off` NO tiene columna `user_id` en el esquema pinned (`ARCHITECTURE.md` §14.b) — a
    diferencia de `stock_moves`/`invoices`, esta función no recibe ni guarda quién registró la
    ausencia."""
    kind_norm = str(kind or "").strip().lower()
    if kind_norm not in _AUSENCIA_KINDS_VALIDOS:
        raise ValueError(f"kind debe ser uno de {_AUSENCIA_KINDS_VALIDOS}.")
    desde_d = _parse_fecha(desde, "desde")
    hasta_d = _parse_fecha(hasta, "hasta")
    if desde_d > hasta_d:
        raise ValueError("desde no puede ser posterior a hasta.")

    empleado = (
        await session.execute(
            text(
                "SELECT id FROM employees WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid"
            ),
            {"id": str(employee_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    if empleado is None:
        return None

    solapada = (
        await session.execute(
            text(
                "SELECT id FROM time_off WHERE tenant_id = :tenant_id ::uuid "
                "AND employee_id = :employee_id ::uuid AND status = :status_aprobada "
                "AND hasta >= :desde AND desde <= :hasta"
            ),
            {
                "tenant_id": str(tenant_id),
                "employee_id": str(employee_id),
                "status_aprobada": _AUSENCIA_STATUS_APPROVED,
                "desde": desde_d,
                "hasta": hasta_d,
            },
        )
    ).mappings().first()
    if solapada is not None:
        raise AusenciaSolapadaError(
            "Ese rango de fechas se solapa con otra ausencia ya aprobada de este empleado."
        )

    row = (
        await session.execute(
            text(
                "INSERT INTO time_off "
                "(tenant_id, employee_id, kind, desde, hasta, notas) "
                "VALUES (:tenant_id ::uuid, :employee_id ::uuid, :kind, :desde, :hasta, :notas) "
                "RETURNING *"
            ),
            {
                "tenant_id": str(tenant_id),
                "employee_id": str(employee_id),
                "kind": kind_norm,
                "desde": desde_d,
                "hasta": hasta_d,
                "notas": notas or "",
            },
        )
    ).mappings().first()
    if row is None:  # defensivo: no debería pasar nunca (acabamos de insertar).
        raise RuntimeError("No se pudo registrar la ausencia (fila no devuelta por Postgres).")
    await session.flush()
    return dict(row)


async def listar_ausencias(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    employee_id: UUID | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Ausencias del tenant, más recientes primero (por `desde`). Filtros opcionales por
    `employee_id` y/o `status` (`pending`/`approved`/`rejected`/`cancelled`, `ValueError` si
    no es uno de esos cuatro — ver el docstring del módulo sobre por qué `cancelled` es
    filtrable aunque `resolver_ausencia` no lo produzca)."""
    if status is not None and status not in _AUSENCIA_STATUS_VALIDOS:
        raise ValueError(f"status debe ser uno de {_AUSENCIA_STATUS_VALIDOS}.")

    params: dict[str, Any] = {"tenant_id": str(tenant_id)}
    filtro_sql = ""
    if employee_id is not None:
        filtro_sql += " AND employee_id = :employee_id ::uuid"
        params["employee_id"] = str(employee_id)
    if status is not None:
        filtro_sql += " AND status = :status"
        params["status"] = status

    result = await session.execute(
        text(
            "SELECT * FROM time_off WHERE tenant_id = :tenant_id ::uuid"
            f"{filtro_sql} ORDER BY desde DESC"
        ),
        params,
    )
    return [dict(r) for r in result.mappings().all()]


async def _lock_time_off(
    session: AsyncSession, *, tenant_id: UUID, time_off_id: UUID
) -> dict[str, Any] | None:
    """`SELECT ... FOR UPDATE` sobre `time_off` — mismo patrón exacto que `_lock_payroll_run`
    (más abajo) e `inventory.registrar_movimiento` documentan: toma el lock de fila ANTES de
    leer `status`, así que dos llamadas concurrentes a `resolver_ausencia` sobre LA MISMA
    ausencia (p. ej. "aprobar" y "rechazar" disparadas casi al mismo tiempo por dos personas
    distintas revisando la misma solicitud) se serializan (la segunda espera a que la primera
    confirme) en vez de correr la carrera check-then-act de leer un `status` que ya quedó
    obsoleto — así una doble resolución concurrente SIEMPRE encuentra la ausencia ya
    `'approved'`/`'rejected'` en la segunda vuelta y cae en `EstadoAusenciaError`, nunca la
    resuelve dos veces. Reproducido y verificado contra Postgres real (WP-V7-01): sin este
    lock, dos `resolver_ausencia` concurrentes (una `aprobar=True`, otra `aprobar=False`)
    podían AMBAS "tener éxito" sin lanzar nunca `EstadoAusenciaError`, porque el `UPDATE` de
    abajo no repite el filtro `status = 'pending'` en su `WHERE` — el invariante solo lo
    imponía código Python contra una lectura ya obsoleta para la segunda transacción."""
    row = (
        await session.execute(
            text(
                "SELECT * FROM time_off "
                "WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid FOR UPDATE"
            ),
            {"id": str(time_off_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def resolver_ausencia(
    session: AsyncSession, *, tenant_id: UUID, time_off_id: UUID, aprobar: bool
) -> dict[str, Any] | None:
    """Aprueba (`aprobar=True`) o rechaza (`aprobar=False`) una ausencia — SOLO permitido
    desde `status='pending'` (`EstadoAusenciaError` si ya se resolvió antes, ver su
    docstring). `time_off` no tiene columna `approved_at` (ver el docstring del módulo): esta
    función solo toca `status`/`updated_at`. Devuelve `None` si la ausencia no existe (o no es
    de este tenant) — el llamador HTTP lo traduce a `404`.

    El `SELECT` inicial toma el lock de fila (`_lock_time_off`, `FOR UPDATE`) ANTES de leer
    `status` — ver su docstring para el porqué (carrera real, reproducida y cerrada en
    WP-V7-01)."""
    actual = await _lock_time_off(session, tenant_id=tenant_id, time_off_id=time_off_id)
    if actual is None:
        return None
    if actual["status"] != _AUSENCIA_STATUS_PENDING:
        raise EstadoAusenciaError(
            f"Esta ausencia ya está en estado '{actual['status']}'; solo se puede "
            "aprobar/rechazar una ausencia 'pending'."
        )

    nuevo_status = _AUSENCIA_STATUS_APPROVED if aprobar else _AUSENCIA_STATUS_REJECTED
    row = (
        await session.execute(
            text(
                "UPDATE time_off SET status = :status, updated_at = now() "
                "WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid RETURNING *"
            ),
            {"status": nuevo_status, "id": str(time_off_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    await session.flush()
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Nómina (payroll_runs / payroll_items)
# ---------------------------------------------------------------------------


def _con_totales_calculados(
    payroll_run: dict[str, Any], items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Agrega `total_bruto`/`total_deducciones` CALCULADOS (sumando `items`, nunca
    persistidos: `payroll_runs` solo guarda `total` = neto, ver el docstring del módulo) al
    dict de una corrida — usado tanto por `calcular_nomina` (recién creada) como por
    `obtener_nomina` (ya existente), para que el frontend siempre reciba el mismo desglose sin
    tener que sumarlo él mismo."""
    payroll_run = dict(payroll_run)
    payroll_run["total_bruto"] = _round2(
        sum((Decimal(str(i["bruto"])) for i in items), Decimal("0"))
    )
    payroll_run["total_deducciones"] = _round2(
        sum((Decimal(str(i["deducciones"])) for i in items), Decimal("0"))
    )
    return payroll_run


async def calcular_nomina(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    periodo: str,
    deducciones_pct: Decimal | float | int | str | None = None,
    moneda: str = _MONEDA_DEFECTO,
    notas: str = "",
) -> dict[str, Any]:
    """Genera un BORRADOR de nómina (`payroll_runs.status='draft'`) para `periodo`
    (`"YYYY-MM"`): toma cada empleado `active` CON `salario_mensual` asignado, `bruto =
    salario_mensual`, `deducciones = round(bruto · deducciones_pct / 100, 2)` (0 si
    `deducciones_pct` se omite), `neto = bruto - deducciones`. `deducciones_pct` es un
    porcentaje GLOBAL opcional (0-50, `ValueError` fuera de rango) aplicado por igual a todos
    los empleados de esta corrida — no hay deducciones individuales todavía (ver
    `docs/rrhh.md`, "Qué queda para P2"). NUNCA se persiste como columna propia (el esquema
    pinned no la tiene): solo alimenta el cálculo de cada línea.

    Inserta `payroll_runs` + todos sus `payroll_items` en UNA sola transacción de request
    (sin commit intermedio — mismo principio que `invoices.crear_factura`). Todos los montos
    son `Decimal` con redondeo bancario a 2 decimales en cada paso — JAMÁS `float` (mismo
    criterio que `invoices.compute_totals`). `payroll_runs.total` (única columna persistida)
    guarda el NETO (`Σ payroll_items.neto`); el dict devuelto además trae `total_bruto`/
    `total_deducciones` CALCULADOS (ver `_con_totales_calculados`).

    Empleados `inactive`, o `active` pero sin `salario_mensual` asignado, se EXCLUYEN en
    silencio (no es un error: es información — el resumen de la corrida deja ver cuántos
    empleados entraron). Si ningún empleado califica, igual se crea el `draft` con 0 items y
    totales en 0 (nunca es un `ValueError`: una nómina vacía es información válida, no un dato
    inválido).

    **Nunca mueve dinero** (ver el docstring del módulo): esta función es aritmética +
    persistencia de un borrador, punto. `aprobar_nomina` es el único paso siguiente posible, y
    tampoco mueve dinero — solo registra una aprobación.
    """
    periodo_norm = _validar_periodo(periodo)
    pct = _validar_deducciones_pct(deducciones_pct)
    moneda_norm = _normalizar_moneda(moneda)

    empleados = (
        await session.execute(
            text(
                "SELECT id, nombre, salario_mensual FROM employees "
                "WHERE tenant_id = :tenant_id ::uuid AND status = 'active' "
                "AND salario_mensual IS NOT NULL ORDER BY nombre ASC"
            ),
            {"tenant_id": str(tenant_id)},
        )
    ).mappings().all()

    items_calculados: list[dict[str, Any]] = []
    total_neto = Decimal("0.00")
    for empleado in empleados:
        bruto = _round2(Decimal(str(empleado["salario_mensual"])))
        deducciones = _round2(bruto * pct / Decimal("100")) if pct else Decimal("0.00")
        neto = _round2(bruto - deducciones)
        items_calculados.append(
            {
                "employee_id": empleado["id"],
                "empleado_nombre": empleado["nombre"],
                "bruto": bruto,
                "deducciones": deducciones,
                "neto": neto,
            }
        )
        total_neto += neto

    run_row = (
        await session.execute(
            text(
                "INSERT INTO payroll_runs "
                "(tenant_id, user_id, periodo, status, total, moneda, notas) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, :periodo, 'draft', :total, "
                ":moneda, :notas) RETURNING *"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "periodo": periodo_norm,
                "total": _round2(total_neto),
                "moneda": moneda_norm,
                "notas": notas or "",
            },
        )
    ).mappings().first()
    if run_row is None:  # defensivo: no debería pasar nunca (acabamos de insertar).
        raise RuntimeError("No se pudo crear la nómina (fila no devuelta por Postgres).")
    payroll_run = dict(run_row)
    payroll_run_id = payroll_run["id"]

    items_rows: list[dict[str, Any]] = []
    for item in items_calculados:
        item_row = (
            await session.execute(
                text(
                    "INSERT INTO payroll_items "
                    "(tenant_id, payroll_run_id, employee_id, bruto, deducciones, neto) "
                    "VALUES (:tenant_id ::uuid, :payroll_run_id ::uuid, :employee_id ::uuid, "
                    ":bruto, :deducciones, :neto) RETURNING *"
                ),
                {
                    "tenant_id": str(tenant_id),
                    "payroll_run_id": str(payroll_run_id),
                    "employee_id": str(item["employee_id"]),
                    "bruto": item["bruto"],
                    "deducciones": item["deducciones"],
                    "neto": item["neto"],
                },
            )
        ).mappings().first()
        if item_row is None:  # defensivo: idem arriba.
            raise RuntimeError("No se pudo crear una línea de la nómina.")
        item_dict = dict(item_row)
        item_dict["empleado_nombre"] = item["empleado_nombre"]
        items_rows.append(item_dict)

    await session.flush()
    payroll_run = _con_totales_calculados(payroll_run, items_rows)
    payroll_run["items"] = items_rows
    return payroll_run


async def listar_nominas(
    session: AsyncSession, *, tenant_id: UUID, status: str | None = None
) -> list[dict[str, Any]]:
    """Corridas de nómina del tenant, más recientes primero — SIN sus items (ver
    `obtener_nomina` para el detalle con items). `status` filtra por
    `draft`/`approved`/`paid`/`cancelled` (`ValueError` si no es uno de esos cuatro — ver el
    docstring del módulo sobre por qué `paid` es filtrable aunque este work package nunca lo
    escriba)."""
    if status is not None and status not in _PAYROLL_STATUS_VALIDOS:
        raise ValueError(f"status debe ser uno de {_PAYROLL_STATUS_VALIDOS}.")

    params: dict[str, Any] = {"tenant_id": str(tenant_id)}
    filtro_sql = ""
    if status is not None:
        filtro_sql = " AND status = :status"
        params["status"] = status

    result = await session.execute(
        text(
            "SELECT * FROM payroll_runs WHERE tenant_id = :tenant_id ::uuid"
            f"{filtro_sql} ORDER BY created_at DESC"
        ),
        params,
    )
    return [dict(r) for r in result.mappings().all()]


async def obtener_nomina(
    session: AsyncSession, *, tenant_id: UUID, payroll_run_id: UUID
) -> dict[str, Any] | None:
    """Una corrida de nómina con sus `items` anidados (cada item trae `empleado_nombre` vía
    `JOIN` con `employees`, para que el frontend no tenga que resolverlo aparte) y
    `total_bruto`/`total_deducciones` CALCULADOS (ver `_con_totales_calculados`), o `None` si
    no existe (o no es de este tenant)."""
    row = (
        await session.execute(
            text(
                "SELECT * FROM payroll_runs WHERE tenant_id = :tenant_id ::uuid "
                "AND id = :id ::uuid"
            ),
            {"tenant_id": str(tenant_id), "id": str(payroll_run_id)},
        )
    ).mappings().first()
    if row is None:
        return None

    items_result = await session.execute(
        text(
            "SELECT pi.*, e.nombre AS empleado_nombre FROM payroll_items pi "
            "JOIN employees e ON e.id = pi.employee_id "
            "WHERE pi.tenant_id = :tenant_id ::uuid AND pi.payroll_run_id = :payroll_run_id ::uuid "
            "ORDER BY e.nombre ASC"
        ),
        {"tenant_id": str(tenant_id), "payroll_run_id": str(payroll_run_id)},
    )
    items = [dict(r) for r in items_result.mappings().all()]
    payroll_run = _con_totales_calculados(dict(row), items)
    payroll_run["items"] = items
    return payroll_run


async def _lock_payroll_run(
    session: AsyncSession, *, tenant_id: UUID, payroll_run_id: UUID
) -> dict[str, Any] | None:
    """`SELECT ... FOR UPDATE` sobre `payroll_runs` — toma el lock de fila ANTES de leer
    `status`, mismo patrón exacto que `inventory.registrar_movimiento` documenta para
    `products`: dos llamadas concurrentes a `aprobar_nomina`/`cancelar_nomina` sobre la MISMA
    corrida se serializan (la segunda espera a que la primera confirme) en vez de correr la
    carrera check-then-act de leer un `status` que ya quedó obsoleto — así una doble
    aprobación concurrente SIEMPRE encuentra la corrida ya `'approved'`/`'cancelled'` en la
    segunda vuelta y cae en `EstadoNominaError`, nunca aprueba dos veces."""
    row = (
        await session.execute(
            text(
                "SELECT * FROM payroll_runs "
                "WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid FOR UPDATE"
            ),
            {"id": str(payroll_run_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def aprobar_nomina(
    session: AsyncSession, *, tenant_id: UUID, payroll_run_id: UUID
) -> dict[str, Any] | None:
    """`draft → approved` + `approved_at`. SOLO permitido desde `'draft'` — reaprobar una
    corrida ya `'approved'` (o cancelada) lanza `EstadoNominaError` (idempotencia explícita,
    `409` en el router). Devuelve `None` si la corrida no existe (o no es de este tenant).

    **Nunca mueve dinero** (ver el docstring del módulo): esto es un cambio de `status` en
    una fila propia del tenant, nada más — ningún proveedor de pagos, ninguna llamada de red,
    ninguna transferencia. El guardrail de confirmación explícita (`{"confirmar": true}`)
    vive en el router, no acá — esta función asume que quien la llama YA obtuvo esa
    confirmación."""
    run = await _lock_payroll_run(session, tenant_id=tenant_id, payroll_run_id=payroll_run_id)
    if run is None:
        return None
    if run["status"] != _PAYROLL_STATUS_DRAFT:
        raise EstadoNominaError(
            f"La nómina está en estado '{run['status']}'; solo se puede aprobar una nómina "
            "en estado 'draft' (evita doble aprobación)."
        )

    row = (
        await session.execute(
            text(
                "UPDATE payroll_runs SET status = 'approved', approved_at = now(), "
                "updated_at = now() WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid "
                "RETURNING *"
            ),
            {"id": str(payroll_run_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    await session.flush()
    return dict(row) if row is not None else None


async def cancelar_nomina(
    session: AsyncSession, *, tenant_id: UUID, payroll_run_id: UUID
) -> dict[str, Any] | None:
    """`draft → cancelled`. SOLO permitido desde `'draft'` (mismo criterio que
    `aprobar_nomina`: cancelar una corrida ya `'approved'`/`'cancelled'` lanza
    `EstadoNominaError`, `409` en el router). Devuelve `None` si no existe."""
    run = await _lock_payroll_run(session, tenant_id=tenant_id, payroll_run_id=payroll_run_id)
    if run is None:
        return None
    if run["status"] != _PAYROLL_STATUS_DRAFT:
        raise EstadoNominaError(
            f"La nómina está en estado '{run['status']}'; solo se puede cancelar una nómina "
            "en estado 'draft'."
        )

    row = (
        await session.execute(
            text(
                "UPDATE payroll_runs SET status = 'cancelled', updated_at = now() "
                "WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid RETURNING *"
            ),
            {"id": str(payroll_run_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    await session.flush()
    return dict(row) if row is not None else None
