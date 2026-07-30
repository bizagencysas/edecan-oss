"""Criterio de `edecan-prompted-tools-arguments-texto`.

`parse_tool_call` solo acepta `arguments` cuando ya es un objeto JSON. Los
modelos —igual que la API de OpenAI, donde `function.arguments` SIEMPRE viaja
como string— escriben con frecuencia `"arguments": "{\\"path\\": \\"a.py\\"}"`.
Hoy ese caso no falla: devuelve la herramienta con `arguments={}`, así que la
llamada se ejecuta SIN sus argumentos. Es la peor forma de fallar.

Falla hoy en el primer caso.
"""

from __future__ import annotations

import json
import sys

from edecan_llm.prompted_tools import parse_tool_call

_CODIGO = 'def suma(a, b):\n    """Suma {a} y {b}."""\n    return a + b\n'

_CASOS: list[tuple[str, str, str, dict]] = [
    (
        "arguments como texto JSON",
        json.dumps({"tool_call": {"name": "leer", "arguments": '{"path": "a.py"}'}}),
        "leer",
        {"path": "a.py"},
    ),
    (
        "arguments como texto JSON con un bloque de código dentro",
        json.dumps({"tool_call": {"name": "escribir", "arguments": json.dumps({"code": _CODIGO})}}),
        "escribir",
        {"code": _CODIGO},
    ),
    (
        "arguments como texto vacío de objeto",
        json.dumps({"tool_call": {"name": "hora_actual", "arguments": "{}"}}),
        "hora_actual",
        {},
    ),
    (
        "arguments como objeto (comportamiento actual, no debe romperse)",
        json.dumps({"tool_call": {"name": "leer", "arguments": {"path": "a.py"}}}),
        "leer",
        {"path": "a.py"},
    ),
    (
        "arguments como texto no parseable cae a vacío, sin lanzar",
        json.dumps({"tool_call": {"name": "leer", "arguments": "path=a.py"}}),
        "leer",
        {},
    ),
    (
        "arguments ausente cae a vacío",
        json.dumps({"tool_call": {"name": "leer"}}),
        "leer",
        {},
    ),
]


def main() -> int:
    for etiqueta, texto, nombre, esperado in _CASOS:
        llamada = parse_tool_call(f"Claro, uso una herramienta:\n```json\n{texto}\n```\n")
        if llamada is None:
            print(f"{etiqueta}: parse_tool_call devolvió None")
            return 1
        if llamada.name != nombre:
            print(f"{etiqueta}: nombre {llamada.name!r}, esperado {nombre!r}")
            return 1
        if llamada.arguments != esperado:
            print(f"{etiqueta}: arguments {llamada.arguments!r}, esperado {esperado!r}")
            return 1

    if parse_tool_call("Hola, no necesito herramientas.") is not None:
        print("texto normal se interpretó como una llamada a herramienta")
        return 1

    print("ok: `arguments` en texto JSON se parsea sin perder argumentos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
