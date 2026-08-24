"""Tests de `edecan_gym.checkin.decidir`."""

from __future__ import annotations

import pytest
from edecan_gym import decidir


@pytest.mark.parametrize("respuesta", ["si", "sí", "yes", " Si ", "YES", "SÍ"])
def test_decidir_afirmativas(respuesta):
    assert decidir(respuesta) is True


@pytest.mark.parametrize("respuesta", ["no", "nope", " NO ", "Nope"])
def test_decidir_negativas(respuesta):
    assert decidir(respuesta) is False


@pytest.mark.parametrize("respuesta", ["quizás", "", "tal vez", "1"])
def test_decidir_invalida_lanza(respuesta):
    with pytest.raises(ValueError):
        decidir(respuesta)