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

    Shape exacto (siempre las tres claves, incluso si algo falla):
    ``{"claude_cli": {"installed", "path", "version"},
    "codex_cli": {"installed", "path", "version"},
    "ollama": {"running", "base_url", "models"}}``. `settings`, si se pasa,
    puede traer `CLAUDE_CLI_PATH`/`CODEX_CLI_PATH`/`OLLAMA_BASE_URL` para
    saltarse la búsqueda automática (`getattr` con default `None`, así que
    cualquier objeto — o `None` — sirve).
    """
    return {
        "claude_cli": _detect_cli(settings, "claude", "CLAUDE_CLI_PATH"),
        "codex_cli": _detect_cli(settings, "codex", "CODEX_CLI_PATH"),
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
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:  # noqa: BLE001 - la versión es "mejor esfuerzo": installed sigue True sin ella
        logger.warning("detect_local_providers: fallo leyendo versión de %s", path, exc_info=True)
        return None
    output = (result.stdout or result.stderr or "").strip()
    return output.splitlines()[0].strip() if output else None


def _detect_ollama(settings: Any) -> dict[str, Any]:
    base_url = getattr(settings, "OLLAMA_BASE_URL", None) or _OLLAMA_DEFAULT_BASE_URL
    try:
        response = httpx.get(f"{base_url}/api/tags", timeout=_OLLAMA_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        models = [m["name"] for m in data.get("models") or [] if m.get("name")]
        return {"running": True, "base_url": base_url, "models": models}
    except Exception:  # noqa: BLE001 - Ollama apagado es el caso normal, no un error a propagar
        return {"running": False, "base_url": base_url, "models": []}
