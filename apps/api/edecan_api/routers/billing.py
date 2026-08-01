"""`POST /v1/billing/webhook` y `POST /v1/billing/portal` (ARCHITECTURE.md §10.12).

El webhook verifica la firma de Stripe (HMAC-SHA256 del header
`Stripe-Signature`, esquema `t=<timestamp>,v1=<hex hmac>`) y maneja
`checkout.session.completed` y `customer.subscription.updated|deleted`,
actualizando `subscriptions` y `tenants.plan_key`.

`client_reference_id` (o `metadata.tenant_id`) del evento de Stripe debe ser
el `tenant_id` de la plataforma — se asume que la sesión de Checkout se creó
pasando ese valor (la creación de la sesión de Checkout en sí es responsabilidad
del frontend/otro paquete de trabajo; aquí solo se procesa el webhook).

## Modelo de precio de pago único (2026-07-09)

Edecán se vende ahora por pago único, dos tiers, sin suscripción mensual
(`edecan_schemas.plans` ya no gatea capacidades por plan — ver su
docstring): "código completo" ($99) y "código + actualizaciones de por
vida" ($199, `tenants.lifetime_updates_purchased_at`, migración 0010).
`checkout.session.completed` se bifurca por `obj["mode"]` (campo real del
objeto Checkout Session de Stripe — `"payment"` para pago único, `"subscription"`
para lo viejo):

- `mode == "payment"` (camino nuevo): NO hay objeto Subscription de Stripe
  (`obj.get("subscription")` viene `None`) — no se toca la tabla
  `subscriptions` en absoluto, que es inherentemente de suscripciones
  recurrentes (`current_period_end` NOT NULL sin default no tiene sentido
  para un pago único). Se fija `tenants.plan_key` (a cualquier `plan_key`
  válido — con las 4 entradas de `PLANES` ahora idénticas y sin gating, cuál
  elijamos no cambia el acceso del tenant, ver docstring de
  `edecan_schemas.plans`) y, si `metadata.tier == "lifetime"` (fijado por
  quien crea la sesión de Checkout — la página de compra, fuera de este
  paquete de trabajo, junto al Price ID de Stripe que corresponda al tier
  de $199), también `tenants.lifetime_updates_purchased_at`.
- `mode == "subscription"` (o ausente, camino viejo): sigue el flujo
  original tal cual, sin cambios — sigue siendo válido para cualquier
  tenant que ya estuviera en un modelo de suscripción hospedada.

`customer.subscription.updated`/`.deleted` no aplican a compras de pago
único (Stripe nunca las dispara para un Checkout Session `mode="payment"`)
— se dejan intactas para el camino de suscripción legado.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_platform_repo
from edecan_api.repo import Repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/billing", tags=["billing"])

STRIPE_SIGNATURE_TOLERANCE_SECONDS = 300


def _verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Verifica `Stripe-Signature: t=<ts>,v1=<hex hmac_sha256(secret, f'{ts}.{payload}')>`."""
    if not sig_header:
        return False
    parts: dict[str, str] = {}
    for item in sig_header.split(","):
        key, _, value = item.partition("=")
        if key and value:
            parts[key.strip()] = value.strip()

    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        return False

    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False

    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False
    return age <= STRIPE_SIGNATURE_TOLERANCE_SECONDS


def _epoch_to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


async def _handle_checkout_completed_pago_unico(
    repo: Repo, tenant_id: uuid.UUID, metadata: dict[str, Any]
) -> None:
    """`mode == "payment"` — ver docstring del módulo, "Modelo de precio de
    pago único". Nunca toca `subscriptions` (no hay objeto Subscription de
    Stripe para un Checkout Session de pago único)."""
    tier = str(metadata.get("tier") or "base").strip().lower()
    es_lifetime = tier == "lifetime"
    # Cualquier `plan_key` válido sirve — las 4 entradas de `PLANES` ahora
    # son idénticas (ver su docstring), así que esto solo deja una fila de
    # tenant con un `plan_key` consistente, no restringe nada.
    await repo.update_tenant_plan(tenant_id, "hosted_business")
    if es_lifetime:
        await repo.update_tenant_lifetime_updates(tenant_id)
    await repo.add_audit_log(
        tenant_id=tenant_id,
        actor_user_id=None,
        action="billing.purchase_completed",
        target=tier,
        meta={"lifetime_updates": es_lifetime},
    )


async def _handle_checkout_completed(repo: Repo, obj: dict[str, Any]) -> None:
    metadata = obj.get("metadata") or {}
    tenant_id_raw = obj.get("client_reference_id") or metadata.get("tenant_id")
    if not tenant_id_raw:
        logger.warning(
            "checkout.session.completed sin tenant_id (client_reference_id/metadata); se ignora."
        )
        return
    try:
        tenant_id = uuid.UUID(str(tenant_id_raw))
    except ValueError:
        logger.warning("checkout.session.completed con tenant_id inválido: %r", tenant_id_raw)
        return

    if obj.get("mode") == "payment":
        await _handle_checkout_completed_pago_unico(repo, tenant_id, metadata)
        return

    customer_id = obj.get("customer")
    subscription_id = obj.get("subscription")
    plan_key = metadata.get("plan_key")
    if not customer_id or not subscription_id or not plan_key:
        # `subscriptions.stripe_customer_id`/`stripe_subscription_id`/`plan_key` son
        # NOT NULL sin default (migración 0001), igual que `current_period_end` más
        # abajo, pero a diferencia de ese campo no hay un valor provisional razonable:
        # para los identificadores de Stripe, sembrar un placeholder arriesgaría chocar
        # con el UNIQUE de `stripe_subscription_id` de otro tenant; para `plan_key`, uno
        # inventado no existiría en `PLANES` (`edecan_schemas.plans`) y reventaría el
        # cálculo de flags del tenant más adelante. Así que el evento se ignora en vez
        # de dejar que el INSERT reviente.
        logger.warning(
            "checkout.session.completed sin customer/subscription/plan_key (tenant_id=%s); "
            "se ignora.",
            tenant_id,
        )
        return

    fields: dict[str, Any] = {
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription_id,
        "status": "active",
        "plan_key": plan_key,
        # `subscriptions.current_period_end` es NOT NULL sin default (migración
        # 0001) y el objeto Checkout Session de Stripe no lo trae (vive en el
        # objeto Subscription, no en el de Session): sin esto, el INSERT de la
        # primera suscripción del tenant en `upsert_subscription` revienta
        # contra Postgres real. Se siembra un valor provisional; el webhook
        # `customer.subscription.updated` que Stripe dispara al crearse la
        # suscripción lo corrige con el valor real vía `_handle_subscription_updated`.
        "current_period_end": _epoch_to_datetime(obj.get("current_period_end"))
        or datetime.now(UTC),
    }

    await repo.upsert_subscription(tenant_id=tenant_id, fields=fields)
    await repo.update_tenant_plan(tenant_id, plan_key)
    await repo.add_audit_log(
        tenant_id=tenant_id,
        actor_user_id=None,
        action="billing.checkout_completed",
        target=str(obj.get("customer")),
        meta={"subscription_id": obj.get("subscription"), "plan_key": plan_key},
    )


async def _handle_subscription_updated(repo: Repo, obj: dict[str, Any], *, deleted: bool) -> None:
    customer_id = obj.get("customer")
    subscription_id = obj.get("id")

    existing = None
    if subscription_id:
        existing = await repo.get_subscription_by_stripe_subscription(subscription_id)
    if existing is None and customer_id:
        existing = await repo.get_subscription_by_stripe_customer(customer_id)
    if existing is None:
        logger.warning(
            "Evento de suscripción de Stripe sin coincidencia local: customer=%r subscription=%r",
            customer_id,
            subscription_id,
        )
        return

    tenant_id = existing["tenant_id"]
    status_value = (
        "cancelled" if deleted else str(obj.get("status") or existing.get("status") or "active")
    )
    fields: dict[str, Any] = {"status": status_value}
    period_end = _epoch_to_datetime(obj.get("current_period_end"))
    if period_end is not None:
        fields["current_period_end"] = period_end

    await repo.upsert_subscription(tenant_id=tenant_id, fields=fields)
    if deleted:
        await repo.update_tenant_plan(tenant_id, "free_selfhost")

    await repo.add_audit_log(
        tenant_id=tenant_id,
        actor_user_id=None,
        action="billing.subscription_deleted" if deleted else "billing.subscription_updated",
        target=str(subscription_id),
        meta={"status": status_value},
    )


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    repo: Repo = Depends(get_platform_repo),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")

    if not settings.STRIPE_WEBHOOK_SECRET or not _verify_stripe_signature(
        payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Firma de Stripe inválida."
        )

    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Payload no es JSON válido."
        ) from exc

    event_type = event.get("type")
    data_object = (event.get("data") or {}).get("object") or {}

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(repo, data_object)
    elif event_type == "customer.subscription.updated":
        await _handle_subscription_updated(repo, data_object, deleted=False)
    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_updated(repo, data_object, deleted=True)
    # Cualquier otro tipo de evento se reconoce (200) pero se ignora: Stripe
    # reintenta si no respondemos 2xx, y no todos los eventos nos interesan.

    return {"status": "ok"}


@router.post("/portal")
async def billing_portal(
    current_user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """URL de portal de facturación **placeholder**.

    Crear una sesión real del Billing Portal de Stripe requiere una llamada de
    servidor a la API de Stripe (`POST https://api.stripe.com/v1/billing_portal/sessions`
    con `STRIPE_SECRET_KEY`). Este paquete de trabajo no ejecuta llamadas de
    red reales a servicios de pago (ver las reglas duras del proyecto): en
    producción, reemplaza este cuerpo por esa llamada real y devuelve la
    `url` que retorna Stripe.
    """
    base = settings.WEB_BASE_URL.rstrip("/")
    return {"url": f"{base}/app/facturacion?portal=pendiente-configurar-stripe"}
