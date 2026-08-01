"""KPIs mensuales del negocio (`ROADMAP_V2.md` §7.4/§7.7, WP-V2-12 — pantalla 4 del mockup
de `REQUISITOS_V2.md`: ingresos/gastos/beneficio/nuevos clientes + dona de ventas por canal
+ actividad reciente).

`kpis_mes` es una función pura (recibe `session` explícito, no `ToolContext` — mismo
criterio que `invoices.py`) que reutiliza tres tablas ya existentes, sin duplicar ningún
cálculo financiero:

- `transactions` (v1, `ARCHITECTURE.md` §10.3) → `ingresos`/`gastos`/`beneficio` y la dona
  "ventas por canal" (agrupada por el campo `cuenta`).
- `contacts` (v1) → `nuevos_clientes`.
- `invoices`/`invoice_items` (este mismo paquete) → `facturado`/`cobrado`.

**Supuesto importante, documentado a propósito (evita doble conteo)**: `ingresos` sale
ÚNICAMENTE de `transactions`. Una factura es un DOCUMENTO — una promesa/registro de cobro —,
no un ingreso por sí sola con solo crearla o marcarla `sent`. El cruce entre "facturé" y
"de verdad cobré" lo hace el USUARIO a mano: cuando cobra una factura de verdad, además de
marcarla `paid` (`POST /v1/negocios/facturas/{id}/estado`) debe registrar la transacción de
ingreso correspondiente (`registrar_transaccion` / `POST /v1/finance/transactions`) para que
aparezca en `ingresos`. Si `ingresos` sumara automáticamente `invoices.total`, cualquier
tenant que YA registra sus ingresos a mano en `transactions` vería el mismo cobro contado
dos veces. `facturado`/`cobrado` quedan como métricas informativas de la cartera de
facturas, deliberadamente separadas de `ingresos`/`beneficio`.

Todo está acotado al mes pedido (`mes`, formato `"YYYY-MM"`) y a `(tenant_id, user_id)` —
mismo alcance por-usuario que `edecan_toolkit.finanzas.ResumenFinanzasTool` y
`edecan_commerce.budgets.estado_presupuestos`: cada usuario ve el estado de SU negocio
dentro del tenant.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_TOP_CANALES = 6
_OTROS_LABEL = "otros"
_SIN_CUENTA_LABEL = "sin cuenta"
_ACTIVIDAD_LIMITE = 10


def _rango_del_mes(mes: str) -> tuple[date, date]:
    try:
        anio_str, mes_str = mes.split("-", 1)
        anio, mes_num = int(anio_str), int(mes_str)
        inicio = date(anio, mes_num, 1)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"'{mes}' no es un mes válido (usa YYYY-MM).") from exc
    if not 1 <= mes_num <= 12:
        raise ValueError(f"'{mes}' no es un mes válido (usa YYYY-MM).")
    fin = date(anio + 1, 1, 1) if mes_num == 12 else date(anio, mes_num + 1, 1)
    return inicio, fin


def _as_datetime(value: Any) -> datetime:
    """Normaliza `date`/`datetime`/texto ISO a un `datetime` tz-aware (UTC si no trae zona)
    para poder ordenar juntos `invoices.created_at` (`timestamptz`) y `transactions.fecha`
    (`date`) sin que Python reviente con `TypeError: can't compare datetime.date to
    datetime.datetime` — comparar por texto ISO sería más simple pero un `date` sin hora
    ("2026-07-08") ordena mal contra un `datetime` del mismo día con hora
    ("2026-07-08T10:00:00") por ser un prefijo más corto; normalizar a `datetime` real evita
    esa trampa.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


async def kpis_mes(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID, mes: str | None = None
) -> dict[str, Any]:
    """KPIs del mes `mes` (`"YYYY-MM"`, por defecto el mes actual UTC): ver el docstring del
    módulo para el detalle de cada campo y el criterio anti-doble-conteo. Lanza `ValueError`
    si `mes` no tiene el formato esperado.
    """
    mes = mes or date.today().strftime("%Y-%m")
    inicio, fin = _rango_del_mes(mes)
    params = {
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "inicio": inicio,
        "fin": fin,
    }

    tx_result = await session.execute(
        text(
            "SELECT monto, cuenta FROM transactions WHERE tenant_id = :tenant_id ::uuid "
            "AND user_id = :user_id ::uuid AND fecha >= :inicio AND fecha < :fin"
        ),
        params,
    )
    filas_tx = tx_result.mappings().all()

    ingresos = Decimal("0")
    gastos = Decimal("0")  # se acumula negativo (es la suma de montos ya negativos)
    por_canal_acc: dict[str, Decimal] = {}
    for fila in filas_tx:
        monto = Decimal(str(fila["monto"]))
        if monto > 0:
            ingresos += monto
            canal = (fila["cuenta"] or "").strip() or _SIN_CUENTA_LABEL
            por_canal_acc[canal] = por_canal_acc.get(canal, Decimal("0")) + monto
        elif monto < 0:
            gastos += monto
    beneficio = ingresos + gastos
    por_canal = _top_n_mas_otros(por_canal_acc, _TOP_CANALES)

    nuevos_clientes_result = await session.execute(
        text(
            "SELECT COUNT(*) AS n FROM contacts WHERE tenant_id = :tenant_id ::uuid "
            "AND user_id = :user_id ::uuid AND created_at >= :inicio AND created_at < :fin"
        ),
        params,
    )
    nuevos_row = nuevos_clientes_result.mappings().first()
    nuevos_clientes = int(nuevos_row["n"]) if nuevos_row and nuevos_row.get("n") is not None else 0

    facturado_result = await session.execute(
        text(
            "SELECT COALESCE(SUM(total), 0) AS total FROM invoices "
            "WHERE tenant_id = :tenant_id ::uuid AND user_id = :user_id ::uuid "
            "AND status IN ('sent', 'paid') AND created_at >= :inicio AND created_at < :fin"
        ),
        params,
    )
    facturado_row = facturado_result.mappings().first()
    facturado = Decimal(str(facturado_row["total"])) if facturado_row else Decimal("0")

    # `cobrado` se acota por `updated_at` (no `created_at`): es la mejor aproximación
    # disponible hoy de "cuándo se cobró" sin una columna `paid_at` dedicada (P2, ver
    # docstring de `invoices.cambiar_estado`) — una factura creada en junio pero cobrada en
    # julio debe aparecer en `cobrado` de julio, no de junio.
    cobrado_result = await session.execute(
        text(
            "SELECT COALESCE(SUM(total), 0) AS total FROM invoices "
            "WHERE tenant_id = :tenant_id ::uuid AND user_id = :user_id ::uuid "
            "AND status = 'paid' AND updated_at >= :inicio AND updated_at < :fin"
        ),
        params,
    )
    cobrado_row = cobrado_result.mappings().first()
    cobrado = Decimal(str(cobrado_row["total"])) if cobrado_row else Decimal("0")

    actividad = await _actividad_reciente(
        session, tenant_id=tenant_id, user_id=user_id, inicio=inicio, fin=fin
    )

    return {
        "mes": mes,
        "ingresos": float(ingresos),
        "gastos": float(abs(gastos)),
        "beneficio": float(beneficio),
        "nuevos_clientes": nuevos_clientes,
        "facturado": float(facturado),
        "cobrado": float(cobrado),
        "por_canal": por_canal,
        "actividad": actividad,
    }


def _top_n_mas_otros(acumulado: dict[str, Decimal], n: int) -> list[dict[str, Any]]:
    """Los `n` canales de mayor total (desc.), con el resto colapsado en un bucket
    `"otros"` — omitido si no sobra ningún canal fuera del top `n`."""
    ordenado = sorted(acumulado.items(), key=lambda kv: kv[1], reverse=True)
    top, resto = ordenado[:n], ordenado[n:]
    resultado = [{"canal": canal, "total": float(total)} for canal, total in top]
    if resto:
        total_otros = sum((total for _, total in resto), Decimal("0"))
        resultado.append({"canal": _OTROS_LABEL, "total": float(total_otros)})
    return resultado


async def _actividad_reciente(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID, inicio: date, fin: date
) -> list[dict[str, Any]]:
    """Últimos `_ACTIVIDAD_LIMITE` eventos del mes entre `invoices` y `transactions`,
    mezclados y ordenados por fecha descendente. Pide `_ACTIVIDAD_LIMITE` de cada tabla por
    separado (no hay `UNION` posible entre dos esquemas de fila distintos vía `text()` sin
    duplicar columnas) y recorta el resultado combinado en Python — con como mucho 10+10
    filas de entrada esto es trivial, nunca miles de filas.
    """
    params = {"tenant_id": str(tenant_id), "user_id": str(user_id), "inicio": inicio, "fin": fin}

    facturas_result = await session.execute(
        text(
            "SELECT id, numero, cliente_nombre, total, moneda, status, created_at FROM invoices "
            "WHERE tenant_id = :tenant_id ::uuid AND user_id = :user_id ::uuid "
            "AND created_at >= :inicio AND created_at < :fin "
            "ORDER BY created_at DESC LIMIT :limite"
        ),
        {**params, "limite": _ACTIVIDAD_LIMITE},
    )
    eventos: list[dict[str, Any]] = [
        {
            "tipo": "factura",
            "id": str(f["id"]),
            "fecha": f["created_at"],
            "descripcion": f"Factura {f['numero']} — {f['cliente_nombre']}",
            "monto": float(f["total"]),
            "moneda": f["moneda"],
            "status": f["status"],
        }
        for f in facturas_result.mappings().all()
    ]

    tx_result = await session.execute(
        text(
            "SELECT id, descripcion, categoria, monto, moneda, fecha FROM transactions "
            "WHERE tenant_id = :tenant_id ::uuid AND user_id = :user_id ::uuid "
            "AND fecha >= :inicio AND fecha < :fin "
            "ORDER BY fecha DESC LIMIT :limite"
        ),
        {**params, "limite": _ACTIVIDAD_LIMITE},
    )
    eventos.extend(
        {
            "tipo": "transaccion",
            "id": str(t["id"]),
            "fecha": t["fecha"],
            "descripcion": t["descripcion"] or (t["categoria"] or "Transacción"),
            "monto": float(t["monto"]),
            "moneda": t["moneda"],
            "status": None,
        }
        for t in tx_result.mappings().all()
    )

    eventos.sort(key=lambda e: _as_datetime(e["fecha"]), reverse=True)
    eventos = eventos[:_ACTIVIDAD_LIMITE]
    for evento in eventos:
        fecha = evento["fecha"]
        evento["fecha"] = fecha.isoformat() if hasattr(fecha, "isoformat") else str(fecha)
    return eventos
