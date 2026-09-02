"""Bootstrap de Chromium en primer arranque (macOS y demás plataformas).

El bundle congelado de Edecán NO empaqueta el binario de Chromium: Playwright
lo descarga en `~/Library/Caches/ms-playwright/` (macOS) o el equivalente del
SO. En una Mac fresca esa caché no existe y el navegador falla hasta que alguien
corre `playwright install chromium` a mano.

`asegurar_chromium_instalado()` detecta la ausencia del ejecutable y lanza
`python -m playwright install chromium` una sola vez por proceso (idempotente,
best-effort). No modifica el bundle notarizado ni infla el .app.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_instalado_en_este_proceso = False


def _playwright_cache_root() -> Path:
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env:
        return Path(env)
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "ms-playwright"
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "ms-playwright"
    return home / ".cache" / "ms-playwright"


def chromium_instalado() -> bool:
    """True si hay al menos un directorio `chromium-*` con ejecutable."""
    raiz = _playwright_cache_root()
    if not raiz.is_dir():
        return False
    for candidato in sorted(raiz.glob("chromium-*"), reverse=True):
        for nombre in ("chrome", "chrome.exe", "Chromium.app"):
            if (candidato / nombre).exists():
                return True
            app = candidato / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
            if app.is_file():
                return True
    return False


def asegurar_chromium_instalado(*, force: bool = False) -> bool:
    """Descarga Chromium si falta. Devuelve True si ya estaba o se instaló."""
    global _instalado_en_este_proceso
    if _instalado_en_este_proceso and not force:
        return True
    if not force and chromium_instalado():
        _instalado_en_este_proceso = True
        return True

    with _lock:
        if _instalado_en_este_proceso and not force:
            return True
        if not force and chromium_instalado():
            _instalado_en_este_proceso = True
            return True
        try:
            logger.info(
                "Chromium no encontrado en %s; ejecutando playwright install chromium",
                _playwright_cache_root(),
            )
            proc = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            if proc.returncode != 0:
                logger.warning(
                    "playwright install chromium falló (exit=%s): %s",
                    proc.returncode,
                    (proc.stderr or proc.stdout or "").strip()[:500],
                )
                return False
            ok = chromium_instalado()
            if ok:
                _instalado_en_este_proceso = True
                logger.info("Chromium instalado en %s", _playwright_cache_root())
            else:
                logger.warning(
                    "playwright install chromium terminó bien pero no se encontró el binario en %s",
                    _playwright_cache_root(),
                )
            return ok
        except subprocess.TimeoutExpired:
            logger.warning("playwright install chromium excedió el tiempo límite")
            return False
        except Exception:
            logger.warning("no se pudo instalar Chromium automáticamente", exc_info=True)
            return False


__all__ = ["asegurar_chromium_instalado", "chromium_instalado"]
