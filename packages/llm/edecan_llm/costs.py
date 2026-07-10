"""Estimación de costo en USD por uso de tokens (`ARCHITECTURE.md` §3).

Los valores de `COSTOS` son **placeholders de referencia** (USD por millón de
tokens, "MTok") pensados para estimar gasto/margen antes de escribir
`usage_events`. Son configuración de negocio, no secretos: actualízalos con el
pricing vigente de cada proveedor/modelo, o pasa tu propia tabla a
`estimate(..., costos=mi_tabla)` sin tocar el default.
"""

from __future__ import annotations

from .base import Usage

# {modelo: (usd_entrada_por_millon_tokens, usd_salida_por_millon_tokens)}
# Placeholders — revisar contra el pricing oficial de cada proveedor antes de
# usarlos para facturar de verdad.
COSTOS: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-opus-4-5": (15.00, 75.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}

_TOKENS_POR_MTOK = 1_000_000


def estimate(
    model: str,
    usage: Usage,
    costos: dict[str, tuple[float, float]] | None = None,
) -> float:
    """Estima el costo en USD de un `Usage` para `model`.

    Si `model` no está en la tabla de costos, devuelve `0.0` en vez de
    inventar un precio; quien llame puede loguear ese caso para completar
    `COSTOS`.
    """
    tabla = costos if costos is not None else COSTOS
    precios = tabla.get(model)
    if precios is None:
        return 0.0
    usd_entrada_mtok, usd_salida_mtok = precios
    costo_entrada = (usage.input_tokens / _TOKENS_POR_MTOK) * usd_entrada_mtok
    costo_salida = (usage.output_tokens / _TOKENS_POR_MTOK) * usd_salida_mtok
    return costo_entrada + costo_salida
