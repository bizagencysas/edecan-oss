"""Bitácora de auditoría del companion (ARCHITECTURE.md §10.7, §10.12).

Cada acción que pasa por `actions.execute` deja una línea JSON en
`~/.edecan/companion.log` (JSONL — una entrada por línea) con qué se pidió,
si el usuario la aprobó y si terminó bien. El contenido de archivos o del
portapapeles NUNCA se escribe tal cual en la bitácora — solo su tamaño— para
no dejar datos sensibles en texto plano en un archivo de log.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Claves de `params` cuyo VALOR nunca se escribe en claro en la bitácora
# (solo su tamaño), porque pueden traer contenido de archivos o del
# portapapeles: `write_file` usa "content", `clipboard_set` usa "text".
# `input_key` (control remoto de teclado, WP-V4-10) usa "texto" -- sin
# redactarlo, la bitácora de auditoría (y el prompt de aprobación, que
# reusa `sanitize_params`) se volverían un keylogger de facto (mismo riesgo
# que señala docs/control-remoto.md §7 para la auditoría agregada de P2).
_REDACTED_KEYS = frozenset(
    {
        "api_key",
        "argv",
        "authorization",
        "client_secret",
        "content",
        "content_b64",
        "cookie",
        "data",
        "device_token",
        "input",
        "key",
        "message",
        "new_string",
        "old_string",
        "password",
        "prompt",
        "refresh_token",
        "secret",
        "text",
        "texto",
        "token",
    }
)


def _is_redacted_key(key: str) -> bool:
    normalized = key.strip().lower()
    return (
        normalized in _REDACTED_KEYS
        or "secret" in normalized
        or "password" in normalized
        or "token" in normalized
        or normalized.endswith("_key")
    )


def _redacted_summary(value: Any) -> str:
    if isinstance(value, str):
        return f"<{len(value)} caracteres omitidos>"
    if isinstance(value, (list, tuple, set, frozenset)):
        return f"<{len(value)} elementos omitidos>"
    if isinstance(value, dict):
        return f"<{len(value)} campos omitidos>"
    return "<valor omitido>"


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(nested_key): (
                _redacted_summary(nested_value)
                if _is_redacted_key(str(nested_key))
                else _sanitize_value(nested_value)
            )
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item) for item in value)
    return value


def sanitize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Copia `params` reemplazando valores potencialmente sensibles por un resumen de tamaño.

    Se usa tanto para la bitácora de auditoría como para lo que se muestra
    en el prompt de aprobación interactiva (`approval.py`), así ninguno de
    los dos vuelca contenido de archivos/portapapeles a una terminal o log.
    """
    sanitized: dict[str, Any] = {}
    for key, value in (params or {}).items():
        sanitized[key] = (
            _redacted_summary(value) if _is_redacted_key(key) else _sanitize_value(value)
        )
    return sanitized


def log_action(
    *,
    action: str,
    params: dict[str, Any] | None,
    approved: bool,
    ok: bool,
    log_path: Path,
    error: str | None = None,
) -> None:
    """Añade (append) una línea JSONL a `log_path`.

    Nunca lanza: un fallo escribiendo la bitácora (disco lleno, permisos,
    etc.) no debe tumbar el companion ni bloquear la respuesta al asistente
    — solo se deja constancia con `logging.exception`.

    `error` (opcional) es el mismo texto que se devuelve al servidor cuando
    `ok=False` — sin él, una línea `ok: false` no dice POR QUÉ falló y el
    único lugar donde queda el motivo real es la respuesta HTTP al teléfono,
    imposible de consultar después. Acotado a 500 caracteres: es un motivo,
    no un volcado.
    """
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "action": action,
        "params": sanitize_params(params),
        "approved": approved,
        "ok": ok,
    }
    if error:
        entry["error"] = error[:500]
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        try:
            log_path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        logger.exception("No se pudo escribir en la bitácora de auditoría %s", log_path)
