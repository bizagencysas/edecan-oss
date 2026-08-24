"""Bitácora local de cada llamada al proveedor de LLM (idea del dueño, jul-2026).

Nace de un bug real: la app de escritorio lanza el backend como sidecar y su
`stdout` no va a ningún lado, así que un `logger.warning` normal es invisible
en producción. El bug de ese día (el selector de `capability_routing` dejaba
fuera una herramienta necesaria) solo apareció cuando alguien relanzó el
backend a mano capturando `stdout` — y estaba en UNA línea. Esta bitácora
persiste en disco sin depender de a dónde vaya el `stdout` del proceso real.

Cada línea registra: qué se le pidió al proveedor (system + últimos mensajes,
truncados), qué contestó, QUÉ HERRAMIENTAS SE LE OFRECIERON ese turno, cuáles
pidió, tokens y duración. El campo `tools_offered`/`tools_requested` no es
adorno: comparar ambos delata en un vistazo un selector que dejó una tool
fuera (exactamente lo que costó horas de depuración) sin tener que reproducir
la conversación.

Privacidad: esto es un asistente PRIVADO de un solo dueño (ARCHITECTURE.md
§0). Los prompts no deben salir a un tercero — por eso esta bitácora escribe
solo a un archivo local (`DATA_DIR`, el mismo campo que ya usa
`edecan_api.config.Settings`), nunca a un servicio externo, y pasa cada
string por `redact()` antes de tocar disco.

Activación: se lee de `ctx.settings` con `getattr` defensivo, el mismo
criterio que ya usa el resto de `edecan_core` para no acoplarse al tipo real
de `Settings` (ver el docstring de `ToolContext.settings`). Sin `DATA_DIR`
configurado — como en TODOS los tests de este paquete, que pasan
`settings=None` — esta función es un no-op total: ni siquiera toca el
filesystem. `EDECAN_LLM_CALL_LOG=False` en `settings` es el apagador
explícito que pidió el dueño.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .llm_types import ChatMessage
from .safety import redact

logger = logging.getLogger(__name__)

_LOG_FILENAME = "llm-calls.jsonl"
_DEFAULT_MAX_BYTES = 5_000_000
"""Tope por archivo antes de rotar — 5 MB de JSON líneas son ya miles de
llamadas; suficiente para depurar sin arriesgar llenar el disco del dueño."""
_DEFAULT_BACKUP_COUNT = 3
_DEFAULT_TRUNCATE_CHARS = 2000
_DEFAULT_LAST_MESSAGES = 6


def _settings_value(settings: Any, name: str, default: Any) -> Any:
    """`getattr` defensivo sobre `ctx.settings` — nunca revienta si el
    objeto no tiene el campo (o ni siquiera existe, como en los tests)."""
    try:
        value = getattr(settings, name, default)
    except Exception:  # noqa: BLE001 - un `settings` exótico no debe romper el turno
        return default
    return default if value is None else value


def _truncate(text: str, limit: int) -> str:
    texto = redact(text)
    if len(texto) <= limit:
        return texto
    resto = len(texto) - limit
    return f"{texto[:limit]}…[truncado, {resto} caracteres más]"


def _message_preview(message: ChatMessage, limit: int) -> dict[str, Any]:
    """Resumen corto de UN mensaje — nunca el bloque crudo: un `tool_result`
    puede traer un payload grande (p. ej. el HTML de una página) que no
    aporta nada a la depuración y sí infla el archivo."""
    content = message.content
    if isinstance(content, str):
        return {"role": message.role, "content_preview": _truncate(content, limit)}
    partes: list[str] = []
    for block in content:
        tipo = str(block.get("type", "?"))
        if tipo == "text":
            partes.append(_truncate(str(block.get("text", "")), limit))
        elif tipo == "tool_use":
            input_preview = _truncate(str(block.get("input")), limit // 2)
            partes.append(f"tool_use:{block.get('name')}({input_preview})")
        elif tipo == "tool_result":
            partes.append(f"tool_result:{_truncate(str(block.get('content', '')), limit // 2)}")
        else:
            partes.append(tipo)
    return {"role": message.role, "content_preview": " | ".join(partes)}


def _rotated_path(path: Path, index: int) -> Path:
    return path.with_name(f"{path.name}.{index}")


def _rotate_if_needed(path: Path, max_bytes: int, backup_count: int) -> None:
    """Rotación por tamaño simple (sin `logging.handlers`, que cachea el
    handler entre llamadas y complica probar con distintos `DATA_DIR` en el
    mismo proceso de tests): `archivo` -> `.1` -> `.2` ... hasta
    `backup_count`, descartando el más viejo."""
    if max_bytes <= 0 or backup_count <= 0:
        return
    if not path.exists() or path.stat().st_size < max_bytes:
        return
    for index in range(backup_count - 1, 0, -1):
        origen = _rotated_path(path, index)
        if origen.exists():
            origen.replace(_rotated_path(path, index + 1))
    path.replace(_rotated_path(path, 1))


def log_llm_call(
    *,
    settings: Any,
    tenant_id: Any,
    user_id: Any,
    provider: Any,
    model: str,
    iteration: int,
    system_prompt: str | None,
    messages: list[ChatMessage],
    tools_offered: list[str],
    tools_requested: list[str],
    response_text: str,
    duration_seconds: float,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Escribe una línea JSON con esta llamada al proveedor, o no hace nada.

    Nunca lanza — una bitácora de depuración jamás debe tumbar un turno real
    (mismo criterio que el resto de `agent.py`: ver los `except Exception`
    de `_automatic_grounding`/`_execute_resolved_calls`)."""
    if not _settings_value(settings, "EDECAN_LLM_CALL_LOG", True):
        return
    data_dir = _settings_value(settings, "DATA_DIR", None)
    if not data_dir:
        return
    try:
        max_bytes = int(
            _settings_value(settings, "EDECAN_LLM_CALL_LOG_MAX_BYTES", _DEFAULT_MAX_BYTES)
        )
        backup_count = int(
            _settings_value(settings, "EDECAN_LLM_CALL_LOG_BACKUP_COUNT", _DEFAULT_BACKUP_COUNT)
        )
        truncate_chars = int(
            _settings_value(
                settings, "EDECAN_LLM_CALL_LOG_TRUNCATE_CHARS", _DEFAULT_TRUNCATE_CHARS
            )
        )
        last_n = int(
            _settings_value(settings, "EDECAN_LLM_CALL_LOG_LAST_MESSAGES", _DEFAULT_LAST_MESSAGES)
        )

        directorio = Path(str(data_dir)).expanduser()
        directorio.mkdir(parents=True, exist_ok=True)
        path = directorio / _LOG_FILENAME
        _rotate_if_needed(path, max_bytes, backup_count)

        # Marca explícita del caso más costoso de diagnosticar: el modelo no
        # llamó herramienta Y no escribió texto. Con `output_tokens > 0` además,
        # generó algo que el proveedor descartó -- puede ser una llamada mal
        # formada o pensamiento sin cerrar. Poner el flag ARRIBA vuelve fácil
        # buscar `grep '"salida_vacia": true'` en el jsonl; sin él la única
        # forma es leer cada línea y comparar los tres campos. Se calcula acá,
        # no en el caller, para que ningún caller pueda olvidarlo.
        salida_vacia = not tools_requested and not response_text.strip()

        registro = {
            "ts": datetime.now(UTC).isoformat(),
            "salida_vacia": salida_vacia,
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "provider": str(getattr(provider, "name", None) or provider.__class__.__name__),
            "model": model,
            "iteration": iteration,
            "duration_ms": round(duration_seconds * 1000),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "system_preview": _truncate(system_prompt or "", truncate_chars),
            "messages_preview": [
                _message_preview(message, truncate_chars) for message in messages[-last_n:]
            ],
            # El punto entero de esta bitácora (ver docstring del módulo):
            # ofrecidas vs. pedidas delata un selector que dejó una fuera.
            "tools_offered": tools_offered,
            "tools_requested": tools_requested,
            "response_text_preview": _truncate(response_text, truncate_chars),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - la bitácora nunca debe tumbar un turno real
        logger.warning("No se pudo escribir la bitácora de llamadas al modelo", exc_info=True)
