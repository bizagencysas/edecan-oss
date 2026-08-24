"""Permite ejecutar el companion con `python -m edecan_companion --server ... --code ...`."""

from __future__ import annotations

import sys

from edecan_companion.main import main

if __name__ == "__main__":
    sys.exit(main())
