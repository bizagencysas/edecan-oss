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
