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
import importlib.util
import io
import json
import logging
import math
import mimetypes
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from edecan_companion import audit, linux_session
from edecan_companion.config import CompanionConfig
from edecan_companion.personal_apps import PERSONAL_APP_ACTIONS, PersonalAppError

logger = logging.getLogger(__name__)

MAX_READ_FILE_BYTES = 256 * 1024
# Tope de un archivo transferido entre el teléfono y esta computadora
# (`transfer_*`). El contenido viaja en base64 dentro del JSON del comando, así
# que este límite acota tanto el cuerpo HTTP del API como el mensaje del
# WebSocket del companion. 10 MiB (base64 ≈ 13.3 MiB) se elige a propósito por
# DEBAJO del `ws_max_size` por defecto de uvicorn (16 MiB) en el servidor, para
# que el `transfer_pull` (companion → servidor) no supere ese límite del
# transporte; el companion standalone sube su propio `max_size` de recepción
# para el `transfer_push` en sentido inverso (ver `edecan_companion.main`).
MAX_TRANSFER_BYTES = 10 * 1024 * 1024
MAX_TRANSFER_LIST = 500
MAX_COMMAND_OUTPUT_BYTES = 10 * 1024
COMMAND_TIMEOUT_SECONDS = 30
HELPER_SUBPROCESS_TIMEOUT_SECONDS = 15
DESKTOP_BRIDGE_MAX_RESPONSE_BYTES = 64 * 1024 * 1024

# `open_app` en Linux lanza con `Popen` sin esperar (para no colgarse con
# apps gráficas de larga vida -- ver el comentario junto a esa llamada). Ese
# `Popen` fire-and-forget, medido en vivo (edecan-prod, 1-ago-2026), reportaba
# `launched: True` incluso cuando el proceso moría al instante (`Exec=`
# apuntando a un binario que sale con código != 0, o un `$DISPLAY` inválido):
# un fallo en silencio de manual. Esta ventana corta es un compromiso, no una
# garantía -- si el proceso sigue vivo pasados estos ms se reporta como
# lanzado con éxito, aunque falle más tarde; el objetivo es solo atrapar el
# caso medido de "murió antes de terminar de arrancar".
LINUX_OPEN_APP_POLL_SECONDS = 0.3

# `screenshot`/`input_pointer`/`input_key` en Windows/Linux dependen del
# extra opcional `remote-control` (`mss`/`pynput`/`Pillow`, ver
# `pyproject.toml`). `pip install 'edecan-companion[remote-control]'` -- lo
# que decía este mensaje antes -- NO funciona si se instaló desde PyPI: este
# paquete no está publicado en ningún índice (medido en vivo, edecan-prod,
# 1-ago-2026: `uv pip install --dry-run` da "no solution found"). Instalar
# los paquetes directo sí funciona (medido igual, en el mismo servidor).
# Sin sujeto a propósito -- cada llamador antepone el suyo
# (f"la captura en Windows/Linux {_LINUX_REMOTE_CONTROL_INSTALL_HINT}",
# f"el control remoto en Windows/Linux {_LINUX_REMOTE_CONTROL_INSTALL_HINT}").
_LINUX_REMOTE_CONTROL_INSTALL_HINT = (
    "necesita `mss`, `pynput` y `Pillow`. `pip install 'edecan-companion[remote-control]'` "
    "no funciona si instalaste desde PyPI (el paquete no está publicado en ningún "
    "índice); instala los paquetes directo: pip install 'mss>=10.0' 'pynput>=1.7.7' "
    "'Pillow>=10.4' (o `uv pip install -e 'apps/companion[remote-control]'` desde un "
    "clon editable de este repo)"
)

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
_PERSONAL_MESSAGE_ACTIONS = frozenset({"mac_mail_send", "mac_messages_send"})

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


def _resolve_in_sandbox(
    config: CompanionConfig, raw_path: str | None, root: Path | None = None
) -> Path:
    """Resuelve `raw_path` dentro de `root` (default: `config.sandbox_dir`);
    lanza `ActionError` si escapa.

    `root` es la carpeta efectiva de confinamiento: `config.sandbox_dir` para
    el uso histórico, o la carpeta `workspace_scope` de un agente cuando la
    tool `usar_computadora` la inyecta como `params["workspace_root"]`. En
    ambos casos `raw_path` se trata como relativo a esa raíz (se descarta
    cualquier apariencia de ruta absoluta) y se resuelve siguiendo enlaces
    simbólicos (`Path.resolve`), así que tanto un "../.." como un symlink
    que apunte fuera terminan rechazados por el chequeo final de
    `relative_to`.
    """
    root = root if root is not None else config.sandbox_dir
    raw_path = (raw_path or ".").strip() or "."

    # Nunca interpretar el path del usuario como absoluto: siempre relativo
    # al root, aunque venga con "/" al inicio.
    relative = raw_path.replace("\\", "/").lstrip("/")
    candidate = (root / relative).resolve()

    try:
        candidate.relative_to(root)
    except ValueError:
        raise ActionError(f"ruta fuera del sandbox permitido: {raw_path!r}") from None

    return candidate


def _is_within_sandbox(path: Path, config: CompanionConfig, root: Path | None = None) -> bool:
    """`True` si `path` (resolviendo symlinks) sigue dentro de `root` (default:
    `config.sandbox_dir`).

    A diferencia de `_resolve_in_sandbox` (que valida una ruta *pedida* por
    el asistente, y lanza `ActionError` si escapa), esto valida en silencio
    rutas *descubiertas* al recorrer el sandbox (`list_tree`/`search_files`):
    un symlink a una carpeta o archivo de fuera del sandbox no debe ni
    recorrerse ni leerse, aunque su nombre en sí ya se podía listar antes
    (mismo comportamiento que `read_dir`, que nunca revisó esto para sus
    entradas directas).
    """
    root = root if root is not None else config.sandbox_dir
    try:
        path.resolve().relative_to(root)
    except (OSError, RuntimeError, ValueError):
        # ValueError: resuelve pero cae fuera del sandbox. OSError/RuntimeError:
        # símlink roto o loop de símlinks -- en cualquier caso, no es seguro.
        return False
    return True


def _sandbox_root(config: CompanionConfig, params: dict[str, Any]) -> Path:
    """Raíz de confinamiento efectiva para acciones de archivos/terminal.

    `params["workspace_root"]` es el `workspace_scope` del agente, inyectado
    por la tool `usar_computadora` (que lo fija server-side y descarta
    cualquier valor del modelo). Si no viene, se conserva el comportamiento
    histórico: `config.sandbox_dir` (la máquina del dueño).
    """
    override = params.get("workspace_root")
    if isinstance(override, str) and override.strip():
        return Path(os.path.realpath(os.path.expanduser(override.strip())))
    return config.sandbox_dir


# ---------------------------------------------------------------------------
# Handlers — síncronos y bloqueantes a propósito (se corren con asyncio.to_thread)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# `open_app` en Linux -- resolución de `.desktop`, ver docstring de _open_app.
#
# `xdg-open <nombre-de-app>` (lo que este módulo usaba antes) está pensado
# para abrir un ARCHIVO/URL/mimetype, no un nombre de aplicación: medido en
# vivo (edecan-prod, 1-ago-2026), con la acción real del companion se
# quedaba colgado exactamente `HELPER_SUBPROCESS_TIMEOUT_SECONDS` y además
# dejaba un diálogo `exo-open` huérfano en el escritorio del dueño en CADA
# intento (4/4 apps probadas). La acción estaba muerta para su uso previsto.
#
# La solución no es cambiar a `gtk-launch`: ese comando resuelve el `.desktop`
# usando `XDG_DATA_DIRS`, y esa variable de la sesión gráfica real medida en
# el servidor (`/usr/local/share:/usr/share`) NO incluye el directorio de
# snap (`/var/lib/snapd/desktop/applications`) -- ahí vive el Firefox de esa
# máquina. En vez de depender de esa variable, este módulo busca el
# `.desktop` él mismo en una lista fija de carpetas (que SÍ incluye snap y
# flatpak) y ejecuta directamente su `Exec=`, sin pasar por `gtk-launch` ni
# por `xdg-open`.
_LINUX_DESKTOP_APP_DIRS: tuple[Path, ...] = (
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path.home() / ".local/share/applications",
    Path("/var/lib/snapd/desktop/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local/share/flatpak/exports/share/applications",
)

# Códigos de campo del `Exec=` de un `.desktop` (freedesktop.org, "Desktop
# Entry Specification" §Exec): se sustituyen por archivos/URLs que el
# invocador pasaría, o metadatos del propio lanzador. `open_app` no abre con
# ningún archivo/URL asociado -- solo lanza la app -- así que se descartan.
_DESKTOP_EXEC_FIELD_CODES = frozenset({"%f", "%F", "%u", "%U", "%c", "%i", "%k", "%v", "%m"})


def _linux_desktop_search_dirs() -> list[Path]:
    """Carpetas donde buscar `.desktop`, en orden: `XDG_DATA_DIRS` de la
    sesión primero (respeta lo que el entorno real anuncie), luego la lista
    fija de arriba -- que incluye snap/flatpak SIN depender de que esa
    variable las traiga (medido: en esta máquina no las trae)."""
    vistas: set[Path] = set()
    carpetas: list[Path] = []
    xdg = os.environ.get("XDG_DATA_DIRS", "")
    candidatas = [Path(parte) / "applications" for parte in xdg.split(":") if parte.strip()]
    candidatas += list(_LINUX_DESKTOP_APP_DIRS)
    for carpeta in candidatas:
        if carpeta not in vistas:
            vistas.add(carpeta)
            carpetas.append(carpeta)
    return carpetas


def _parse_desktop_entry(path: Path) -> dict[str, str] | None:
    """Claves de la sección `[Desktop Entry]` de un `.desktop` (solo lo que
    hace falta: `Name`/`Exec`/`TryExec`). `None` si no se pudo leer."""
    try:
        crudo = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    en_seccion = False
    valores: dict[str, str] = {}
    for linea in crudo.splitlines():
        linea = linea.strip()
        if linea.startswith("["):
            en_seccion = linea == "[Desktop Entry]"
            continue
        if not en_seccion or not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        valores.setdefault(clave.strip(), valor.strip())
    return valores or None


def _find_desktop_entry(app: str) -> tuple[Path, dict[str, str]] | None:
    """Busca un `.desktop` por nombre de archivo (`app.desktop`, con las
    variantes típicas de mayúsculas/espacios) y, si ninguno calza, por su
    campo `Name=` -- así sirve tanto un id técnico (`xfce4-terminal`,
    `firefox_firefox`) como el nombre visible que alguien copiaría del menú
    (`Visual Studio Code`)."""
    candidatos_stem = {app, app.lower(), app.replace(" ", "-").lower()}
    por_nombre: tuple[Path, dict[str, str]] | None = None
    for carpeta in _linux_desktop_search_dirs():
        if not carpeta.is_dir():
            continue
        try:
            entradas = sorted(carpeta.glob("*.desktop"))
        except OSError:
            continue
        for entrada in entradas:
            if entrada.stem in candidatos_stem or entrada.stem.lower() in candidatos_stem:
                valores = _parse_desktop_entry(entrada)
                if valores:
                    return entrada, valores
        if por_nombre is None:
            for entrada in entradas:
                valores = _parse_desktop_entry(entrada)
                if valores and valores.get("Name", "").strip().lower() == app.lower():
                    por_nombre = (entrada, valores)
    return por_nombre


def _desktop_exec_argv(exec_line: str) -> list[str]:
    """`Exec=` de un `.desktop` sin los códigos de campo (ver
    `_DESKTOP_EXEC_FIELD_CODES`), listo para `subprocess`. `shlex.split`
    entiende comillas y el patrón `env VAR=valor programa` que usan varios
    lanzadores de snap."""
    return [token for token in shlex.split(exec_line) if token not in _DESKTOP_EXEC_FIELD_CODES]


def _open_app(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    app = params.get("app")
    if not isinstance(app, str) or not app.strip():
        raise ActionError("falta el parámetro 'app' (texto)")
    app = app.strip()

    if not config.allow_all_apps and app not in config.allowed_apps:
        raise ActionError(f"app no permitida (agrégala a allowed_apps en companion.yaml): {app!r}")

    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["open", "-a", app],
                check=True,
                timeout=HELPER_SUBPROCESS_TIMEOUT_SECONDS,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise ActionError(f"no se encontró el comando del sistema: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ActionError("se agotó el tiempo de espera abriendo la app") from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() if exc.stderr else str(exc)
            raise ActionError(f"no se pudo abrir {app!r}: {detail}") from exc
        return {"app": app, "launched": True}

    if not sys.platform.startswith("linux"):
        raise ActionError(f"abrir apps no está soportado en esta plataforma: {sys.platform!r}")

    encontrado = _find_desktop_entry(app)
    if encontrado is None:
        buscadas = ", ".join(str(carpeta) for carpeta in _linux_desktop_search_dirs())
        raise ActionError(
            f"no encontré un lanzador (.desktop) para {app!r}. Usa el nombre exacto del "
            f"archivo .desktop (sin la extensión, p. ej. 'firefox' o 'code') o el nombre "
            f"visible tal como aparece en el menú (p. ej. 'Visual Studio Code'); revisa que "
            f"exista en alguna de estas carpetas: {buscadas}"
        )
    desktop_path, valores = encontrado
    exec_line = valores.get("Exec")
    if not exec_line:
        raise ActionError(f"el lanzador {desktop_path} no tiene una clave 'Exec='")
    argv = _desktop_exec_argv(exec_line)
    if not argv:
        raise ActionError(f"el lanzador {desktop_path} tiene un 'Exec=' vacío tras limpiarlo")

    # Lanzamiento SIN esperar (`Popen`, no `run`): estas son apps gráficas de
    # larga vida, no comandos de una sola pasada -- esperar su salida es
    # exactamente el bug que tenía `xdg-open` (colgarse hasta el timeout).
    # `env=entorno_fusionado()` rellena DISPLAY/WAYLAND_DISPLAY/DBUS si el
    # proceso del companion no los tiene (systemd sin sesión gráfica
    # heredada, ver `linux_session.py`) sin pisar lo que ya esté puesto.
    #
    # Pero un `Popen` puramente fire-and-forget es un fallo en silencio:
    # medido en vivo (edecan-prod, 1-ago-2026) con `Exec=/bin/false` y con un
    # `$DISPLAY` inválido, el proceso moría al instante y esto igual
    # devolvía `launched: True`. `stderr` va a un archivo temporal (nunca a
    # `PIPE` -- la misma trampa del portapapeles: si el proceso se
    # demoniza, un `PIPE` sin leer puede colgar) y, tras una ventana corta,
    # se comprueba si ya murió. No es una garantía (nada impide que falle
    # medio segundo después), pero atrapa el caso medido.
    try:
        with tempfile.TemporaryFile() as stderr_file:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
                start_new_session=True,
                cwd=str(Path.home()),
                env=linux_session.entorno_fusionado(),
            )
            time.sleep(LINUX_OPEN_APP_POLL_SECONDS)
            returncode = proc.poll()
            if returncode is not None and returncode != 0:
                stderr_file.seek(0)
                detail = stderr_file.read().decode("utf-8", errors="replace").strip()
                mensaje = f"{app!r} se lanzó pero terminó de inmediato con código {returncode}"
                mensaje += (
                    f": {detail}"
                    if detail
                    else " (sin salida de error, revisa que $DISPLAY/$WAYLAND_DISPLAY sean válidos)"
                )
                raise ActionError(mensaje)
    except FileNotFoundError as exc:
        raise ActionError(
            f"el ejecutable de {desktop_path} no existe o no está en PATH: {exc}"
        ) from exc
    except OSError as exc:
        raise ActionError(f"no se pudo lanzar {app!r}: {exc}") from exc

    return {"app": app, "launched": True}


_APPLESCRIPT_REUTILIZAR_TAB = """
on run argv
    set theURL to item 1 of argv
    set theHost to item 2 of argv
    tell application "Google Chrome"
        if (count of windows) is 0 then
            make new window
            set URL of active tab of front window to theURL
            return "new-window"
        end if
        set wi to 0
        repeat with w in windows
            set wi to wi + 1
            set ti to 0
            repeat with t in tabs of w
                set ti to ti + 1
                if (URL of t) contains theHost then
                    set URL of t to theURL
                    set index of w to 1
                    set active tab index of w to ti
                    return "reused"
                end if
            end repeat
        end repeat
        tell front window to make new tab with properties {URL:theURL}
        set index of front window to 1
        return "new-tab"
    end tell
end run
"""


def _open_url(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    url = params.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ActionError("falta el parámetro 'url' (texto)")
    url = url.strip()
    if not url.startswith(("http://", "https://", "mailto:", "tel:")):
        raise ActionError("solo abro URLs http(s), mailto o tel")

    if sys.platform == "darwin":
        # Chrome acumulaba UNA PESTAÑA NUEVA por cada visita (el scan de vida
        # digital deja 20+ pestañas de LinkedIn). Reutilizar: si ya hay una
        # pestaña del MISMO sitio, navegarla a la URL y traerla al frente.
        # Si AppleScript falla (sin Chrome, permiso de automatización negado),
        # cae al `open` clásico — comportamiento viejo, nunca peor.
        try:
            host = urlparse(url).netloc.removeprefix("www.")
            if host and url.startswith("http"):
                resultado_tab = subprocess.run(
                    ["osascript", "-", url, host],
                    input=_APPLESCRIPT_REUTILIZAR_TAB,
                    check=False,
                    timeout=HELPER_SUBPROCESS_TIMEOUT_SECONDS,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if resultado_tab.returncode == 0 and resultado_tab.stdout.strip():
                    return {"url": url, "launched": True, "modo": resultado_tab.stdout.strip()}
                raise ActionError(resultado_tab.stderr.strip()[:200] or "osascript falló")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ActionError):
            pass  # cae al `open` clásico
        argv = ["open", url]
    elif sys.platform.startswith("linux"):
        argv = ["xdg-open", url]
    else:
        raise ActionError(f"abrir URLs no está soportado en esta plataforma: {sys.platform!r}")

    try:
        subprocess.run(
            argv,
            check=True,
            timeout=HELPER_SUBPROCESS_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise ActionError(f"no se encontró el comando del sistema: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ActionError("se agotó el tiempo de espera abriendo la URL") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise ActionError(f"no se pudo abrir {url!r}: {detail}") from exc
    return {"url": url, "launched": True}


def _read_dir(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    root = _sandbox_root(config, params)
    target = _resolve_in_sandbox(config, params.get("path"), root)
    if not target.exists():
        raise ActionError(f"no existe: {target.relative_to(root)}")
    if not target.is_dir():
        raise ActionError(f"no es una carpeta: {target.relative_to(root)}")

    entries: list[dict[str, Any]] = []
    for entry in sorted(target.iterdir(), key=lambda p: p.name):
        try:
            is_dir = entry.is_dir()
            size = None if is_dir else entry.stat().st_size
        except OSError:
            continue  # entrada ilegible (p. ej. symlink roto): se omite, no se aborta el listado
        entries.append({"name": entry.name, "is_dir": is_dir, "size_bytes": size})

    return {"path": str(target.relative_to(root)), "entries": entries}


def _read_file(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    root = _sandbox_root(config, params)
    target = _resolve_in_sandbox(config, params.get("path"), root)
    if not target.exists() or not target.is_file():
        raise ActionError(f"no existe el archivo: {target.relative_to(root)}")

    size = target.stat().st_size
    if size > MAX_READ_FILE_BYTES:
        raise ActionError(f"archivo demasiado grande ({size} bytes; máximo {MAX_READ_FILE_BYTES})")

    raw = target.read_bytes()
    try:
        content, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        content, encoding = base64.b64encode(raw).decode("ascii"), "base64"

    return {
        "path": str(target.relative_to(root)),
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

    root = _sandbox_root(config, params)
    target = _resolve_in_sandbox(config, params.get("path"), root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    return {"path": str(target.relative_to(root)), "bytes_written": len(data)}


def _trash_path(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    """Mueve una ruta del sandbox a la papelera recuperable."""
    root = _sandbox_root(config, params)
    target = _resolve_in_sandbox(config, params.get("path"), root)
    if target == root:
        raise ActionError("no se puede enviar a la papelera la raíz completa del sandbox")
    if not target.exists():
        raise ActionError(f"no existe: {target.relative_to(root)}")
    try:
        from send2trash import send2trash

        send2trash(str(target))
    except OSError as exc:
        raise ActionError(f"no se pudo mover a la papelera: {exc}") from exc
    return {"path": str(target.relative_to(root)), "trashed": True}


# ---------------------------------------------------------------------------
# Portapapeles
# ---------------------------------------------------------------------------
#
# Wayland NO habla el protocolo de selección de X11 que usa `xclip` -- hace
# falta `wl-clipboard` (`wl-copy`/`wl-paste`). Se decide por `WAYLAND_DISPLAY`
# (la misma señal que usa el propio `wl-clipboard` para elegir backend), no
# por "cuál binario está instalado": así, si falta la herramienta, el mensaje
# apunta a la correcta para ESTA sesión y no a la del otro protocolo.
#
# `env` recibe el entorno YA FUSIONADO con `linux_session.entorno_fusionado()`
# -- si se decide leyendo `os.environ` a secas (como hacía esto antes de
# medir el hallazgo en vivo del 1-ago-2026), un companion sin `WAYLAND_DISPLAY`
# heredado (systemd) SIEMPRE elige `xclip`, incluso en una sesión Wayland
# real cuya variable sí está descubrible vía `/proc`. El llamador pasa el
# mismo entorno fusionado también como `env=` del subprocess, para que la
# herramienta elegida y el entorno con el que corre sean siempre coherentes.


def _linux_clipboard_argv(*, leer: bool, env: dict[str, str] | None = None) -> list[str]:
    entorno = env if env is not None else linux_session.entorno_fusionado()
    if entorno.get("WAYLAND_DISPLAY"):
        return ["wl-paste", "--no-newline"] if leer else ["wl-copy"]
    if leer:
        return ["xclip", "-selection", "clipboard", "-o"]
    return ["xclip", "-selection", "clipboard"]


def _linux_clipboard_missing_tool_error(binario: str, exc: Exception) -> ActionError:
    paquete = "wl-clipboard" if binario in {"wl-copy", "wl-paste"} else "xclip"
    return ActionError(
        f"no se encontró {binario!r}; instálalo con `sudo apt install {paquete}` "
        f"(Debian/Ubuntu), `sudo dnf install {paquete}` (Fedora) o el equivalente de tu "
        f"distro ({exc})"
    )


def _clipboard_get(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    entorno_linux: dict[str, str] | None = None
    if sys.platform == "darwin":
        argv = ["pbpaste"]
    elif sys.platform.startswith("linux"):
        # Mismo entorno fusionado (ver `linux_session.py`) para elegir la
        # herramienta (X11 vs Wayland) Y para correrla -- si el companion
        # corre sin `$DISPLAY`/`$WAYLAND_DISPLAY` heredados (systemd), esto
        # es lo que hace que xclip/wl-copy encuentren la sesión gráfica real.
        entorno_linux = linux_session.entorno_fusionado()
        argv = _linux_clipboard_argv(leer=True, env=entorno_linux)
    else:
        raise ActionError(f"portapapeles no soportado en esta plataforma: {sys.platform!r}")

    try:
        proc = subprocess.run(
            argv,
            check=True,
            timeout=HELPER_SUBPROCESS_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=entorno_linux,
        )
    except FileNotFoundError as exc:
        if sys.platform.startswith("linux"):
            raise _linux_clipboard_missing_tool_error(argv[0], exc) from exc
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

    entorno_linux: dict[str, str] | None = None
    if sys.platform == "darwin":
        argv = ["pbcopy"]
    elif sys.platform.startswith("linux"):
        # Mismo entorno fusionado que `_clipboard_get` -- ver el comentario
        # ahí. Sin esto, bajo systemd (sin `$DISPLAY`/`$WAYLAND_DISPLAY`
        # heredados) `_linux_clipboard_argv` elegía siempre `xclip` aunque la
        # sesión fuera Wayland, y `xclip`/`wl-copy` fallaban con "Can't open
        # display: (null)" pese a que la sesión gráfica real sí existía y
        # era descubrible vía `/proc` (medido en vivo, 1-ago-2026).
        entorno_linux = linux_session.entorno_fusionado()
        argv = _linux_clipboard_argv(leer=False, env=entorno_linux)
    else:
        raise ActionError(f"portapapeles no soportado en esta plataforma: {sys.platform!r}")

    if sys.platform.startswith("linux"):
        # `xclip`/`wl-copy` se demonizan tras copiar (X11/Wayland exigen que
        # alguien siga vivo para SERVIR la selección después de que el
        # proceso original termine). Ese demonio nace con un `fork()`, no un
        # `exec()`, así que hereda TAL CUAL los descriptores de las tuberías
        # de stdout/stderr que abriría `capture_output=True` -- y como sigue
        # vivo de fondo, esas tuberías nunca ven EOF de todos sus dueños.
        # Resultado medido en vivo (edecan-prod, 1-ago-2026): el texto SÍ se
        # copiaba, pero `subprocess.run` se quedaba esperando el `read()`
        # hasta agotar el timeout completo y reportaba error igual -- un
        # "falla" que en realidad había tenido éxito. La única forma de que
        # esto no cuelgue es no crear tuberías: stdout va a `/dev/null` (no
        # hace falta) y stderr a un archivo real (si acaso hay que explicar
        # un fallo real, no un timeout fantasma).
        try:
            with tempfile.TemporaryFile() as stderr_file:
                try:
                    subprocess.run(
                        argv,
                        input=text,
                        check=True,
                        timeout=HELPER_SUBPROCESS_TIMEOUT_SECONDS,
                        stdout=subprocess.DEVNULL,
                        stderr=stderr_file,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=entorno_linux,
                    )
                except subprocess.CalledProcessError as exc:
                    stderr_file.seek(0)
                    detail = stderr_file.read().decode("utf-8", errors="replace").strip()
                    raise ActionError(
                        f"no se pudo escribir el portapapeles: {detail or exc}"
                    ) from exc
        except FileNotFoundError as exc:
            raise _linux_clipboard_missing_tool_error(argv[0], exc) from exc
        except subprocess.TimeoutExpired as exc:
            raise ActionError("se agotó el tiempo de espera escribiendo el portapapeles") from exc
        return {"written_chars": len(text)}

    try:
        subprocess.run(
            argv,
            input=text,
            check=True,
            timeout=HELPER_SUBPROCESS_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
# Transferencia de archivos (buzón compartido `config.transfer_dir`)
# ---------------------------------------------------------------------------


def _transfer_dir(config: CompanionConfig) -> Path:
    """Carpeta buzón, creada al primer uso con permisos `0700`.

    Es una carpeta visible del usuario (`~/Edecán/Compartidos` por defecto),
    NO el sandbox del IDE: aquí aterrizan los archivos que manda el teléfono y
    de aquí puede recuperar los que el dueño deje. Se crea perezosamente para
    no sembrar carpetas vacías en equipos que nunca usan la función.
    """
    target = config.transfer_dir
    target.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(target, 0o700)
    return target


def _safe_transfer_name(raw: Any) -> str:
    """Reduce `raw` a un nombre de archivo seguro DENTRO de `transfer_dir`.

    Toma solo el *basename* (`os.path.basename`, se queda con lo que sigue al
    último separador en cualquier plataforma) y rechaza lo que no sea un
    nombre normal: vacío, `.`/`..`, o con separadores/NUL tras el basename.
    Así un `name` malicioso como `../../.ssh/authorized_keys` colapsa a
    `authorized_keys` y jamás escapa del buzón.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ActionError("falta el parámetro 'name' (nombre de archivo)")
    name = os.path.basename(raw.replace("\\", "/")).strip()
    if not name or name in {".", ".."} or "/" in name or "\0" in name:
        raise ActionError(f"nombre de archivo inválido: {raw!r}")
    if len(name) > 255:
        raise ActionError("el nombre de archivo es demasiado largo (máx. 255 caracteres)")
    return name


def _resolve_in_transfer(name: str, config: CompanionConfig) -> Path:
    """Ruta absoluta de `name` dentro de `transfer_dir`, verificada sin fugas.

    `transfer_dir` ya llega "real" (sin symlinks, `config.load_config`); se
    resuelve el candidato y se confirma que sigue colgando de esa raíz —
    misma invariante que `_resolve_in_sandbox` para el IDE.
    """
    base = _transfer_dir(config)
    candidate = Path(os.path.realpath(base / name))
    if candidate != base and base not in candidate.parents:
        raise ActionError(f"nombre de archivo inválido: {name!r}")
    return candidate


def _create_unique_transfer_file(name: str, content: bytes, config: CompanionConfig) -> Path:
    """Escribe `content` en un archivo NUEVO del buzón sin pisar ninguno existente.

    Si `foto.png` ya existe, prueba `foto (2).png`, `foto (3).png`, … Usa
    `O_CREAT|O_EXCL` (creación atómica) en vez de un `exists()` seguido de
    `write`, así dos `transfer_push` concurrentes del mismo nombre (posible en
    la app de escritorio, donde el bridge corre en paralelo por request) nunca
    calculan el mismo "nombre libre" y se pisan — quien pierde la carrera
    simplemente prueba el siguiente número.
    """
    base = _transfer_dir(config)
    stem, suffix = os.path.splitext(name)
    counter = 1
    while True:
        nombre = name if counter == 1 else f"{stem} ({counter}){suffix}"
        destino = base / nombre
        try:
            fd = os.open(destino, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            counter += 1
            continue
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
        except OSError:
            with contextlib.suppress(OSError):
                destino.unlink()
            raise
        return destino


def _transfer_push(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    """Teléfono → computadora: guarda un archivo en el buzón compartido.

    `params`: `{name, content_b64}`. Devuelve `{name, path, bytes}` con el
    nombre FINAL (puede diferir del pedido si hubo colisión) y la ruta
    absoluta para que el teléfono muestre "Guardado en …".
    """
    name = _safe_transfer_name(params.get("name"))
    encoded = params.get("content_b64")
    if not isinstance(encoded, str) or not encoded:
        raise ActionError("falta el parámetro 'content_b64' (contenido en base64)")
    # Cota barata ANTES de decodificar: base64 infla ~4/3, así que un cuerpo
    # de >4/3*MAX ya excede el tope sin gastar memoria decodificándolo.
    if len(encoded) > (MAX_TRANSFER_BYTES // 3) * 4 + 4:
        raise ActionError(
            f"el archivo supera el máximo de {MAX_TRANSFER_BYTES // (1024 * 1024)} MiB"
        )
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ActionError(f"contenido en base64 inválido: {exc}") from exc
    if len(content) > MAX_TRANSFER_BYTES:
        raise ActionError(
            f"el archivo supera el máximo de {MAX_TRANSFER_BYTES // (1024 * 1024)} MiB"
        )

    try:
        destino = _create_unique_transfer_file(name, content, config)
    except OSError as exc:
        raise ActionError(f"no se pudo guardar el archivo: {exc}") from exc
    return {"name": destino.name, "path": str(destino), "bytes": len(content)}


def _transfer_list(_params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    """Lista los archivos del buzón compartido (para que el teléfono elija).

    Devuelve `{files: [{name, bytes, modified}]}`, más recientes primero, solo
    archivos regulares (nunca carpetas ni symlinks a carpetas), acotado a
    `MAX_TRANSFER_LIST` para no volcar un directorio enorme por el canal.
    """
    base = _transfer_dir(config)
    entradas: list[dict[str, Any]] = []
    try:
        for entry in os.scandir(base):
            if not entry.is_file(follow_symlinks=False):
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            entradas.append({"name": entry.name, "bytes": stat.st_size, "modified": stat.st_mtime})
    except OSError as exc:
        raise ActionError(f"no se pudo leer la carpeta compartida: {exc}") from exc
    entradas.sort(key=lambda item: item["modified"], reverse=True)
    return {"files": entradas[:MAX_TRANSFER_LIST], "dir": str(base)}


def _transfer_pull(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    """Computadora → teléfono: entrega un archivo del buzón por su nombre.

    `params`: `{name}` (un nombre del buzón, NUNCA una ruta arbitraria).
    Devuelve `{name, content_b64, bytes, mime}`.
    """
    name = _safe_transfer_name(params.get("name"))
    ruta = _resolve_in_transfer(name, config)
    if not ruta.is_file():
        raise ActionError(f"no existe ese archivo en la carpeta compartida: {name!r}")
    try:
        size = ruta.stat().st_size
    except OSError as exc:
        raise ActionError(f"no se pudo leer el archivo: {exc}") from exc
    if size > MAX_TRANSFER_BYTES:
        raise ActionError(
            f"el archivo supera el máximo de {MAX_TRANSFER_BYTES // (1024 * 1024)} MiB"
        )
    try:
        content = ruta.read_bytes()
    except OSError as exc:
        raise ActionError(f"no se pudo leer el archivo: {exc}") from exc
    mime = mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
    return {
        "name": ruta.name,
        "content_b64": base64.b64encode(content).decode("ascii"),
        "bytes": len(content),
        "mime": mime,
    }


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------


def _truncate_utf8(text: str, limit_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit_bytes:
        return text, False
    return encoded[:limit_bytes].decode("utf-8", errors="ignore"), True


def _split_command(command: str) -> list[str]:
    """Parte `command` en argv, respetando comillas igual que una shell POSIX.

    `shlex.split` en modo POSIX (el único que tiene) trata `\\` como escape,
    así que en Windows `shlex.split(r"notepad C:\\Users\\a.txt")` devolvía
    `["notepad", "C:Usersa.txt"]` -- la ruta quedaba destrozada porque ahí
    `\\` es separador de carpeta, no escape de shell. Se duplica cada `\\`
    ANTES de partir solo en Windows: eso neutraliza el escape de `shlex` y
    conserva las barras invertidas literalmente, sin tocar cómo se resuelven
    las comillas (que sí siguen funcionando igual en ambas plataformas).
    """
    if sys.platform == "win32":
        command = command.replace("\\", "\\\\")
    return shlex.split(command)


def _argv_para_windows(argv: list[str]) -> list[str]:
    """En Windows, ``subprocess.run(argv, shell=False)`` usa ``CreateProcess``
    directamente -- que a diferencia de una shell NO busca en ``PATHEXT`` ni
    sabe lanzar un guion por lotes (``.cmd``/``.bat``, el shim típico de
    herramientas instaladas vía npm: ``npm``, ``npx``, ``tsc``, ``eslint``...)
    aunque ``shutil.which`` sí lo encuentre. Es EXACTAMENTE el mismo bug ya
    medido y documentado en ``edecan_mcp.transport._argv_ejecutable`` y en
    ``ide_opencode_binario._argv_para_ejecutar`` -- ver esos módulos para la
    cita completa del ``FileNotFoundError [WinError 2]`` medido en vivo.
    Mismo criterio aplicado acá: resolver con ``shutil.which`` (si acierta,
    devuelve la ruta real, incluida su extensión) y, si esa ruta es un
    guion por lotes, delegar en ``cmd.exe /c`` con el argv todavía separado
    -- JAMÁS ``shell=True`` (ver el docstring del módulo: "``run_command``
    ... siempre corre con `shell=False`").

    Un comando que no resuelve a ningún archivo real (los builtins de la
    shell de Windows, como ``echo``/``dir``/``cd``/``type``, que ahí NO son
    ejecutables independientes) se deja tal cual: sin ``shell=True`` no hay
    forma segura de correrlos, y permitirlos en ``allowed_commands`` nunca
    tuvo sentido -- ``subprocess.run`` seguirá fallando con el mismo
    ``FileNotFoundError`` que ya se traduce a un ``ActionError`` legible más
    abajo, en vez de fallar en silencio o abrir una shell de verdad."""

    if sys.platform != "win32":
        return argv
    resuelto = shutil.which(argv[0])
    if resuelto is None:
        return argv
    if Path(resuelto).suffix.lower() in (".cmd", ".bat"):
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return [comspec, "/c", resuelto, *argv[1:]]
    return [resuelto, *argv[1:]]


def _run_command(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    command = params.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ActionError("falta el parámetro 'command' (texto)")

    try:
        argv = _split_command(command)
    except ValueError as exc:
        raise ActionError(f"comando mal formado: {exc}") from exc

    if not argv:
        raise ActionError("comando vacío")

    # La comprobación de permiso corre SIEMPRE contra el nombre literal que
    # configuró el dueño en allowed_commands (p.ej. "npm") -- nunca contra
    # la ruta resuelta que arma _argv_para_windows (p.ej. "...\npm.cmd"),
    # para que la lista de permitidos siga siendo la que el dueño escribió.
    executable = argv[0]
    # Máquina de UN DUEÑO (desktop local): el dueño pidió acceso TOTAL a la
    # terminal (incl. sudo); se salta el allowlist de ejecutables. En hosted
    # (multi-tenant) `allow_all_commands` es False por defecto y el allowlist
    # sigue mandando.
    if (
        not getattr(config, "allow_all_commands", False)
        and executable not in config.allowed_commands
    ):
        raise ActionError(
            f"comando no permitido (agrega {executable!r} a allowed_commands en companion.yaml)"
        )

    try:
        proc = subprocess.run(
            _argv_para_windows(argv),
            cwd=_sandbox_root(config, params),
            shell=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
    root = _sandbox_root(config, params)
    target = _resolve_in_sandbox(config, params.get("path"), root)
    if not target.exists():
        raise ActionError(f"no existe: {target.relative_to(root)}")
    if not target.is_dir():
        raise ActionError(f"no es una carpeta: {target.relative_to(root)}")

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
                can_descend = depth + 1 < max_depth and _is_within_sandbox(child_path, config, root)
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
        "path": str(target.relative_to(root)),
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

    root = _sandbox_root(config, params)
    base = _resolve_in_sandbox(config, params.get("path"), root)
    if not base.exists():
        raise ActionError(f"no existe: {base.relative_to(root)}")

    matches: list[dict[str, Any]] = []
    files_scanned = 0
    truncated = False

    for file_path in _iter_files_safe(base):
        if files_scanned >= MAX_SEARCH_FILES or len(matches) >= MAX_SEARCH_MATCHES:
            truncated = True
            break
        files_scanned += 1

        if not _is_within_sandbox(file_path, config, root):
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

        rel = str(file_path.relative_to(root))
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

    root = _sandbox_root(config, params)
    target = _resolve_in_sandbox(config, params.get("path"), root)
    if not target.exists() or not target.is_file():
        raise ActionError(f"no existe el archivo: {target.relative_to(root)}")

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
        "path": str(target.relative_to(root)),
        "replacements": replacements,
        "bytes_written": len(new_content.encode("utf-8")),
    }


_FORMATOS_CAPTURA = frozenset({"png", "jpeg", "webp"})
_FORMATOS_CON_PERDIDA = frozenset({"jpeg", "webp"})


def _screenshot_options(params: dict[str, Any]) -> tuple[str, int, int | None]:
    """Valida las opciones de transporte sin acoplarlas al backend de captura."""
    image_format = str(params.get("format") or "png").lower()
    if image_format == "jpg":
        image_format = "jpeg"
    if image_format not in _FORMATOS_CAPTURA:
        raise ActionError("'format' debe ser 'png', 'jpeg' o 'webp'")

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


def _pillow_tiene_webp() -> bool:
    try:
        from PIL import features  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        return bool(features.check("webp"))
    except Exception:  # noqa: BLE001 - plugin ausente o wheel incompleto
        return False


def _codificar_captura(image: Any, image_format: str, quality: int) -> tuple[bytes, str]:
    """WebP primero; JPEG si el wheel no trae libwebp. PNG solo a pedido."""
    output = io.BytesIO()
    if image_format == "webp":
        try:
            if not _pillow_tiene_webp():
                raise OSError("webp no disponible")
            image.save(output, format="WEBP", quality=quality, method=4)
            return output.getvalue(), "image/webp"
        except (OSError, ValueError):
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            return output.getvalue(), "image/jpeg"
    if image_format == "jpeg":
        image.save(output, format="JPEG", quality=quality, optimize=True)
        return output.getvalue(), "image/jpeg"
    image.save(output, format="PNG", optimize=True)
    return output.getvalue(), "image/png"


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
    Pedir ``jpeg`` o ``webp`` recodifica siempre, para no mandar un PNG Retina
    de varios megas al modelo ni al teléfono.
    """
    lossy = image_format in _FORMATOS_CON_PERDIDA
    needs_conversion = lossy or (max_width is not None and width > max_width)
    if not needs_conversion:
        return image_bytes, width, height, "image/png"
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        return image_bytes, width, height, "image/png"

    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = source.convert("RGB") if lossy else source.copy()
            if max_width is not None and image.width > max_width:
                new_height = max(1, round(image.height * max_width / image.width))
                image = image.resize((max_width, new_height), Image.Resampling.LANCZOS)
            encoded, mime = _codificar_captura(image, image_format, quality)
            return encoded, image.width, image.height, mime
    except (OSError, ValueError) as exc:
        raise ActionError(f"no se pudo preparar la captura para transmisión: {exc}") from exc


def _screenshot_via_mss(params: dict[str, Any]) -> tuple[bytes, int, int, int, int]:
    """Captura la pantalla primaria (o ``display``) en Windows/Linux."""
    try:
        import mss  # type: ignore[import-not-found]
        import mss.tools  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ActionError(
            f"la captura en Windows/Linux {_LINUX_REMOTE_CONTROL_INSTALL_HINT}"
        ) from exc

    if sys.platform.startswith("linux"):
        # `mss` habla X11/Wayland EN ESTE PROCESO (no por subprocess), así
        # que aquí sí hace falta rellenar `os.environ` de verdad -- no basta
        # con pasar un `env=` a nadie. `setdefault` nunca pisa lo que ya
        # esté puesto explícitamente (mismo criterio que `linux_session.py`).
        for clave, valor in linux_session.descubrir_variables_de_sesion().items():
            os.environ.setdefault(clave, valor)

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
    except Exception as exc:
        # OJO: NO alcanza con (OSError, RuntimeError, ValueError) -- medido
        # en vivo (edecan-prod, 1-ago-2026, sin $DISPLAY): el error real de
        # `mss` en esta build fue `mss.linux.xcbhelpers.XError`, que hereda
        # DIRECTO de `Exception` (ni de OSError ni de RuntimeError). Con el
        # except angosto de antes, ese fallo escapaba crudo -- un traceback
        # de `mss` hasta el agente -- en vez del `ActionError` con la pista
        # de abajo. `ActionError` ya se re-lanzó arriba sin tocar, así que
        # capturar `Exception` acá no la enmascara.
        hint = "autoriza la captura de pantalla para Edecán en el sistema"
        if sys.platform.startswith("linux"):
            hint = "verifica la sesión gráfica X11/Wayland y el permiso de captura"
        raise ActionError(f"no se pudo capturar la pantalla: {exc}; {hint}") from exc


def _macos_display_target(params: dict[str, Any]) -> tuple[int, int, int, int]:
    """Resuelve el número de pantalla de ``screencapture`` y su geometría.

    macOS numera sus pantallas desde 1 para ``screencapture -D``. Conservamos
    también el ``CGDirectDisplayID`` y el origen global para que los toques del
    teléfono sigan mapeando correctamente cuando hay más de un monitor.

    UNIDADES (importa, y hasta el fix de la coordenada normalizada esto estaba
    mezclado sin decirlo): el ``origin_x``/``origin_y`` que devuelve esta
    función sale de ``CGDisplayBounds``, o sea PUNTOS lógicos — el mismo
    espacio que consume ``CGEvent``. El ``width``/``height`` que acompañan a
    esos orígenes en el resultado de ``_screenshot`` NO: esos son PÍXELES de
    la imagen ya reducida por ``_optimize_screenshot``. Por eso el cliente no
    puede sumar el origen a una coordenada en píxeles del frame y esperar que
    caiga donde el dedo tocó: para eso están ``nx``/``ny`` en
    ``_input_pointer``.
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
    reference implementation, pero aquí se ejecuta sin shell, con timeout, archivo temporal
    aislado y la identidad firmada estable de ``edecan-local``.
    """

    display_index, _display_id, origin_x, origin_y = _macos_display_target(params)
    include_cursor = params.get("include_cursor", True)
    if not isinstance(include_cursor, bool):
        raise ActionError("'include_cursor' debe ser true o false")

    bridge_params = {"display": display_index, "include_cursor": include_cursor}
    if os.environ.get("EDECAN_DESKTOP_BRIDGE_SOCKET", "").strip():
        # El sidecar no debe tocar TCC ni ``screencapture``: macOS concede
        # Grabación de pantalla al proceso principal (`cc.edecan.desktop`) y
        # un cdhash nuevo tras ditto deja el preflight del helper en falso
        # aunque tccd siga mostrando el interruptor encendido.
        bridge_error: str | None = None
        try:
            bridge_result = _desktop_bridge_call("screenshot", bridge_params)
        except ActionError as exc:
            bridge_error = str(exc)
            bridge_result = None
        if bridge_result is not None:
            encoded = bridge_result.get("image_b64")
            if not isinstance(encoded, str) or not encoded:
                raise ActionError("el puente nativo devolvio una captura invalida")
            try:
                image_bytes = base64.b64decode(encoded, validate=True)
                from PIL import Image  # type: ignore[import-not-found]

                with Image.open(io.BytesIO(image_bytes)) as image:
                    width, height = image.size
            except (binascii.Error, ImportError, OSError, ValueError) as exc:
                raise ActionError(f"el puente nativo devolvio una captura invalida: {exc}") from exc
            return image_bytes, int(width), int(height), origin_x, origin_y
        mensaje = (
            "El proceso principal de Edecán no pudo capturar la pantalla. "
            "Abre Configuracion del Sistema > Privacidad y seguridad > "
            "Grabacion de pantalla, activa Edecán y reinicia la app. "
            "Tras una actualizacion, apaga y vuelve a encender su interruptor "
            "o reinicia la Mac si el permiso ya estaba activo pero el Remoto "
            "sigue en negro."
        )
        if bridge_error:
            mensaje += f" Detalle: {bridge_error[:200]}"
        raise ActionError(mensaje)

    # CLI/desarrollo sin puente: el helper estable solo captura si TCC ya lo
    # autoriza, para no repetir el modal en cada frame del teléfono.
    helper_allowed = _macos_screen_capture_allowed()
    if not helper_allowed:
        raise ActionError(
            "Grabacion de pantalla esta desactivada para Edecan. Abre "
            "Configuracion del Sistema > Privacidad y seguridad > "
            "Grabacion de audio del sistema y pantalla, activa Edecan en la "
            "lista superior (no en 'Solo grabacion de audio del sistema') y "
            "vuelve a abrir Edecan."
        )

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
            # 390 y no 500: el mensaje base + este sufijo deben caber en el
            # tope de 500 caracteres del campo `error` de audit.log_action.
            detail = completed.stderr.decode("utf-8", "replace").strip()
            suffix = f": {detail[:390]}" if detail else ""
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


def _macos_screen_capture_allowed() -> bool:
    """Consulta TCC sin abrir dialogos ni provocar una nueva solicitud.

    Falla abierto si CoreGraphics no pudiera cargarse: en ese caso la llamada
    real a ``screencapture`` conserva el diagnostico nativo. En macOS normal,
    un ``False`` evita que el polling del telefono repita el modal del sistema.
    """

    if sys.platform != "darwin":
        return True
    try:
        import ctypes

        core_graphics = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        core_graphics.CGPreflightScreenCaptureAccess.argtypes = []
        core_graphics.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(core_graphics.CGPreflightScreenCaptureAccess())
    except (AttributeError, OSError):
        logger.warning(
            "No se pudo consultar TCC antes de capturar; se conserva el diagnostico nativo.",
            exc_info=True,
        )
        return True


def _desktop_bridge_call(
    action: str,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    """Ejecuta una accion tipada dentro del proceso de escritorio autorizado.

    El socket Unix y su capacidad aleatoria solo existen en la app instalada.
    En CLI/desarrollo sin Tauri devuelve ``None`` para conservar los backends
    nativos anteriores. Nunca acepta un ejecutable, shell ni ruta arbitraria.
    """

    socket_path = os.environ.get("EDECAN_DESKTOP_BRIDGE_SOCKET", "").strip()
    token = os.environ.get("EDECAN_DESKTOP_BRIDGE_TOKEN", "").strip()
    if not socket_path or not token:
        return None
    request = (
        json.dumps(
            {"token": token, "action": action, "params": params},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    response = bytearray()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(HELPER_SUBPROCESS_TIMEOUT_SECONDS)
            client.connect(socket_path)
            client.sendall(request)
            while not response.endswith(b"\n"):
                chunk = client.recv(256 * 1024)
                if not chunk:
                    break
                response.extend(chunk)
                if len(response) > DESKTOP_BRIDGE_MAX_RESPONSE_BYTES:
                    raise ActionError("la respuesta del puente nativo es demasiado grande")
    except (OSError, TimeoutError) as exc:
        raise ActionError(f"no se pudo contactar el puente remoto nativo: {exc}") from exc
    try:
        payload = json.loads(bytes(response))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActionError("el puente remoto nativo devolvio una respuesta invalida") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload, dict) else None
        raise ActionError(str(error or "el puente remoto nativo fallo"))
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ActionError("el puente remoto nativo devolvio un resultado invalido")
    return result


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
    """Captura la pantalla y devuelve un frame PNG/JPEG/WebP optimizado en base64.

    En macOS usa el capturador nativo del sistema para incluir ventanas, Dock,
    barra de menú y cursor; en Windows/Linux usa `mss`, instalado mediante el
    extra ``remote-control``.
    Los permisos siguen siendo siempre los nativos del sistema operativo: esta
    acción no los solicita, evade ni automatiza. Puede reducir el frame y
    convertirlo a WebP (JPEG de respaldo) para que el visor remoto sea fluido
    y el modelo no reciba un PNG de varios megas.
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
            "ventanas": [],
        }

    raw_bytes, raw_width, raw_height, origin_x, origin_y = _screenshot_via_screencapture(params)
    para_el_modelo = params.get("crop_frontmost") is True
    recorte = None
    if para_el_modelo:
        recorte = _recortar_ventana_al_frente(
            raw_bytes,
            width=raw_width,
            height=raw_height,
            origin_x=origin_x,
            origin_y=origin_y,
            params=params,
        )
    image_bytes, width, height, mime = _optimize_screenshot(
        raw_bytes,
        width=raw_width,
        height=raw_height,
        image_format=image_format,
        quality=quality,
        max_width=max_width,
    )
    payload: dict[str, Any] = {
        "image_b64": base64.b64encode(image_bytes).decode("ascii"),
        "width": width,
        "height": height,
        "mime": mime,
        "origin_x": origin_x,
        "origin_y": origin_y,
        "ventanas": _macos_ventanas_visibles(),
    }
    if para_el_modelo:
        payload["foco"] = _macos_foco_accesibilidad()
        ocr_fuente = recorte[0] if recorte is not None else raw_bytes
        texto_visible = _ocr_vision_macos(ocr_fuente)
        if texto_visible:
            payload["texto_visible"] = texto_visible
    if recorte is not None:
        crop_bytes, crop_w, crop_h = recorte
        crop_bytes, crop_w, crop_h, crop_mime = _optimize_screenshot(
            crop_bytes,
            width=crop_w,
            height=crop_h,
            image_format=image_format,
            quality=max(quality, 90),
            max_width=min(max_width or 2560, 2560),
        )
        payload["crop_b64"] = base64.b64encode(crop_bytes).decode("ascii")
        payload["crop_mime"] = crop_mime
        payload["crop_width"] = crop_w
        payload["crop_height"] = crop_h
    return payload


_IGNORAR_VENTANAS = frozenset(
    {
        "Window Server",
        "SystemUIServer",
        "Control Center",
        "Notification Centre",
        "Centro de notificaciones",
        "Spotlight",
    }
)


def _macos_ventanas_crudas() -> list[dict[str, Any]]:
    """Ventanas on-screen con bounds en puntos. Interno; no va al modelo."""
    if sys.platform != "darwin":
        return []
    try:
        import Quartz  # type: ignore[import-not-found]
    except ImportError:
        return []
    try:
        crudo = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
    except Exception:  # noqa: BLE001 - la foto ya salió; la lista es extra
        return []
    if not crudo:
        return []
    vistas: list[dict[str, Any]] = []
    for info in crudo:
        if not isinstance(info, dict):
            continue
        try:
            capa = int(info.get("kCGWindowLayer") or 0)
        except (TypeError, ValueError):
            continue
        if capa != 0:
            continue
        bounds = info.get("kCGWindowBounds") or {}
        try:
            ancho = float(bounds.get("Width") or 0)
            alto = float(bounds.get("Height") or 0)
            origen_x = float(bounds.get("X") or 0)
            origen_y = float(bounds.get("Y") or 0)
        except (TypeError, ValueError):
            continue
        if ancho < 120 or alto < 80:
            continue
        app = str(info.get("kCGWindowOwnerName") or "").strip()
        if not app or app in _IGNORAR_VENTANAS:
            continue
        titulo = str(info.get("kCGWindowName") or "").strip()
        vistas.append(
            {
                "app": app,
                "titulo": titulo,
                "bounds": {"X": origen_x, "Y": origen_y, "Width": ancho, "Height": alto},
            }
        )
        if len(vistas) >= 16:
            break
    return vistas


def _macos_ventanas_visibles() -> list[dict[str, Any]]:
    """Apps y títulos al frente. Lo que el modelo debe decir, no inventar."""
    vistas: list[dict[str, Any]] = []
    for item in _macos_ventanas_crudas():
        fila = {"app": item["app"], "titulo": item["titulo"]}
        vistas.append(fila)
    if vistas:
        vistas[0] = {**vistas[0], "al_frente": True}
    return vistas


def _recortar_ventana_al_frente(
    image_bytes: bytes,
    *,
    width: int,
    height: int,
    origin_x: int,
    origin_y: int,
    params: dict[str, Any],
) -> tuple[bytes, int, int] | None:
    """Recorte en píxeles de la ventana al frente, desde el PNG Retina crudo."""
    crudas = _macos_ventanas_crudas()
    if not crudas or width <= 0 or height <= 0:
        return None
    bounds = crudas[0].get("bounds")
    if not isinstance(bounds, dict):
        return None
    try:
        _, _, disp_w, disp_h = _macos_pointer_display_bounds(params)
    except ActionError:
        return None
    disp_x, disp_y = origin_x, origin_y
    if disp_w <= 0 or disp_h <= 0:
        return None
    area_ventana = float(bounds["Width"]) * float(bounds["Height"])
    # Maximizada ≈ todo el display: el recorte no aporta. El OCR igual corre.
    if area_ventana >= 0.92 * disp_w * disp_h:
        return None
    scale_x = width / disp_w
    scale_y = height / disp_h
    pad = 16.0
    left = int(((float(bounds["X"]) - disp_x) - pad) * scale_x)
    top = int(((float(bounds["Y"]) - disp_y) - pad) * scale_y)
    right = int((float(bounds["X"]) - disp_x + float(bounds["Width"]) + pad) * scale_x)
    bottom = int((float(bounds["Y"]) - disp_y + float(bounds["Height"]) + pad) * scale_y)
    left = max(0, min(width - 1, left))
    top = max(0, min(height - 1, top))
    right = max(left + 1, min(width, right))
    bottom = max(top + 1, min(height, bottom))
    if right - left < 200 or bottom - top < 120:
        return None
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            recorte = source.crop((left, top, right, bottom))
            salida = io.BytesIO()
            recorte.save(salida, format="PNG")
            return salida.getvalue(), recorte.width, recorte.height
    except (OSError, ValueError):
        return None


def _macos_foco_accesibilidad() -> dict[str, str]:
    """App, ventana y valor del campo enfocado. Vacío si no hay Accesibilidad."""
    if sys.platform != "darwin":
        return {}
    foco: dict[str, str] = {}
    try:
        from AppKit import NSWorkspace  # type: ignore[import-not-found]

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is not None:
            nombre = str(app.localizedName() or "").strip()
            if nombre:
                foco["app"] = nombre
    except Exception:  # noqa: BLE001 - el foco es extra
        pass
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXUIElementCopyAttributeValue,
            AXUIElementCreateSystemWide,
            kAXFocusedApplicationAttribute,
            kAXFocusedUIElementAttribute,
            kAXRoleAttribute,
            kAXTitleAttribute,
            kAXValueAttribute,
        )

        system = AXUIElementCreateSystemWide()
        focused_app = AXUIElementCopyAttributeValue(system, kAXFocusedApplicationAttribute)
        if focused_app is not None:
            titulo = AXUIElementCopyAttributeValue(focused_app, kAXTitleAttribute)
            if titulo:
                foco["ventana"] = str(titulo).strip()[:180]
        focused = AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute)
        if focused is None:
            return foco
        rol = str(AXUIElementCopyAttributeValue(focused, kAXRoleAttribute) or "").strip()
        if rol:
            foco["rol"] = rol[:80]
        if "Secure" in rol:
            return foco
        valor = AXUIElementCopyAttributeValue(focused, kAXValueAttribute)
        texto = str(valor or "").strip()
        if texto and len(texto) <= 400:
            foco["valor"] = texto
        elif texto:
            foco["valor"] = "(campo largo; usa la foto y el OCR)"
    except Exception:  # noqa: BLE001 - sin permiso AX o API distinta
        return foco
    return foco


def _ocr_vision_macos(image_bytes: bytes) -> list[str]:
    """Live Text del sistema. Sin dependencias pip: carga Vision.framework."""
    if sys.platform != "darwin" or not image_bytes:
        return []
    try:
        import objc  # type: ignore[import-not-found]
        from Foundation import NSData  # type: ignore[import-not-found]
    except ImportError:
        return []
    try:
        vision: dict[str, Any] = {}
        objc.loadBundle(
            "Vision",
            vision,
            bundle_path="/System/Library/Frameworks/Vision.framework",
        )
        reconocer = vision.get("VNRecognizeTextRequest")
        manejador = vision.get("VNImageRequestHandler")
        if reconocer is None or manejador is None:
            return []
        nsdata = NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
        request = reconocer.alloc().init()
        try:
            request.setRecognitionLevel_(1)  # accurate
        except Exception:  # noqa: BLE001 - constante distinta según versión
            pass
        handler = manejador.alloc().initWithData_options_(nsdata, None)
        ok = handler.performRequests_error_([request], None)
        if not ok:
            return []
        lineas: list[str] = []
        vistos: set[str] = set()
        for observacion in request.results() or []:
            candidatos = observacion.topCandidates_(1)
            if not candidatos:
                continue
            texto = str(candidatos[0].string() or "").strip()
            if len(texto) < 2 or texto in vistos:
                continue
            vistos.add(texto)
            lineas.append(texto)
            if len(lineas) >= 40:
                break
        return lineas
    except Exception:  # noqa: BLE001 - Vision ausente o imagen inválida
        return []


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


class _DesktopBridgeInputBackend:
    """Input macOS ejecutado por la app principal que ya posee Accesibilidad."""

    @staticmethod
    def _call(action: str, params: dict[str, Any]) -> None:
        if _desktop_bridge_call(action, params) is None:
            raise ActionError("el puente remoto nativo no esta disponible")

    def move_pointer(self, x: int, y: int) -> None:
        self._call("move_pointer", {"x": x, "y": y})

    def click_pointer(self, x: int, y: int, button: str) -> None:
        self._call("click_pointer", {"x": x, "y": y, "button": button})

    def pointer_down(self, x: int, y: int, button: str) -> None:
        self._call("pointer_down", {"x": x, "y": y, "button": button})

    def pointer_up(self, x: int, y: int, button: str) -> None:
        self._call("pointer_up", {"x": x, "y": y, "button": button})

    def scroll_pointer(self, delta_x: int, delta_y: int) -> None:
        self._call("scroll_pointer", {"delta_x": delta_x, "delta_y": delta_y})

    def type_text(self, text: str) -> None:
        self._call("type_text", {"text": text})

    def press_key(self, key: str, modifiers: tuple[str, ...] = ()) -> None:
        self._call("press_key", {"key": key, "modifiers": list(modifiers)})


class _PynputInputBackend:
    """Backend real para Windows/Linux mediante el extra ``remote-control``."""

    def __init__(self) -> None:
        if sys.platform.startswith("linux"):
            # `pynput` habla X11 EN ESTE PROCESO -- mismo motivo que
            # `_screenshot_via_mss`: hay que rellenar `os.environ` de verdad,
            # `setdefault` nunca pisa lo que ya esté puesto.
            for clave, valor in linux_session.descubrir_variables_de_sesion().items():
                os.environ.setdefault(clave, valor)
        try:
            from pynput import keyboard, mouse  # type: ignore[import-not-found]

            self._keyboard_module = keyboard
            self._mouse_module = mouse
            self._keyboard = keyboard.Controller()
            self._mouse = mouse.Controller()
        except ImportError as exc:
            if sys.platform.startswith("linux") and importlib.util.find_spec("pynput") is not None:
                # `pynput` SÍ está instalado: en Linux, al importarse, intenta
                # conectarse a X11 y convierte ESE fallo en `ImportError`
                # (así lo diseñó la propia librería -- "no hay backend
                # soportado en esta plataforma" cubre tanto "no está
                # instalado" como "no hay sesión gráfica"). Medido en vivo
                # (edecan-prod, 1-ago-2026, sin $DISPLAY): el mensaje de abajo
                # decía "instala el extra", y el extra YA estaba instalado --
                # instrucción falsa, igual de familia que el `brew` en Linux.
                #
                # `os.environ.get("DISPLAY")` (tras el `setdefault` de arriba)
                # distingue "de verdad no hay pista de sesión" de "hay un
                # $DISPLAY puesto pero apunta a algo que no responde" --
                # medido en vivo que el mensaje genérico afirmaba de más en
                # el segundo caso ($DISPLAY=:99 inválido SÍ estaba puesta).
                display_actual = os.environ.get("DISPLAY")
                if display_actual:
                    detalle_display = (
                        f"$DISPLAY está puesta ({display_actual!r}) pero no responde"
                    )
                else:
                    detalle_display = (
                        "no tiene $DISPLAY puesta ni pudo descubrirla de una sesión activa"
                    )
                raise ActionError(
                    f"no se pudo conectar con la sesión gráfica X11 (pynput: {detalle_display}); "
                    "revisa que haya una sesión de escritorio X11 en esta máquina, que "
                    "$DISPLAY apunte a ella, y que el servicio de Edecán pueda verla "
                    "(systemd: Environment=DISPLAY=:10 en la unidad, o exporta $DISPLAY "
                    "antes de arrancarlo)"
                ) from exc
            raise ActionError(
                f"el control remoto en Windows/Linux {_LINUX_REMOTE_CONTROL_INSTALL_HINT}"
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
        if os.environ.get("EDECAN_DESKTOP_BRIDGE_SOCKET") and os.environ.get(
            "EDECAN_DESKTOP_BRIDGE_TOKEN"
        ):
            return _DesktopBridgeInputBackend()
        return _QuartzInputBackend()
    if sys.platform == "win32" or sys.platform.startswith("linux"):
        return _PynputInputBackend()
    raise ActionError("el control remoto de teclado/mouse no está soportado en esta plataforma")


# ---------------------------------------------------------------------------
# Coordenada NORMALIZADA (`nx`/`ny`, 0.0..1.0) -> coordenada real del puntero.
#
# El bug que arregla: hay TRES espacios de coordenadas distintos y hasta acá
# nadie convertía entre ellos.
#   1. `screencapture` captura en PÍXELES Retina nativos (p. ej. 3456x2234).
#   2. `_optimize_screenshot` REDUCE esa imagen a `max_width` (1600 por
#      defecto, y con `format: "jpeg"`/`"webp"` el resize corre SIEMPRE) y
#      es ese tamaño reducido el que viaja al teléfono como `width`/`height`.
#   3. `CGEvent` no consume ninguno de los dos: consume el espacio global de
#      pantalla en PUNTOS lógicos (el de `CGDisplayBounds`, p. ej. 2056x1329).
# Mandar la coordenada del frame (1600) como si fueran puntos (2056) hace que
# cada clic aterrice al 77.8% del camino hacia la esquina superior izquierda:
# el Dock y la franja derecha de la pantalla quedaban inalcanzables.
#
# La solución (la misma que usa reference implementation): el teléfono manda una FRACCIÓN
# `nx`/`ny` en 0.0..1.0 y es el companion —el único que conoce la geometría
# real del escritorio— quien la multiplica por el tamaño del display en las
# unidades que de verdad consume su backend de input. Así da igual a qué
# tamaño se haya comprimido la imagen.
#
# Compatibilidad: `x`/`y` siguen siendo obligatorios y son el ÚNICO camino
# cuando no llegan `nx`/`ny` (clientes viejos, panel web) — ese camino queda
# byte por byte como estaba.
# ---------------------------------------------------------------------------


def _macos_pointer_display_bounds(params: dict[str, Any]) -> tuple[int, int, int, int]:
    """`(origin_x, origin_y, width, height)` del display en PUNTOS lógicos.

    Puntos, no píxeles: es exactamente el espacio que consume `CGEvent`
    (`_QuartzInputBackend`) y también el puente nativo de la app instalada
    (`_DesktopBridgeInputBackend` -> `remote_bridge.rs`, que hace
    `CGEvent::new_mouse_event` con el punto tal cual se lo pasan). En una
    Retina de 3456x2234 píxeles, esto devuelve 2056x1329.
    """
    _display_index, display_id, origin_x, origin_y = _macos_display_target(params)
    try:
        import Quartz  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - _macos_display_target ya falló antes
        raise ActionError("el control remoto de macOS requiere pyobjc-framework-Quartz") from exc

    bounds = Quartz.CGDisplayBounds(display_id)
    width = int(round(bounds.size.width))
    height = int(round(bounds.size.height))
    if width <= 0 or height <= 0:
        raise ActionError("macOS devolvió una geometría de pantalla inválida")
    return origin_x, origin_y, width, height


def _mss_pointer_display_bounds(params: dict[str, Any]) -> tuple[int, int, int, int]:
    """`(origin_x, origin_y, width, height)` del monitor en Windows/Linux.

    Acá la unidad son PÍXELES del escritorio virtual, que es justo lo que
    consume `pynput` (`_PynputInputBackend`) — no hay un espacio de "puntos"
    separado como en macOS. Misma numeración de `display` que
    `_screenshot_via_mss` (1 = monitor primario) para que la fracción se
    resuelva contra la MISMA pantalla que se capturó.
    """
    try:
        import mss  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ActionError(
            f"el control remoto en Windows/Linux {_LINUX_REMOTE_CONTROL_INSTALL_HINT}"
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
            width = int(monitor.get("width", 0))
            height = int(monitor.get("height", 0))
            if width <= 0 or height <= 0:
                raise ActionError("el sistema devolvió una geometría de pantalla inválida")
            return int(monitor.get("left", 0)), int(monitor.get("top", 0)), width, height
    except ActionError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ActionError(f"no se pudo leer la geometría de la pantalla: {exc}") from exc


def _pointer_display_bounds(params: dict[str, Any]) -> tuple[int, int, int, int]:
    """Geometría del display destino EN LAS UNIDADES DEL BACKEND DE INPUT.

    macOS -> puntos lógicos (`CGDisplayBounds`); Windows/Linux -> píxeles del
    escritorio virtual (`mss`). Nunca píxeles de la captura ni del JPEG ya
    reducido: esos dos espacios no le sirven a nadie del lado del sistema.
    """
    if sys.platform == "darwin":
        return _macos_pointer_display_bounds(params)
    if sys.platform == "win32" or sys.platform.startswith("linux"):
        return _mss_pointer_display_bounds(params)
    raise ActionError("el control remoto de teclado/mouse no está soportado en esta plataforma")


def _coerce_fraction(value: Any, name: str) -> float:
    """`value` -> `float` en `[0.0, 1.0]`, o `ActionError` con el nombre exacto."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ActionError(f"'{name}' debe ser un número entre 0.0 y 1.0")
    fraction = float(value)
    if not math.isfinite(fraction) or fraction < 0.0 or fraction > 1.0:
        raise ActionError(f"'{name}' debe ser un número entre 0.0 y 1.0")
    return fraction


def _read_normalized_pair(
    params: dict[str, Any], name_x: str, name_y: str
) -> tuple[float, float] | None:
    """`(nx, ny)` validados, o `None` si el cliente no mandó NINGUNO de los dos.

    Mandar solo uno es un error: quedaría medio gesto en el espacio nuevo y
    medio en el viejo, que es exactamente el bug que esto viene a arreglar.
    """
    raw_x = params.get(name_x)
    raw_y = params.get(name_y)
    if raw_x is None and raw_y is None:
        return None
    if raw_x is None or raw_y is None:
        raise ActionError(f"'{name_x}' y '{name_y}' se mandan juntos o no se manda ninguno")
    return _coerce_fraction(raw_x, name_x), _coerce_fraction(raw_y, name_y)


def _fraction_to_display_coord(fraction: float, origin: int, size: int) -> int:
    """`fraction` (0..1) -> coordenada absoluta dentro de `[origin, origin+size)`.

    Se recorta a `size - 1` a propósito: `fraction == 1.0` (el dedo justo en
    el borde derecho/inferior) daría `origin + size`, que ya es el primer
    punto del monitor de al lado — o directamente fuera de la pantalla.

    Redondeo "mitad hacia arriba" con `floor(v + 0.5)`, NO el `round()` de
    Python (que redondea 0.5 al par más cercano): así el resultado coincide
    exactamente con el de los clientes, que usan `Math.round` (web),
    `.rounded()` (Swift) y `roundToInt()` (Kotlin) — los tres redondean la
    mitad hacia arriba.
    """
    scaled = math.floor(fraction * size + 0.5)
    return origin + min(max(scaled, 0), max(size - 1, 0))


def _input_pointer(params: dict[str, Any], config: CompanionConfig) -> dict[str, Any]:
    """Control completo de puntero: movimiento, clics, drag y scroll.

    `button` (default `"left"`) elige qué botón usar para `click`/
    `double_click`; `right_click` siempre usa el botón derecho sin importar
    lo que traiga `button`. Todo gesto que no sea `move` primero MUEVE el
    puntero a `(x, y)` y luego hace clic ahí -- nunca asume que el puntero ya
    estaba en esa posición.

    COORDENADAS. `x`/`y` (enteros, obligatorios) son el camino histórico: se
    usan TAL CUAL contra el backend de input. `nx`/`ny` (float 0.0..1.0,
    OPCIONALES) son el camino nuevo y correcto: si llegan, ganan, y la
    coordenada real se calcula acá multiplicándolos por el tamaño del display
    en las unidades del backend (`_pointer_display_bounds`) -- ver el bloque
    de comentarios de arriba para el porqué. `drag` acepta el mismo par para
    su punto de partida: `start_nx`/`start_ny`.

    La resolución ocurre SIEMPRE de este lado, nunca en el puente Rust: los
    dos backends de macOS (`_QuartzInputBackend` y `_DesktopBridgeInputBackend`
    -> `remote_bridge.rs`) reciben la coordenada ya resuelta en puntos, así
    que `remote_bridge.rs` no cambia ni una línea y no hay dos lugares donde
    pueda desincronizarse la misma cuenta.
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

    # Camino nuevo: la fracción manda. Se valida SIEMPRE (aunque falte el
    # otro par) para que un `nx` mal formado dé un error claro en vez de
    # colarse silenciosamente por el camino legacy.
    fraccion = _read_normalized_pair(params, "nx", "ny")
    fraccion_inicio = _read_normalized_pair(params, "start_nx", "start_ny")
    if fraccion is not None or fraccion_inicio is not None:
        origin_x, origin_y, ancho, alto = _pointer_display_bounds(params)
        if fraccion is not None:
            x = _fraction_to_display_coord(fraccion[0], origin_x, ancho)
            y = _fraction_to_display_coord(fraccion[1], origin_y, alto)
        if fraccion_inicio is not None:
            start_x = _fraction_to_display_coord(fraccion_inicio[0], origin_x, ancho)
            start_y = _fraction_to_display_coord(fraccion_inicio[1], origin_y, alto)

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
    "open_url": _open_url,
    "read_dir": _read_dir,
    "read_file": _read_file,
    "write_file": _write_file,
    "trash_path": _trash_path,
    "clipboard_get": _clipboard_get,
    "clipboard_set": _clipboard_set,
    "transfer_push": _transfer_push,
    "transfer_list": _transfer_list,
    "transfer_pull": _transfer_pull,
    "run_command": _run_command,
    "list_tree": _list_tree,
    "search_files": _search_files,
    "apply_edit": _apply_edit,
    "screenshot": _screenshot,
    "input_pointer": _input_pointer,
    "input_key": _input_key,
    **PERSONAL_APP_ACTIONS,
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
    if action in _PERSONAL_MESSAGE_ACTIONS and "body" in params:
        # Compatibilidad con clientes anteriores sin dejar el cuerpo en claro
        # en aprobación/auditoría: ``message`` es una clave sensible conocida
        # por ``audit.sanitize_params``. Se copia para no mutar el dict del
        # caller mientras la operación corre en otro hilo.
        params = dict(params)
        params.setdefault("message", params["body"])
        params.pop("body", None)
    handler = ACTIONS.get(action)

    if handler is None:
        logger.warning("Acción no soportada solicitada: %r", action)
        error = f"acción no soportada: {action!r}"
        audit.log_action(
            action=action,
            params=params,
            approved=False,
            ok=False,
            log_path=config.audit_log_path,
            error=error,
        )
        return {"ok": False, "error": error}

    if action in _IDE_ACTIONS and not config.ide_enabled:
        logger.info("Acción de IDE %r rechazada: ide_enabled=false en companion.yaml.", action)
        error = "el IDE está deshabilitado en este companion (ide_enabled=false en companion.yaml)"
        audit.log_action(
            action=action,
            params=params,
            approved=False,
            ok=False,
            log_path=config.audit_log_path,
            error=error,
        )
        return {"ok": False, "error": error}

    if action in _INPUT_ACTIONS and not config.remote_input_enabled:
        logger.info(
            "Acción de control remoto %r rechazada: remote_input_enabled=false en companion.yaml.",
            action,
        )
        error = (
            "el control remoto de teclado/mouse está deshabilitado en este companion "
            "(remote_input_enabled=false en companion.yaml)"
        )
        audit.log_action(
            action=action,
            params=params,
            approved=False,
            ok=False,
            log_path=config.audit_log_path,
            error=error,
        )
        return {"ok": False, "error": error}

    try:
        approved = bool(await approver(action, params, config))
    except Exception:
        logger.exception(
            "El approver falló evaluando la acción %r; se rechaza por seguridad.", action
        )
        approved = False

    if not approved:
        error = "acción rechazada (sin aprobación del usuario)"
        audit.log_action(
            action=action,
            params=params,
            approved=False,
            ok=False,
            log_path=config.audit_log_path,
            error=error,
        )
        return {"ok": False, "error": error}

    try:
        result = await asyncio.to_thread(handler, params, config)
    except (ActionError, PersonalAppError) as exc:
        audit.log_action(
            action=action,
            params=params,
            approved=True,
            ok=False,
            log_path=config.audit_log_path,
            error=str(exc),
        )
        return {"ok": False, "error": str(exc)}
    except Exception:
        logger.exception("Error inesperado ejecutando la acción %r", action)
        error = "error interno del companion ejecutando la acción"
        audit.log_action(
            action=action,
            params=params,
            approved=True,
            ok=False,
            log_path=config.audit_log_path,
            error=error,
        )
        return {"ok": False, "error": error}

    audit.log_action(
        action=action, params=params, approved=True, ok=True, log_path=config.audit_log_path
    )
    return {"ok": True, "result": result}
