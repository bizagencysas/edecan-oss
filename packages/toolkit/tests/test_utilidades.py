"""Tests de `edecan_toolkit.utilidades`: `hora_actual` y `calculadora`."""

from __future__ import annotations

import pytest
from edecan_toolkit.utilidades import (
    CalculadoraTool,
    ExpresionInsegura,
    HoraActualTool,
    evaluar_expresion,
)


def test_evaluar_expresion_operaciones_basicas():
    assert evaluar_expresion("2 + 3 * 4") == 14
    assert evaluar_expresion("(2 + 3) * 4") == 20
    assert evaluar_expresion("2 ** 10") == 1024
    assert evaluar_expresion("-5 + 2") == -3
    assert evaluar_expresion("10 / 4") == 2.5
    assert evaluar_expresion("10 // 3") == 3
    assert evaluar_expresion("10 % 3") == 1


def test_evaluar_expresion_rechaza_dunder_import():
    with pytest.raises(ExpresionInsegura):
        evaluar_expresion("__import__('os').system('ls')")


def test_evaluar_expresion_rechaza_nombres_llamadas_y_atributos():
    with pytest.raises(ExpresionInsegura):
        evaluar_expresion("open('/etc/passwd')")
    with pytest.raises(ExpresionInsegura):
        evaluar_expresion("x + 1")
    with pytest.raises(ExpresionInsegura):
        evaluar_expresion("(1).__class__")
    with pytest.raises(ExpresionInsegura):
        evaluar_expresion("[1, 2, 3][0]")


def test_evaluar_expresion_rechaza_sintaxis_invalida():
    with pytest.raises(ExpresionInsegura):
        evaluar_expresion("2 +")


def test_evaluar_expresion_division_entre_cero_propaga_zerodivisionerror():
    with pytest.raises(ZeroDivisionError):
        evaluar_expresion("1 / 0")


async def test_calculadora_tool_rechaza_dunder_import_sin_crashear(make_ctx):
    resultado = await CalculadoraTool().run(make_ctx(), {"expresion": "__import__('os')"})
    assert "no está permitido" in resultado.content
    assert resultado.data is None


async def test_calculadora_tool_calcula_correctamente(make_ctx):
    resultado = await CalculadoraTool().run(make_ctx(), {"expresion": "10 / 4"})
    assert resultado.data["resultado"] == 2.5
    assert "2.5" in resultado.content


async def test_calculadora_tool_division_entre_cero_no_crashea(make_ctx):
    resultado = await CalculadoraTool().run(make_ctx(), {"expresion": "1 / 0"})
    assert "dividir entre cero" in resultado.content.lower()


async def test_calculadora_tool_expresion_vacia(make_ctx):
    resultado = await CalculadoraTool().run(make_ctx(), {"expresion": "   "})
    assert "expresión" in resultado.content.lower()


async def test_hora_actual_zona_valida(make_ctx):
    resultado = await HoraActualTool().run(make_ctx(), {"timezone": "America/Mexico_City"})
    assert resultado.data["timezone"] == "America/Mexico_City"
    assert "T" in resultado.data["iso"]


async def test_hora_actual_zona_invalida_no_crashea(make_ctx):
    resultado = await HoraActualTool().run(make_ctx(), {"timezone": "No/Existe"})
    assert "no reconozco" in resultado.content.lower()


async def test_hora_actual_default_utc(make_ctx):
    resultado = await HoraActualTool().run(make_ctx(), {})
    assert resultado.data["timezone"] == "UTC"
