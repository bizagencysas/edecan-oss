"""Las 4 herramientas de `edecan_commerce` (nombres exactos, `ROADMAP_V2.md` §7.7).

`cotizar_activo` y `gestionar_presupuesto` son de solo lectura/gestión de presupuesto (no
`dangerous`). `preparar_pago`/`preparar_orden` son `dangerous=True`: `edecan_core.agent.
Agent.run_turn` exige confirmación humana del *tool call* antes de correrlas — pero incluso
confirmadas, lo ÚNICO que hacen es insertar una fila `orders(status='draft')`. Jamás
ejecutan nada, jamás mueven dinero: ver `docs/dinero-real.md` y el docstring de `paper.py`
para el guardrail completo. Doble gate: confirmación del tool call en el chat + confirmación
de la orden en la página `/app/ordenes` (`POST /v1/commerce/orders/{id}/confirm`).

Las 4 requieren el flag de plan `commerce.orders` (fila completa de la tabla de
`ROADMAP_V2.md` §7.7, igual que `edecan_browser` gatea sus 3 tools con un único flag
`tools.browser`).
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from edecan_core import Tool, ToolContext, ToolResult
from edecan_schemas.plans import FLAG_COMMERCE_ORDERS
from sqlalchemy import text

from .budgets import estado_presupuestos, fijar_presupuesto, listar_presupuestos
from .quotes import get_quote_provider

_AVISO_MERCADO = "Información de mercado, no consejo de inversión."
_MONEDA_DEFECTO = "USD"


def _parse_monto(valor: Any, campo: str = "monto") -> Decimal | None:
    if valor is None:
        return None
    try:
        return Decimal(str(valor))
    except InvalidOperation:
        return None


async def _crear_orden_draft(
    session: Any,
    *,
    tenant_id: UUID,
    user_id: UUID,
    kind: str,
    descripcion: str,
    monto: Decimal | None = None,
    moneda: str = _MONEDA_DEFECTO,
    simbolo: str | None = None,
    lado: str | None = None,
    cantidad: Decimal | None = None,
    meta: dict[str, Any] | None = None,
) -> Any:
    """INSERT directo en `orders` con `status='draft'` — la ÚNICA cosa que hacen
    `preparar_pago`/`preparar_orden`. No hay ninguna otra sentencia SQL en esta función:
    ver `test_tools.test_preparar_pago_y_preparar_orden_nunca_ejecutan_solo_crean_draft`.
    """
    row = (
        await session.execute(
            text(
                "INSERT INTO orders "
                "(tenant_id, user_id, kind, status, descripcion, monto, moneda, simbolo, "
                "lado, cantidad, meta) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, :kind, 'draft', :descripcion, "
                ":monto, :moneda, :simbolo, :lado, :cantidad, CAST(:meta AS jsonb)) "
                "RETURNING id"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "kind": kind,
                "descripcion": descripcion,
                "monto": monto,
                "moneda": moneda,
                "simbolo": simbolo,
                "lado": lado,
                "cantidad": cantidad,
                "meta": json.dumps(meta or {}),
            },
        )
    ).mappings().first()
    await session.flush()
    if row is None:  # defensivo: Postgres no devolvió la fila recién insertada.
        raise RuntimeError("No se pudo crear el borrador de orden.")
    return row["id"]


class CotizarActivoTool(Tool):
    name = "cotizar_activo"
    description = (
        "Consulta la cotización actual de un activo (cripto o acción). Es solo información "
        "de mercado — nunca un consejo de inversión ni una operación."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "simbolo": {
                "type": "string",
                "description": "Símbolo del activo a cotizar, p. ej. 'BTC', 'ETH', 'AAPL'.",
            },
        },
        "required": ["simbolo"],
    }
    requires_flags = frozenset({FLAG_COMMERCE_ORDERS})

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        simbolo = str(args.get("simbolo", "")).strip().upper()
        if not simbolo:
            return ToolResult(content="Necesito el símbolo del activo a cotizar.")

        proveedor = get_quote_provider(ctx.settings)
        try:
            cotizacion = await proveedor.quote(simbolo)
        except Exception as exc:
            return ToolResult(content=f"No pude cotizar '{simbolo}': {exc}")

        return ToolResult(
            content=(
                f"{cotizacion.simbolo}: {cotizacion.moneda} {cotizacion.precio:,.2f} "
                f"(fuente: {cotizacion.fuente}). {_AVISO_MERCADO}"
            ),
            data={
                "simbolo": cotizacion.simbolo,
                "precio": cotizacion.precio,
                "moneda": cotizacion.moneda,
                "fuente": cotizacion.fuente,
                "ts": cotizacion.ts.isoformat(),
            },
        )


class GestionarPresupuestoTool(Tool):
    name = "gestionar_presupuesto"
    description = (
        "Fija, lista o muestra el estado mensual (gastado vs. presupuestado, con % y "
        "alerta) de los presupuestos por categoría del usuario."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "accion": {
                "type": "string",
                "enum": ["fijar", "listar", "estado"],
                "description": "Qué hacer con los presupuestos.",
            },
            "categoria": {
                "type": "string",
                "description": "Categoría del presupuesto. Requerida para accion='fijar'.",
            },
            "monto": {
                "type": "number",
                "description": "Monto mensual del presupuesto. Requerido para accion='fijar'.",
            },
        },
        "required": ["accion"],
    }
    requires_flags = frozenset({FLAG_COMMERCE_ORDERS})

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        accion = args.get("accion")

        if accion == "fijar":
            categoria = str(args.get("categoria", "")).strip()
            if not categoria:
                return ToolResult(content="Necesito la categoría del presupuesto a fijar.")
            monto = _parse_monto(args.get("monto"))
            if monto is None:
                return ToolResult(content=f"'{args.get('monto')}' no es un monto válido.")
            try:
                row = await fijar_presupuesto(
                    ctx.session,
                    tenant_id=ctx.tenant_id,
                    user_id=ctx.user_id,
                    categoria=categoria,
                    monto_mensual=monto,
                )
            except ValueError as exc:
                return ToolResult(content=str(exc))
            return ToolResult(
                content=(
                    f"Presupuesto de «{categoria}» fijado en {row['moneda']} "
                    f"{float(row['monto_mensual']):,.2f}/mes."
                ),
                data={
                    "categoria": categoria,
                    "monto_mensual": float(row["monto_mensual"]),
                    "moneda": row["moneda"],
                },
            )

        if accion == "listar":
            filas = await listar_presupuestos(
                ctx.session, tenant_id=ctx.tenant_id, user_id=ctx.user_id
            )
            if not filas:
                return ToolResult(
                    content="No tienes presupuestos configurados todavía.",
                    data={"presupuestos": []},
                )
            lineas = [
                f"- {f['categoria']}: {f['moneda']} {float(f['monto_mensual']):,.2f}/mes"
                for f in filas
            ]
            return ToolResult(
                content="Presupuestos configurados:\n" + "\n".join(lineas),
                data={"presupuestos": filas},
            )

        if accion == "estado":
            estado = await estado_presupuestos(
                ctx.session, tenant_id=ctx.tenant_id, user_id=ctx.user_id
            )
            if not estado:
                return ToolResult(
                    content="No tienes presupuestos configurados todavía.", data={"estado": []}
                )
            lineas = []
            for e in estado:
                sufijo = " (cerca del límite)" if e["alerta"] else ""
                lineas.append(
                    f"- {e['categoria']}: {e['moneda']} {e['gastado']:,.2f} de "
                    f"{e['monto_mensual']:,.2f} ({e['pct']:.0f}%){sufijo}"
                )
            return ToolResult(
                content=f"Estado de presupuestos de {estado[0]['mes']}:\n" + "\n".join(lineas),
                data={"estado": estado},
            )

        return ToolResult(content="'accion' debe ser 'fijar', 'listar' o 'estado'.")


class PrepararPagoTool(Tool):
    name = "preparar_pago"
    description = (
        "Crea un BORRADOR de orden de pago. NO mueve dinero real ni ejecuta nada: solo deja "
        "el borrador listo para que el usuario lo confirme o cancele en la página Órdenes."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "descripcion": {"type": "string", "description": "Qué se va a pagar."},
            "monto": {"type": "number", "description": "Monto del pago (mayor que cero)."},
            "moneda": {
                "type": "string",
                "description": "Código ISO-4217 de 3 letras.",
                "default": _MONEDA_DEFECTO,
            },
            "beneficiario": {
                "type": "string",
                "description": "A quién se le paga (nombre o cuenta), opcional.",
            },
        },
        "required": ["descripcion", "monto"],
    }
    requires_flags = frozenset({FLAG_COMMERCE_ORDERS})
    dangerous = True

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        descripcion = str(args.get("descripcion", "")).strip()
        if not descripcion:
            return ToolResult(content="Necesito una descripción de qué se va a pagar.")
        monto = _parse_monto(args.get("monto"))
        if monto is None:
            return ToolResult(content=f"'{args.get('monto')}' no es un monto válido.")
        if monto <= 0:
            return ToolResult(content="El monto del pago debe ser mayor que cero.")
        moneda = str(args.get("moneda") or _MONEDA_DEFECTO).strip().upper()
        if len(moneda) != 3 or not moneda.isalpha():
            moneda = _MONEDA_DEFECTO
        beneficiario = str(args.get("beneficiario") or "").strip() or None

        meta = {"beneficiario": beneficiario} if beneficiario else {}
        order_id = await _crear_orden_draft(
            ctx.session,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            kind="payment",
            descripcion=descripcion,
            monto=monto,
            moneda=moneda,
            meta=meta,
        )
        return ToolResult(
            content=(
                f"Borrador de pago creado: {moneda} {monto:,.2f} — «{descripcion}». "
                "NO se ha movido dinero. Confírmalo o cancélalo en la página Órdenes."
            ),
            data={"order_id": str(order_id)},
        )


class PrepararOrdenTool(Tool):
    name = "preparar_orden"
    description = (
        "Crea un BORRADOR de orden de compra/venta de un activo, con una cotización "
        "adjunta. NO ejecuta nada: el usuario debe confirmarla en la página Órdenes, donde "
        "se ejecuta en modo simulado (paper) — nunca con dinero real."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "simbolo": {"type": "string", "description": "Símbolo del activo, p. ej. 'BTC'."},
            "lado": {
                "type": "string",
                "enum": ["buy", "sell"],
                "description": "Comprar ('buy') o vender ('sell').",
            },
            "cantidad": {
                "type": "number",
                "description": "Cantidad del activo a comprar/vender (mayor que cero).",
            },
        },
        "required": ["simbolo", "lado", "cantidad"],
    }
    requires_flags = frozenset({FLAG_COMMERCE_ORDERS})
    dangerous = True

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        simbolo = str(args.get("simbolo", "")).strip().upper()
        if not simbolo:
            return ToolResult(content="Necesito el símbolo del activo (p. ej. 'BTC').")
        lado = args.get("lado")
        if lado not in ("buy", "sell"):
            return ToolResult(content="'lado' debe ser 'buy' o 'sell'.")
        cantidad = _parse_monto(args.get("cantidad"), "cantidad")
        if cantidad is None:
            return ToolResult(content=f"'{args.get('cantidad')}' no es una cantidad válida.")
        if cantidad <= 0:
            return ToolResult(content="La cantidad debe ser mayor que cero.")

        proveedor = get_quote_provider(ctx.settings)
        try:
            cotizacion = await proveedor.quote(simbolo)
        except Exception as exc:
            return ToolResult(content=f"No pude cotizar '{simbolo}': {exc}")

        meta = {
            "cotizacion": {
                "precio": cotizacion.precio,
                "moneda": cotizacion.moneda,
                "fuente": cotizacion.fuente,
                "ts": cotizacion.ts.isoformat(),
            }
        }
        monto = (cantidad * Decimal(str(cotizacion.precio))).quantize(Decimal("0.01"))
        accion = "Compra" if lado == "buy" else "Venta"
        order_id = await _crear_orden_draft(
            ctx.session,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            kind="trade",
            descripcion=f"{accion} de {cantidad} {simbolo}",
            monto=monto,
            moneda=cotizacion.moneda,
            simbolo=simbolo,
            lado=lado,
            cantidad=cantidad,
            meta=meta,
        )
        return ToolResult(
            content=(
                f"Borrador de orden creado: {lado} {cantidad} {simbolo} a {cotizacion.moneda} "
                f"{cotizacion.precio:,.2f} (fuente: {cotizacion.fuente}). NO se ha ejecutado "
                "nada. Confírmala o cancélala en la página Órdenes."
            ),
            data={"order_id": str(order_id), "cotizacion": meta["cotizacion"]},
        )
