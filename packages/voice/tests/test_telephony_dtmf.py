"""Marcado de teclas (DTMF) en llamadas: `<Play digits>` + `Gather` que también capta tonos.

Sin esto el agente se queda mudo ante un menú automático ("marque 1 para ventas"): intentaría
decir el número en voz alta, que no marca nada. Como el modelo no puede emitir XML, pide los
tonos con el marcador `[[tonos:1]]` dentro de su respuesta y `extraer_tonos` lo convierte en
un `<Play digits>` real, quitándolo del texto que se habla.
"""

from __future__ import annotations

import pytest
from edecan_voice.telephony import conversation_twiml, extraer_tonos, normalizar_digitos


class TestNormalizarDigitos:
    @pytest.mark.parametrize(
        ("entrada", "esperado"),
        [
            ("123", "123"),
            ("*0#", "*0#"),
            ("w1", "w1"),  # `w` = pausa de ~0.5s que acepta Twilio
            ("1-2 3", "123"),  # separadores humanos: se limpian
            ("W1", "w1"),  # se normaliza a minúscula
            ("abc", ""),  # nada marcable -> vacío, el llamador omite el <Play>
            ("", ""),
            (None, ""),
        ],
    )
    def test_deja_solo_lo_marcable(self, entrada: str | None, esperado: str) -> None:
        assert normalizar_digitos(entrada) == esperado

    def test_recorta_secuencias_absurdamente_largas(self) -> None:
        assert len(normalizar_digitos("1" * 500)) == 32


class TestExtraerTonos:
    def test_saca_el_marcador_del_texto_hablado(self) -> None:
        # Nadie debe oír "corchete corchete tonos dos puntos uno".
        hablado, digitos = extraer_tonos("Un momento. [[tonos:1]] Ya marqué.")
        assert hablado == "Un momento. Ya marqué."
        assert digitos == "1"

    def test_respuesta_de_solo_tonos_deja_el_texto_vacio(self) -> None:
        hablado, digitos = extraer_tonos("[[tonos:2]]")
        assert hablado == ""
        assert digitos == "2"

    def test_varios_marcadores_se_concatenan_en_orden(self) -> None:
        _, digitos = extraer_tonos("[[tonos:1]] espera [[tonos:#]]")
        assert digitos == "1#"

    def test_tolera_espacios_y_mayusculas_en_el_marcador(self) -> None:
        _, digitos = extraer_tonos("[[ TONOS : 4 2 ]]")
        assert digitos == "42"

    def test_texto_sin_marcador_pasa_intacto(self) -> None:
        hablado, digitos = extraer_tonos("Buenas tardes, le llamo de parte de la oficina.")
        assert hablado == "Buenas tardes, le llamo de parte de la oficina."
        assert digitos == ""

    def test_no_confunde_corchetes_normales_con_un_marcador(self) -> None:
        hablado, digitos = extraer_tonos("Dijo [algo entre corchetes] y nada más.")
        assert digitos == ""
        assert "corchetes" in hablado


class TestConversationTwiml:
    def test_emite_play_digits_despues_de_hablar(self) -> None:
        # El orden importa: primero se dice lo que haya que decir, luego se marca. Al revés,
        # el tono se pierde mientras la central todavía está enunciando el menú.
        xml = conversation_twiml(
            message="Un momento.", gather_url="https://x.test/g", send_digits="1"
        )
        assert '<Play digits="1" />' in xml or '<Play digits="1"/>' in xml
        assert xml.index("Say") < xml.index("digits")

    def test_sin_tonos_no_emite_play_digits(self) -> None:
        xml = conversation_twiml(message="Hola.", gather_url="https://x.test/g")
        assert "digits" not in xml

    def test_digitos_no_marcables_no_producen_twiml_invalido(self) -> None:
        # Un `send_digits` basura debe ignorarse, nunca romper la llamada entera.
        xml = conversation_twiml(
            message="Hola.", gather_url="https://x.test/g", send_digits="hola"
        )
        assert "digits" not in xml

    def test_el_gather_tambien_capta_tonos_del_otro_lado(self) -> None:
        # Un menú puede pedir confirmación por teclado; con `input="speech"` a secas, marcar
        # una tecla se vería idéntico a un silencio.
        xml = conversation_twiml(message="Hola.", gather_url="https://x.test/g")
        assert 'input="speech dtmf"' in xml

    def test_al_terminar_marca_los_tonos_antes_de_colgar(self) -> None:
        xml = conversation_twiml(
            message="Listo.",
            gather_url="https://x.test/g",
            end_after_message=True,
            send_digits="9",
        )
        assert xml.index("digits") < xml.index("Hangup")


class TestConfirmacionDeLlamada:
    """`EDECAN_PHONE_REQUIRE_CONFIRMATION` decide si marcar exige tarjeta de confirmación.

    Por defecto SÍ: para una instalación nueva, que un modelo marque un teléfono real sin
    freno es demasiado. El dueño puede apagarlo cuando su propio mensaje ya trae número,
    destinatario, objetivo y agente -- ahí la tarjeta le pregunta lo que acaba de escribir.
    """

    def _llamar(self):
        from edecan_voice.tools import get_all_tools

        return next(t for t in get_all_tools() if t.name == "llamar_contacto")

    def test_por_defecto_exige_confirmacion(self, monkeypatch) -> None:
        monkeypatch.delenv("EDECAN_PHONE_REQUIRE_CONFIRMATION", raising=False)
        assert self._llamar().dangerous is True

    @pytest.mark.parametrize("valor", ["0", "false", "no", "off", "OFF", " 0 "])
    def test_se_puede_apagar(self, monkeypatch, valor: str) -> None:
        monkeypatch.setenv("EDECAN_PHONE_REQUIRE_CONFIRMATION", valor)
        assert self._llamar().dangerous is False

    @pytest.mark.parametrize("valor", ["1", "true", "yes", "cualquier-cosa"])
    def test_cualquier_otro_valor_mantiene_el_freno(self, monkeypatch, valor: str) -> None:
        # Fail-closed: un valor que no sea un "no" explícito NO desactiva la protección.
        monkeypatch.setenv("EDECAN_PHONE_REQUIRE_CONFIRMATION", valor)
        assert self._llamar().dangerous is True

    def test_apagarlo_no_toca_las_otras_defensas(self, monkeypatch) -> None:
        # El flag de plan sigue exigiéndose: apagar la tarjeta no regala la capacidad.
        monkeypatch.setenv("EDECAN_PHONE_REQUIRE_CONFIRMATION", "0")
        assert self._llamar().requires_flags == frozenset({"voice.telephony"})
