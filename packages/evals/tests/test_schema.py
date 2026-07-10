"""Tests de `edecan_evals.schema` — sin dependencias de paquetes hermanos."""

from __future__ import annotations

import pytest
from edecan_evals.schema import Caso, Esperado, GuionEntry, Suite
from pydantic import ValidationError


def _caso(**overrides: object) -> dict:
    base = {"id": "c1", "mensajes": ["hola"], "esperado": {}}
    base.update(overrides)
    return base


def test_esperado_defaults() -> None:
    esperado = Esperado()
    assert esperado.tools_llamadas is None
    assert esperado.contiene is None
    assert esperado.no_contiene is None
    assert esperado.rechaza is False


def test_caso_persona_default_vacia() -> None:
    caso = Caso.model_validate(_caso())
    assert caso.persona == {}
    assert caso.mensajes == ["hola"]


def test_caso_mensajes_vacios_falla() -> None:
    with pytest.raises(ValidationError):
        Caso.model_validate(_caso(mensajes=[]))


def test_caso_multi_turno() -> None:
    caso = Caso.model_validate(_caso(mensajes=["turno 1", "turno 2"]))
    assert caso.mensajes == ["turno 1", "turno 2"]


def test_suite_requiere_al_menos_un_caso() -> None:
    with pytest.raises(ValidationError):
        Suite.model_validate({"nombre": "vacia", "casos": []})


def test_suite_guion_default_vacio() -> None:
    suite = Suite.model_validate({"nombre": "s", "casos": [_caso()]})
    assert suite.guion == {}
    assert suite.descripcion == ""


def test_suite_carga_guion() -> None:
    suite = Suite.model_validate(
        {
            "nombre": "s",
            "casos": [_caso()],
            "guion": {"hola": {"texto": "hola de vuelta"}},
        }
    )
    assert suite.guion["hola"].texto == "hola de vuelta"


class TestGuionEntry:
    def test_solo_texto_es_valido(self) -> None:
        entrada = GuionEntry(texto="hola")
        assert entrada.texto == "hola"
        assert entrada.tool is None

    def test_solo_tool_es_valido(self) -> None:
        entrada = GuionEntry(tool="hora_actual", args={"a": 1})
        assert entrada.tool == "hora_actual"
        assert entrada.args == {"a": 1}

    def test_ninguno_falla(self) -> None:
        with pytest.raises(ValidationError):
            GuionEntry()

    def test_ambos_falla(self) -> None:
        with pytest.raises(ValidationError):
            GuionEntry(texto="hola", tool="hora_actual")

    def test_args_default_vacio(self) -> None:
        entrada = GuionEntry(tool="hora_actual")
        assert entrada.args == {}
