from __future__ import annotations

from edecan_core.explainability import (
    execution_summary,
    render_for_mode,
    why_did_you_do,
)


def test_execution_summary_muestra_las_cuatro_secciones_con_conteo() -> None:
    summary = execution_summary(
        done=["a", "b"],
        changed=["c"],
        verified=["d", "e", "f"],
        remaining=["g"],
    )

    assert "Done (2): a, b" in summary
    assert "Changed (1): c" in summary
    assert "Verified (3): d, e, f" in summary
    assert "Remaining (1): g" in summary


def test_execution_summary_marca_none_en_secciones_vacias() -> None:
    summary = execution_summary([], [], [], [])

    assert "Done: none" in summary
    assert "Changed: none" in summary
    assert "Verified: none" in summary
    assert "Remaining: none" in summary


def test_execution_summary_ignora_items_vacios() -> None:
    summary = execution_summary(
        done=["a", "", "  "],
        changed=[],
        verified=[],
        remaining=[],
    )

    assert "Done (1): a" in summary


def test_why_did_you_do_muestra_evidencia_tools_y_razon() -> None:
    explanation = why_did_you_do(
        evidence=["archivo.txt", "docs API"],
        tools_used=["buscar_web", "leer_archivo"],
        reason="la fuente oficial confirmaba la versión",
    )

    assert "Evidence: archivo.txt; docs API" in explanation
    assert "Tools: buscar_web, leer_archivo" in explanation
    assert "Why: la fuente oficial confirmaba la versión" in explanation


def test_why_did_you_do_maneja_listas_y_razon_vacias() -> None:
    explanation = why_did_you_do([], [], "")

    assert "Evidence: none" in explanation
    assert "Tools: none" in explanation
    assert "Why: none" in explanation


def test_why_did_you_do_redacta_marcadores_de_razonamiento() -> None:
    explanation = why_did_you_do(
        evidence=["let me think step by step about the answer"],
        tools_used=[],
        reason="mi chain of thought me llevó a decidir",
    )

    assert "let me think" not in explanation.lower()
    assert "chain of thought" not in explanation.lower()


def test_why_did_you_do_elimina_bloques_de_cot() -> None:
    explanation = why_did_you_do(
        evidence=["<thinking>esto es CoT privado</thinking> archivo real"],
        tools_used=[],
        reason="acción basada en evidencia",
    )

    assert "esto es CoT privado" not in explanation
    assert "archivo real" in explanation


def test_why_did_you_do_descarta_evidencia_que_es_solo_cot() -> None:
    explanation = why_did_you_do(
        evidence=["<thinking>solo razonamiento</thinking>"],
        tools_used=[],
        reason="razón válida",
    )

    assert "Evidence: none" in explanation
    assert "<thinking>" not in explanation


def test_render_for_mode_simple_oculta_detalles_tecnicos() -> None:
    rendered = render_for_mode(
        "Resumen",
        ["tests: 12 passed", "logs: sin errores"],
        expert=False,
    )

    assert rendered == "Resumen"
    assert "tests" not in rendered
    assert "logs" not in rendered


def test_render_for_mode_experto_inyecta_detalles_colapsables() -> None:
    rendered = render_for_mode(
        "Resumen",
        ["tests: 12 passed", "diffs: +3 -1"],
        expert=True,
    )

    assert "Resumen" in rendered
    assert "<details>" in rendered
    assert "<summary>Technical details</summary>" in rendered
    assert "tests: 12 passed" in rendered
    assert "diffs: +3 -1" in rendered


def test_render_for_mode_experto_sin_detalles_devuelve_solo_resumen() -> None:
    assert render_for_mode("Resumen", [], expert=True) == "Resumen"
    assert render_for_mode("Resumen", ["", "  "], expert=True) == "Resumen"