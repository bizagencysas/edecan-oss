"""Tests de los evals de voz (PHASE2.md §179) vía `edecan_core.evals_suite`.

La lógica de chequeo de voz vive en `edecan_core.evals_suite` (naturalidad, fin
de turno y ausencia de Markdown literal) y es independiente de `edecan_voice`
(los tests de este paquete no importan hermanos de implementación; aquí solo se
ejercita el *check logic* determinista, listo para correrse contra salida real
de un TTS/reescritor después).
"""

from __future__ import annotations

from edecan_core.evals_suite import (
    ends_turn,
    no_markdown_literal,
    no_raw_url,
    run_evals,
    voice_cases,
    voice_natural,
)


def test_voice_cases_son_tres_y_estan_en_la_categoria_voice() -> None:
    casos = voice_cases()
    assert len(casos) == 3
    assert all(c.category == "voice" for c in casos)


def test_no_markdown_literal_rechaza_marcas_que_el_tts_leeria_literal() -> None:
    # Bien: texto hablado plano.
    assert no_markdown_literal("Primero, el costo. Segundo, la latencia.") is True
    # Mal: negrita, citación, tabla y lista se leerían carácter por carácter.
    assert no_markdown_literal("Esto es **muy** importante") is False
    assert no_markdown_literal("según el informe [1, 2]") is False
    assert no_markdown_literal("| A | B |\n|---|---|") is False
    assert no_markdown_literal("- manzana\n- pera") is False


def test_no_raw_url_evita_leer_la_url_caracter_por_caracter() -> None:
    assert no_raw_url("te dejé el enlace en pantalla") is True
    assert no_raw_url("mira https://edecan.cc/a para más") is False


def test_ends_turn_detecta_cierre_limpio() -> None:
    assert ends_turn("Perfecto, listo.") is True
    assert ends_turn("[warmly] Claro, te cuento.") is True
    assert ends_turn("esto quedó incompleto") is False


def test_voice_natural_composicion_de_senales() -> None:
    assert voice_natural("Te recomiendo empezar por lo más simple.") is True
    assert voice_natural("") is False
    assert voice_natural("mira https://edecan.cc/a") is False
    assert voice_natural("esto es **importante**") is False


def test_run_evals_con_salida_apta_para_voz_todo_pasa() -> None:
    def evaluator(_case) -> str:
        return "Te muestro tres ideas para mejorar la retención, si te parece."

    results = run_evals(voice_cases(), evaluator=evaluator)
    assert all(r.passed for r in results)


def test_run_evals_con_markdown_literal_falla_el_caso_de_markdown() -> None:
    def evaluator(case) -> str:
        if case.name == "voice_no_markdown_literal":
            return "Aquí van los pasos:\n1. primero\n2. segundo"
        return "Respuesta natural y completa."

    results = run_evals(voice_cases(), evaluator=evaluator)
    by_name = {r.name: r for r in results}
    assert by_name["voice_no_markdown_literal"].passed is False
    assert by_name["voice_natural_speech"].passed is True
    assert by_name["voice_ends_turn_cleanly"].passed is True