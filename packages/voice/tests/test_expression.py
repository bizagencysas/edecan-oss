from edecan_voice.expression import expressive_eleven_v3_text, plain_text_for_speech


def test_plain_text_for_speech_removes_markdown_without_losing_content() -> None:
    assert plain_text_for_speech("**Listo**. Abre [Edecán](https://edecan.cc).") == (
        "Listo. Abre Edecán."
    )


def test_expressive_eleven_v3_uses_one_safe_direction() -> None:
    assert expressive_eleven_v3_text("Listo, quedó configurado.") == (
        "[warmly] Listo, quedó configurado."
    )


def test_expressive_text_does_not_change_visible_source() -> None:
    source = "¿Qué te gustaría hacer?"
    spoken = expressive_eleven_v3_text(source)
    assert spoken == "[curious] ¿Qué te gustaría hacer?"
    assert source == "¿Qué te gustaría hacer?"
