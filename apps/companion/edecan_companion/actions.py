"""Acciones que el companion puede ejecutar en el equipo del usuario.

Contrato (ARCHITECTURE.md §10.7, §10.12): `execute(action, params, config,
approver)` es el único punto de entrada. Por cada llamada:

1. Si `action` no es una de las soportadas → error, sin pedir aprobación.
2. Se pide aprobación vía `approver(action, params, config)` — por defecto
   (`approval.default_approver`) es una pregunta interactiva en la terminal,
   salvo que la acción esté en `config.auto_approve`.
3. Si se aprueba, se corre el handler correspondiente en un hilo aparte
   (son funciones bloqueantes: IO de archivos o `subprocess`) y se devuelve
   su resultado.
4. CADA llamada deja constancia en la bitácora de auditoría
   (`audit.log_action`), se haya aprobado o no, haya salido bien o no.

Las acciones de archivos (`read_dir`, `read_file`, `write_file`) están
restringidas a `config.sandbox_dir`: cualquier ruta que se resuelva fuera de
esa carpeta (rutas "..", absolutas, o enlaces simbólicos que apunten afuera)
se rechaza. `run_command` solo permite ejecutables listados en
`config.allowed_commands`, y siempre corre con `shell=False` y con timeout —
nunca interpreta ";", "&&", tuberías ni ningún otro metacarácter de shell.

`input_pointer`/`input_key` (control remoto de teclado/mouse, WP-V4-10) son
las acciones de mayor impacto de todo este módulo: además del pipeline de
arriba, exigen `config.remote_input_enabled=true` (apagado por defecto,
opt-in explícito del dueño de la máquina) y, en macOS, el permiso de
Accesibilidad concedido a mano en Ajustes del Sistema — nunca automatizado.
Ver `docs/control-remoto.md` §7 y el docstring de `_QuartzInputBackend`.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import io
import logging
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, Protocol

from edecan_companion import audit
from edecan_companion.config import CompanionConfig

logger = logging.getLogger(__name__)

MAX_READ_FILE_BYTES = 256 * 1024
MAX_COMMAND_OUTPUT_BYTES = 10 * 1024
COMMAND_TIMEOUT_SECONDS = 30
HELPER_SUBPROCESS_TIMEOUT_SECONDS = 15

# -- IDE embebido (ROADMAP_V2.md §7.8, WP-V2-08) -----------------------------

MAX_TREE_DEPTH = 5
MAX_TREE_ENTRIES = 500
MAX_SEARCH_FILES = 2000
MAX_SEARCH_MATCHES = 200
MAX_SEARCH_LINE_CHARS = 200
MAX_SEARCH_FILE_BYTES = 256 * 1024

# Carpetas que `list_tree`/`search_files` nunca recorren ni cuentan contra
# sus topes -- ruido casi siempre irrelevante para un IDE (control de
# versiones, dependencias instaladas, cachés de bytecode/venv).
_IGNORED_TREE_DIR_NAMES = frozenset({".git", "node_modules", "__pycache__", ".venv"})

# Acciones del IDE embebido, gateadas además por `config.ide_enabled`
# (`execute()` las corta ANTES de pedir aprobación si está en `false`).
_IDE_ACTIONS = frozenset({"list_tree", "search_files", "apply_edit", "trash_path", "screenshot"})

# -- Control remoto de teclado/mouse (WP-V4-10, docs/control-remoto.md §7) --
#
# Gateadas además por `config.remote_input_enabled` (mismo patrón que
# `_IDE_ACTIONS`/`ide_enabled`: `execute()` las corta ANTES de pedir
# aprobación si está en `false`) y, por encima de eso, por la regla de
# aprobación "más dura" de `approval.py` (recordada solo por sesión de
# control activa + `remote_input_remember_minutes`, nunca por `auto_approve`
# -- ver el docstring de `approval._approve_input_action`).
_INPUT_ACTIONS = frozenset({"input_pointer", "input_key"})

_POINTER_ACTIONS: tuple[str, ...] = (
    "move",
    "click",
    "double_click",
    "right_click",
    "mouse_down",
    "mouse_up",
    "drag",
    "scroll",
)
_MOUSE_BUTTONS: tuple[str, ...] = ("left", "right", "middle")
_SPECIAL_KEYS: tuple[str, ...] = (
    "enter",
    "tab",
    "escape",
    "backspace",
    "arrow_up",
    "arrow_down",
    "arrow_left",
    "arrow_right",
    "delete_forward",
    "home",
    "end",
    "page_up",
    "page_down",
    "space",
    "a",
    "c",
    "v",
    "x",
    "z",
    "s",
)
_KEY_MODIFIERS: tuple[str, ...] = ("command", "control", "option", "shift")

# Keycodes virtuales estándar de macOS (`Events.h`, iguales en cualquier
# distribución de teclado -- son posiciones físicas de tecla, no símbolos).
_SPECIAL_KEYCODES: dict[str, int] = {
    "enter": 36,
    "tab": 48,
    "escape": 53,
    "backspace": 51,
    "arrow_up": 126,
    "arrow_down": 125,
    "arrow_left": 123,
    "arrow_right": 124,
    "delete_forward": 117,
    "home": 115,
    "end": 119,
    "page_up": 116,
    "page_down": 121,
    "space": 49,
    "a": 0,
    "c": 8,
    "v": 9,
    "x": 7,
    "z": 6,
    "s": 1,
}


class ActionError(Exception):
    """Error esperado (validación, permisos, IO) — seguro de mostrar tal cual al usuario."""


class Approver(Protocol):
    def __call__(
        self, action: str, params: dict[str, Any], config: CompanionConfig
    ) -> Awaitable[bool]: ...


ActionHandler = Callable[[dict[str, Any], CompanionConfig], dict[str, Any]]


# ---------------------------------------------------------------------------
# Sandbox de archivos
# ---------------------------------------------------------------------------


def _resolve_in_sandbox(config: CompanionConfig, raw_path: str | None) -> Path:
    """Resuelve `raw_path` dentro de `config.sandbox_dir`; lanza `ActionError` si escapa.

    `raw_path` siempre se trata como relativo al sandbox (se descarta
    cualquier apariencia de ruta absoluta) y se resuelve siguiendo enlaces
    simbólicos (`Path.resolve`), así que tanto un "../.." como un symlink
    que apunte fuera del sandbox terminan rechazados por el chequeo final
    de `relative_to`.
    """
    raw_path = (raw_path or ".").strip() or "."

    # Nunca interpretar el path del usuario como absoluto: siempre relativo
    # al sandbox, aunque venga con "/" al inicio.
    relative = raw_path.replace("\\", "/").lstrip("/")
    candidate = (config.sandbox_dir / relative).resolve()

    try:
        candidate.relative_to(config.sandbox_dir)
    except ValueError:
        raise ActionError(f"ruta fuera del sandbox permitido: {raw_path!r}") from None

    return candidate


def _is_within_sandbox(path: Path, config: CompanionConfig) -> bool:
    """`True` si `path` (resolviendo symlinks) sigue dentro de `config.sandbox_dir`.

    A diferencia de `_resolve_in_sandbox` (que valida una ruta *pedida* por
    el asistente, y lanza `ActionError` si escapa), esto valida en silencio
    rutas *descubiertas* al recorrer el sandbox (`list_tree`/`search_files`):
    un symlink a una carpeta o archivo de fuera del sandbox no debe ni
    recorrerse ni leerse, aunque su nombre en sí ya se podía listar antes
    (mismo comportamiento que `read_dir`, que nunca revisó esto para sus
    entradas directas).
    """
    try:
        path.resolve().relative_to(config.sandbox_dir)
    except (OSError, RuntimeError, ValueError):
        # ValueError: resuelve pero cae fuera del sandbox. OSError/RuntimeError:
        # símlink roto o loop de símlinks -- en cualquier caso, no es seguro.
        return False
    return True


# ---------------------------------------------------------------------------
# Handlers — síncronos y bloqueantes a propósito (se corren con asyncio.to_thread)
# ---------------------------------------------------------------------------


def _open_app(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    app = params.get("app")
    if not isinstance(app, str) or not app.strip():
        raise ActionError("falta el parámetro 'app' (texto)")
    app = app.strip()

    if app not in config.allowed_apps:
        raise ActionError(f"app no permitida (agrégala a allowed_apps en companion.yaml): {app!r}")

    if sys.platform == "darwin":
        argv = ["open", "-a", app]
    elif sys.platform.startswith("linux"):
        argv = ["xdg-open", app]
    else:
        raise ActionError(f"abrir apps no está soportado en esta plataforma: {sys.platform!r}")

    try:
        subprocess.run(
            argv,
            check=True,
            timeout=HELPER_SUBPROCESS_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ActionError(f"no se encontró el comando del sistema: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ActionError("se agotó el tiempo de espera abriendo la app") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise ActionError(f"no se pudo abrir {app!r}: {detail}") from exc

    return {"app": app, "launched": True}


def _read_dir(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    target = _resolve_in_sandbox(config, params.get("path"))
    if not target.exists():
        raise ActionError(f"no existe: {target.relative_to(config.sandbox_dir)}")
    if not target.is_dir():
        raise ActionError(f"no es una carpeta: {target.relative_to(config.sandbox_dir)}")

    entries: list[dict[str, Any]] = []
    for entry in sorted(target.iterdir(), key=lambda p: p.name):
        try:
            is_dir = entry.is_dir()
            size = None if is_dir else entry.stat().st_size
        except OSError:
            continue  # entrada ilegible (p. ej. symlink roto): se omite, no se aborta el listado
        entries.append({"name": entry.name, "is_dir": is_dir, "size_bytes": size})

    return {"path": str(target.relative_to(config.sandbox_dir)), "entries": entries}


def _read_file(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    target = _resolve_in_sandbox(config, params.get("path"))
    if not target.exists() or not target.is_file():
        raise ActionError(f"no existe el archivo: {target.relative_to(config.sandbox_dir)}")

    size = target.stat().st_size
    if size > MAX_READ_FILE_BYTES:
        raise ActionError(f"archivo demasiado grande ({size} bytes; máximo {MAX_READ_FILE_BYTES})")

    raw = target.read_bytes()
    try:
        content, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        content, encoding = base64.b64encode(raw).decode("ascii"), "base64"

    return {
        "path": str(target.relative_to(config.sandbox_dir)),
        "content": content,
        "encoding": encoding,
        "size_bytes": size,
    }


def _write_file(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    raw_content = params.get("content")
    if not isinstance(raw_content, str):
        raise ActionError("falta el parámetro 'content' (texto)")

    encoding = params.get("encoding", "utf-8")
    if encoding == "base64":
        try:
            data = base64.b64decode(raw_content, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ActionError(f"'content' no es base64 válido: {exc}") from exc
    elif encoding == "utf-8":
        data = raw_content.encode("utf-8")
    else:
        raise ActionError(f"'encoding' no soportado: {encoding!r} (usa 'utf-8' o 'base64')")

    target = _resolve_in_sandbox(config, params.get("path"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    return {"path": str(target.relative_to(config.sandbox_dir)), "bytes_written": len(data)}


def _trash_path(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    """Mueve una ruta del sandbox a la papelera recuperable."""
    target = _resolve_in_sandbox(config, params.get("path"))
    if target == config.sandbox_dir:
        raise ActionError("no se puede enviar a la papelera la raíz completa del sandbox")
    if not target.exists():
        raise ActionError(f"no existe: {target.relative_to(config.sandbox_dir)}")
    try:
        from send2trash import send2trash

        send2trash(str(target))
    except OSError as exc:
        raise ActionError(f"no se pudo mover a la papelera: {exc}") from exc
    return {"path": str(target.relative_to(config.sandbox_dir)), "trashed": True}


# ---------------------------------------------------------------------------
# Portapapeles
# ---------------------------------------------------------------------------


def _clipboard_get(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    if sys.platform == "darwin":
        argv = ["pbpaste"]
    elif sys.platform.startswith("linux"):
        argv = ["xclip", "-selection", "clipboard", "-o"]
    else:
        raise ActionError(f"portapapeles no soportado en esta plataforma: {sys.platform!r}")

    try:
        proc = subprocess.run(
            argv,
            check=True,
            timeout=HELPER_SUBPROCESS_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ActionError(
            f"no se encontró {argv[0]!r}; instálalo para poder usar el portapapeles ({exc})"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ActionError("se agotó el tiempo de espera leyendo el portapapeles") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise ActionError(f"no se pudo leer el portapapeles: {detail}") from exc

    return {"text": proc.stdout}


def _clipboard_set(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    text = params.get("text")
    if not isinstance(text, str):
        raise ActionError("falta el parámetro 'text' (texto)")

    if sys.platform == "darwin":
        argv = ["pbcopy"]
    elif sys.platform.startswith("linux"):
        argv = ["xclip", "-selection", "clipboard"]
    else:
        raise ActionError(f"portapapeles no soportado en esta plataforma: {sys.platform!r}")

    try:
        subprocess.run(
            argv,
            input=text,
            check=True,
            timeout=HELPER_SUBPROCESS_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ActionError(
            f"no se encontró {argv[0]!r}; instálalo para poder usar el portapapeles ({exc})"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ActionError("se agotó el tiempo de espera escribiendo el portapapeles") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise ActionError(f"no se pudo escribir el portapapeles: {detail}") from exc

    return {"written_chars": len(text)}


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------


def _truncate_utf8(text: str, limit_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit_bytes:
        return text, False
    return encoded[:limit_bytes].decode("utf-8", errors="ignore"), True


def _run_command(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    command = params.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ActionError("falta el parámetro 'command' (texto)")

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ActionError(f"comando mal formado: {exc}") from exc

    if not argv:
        raise ActionError("comando vacío")

    executable = argv[0]
    if executable not in config.allowed_commands:
        raise ActionError(
            f"comando no permitido (agrega {executable!r} a allowed_commands en companion.yaml)"
        )

    try:
        proc = subprocess.run(
            argv,
            cwd=config.sandbox_dir,
            shell=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ActionError(f"no se encontró el ejecutable {executable!r}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ActionError(
            f"el comando superó el tiempo límite de {COMMAND_TIMEOUT_SECONDS}s"
        ) from exc

    stdout, stdout_truncated = _truncate_utf8(proc.stdout, MAX_COMMAND_OUTPUT_BYTES)
    stderr, stderr_truncated = _truncate_utf8(proc.stderr, MAX_COMMAND_OUTPUT_BYTES)

    return {
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": stdout_truncated or stderr_truncated,
    }


# ---------------------------------------------------------------------------
# IDE embebido (ROADMAP_V2.md §7.8, WP-V2-08): list_tree, search_files,
# apply_edit, screenshot -- las cuatro pasan por el mismo pipeline de
# aprobación+auditoría+sandbox que el resto (ver `execute()` más abajo).
# ---------------------------------------------------------------------------


def _clamp_int(raw: Any, *, default: int, minimum: int, maximum: int) -> int:
    """`int(raw)` acotado a `[minimum, maximum]`; `default` si falta o no es convertible.

    Nunca lanza: un `max_depth`/`max_entries` inválido o desmedido degrada en
    silencio al tope permitido en vez de fallar la acción completa.
    """
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _iter_dir_safe(dir_path: Path) -> list[tuple[str, bool]]:
    """`[(nombre, es_carpeta)]` de `dir_path`; carpeta/entradas ilegibles se omiten (no abortan)."""
    try:
        children = list(dir_path.iterdir())
    except OSError:
        return []
    result: list[tuple[str, bool]] = []
    for child in children:
        try:
            result.append((child.name, child.is_dir()))
        except OSError:
            continue
    return result


def _list_tree(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    """Árbol recursivo de `path` (default: raíz del sandbox), acotado en profundidad y tamaño.

    `max_depth` (≤ `MAX_TREE_DEPTH`) y `max_entries` (≤ `MAX_TREE_ENTRIES`,
    contado sobre TODO el árbol, no por carpeta) se recortan en silencio al
    tope si se pide más -- nunca lanzan error, así "pide un árbol enorme"
    degrada a "árbol truncado" (`truncated: true`) en vez de fallar la
    acción. `_IGNORED_TREE_DIR_NAMES` se ignora siempre (ni se lista ni
    cuenta para `max_entries`). Una carpeta que llegó al límite de
    profundidad, o que es un symlink que escapa del sandbox
    (`_is_within_sandbox`), se lista como hoja (`children: None`) en vez de
    expandirse.
    """
    target = _resolve_in_sandbox(config, params.get("path"))
    if not target.exists():
        raise ActionError(f"no existe: {target.relative_to(config.sandbox_dir)}")
    if not target.is_dir():
        raise ActionError(f"no es una carpeta: {target.relative_to(config.sandbox_dir)}")

    max_depth = _clamp_int(
        params.get("max_depth"), default=MAX_TREE_DEPTH, minimum=1, maximum=MAX_TREE_DEPTH
    )
    max_entries = _clamp_int(
        params.get("max_entries"), default=MAX_TREE_ENTRIES, minimum=1, maximum=MAX_TREE_ENTRIES
    )
    state = {"remaining": max_entries, "truncated": False}

    def _walk(dir_path: Path, depth: int) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        entries = sorted(_iter_dir_safe(dir_path), key=lambda e: (not e[1], e[0]))
        for name, is_dir in entries:
            if is_dir and name in _IGNORED_TREE_DIR_NAMES:
                continue
            if state["remaining"] <= 0:
                state["truncated"] = True
                break
            state["remaining"] -= 1
            child_path = dir_path / name
            node: dict[str, Any] = {"name": name, "is_dir": is_dir}
            if is_dir:
                can_descend = depth + 1 < max_depth and _is_within_sandbox(child_path, config)
                node["children"] = _walk(child_path, depth + 1) if can_descend else None
            else:
                try:
                    node["size_bytes"] = child_path.stat().st_size
                except OSError:
                    node["size_bytes"] = None
            nodes.append(node)
        return nodes

    entries = _walk(target, depth=0)
    return {
        "path": str(target.relative_to(config.sandbox_dir)),
        "entries": entries,
        "truncated": state["truncated"],
    }


def _iter_files_safe(base: Path) -> Iterator[Path]:
    """Archivos bajo `base` (o `base` mismo si ya es un archivo).

    Usa `os.walk` con `followlinks=False` (su default): un symlink a una
    carpeta puede listarse como nombre pero nunca se recorre su contenido,
    así que no hace falta un chequeo de sandbox aparte para carpetas (sí para
    archivos individuales -- ver `_is_within_sandbox` en `_search_files`).
    Orden determinista (nombres ordenados) e ignora `_IGNORED_TREE_DIR_NAMES`.
    """
    if base.is_file():
        yield base
        return
    if not base.is_dir():
        return
    for root, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_TREE_DIR_NAMES)
        for filename in sorted(filenames):
            yield Path(root) / filename


def _search_files(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    """Busca `query` (substring, sin distinguir mayúsculas) línea por línea bajo `path`.

    Recorrido acotado a `MAX_SEARCH_FILES` archivos considerados y
    `MAX_SEARCH_MATCHES` coincidencias devueltas -- lo que se cumpla primero
    corta la búsqueda y marca `truncated`. Solo mira archivos de texto: se
    saltan en silencio los que pesan más de `MAX_SEARCH_FILE_BYTES` o que no
    decodifican como UTF-8 (se asumen binarios). Cada línea coincidente se
    recorta a `MAX_SEARCH_LINE_CHARS` caracteres. Un archivo descubierto por
    el recorrido que resulte ser un symlink apuntando fuera del sandbox se
    salta (`_is_within_sandbox`) -- nunca se lee contenido de fuera del
    sandbox, aunque el recorrido lo haya "encontrado" por su nombre.
    """
    query = params.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ActionError("falta el parámetro 'query' (texto)")
    needle = query.lower()

    base = _resolve_in_sandbox(config, params.get("path"))
    if not base.exists():
        raise ActionError(f"no existe: {base.relative_to(config.sandbox_dir)}")

    matches: list[dict[str, Any]] = []
    files_scanned = 0
    truncated = False

    for file_path in _iter_files_safe(base):
        if files_scanned >= MAX_SEARCH_FILES or len(matches) >= MAX_SEARCH_MATCHES:
            truncated = True
            break
        files_scanned += 1

        if not _is_within_sandbox(file_path, config):
            continue
        try:
            if file_path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
            raw = file_path.read_bytes()
        except OSError:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue  # binario: no es un archivo de texto, se omite

        rel = str(file_path.relative_to(config.sandbox_dir))
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(matches) >= MAX_SEARCH_MATCHES:
                truncated = True
                break
            if needle in line.lower():
                texto = line if len(line) <= MAX_SEARCH_LINE_CHARS else line[:MAX_SEARCH_LINE_CHARS]
                matches.append({"path": rel, "line": lineno, "texto": texto})

    return {"query": query, "matches": matches, "truncated": truncated}


def _apply_edit(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    """Reemplaza `old_string` por `new_string` en `path` -- edición quirúrgica, no reescritura.

    Sin `replace_all`, `old_string` debe aparecer EXACTAMENTE una vez (si no,
    `ActionError` con el conteo real, para que quien pidió la edición pase un
    fragmento más específico o use `replace_all=true` a propósito). La
    escritura es atómica: se escribe a un archivo temporal en la MISMA
    carpeta (mismo filesystem) y se hace `os.replace` (rename atómico) sobre
    el destino -- nunca queda el archivo a medio escribir. Solo texto UTF-8;
    reutiliza el mismo tope `MAX_READ_FILE_BYTES` que `read_file`.
    """
    old_string = params.get("old_string")
    if not isinstance(old_string, str) or old_string == "":
        raise ActionError("falta el parámetro 'old_string' (texto no vacío)")
    new_string = params.get("new_string")
    if not isinstance(new_string, str):
        raise ActionError("falta el parámetro 'new_string' (texto)")
    replace_all = bool(params.get("replace_all", False))

    target = _resolve_in_sandbox(config, params.get("path"))
    if not target.exists() or not target.is_file():
        raise ActionError(f"no existe el archivo: {target.relative_to(config.sandbox_dir)}")

    size = target.stat().st_size
    if size > MAX_READ_FILE_BYTES:
        raise ActionError(f"archivo demasiado grande ({size} bytes; máximo {MAX_READ_FILE_BYTES})")

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ActionError(
            "el archivo no es texto UTF-8 legible; apply_edit no soporta binarios"
        ) from exc

    count = content.count(old_string)
    if count == 0:
        raise ActionError("old_string no se encontró en el archivo")
    if not replace_all and count > 1:
        raise ActionError(
            f"old_string no es único: aparece {count} veces; usa replace_all=true o pasa un "
            "fragmento más largo que solo coincida una vez"
        )

    new_content = (
        content.replace(old_string, new_string)
        if replace_all
        else content.replace(old_string, new_string, 1)
    )
    replacements = count if replace_all else 1

    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_content)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_name)
        raise

    return {
        "path": str(target.relative_to(config.sandbox_dir)),
        "replacements": replacements,
        "bytes_written": len(new_content.encode("utf-8")),
    }


def _screenshot_options(params: dict[str, Any]) -> tuple[str, int, int | None]:
    """Valida las opciones de transporte sin acoplarlas al backend de captura."""
    image_format = str(params.get("format") or "png").lower()
    if image_format == "jpg":
        image_format = "jpeg"
    if image_format not in {"png", "jpeg"}:
        raise ActionError("'format' debe ser 'png' o 'jpeg'")

    quality = params.get("quality", 70)
    if not isinstance(quality, int) or isinstance(quality, bool) or not 35 <= quality <= 95:
        raise ActionError("'quality' debe ser un entero entre 35 y 95")

    max_width = params.get("max_width")
    if max_width is not None and (
        not isinstance(max_width, int)
        or isinstance(max_width, bool)
        or not 640 <= max_width <= 3840
    ):
        raise ActionError("'max_width' debe ser un entero entre 640 y 3840")
    return image_format, quality, max_width


def _optimize_screenshot(
    image_bytes: bytes,
    *,
    width: int,
    height: int,
    image_format: str,
    quality: int,
    max_width: int | None,
) -> tuple[bytes, int, int, str]:
    """Reduce peso/latencia con Pillow cuando el extra remoto está instalado.

    La captura PNG básica de macOS conserva compatibilidad con instalaciones
    antiguas sin Pillow. En ese caso se devuelve intacta; Windows/Linux sí
    instalan Pillow mediante el extra ``remote-control``.
    """
    needs_conversion = image_format == "jpeg" or (max_width is not None and width > max_width)
    if not needs_conversion:
        return image_bytes, width, height, "image/png"
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        return image_bytes, width, height, "image/png"

    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = source.convert("RGB") if image_format == "jpeg" else source.copy()
            if max_width is not None and image.width > max_width:
                new_height = max(1, round(image.height * max_width / image.width))
                image = image.resize((max_width, new_height), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            if image_format == "jpeg":
                image.save(output, format="JPEG", quality=quality, optimize=True)
                mime = "image/jpeg"
            else:
                image.save(output, format="PNG", optimize=True)
                mime = "image/png"
            return output.getvalue(), image.width, image.height, mime
    except (OSError, ValueError) as exc:
        raise ActionError(f"no se pudo preparar la captura para transmisión: {exc}") from exc


def _screenshot_via_mss(params: dict[str, Any]) -> tuple[bytes, int, int, int, int]:
    """Captura la pantalla primaria (o ``display``) en Windows/Linux."""
    try:
        import mss  # type: ignore[import-not-found]
        import mss.tools  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ActionError(
            "la captura en Windows/Linux requiere el extra 'remote-control'; "
            "instálalo con: pip install 'edecan-companion[remote-control]'"
        ) from exc

    try:
        with mss.mss() as capture:
            display = params.get("display")
            monitor_index = 1 if display is None else int(display)
            if monitor_index < 0 or monitor_index >= len(capture.monitors):
                raise ActionError(
                    f"'display' fuera de rango: usa un valor entre 0 y {len(capture.monitors) - 1}"
                )
            monitor = capture.monitors[monitor_index]
            shot = capture.grab(monitor)
            image_bytes = mss.tools.to_png(shot.rgb, shot.size)
            return (
                image_bytes,
                int(shot.width),
                int(shot.height),
                int(monitor.get("left", 0)),
                int(monitor.get("top", 0)),
            )
    except ActionError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        hint = "autoriza la captura de pantalla para Edecán en el sistema"
        if sys.platform.startswith("linux"):
            hint = "verifica la sesión gráfica X11/Wayland y el permiso de captura"
        raise ActionError(f"no se pudo capturar la pantalla: {exc}; {hint}") from exc


def _macos_display_target(params: dict[str, Any]) -> tuple[int, int, int, int]:
    """Resuelve el número de pantalla de ``screencapture`` y su geometría.

    macOS numera sus pantallas desde 1 para ``screencapture -D``. Conservamos
    también el ``CGDirectDisplayID`` y el origen global para que los toques del
    teléfono sigan mapeando correctamente cuando hay más de un monitor.
    """
    display = params.get("display")
    if display is not None:
        if isinstance(display, bool):
            raise ActionError("'display' debe ser un número entero")
        try:
            display_index = int(display)
        except (TypeError, ValueError):
            raise ActionError("'display' debe ser un número entero") from None
        if display_index < 1:
            raise ActionError("'display' debe ser un número entero desde 1")
    else:
        display_index = None

    try:
        import Quartz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ActionError("la captura nativa de macOS requiere pyobjc-framework-Quartz") from exc

    error, displays, count = Quartz.CGGetActiveDisplayList(32, None, None)
    if error != Quartz.kCGErrorSuccess:
        raise ActionError(f"macOS no pudo enumerar las pantallas (código {error})")
    selected_index = display_index or 1
    if selected_index > count:
        raise ActionError(f"'display' fuera de rango: usa un valor entre 1 y {count}")
    display_id = displays[selected_index - 1]
    bounds = Quartz.CGDisplayBounds(display_id)
    return selected_index, display_id, int(bounds.origin.x), int(bounds.origin.y)


def _screenshot_via_screencapture(
    params: dict[str, Any],
) -> tuple[bytes, int, int, int, int]:
    """Captura el escritorio completo usando el backend nativo de macOS.

    ``CGDisplayCreateImage`` puede devolver únicamente el fondo de escritorio
    en versiones recientes de macOS aunque TCC informe que el permiso existe.
    La utilidad del sistema ``screencapture`` usa el pipeline moderno que sí
    incluye ventanas, barra de menú y Dock. Es el mismo enfoque probado por
    Jarvis, pero aquí se ejecuta sin shell, con timeout, archivo temporal
    aislado y la identidad firmada estable de ``edecan-local``.
    """

    display_index, _display_id, origin_x, origin_y = _macos_display_target(params)
    include_cursor = params.get("include_cursor", True)
    if not isinstance(include_cursor, bool):
        raise ActionError("'include_cursor' debe ser true o false")

    fd, temporary_name = tempfile.mkstemp(prefix="edecan-screen-", suffix=".png")
    os.close(fd)
    temporary_path = Path(temporary_name)
    # ``screencapture`` crea el archivo. Evitamos entregarle uno preexistente
    # y lo eliminamos siempre al terminar, incluso en timeout o permiso negado.
    with contextlib.suppress(OSError):
        temporary_path.unlink()

    command = [
        "/usr/sbin/screencapture",
        "-x",
        "-t",
        "png",
        "-D",
        str(display_index),
    ]
    if include_cursor:
        command.append("-C")
    command.append(str(temporary_path))

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=HELPER_SUBPROCESS_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            suffix = f": {detail[:500]}" if detail else ""
            raise ActionError(
                "macOS no pudo capturar las ventanas. Verifica Grabación de pantalla "
                f"para Edecán y vuelve a abrir la app{suffix}"
            )
        image_bytes = temporary_path.read_bytes()
        if not image_bytes:
            raise ActionError("macOS devolvió una captura vacía")
        try:
            from PIL import Image  # type: ignore[import-not-found]

            with Image.open(io.BytesIO(image_bytes)) as image:
                width, height = image.size
        except (ImportError, OSError, ValueError) as exc:
            raise ActionError(f"macOS devolvió una captura inválida: {exc}") from exc
        return image_bytes, int(width), int(height), origin_x, origin_y
    except subprocess.TimeoutExpired as exc:
        raise ActionError("macOS tardó demasiado en capturar la pantalla") from exc
    except OSError as exc:
        raise ActionError(f"no se pudo ejecutar la captura nativa de macOS: {exc}") from exc
    finally:
        with contextlib.suppress(OSError):
            temporary_path.unlink()


def _screenshot_via_quartz(params: dict[str, Any]) -> tuple[bytes, int, int, int, int]:
    """Backend Quartz conservado para diagnóstico y compatibilidad interna."""

    _display_index, display_id, origin_x, origin_y = _macos_display_target(params)

    try:
        import AppKit  # type: ignore[import-not-found]
        import Quartz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ActionError("la captura nativa de macOS requiere pyobjc-framework-Quartz") from exc

    image = Quartz.CGDisplayCreateImage(display_id)
    if image is None:
        raise ActionError(
            "Edecán no pudo leer la pantalla. Autoriza Grabación de pantalla para "
            "edecan-local en Ajustes del Sistema y vuelve a abrir Edecán."
        )

    bitmap = AppKit.NSBitmapImageRep.alloc().initWithCGImage_(image)
    encoded = bitmap.representationUsingType_properties_(
        AppKit.NSBitmapImageFileTypePNG,
        {},
    )
    image_bytes = bytes(encoded) if encoded is not None else b""
    if not image_bytes:
        raise ActionError("macOS devolvió una captura vacía")

    return (
        image_bytes,
        int(Quartz.CGImageGetWidth(image)),
        int(Quartz.CGImageGetHeight(image)),
        origin_x,
        origin_y,
    )


def _screenshot(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    """Captura la pantalla y devuelve un frame PNG/JPEG optimizado en base64.

    En macOS usa el capturador nativo del sistema para incluir ventanas, Dock,
    barra de menú y cursor; en Windows/Linux usa `mss`, instalado mediante el
    extra ``remote-control``.
    Los permisos siguen siendo siempre los nativos del sistema operativo: esta
    acción no los solicita, evade ni automatiza. Puede reducir el frame y
    convertirlo a JPEG para que el visor remoto sea fluido.
    """
    image_format, quality, max_width = _screenshot_options(params)
    if sys.platform != "darwin":
        if sys.platform != "win32" and not sys.platform.startswith("linux"):
            raise ActionError("captura no soportada en esta plataforma")
        image_bytes, width, height, origin_x, origin_y = _screenshot_via_mss(params)
        image_bytes, width, height, mime = _optimize_screenshot(
            image_bytes,
            width=width,
            height=height,
            image_format=image_format,
            quality=quality,
            max_width=max_width,
        )
        return {
            "image_b64": base64.b64encode(image_bytes).decode("ascii"),
            "width": width,
            "height": height,
            "mime": mime,
            "origin_x": origin_x,
            "origin_y": origin_y,
        }

    image_bytes, width, height, origin_x, origin_y = _screenshot_via_screencapture(params)
    image_bytes, width, height, mime = _optimize_screenshot(
        image_bytes,
        width=width,
        height=height,
        image_format=image_format,
        quality=quality,
        max_width=max_width,
    )
    return {
        "image_b64": base64.b64encode(image_bytes).decode("ascii"),
        "width": width,
        "height": height,
        "mime": mime,
        "origin_x": origin_x,
        "origin_y": origin_y,
    }


# ---------------------------------------------------------------------------
# Control remoto de teclado/mouse (WP-V4-10, docs/control-remoto.md §7):
# input_pointer, input_key -- nivel TeamViewer. CGEvent (macOS) queda
# ABSTRAÍDO detrás de `InputBackend` a propósito: ni un test ni un bug de
# aprobación debe poder mover el mouse real o escribir texto real en esta
# máquina (CI o de un desarrollador) -- solo `_QuartzInputBackend`, la única
# implementación real, toca `Quartz` de verdad, y solo se construye cuando de
# verdad hace falta ejecutar la acción (nunca al importar este módulo).
# ---------------------------------------------------------------------------


class InputBackend(Protocol):
    """Backend de bajo nivel que sintetiza input de teclado/mouse.

    `_input_pointer`/`_input_key` SOLO hablan con esta interfaz, nunca con
    `Quartz` directo -- así los tests pueden inyectar un doble que graba
    llamadas (ver `tests/test_actions_input.py::_FakeInputBackend`) sin tocar
    el mouse/teclado real de la máquina que corre la suite.
    """

    def move_pointer(self, x: int, y: int) -> None: ...
    def click_pointer(self, x: int, y: int, button: str) -> None: ...
    def pointer_down(self, x: int, y: int, button: str) -> None: ...
    def pointer_up(self, x: int, y: int, button: str) -> None: ...
    def scroll_pointer(self, delta_x: int, delta_y: int) -> None: ...
    def type_text(self, text: str) -> None: ...
    def press_key(self, key: str, modifiers: tuple[str, ...] = ()) -> None: ...


class _QuartzInputBackend:
    """Implementación real vía Quartz `CGEvent` -- SOLO macOS.

    `pyobjc-framework-Quartz` es una dependencia OPCIONAL de este paquete
    (`[project.optional-dependencies]` en `pyproject.toml`, grupo
    `remote-input`) -- por eso `Quartz` se importa de forma perezosa, DENTRO
    de `__init__`, nunca a nivel de módulo: el resto de `edecan_companion`
    (incluidas TODAS las demás acciones) debe seguir funcionando en una
    máquina sin ese paquete instalado, o en Linux/Windows, donde no existe.

    macOS exige que el proceso que llama a `CGEvent*` tenga el permiso de
    **Accesibilidad** concedido en Ajustes del Sistema → Privacidad y
    Seguridad → Accesibilidad -- un clic humano explícito que este backend
    NUNCA solicita ni evade (mismo principio que `_screenshot` con el permiso
    de Grabación de pantalla, ver su docstring). Si el permiso no está
    concedido, `Quartz.AXIsProcessTrusted()` devuelve `False` *antes* de
    intentar sintetizar ningún evento, y esto falla con un `ActionError`
    claro y accionable en vez de simplemente no hacer nada en silencio.
    """

    def __init__(self) -> None:
        try:
            import Quartz  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ActionError(
                "el control remoto de teclado/mouse requiere el paquete opcional "
                "'pyobjc-framework-Quartz' -- instálalo con: pip install "
                "'edecan-companion[remote-input]' (o: pip install pyobjc-framework-Quartz)"
            ) from exc

        if not Quartz.AXIsProcessTrusted():
            raise ActionError(
                "este proceso no tiene el permiso de Accesibilidad concedido en macOS. "
                "Abre Edecán → Ajustes → Permisos de esta computadora y pulsa "
                "'Comprobar y permitir' en Accesibilidad. Edecán abrirá el diálogo "
                "correcto y te mostrará su archivo exacto si macOS exige seleccionarlo."
            )

        self._Quartz = Quartz

    def _mouse_button_constant(self, button: str) -> Any:
        Quartz = self._Quartz
        return {
            "left": Quartz.kCGMouseButtonLeft,
            "right": Quartz.kCGMouseButtonRight,
            "middle": Quartz.kCGMouseButtonCenter,
        }[button]

    def move_pointer(self, x: int, y: int) -> None:
        Quartz = self._Quartz
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, (x, y), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def click_pointer(self, x: int, y: int, button: str) -> None:
        Quartz = self._Quartz
        mouse_button = self._mouse_button_constant(button)
        down_type, up_type = {
            "left": (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp),
            "right": (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp),
            "middle": (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp),
        }[button]
        for event_type in (down_type, up_type):
            event = Quartz.CGEventCreateMouseEvent(None, event_type, (x, y), mouse_button)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def _post_pointer_button(self, x: int, y: int, button: str, *, down: bool) -> None:
        Quartz = self._Quartz
        mouse_button = self._mouse_button_constant(button)
        event_type = {
            ("left", True): Quartz.kCGEventLeftMouseDown,
            ("left", False): Quartz.kCGEventLeftMouseUp,
            ("right", True): Quartz.kCGEventRightMouseDown,
            ("right", False): Quartz.kCGEventRightMouseUp,
            ("middle", True): Quartz.kCGEventOtherMouseDown,
            ("middle", False): Quartz.kCGEventOtherMouseUp,
        }[(button, down)]
        event = Quartz.CGEventCreateMouseEvent(None, event_type, (x, y), mouse_button)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def pointer_down(self, x: int, y: int, button: str) -> None:
        self._post_pointer_button(x, y, button, down=True)

    def pointer_up(self, x: int, y: int, button: str) -> None:
        self._post_pointer_button(x, y, button, down=False)

    def scroll_pointer(self, delta_x: int, delta_y: int) -> None:
        Quartz = self._Quartz
        event = Quartz.CGEventCreateScrollWheelEvent(
            None, Quartz.kCGScrollEventUnitPixel, 2, delta_y, delta_x
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def type_text(self, text: str) -> None:
        Quartz = self._Quartz
        for char in text:
            for key_down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, 0, key_down)
                Quartz.CGEventKeyboardSetUnicodeString(event, len(char), char)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def press_key(self, key: str, modifiers: tuple[str, ...] = ()) -> None:
        Quartz = self._Quartz
        keycode = _SPECIAL_KEYCODES[key]
        flags = 0
        for modifier in modifiers:
            flags |= {
                "command": Quartz.kCGEventFlagMaskCommand,
                "control": Quartz.kCGEventFlagMaskControl,
                "option": Quartz.kCGEventFlagMaskAlternate,
                "shift": Quartz.kCGEventFlagMaskShift,
            }[modifier]
        for key_down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, keycode, key_down)
            if flags:
                Quartz.CGEventSetFlags(event, flags)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


class _PynputInputBackend:
    """Backend real para Windows/Linux mediante el extra ``remote-control``."""

    def __init__(self) -> None:
        try:
            from pynput import keyboard, mouse  # type: ignore[import-not-found]

            self._keyboard_module = keyboard
            self._mouse_module = mouse
            self._keyboard = keyboard.Controller()
            self._mouse = mouse.Controller()
        except ImportError as exc:
            raise ActionError(
                "el control remoto en Windows/Linux requiere el extra 'remote-control'; "
                "instálalo con: pip install 'edecan-companion[remote-control]'"
            ) from exc
        except Exception as exc:
            raise ActionError(
                f"no se pudo iniciar el control de teclado/mouse: {exc}; "
                "verifica la sesión gráfica y sus permisos"
            ) from exc

    def _button(self, button: str) -> Any:
        return getattr(self._mouse_module.Button, button)

    def _key(self, key: str) -> Any:
        if len(key) == 1:
            return key
        aliases = {
            "escape": "esc",
            "arrow_up": "up",
            "arrow_down": "down",
            "arrow_left": "left",
            "arrow_right": "right",
            "delete_forward": "delete",
        }
        return getattr(self._keyboard_module.Key, aliases.get(key, key))

    def move_pointer(self, x: int, y: int) -> None:
        self._mouse.position = (x, y)

    def click_pointer(self, x: int, y: int, button: str) -> None:
        self.move_pointer(x, y)
        self._mouse.click(self._button(button), 1)

    def pointer_down(self, x: int, y: int, button: str) -> None:
        self.move_pointer(x, y)
        self._mouse.press(self._button(button))

    def pointer_up(self, x: int, y: int, button: str) -> None:
        self.move_pointer(x, y)
        self._mouse.release(self._button(button))

    def scroll_pointer(self, delta_x: int, delta_y: int) -> None:
        self._mouse.scroll(delta_x, delta_y)

    def type_text(self, text: str) -> None:
        self._keyboard.type(text)

    def press_key(self, key: str, modifiers: tuple[str, ...] = ()) -> None:
        modifier_aliases = {
            # ``command`` significa modificador primario del SO: Cmd en
            # macOS (Quartz) y Ctrl en Windows/Linux (pynput).
            "command": "ctrl",
            "control": "ctrl",
            "option": "alt",
            "shift": "shift",
        }
        held = [getattr(self._keyboard_module.Key, modifier_aliases[item]) for item in modifiers]
        try:
            for modifier in held:
                self._keyboard.press(modifier)
            resolved = self._key(key)
            self._keyboard.press(resolved)
            self._keyboard.release(resolved)
        finally:
            for modifier in reversed(held):
                self._keyboard.release(modifier)


def _get_input_backend() -> InputBackend:
    """Punto de extensión único para obtener el `InputBackend` a usar.

    Se construye uno NUEVO en cada llamada a propósito (no se cachea): el
    permiso de Accesibilidad puede concederse en cualquier momento mientras
    el companion sigue corriendo, y así se refleja de inmediato sin tener que
    reiniciar el proceso. Los tests monkeypatchean esta función entera
    (`monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)`) --
    mismo criterio que el resto del archivo monkeypatchea `subprocess.run`/
    `sys.platform`, así nunca construyen un `_QuartzInputBackend` real.
    """
    if sys.platform == "darwin":
        return _QuartzInputBackend()
    if sys.platform == "win32" or sys.platform.startswith("linux"):
        return _PynputInputBackend()
    raise ActionError("el control remoto de teclado/mouse no está soportado en esta plataforma")


def _input_pointer(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    """Control completo de puntero: movimiento, clics, drag y scroll.

    `button` (default `"left"`) elige qué botón usar para `click`/
    `double_click`; `right_click` siempre usa el botón derecho sin importar
    lo que traiga `button`. Todo gesto que no sea `move` primero MUEVE el
    puntero a `(x, y)` y luego hace clic ahí -- nunca asume que el puntero ya
    estaba en esa posición.
    """
    x = params.get("x")
    y = params.get("y")
    if not isinstance(x, int) or isinstance(x, bool):
        raise ActionError("falta o es inválido el parámetro 'x' (entero)")
    if not isinstance(y, int) or isinstance(y, bool):
        raise ActionError("falta o es inválido el parámetro 'y' (entero)")

    accion = params.get("accion")
    if accion not in _POINTER_ACTIONS:
        raise ActionError(f"'accion' inválida: {accion!r} (usa una de {_POINTER_ACTIONS})")

    button = params.get("button") or "left"
    if button not in _MOUSE_BUTTONS:
        raise ActionError(f"'button' inválido: {button!r} (usa una de {_MOUSE_BUTTONS})")
    if accion == "right_click":
        button = "right"

    delta_x = params.get("delta_x", 0)
    delta_y = params.get("delta_y", 0)
    start_x = params.get("start_x")
    start_y = params.get("start_y")
    if accion == "scroll":
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (delta_x, delta_y)):
            raise ActionError("'delta_x' y 'delta_y' deben ser enteros")
        if delta_x == 0 and delta_y == 0:
            raise ActionError("scroll necesita un delta_x o delta_y distinto de cero")
        delta_x = max(-2400, min(delta_x, 2400))
        delta_y = max(-2400, min(delta_y, 2400))
    if accion == "drag":
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (start_x, start_y)):
            raise ActionError("drag necesita 'start_x' y 'start_y' enteros")

    backend = _get_input_backend()
    if accion == "scroll":
        backend.move_pointer(x, y)
        backend.scroll_pointer(delta_x, delta_y)
    elif accion == "drag":
        backend.move_pointer(start_x, start_y)
        backend.pointer_down(start_x, start_y, button)
        # Interpolación acotada: suficiente para que ventanas/listas reconozcan
        # el drag sin convertir una sola petición en un stream ilimitado.
        for step in range(1, 13):
            px = round(start_x + (x - start_x) * step / 12)
            py = round(start_y + (y - start_y) * step / 12)
            backend.move_pointer(px, py)
        backend.pointer_up(x, y, button)
    else:
        backend.move_pointer(x, y)
    if accion in ("click", "double_click", "right_click"):
        backend.click_pointer(x, y, button)
        if accion == "double_click":
            backend.click_pointer(x, y, button)
    elif accion == "mouse_down":
        backend.pointer_down(x, y, button)
    elif accion == "mouse_up":
        backend.pointer_up(x, y, button)

    result = {"x": x, "y": y, "accion": accion, "button": button}
    if accion == "scroll":
        result.update({"delta_x": delta_x, "delta_y": delta_y})
    elif accion == "drag":
        result.update({"start_x": start_x, "start_y": start_y})
    return result


def _input_key(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    """`{texto? | tecla?: enter|tab|escape|backspace|arrow_*}` -- exactamente una de las dos.

    `texto` escribe cada carácter tal cual (Unicode, vía
    `CGEventKeyboardSetUnicodeString` -- no depende del layout de teclado);
    `tecla` sintetiza una tecla especial por su keycode virtual
    (`_SPECIAL_KEYCODES`). Enviar ambas, o ninguna, es un error de validación
    -- no hay una interpretación razonable de "las dos a la vez".
    """
    texto = params.get("texto")
    tecla = params.get("tecla")
    raw_modifiers = params.get("modifiers", [])
    if (texto is None) == (tecla is None):
        raise ActionError("envía exactamente uno de 'texto' o 'tecla' (no ambos, no ninguno)")

    # Valida TODO el parámetro primero, adquiere el backend (que puede fallar
    # por motivos ajenos al pedido -- falta Quartz, falta permiso de
    # Accesibilidad) recién al final: un 'tecla' inválido debe reportarse
    # como tal incluso en una máquina sin backend disponible.
    if texto is not None:
        if not isinstance(texto, str) or texto == "":
            raise ActionError("'texto' debe ser texto no vacío")
        backend = _get_input_backend()
        backend.type_text(texto)
        return {"tipo": "texto", "length": len(texto)}

    if tecla not in _SPECIAL_KEYS:
        raise ActionError(f"'tecla' inválida: {tecla!r} (usa una de {_SPECIAL_KEYS})")
    if not isinstance(raw_modifiers, list) or any(m not in _KEY_MODIFIERS for m in raw_modifiers):
        raise ActionError(f"'modifiers' inválido: usa solo valores de {_KEY_MODIFIERS}")
    modifiers = tuple(dict.fromkeys(raw_modifiers))
    backend = _get_input_backend()
    if modifiers:
        backend.press_key(tecla, modifiers)
    else:
        backend.press_key(tecla)
    result = {"tipo": "tecla", "tecla": tecla}
    if modifiers:
        result["modifiers"] = list(modifiers)
    return result


ACTIONS: dict[str, ActionHandler] = {
    "open_app": _open_app,
    "read_dir": _read_dir,
    "read_file": _read_file,
    "write_file": _write_file,
    "trash_path": _trash_path,
    "clipboard_get": _clipboard_get,
    "clipboard_set": _clipboard_set,
    "run_command": _run_command,
    "list_tree": _list_tree,
    "search_files": _search_files,
    "apply_edit": _apply_edit,
    "screenshot": _screenshot,
    "input_pointer": _input_pointer,
    "input_key": _input_key,
}


# ---------------------------------------------------------------------------
# Punto de entrada único
# ---------------------------------------------------------------------------


async def execute(
    action: str,
    params: dict[str, Any] | None,
    config: CompanionConfig,
    approver: Approver,
) -> dict[str, Any]:
    """Ejecuta `action` si está soportada y aprobada. Nunca lanza: siempre devuelve un dict.

    Devuelve `{"ok": True, "result": {...}}` o `{"ok": False, "error": "..."}`
    — `main.py` le agrega el `request_id` del mensaje original antes de
    devolverlo al servidor.
    """
    params = params if isinstance(params, dict) else {}
    handler = ACTIONS.get(action)

    if handler is None:
        logger.warning("Acción no soportada solicitada: %r", action)
        audit.log_action(
            action=action, params=params, approved=False, ok=False, log_path=config.audit_log_path
        )
        return {"ok": False, "error": f"acción no soportada: {action!r}"}

    if action in _IDE_ACTIONS and not config.ide_enabled:
        logger.info("Acción de IDE %r rechazada: ide_enabled=false en companion.yaml.", action)
        audit.log_action(
            action=action, params=params, approved=False, ok=False, log_path=config.audit_log_path
        )
        return {
            "ok": False,
            "error": (
                "el IDE está deshabilitado en este companion (ide_enabled=false en companion.yaml)"
            ),
        }

    if action in _INPUT_ACTIONS and not config.remote_input_enabled:
        logger.info(
            "Acción de control remoto %r rechazada: remote_input_enabled=false en companion.yaml.",
            action,
        )
        audit.log_action(
            action=action, params=params, approved=False, ok=False, log_path=config.audit_log_path
        )
        return {
            "ok": False,
            "error": (
                "el control remoto de teclado/mouse está deshabilitado en este companion "
                "(remote_input_enabled=false en companion.yaml)"
            ),
        }

    try:
        approved = bool(await approver(action, params, config))
    except Exception:
        logger.exception(
            "El approver falló evaluando la acción %r; se rechaza por seguridad.", action
        )
        approved = False

    if not approved:
        audit.log_action(
            action=action, params=params, approved=False, ok=False, log_path=config.audit_log_path
        )
        return {"ok": False, "error": "acción rechazada (sin aprobación del usuario)"}

    try:
        result = await asyncio.to_thread(handler, params, config)
    except ActionError as exc:
        audit.log_action(
            action=action, params=params, approved=True, ok=False, log_path=config.audit_log_path
        )
        return {"ok": False, "error": str(exc)}
    except Exception:
        logger.exception("Error inesperado ejecutando la acción %r", action)
        audit.log_action(
            action=action, params=params, approved=True, ok=False, log_path=config.audit_log_path
        )
        return {"ok": False, "error": "error interno del companion ejecutando la acción"}

    audit.log_action(
        action=action, params=params, approved=True, ok=True, log_path=config.audit_log_path
    )
    return {"ok": True, "result": result}
