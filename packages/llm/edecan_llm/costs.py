"""Estimación de costo en USD por uso de tokens (`ARCHITECTURE.md` §3).

`COSTOS` tiene dos capas:

1. **Placeholders de referencia** (USD por millón de tokens, "MTok") para
   modelos de proveedores externos (claude/gpt-4o) — actualízalos con el
   pricing vigente de cada proveedor, o pasa tu propia tabla a
   `estimate(..., costos=mi_tabla)` sin tocar el default.
2. **Precios reales de Workers AI** leídos de `config/modelos.yml`
   (`perfiles.<perfil>.precio_referencia`, a su vez leídos de la API de
   Cloudflare — ver el encabezado de ese archivo). Se fusionan ENCIMA de los
   placeholders para que los modelos reales del chat (scout, kimi) dejen de
   tener `cost_usd=None` y la alerta `USAGE_ALERT_USD_PER_DAY` pueda
   dispararse. Los modelos sin `precio_referencia` siguen devolviendo
   `cost_status="unknown"` honesto (ver `apps/api/.../llm_attribution.py`).
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


def _costos_desde_modelos_yml() -> dict[str, tuple[float, float]]:
    """Tabla de costos reales desde `perfiles.<perfil>.precio_referencia`.

    Solo el par `{modelo, precio_referencia}` de cada perfil: los precios
    viven en `config/modelos.yml` (lectura de la API de Cloudflare) y acá no
    se copian a mano. Un YAML ausente/corrupto o un perfil sin precio devuelve
    `{}` — jamás tumba la importación del módulo ni la estimación.
    """
    try:
        from .task_router import cargar_configuracion_modelos
    except Exception:  # noqa: BLE001 - el catálogo no puede tumbar una llamada
        return {}
    config = cargar_configuracion_modelos()
    perfiles = config.get("perfiles")
    if not isinstance(perfiles, dict):
        return {}
    tabla: dict[str, tuple[float, float]] = {}
    for data in perfiles.values():
        if not isinstance(data, dict):
            continue
        modelo = str(data.get("modelo") or "").strip()
        precio = data.get("precio_referencia")
        if not modelo or not isinstance(precio, dict):
            continue
        entrada = precio.get("entrada")
        salida = precio.get("salida")
        if isinstance(entrada, (int, float)) and isinstance(salida, (int, float)):
            tabla[modelo] = (float(entrada), float(salida))
    return tabla


COSTOS.update(_costos_desde_modelos_yml())

_TOKENS_POR_MTOK = 1_000_000


def estimate(
    model: str,
    usage: Usage,
    costos: dict[str, tuple[float, float]] | None = None,
    costos_cache: dict[str, float] | None = None,
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
    cached_tokens = getattr(usage, "cached_input_tokens", 0) or 0
    non_cached_input = max(0, usage.input_tokens - cached_tokens)

    price_cache_mtok = (
        costos_cache.get(model) if costos_cache and model in costos_cache else usd_entrada_mtok
    )

    costo_entrada_normal = (non_cached_input / _TOKENS_POR_MTOK) * usd_entrada_mtok
    costo_entrada_cache = (cached_tokens / _TOKENS_POR_MTOK) * price_cache_mtok
    costo_salida = (usage.output_tokens / _TOKENS_POR_MTOK) * usd_salida_mtok
    return costo_entrada_normal + costo_entrada_cache + costo_salida
