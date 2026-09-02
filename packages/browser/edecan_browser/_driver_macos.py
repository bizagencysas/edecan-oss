"""Re-firma del driver `node` de Playwright en la app congelada sobre macOS.

## Por qué existe

Playwright lleva su propio runtime Node (`playwright/driver/node`, firmado por
Microsoft con hardened runtime y los entitlements de JIT que V8 exige). El hook
oficial de PyInstaller lo empaca como data, pero en el pipeline de este repo el
binario sale re-procesado y re-firmado ad-hoc **perdiendo los entitlements**,
aunque conserva el flag de hardened runtime (`flags=0x10000`). Consecuencia,
verificada empíricamente (26-ago-2026, mismo binario, distintas firmas):

- hardened runtime **sin** `com.apple.security.cs.allow-jit` → node muere al
  arrancar con `Fatal process out of memory: Failed to reserve virtual memory
  for CodeRange` (V8 no puede hacer el `mmap` ejecutable de su code range),
  antes de ejecutar una sola línea del driver.
- hardened runtime **con** `allow-jit` (y `allow-unsigned-executable-memory` /
  `disable-executable-page-protection`) → corre normal.

La app congelada extrae el driver a un directorio temporal (`_MEIxxxx`) en cada
arranque, así que la firma correcta no puede arreglarse en el build: se re-firma
la copia extraída, una vez, antes de lanzar Playwright.

## Contrato

`asegurar_driver_playwright_macos()` es **best-effort e idempotente**: solo
actúa en macOS congelado, no toca nada si el driver NO está en el estado letal
(sin hardened runtime, o runtime ya con `allow-jit`), y cualquier fallo
(`codesign` ausente, permisos, binario no encontrado) se registra y se sigue —
el `launch()` de Playwright degrada por su propio camino de error claro. Nunca
lanza: arreglar la firma no puede ser peor que no arreglarla.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_ENTITLEMENTS_JIT = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>com.apple.security.cs.allow-jit</key>
\t<true/>
\t<key>com.apple.security.cs.allow-unsigned-executable-memory</key>
\t<true/>
\t<key>com.apple.security.cs.disable-executable-page-protection</key>
\t<true/>
\t<key>com.apple.security.cs.allow-dyld-environment-variables</key>
\t<true/>
</dict>
</plist>
"""


def _driver_node_path() -> Path | None:
    """Ruta al binario `node` del driver, relativa al paquete `playwright`
    instalado (en frozen, dentro del `_MEIxxxx` extraído). `None` si el
    paquete no está importable o el binario no está donde debe."""
    try:
        import playwright
    except ImportError:  # pragma: no cover - el caller ya importó async_api antes
        return None
    candidato = Path(playwright.__file__).parent / "driver" / "node"
    return candidato if candidato.is_file() else None


_BIT_HARDENED_RUNTIME = 0x10000
"""Bit `csflags` del hardened runtime (`codesign -dv` lo muestra como
`flags=0x…(runtime)`). Solo ese estado (runtime SIN `allow-jit`) mata a V8."""


def _necesita_refirma(driver_node: Path) -> bool | None:
    """¿El binario está en el estado que mata a V8 (hardened runtime sin
    `allow-jit`)? `None` si no se pudo consultar (sin `codesign`, timeout)."""
    try:
        consulta = subprocess.run(
            ["codesign", "-dv", "--entitlements", "-", str(driver_node)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    salida = consulta.stdout + consulta.stderr
    if consulta.returncode != 0:
        # Sin firma válida: en arm64 un binario sin firma recibe SIGKILL al
        # ejecutarse; firmarlo ad-hoc con los entitlements solo puede ayudar.
        return True
    m = re.search(r"flags=0x([0-9a-fA-F]+)", salida)
    con_runtime = bool(m) and (int(m.group(1), 16) & _BIT_HARDENED_RUNTIME) != 0
    if not con_runtime:
        # Sin hardened runtime V8 reserva su CodeRange libremente: aunque no
        # haya entitlements, el driver corre (estado del build actual).
        return False
    return "allow-jit" not in salida


def asegurar_driver_playwright_macos() -> None:
    """Deja el driver `node` apto para JIT (best-effort, ver docstring del módulo)."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return
    driver_node = _driver_node_path()
    if driver_node is None:
        return
    necesita = _necesita_refirma(driver_node)
    if necesita is None:
        logger.warning(
            "No pude consultar la firma del driver de Playwright (%s); se lanza sin "
            "re-firmar. Si falla con «Failed to reserve virtual memory for CodeRange», "
            "esta es la causa.",
            driver_node,
        )
        return
    if not necesita:
        return
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".plist", delete=False) as plist:
            plist.write(_ENTITLEMENTS_JIT)
            ruta_plist = plist.name
        try:
            firma = subprocess.run(
                [
                    "codesign",
                    "--force",
                    "--sign",
                    "-",
                    "--options",
                    "runtime",
                    "--entitlements",
                    ruta_plist,
                    str(driver_node),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        finally:
            Path(ruta_plist).unlink(missing_ok=True)
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("Re-firma del driver de Playwright no pudo ejecutarse.", exc_info=True)
        return
    if firma.returncode != 0:
        logger.warning("Re-firma del driver de Playwright falló: %s", firma.stderr.strip())
        return
    logger.info("Driver de Playwright re-firmado con entitlements de JIT (%s).", driver_node)
