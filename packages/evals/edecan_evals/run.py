"""Punto de entrada de `python -m edecan_evals.run --suite <nombre> [--live]`.

Toda la lógica real vive en `edecan_evals.runner` (importable sin
`edecan_core`, ver su docstring); este módulo es deliberadamente un envoltorio
delgado para que `python -m edecan_evals.run` funcione tal como lo pinned
WP-15, sin ejecutar el `argparse`/`sys.exit` del CLI con un simple
`import edecan_evals.runner`.
"""

from __future__ import annotations

import sys

from edecan_evals.runner import main

if __name__ == "__main__":
    sys.exit(main())
