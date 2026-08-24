"""Auto-detección de proveedores LLM locales (`DIRECCION_ACTUAL.md`,
"Principio de UX no negociable: configuración de pocos clicks", WP-V3-03).

Síncrono y rápido a propósito: pensado para correr al abrir la pantalla de
Configuración (o el wizard de primer arranque) sin bloquear la UI con
llamadas async — cada chequeo tiene su propio timeout corto y
`detect_local_providers` NUNCA lanza: cualquier error (binario roto, Ollama
apagado, lo que sea) se traduce en silencio a "no detectado", nunca revienta
la pantalla que lo llama.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_VERSION_TIMEOUT_SECONDS = 5
_OLLAMA_TIMEOUT_SECONDS = 2
_OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"


def detect_local_providers(settings: Any = None) -> dict[str, Any]:
    """Detecta qué proveedores LLM locales están disponibles en esta máquina.

    Shape exacto:
    ``{"claude_cli": {"installed", "path", "version"},
    "ollama": {"running", "base_url", "models"}}``. `settings`, si se pasa,
    puede traer `CLAUDE_CLI_PATH`/`OLLAMA_BASE_URL` para
    saltarse la búsqueda automática (`getattr` con default `None`, así que
    cualquier objeto — o `None` — sirve).
    """
    return {
        "claude_cli": _detect_cli(settings, "claude", "CLAUDE_CLI_PATH"),
        "ollama": _detect_ollama(settings),
    }


def _detect_cli(settings: Any, binary_name: str, settings_attr: str) -> dict[str, Any]:
    try:
        path = getattr(settings, settings_attr, None) or shutil.which(binary_name)
        if not path:
            return {"installed": False, "path": None, "version": None}
        return {"installed": True, "path": path, "version": _cli_version(path)}
    except Exception:  # noqa: BLE001 - detect() JAMÁS debe reventar la pantalla que lo llama
        logger.warning("detect_local_providers: fallo detectando %s", binary_name, exc_info=True)
        return {"installed": False, "path": None, "version": None}


def _cli_version(path: str) -> str | None:
    try:
        result = subprocess.run(
            _windows_shim_argv(path, ["--version"]),
            capture_output=True,
            text=True,
            # Windows no usa UTF-8 por default (rompe acentos); en POSIX
            # UTF-8 ya es lo esperado, asi que fijarlo explicito es portable.
            encoding="utf-8",
            errors="replace",
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:  # noqa: BLE001 - la versión es "mejor esfuerzo": installed sigue True sin ella
        logger.warning("detect_local_providers: fallo leyendo versión de %s", path, exc_info=True)
        return None
    output = (result.stdout or result.stderr or "").strip()
    return output.splitlines()[0].strip() if output else None


def _windows_shim_argv(binary_path: str, rest: list[str]) -> list[str]:
    """En Windows, los binarios globales de npm (como `claude`) suelen ser
    shims `.cmd`/`.bat`, no un `.exe`. `CreateProcess` (lo que usa
    `subprocess` con `shell=False`) no sabe ejecutar un `.cmd` directamente
    — falla con "no es una aplicacion Win32 valida" — hace falta pasarlo por
    `cmd.exe /c`. Esto NO reabre la puerta a `shell=True`: seguimos pasando
    una lista de argumentos (cada uno se cita por separado via
    `subprocess.list2cmdline`) y `binary_path`/`rest` aqui son la ruta ya
    resuelta por `shutil.which` y flags fijas del proveedor, nunca texto
    libre de usuario o del modelo (evita la clase de bug "BatBadBut")."""
    if os.name == "nt" and binary_path.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("ComSpec", "cmd.exe")
        return [comspec, "/d", "/s", "/c", binary_path, *rest]
    return [binary_path, *rest]


def _detect_ollama(settings: Any) -> dict[str, Any]:
    base_url = (getattr(settings, "OLLAMA_BASE_URL", None) or _OLLAMA_DEFAULT_BASE_URL).rstrip("/")
    try:
        response = httpx.get(f"{base_url}/api/tags", timeout=_OLLAMA_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        models = [m["name"] for m in data.get("models") or [] if m.get("name")]
        return {"running": True, "base_url": base_url, "models": models}
    except Exception:  # noqa: BLE001 - Ollama apagado es el caso normal, no un error a propagar
        return {"running": False, "base_url": base_url, "models": []}
