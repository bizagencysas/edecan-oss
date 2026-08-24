"""Punto de entrada que PyInstaller congela como el binario `edecan-local`.

Deliberadamente un archivo de una sola responsabilidad: reexportar
`edecan_local.__main__:main` (WP-V3-05, `apps/local/edecan_local/__main__.py`,
contrato pinned en `ARCHITECTURE.md` §12.f) como el script "main" de
`Analysis()` en `edecan_local.spec`. PyInstaller necesita un archivo .py real
como entry point — no puede apuntar directo a `python -m edecan_local` como
hace `Makefile`/`scripts/dev.sh` en modo no empaquetado — así que este
archivo es el puente mínimo entre ambos mundos.

No agregues lógica acá (parseo de flags, setup de logging, etc.): eso vive
en `edecan_local.__main__` para que el comportamiento sea IDÉNTICO corriendo
como `python -m edecan_local` (dev) o como el binario `edecan-local`
congelado (producción, sidecar de Tauri) — este archivo nunca debe divergir
de ese contrato.
"""

from __future__ import annotations

from edecan_local.__main__ import main

if __name__ == "__main__":
    main()
