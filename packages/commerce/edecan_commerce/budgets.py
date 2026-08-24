"""Presupuestos por categoría (`ROADMAP_V2.md` §7.4, tabla `budgets`) y su estado mensual.

`estado_presupuestos` reutiliza la tabla `transactions` v1 (`ARCHITECTURE.md` §10.3,
`packages/toolkit/edecan_toolkit/finanzas.py` es la tool que la alimenta) para calcular
"gastado" = suma de transacciones **negativas** del mes en esa categoría — no hay un cálculo
propio de gasto en este módulo, se apoya en el ledger de finanzas que ya existe.

Funciones puras que reciben `session` explícito (no `ToolContext`) para que las use tanto
`tools.py` (con `ctx.session`) como `apps/api/edecan_api/routers/commerce.py` (con su propia
sesión de tenant vía `edecan_api.deps.get_tenant_session`) sin duplicar el cálculo del `%`
gastado / alerta en dos lugares.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ALERTA_PCT = 90.0


def _normalizar_categoria(categoria: str) -> str:
    categoria = (categoria or "").strip()
    if not categoria:
        raise ValueError("categoria es obligatoria.")
    return categoria


def _normalizar_moneda(moneda: str | None) -> str:
    moneda = (moneda or "USD").strip().upper()
    return moneda if len(moneda) == 3 and moneda.isalpha() else "USD"


async def fijar_presupuesto(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    categoria: str,
    monto_mensual: Decimal,
    moneda: str = "USD",
) -> dict[str, Any]:
    """Crea o actualiza (por `(tenant_id, user_id, categoria)`) el presupuesto mensual.

    No hay una restricción `UNIQUE` fijada por el contrato (`ROADMAP_V2.md` §7.4) para usar
    `ON CONFLICT` — mismo criterio que `edecan_toolkit.contactos.GestionarContactoTool`: se
    resuelve con un `SELECT` seguido de `UPDATE`/`INSERT` explícito.
    """
    categoria = _normalizar_categoria(categoria)
    if monto_mensual is None or monto_mensual <= 0:
        raise ValueError("monto_mensual debe ser mayor que cero.")
    moneda = _normalizar_moneda(moneda)

    existing = (
        await session.execute(
            text(
                "SELECT id FROM budgets WHERE tenant_id = :tenant_id ::uuid "
                "AND user_id = :user_id ::uuid AND categoria = :categoria"
            ),
            {"tenant_id": str(tenant_id), "user_id": str(user_id), "categoria": categoria},
        )
    ).mappings().first()

    if existing is None:
        row = (
            await session.execute(
                text(
                    "INSERT INTO budgets (tenant_id, user_id, categoria, monto_mensual, moneda) "
                    "VALUES (:tenant_id ::uuid, :user_id ::uuid, :categoria, :monto_mensual, "
                    ":moneda) RETURNING *"
                ),
                {
                    "tenant_id": str(tenant_id),
                    "user_id": str(user_id),
                    "categoria": categoria,
                    "monto_mensual": monto_mensual,
                    "moneda": moneda,
                },
            )
        ).mappings().first()
    else:
        row = (
            await session.execute(
                text(
                    "UPDATE budgets SET monto_mensual = :monto_mensual, moneda = :moneda, "
                    "updated_at = now() WHERE id = :id ::uuid RETURNING *"
                ),
                {"id": str(existing["id"]), "monto_mensual": monto_mensual, "moneda": moneda},
            )
        ).mappings().first()

    if row is None:  # defensivo: no debería pasar nunca (acabamos de insertar/actualizar).
        raise RuntimeError("No se pudo fijar el presupuesto (fila no devuelta por Postgres).")
    await session.flush()
    return dict(row)


async def listar_presupuestos(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT * FROM budgets WHERE tenant_id = :tenant_id ::uuid "
            "AND user_id = :user_id ::uuid ORDER BY categoria"
        ),
        {"tenant_id": str(tenant_id), "user_id": str(user_id)},
    )
    return [dict(r) for r in result.mappings().all()]


async def estado_presupuestos(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID, mes: str | None = None
) -> list[dict[str, Any]]:
    """Estado del mes por categoría presupuestada: `gastado`, `%` y `alerta` (`> 90%`).

    `mes` es `"YYYY-MM"`; por defecto, el mes actual. Categorías sin presupuesto fijado no
    aparecen (no hay nada contra qué medir el `%`); categorías presupuestadas sin ningún
    gasto este mes sí aparecen, con `gastado=0`.
    """
    mes = mes or date.today().strftime("%Y-%m")
    result = await session.execute(
        text(
            """
            SELECT
                b.id, b.categoria, b.monto_mensual, b.moneda,
                COALESCE(g.gastado, 0) AS gastado
            FROM budgets b
            LEFT JOIN (
                SELECT categoria, SUM(-monto) AS gastado
                FROM transactions
                WHERE tenant_id = :tenant_id ::uuid AND user_id = :user_id ::uuid
                  AND monto < 0 AND to_char(fecha, 'YYYY-MM') = :mes
                GROUP BY categoria
            ) g ON g.categoria = b.categoria
            WHERE b.tenant_id = :tenant_id ::uuid AND b.user_id = :user_id ::uuid
            ORDER BY b.categoria
            """
        ),
        {"tenant_id": str(tenant_id), "user_id": str(user_id), "mes": mes},
    )

    estado: list[dict[str, Any]] = []
    for fila in result.mappings().all():
        monto_mensual = Decimal(str(fila["monto_mensual"]))
        gastado = Decimal(str(fila["gastado"]))
        pct = float(gastado / monto_mensual * 100) if monto_mensual > 0 else 0.0
        estado.append(
            {
                "id": fila["id"],
                "categoria": fila["categoria"],
                "monto_mensual": float(monto_mensual),
                "moneda": fila["moneda"],
                "gastado": float(gastado),
                "pct": round(pct, 1),
                "alerta": pct > ALERTA_PCT,
                "mes": mes,
            }
        )
    return estado
