"""Tests del harness y los checkers de `edecan_core.evals_suite` (PHASE2.md §178-185).

Ver el docstring del módulo bajo prueba para el contexto: estas piezas codifican
la lógica de chequeo en heurísticas deterministas sobre texto, de modo que los
evals reales (contra salida de modelo) puedan correrse después sin I/O.
"""

from __future__ import annotations

import pytest
from edecan_core.evals_suite import (
    EvalCase,
    EvalResult,
    all_cases,
    answered_what_was_asked,
    avoids,
    avoids_any,
    contains_keyword,
    ends_turn,
    exact_match,
    has_max_words,
    mentions,
    mentions_any,
    no_fabricated_recollection,
    no_markdown_literal,
    no_placeholder,
    no_question_mark,
    no_raw_url,
    no_secret_or_private_path,
    not_empty,
    pass_rate,
    refuses_request,
    run_evals,
    security_cases,
    under_word_count,
    voice_cases,
    voice_natural,
    word_count,
)

# ---------------------------------------------------------------------------
# Estructura y validación de EvalCase
# ---------------------------------------------------------------------------


def test_evalcase_requiere_expected_o_predicate() -> None:
    with pytest.raises(ValueError):
        EvalCase(name="roto", input="hola", category="x")


def test_evalcase_rechaza_expected_y_predicate_a_la_vez() -> None:
    with pytest.raises(ValueError):
        EvalCase(
            name="roto",
            input="hola",
            category="x",
            expected="hola",
            predicate=not_empty,
        )


def test_evalcase_acepta_expected() -> None:
    caso = EvalCase(name="ok", input="hola", category="x", expected="hola")
    assert caso.predicate is None


def test_evalcase_acepta_predicate() -> None:
    caso = EvalCase(name="ok", input="hola", category="x", predicate=not_empty)
    assert caso.expected is None


# ---------------------------------------------------------------------------
# run_evals
# ---------------------------------------------------------------------------


def test_run_evals_expected_match_pasa() -> None:
    casos = [EvalCase(name="a", input="¿cómo?", category="x", expected="bien")]
    results = run_evals(casos, evaluator=lambda _c: "  bien  ")
    assert results == [EvalResult(name="a", passed=True, details="pass")]


def test_run_evals_expected_no_match_falla() -> None:
    casos = [EvalCase(name="a", input="¿cómo?", category="x", expected="bien")]
    results = run_evals(casos, evaluator=lambda _c: "mal")
    assert results[0].passed is False
    assert "expected 'bien'" in results[0].details


def test_run_evals_predicate_usa_la_salida() -> None:
    casos = [EvalCase(name="a", input="hola", category="x", predicate=not_empty)]
    results = run_evals(casos, evaluator=lambda _c: "respuesta")
    assert results[0].passed is True


def test_run_evals_evaluator_recibe_el_caso_completo() -> None:
    seen: list[EvalCase] = []

    def evaluator(case: EvalCase) -> str:
        seen.append(case)
        return case.input

    casos = [EvalCase(name="a", input="hola", category="x", expected="hola")]
    run_evals(casos, evaluator=evaluator)
    assert seen == casos


def test_run_evals_evaluator_error_no_propaga() -> None:
    casos = [EvalCase(name="a", input="hola", category="x", expected="hola")]

    def evaluator(_c: EvalCase) -> str:
        raise RuntimeError("boom")

    results = run_evals(casos, evaluator=evaluator)
    assert results[0].passed is False
    assert "evaluator raised RuntimeError" in results[0].details


def test_run_evals_checker_error_no_propaga() -> None:
    def malo(_output: str) -> bool:
        raise ValueError("checker roto")

    casos = [EvalCase(name="a", input="hola", category="x", predicate=malo)]
    results = run_evals(casos, evaluator=lambda _c: "algo")
    assert results[0].passed is False
    assert "check raised ValueError" in results[0].details


# ---------------------------------------------------------------------------
# pass_rate
# ---------------------------------------------------------------------------


def test_pass_rate_lista_vacia_es_cero() -> None:
    assert pass_rate([]) == 0.0


def test_pass_rate_todo_pasa_es_uno() -> None:
    results = [EvalResult("a", True), EvalResult("b", True)]
    assert pass_rate(results) == 1.0


def test_pass_rate_mezclado() -> None:
    results = [EvalResult("a", True), EvalResult("b", False)]
    assert pass_rate(results) == 0.5


# ---------------------------------------------------------------------------
# Checkers básicos
# ---------------------------------------------------------------------------


def test_not_empty() -> None:
    assert not_empty("hola") is True
    assert not_empty("   ") is False
    assert not_empty("") is False


def test_no_placeholder() -> None:
    assert no_placeholder("listo, te lo envié") is True
    assert no_placeholder("") is True
    assert no_placeholder("TODO: implementar esto") is False
    assert no_placeholder("Lorem ipsum dolor sit amet") is False
    assert no_placeholder("esto es un placeholder") is False
    assert no_placeholder("datos fake de prueba") is False
    # "todo" minúsculo (español) NO debe disparar el marcador TODO.
    assert no_placeholder("Todo listo, ya quedó") is True


def test_exact_match() -> None:
    assert exact_match("  hola ", "hola") is True
    assert exact_match("hola", "adiós") is False


def test_contains_keyword() -> None:
    assert contains_keyword("El Gato está en casa", "gato") is True
    assert contains_keyword("nada por aquí", "gato") is False


# ---------------------------------------------------------------------------
# Checkers conversacionales
# ---------------------------------------------------------------------------


def test_word_count_y_under_word_count() -> None:
    assert word_count("uno dos tres") == 3
    assert under_word_count("uno dos", 2) is True
    assert under_word_count("uno dos tres", 2) is False


def test_no_question_mark() -> None:
    assert no_question_mark("Listo, alarma puesta.") is True
    assert no_question_mark("¿A qué hora la quieres?") is False


def test_answered_what_was_asked() -> None:
    assert answered_what_was_asked("Me llamo Edecán.") is True
    assert answered_what_was_asked("") is False
    assert answered_what_was_asked("TODO: responder") is False


# ---------------------------------------------------------------------------
# Checker de memoria
# ---------------------------------------------------------------------------


def test_no_fabricated_recollection() -> None:
    assert no_fabricated_recollection("No tengo registro de eso.") is True
    assert no_fabricated_recollection("Me dijiste que preferías el plan A.") is False
    assert no_fabricated_recollection("Recuerdo que hablamos del tema.") is False


# ---------------------------------------------------------------------------
# Checker de abuso de herramientas
# ---------------------------------------------------------------------------


def test_refuses_request() -> None:
    assert refuses_request("No puedo hacer eso.") is True
    assert refuses_request("Lo siento, no está permitido enviar ese correo.") is True
    assert refuses_request("Correo enviado.") is False


# ---------------------------------------------------------------------------
# Checkers de voz
# ---------------------------------------------------------------------------


def test_no_raw_url() -> None:
    assert no_raw_url("te dejé el enlace en pantalla") is True
    assert no_raw_url("mira https://edecan.cc/a") is False


def test_no_markdown_literal() -> None:
    assert no_markdown_literal("Primero, el costo. Segundo, la latencia.") is True
    assert no_markdown_literal("Esto es **importante**") is False
    assert no_markdown_literal("Revisa el informe [1, 2]") is False
    assert no_markdown_literal("| A | B |\n|---|---|") is False
    assert no_markdown_literal("1. primero\n2. segundo") is False


def test_ends_turn() -> None:
    assert ends_turn("Perfecto, listo.") is True
    assert ends_turn("[warmly] Claro que sí.") is True
    assert ends_turn("quedó a medias") is False
    assert ends_turn("") is False


def test_voice_natural() -> None:
    assert voice_natural("Te recomiendo empezar con lo más simple.") is True
    assert voice_natural("mira https://edecan.cc/a") is False
    assert voice_natural("Esto es **importante**") is False
    assert voice_natural("") is False


def test_no_secret_or_private_path() -> None:
    assert no_secret_or_private_path("Listo, te dejé el resultado en pantalla.") is True
    assert no_secret_or_private_path("TOKEN=super-secreto-123456") is False
    assert no_secret_or_private_path("/Users/example/secret.txt") is False


def test_security_cases_forman_parte_de_all_cases() -> None:
    assert {case.name for case in security_cases()} <= {case.name for case in all_cases()}


# ---------------------------------------------------------------------------
# Checkers parametrizados
# ---------------------------------------------------------------------------


def test_mentions_y_mentions_any() -> None:
    assert mentions("gato")("hay un gato") is True
    assert mentions("gato")("hay un perro") is False
    assert mentions_any(("gato", "perro"))("hay un perro") is True
    assert mentions_any(("gato", "perro"))("hay una vaca") is False


def test_avoids_y_avoids_any() -> None:
    assert avoids("competidor")("no hablaré de eso") is True
    assert avoids("competidor")("el competidor es X") is False
    assert avoids_any(("competidor", "competencia"))("todo bien") is True
    assert avoids_any(("competidor", "competencia"))("hay competencia") is False


def test_has_max_words() -> None:
    assert has_max_words(2)("uno dos") is True
    assert has_max_words(2)("uno dos tres") is False


# ---------------------------------------------------------------------------
# Conjuntos de casos: integridad y ejecutabilidad determinista
# ---------------------------------------------------------------------------


def test_all_cases_no_esta_vacio_y_son_validos() -> None:
    casos = all_cases()
    assert len(casos) >= 10
    nombres = [c.name for c in casos]
    assert len(nombres) == len(set(nombres)), "nombres duplicados"
    for caso in casos:
        # exactamente uno de expected/predicate (ya validado en __post_init__)
        assert (caso.expected is not None) != (caso.predicate is not None)


def test_all_cases_corre_sin_excepciones_con_evaluador_fake() -> None:
    def fake(case: EvalCase) -> str:
        return case.input

    results = run_evals(all_cases(), evaluator=fake)
    assert len(results) == len(all_cases())
    # Con el evaluador echo, cada caso devuelve un resultado (pase o no, sin excepción).
    for result in results:
        assert isinstance(result, EvalResult)


def test_voice_cases_cubren_los_tres_ejes() -> None:
    nombres = {c.name for c in voice_cases()}
    assert nombres == {
        "voice_natural_speech",
        "voice_ends_turn_cleanly",
        "voice_no_markdown_literal",
    }
