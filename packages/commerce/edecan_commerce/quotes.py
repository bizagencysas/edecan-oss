"""Cotizaciones de mercado — SOLO LECTURA (`ROADMAP_V2.md` §7.5/§7.7, WP-V2-10).

`QuoteProvider` es el protocolo común; `get_quote_provider(settings)` resuelve la
implementación activa según `settings.QUOTES_PROVIDER` (`stub`|`coingecko`), leído de forma
defensiva (`getattr(settings, "QUOTES_PROVIDER", "stub")` — convención dura de
`ROADMAP_V2.md` §7.5: "toda tool lee settings con getattr(...), nunca revienta si falta el
campo"; `edecan_api.config.Settings` todavía no declara este campo, ver `docs/dinero-real.md`).

Ninguna clase de este módulo escribe nada: ni una orden, ni una transacción, ni un archivo.
Es deliberadamente el módulo más simple del paquete — "cotizar" nunca debe poder mover nada.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

# Símbolos comunes soportados por `CoinGeckoQuotes` -> id de CoinGecko. Lista corta a
# propósito: crece bajo demanda, nunca se adivina un id para un símbolo no listado (eso
# podría cotizar el activo equivocado en silencio).
_COINGECKO_IDS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "USDT": "tether",
    "USDC": "usd-coin",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "MATIC": "matic-network",
    "LTC": "litecoin",
    "DOT": "polkadot",
}


@dataclass(frozen=True)
class Quote:
    """Una cotización normalizada, sin importar el proveedor de origen."""

    simbolo: str
    precio: float
    moneda: str
    fuente: str
    ts: datetime


@runtime_checkable
class QuoteProvider(Protocol):
    """Protocolo común de proveedor de cotizaciones."""

    async def quote(self, simbolo: str) -> Quote: ...


class StubQuotes:
    """Proveedor determinista y 100% offline (`QUOTES_PROVIDER=stub`, default).

    El precio sale de `sha256(simbolo)`: el mismo símbolo siempre cotiza al mismo precio (útil
    para tests y para self-host sin ninguna API externa configurada), pero no tiene ninguna
    relación con el precio real del activo — nunca se usa como fuente de verdad de producción.
    """

    async def quote(self, simbolo: str) -> Quote:
        simbolo_norm = simbolo.strip().upper()
        if not simbolo_norm:
            raise ValueError("Símbolo vacío.")
        digest = hashlib.sha256(simbolo_norm.encode("utf-8")).hexdigest()
        n = int(digest[:8], 16)
        precio = round(1.0 + (n % 10_000_000) / 100, 2)  # rango "creíble": 1.00 - 100000.99
        return Quote(
            simbolo=simbolo_norm,
            precio=precio,
            moneda="USD",
            fuente="stub",
            ts=datetime.now(UTC),
        )


class CoinGeckoQuotes:
    """CoinGecko Simple Price API — pública, oficial, sin API key.

    `GET https://api.coingecko.com/api/v3/simple/price?ids={id}&vs_currencies=usd`.
    """

    def __init__(self, base_url: str = _COINGECKO_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")

    async def quote(self, simbolo: str) -> Quote:
        simbolo_norm = simbolo.strip().upper()
        coingecko_id = _COINGECKO_IDS.get(simbolo_norm)
        if coingecko_id is None:
            raise ValueError(
                f"Símbolo '{simbolo_norm}' no reconocido por CoinGeckoQuotes. Símbolos "
                f"soportados: {', '.join(sorted(_COINGECKO_IDS))}."
            )
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            respuesta = await http.get(
                f"{self._base_url}/simple/price",
                params={"ids": coingecko_id, "vs_currencies": "usd"},
            )
        respuesta.raise_for_status()
        datos = respuesta.json()
        precio = (datos.get(coingecko_id) or {}).get("usd")
        if precio is None:
            raise ValueError(f"CoinGecko no devolvió precio en USD para '{simbolo_norm}'.")
        return Quote(
            simbolo=simbolo_norm,
            precio=float(precio),
            moneda="USD",
            fuente="coingecko",
            ts=datetime.now(UTC),
        )


def get_quote_provider(settings: Any) -> QuoteProvider:
    """Construye el `QuoteProvider` configurado en `settings.QUOTES_PROVIDER`.

    Lectura defensiva vía `getattr` (ver docstring del módulo): no acopla este paquete a una
    clase `Settings` concreta ni revienta si el campo todavía no existe.
    """
    proveedor = str(getattr(settings, "QUOTES_PROVIDER", None) or "stub").strip().lower()
    if proveedor == "coingecko":
        return CoinGeckoQuotes()
    if proveedor != "stub":
        logger.warning("QUOTES_PROVIDER=%r desconocido; usando 'stub'.", proveedor)
    return StubQuotes()
