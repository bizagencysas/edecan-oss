"""Modelo de precio de pago único (2026-07-09): las 4 entradas de `PLANES`
se mantienen (compatibilidad con `plan_key` ya sembrados/usados como
fixture en tests de otros paquetes), pero ya NINGUNA gatea nada — las 4
conceden absolutamente todos los flags/límites. La única diferencia real
entre tiers de compra vive en `tenants.lifetime_updates_purchased_at`
(migración 0010), no en este archivo. Ver docstring de `edecan_schemas.
plans` para el detalle completo."""

from __future__ import annotations

from edecan_schemas.plans import BOOL_FLAGS, INT_LIMITS, PLANES, UNLIMITED


def test_hay_exactamente_4_planes():
    assert set(PLANES) == {"free_selfhost", "hosted_basic", "hosted_pro", "hosted_business"}


def test_todos_los_flags_booleanos_en_true_en_los_4_planes():
    for plan_key, plan in PLANES.items():
        for flag in BOOL_FLAGS:
            assert plan.flags[flag] is True, (plan_key, flag)


def test_todos_los_limites_ilimitados_en_los_4_planes():
    for plan_key, plan in PLANES.items():
        for limit in INT_LIMITS:
            assert plan.flags[limit] == UNLIMITED, (plan_key, limit)


def test_los_4_planes_tienen_flags_identicos():
    contenidos = {plan_key: plan.flags for plan_key, plan in PLANES.items()}
    primero = next(iter(contenidos.values()))
    for plan_key, flags in contenidos.items():
        assert flags == primero, plan_key


def test_plan_def_es_inmutable():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PLANES["free_selfhost"].precio_usd_mes = 999
