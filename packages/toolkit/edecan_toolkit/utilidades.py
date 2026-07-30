"""Utilidades varias: hora actual (zoneinfo) y calculadora aritmética segura (ast)."""

from __future__ import annotations

import ast
import operator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from edecan_core import Tool, ToolContext, ToolResult

_DIAS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MESES_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _formatear_es(momento: datetime) -> str:
    """Formatea una fecha-hora en español sin depender de `locale` (poco
    fiable entre sistemas/contenedores) — usa tablas de nombres propias."""
    dia = _DIAS_ES[momento.weekday()]
    mes = _MESES_ES[momento.month - 1]
    hora = momento.strftime("%H:%M")
    return f"{dia} {momento.day} de {mes} de {momento.year}, {hora} ({momento.tzinfo})"


class HoraActualTool(Tool):
    name = "hora_actual"
    description = "Devuelve la fecha y hora actuales en la zona horaria indicada."
    input_schema = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "Zona horaria IANA (ej. 'America/Mexico_City'). Por defecto: UTC.",
                "default": "UTC",
            },
        },
        "required": [],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        tz_nombre = str(args.get("timezone") or "UTC")
        try:
            zona = ZoneInfo(tz_nombre)
        except (ZoneInfoNotFoundError, ValueError):
            return ToolResult(content=f"No reconozco la zona horaria '{tz_nombre}'.")

        ahora = datetime.now(zona)
        return ToolResult(
            content=f"Son las {_formatear_es(ahora)}.",
            data={"iso": ahora.isoformat(), "timezone": tz_nombre},
        )


# --- calculadora: evaluación aritmética segura vía ast ---------------------

_OPERADORES_BINARIOS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_OPERADORES_UNARIOS: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class ExpresionInsegura(ValueError):
    """La expresión contiene algo que no es aritmética pura: nombres, llamadas,
    atributos, subíndices, comprensiones, imports (`__import__` incluido), etc.
    """


def evaluar_expresion(expresion: str) -> float:
    """Evalúa una expresión aritmética de forma segura, SIN usar `eval`/`exec`.

    Solo admite números y los operadores `+ - * / // % **` con paréntesis:
    se recorre el AST a mano (`ast.parse(..., mode="eval")`) y cualquier nodo
    que no sea explícitamente aritmético (nombres, llamadas — incluida
    `__import__(...)` —, atributos, subíndices, comparaciones, ...) hace que
    se rechace la expresión completa con `ExpresionInsegura`.
    """
    try:
        arbol = ast.parse(expresion, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise ExpresionInsegura(f"'{expresion}' no es una expresión válida.") from exc
    return _evaluar_nodo(arbol.body)


def _evaluar_nodo(nodo: ast.AST) -> float:
    if isinstance(nodo, ast.Constant):
        if isinstance(nodo.value, bool) or not isinstance(nodo.value, int | float):
            raise ExpresionInsegura("Solo se permiten números.")
        return nodo.value
    if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPERADORES_BINARIOS:
        izquierda = _evaluar_nodo(nodo.left)
        derecha = _evaluar_nodo(nodo.right)
        return _OPERADORES_BINARIOS[type(nodo.op)](izquierda, derecha)
    if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _OPERADORES_UNARIOS:
        return _OPERADORES_UNARIOS[type(nodo.op)](_evaluar_nodo(nodo.operand))
    raise ExpresionInsegura(
        f"'{type(nodo).__name__}' no está permitido en la calculadora "
        "(solo números y operadores aritméticos)."
    )


class CalculadoraTool(Tool):
    name = "calculadora"
    description = "Evalúa una expresión aritmética: +, -, *, /, //, %, ** y paréntesis."
    input_schema = {
        "type": "object",
        "properties": {
            "expresion": {
                "type": "string",
                "description": "Expresión a evaluar, ej. '(23 + 4) * 2 / 3'.",
            },
        },
        "required": ["expresion"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        expresion = str(args.get("expresion", ""))
        if not expresion.strip():
            return ToolResult(content="Dime qué expresión quieres que calcule.")
        try:
            resultado = evaluar_expresion(expresion)
        except ExpresionInsegura as exc:
            return ToolResult(content=str(exc))
        except ZeroDivisionError:
            return ToolResult(content="No se puede dividir entre cero.")
        return ToolResult(content=f"{expresion} = {resultado}", data={"resultado": resultado})
