"""Tests de defaults de `edecan_api.config.Settings` que importan por veracidad.

`QUOTES_PROVIDER` es el único de los proveedores "stub" con un llamador real en producción
(`edecan_commerce.tools.get_quote_provider`, ver `packages/commerce/edecan_commerce/quotes.py`)
— a diferencia de `SEARCH_PROVIDER`/`IMAGES_PROVIDER`, cuyos selectores no tienen ningún
llamador (medido: `buscar_web`/`generar_imagen` resuelven siempre por tenant, nunca por esta
config de plataforma). Por eso este test fija en piedra el default correcto: si alguien lo
revierte a `"stub"` sin querer, `cotizar_activo` vuelve a inventar precios con `sha256` del
símbolo (medido: BTC con 2,7% de error creíble, AAPL 166 veces el precio real) para cualquier
instalación nueva, sin que nadie lo pida.
"""

from __future__ import annotations

from edecan_api.config import Settings


def test_quotes_provider_default_es_coingecko_no_stub() -> None:
    """Una instalación recién clonada, sin ningún `.env`, cotiza cripto real (CoinGecko: sin
    clave, sin costo, medido funcionando) en vez de inventar un precio con `sha256` del
    símbolo. Ver `packages/commerce/edecan_commerce/quotes.py` para el contrato completo."""
    settings = Settings()
    assert settings.QUOTES_PROVIDER == "coingecko"
