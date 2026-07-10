"""Finanzas personales (`ARCHITECTURE.md` §10.3, tabla `transactions`).

Convención de signo: `monto` positivo = ingreso, negativo = gasto (no hay un
campo `tipo` aparte en el esquema pinned, así que el signo es la fuente de verdad).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text

_MONEDA_DEFECTO = "USD"
_TOP_CATEGORIAS = 5


class RegistrarTransaccionTool(Tool):
    name = "registrar_transaccion"
    description = (
        "Registra un ingreso o gasto del usuario. Usa un monto positivo para "
        "ingresos y negativo para gastos."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "monto": {
                "type": "number",
                "description": "Monto de la transacción (positivo=ingreso, negativo=gasto).",
            },
            "categoria": {
                "type": "string",
                "description": (
                    "Categoría de la transacción (ej. 'comida', 'transporte', 'salario')."
                ),
            },
            "descripcion": {"type": "string", "description": "Descripción breve, opcional."},
            "fecha": {
                "type": "string",
                "description": "Fecha ISO (YYYY-MM-DD) de la transacción. Por defecto: hoy.",
            },
            "moneda": {
                "type": "string",
                "description": "Código ISO-4217 de tres letras.",
                "default": _MONEDA_DEFECTO,
            },
            "cuenta": {"type": "string", "description": "Cuenta o método de pago, opcional."},
        },
        "required": ["monto", "categoria"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        if "monto" not in args or args["monto"] is None:
            return ToolResult(content="Falta el monto de la transacción.")
        try:
            monto = Decimal(str(args["monto"]))
        except InvalidOperation:
            return ToolResult(content=f"'{args['monto']}' no es un monto válido.")

        categoria = str(args.get("categoria", "")).strip() or "general"
        descripcion = args.get("descripcion") or ""
        moneda = str(args.get("moneda") or _MONEDA_DEFECTO).upper()
        if len(moneda) != 3 or not moneda.isalpha():
            moneda = _MONEDA_DEFECTO
        cuenta = args.get("cuenta") or ""

        fecha_str = args.get("fecha")
        try:
            fecha = date.fromisoformat(fecha_str) if fecha_str else date.today()
        except ValueError:
            return ToolResult(content=f"'{fecha_str}' no es una fecha válida (usa YYYY-MM-DD).")

        resultado = await ctx.session.execute(
            text(
                "INSERT INTO transactions "
                "(tenant_id, user_id, fecha, monto, moneda, categoria, descripcion, cuenta) "
                "VALUES (:tenant_id, :user_id, :fecha, :monto, :moneda, :categoria, "
                ":descripcion, :cuenta) "
                "RETURNING id"
            ),
            {
                "tenant_id": str(ctx.tenant_id),
                "user_id": str(ctx.user_id),
                "fecha": fecha,
                "monto": monto,
                "moneda": moneda,
                "categoria": categoria,
                "descripcion": descripcion,
                "cuenta": cuenta,
            },
        )
        fila = resultado.mappings().first()

        tipo = "ingreso" if monto >= 0 else "gasto"
        return ToolResult(
            content=(
                f"Registré un {tipo} de {moneda} {abs(monto):,.2f} en «{categoria}» "
                f"({fecha.isoformat()})."
            ),
            data={
                "id": str(fila["id"]) if fila else None,
                "monto": float(monto),
                "moneda": moneda,
                "categoria": categoria,
                "fecha": fecha.isoformat(),
            },
        )


class ResumenFinanzasTool(Tool):
    name = "resumen_finanzas"
    description = (
        "Resume ingresos, gastos, balance y top categorías del mes indicado, "
        "en texto claro estilo CFO personal."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "mes": {
                "type": "string",
                "description": "Mes a resumir, formato YYYY-MM. Por defecto: mes actual.",
            },
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        mes = str(args.get("mes") or date.today().strftime("%Y-%m"))
        rango = _rango_del_mes(mes)
        if rango is None:
            return ToolResult(content=f"'{mes}' no es un mes válido (usa YYYY-MM).")
        inicio, fin = rango

        resultado = await ctx.session.execute(
            text(
                "SELECT categoria, SUM(monto) AS total, COUNT(*) AS n FROM transactions "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id "
                "AND fecha >= :inicio AND fecha < :fin "
                "GROUP BY categoria ORDER BY total DESC"
            ),
            {
                "tenant_id": str(ctx.tenant_id),
                "user_id": str(ctx.user_id),
                "inicio": inicio,
                "fin": fin,
            },
        )
        filas = resultado.mappings().all()

        if not filas:
            return ToolResult(
                content=f"No hay transacciones registradas en {mes}.",
                data={
                    "mes": mes,
                    "ingresos": 0.0,
                    "gastos": 0.0,
                    "balance": 0.0,
                    "por_categoria": [],
                },
            )

        por_categoria = [
            {"categoria": f["categoria"], "total": float(f["total"]), "n": int(f["n"])}
            for f in filas
        ]
        ingresos = sum(c["total"] for c in por_categoria if c["total"] > 0)
        gastos = sum(c["total"] for c in por_categoria if c["total"] < 0)
        balance = ingresos + gastos
        n_total = sum(c["n"] for c in por_categoria)

        top_gastos = sorted((c for c in por_categoria if c["total"] < 0), key=lambda c: c["total"])[
            :_TOP_CATEGORIAS
        ]

        lineas = [
            f"Resumen financiero de {mes}:",
            f"  Ingresos: ${ingresos:,.2f}",
            f"  Gastos: ${abs(gastos):,.2f}",
            f"  Balance neto: ${balance:,.2f}",
        ]
        if top_gastos:
            lineas.append("Top categorías de gasto:")
            lineas.extend(
                f"  {i}. {c['categoria']} — ${abs(c['total']):,.2f} ({c['n']} transacciones)"
                for i, c in enumerate(top_gastos, start=1)
            )
        lineas.append(f"({n_total} transacciones en total)")

        return ToolResult(
            content="\n".join(lineas),
            data={
                "mes": mes,
                "ingresos": float(ingresos),
                "gastos": float(gastos),
                "balance": float(balance),
                "por_categoria": por_categoria,
            },
        )


def _rango_del_mes(mes: str) -> tuple[date, date] | None:
    try:
        anio_str, mes_str = mes.split("-", 1)
        anio, mes_num = int(anio_str), int(mes_str)
        inicio = date(anio, mes_num, 1)
    except (ValueError, TypeError):
        return None
    fin = date(anio + 1, 1, 1) if mes_num == 12 else date(anio, mes_num + 1, 1)
    return inicio, fin
