"""Criterio de `edecan-toolkit-all-ordenado`.

`edecan_toolkit.__all__` debe estar ordenado y ser coherente con lo que el
módulo exporta de verdad. Falla hoy: la lista tiene `DuckDuckGoSearch` antes
de `BuscarContactosTool` y `CrearArtefactosTool` después de `CrearEventoTool`.
"""

from __future__ import annotations

import sys

import edecan_toolkit


def main() -> int:
    actual = list(edecan_toolkit.__all__)
    esperado = sorted(actual)
    if actual != esperado:
        primeros = [(a, b) for a, b in zip(actual, esperado, strict=True) if a != b][:3]
        print(f"__all__ no está ordenado; primeras diferencias (actual, esperado): {primeros}")
        return 1

    faltan = [nombre for nombre in actual if not hasattr(edecan_toolkit, nombre)]
    if faltan:
        print(f"__all__ nombra símbolos que el módulo no expone: {faltan}")
        return 1

    if len(set(actual)) != len(actual):
        print("__all__ tiene nombres repetidos")
        return 1

    print(f"ok: __all__ ordenado y consistente ({len(actual)} símbolos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
