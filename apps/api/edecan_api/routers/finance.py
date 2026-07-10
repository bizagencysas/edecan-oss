"""CRUD `/v1/finance/transactions` + `/v1/finance/summary` (ARCHITECTURE.md §10.12, §10.3),
más `/v1/finance/stripe/*` — Stripe bring-your-own de solo lectura (no Plaid: no todos los
países del mercado de Edecán tienen cobertura de Plaid para esto, y Stripe ya es la única
integración de pagos que el resto de la plataforma soporta, ver `routers/billing.py`).

## `/v1/finance/stripe/*` — mismo patrón "pegar y validar" que `routers/viajes.py`
(Amadeus/AfterShip): una `connector_account` (`connector_key="finance_stripe"`) +
`TokenVault` guardan la key restringida; nada se persiste sin validar primero contra la
API real de Stripe (`GET /v1/balance`, el endpoint de menor privilegio posible — solo
exige el permiso "Balance: Read").

**La key DEBE ser una Restricted key (`rk_...`), nunca la Secret key completa
(`sk_...`)** — se rechaza explícito en `put_stripe_credentials` antes de siquiera
intentar validarla, no solo se documenta: una `sk_live_...` completa puede mover dinero,
reembolsar, cambiar la cuenta bancaria de payout, etc.; Edecán solo necesita LEER. Cómo
crear la key restringida (Stripe Dashboard → Developers → API keys → Create restricted
key, con SOLO "Balance: Read" y "Balance Transactions: Read" en `True`, todo lo demás en
"None") y cómo limitarla por IP (Stripe no soporta allowlist de IP a nivel de key --
soporta restringir por *webhook endpoint*, no por request saliente-- así que la
mitigación real es el alcance mínimo de permisos de la key en sí, documentado en
`docs/finanzas-stripe.md`).

## Sincronización (`POST /v1/finance/stripe/sync`)

Trae los últimos `_SYNC_LIMIT` balance transactions de Stripe y los mapea a filas de
`transactions` (`fecha` del epoch `created`, `monto` de centavos a unidades, `moneda` en
mayúsculas, `categoria` del `type` de Stripe, `cuenta="Stripe"`). Sin columna dedicada
para el id externo (`transactions` no tiene una, igual que `contacts` — ver
`apps/web/src/app/(app)/app/contactos/page.tsx`/investigación de import de contactos):
el id de Stripe se embebe como marcador `[stripe:<id>]` al final de `descripcion`, y una
sincronización repetida lo busca ahí antes de insertar, para no duplicar.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
from edecan_db.vault import TokenVault
from edecan_schemas import TokenBundle
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from edecan_api.deps import CurrentUser, get_current_user, get_repo, get_vault, rate_limit
from edecan_api.repo import Repo

router = APIRouter(prefix="/v1/finance", tags=["finance"], dependencies=[Depends(rate_limit)])

# `transactions.moneda`/`monto` son `CHAR(3) NOT NULL`/`NUMERIC(14,2) NOT NULL` en el esquema
# (packages/db/edecan_db/models.py). Sin estos límites, repo.py pasa el valor directo al
# INSERT/UPDATE y Postgres revienta con un 500 sin capturar ("value too long for type
# character(3)" / "numeric field overflow") en vez de un 422 de validación; una moneda de
# menos de 3 caracteres además se guardaría con blank-padding silencioso. Mismo criterio de
# 3 letras que `edecan_toolkit.finanzas.RegistrarTransaccionTool`, pero acá se rechaza en vez
# de caer a "USD": este CRUD lo puede llamar la web, el móvil o un script cualquiera, así que
# un código mal formado debe ser un error explícito, no una sustitución silenciosa.
_MONEDA_RE = re.compile(r"^[A-Za-z]{3}$")


def _validar_moneda(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().upper()
    if not _MONEDA_RE.match(value):
        raise ValueError("moneda debe ser un código ISO-4217 de 3 letras, p. ej. 'USD'.")
    return value


class TransactionIn(BaseModel):
    fecha: date
    monto: Decimal = Field(max_digits=14, decimal_places=2)
    moneda: str = "USD"
    categoria: str | None = None
    descripcion: str | None = None
    cuenta: str | None = None

    _validar = field_validator("moneda")(_validar_moneda)


class TransactionPatch(BaseModel):
    fecha: date | None = None
    monto: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    moneda: str | None = None
    categoria: str | None = None
    descripcion: str | None = None
    cuenta: str | None = None

    _validar = field_validator("moneda")(_validar_moneda)


@router.get("/transactions")
async def list_transactions(
    mes: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    return await repo.list_transactions(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id, mes=mes
    )


@router.post("/transactions", status_code=status.HTTP_201_CREATED)
async def create_transaction(
    body: TransactionIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    return await repo.create_transaction(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id, fields=body.model_dump()
    )


@router.get("/transactions/{transaction_id}")
async def get_transaction(
    transaction_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    row = await repo.get_transaction(
        tenant_id=current_user.tenant_id, transaction_id=transaction_id
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Transacción no encontrada."
        )
    return row


@router.put("/transactions/{transaction_id}")
async def update_transaction(
    transaction_id: uuid.UUID,
    body: TransactionPatch,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    row = await repo.update_transaction(
        tenant_id=current_user.tenant_id, transaction_id=transaction_id, fields=fields
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Transacción no encontrada."
        )
    return row


@router.delete("/transactions/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    transaction_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    deleted = await repo.delete_transaction(
        tenant_id=current_user.tenant_id, transaction_id=transaction_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Transacción no encontrada."
        )


@router.get("/summary")
async def finance_summary(
    mes: str,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    return await repo.finance_summary(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id, mes=mes
    )


# ---------------------------------------------------------------------------
# PUT/DELETE/GET /v1/finance/stripe/credentials + POST /v1/finance/stripe/sync
# ---------------------------------------------------------------------------

_STRIPE_CONNECTOR_KEY = "finance_stripe"
_DISPLAY_NAME_STRIPE = "Stripe (finanzas)"
_STRIPE_API_BASE = "https://api.stripe.com/v1"
_STRIPE_TIMEOUT_SECONDS = 10.0
_SYNC_LIMIT = 100
_STRIPE_ID_MARKER_RE = re.compile(r"\[stripe:(\w+)\]")


class StripeCredentialsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    api_key: str
    validate_: bool = Field(default=True, alias="validate")


def _detalle_error_stripe(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"Stripe respondió {response.status_code}."
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return f"Stripe respondió {response.status_code}."


async def _ping_stripe(api_key: str, *, timeout: float) -> None:
    """`GET /v1/balance` — el endpoint de Stripe de menor privilegio posible,
    solo exige el permiso "Balance: Read" en la restricted key."""
    try:
        async with httpx.AsyncClient(base_url=_STRIPE_API_BASE, timeout=timeout) as client:
            response = await client.get("/balance", headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"No pudimos conectar con Stripe: {exc}"
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stripe rechazó la key: {_detalle_error_stripe(response)}",
        )


async def _find_stripe_account(repo: Repo, tenant_id: uuid.UUID) -> dict[str, Any] | None:
    accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    matches = [a for a in accounts if a["connector_key"] == _STRIPE_CONNECTOR_KEY]
    if not matches:
        return None
    return min(matches, key=lambda a: a["created_at"])


@router.put("/stripe/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def put_stripe_credentials(
    payload: StripeCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="El api_key no puede estar vacío."
        )
    if not api_key.startswith("rk_"):
        # Nunca una Secret key completa (`sk_...`, puede mover dinero) — ver
        # docstring del módulo. Se rechaza ANTES de intentar validarla contra
        # Stripe: una `sk_live_...` real validaría bien contra `/v1/balance`
        # (tiene de sobra ese permiso), así que la única forma de bloquearla
        # es este chequeo explícito del prefijo, no la validación en sí.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Usa una Restricted key de Stripe (empieza con 'rk_'), nunca tu Secret key "
                "completa ('sk_') — ver docs/finanzas-stripe.md para cómo crearla con solo "
                "permiso de lectura."
            ),
        )

    if payload.validate_:
        await _ping_stripe(api_key, timeout=_STRIPE_TIMEOUT_SECONDS)

    account = await _find_stripe_account(repo, current_user.tenant_id)
    if account is None:
        account = await repo.create_connector_account(
            tenant_id=current_user.tenant_id,
            connector_key=_STRIPE_CONNECTOR_KEY,
            external_account_id=_STRIPE_CONNECTOR_KEY,
            display_name=_DISPLAY_NAME_STRIPE,
            scopes=[],
        )
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=api_key, token_type="config", scopes=["stripe"]),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="finance.stripe_connected",
        target=_STRIPE_CONNECTOR_KEY,
    )


@router.delete("/stripe/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stripe_credentials(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    account = await _find_stripe_account(repo, current_user.tenant_id)
    if account is None:
        return  # idempotente: nada que borrar ya es un estado válido de "desconectado".
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="finance.stripe_disconnected",
        target=_STRIPE_CONNECTOR_KEY,
    )


@router.get("/stripe/status")
async def get_stripe_status(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> dict[str, Any]:
    account = await _find_stripe_account(repo, current_user.tenant_id)
    if account is None:
        return {"connected": False, "masked": None}
    bundle = await vault.get(current_user.tenant_id, account["id"])
    if bundle is None:
        return {"connected": False, "masked": None}
    key = bundle.access_token
    masked = f"…{key[-4:]}" if len(key) > 4 else "…"
    return {"connected": True, "masked": masked}


@router.post("/stripe/sync")
async def sync_stripe_transactions(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> dict[str, Any]:
    account = await _find_stripe_account(repo, current_user.tenant_id)
    bundle = await vault.get(current_user.tenant_id, account["id"]) if account else None
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Conecta tu cuenta de Stripe primero (PUT /v1/finance/stripe/credentials).",
        )
    api_key = bundle.access_token

    try:
        async with httpx.AsyncClient(
            base_url=_STRIPE_API_BASE, timeout=_STRIPE_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(
                "/balance_transactions",
                params={"limit": _SYNC_LIMIT},
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"No pudimos conectar con Stripe: {exc}"
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stripe rechazó la sincronización: {_detalle_error_stripe(response)}",
        )

    existentes = await repo.list_transactions(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id, mes=None
    )
    ids_ya_importados = {
        match.group(1)
        for t in existentes
        if (match := _STRIPE_ID_MARKER_RE.search(t.get("descripcion") or ""))
    }

    stripe_txns = response.json().get("data", [])
    sincronizadas = 0
    for txn in stripe_txns:
        if txn["id"] in ids_ya_importados:
            continue
        fecha = datetime.fromtimestamp(txn["created"], tz=UTC).date()
        monto = Decimal(txn["amount"]) / Decimal(100)
        descripcion_base = txn.get("description") or txn.get("type") or "stripe"
        await repo.create_transaction(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            fields={
                "fecha": fecha,
                "monto": monto,
                "moneda": str(txn["currency"]).upper(),
                "categoria": txn.get("type") or "stripe",
                "descripcion": f"{descripcion_base} [stripe:{txn['id']}]",
                "cuenta": "Stripe",
            },
        )
        sincronizadas += 1

    return {"sincronizadas": sincronizadas, "total_stripe": len(stripe_txns)}
