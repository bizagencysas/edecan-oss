"""Utilidades internas compartidas entre las herramientas de `edecan_toolkit`.

No forma parte del contrato público del paquete (por eso el prefijo `_`).
"""

from __future__ import annotations

from typing import Any


def clamp_int(valor: Any, *, default: int, minimo: int, maximo: int) -> int:
    """Convierte `valor` a `int` (usando `default` si falta o no es convertible)
    y lo acota al rango cerrado [`minimo`, `maximo`].

    Se usa en todos los argumentos tipo "límite de resultados" de las tools
    para que un valor fuera de rango o inválido enviado por el modelo nunca
    provoque una consulta sin límite ni un error — simplemente se acota.
    """
    try:
        n = int(valor) if valor is not None else default
    except (TypeError, ValueError):
        n = default
    return max(minimo, min(maximo, n))
