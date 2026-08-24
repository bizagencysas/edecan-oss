"""Tests de `edecan_gym.plan`: serialización y generación de planes con LLM falso."""

from __future__ import annotations

import json

import pytest
from edecan_gym import Ejercicio, WorkoutPlan, generar_plan, prompt_collage


def _plan_dict(**overrides) -> dict:
    base = {
        "titulo": "Fuerza superior",
        "objetivo": "hipertrofia",
        "duracion_min": 45,
        "ejercicios": [
            {
                "nombre": "Press banca",
                "musculo": "pecho",
                "series": 3,
                "repeticiones": "8-12",
                "descanso_seg": 90,
                "notas": "controla la bajada",
            },
            {
                "nombre": "Remo",
                "musculo": "espalda",
                "series": 4,
                "repeticiones": "10",
                "descanso_seg": 60,
                "notas": "",
            },
        ],
    }
    base.update(overrides)
    return base


def test_ejercicio_roundtrip():
    original = Ejercicio("Sentadilla", "pierna", 3, "8-12", 120, "baja controlada")
    assert Ejercicio.from_dict(original.to_dict()) == original


def test_workout_plan_roundtrip_sin_imagen():
    plan = WorkoutPlan.from_dict(_plan_dict())
    assert plan.titulo == "Fuerza superior"
    assert len(plan.ejercicios) == 2
    assert plan.imagen_url is None
    assert WorkoutPlan.from_dict(plan.to_dict()) == plan


def test_workout_plan_roundtrip_con_imagen():
    plan = WorkoutPlan.from_dict(_plan_dict(imagen_url="https://example.com/c.png"))
    assert plan.imagen_url == "https://example.com/c.png"
    assert WorkoutPlan.from_dict(plan.to_dict()) == plan


def test_workout_plan_roundtrip_con_imagen_file_id():
    plan = WorkoutPlan.from_dict(_plan_dict(imagen_file_id="file-123"))
    assert plan.imagen_file_id == "file-123"
    assert WorkoutPlan.from_dict(plan.to_dict()) == plan


def _fake_completar(respuestas):
    llamadas = []

    async def completar(system, user):
        llamadas.append((system, user))
        return respuestas[min(len(llamadas) - 1, len(respuestas) - 1)]

    return completar, llamadas


async def test_generar_plan_feliz():
    completar, llamadas = _fake_completar([json.dumps(_plan_dict())])
    plan = await generar_plan(completar)
    assert plan.titulo == "Fuerza superior"
    assert len(plan.ejercicios) == 2
    assert len(llamadas) == 1
    sistema, usuario = llamadas[0]
    assert "instructor" in sistema
    assert "calienta" in sistema
    assert "hipertrofia" in usuario


async def test_generar_plan_conserva_url_de_imagen():
    completar, _ = _fake_completar([json.dumps(_plan_dict(imagen_url="https://x/y.png"))])
    plan = await generar_plan(completar)
    assert plan.imagen_url == "https://x/y.png"


async def test_generar_plan_acepta_json_envuelto_en_markdown():
    payload = f"```json\n{json.dumps(_plan_dict())}\n```"
    completar, _ = _fake_completar([payload])
    plan = await generar_plan(completar)
    assert plan.titulo == "Fuerza superior"


async def test_generar_plan_recupera_despues_de_json_invalido():
    completar, llamadas = _fake_completar(["esto no es JSON", json.dumps(_plan_dict())])
    plan = await generar_plan(completar)
    assert plan.titulo == "Fuerza superior"
    assert len(llamadas) == 2
    assert "rechazada" in llamadas[1][1]


async def test_generar_plan_recupera_despues_de_validacion_invalida():
    malo = _plan_dict()
    malo["ejercicios"][0]["series"] = 0
    completar, llamadas = _fake_completar([json.dumps(malo), json.dumps(_plan_dict())])
    plan = await generar_plan(completar)
    assert plan.ejercicios[0].series == 3
    assert len(llamadas) == 2
    assert "series" in llamadas[1][1]


async def test_generar_plan_agota_reintentos_y_lanza_valueerror():
    completar, llamadas = _fake_completar(["no JSON", "aún no JSON"])
    with pytest.raises(ValueError):
        await generar_plan(completar, reintentos=1)
    assert len(llamadas) == 2


async def test_generar_plan_rechaza_mas_de_diez_ejercicios():
    malo = _plan_dict(ejercicios=[_plan_dict()["ejercicios"][0] for _ in range(11)])
    completar, _ = _fake_completar([json.dumps(malo), json.dumps(_plan_dict())])
    plan = await generar_plan(completar)
    assert len(plan.ejercicios) == 2


async def test_generar_plan_un_solo_ejercicio():
    unico = _plan_dict(ejercicios=[_plan_dict()["ejercicios"][0]])
    completar, _ = _fake_completar([json.dumps(unico)])
    plan = await generar_plan(completar)
    assert len(plan.ejercicios) == 1


def test_prompt_collage_incluye_ejercicios_y_grid():
    plan = WorkoutPlan.from_dict(_plan_dict())
    prompt = prompt_collage(plan)
    assert "collage" in prompt
    assert "Press banca" in prompt
    assert "3 series de 8-12 repeticiones" in prompt
    assert "Remo" in prompt


def test_prompt_collage_plan_sin_ejercicios():
    plan = WorkoutPlan("Vacío", "prueba", 10, [])
    prompt = prompt_collage(plan)
    assert "Vacío" in prompt
    assert isinstance(prompt, str)