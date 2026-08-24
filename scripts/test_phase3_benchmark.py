from __future__ import annotations

import pytest

from scripts.phase3_benchmark import percentile, run


def test_percentile_es_determinista_y_acotado() -> None:
    valores = [5, 1, 9, 3, 7]
    assert percentile(valores, 0.50) == 5
    assert percentile(valores, 0.95) == 9
    with pytest.raises(ValueError):
        percentile([], 0.95)


def test_benchmark_reporta_denominador_concurrencia_y_sin_efectos() -> None:
    reporte = run(iterations=3, concurrency=2)
    assert reporte["samples"] == 1536
    assert reporte["case_count"] == 512
    assert reporte["concurrency"] == 2
    assert reporte["external_calls"] is False
    assert set(reporte["latency_ms"]) == {"p50", "p95", "p99"}
    assert reporte["throughput_samples_per_second"] > 0
