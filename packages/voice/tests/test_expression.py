from edecan_core.speech_tags import SPEECH_TAG_RE
from edecan_voice.expression import expressive_eleven_v3_text, plain_text_for_speech


def test_plain_text_for_speech_removes_markdown_without_losing_content() -> None:
    assert plain_text_for_speech("**Listo**. Abre [Edecán](https://edecan.cc).") == (
        "Listo. Abre Edecán."
    )


def test_expressive_replaces_speech_tags_with_one_stable_direction() -> None:
    result = expressive_eleven_v3_text("[warmly] Listo, quedó configurado. [pause] ¿Te parece bien?")
    assert result == "[calmly] Listo, quedó configurado. ¿Te parece bien?"
    assert len(SPEECH_TAG_RE.findall(result)) == 1
