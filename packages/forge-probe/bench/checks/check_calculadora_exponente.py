"""Criterio de `edecan-calculadora-exponente-acotado`.

`evaluar_expresion` se vende como "aritmética segura", pero `**` no tiene
tope: `9**9**9` deja el proceso quemando CPU y memoria hasta que el sistema
lo mata. Como la calculadora la invoca el modelo con texto del usuario, es
una denegación de servicio a un turno de distancia.

Cada caso corre en un subproceso con tope de tiempo: hoy el primero NO
termina y el criterio falla por timeout.
"""

from __future__ import annotations

import subprocess
import sys

_TIEMPO_MAXIMO_S = 8

_RECHAZAR = ("9**9**9", "2**100000000", "(2**64)**(2**64)")
_ACEPTAR = (("2**10", 1024.0), ("(23 + 4) * 2 / 3", 18.0), ("2**64", float(2**64)))

_PROGRAMA_RECHAZO = (
    "import sys\n"
    "from edecan_toolkit.utilidades import ExpresionInsegura, evaluar_expresion\n"
    "try:\n"
    "    valor = evaluar_expresion(sys.argv[1])\n"
    "except ExpresionInsegura:\n"
    "    sys.exit(0)\n"
    "except (OverflowError, MemoryError, ValueError) as exc:\n"
    "    print('lanzó ' + type(exc).__name__ + ' en vez de ExpresionInsegura')\n"
    "    sys.exit(2)\n"
    "print('devolvió un resultado en vez de rechazar la expresión')\n"
    "sys.exit(2)\n"
)

_PROGRAMA_ACEPTACION = (
    "import sys\n"
    "from edecan_toolkit.utilidades import evaluar_expresion\n"
    "print(repr(float(evaluar_expresion(sys.argv[1]))))\n"
)


def _correr(programa: str, expresion: str) -> tuple[int, str]:
    try:
        proceso = subprocess.run(
            [sys.executable, "-c", programa, expresion],
            capture_output=True,
            text=True,
            timeout=_TIEMPO_MAXIMO_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"no terminó en {_TIEMPO_MAXIMO_S}s"
    return proceso.returncode, (proceso.stdout + proceso.stderr).strip()


def main() -> int:
    for expresion in _RECHAZAR:
        codigo, salida = _correr(_PROGRAMA_RECHAZO, expresion)
        if codigo != 0:
            print(f"{expresion!r}: {salida}")
            return 1

    for expresion, esperado in _ACEPTAR:
        codigo, salida = _correr(_PROGRAMA_ACEPTACION, expresion)
        if codigo != 0:
            print(f"{expresion!r} dejó de funcionar: {salida}")
            return 1
        if float(salida) != esperado:
            print(f"{expresion!r} devolvió {salida}, esperado {esperado}")
            return 1

    print("ok: los exponentes desmedidos se rechazan y la aritmética normal sigue igual")
    return 0


if __name__ == "__main__":
    sys.exit(main())
