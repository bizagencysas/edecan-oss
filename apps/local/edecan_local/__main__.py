"""`python -m edecan_local` — punto de entrada (`ARCHITECTURE.md` §12f).

Deliberadamente trivial: toda la orquestación real vive en
`edecan_local.runtime` (parseo de flags, arranque, apagado limpio) — este
archivo solo es el gancho que Python busca al invocar el paquete como script
(`python -m <paquete>` ejecuta `<paquete>/__main__.py`).
"""

from __future__ import annotations

from edecan_local.runtime import main

if __name__ == "__main__":
    main()
