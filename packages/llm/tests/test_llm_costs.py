"""Tests de estimación de costo (`edecan_llm.costs`)."""

from __future__ import annotations

import pytest
from edecan_llm.base import Usage
from edecan_llm.costs import COSTOS, estimate


def test_estimate_modelo_conocido() -> None:
    costo = estimate("claude-sonnet-4-5", Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    usd_entrada, usd_salida = COSTOS["claude-sonnet-4-5"]
    assert costo == pytest.approx(usd_entrada + usd_salida)


def test_estimate_modelo_desconocido_devuelve_cero() -> None:
    assert estimate("modelo-que-no-existe", Usage(input_tokens=100, output_tokens=100)) == 0.0


def test_estimate_sin_uso_es_cero() -> None:
    assert estimate("claude-sonnet-4-5", Usage()) == 0.0


def test_estimate_tabla_personalizada() -> None:
    tabla = {"mi-modelo": (1.0, 2.0)}
    costo = estimate("mi-modelo", Usage(input_tokens=500_000, output_tokens=500_000), costos=tabla)
    assert costo == pytest.approx(0.5 + 1.0)


def test_costos_tiene_los_modelos_pinned_en_env_example() -> None:
    # ARCHITECTURE.md §10.2: ANTHROPIC_MODEL_PRINCIPAL/RAPIDO por defecto.
    assert "claude-sonnet-4-5" in COSTOS
    assert "claude-haiku-4-5" in COSTOS


def test_costos_carga_precio_referencia_desde_modelos_yml() -> None:
    """C9a: los precios reales de `config/modelos.yml`
    (`perfiles.<perfil>.precio_referencia`) se fusionan en `COSTOS` — así los
    modelos reales del chat (scout, kimi) dejan de tener `cost_usd=None`.

    No se copia el valor a mano: se contrasta contra el propio YAML, que es la
    autoridad, para no romper el test cuando cambie un precio (es DATO).
    """
    from edecan_llm.task_router import cargar_configuracion_modelos

    config = cargar_configuracion_modelos()
    perfiles = config.get("perfiles") or {}
    con_precio = 0
    for data in perfiles.values():
        if not isinstance(data, dict):
            continue
        modelo = str(data.get("modelo") or "").strip()
        precio = data.get("precio_referencia")
        if not modelo or not isinstance(precio, dict):
            continue
        entrada = precio.get("entrada")
        salida = precio.get("salida")
        if not (isinstance(entrada, (int, float)) and isinstance(salida, (int, float))):
            continue
        con_precio += 1
        assert modelo in COSTOS
        assert COSTOS[modelo] == (float(entrada), float(salida))
    # scout (chat_rapido) y kimi-k2.7-code (ingenieria_software) como mínimo.
    assert con_precio >= 2


def test_estimate_scout_tiene_costo_real() -> None:
    """El modelo real del chat ya estima costo distinto de cero (alerta viva)."""
    scout = "@cf/meta/llama-4-scout-17b-16e-instruct"
    assert scout in COSTOS
    entrada, salida = COSTOS[scout]
    costo = estimate(scout, Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert costo == pytest.approx(entrada + salida)
