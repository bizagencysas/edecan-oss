"""Tests de `edecan_gym.session`: máquina de estados y serialización."""

from __future__ import annotations

import pytest
from edecan_gym import ESTADOS, Ejercicio, WorkoutPlan, WorkoutSession


def _plan() -> WorkoutPlan:
    return WorkoutPlan(
        "Fuerza",
        "hipertrofia",
        30,
        [
            Ejercicio("Press banca", "pecho", 3, "8-12", 90),
            Ejercicio("Sentadilla", "pierna", 4, "10", 120),
        ],
    )


def test_estado_inicial_es_planned():
    assert WorkoutSession(_plan()).estado == "planned"


def test_iniciar_desde_planned():
    s = WorkoutSession(_plan())
    s.iniciar(now="2026-01-01T00:00:00+00:00")
    assert s.estado == "active"
    assert s.started_at == "2026-01-01T00:00:00+00:00"


def test_iniciar_no_sobrescribe_started_at():
    s = WorkoutSession(_plan(), started_at="2025-01-01T00:00:00+00:00")
    s.iniciar(now="2026-01-01T00:00:00+00:00")
    assert s.started_at == "2025-01-01T00:00:00+00:00"


def test_iniciar_desde_completed_lanza():
    with pytest.raises(ValueError):
        WorkoutSession(_plan(), estado="completed").iniciar()


def test_pausar_y_reanudar():
    s = WorkoutSession(_plan())
    s.iniciar()
    s.pausar()
    assert s.estado == "paused"
    s.reanudar()
    assert s.estado == "active"


def test_pausar_desde_planned_lanza():
    with pytest.raises(ValueError):
        WorkoutSession(_plan()).pausar()


def test_reanudar_desde_active_lanza():
    with pytest.raises(ValueError):
        WorkoutSession(_plan(), estado="active").reanudar()


def test_registrar_serie_en_active():
    s = WorkoutSession(_plan())
    s.iniciar()
    s.registrar_serie(0, 12, peso_kg=60.0, now="2026-01-01T00:00:00+00:00")
    assert len(s.series) == 1
    assert s.series[0].peso_kg == 60.0
    assert s.series[0].en == "2026-01-01T00:00:00+00:00"


def test_registrar_serie_en_paused():
    s = WorkoutSession(_plan())
    s.iniciar()
    s.pausar()
    s.registrar_serie(1, 10)
    assert len(s.series) == 1


def test_registrar_serie_en_planned_lanza():
    with pytest.raises(ValueError):
        WorkoutSession(_plan()).registrar_serie(0, 10)


def test_registrar_serie_fuera_de_rango():
    s = WorkoutSession(_plan(), estado="active")
    with pytest.raises(ValueError):
        s.registrar_serie(2, 10)
    with pytest.raises(ValueError):
        s.registrar_serie(-1, 10)


def test_registrar_serie_repeticiones_cero():
    with pytest.raises(ValueError):
        WorkoutSession(_plan(), estado="active").registrar_serie(0, 0)


def test_terminar_desde_active():
    s = WorkoutSession(_plan(), estado="active")
    s.terminar()
    assert s.estado == "completed"


def test_terminar_desde_planned_lanza():
    with pytest.raises(ValueError):
        WorkoutSession(_plan()).terminar()


def test_cancelar_desde_cualquier_estado():
    for estado in ESTADOS:
        s = WorkoutSession(_plan(), estado=estado)
        s.cancelar()
        assert s.estado == "cancelled"


def test_series_completadas_por_ejercicio():
    s = WorkoutSession(_plan(), estado="active")
    s.registrar_serie(0, 12)
    s.registrar_serie(0, 10)
    s.registrar_serie(1, 10)
    assert s.series_completadas(0) == 2
    assert s.series_completadas(1) == 1


def test_resumen_tiene_estructura_completa():
    s = WorkoutSession(_plan(), estado="active")
    s.registrar_serie(0, 12, peso_kg=60.0)
    resumen = s.resumen()
    assert resumen["estado"] == "active"
    assert resumen["titulo"] == "Fuerza"
    assert resumen["series_total"] == 7
    assert resumen["series_hechas"] == 1
    assert resumen["progreso"] == [
        {"idx": 0, "series_hechas": 1, "series_total": 3},
        {"idx": 1, "series_hechas": 0, "series_total": 4},
    ]
    assert resumen["series"][0]["ejercicio_idx"] == 0


def test_to_dict_y_from_dict_roundtrip():
    s = WorkoutSession(_plan())
    s.iniciar(now="2026-01-01T00:00:00+00:00")
    s.registrar_serie(0, 12, peso_kg=60.0, now="2026-01-01T00:05:00+00:00")
    d = s.to_dict()
    assert d["plan"]["titulo"] == "Fuerza"
    reconstruida = WorkoutSession.from_dict(d, _plan())
    assert reconstruida.estado == s.estado
    assert reconstruida.started_at == s.started_at
    assert reconstruida.series == s.series
    assert reconstruida.resumen() == s.resumen()


def test_estado_invalido_en_constructor_lanza():
    with pytest.raises(ValueError):
        WorkoutSession(_plan(), estado="volando")