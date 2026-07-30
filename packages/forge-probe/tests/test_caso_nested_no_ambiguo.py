"""El caso `nested` no puede exigir adivinar puntuación.

Un caso de prueba imposible de acertar no mide al modelo: mide un defecto del
caso. Esto ocurrió de verdad — 14 fallos de `texto_alterado` que eran un punto
final que la frase del prompt añadía y el valor esperado no tenía.
"""

from __future__ import annotations

from edecan_forge_probe.probes.tools import _caso_nested


def test_cada_valor_esperado_aparece_delimitado_en_el_prompt():
    caso = _caso_nested(8)
    plan = caso.argumentos_esperados["plan"]
    assert f'"{plan["objetivo"]}"' in caso.prompt
    for paso in plan["pasos"]:
        assert f'"{paso["motivo"]}"' in caso.prompt


def test_ningun_valor_esperado_queda_pegado_a_puntuacion_de_la_frase():
    caso = _caso_nested(13)
    plan = caso.argumentos_esperados["plan"]
    # El valor seguido de punto sería la trampa: el modelo no puede saber si el
    # punto es del valor o de la frase.
    assert f"{plan['objetivo']}." not in caso.prompt


def test_el_booleano_se_pide_sin_ambiguedad():
    par, impar = _caso_nested(8), _caso_nested(13)
    assert par.argumentos_esperados["plan"]["reversible"] is True
    assert impar.argumentos_esperados["plan"]["reversible"] is False
    assert "¿Es reversible?: sí" in par.prompt
    assert "¿Es reversible?: no" in impar.prompt
