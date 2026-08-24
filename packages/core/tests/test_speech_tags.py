from edecan_core.speech_tags import SPEECH_TAG_RE, enriquecer_speech_tags, ocultar_speech_tags


def test_parrafo_sin_tags_se_mantiene_limpio():
    fuente = (
        "No tengo la información exacta de cuánto gana Netflix por segundo. "
        "Sin embargo, puedo decirte que, según los resultados financieros de la empresa, "
        "en 2022 obtuvo unos ingresos de $32.000 millones de dólares. "
        "Si dividimos esta cantidad por el número de segundos en un año "
        "(aproximadamente 31,5 millones de segundos), podemos estimar que Netflix "
        "gana alrededor de $1.015 por segundo. "
        "Ten en cuenta que esta es solo una estimación y no refleja necesariamente "
        "la situación financiera actual de la empresa."
    )
    resultado = enriquecer_speech_tags(fuente)
    tags = SPEECH_TAG_RE.findall(resultado)
    assert len(tags) == 0
    assert not resultado.startswith("[")
    assert "$32.000 millones" in resultado
    assert "$1.015 por segundo" in resultado
    assert "Ten en cuenta" in resultado


def test_limpia_tags_del_modelo_en_oraciones():
    fuente = (
        "[warmly] ¡Claro! Déjame revisar eso. [pause] Encontré tres opciones. "
        "[curious] ¿Cuál te late?"
    )
    resultado = enriquecer_speech_tags(fuente)
    assert "[warmly]" not in resultado
    assert "[pause]" not in resultado
    assert "[curious]" not in resultado
    assert "¡Claro! Déjame revisar eso." in resultado
    assert len(SPEECH_TAG_RE.findall(resultado)) == 0


def test_limpia_parrafo_ya_taggeado():
    fuente = (
        "[warmly] ¡Claro! [pause] Déjame revisar eso. [thoughtful] Encontré tres "
        "opciones. [curious] ¿Cuál te late?"
    )
    assert (
        enriquecer_speech_tags(fuente)
        == "¡Claro! Déjame revisar eso. Encontré tres opciones. ¿Cuál te late?"
    )


def test_limpia_oraciones_sueltas():
    fuente = "[warmly] Hola. No tengo el dato exacto. ¿Lo calculamos juntos?"
    resultado = enriquecer_speech_tags(fuente)
    assert not resultado.startswith("[warmly]")
    assert len(SPEECH_TAG_RE.findall(resultado)) == 0


def test_no_mete_tags_dentro_de_codigo_ni_enlaces():
    fuente = "Mira esto: [Edecán](https://edecan.cc) y el snippet.\n```\nprint(1)\n```\nListo."
    resultado = enriquecer_speech_tags(fuente)
    assert "[Edecán](https://edecan.cc)" in resultado
    assert "```\nprint(1)\n```" in resultado
    assert resultado.startswith("Mira esto:")


def test_vacio_se_queda_igual():
    assert enriquecer_speech_tags("") == ""
    assert enriquecer_speech_tags("   ") == "   "


def test_acepta_cualquier_tag_o_efecto_y_el_chat_los_oculta():
    fuente = (
        "[thoughtfully] Recuerdo que eres Alex. "
        "[laughs] También trabajas en tecnología. "
        "[applause] Y [clears throat] valoras el humor. "
        "¿Es correcto?"
    )
    visible = ocultar_speech_tags(fuente)
    assert "[thoughtfully]" not in visible
    assert "[laughs]" not in visible
    assert "[applause]" not in visible
    assert "[clears throat]" not in visible
    assert "Recuerdo que eres Alex." in visible
    assert "También trabajas en tecnología." in visible
    assert "valoras el humor." in visible
    assert "¿Es correcto?" in visible


def test_oculta_tags_y_conserva_markdown():
    fuente = "[warmly] Mira [Edecán](https://edecan.cc) y ![foto](https://x/a.png)."
    visible = ocultar_speech_tags(fuente)
    assert "[warmly]" not in visible
    assert "[Edecán](https://edecan.cc)" in visible
    assert "![foto](https://x/a.png)" in visible


def test_tag_inventada_se_limpia_y_texto_queda_legible():
    fuente = (
        "[thoughtfully] Recuerdo que eres Alex Manuel Example Gonzalez, "
        "nacido el 8 de enero de 1996. "
        "También recuerdo que trabajas en tecnología. "
        "Además, valoras la comunicación humana. "
        "¿Es correcto?"
    )
    resultado = enriquecer_speech_tags(fuente)
    assert not resultado.startswith("[thoughtfully]")
    assert len(SPEECH_TAG_RE.findall(resultado)) == 0
    assert resultado.startswith("Recuerdo que eres")
