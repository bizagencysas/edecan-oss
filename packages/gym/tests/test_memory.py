"""Tests de `edecan_gym.memory.contexto_para_plan`."""

from __future__ import annotations

from edecan_gym import contexto_para_plan


def test_contexto_vacio():
    assert contexto_para_plan([]) == "Sin historial de sesiones previas."


def test_contexto_limita_a_los_ultimos():
    historial = [{"titulo": f"s{i}", "series": []} for i in range(10)]
    texto = contexto_para_plan(historial, limite=3)
    assert "s7" in texto and "s8" in texto and "s9" in texto
    assert "s0" not in texto


def test_contexto_incluye_nombres_series_y_pesos():
    historial = [
        {
            "titulo": "Empuje",
            "plan": {"ejercicios": [{"nombre": "Press banca"}, {"nombre": "Press militar"}]},
            "series": [
                {"ejercicio_idx": 0, "repeticiones": 12, "peso_kg": 60.0},
                {"ejercicio_idx": 0, "repeticiones": 10, "peso_kg": 65.0},
                {"ejercicio_idx": 1, "repeticiones": 8, "peso_kg": None},
            ],
        }
    ]
    texto = contexto_para_plan(historial)
    assert "Press banca" in texto
    assert "2 series" in texto
    assert "65.0 kg" in texto
    assert "Press militar" in texto


def test_contexto_sin_nombres_usa_indice():
    historial = [
        {
            "titulo": "Día de pierna",
            "series": [{"ejercicio_idx": 0, "repeticiones": 10, "peso_kg": 80.0}],
        }
    ]
    texto = contexto_para_plan(historial)
    assert "ejercicio 0" in texto
    assert "Día de pierna" in texto


def test_contexto_sesion_sin_series():
    historial = [{"titulo": "Solo plan", "series": []}]
    texto = contexto_para_plan(historial)
    assert "Solo plan" in texto
    assert "sin series registradas" in texto