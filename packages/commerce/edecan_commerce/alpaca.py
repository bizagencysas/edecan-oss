"""Cliente seguro para Alpaca Paper Trading.

Solo usa ``https://paper-api.alpaca.markets``. No acepta una URL arbitraria
ni contiene un modo live. Las claves siempre llegan desde el TokenVault del
tenant y no forman parte de ``repr``, errores, logs o resultados de tools.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
from edecan_core import Tool, ToolContext, ToolResult
from edecan_schemas.plans import FLAG_COMMERCE_ORDERS
from sqlalchemy import text

logger = logging.getLogger(__name__)

ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_CONNECTOR_KEY = "alpaca_paper"
_TIMEOUT_SECONDS = 20.0


class AlpacaAPIError(RuntimeError):
    """Error legible que conserva request id, nunca headers ni credenciales."""

    def __init__(self, message: str, *, status_code: int | None, request_id: str | None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class AlpacaCredentials:
    api_key_id: str = field(repr=False)
    secret_key: str = field(repr=False)


def _provider_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:240].strip() or "respuesta sin detalle"
    if isinstance(payload, dict):
        value = payload.get("message") or payload.get("error") or payload.get("detail")
        if value:
            return str(value)[:240]
    return "respuesta no reconocida"


class AlpacaPaperClient:
    """Superficie mínima de cuenta, posiciones y órdenes paper."""

    def __init__(
        self,
        credentials: AlpacaCredentials,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        if not credentials.api_key_id.strip() or not credentials.secret_key.strip():
            raise ValueError("Las dos credenciales de Alpaca Paper son obligatorias.")
        self._credentials = credentials
        self._client = client
        self._timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._credentials.api_key_id,
            "APCA-API-SECRET-KEY": self._credentials.secret_key,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{ALPACA_PAPER_BASE_URL}{path}"
        try:
            if self._client is not None:
                response = await self._client.request(
                    method, url, headers=self._headers, params=params, json=json_body
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(
                        method, url, headers=self._headers, params=params, json=json_body
                    )
        except httpx.HTTPError as exc:
            raise AlpacaAPIError(
                "No se pudo contactar Alpaca Paper.", status_code=None, request_id=None
            ) from exc

        request_id = response.headers.get("x-request-id")
        if not 200 <= response.status_code < 300:
            raise AlpacaAPIError(
                f"Alpaca Paper rechazó la solicitud: {_provider_message(response)}",
                status_code=response.status_code,
                request_id=request_id,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise AlpacaAPIError(
                "Alpaca Paper devolvió una respuesta ilegible.",
                status_code=response.status_code,
                request_id=request_id,
            ) from exc

    async def get_account(self) -> dict[str, Any]:
        value = await self._request("GET", "/v2/account")
        if not isinstance(value, dict):
            raise AlpacaAPIError(
                "Alpaca Paper no devolvió una cuenta válida.", status_code=200, request_id=None
            )
        return value

    async def list_positions(self) -> list[dict[str, Any]]:
        value = await self._request("GET", "/v2/positions")
        return (
            [dict(item) for item in value if isinstance(item, dict)]
            if isinstance(value, list)
            else []
        )

    async def list_orders(self, *, status: str = "open", limit: int = 50) -> list[dict[str, Any]]:
        if status not in {"open", "closed", "all"}:
            raise ValueError("status debe ser open, closed o all.")
        value = await self._request(
            "GET",
            "/v2/orders",
            params={"status": status, "limit": max(1, min(limit, 100)), "direction": "desc"},
        )
        return (
            [dict(item) for item in value if isinstance(item, dict)]
            if isinstance(value, list)
            else []
        )

    async def get_order_by_client_id(self, client_order_id: str) -> dict[str, Any]:
        value = await self._request(
            "GET",
            "/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id},
        )
        if not isinstance(value, dict):
            raise AlpacaAPIError(
                "Alpaca Paper no devolvió una orden válida.", status_code=200, request_id=None
            )
        return value

    async def submit_market_order(
        self, *, symbol: str, side: str, quantity: Decimal, client_order_id: str
    ) -> dict[str, Any]:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol or side not in {"buy", "sell"} or quantity <= 0:
            raise ValueError("Símbolo, lado y cantidad de la orden son inválidos.")
        payload = {
            "symbol": normalized_symbol,
            "qty": format(quantity, "f"),
            "side": side,
            "type": "market",
            "time_in_force": "gtc" if "/" in normalized_symbol else "day",
            "client_order_id": client_order_id,
        }
        try:
            value = await self._request("POST", "/v2/orders", json_body=payload)
        except AlpacaAPIError as original:
            # Un timeout o un 422 por reintento puede significar que Alpaca ya
            # creó la orden. Consultar por el id idempotente evita duplicarla.
            try:
                return await self.get_order_by_client_id(client_order_id)
            except AlpacaAPIError:
                raise original from None
        if not isinstance(value, dict):
            raise AlpacaAPIError(
                "Alpaca Paper no devolvió la orden creada.", status_code=200, request_id=None
            )
        return value


async def resolve_alpaca_paper_client(
    *, session: Any, vault: Any, tenant_id: Any
) -> AlpacaPaperClient | None:
    """Resuelve el cliente del tenant o ``None`` si aún no fue conectado."""

    if session is None or vault is None:
        return None
    try:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT id FROM connector_accounts WHERE tenant_id = :tenant_id "
                        "AND connector_key = :connector_key ORDER BY created_at ASC LIMIT 1"
                    ),
                    {"tenant_id": str(tenant_id), "connector_key": ALPACA_CONNECTOR_KEY},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        bundle = await vault.get(tenant_id, row["id"])
        if bundle is None:
            return None
        config = json.loads(bundle.access_token)
        if not isinstance(config, dict) or config.get("environment") != "paper":
            return None
        api_key_id = str(config.get("api_key_id") or "").strip()
        secret_key = str(config.get("secret_key") or "").strip()
        if not api_key_id or not secret_key:
            return None
        return AlpacaPaperClient(AlpacaCredentials(api_key_id, secret_key))
    except Exception:
        logger.warning(
            "No se pudo resolver Alpaca Paper para tenant_id=%s", tenant_id, exc_info=True
        )
        return None


def _money(value: Any) -> str:
    try:
        return f"${Decimal(str(value)):,.2f}"
    except Exception:
        return "$0.00"


class ConsultarAlpacaTool(Tool):
    name = "consultar_alpaca"
    description = (
        "Consulta la cuenta simulada Alpaca Paper conectada por el usuario: balance, "
        "poder de compra, posiciones u órdenes. Nunca usa una cuenta live."
    )
    requires_flags = frozenset({FLAG_COMMERCE_ORDERS})
    input_schema = {
        "type": "object",
        "properties": {
            "vista": {
                "type": "string",
                "enum": ["cuenta", "posiciones", "ordenes"],
                "default": "cuenta",
            },
            "estado_ordenes": {
                "type": "string",
                "enum": ["open", "closed", "all"],
                "default": "open",
            },
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        client = await resolve_alpaca_paper_client(
            session=ctx.session, vault=ctx.vault, tenant_id=ctx.tenant_id
        )
        if client is None:
            return ToolResult(
                content=(
                    "Alpaca Paper todavía no está conectado. Pega en el chat tu API Key ID "
                    "y tu Secret Key de paper para configurarlo."
                )
            )
        vista = str(args.get("vista") or "cuenta").lower()
        try:
            if vista == "posiciones":
                positions = await client.list_positions()
                if not positions:
                    return ToolResult(
                        content="Tu cuenta Alpaca Paper no tiene posiciones abiertas.",
                        data={"positions": []},
                    )
                lines = [
                    f"{item.get('symbol')}: {item.get('qty')} unidades, "
                    f"valor {_money(item.get('market_value'))}"
                    for item in positions[:30]
                ]
                return ToolResult(
                    content="Posiciones Alpaca Paper:\n" + "\n".join(lines),
                    data={"positions": positions},
                )
            if vista == "ordenes":
                status = str(args.get("estado_ordenes") or "open").lower()
                orders = await client.list_orders(status=status)
                if not orders:
                    return ToolResult(
                        content=f"No hay órdenes {status} en Alpaca Paper.", data={"orders": []}
                    )
                lines = [
                    f"{item.get('side')} {item.get('qty')} "
                    f"{item.get('symbol')}: {item.get('status')}"
                    for item in orders[:30]
                ]
                return ToolResult(
                    content="Órdenes Alpaca Paper:\n" + "\n".join(lines), data={"orders": orders}
                )
            account = await client.get_account()
            content = (
                f"Alpaca Paper: patrimonio {_money(account.get('equity'))}, efectivo "
                f"{_money(account.get('cash'))}, poder de compra "
                f"{_money(account.get('buying_power'))}."
            )
            if bool(account.get("trading_blocked")):
                content += " La cuenta tiene el trading bloqueado."
            return ToolResult(content=content, data={"account": account})
        except (AlpacaAPIError, ValueError) as exc:
            return ToolResult(content=f"No pude consultar Alpaca Paper: {exc}")


class AlpacaPaperBroker:
    """Envía una orden ya confirmada a Alpaca Paper y conserva su evidencia."""

    async def execute(
        self,
        order: Mapping[str, Any],
        session: Any,
        client: AlpacaPaperClient,
    ) -> dict[str, Any]:
        if order.get("status") != "confirmed" or order.get("kind") != "trade":
            raise ValueError("Alpaca Paper solo acepta órdenes de trading ya confirmadas.")
        order_id = order["id"]
        symbol = str(order.get("simbolo") or "").strip().upper()
        side = str(order.get("lado") or "").strip().lower()
        try:
            quantity = Decimal(str(order.get("cantidad")))
        except Exception as exc:
            raise ValueError("La cantidad de la orden no es válida.") from exc
        client_order_id = f"edecan-{str(order_id)}"
        remote = await client.submit_market_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            client_order_id=client_order_id,
        )
        meta = dict(order.get("meta") or {}) if isinstance(order.get("meta"), dict) else {}
        meta["alpaca_paper"] = {
            "order_id": remote.get("id"),
            "client_order_id": remote.get("client_order_id") or client_order_id,
            "status": remote.get("status"),
            "submitted_at": remote.get("submitted_at"),
            "symbol": remote.get("symbol") or symbol,
            "side": remote.get("side") or side,
            "qty": remote.get("qty") or format(quantity, "f"),
        }
        row = (
            (
                await session.execute(
                    text(
                        "UPDATE orders SET status = 'executed_paper', executed_at = now(), "
                        "updated_at = now(), meta = CAST(:meta AS jsonb) "
                        "WHERE id = :id ::uuid AND status = 'confirmed' RETURNING *"
                    ),
                    {"id": str(order_id), "meta": json.dumps(meta)},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise RuntimeError(
                "Alpaca aceptó la orden, pero Edecán no pudo actualizar su copia local. "
                f"Busca client_order_id={client_order_id} en Alpaca Paper."
            )
        await session.execute(
            text(
                "INSERT INTO audit_log (tenant_id, actor_user_id, action, target, meta) "
                "VALUES (:tenant_id, :user_id, 'commerce.alpaca_paper.executed', :target, "
                "CAST(:meta AS jsonb))"
            ),
            {
                "tenant_id": str(order["tenant_id"]),
                "user_id": str(order["user_id"]),
                "target": str(order_id),
                "meta": json.dumps(meta["alpaca_paper"]),
            },
        )
        await session.flush()
        return dict(row)
