from __future__ import annotations

import pytest
from edecan_forge_kernel.contracts import (
    EffectClass,
    RetryPolicy,
    effect_class_max,
    retry_policy_for,
)


def test_orden_total_ascendente() -> None:
    orden = [
        EffectClass.SAFE,
        EffectClass.REVERSIBLE,
        EffectClass.DESTRUCTIVE,
        EffectClass.IRREVERSIBLE,
        EffectClass.EXTERNAL_IDEMPOTENT,
        EffectClass.EXTERNAL_EFFECT,
    ]
    for i in range(len(orden) - 1):
        assert effect_class_max(orden[i], orden[i + 1]) == orden[i + 1]


@pytest.mark.parametrize(
    ("valores", "esperado"),
    [
        ((EffectClass.SAFE,), EffectClass.SAFE),
        ((EffectClass.SAFE, EffectClass.REVERSIBLE), EffectClass.REVERSIBLE),
        ((EffectClass.EXTERNAL_EFFECT, EffectClass.SAFE), EffectClass.EXTERNAL_EFFECT),
        (
            (EffectClass.IRREVERSIBLE, EffectClass.EXTERNAL_IDEMPOTENT),
            EffectClass.EXTERNAL_IDEMPOTENT,
        ),
        (
            (EffectClass.DESTRUCTIVE, EffectClass.SAFE, EffectClass.REVERSIBLE),
            EffectClass.DESTRUCTIVE,
        ),
    ],
)
def test_effect_class_max_compone_con_el_peor_caso(
    valores: tuple[EffectClass, ...], esperado: EffectClass
) -> None:
    assert effect_class_max(*valores) == esperado


def test_effect_class_max_es_conmutativo_y_asociativo() -> None:
    a, b, c = EffectClass.SAFE, EffectClass.EXTERNAL_IDEMPOTENT, EffectClass.DESTRUCTIVE
    assert effect_class_max(a, b, c) == effect_class_max(c, b, a)
    assert effect_class_max(effect_class_max(a, b), c) == effect_class_max(
        a, effect_class_max(b, c)
    )


def test_effect_class_max_sin_argumentos_falla() -> None:
    with pytest.raises(ValueError, match="al menos un valor"):
        effect_class_max()


def test_declaracion_nunca_puede_bajar_la_clase_compuesta() -> None:
    """§5.6: 'la declaración puede subir la clase real, nunca bajarla'. Con `max()`, declarar
    algo más bajo que lo derivado nunca gana — es la propiedad que hace la regla imposible de
    violar por construcción, no por vigilancia."""
    declarada = EffectClass.SAFE
    derivada_de_capacidades = EffectClass.IRREVERSIBLE
    assert effect_class_max(declarada, derivada_de_capacidades) == EffectClass.IRREVERSIBLE


@pytest.mark.parametrize(
    ("clase", "esperada"),
    [
        (EffectClass.SAFE, RetryPolicy.FREE),
        (EffectClass.REVERSIBLE, RetryPolicy.FREE),
        (EffectClass.DESTRUCTIVE, RetryPolicy.TWO_PHASE_NO_RETRY),
        (EffectClass.IRREVERSIBLE, RetryPolicy.TWO_PHASE_NO_RETRY),
        (EffectClass.EXTERNAL_IDEMPOTENT, RetryPolicy.KEYED),
        (EffectClass.EXTERNAL_EFFECT, RetryPolicy.TWO_PHASE_NO_RETRY),
    ],
)
def test_retry_policy_por_clase(clase: EffectClass, esperada: RetryPolicy) -> None:
    assert retry_policy_for(clase) == esperada
