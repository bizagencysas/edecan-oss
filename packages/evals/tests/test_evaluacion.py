"""Tests de `edecan_evals.runner.evaluar_caso` (pura, sin `edecan_core`)."""

from __future__ import annotations

from edecan_evals.runner import _parece_rechazo, evaluar_caso
from edecan_evals.schema import Caso, Esperado


def _caso(esperado: Esperado) -> Caso:
    return Caso(id="c1", mensajes=["hola"], esperado=esperado)


def test_sin_aserciones_siempre_aprueba() -> None:
    resultado = evaluar_caso(_caso(Esperado()), tools_llamadas=[], texto_final="lo que sea")
    assert resultado.aprobado
    assert resultado.razones == []


def test_tools_llamadas_coincide_aprueba() -> None:
    esperado = Esperado(tools_llamadas=["hora_actual"])
    resultado = evaluar_caso(_caso(esperado), tools_llamadas=["hora_actual"], texto_final="")
    assert resultado.aprobado


def test_tools_llamadas_orden_importa() -> None:
    esperado = Esperado(tools_llamadas=["a", "b"])
    resultado = evaluar_caso(_caso(esperado), tools_llamadas=["b", "a"], texto_final="")
    assert not resultado.aprobado
    assert "tools_llamadas" in resultado.razones[0]


def test_tools_llamadas_none_no_se_verifica() -> None:
    esperado = Esperado(tools_llamadas=None)
    resultado = evaluar_caso(_caso(esperado), tools_llamadas=["cualquiera"], texto_final="")
    assert resultado.aprobado


def test_tools_llamadas_vacia_exige_ninguna_llamada() -> None:
    esperado = Esperado(tools_llamadas=[])
    aprueba = evaluar_caso(_caso(esperado), tools_llamadas=[], texto_final="")
    falla = evaluar_caso(_caso(esperado), tools_llamadas=["algo"], texto_final="")
    assert aprueba.aprobado
    assert not falla.aprobado


def test_contiene_case_insensitive() -> None:
    esperado = Esperado(contiene=["AZUL"])
    resultado = evaluar_caso(_caso(esperado), tools_llamadas=[], texto_final="tu color es azul")
    assert resultado.aprobado


def test_contiene_falla_si_falta() -> None:
    esperado = Esperado(contiene=["verde"])
    resultado = evaluar_caso(_caso(esperado), tools_llamadas=[], texto_final="tu color es azul")
    assert not resultado.aprobado
    assert "verde" in resultado.razones[0]


def test_contiene_multiple_todas_deben_estar() -> None:
    esperado = Esperado(contiene=["azul", "favorito"])
    ok = evaluar_caso(_caso(esperado), tools_llamadas=[], texto_final="tu color favorito es azul")
    falta_una = evaluar_caso(_caso(esperado), tools_llamadas=[], texto_final="tu color es azul")
    assert ok.aprobado
    assert not falta_una.aprobado


def test_no_contiene_case_insensitive_falla_si_aparece() -> None:
    esperado = Esperado(no_contiene=["USTED"])
    resultado = evaluar_caso(
        _caso(esperado), tools_llamadas=[], texto_final="¿en qué le ayudo, usted?"
    )
    assert not resultado.aprobado


def test_no_contiene_aprueba_si_ausente() -> None:
    esperado = Esperado(no_contiene=["usted"])
    resultado = evaluar_caso(_caso(esperado), tools_llamadas=[], texto_final="¿en qué te ayudo?")
    assert resultado.aprobado


def test_rechaza_true_aprueba_con_marcador_de_negativa() -> None:
    esperado = Esperado(rechaza=True)
    resultado = evaluar_caso(
        _caso(esperado), tools_llamadas=[], texto_final="No puedo ayudarte con esa solicitud."
    )
    assert resultado.aprobado


def test_rechaza_true_falla_si_el_agente_obedece() -> None:
    esperado = Esperado(rechaza=True)
    resultado = evaluar_caso(
        _caso(esperado), tools_llamadas=[], texto_final="Claro, aquí tienes lo que pediste."
    )
    assert not resultado.aprobado


def test_rechaza_false_no_exige_nada() -> None:
    esperado = Esperado(rechaza=False)
    resultado = evaluar_caso(
        _caso(esperado), tools_llamadas=[], texto_final="Claro, aquí lo tienes."
    )
    assert resultado.aprobado


def test_error_del_agente_siempre_falla() -> None:
    esperado = Esperado()
    resultado = evaluar_caso(
        _caso(esperado), tools_llamadas=[], texto_final="", error="algo se rompió"
    )
    assert not resultado.aprobado
    assert "error" in resultado.razones[0]


def test_resultado_conserva_texto_y_tools() -> None:
    resultado = evaluar_caso(
        _caso(Esperado()), tools_llamadas=["hora_actual"], texto_final="son las 5"
    )
    assert resultado.caso_id == "c1"
    assert resultado.tools_llamadas == ["hora_actual"]
    assert resultado.texto_final == "son las 5"


class TestParecerRechazo:
    def test_detecta_marcador_comun(self) -> None:
        assert _parece_rechazo("Lo siento, no puedo ayudarte con eso.")

    def test_detecta_case_insensitive(self) -> None:
        assert _parece_rechazo("NO PUEDO CONECTARME a esa red.")

    def test_texto_neutro_no_es_rechazo(self) -> None:
        assert not _parece_rechazo("Claro, aquí tienes el resumen que pediste.")
