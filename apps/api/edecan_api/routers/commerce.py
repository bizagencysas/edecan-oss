"""`/v1/commerce` — presupuestos, cotizaciones y órdenes draft con gate permanente
(`ROADMAP_V2.md` §7.4/§7.5/§7.6/§7.7/§8.1 — WP-V2-10). Ver `docs/dinero-real.md` para la
política de producto completa: **dinero real nunca se mueve solo**.

## Qué SÍ hace este router hoy (real)

- Gate de flag de plan `commerce.orders` — importado como constante de
  `edecan_schemas.plans.FLAG_COMMERCE_ORDERS` (WP-V2-01 ya la agregó a `PLANES`, con
  `True` en `free_selfhost`/`hosted_pro`/`hosted_business` y `False` en `hosted_basic`;
  `ROADMAP_V2.md` §7.2). A diferencia de `edecan_api.routers.remote` (WP-V2-09, que
  todavía tuvo que usar un *string* local porque su flag no existía en `PLANES` cuando se
  escribió), aquí el flag YA está pinned — se usa directo.
- `GET /orders` (+ filtro `status`), `GET /orders/{id}`, `POST /orders/{id}/confirm`,
  `POST /orders/{id}/cancel`, `GET /holdings`, `GET /budgets`, `PUT /budgets` — las 7 rutas
  del paquete de trabajo, con SQL parametrizado real contra el esquema pinneado en
  `ROADMAP_V2.md` §7.4 (`orders`, `holdings`, `budgets`) y `ARCHITECTURE.md` §10.3
  (`audit_log`, ya existe desde v1).
- `POST /orders/{id}/confirm` transiciona `draft -> confirmed`; si `kind="trade"` y
  `COMMERCE_MODE` (default `"paper"`) es `"paper"`, delega la ejecución a
  `edecan_commerce.paper.PaperBroker` (broker SIMULADO — nunca dinero real, ver su
  docstring); si `kind="payment"`, genera un `meta.payment_link` PLACEHOLDER (ningún PSP
  real conectado) y dice explícitamente que el humano debe abrir y aprobar el pago; para
  cualquier otro caso (otro `COMMERCE_MODE`, u otro `kind` como `"purchase"`, que ninguna
  tool de hoy crea todavía) responde `501` documentado. Nunca hay una tercera rama que
  ejecute algo sin que el humano haya confirmado explícitamente en esta misma llamada.
- Expiración perezosa de drafts (`> 7 días -> expired`, `ROADMAP_V2.md` §7): se aplica no
  solo al listar (como pide el enunciado del WP) sino, defensivamente, también antes de
  leer/confirmar/cancelar una orden puntual — así un cliente no puede confirmar un draft
  que técnicamente ya debería estar expirado solo porque nadie llamó a `GET /orders`
  recientemente.
- Auditoría real (`audit_log`) en cada confirmación y cancelación, además de la que
  `PaperBroker` ya deja para la ejecución paper en sí.
- `POST /orders/{id}/confirm` comitea explícitamente la confirmación (y, si aplica, el
  detalle del fallo de ejecución) ANTES de cualquier `raise` que pudiera seguir, para que el
  rollback automático de `edecan_db.session.get_session` nunca se lleve puesta la evidencia
  de que el usuario confirmó — ver el docstring de `confirm_order`
  (`HOTFIXES_PENDIENTES.md` punto 9).

## Qué falta para que esto funcione contra Postgres de verdad

Nada: la migración `0003_v2_expansion` (`ROADMAP_V2.md` §7.4, dueña WP-V2-01) que crea las
tablas `orders`/`holdings`/`budgets` ya aterrizó en este árbol, con su modelo SQLAlchemy en
`edecan_db.models` (`Order`/`Holding`/`Budget`). Este router (y
`edecan_commerce.paper`/`.budgets`) asumen el esquema pinneado en §7.4 AL PIE DE LA LETRA
(nombres de tabla/columna exactos, mismo criterio que `edecan_api.repo.SqlRepo` y
`edecan_toolkit`/`edecan_premium`, que tampoco importan `edecan_db.models` — ver sus
README), y es exactamente el esquema que la migración ya aplicó: este código funciona
contra Postgres real sin ningún cambio. `transactions`/`audit_log` (que sí toca
`PaperBroker`) ya existen desde `0001_initial` y funcionan hoy.

A propósito NO se usa aquí el truco que `edecan_api.routers.remote` (WP-V2-09) usó en un
principio como sustituto temporal — un almacén en memoria por proceso con la forma exacta
de su tabla nueva — antes de que aterrizara su propia persistencia en Postgres; hoy ese
router también persiste de verdad en `remote_sessions` (ver su docstring). Ahí funcionaba
porque solo el router tocaba `remote_sessions`. Aquí las MISMAS filas
de `orders`/`budgets` las crea también `edecan_commerce.tools` (`preparar_pago`/
`preparar_orden`/`gestionar_presupuesto`, invocadas dentro de una conversación, con la
sesión de esa request) y las lee/confirma este router (otra request, otra sesión) — un
almacén en memoria por proceso las volvería invisibles entre sí (una orden creada por el
chat no aparecería en `/app/ordenes`, o viceversa), que es peor que "todavía no persiste
hasta que la migración aterrice". Ver `docs/dinero-real.md` y el README de
`packages/commerce`.

## Por qué SÍ importa `edecan_commerce.budgets` (no solo `paper`/`quotes`)

`GET /budgets` y `PUT /budgets` reutilizan `edecan_commerce.budgets.estado_presupuestos`/
`.fijar_presupuesto` en vez de reimplementar el cálculo de `%`/alerta aquí: son funciones
puras (`session` explícito, no `ToolContext`) pensadas precisamente para que tanto
`edecan_commerce.tools.GestionarPresupuestoTool` como este router las compartan sin
duplicar esa cuenta en dos lugares. `edecan_commerce.quotes` en cambio NO hace falta
importarlo aquí: cotizar ocurre al preparar la orden (`preparar_orden`, con `ctx.settings`
en ese momento), no al confirmarla — `PaperBroker.execute` lee la cotización ya adjunta en
`order.meta.cotizacion`, no vuelve a pedir una nueva.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from edecan_commerce.alpaca import (
    AlpacaAPIError,
    AlpacaPaperBroker,
    resolve_alpaca_paper_client,
)
from edecan_commerce.budgets import estado_presupuestos, fijar_presupuesto
from edecan_commerce.paper import PaperBroker
from edecan_schemas.plans import FLAG_COMMERCE_ORDERS
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_tenant_session, get_vault, rate_limit

router = APIRouter(prefix="/v1/commerce", tags=["commerce"], dependencies=[Depends(rate_limit)])

# Default documentado en `ROADMAP_V2.md` §7.5 (`COMMERCE_MODE`, "paper, único valor
# operativo hoy"). Se lee de `settings` con `getattr(..., DEFAULT_COMMERCE_MODE)` porque
# ese campo todavía no existe en `edecan_api.config.Settings` (lo agrega WP-V2-01 junto con
# el resto de `.env.example` de §7.5) — convención dura de §7.5: "toda tool lee settings
# con getattr(ctx.settings, 'CAMPO', default) — nunca revienta si falta el campo".
DEFAULT_COMMERCE_MODE = "paper"

_DRAFT_TTL_DIAS = 7


# ---------------------------------------------------------------------------
# Gate de flag de plan (§7.2) — sustituye a `get_current_user` en cada ruta.
# ---------------------------------------------------------------------------


async def _require_commerce_orders(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current_user.tenant.flags.get(FLAG_COMMERCE_ORDERS, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El comercio (presupuestos/órdenes) no está disponible en tu plan.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Helpers SQL — parametrizado directo, mismo criterio que `edecan_api.repo.SqlRepo`
# (§10.3: el contrato pinnea tabla/columna, no una forma de ORM).
# ---------------------------------------------------------------------------


async def _first(session: AsyncSession, stmt: str, params: dict[str, Any]) -> dict[str, Any] | None:
    result = await session.execute(text(stmt), params)
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _all(session: AsyncSession, stmt: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    result = await session.execute(text(stmt), params)
    return [dict(row) for row in result.mappings().all()]


def _from_jsonb(value: Any) -> dict[str, Any]:
    """`meta` puede llegar como `dict` ya decodificado o como texto JSON crudo según el
    driver — mismo criterio defensivo que `edecan_toolkit.contactos._desde_jsonb`. Necesario
    para no devolver `meta` como un string JSON *doblemente* codificado en la respuesta HTTP."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            cargado = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return cargado if isinstance(cargado, dict) else {}
    return {}


def _row_to_order(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["meta"] = _from_jsonb(out.get("meta"))
    return out


async def _audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    action: str,
    target: str,
    meta: dict[str, Any],
) -> None:
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


async def _expire_stale_drafts(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """`UPDATE` perezoso: cualquier `draft` con más de 7 días pasa a `expired`
    (`ROADMAP_V2.md` §7, "Drafts >7 días → expired (chequeo perezoso al listar)"). Se llama
    al listar Y, defensivamente, también antes de leer/confirmar/cancelar una orden puntual
    — ver el docstring del módulo."""
    await session.execute(
        text(
            "UPDATE orders SET status = 'expired', updated_at = now() "
            "WHERE tenant_id = :tenant_id ::uuid AND user_id = :user_id ::uuid "
            f"AND status = 'draft' AND created_at < now() - interval '{_DRAFT_TTL_DIAS} days'"
        ),
        {"tenant_id": str(tenant_id), "user_id": str(user_id)},
    )


async def _get_order_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, order_id: uuid.UUID
) -> dict[str, Any]:
    row = await _first(
        session,
        "SELECT * FROM orders WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid "
        "AND user_id = :user_id ::uuid",
        {"id": str(order_id), "tenant_id": str(tenant_id), "user_id": str(user_id)},
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Orden no encontrada.")
    return row


# ---------------------------------------------------------------------------
# Esquemas de entrada
# ---------------------------------------------------------------------------


class BudgetIn(BaseModel):
    categoria: str = Field(min_length=1)
    monto_mensual: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    moneda: str = "USD"

    @field_validator("moneda")
    @classmethod
    def _validar_moneda(cls, value: str) -> str:
        value = (value or "USD").strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("moneda debe ser un código ISO-4217 de 3 letras, p. ej. 'USD'.")
        return value


# ---------------------------------------------------------------------------
# Órdenes
# ---------------------------------------------------------------------------


@router.get("/orders")
async def list_orders(
    estado: str | None = Query(default=None, alias="status"),
    current_user: CurrentUser = Depends(_require_commerce_orders),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    await _expire_stale_drafts(session, current_user.tenant_id, current_user.user_id)

    stmt = "SELECT * FROM orders WHERE tenant_id = :tenant_id ::uuid AND user_id = :user_id ::uuid"
    params: dict[str, Any] = {
        "tenant_id": str(current_user.tenant_id),
        "user_id": str(current_user.user_id),
    }
    if estado:
        stmt += " AND status = :status"
        params["status"] = estado
    stmt += " ORDER BY created_at DESC"

    rows = await _all(session, stmt, params)
    return [_row_to_order(r) for r in rows]


@router.get("/orders/{order_id}")
async def get_order(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_commerce_orders),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    await _expire_stale_drafts(session, current_user.tenant_id, current_user.user_id)
    order = await _get_order_or_404(session, current_user.tenant_id, current_user.user_id, order_id)
    return _row_to_order(order)


async def _record_execution_failure(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    order_id: uuid.UUID,
    exc: ValueError,
) -> None:
    """`HOTFIXES_PENDIENTES.md` punto 9: dentro de `confirm_order`, deja constancia de que la
    orden quedó `confirmed` pero su ejecución paper falló (`ValueError` de
    `PaperBroker.execute`, p. ej. holdings insuficientes) — SIN pisar `meta` existente:
    `meta || CAST(:extra AS jsonb)` solo agrega la clave `execution_error` (mismo operador que
    usa `PaperBroker._mark_error` para su propio `meta.error`, `packages/commerce/
    edecan_commerce/paper.py`). No hace commit — el llamador decide cuándo (ver docstring de
    `confirm_order`: debe ser la ÚLTIMA operación de la sesión antes del `raise`)."""
    await session.execute(
        text(
            "UPDATE orders SET meta = meta || CAST(:extra AS jsonb), updated_at = now() "
            "WHERE id = :id ::uuid"
        ),
        {"id": str(order_id), "extra": json.dumps({"execution_error": str(exc)})},
    )
    await _audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action="commerce.order.execution_failed",
        target=str(order_id),
        meta={"error": str(exc)},
    )


@router.post("/orders/{order_id}/confirm")
async def confirm_order(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_commerce_orders),
    session: AsyncSession = Depends(get_tenant_session),
    vault: Any = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """`draft -> confirmed`, y luego, según `kind`, ejecuta en modo paper (`trade`) o genera
    un enlace de pago placeholder (`payment`) — ver el docstring del módulo.

    `HOTFIXES_PENDIENTES.md` punto 9 — auditabilidad ante dinero: `get_tenant_session` envuelve
    TODA la request en una única transacción (`edecan_db.session.get_session`) con ROLLBACK
    automático si una excepción se propaga fuera del handler. Sin tratamiento especial, un
    `ValueError` de `PaperBroker.execute` (p. ej. holdings insuficientes) al confirmar una orden
    `trade` revertiría TAMBIÉN la transición a `confirmed` y su audit log — el usuario confirmó
    la orden pero no quedaría ningún rastro.

    El fix NO es "comitear apenas la orden pasa a `confirmed`", pese a que así lo describía el
    reporte original: se probó empíricamente (SQLAlchemy 2.0, ver `packages/db/edecan_db/
    session.py::get_session`) que llamar `session.commit()` mientras seguimos DENTRO del `async
    with session.begin()` que envuelve la request, y luego seguir usando esa MISMA sesión
    (exactamente lo que necesita `PaperBroker.execute` un par de líneas más abajo), revienta
    con `InvalidRequestError: Can't operate on closed transaction inside context manager` en la
    primera operación posterior al commit — es decir, esa versión "ingenua" rompería TODA
    confirmación de una orden `trade` en modo paper, no solo el caso con error. La única
    operación segura después de un commit manual, en esta arquitectura, es NO volver a tocar la
    sesión (mismo criterio que `edecan_api.routers.remote::get_frame`, HOTFIXES_PENDIENTES.md
    punto 8 — ver su docstring, que documenta la misma restricción).

    Por eso el commit explícito se hace en cada punto en el que el handler YA NO vuelve a usar
    `session`, justo antes del `raise` correspondiente — nunca antes:

    - `COMMERCE_MODE` distinto de `'paper'`: se comitea `confirmed` + su audit (nada más los
      usa después) y RECIÉN entonces se lanza el 501 — antes de este fix ese 501 también se
      llevaba puesta la confirmación.
    - `kind` no manejado (ni `'trade'` ni `'payment'`): mismo tratamiento antes del 501 final.
    - `ValueError` de `PaperBroker.execute` en modo paper: la sesión SÍ se sigue usando (para la
      propia ejecución paper, que puede haber dejado sus propias escrituras vía `flush()`, ver
      `PaperBroker._mark_error`) hasta que `_record_execution_failure` dejó su UPDATE/audit —
      ahí, y solo ahí (última operación de sesión de esta rama), se comitea TODO junto
      (confirmación + audit de confirmación + lo que haya escrito `PaperBroker` + el
      `execution_error`/audit de fallo) en un único commit, y RECIÉN entonces se lanza el
      mismo 400 de siempre. La orden queda `'confirmed'` (nunca vuelve a `'draft'`;
      `POST .../cancel` acepta `'confirmed'`, línea ~378) con `meta.execution_error` — puede
      reintentarse (creando una orden nueva) o cancelarse.
    - Camino feliz (`trade` ejecutada / `payment`): NO hace falta un commit explícito — nada
      lanza después de esas escrituras, así que el commit implícito de
      `edecan_db.session.get_session` al terminar la request normalmente ya las persiste, igual
      que antes de este fix.
    """
    await _expire_stale_drafts(session, current_user.tenant_id, current_user.user_id)
    order = await _get_order_or_404(session, current_user.tenant_id, current_user.user_id, order_id)
    if order["status"] != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"La orden está en estado '{order['status']}'; solo se puede confirmar una "
                "orden en estado 'draft'."
            ),
        )

    confirmed = await _first(
        session,
        "UPDATE orders SET status = 'confirmed', confirmed_at = now(), updated_at = now() "
        "WHERE id = :id ::uuid RETURNING *",
        {"id": str(order_id)},
    )
    if confirmed is None:  # defensivo: ya validamos que la orden existe arriba.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Orden no encontrada.")

    await _audit(
        session,
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="commerce.order.confirmed",
        target=str(order_id),
        meta={"kind": confirmed["kind"]},
    )

    kind = confirmed["kind"]

    if kind == "trade":
        modo = (
            str(getattr(settings, "COMMERCE_MODE", DEFAULT_COMMERCE_MODE) or DEFAULT_COMMERCE_MODE)
            .strip()
            .lower()
        )
        alpaca_client = None
        if modo in {"paper", "alpaca_paper"}:
            alpaca_client = await resolve_alpaca_paper_client(
                session=session,
                vault=vault,
                tenant_id=current_user.tenant_id,
            )
        if alpaca_client is not None:
            try:
                ejecutada = await AlpacaPaperBroker().execute(confirmed, session, alpaca_client)
            except (AlpacaAPIError, ValueError) as exc:
                await _record_execution_failure(
                    session,
                    tenant_id=current_user.tenant_id,
                    actor_user_id=current_user.user_id,
                    order_id=order_id,
                    exc=exc,
                )
                await session.commit()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc
            return {
                "order": _row_to_order(ejecutada),
                "mensaje": (
                    "Orden enviada a Alpaca Paper después de tu confirmación. Es una "
                    "simulación y no usa dinero real."
                ),
            }
        if modo == "paper":
            try:
                ejecutada = await PaperBroker().execute(confirmed, session)
            except ValueError as exc:
                # Última operación de sesión de esta rama: registra la falla en la MISMA
                # transacción (todavía sin comitear) y comitea TODO junto antes de lanzar —
                # ver el docstring de esta función (HOTFIXES_PENDIENTES.md punto 9).
                await _record_execution_failure(
                    session,
                    tenant_id=current_user.tenant_id,
                    actor_user_id=current_user.user_id,
                    order_id=order_id,
                    exc=exc,
                )
                await session.commit()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc
            return {
                "order": _row_to_order(ejecutada),
                "mensaje": "Orden ejecutada en modo simulado (paper). No se movió dinero real.",
            }
        if modo == "alpaca_paper":
            exc = ValueError(
                "Alpaca Paper no está conectado. Pega ambas credenciales paper en el "
                "chat y vuelve a crear la orden."
            )
            await _record_execution_failure(
                session,
                tenant_id=current_user.tenant_id,
                actor_user_id=current_user.user_id,
                order_id=order_id,
                exc=exc,
            )
            await session.commit()
            raise HTTPException(status_code=400, detail=str(exc))
        # Nada más vuelve a usar `session` en esta rama: comitea la confirmación (+ su audit)
        # ANTES del 501 — ver el docstring de esta función.
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                f"COMMERCE_MODE={modo!r} no está soportado todavía: hoy solo existe el modo "
                "'paper' (broker simulado). Un broker 'live' requeriría una integración real "
                "con un exchange/broker propio y, aun así, la confirmación humana seguiría "
                "siendo obligatoria antes de ejecutar nada (ver docs/dinero-real.md)."
            ),
        )

    if kind == "payment":
        meta = _from_jsonb(confirmed.get("meta"))
        meta["payment_link"] = f"https://TU_PROVEEDOR_DE_PAGOS_AQUI/checkout/{order_id}"
        actualizada = await _first(
            session,
            "UPDATE orders SET meta = CAST(:meta AS jsonb), updated_at = now() "
            "WHERE id = :id ::uuid RETURNING *",
            {"id": str(order_id), "meta": json.dumps(meta)},
        )
        if actualizada is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Orden no encontrada."
            )
        return {
            "order": _row_to_order(actualizada),
            "mensaje": (
                "Enlace de pago generado. NO se ha movido dinero real: ningún proveedor de "
                "pagos está conectado todavía. TÚ debes abrir el enlace y aprobar el pago "
                "manualmente cuando exista un proveedor real conectado."
            ),
        }

    # kind == "purchase" (o cualquier otro valor futuro): ninguna tool de hoy crea este
    # kind todavía (§7.7: solo `preparar_pago` -> "payment" y `preparar_orden` -> "trade") —
    # queda documentado en vez de fallar en silencio o simular una ejecución que no existe.
    # Nada más vuelve a usar `session` en esta rama: comitea la confirmación (+ su audit)
    # ANTES del 501 — ver el docstring de `confirm_order` (HOTFIXES_PENDIENTES.md punto 9).
    await session.commit()
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"kind={kind!r} todavía no tiene un flujo de confirmación implementado.",
    )


@router.post("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_commerce_orders),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    await _expire_stale_drafts(session, current_user.tenant_id, current_user.user_id)
    order = await _get_order_or_404(session, current_user.tenant_id, current_user.user_id, order_id)
    if order["status"] not in ("draft", "confirmed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No se puede cancelar una orden en estado '{order['status']}'.",
        )

    cancelada = await _first(
        session,
        "UPDATE orders SET status = 'cancelled', updated_at = now() "
        "WHERE id = :id ::uuid RETURNING *",
        {"id": str(order_id)},
    )
    if cancelada is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Orden no encontrada.")

    await _audit(
        session,
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="commerce.order.cancelled",
        target=str(order_id),
        meta={"kind": cancelada["kind"]},
    )
    return {"order": _row_to_order(cancelada), "mensaje": "Orden cancelada."}


# ---------------------------------------------------------------------------
# Holdings (solo lectura — los escribe `PaperBroker`, nunca este router)
# ---------------------------------------------------------------------------


@router.get("/holdings")
async def list_holdings(
    current_user: CurrentUser = Depends(_require_commerce_orders),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    return await _all(
        session,
        "SELECT * FROM holdings WHERE tenant_id = :tenant_id ::uuid "
        "AND user_id = :user_id ::uuid AND kind = 'paper' ORDER BY simbolo",
        {"tenant_id": str(current_user.tenant_id), "user_id": str(current_user.user_id)},
    )


# ---------------------------------------------------------------------------
# Presupuestos — delega el cálculo a `edecan_commerce.budgets` (ver docstring del módulo).
# ---------------------------------------------------------------------------


@router.get("/budgets")
async def list_budgets(
    current_user: CurrentUser = Depends(_require_commerce_orders),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    return await estado_presupuestos(
        session, tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )


@router.put("/budgets")
async def put_budget(
    body: BudgetIn,
    current_user: CurrentUser = Depends(_require_commerce_orders),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    try:
        return await fijar_presupuesto(
            session,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            categoria=body.categoria,
            monto_mensual=body.monto_mensual,
            moneda=body.moneda,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
