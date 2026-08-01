"""Criterio de `edecan-test-output-safety`.

`edecan_llm.output_safety` no tiene módulo de pruebas propio: hoy solo se
ejercita de refilón desde `test_claude_cli.py`. Este criterio no se conforma
con que exista un archivo: comprueba que las pruebas nuevas MATAN mutantes —
si se rompe cada función pública por separado, la suite nueva tiene que
ponerse roja. Un archivo con `assert True` no pasa de aquí.

Falla hoy: `packages/llm/tests/test_output_safety.py` no existe.

El módulo original se restaura siempre (`finally`), incluso si pytest revienta.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[4]
_MODULO = _RAIZ / "packages/llm/edecan_llm/output_safety.py"
_PRUEBAS = _RAIZ / "packages/llm/tests/test_output_safety.py"

_MUTANTES: tuple[tuple[str, str], ...] = (
    (
        "sanitize_visible_assistant_text deja el texto intacto",
        "\n\ndef sanitize_visible_assistant_text(text: str) -> str:\n    return text.strip()\n",
    ),
    (
        "is_potential_internal_prefix nunca espera",
        "\n\ndef is_potential_internal_prefix(text: str) -> bool:\n    return False\n",
    ),
)


def _pytest() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(_PRUEBAS), "-q", "-p", "no:cacheprovider"],
        cwd=_RAIZ,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def main() -> int:
    if not _PRUEBAS.is_file():
        print(f"falta {_PRUEBAS.relative_to(_RAIZ)}")
        return 1

    limpio = _pytest()
    if limpio.returncode != 0:
        print("las pruebas nuevas no pasan sobre el módulo intacto:")
        print(limpio.stdout[-2000:] or limpio.stderr[-2000:])
        return 1

    original = _MODULO.read_text(encoding="utf-8")
    try:
        for descripcion, mutacion in _MUTANTES:
            _MODULO.write_text(original + mutacion, encoding="utf-8")
            mutado = _pytest()
            if mutado.returncode == 0:
                print(f"la suite nueva NO detecta el mutante: {descripcion}")
                return 1
    finally:
        _MODULO.write_text(original, encoding="utf-8")

    print("ok: las pruebas de output_safety pasan y matan los dos mutantes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
