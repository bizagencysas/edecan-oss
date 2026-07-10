"""`PaperBroker` — ejecutor de órdenes de trading en modo **simulado** ("paper").

GUARDRAIL PERMANENTE (`ROADMAP_V2.md` §8.1, `ARCHITECTURE.md` §0): "Dinero real nunca se
mueve solo". `PaperBroker` es, y solo puede ser, un broker de contabilidad *simulada*:

- No abre ninguna conexión de red. No llama a ningún exchange, banco o broker real.
- No mueve un solo centavo real. `holdings`/`transactions` que este módulo escribe son
  puramente contables — existen para que el usuario pueda practicar/simular sin riesgo, no
  para representar saldo real en ninguna cuenta.
- `execute()` SOLO acepta una orden que ya está `status="confirmed"` — la transición
  `draft -> confirmed` la hace exclusivamente el humano, desde la UI
  (`POST /v1/commerce/orders/{id}/confirm`, ver `apps/api/edecan_api/routers/commerce.py`).
  `PaperBroker` nunca decide por sí mismo que una orden está lista.

**Si algún día existe un broker "live" de verdad** (una integración real con un exchange u
otro tipo de brokerage, vía las credenciales OAuth/API key propias del tenant — nunca
compartidas), debe implementar el mismo contrato de entrada (`status="confirmed"` como único
disparador) y, aun así, seguir exigiendo la MISMA confirmación explícita en la UI antes de
llegar a `confirm`. Ese gate no es una limitación temporal de este WP — es una regla de
producto permanente (`ROADMAP_V2.md` §8.1, `REQUISITOS_V2.md`: "Dinero real nunca se mueve
solo"). Ver `docs/dinero-real.md` para el detalle completo de qué haría falta para ese
broker live y por qué la confirmación humana seguiría siendo irrenunciable incluso entonces.

Habla SQL parametrizado directo (mismo criterio que `edecan_toolkit`/`edecan_premium`, ver
sus README: el contrato pinnea nombres de tabla/columna, no una forma de ORM) contra:

- `orders`/`holdings` — tablas NUEVAS de `ROADMAP_V2.md` §7.4 (migración
  `0003_v2_expansion`, dueña WP-V2-01, ya aterrizada en este árbol, con su modelo
  SQLAlchemy en `edecan_db.models` — ver `docs/dinero-real.md` y el README de este paquete
  para el detalle).
- `transactions`/`audit_log` — tablas v1, YA EXISTEN desde `0001_initial`
  (`ARCHITECTURE.md` §10.3) y funcionan de verdad hoy.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_CATEGORIA_PAPER = "inversion_paper"
_CUENTA_PAPER = "paper"
_KIND_HOLDING = "paper"
_CENTAVOS = Decimal("0.01")
_COSTO_PROMEDIO_ESCALA = Decimal("0.0001")


def _to_decimal(value: Any, campo: str) -> Decimal:
    if value is None:
        raise ValueError(f"Falta '{campo}' en la orden.")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"'{campo}' inválido en la orden: {value!r}.") from exc


def _meta_dict(value: Any) -> dict[str, Any]:
    """`meta` (jsonb) puede llegar como `dict` ya decodificado o como texto JSON crudo según
    el driver — mismo criterio defensivo que `edecan_toolkit.contactos._desde_jsonb`."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            cargado = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return cargado if isinstance(cargado, dict) else {}
    return {}


class PaperBroker:
    """Ejecuta órdenes `kind="trade"` ya `status="confirmed"` en modo simulado.

    `execute()` hace, en una sola transacción (la del `session` recibido — el llamador es
    quien decide cuándo hace commit, típicamente al terminar el request HTTP):

    1. Valida que la orden sea ejecutable (`status="confirmed"`, `kind="trade"`, símbolo/
       lado/cantidad presentes, y una cotización adjunta en `meta["cotizacion"]["precio"]`
       — la deja `preparar_orden`, ver `tools.py`).
    2. Lee el `holding` actual del símbolo (si existe).
    3. Compra: *upsert* de `holdings` con costo promedio ponderado
       `(cant_actual*costo_actual + cant_nueva*precio) / cant_total`.
       Venta: valida que haya cantidad suficiente — si no, deja el motivo en `meta.error`
       (SIN cambiar `status`, ver `_mark_error`) y lanza `ValueError` legible SIN tocar
       `holdings`/`transactions`/`audit_log`.
    4. Marca la orden `status="executed_paper"` + `executed_at`.
    5. Registra una fila real en `transactions` (`categoria="inversion_paper"`,
       `cuenta="paper"`; monto negativo en compra, positivo en venta) y en `audit_log`.
    """

    async def execute(self, order: Mapping[str, Any], session: AsyncSession) -> dict[str, Any]:
        if order.get("status") != "confirmed" or order.get("kind") != "trade":
            raise ValueError(
                "PaperBroker.execute solo acepta órdenes kind='trade' en estado "
                f"'confirmed' (recibí kind={order.get('kind')!r}, status={order.get('status')!r})."
            )

        order_id = order["id"]
        tenant_id = order["tenant_id"]
        user_id = order["user_id"]
        simbolo = str(order.get("simbolo") or "").strip().upper()
        lado = order.get("lado")
        moneda = str(order.get("moneda") or "USD").strip().upper() or "USD"

        if not simbolo:
            raise ValueError("La orden no tiene 'simbolo'; no se puede ejecutar.")
        if lado not in ("buy", "sell"):
            raise ValueError(f"La orden tiene 'lado' inválido: {lado!r} (debe ser 'buy'/'sell').")
        cantidad = _to_decimal(order.get("cantidad"), "cantidad")
        if cantidad <= 0:
            raise ValueError("La cantidad de la orden debe ser mayor que cero.")

        meta = _meta_dict(order.get("meta"))
        cotizacion = meta.get("cotizacion")
        precio_bruto = cotizacion.get("precio") if isinstance(cotizacion, dict) else None
        if precio_bruto is None:
            raise ValueError(
                "La orden no tiene una cotización adjunta (meta.cotizacion.precio); no se "
                "puede ejecutar en modo paper. Crea una orden nueva con 'preparar_orden' "
                "para que quede una cotización fresca."
            )
        precio = _to_decimal(precio_bruto, "meta.cotizacion.precio")
        if precio <= 0:
            raise ValueError("La cotización adjunta a la orden no es un precio válido (> 0).")

        monto_total = (cantidad * precio).quantize(_CENTAVOS)

        holding = await self._get_holding(session, tenant_id, user_id, simbolo)
        cantidad_actual = Decimal(str(holding["cantidad"])) if holding else Decimal("0")
        costo_promedio_actual = Decimal(str(holding["costo_promedio"])) if holding else Decimal("0")

        if lado == "sell":
            if cantidad_actual < cantidad:
                mensaje = (
                    f"No hay suficiente {simbolo} en holdings paper para vender: disponible "
                    f"{cantidad_actual}, solicitado {cantidad}."
                )
                await self._mark_error(session, order_id, mensaje)
                await session.flush()
                raise ValueError(mensaje)
            nueva_cantidad = cantidad_actual - cantidad
            nuevo_costo_promedio = costo_promedio_actual if nueva_cantidad > 0 else Decimal("0")
            monto_transaccion = monto_total  # venta: "entra" dinero paper (positivo)
        else:
            nueva_cantidad = cantidad_actual + cantidad
            nuevo_costo_promedio = (
                ((cantidad_actual * costo_promedio_actual) + (cantidad * precio)) / nueva_cantidad
            ).quantize(_COSTO_PROMEDIO_ESCALA)
            monto_transaccion = -monto_total  # compra: "sale" dinero paper (negativo)

        await self._upsert_holding(
            session,
            holding,
            tenant_id=tenant_id,
            user_id=user_id,
            simbolo=simbolo,
            cantidad=nueva_cantidad,
            costo_promedio=nuevo_costo_promedio,
            moneda=moneda,
        )

        meta["ejecucion"] = {
            "precio": float(precio),
            "monto_total": float(monto_total),
            "holding_cantidad": float(nueva_cantidad),
            "holding_costo_promedio": float(nuevo_costo_promedio),
        }
        updated_order = await self._mark_executed(session, order_id, meta)

        accion = "Compra" if lado == "buy" else "Venta"
        descripcion = f"Orden paper: {accion.lower()} de {cantidad} {simbolo} a {moneda} {precio}"
        await self._insert_transaction(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            monto=monto_transaccion,
            moneda=moneda,
            descripcion=descripcion,
        )
        await self._insert_audit(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            order_id=order_id,
            meta={
                "lado": lado,
                "simbolo": simbolo,
                "cantidad": float(cantidad),
                "precio": float(precio),
            },
        )
        await session.flush()
        logger.info(
            "PaperBroker ejecutó orden %s (%s %s %s a %s %s) para tenant %s.",
            order_id,
            lado,
            cantidad,
            simbolo,
            moneda,
            precio,
            tenant_id,
        )
        return updated_order

    # -- SQL parametrizado, todo sobre el `session` recibido --------------------------

    async def _get_holding(
        self, session: AsyncSession, tenant_id: Any, user_id: Any, simbolo: str
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                "SELECT * FROM holdings WHERE tenant_id = :tenant_id ::uuid "
                "AND user_id = :user_id ::uuid AND simbolo = :simbolo AND kind = :kind"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "simbolo": simbolo,
                "kind": _KIND_HOLDING,
            },
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def _upsert_holding(
        self,
        session: AsyncSession,
        holding: dict[str, Any] | None,
        *,
        tenant_id: Any,
        user_id: Any,
        simbolo: str,
        cantidad: Decimal,
        costo_promedio: Decimal,
        moneda: str,
    ) -> None:
        if holding is None:
            await session.execute(
                text(
                    "INSERT INTO holdings "
                    "(tenant_id, user_id, simbolo, cantidad, costo_promedio, moneda, kind) "
                    "VALUES (:tenant_id ::uuid, :user_id ::uuid, :simbolo, :cantidad, "
                    ":costo_promedio, :moneda, :kind)"
                ),
                {
                    "tenant_id": str(tenant_id),
                    "user_id": str(user_id),
                    "simbolo": simbolo,
                    "cantidad": cantidad,
                    "costo_promedio": costo_promedio,
                    "moneda": moneda,
                    "kind": _KIND_HOLDING,
                },
            )
        else:
            await session.execute(
                text(
                    "UPDATE holdings SET cantidad = :cantidad, costo_promedio = :costo_promedio, "
                    "moneda = :moneda, updated_at = now() WHERE id = :id ::uuid"
                ),
                {
                    "id": str(holding["id"]),
                    "cantidad": cantidad,
                    "costo_promedio": costo_promedio,
                    "moneda": moneda,
                },
            )

    async def _mark_error(self, session: AsyncSession, order_id: Any, mensaje: str) -> None:
        """Deja constancia del error en `meta.error` SIN cambiar `status`.

        A propósito no usa un status `"error"`: el enum pinned de `orders.status`
        (`ROADMAP_V2.md` §7.4) es exactamente `draft|confirmed|executed_paper|cancelled|
        expired` — inventar un sexto valor rompería contra una futura `CHECK CONSTRAINT` de
        la migración `0003_v2_expansion` (mismo patrón que usa la migración `0001_initial`
        para otras columnas enum, p. ej. `CheckConstraint("kind IN ('sms','voice')")` en
        `consents`). La orden se queda en `status="confirmed"` con el motivo en
        `meta.error` — visible en la UI, y todavía cancelable
        (`POST /v1/commerce/orders/{id}/cancel` acepta `draft` y `confirmed`)."""
        await session.execute(
            text(
                "UPDATE orders SET meta = meta || CAST(:extra AS jsonb), updated_at = now() "
                "WHERE id = :id ::uuid"
            ),
            {"id": str(order_id), "extra": json.dumps({"error": mensaje})},
        )

    async def _mark_executed(
        self, session: AsyncSession, order_id: Any, meta: dict[str, Any]
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "UPDATE orders SET status = 'executed_paper', executed_at = now(), "
                "meta = CAST(:meta AS jsonb), updated_at = now() WHERE id = :id ::uuid RETURNING *"
            ),
            {"id": str(order_id), "meta": json.dumps(meta)},
        )
        row = result.mappings().first()
        if row is None:  # defensivo: el llamador ya validó que la orden existe.
            raise ValueError(f"No se encontró la orden {order_id} al marcarla ejecutada.")
        return dict(row)

    async def _insert_transaction(
        self,
        session: AsyncSession,
        *,
        tenant_id: Any,
        user_id: Any,
        monto: Decimal,
        moneda: str,
        descripcion: str,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO transactions "
                "(tenant_id, user_id, fecha, monto, moneda, categoria, descripcion, cuenta) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, CURRENT_DATE, :monto, :moneda, "
                ":categoria, :descripcion, :cuenta)"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "monto": monto,
                "moneda": moneda,
                "categoria": _CATEGORIA_PAPER,
                "descripcion": descripcion,
                "cuenta": _CUENTA_PAPER,
            },
        )

    async def _insert_audit(
        self,
        session: AsyncSession,
        *,
        tenant_id: Any,
        user_id: Any,
        order_id: Any,
        meta: dict[str, Any],
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO audit_log (tenant_id, actor_user_id, action, target, meta) "
                "VALUES (:tenant_id ::uuid, :actor_user_id ::uuid, "
                "'commerce.order.executed_paper', :target, CAST(:meta AS jsonb))"
            ),
            {
                "tenant_id": str(tenant_id),
                "actor_user_id": str(user_id),
                "target": str(order_id),
                "meta": json.dumps(meta),
            },
        )
