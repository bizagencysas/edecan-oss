"""Contrato del dataset golden versionado de Edecán."""

from edecan_core.evals import (
    GOLDEN_DATASET_VERSION,
    GOLDEN_TASKS,
    EvalResult,
    eval_summary,
    load_golden_dataset,
)


def test_golden_dataset_es_versionado_y_tiene_casos_unicos() -> None:
    version, cases = load_golden_dataset()

    assert version == GOLDEN_DATASET_VERSION
    assert version == "2026-08-20.v1"
    nombres = [case["name"] for case in cases]
    assert len(nombres) == len(set(nombres))
    assert len(GOLDEN_TASKS) == 8


def test_resumen_de_eval_identifica_el_dataset() -> None:
    resumen = eval_summary([EvalResult("caso", "latency", True)])

    assert resumen["dataset_version"] == "2026-08-20.v1"
    assert resumen["pass_rate"] == 1.0
