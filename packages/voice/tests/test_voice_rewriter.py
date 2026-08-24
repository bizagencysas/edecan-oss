"""Tests de edecan_voice.voice_rewriter.rewrite_for_voice (PHASE2.md §21-25)."""

from __future__ import annotations

from edecan_voice.voice_rewriter import rewrite_for_voice


def test_numbered_list_becomes_spoken_enumeration() -> None:
    src = "Hay tres factores:\n1. Costo\n2. Latencia\n3. Seguridad\n"
    assert rewrite_for_voice(src) == (
        "Hay tres factores: Primero, Costo. Segundo, Latencia. Tercero, Seguridad."
    )


def test_bullet_list_becomes_spoken_enumeration() -> None:
    src = "- Manzana\n- Pera\n- Uva"
    assert rewrite_for_voice(src) == "Primero, Manzana. Segundo, Pera. Tercero, Uva."


def test_url_becomes_link_phrase() -> None:
    assert rewrite_for_voice("Mira https://edecan.cc/about para más.") == (
        "Mira te dejé el enlace en pantalla para más."
    )


def test_code_block_becomes_code_phrase() -> None:
    src = "Para instalar:\n```python\nprint('hola')\n```\nlisto."
    assert rewrite_for_voice(src) == "Para instalar: te puse el código en pantalla. listo."


def test_inline_code_is_unwrapped() -> None:
    assert rewrite_for_voice("Usa `pip install` para eso.") == "Usa pip install para eso."


def test_citations_become_sources_phrase() -> None:
    assert rewrite_for_voice("Según el informe [1, 2] esto es seguro.") == (
        "Según el informe según las fuentes esto es seguro."
    )


def test_table_becomes_table_phrase() -> None:
    src = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    assert rewrite_for_voice(src) == "te dejé una tabla comparativa en pantalla."


def test_markdown_bold_italic_are_stripped() -> None:
    assert rewrite_for_voice("**Listo**, _ahora_ funciona.") == "Listo, ahora funciona."


def test_speech_tags_are_preserved() -> None:
    src = "[warmly] Hola. [pause] Te muestro tres cosas:\n1. A\n2. B"
    assert rewrite_for_voice(src) == (
        "[warmly] Hola. [pause] Te muestro tres cosas: Primero, A. Segundo, B."
    )


def test_empty_input_is_returned_unchanged() -> None:
    assert rewrite_for_voice("") == ""


def test_length_is_truncated_at_max_words() -> None:
    words = " ".join(f"p{i}" for i in range(300))
    out = rewrite_for_voice(words, max_words=20)
    assert len(out.split()) <= 20


def test_english_language_uses_english_phrases() -> None:
    src = "See https://edecan.cc for details."
    assert rewrite_for_voice(src, language="en") == "See I left the link on screen for details."


def test_en_numbered_list_uses_english_ordinals() -> None:
    src = "1. Cost\n2. Latency\n3. Security"
    assert rewrite_for_voice(src, language="en") == (
        "First, Cost. Second, Latency. Third, Security."
    )
