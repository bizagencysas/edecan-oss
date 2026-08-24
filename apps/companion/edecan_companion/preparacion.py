"""Manifiesto de requisitos del sistema para Windows, y su ejecución.

Pantalla de preparación (encargo del dueño, literal): al abrir Edecán en
Windows, si falta algo del sistema, se ve una lista de lo que falta, con un
solo botón ▶ que instala cada cosa y la hace desaparecer de la lista al
terminar bien. Este módulo es el lado companion de esa pantalla: detecta qué
falta (:func:`detectar`) y ejecuta la instalación de UN requisito a la vez
(:class:`EjecutorPreparacion`).

Linux (encargo del dueño, 1-ago-2026): la instalación con un clic sigue
siendo EXCLUSIVA de Windows -- ver la regla de seguridad de abajo, que no
cambia. Lo que sí se agregó es detección informativa
(:data:`REQUISITOS_LINUX`): antes `detectar()` devolvía `[]` en Linux SIN
haber comprobado nada, que se lee igual que "todo bien" sin serlo. Ahora
dice de verdad qué falta y con qué comando de ESA plataforma se resuelve a
mano (nunca `brew`, que no existe ahí por defecto -- medido en vivo,
edecan-prod, 1-ago-2026).

REGLA DE SEGURIDAD (la más importante de este archivo)
--------------------------------------------------------
Los comandos que se ejecutan salen ÚNICAMENTE de :data:`REQUISITOS`, la lista
fija de abajo, escrita en este código. Nunca de un modelo, nunca de entrada
del usuario, nunca de la red. El cliente (móvil o la propia app de
escritorio) solo puede pedir "instala el requisito `X`" por su ``id`` --
nunca puede mandar un argv ni una cadena de comando (ver
:meth:`EjecutorPreparacion._requisito`, que rechaza cualquier ``id`` que no
esté en este manifiesto ANTES de tocar el sistema). :data:`REQUISITOS_LINUX`
respeta la misma regla por construcción: cada entrada tiene ``instalar=None``
siempre, así que ningún comando de ahí llega jamás a ejecutarse.

Por qué esto importa más que en el resto del companion: una pantalla que
ejecuta comandos con solo un clic es, si se hace mal, una puerta trasera con
botón de play. `run_command`/las terminales del IDE (`ide_sessions.py`) ya
exponen un shell completo, pero SIEMPRE detrás de un ``workspace_id``
autorizado explícitamente por la persona y, en el caso remoto, de una
aprobación humana en el momento (ver ``ide_runtime._APPROVAL_ACTIONS``). Esta
pantalla corre ANTES de que exista ningún workspace -- no hay "carpeta
autorizada" que limite el daño -- así que la única defensa aceptable es que
el conjunto de comandos posibles sea CERRADO y auditable con `grep
"instalar=" preparacion.py`, sin ninguna rama de código que construya un argv
a partir de datos externos.

Piso de soporte y comandos citados de ``docs/edecan-windows.md`` §9
("Piso de soporte") y §10 ("Lista de comprobación para la PC del dueño"):
versión mínima de Windows, PowerShell 7, rutas largas del registro y WebView2
Runtime. Git se agrega por instrucción directa del encargo (el documento no
da un comando de instalación propio para Git -- solo lo asume presente en
``git status``/``git diff`` del checklist -- así que ese ``instalar`` es
autoría de este módulo, no una cita literal del documento).
"""

from __future__ import annotations

import codecs
import ctypes
import importlib.util
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from edecan_companion import pty_compat

# ``winreg`` solo existe en Windows -- mismo patrón de import condicional que
# ``pty_compat.py`` (``fcntl``/``pty``/``termios`` en POSIX, ``winpty`` en
# Windows): el módulo tiene que poder IMPORTARSE en cualquier plataforma
# (macOS/Linux, donde vive este repo y corre la suite) sin que la rama
# ausente rompa nada. Las funciones que lo usan comprueban ``winreg is None``
# antes de tocarlo.
if os.name == "nt":
    import winreg
else:  # pragma: no cover - ejercitado solo en Windows real.
    winreg = None  # type: ignore[assignment]


class EstadoRequisito(StrEnum):
    """Resultado de comprobar UN requisito. Tres estados, nunca una excepción:
    ``comprobar()`` de un requisito puede fallar (registro inaccesible,
    ejecutable ausente de un modo raro) y eso se reporta como DESCONOCIDO, no
    como un 500 que tumbe el arranque de la app (regla explícita del
    encargo: "no puede lanzar")."""

    CUMPLIDO = "cumplido"
    FALTA = "falta"
    DESCONOCIDO = "desconocido"


@dataclass(frozen=True)
class Requisito:
    """Una entrada del manifiesto. Inmutable a propósito: nada en este
    proceso reescribe `instalar` en caliente."""

    id: str
    nombre: str
    por_que: str
    comprobar: Callable[[], EstadoRequisito]
    # ``None`` = no hay comando de CLI que lo resuelva con seguridad (p. ej.
    # actualizar la versión de Windows) -- la pantalla lo muestra como aviso
    # informativo, sin botón de instalación automática para esa fila.
    instalar: list[str] | None
    requiere_admin: bool
    # "si sin esto la app no puede funcionar, o solo se degrada" (encargo,
    # literal). ``True`` bloquea la entrada a la app; ``False`` se puede
    # descartar con "continuar de todas formas".
    obligatorio: bool


# ---------------------------------------------------------------------------
# Detección de cada requisito. Cada función es una unidad de prueba propia
# (los tests de ``test_preparacion.py`` las monkeypatchean directamente --
# "detección con dobles", pedido del encargo) y ninguna lanza: un fallo real
# de lectura se atrapa en :func:`detectar`, no acá adentro, para que cada
# función de comprobación siga siendo simple de leer y de sustituir en tests.
# ---------------------------------------------------------------------------

# Windows 10 versión 1809 = compilación 17763, piso real de ConPTY citado en
# docs/edecan-windows.md §1 y §9.
_BUILD_MINIMO_CONPTY = 17763


def _build_de_windows() -> int | None:
    """Número de compilación de Windows, o ``None`` si no se puede saber
    (plataforma distinta de Windows, o el intérprete no expone
    ``sys.getwindowsversion`` -- CPython solo la define en builds de win32).
    Función separada de :func:`_comprobar_version_windows` para que los tests
    puedan sustituirla sin necesitar un `sys.getwindowsversion` real."""
    getter = getattr(sys, "getwindowsversion", None)
    if getter is None:
        return None
    try:
        return int(getter().build)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _comprobar_version_windows() -> EstadoRequisito:
    build = _build_de_windows()
    if build is None:
        return EstadoRequisito.DESCONOCIDO
    return EstadoRequisito.CUMPLIDO if build >= _BUILD_MINIMO_CONPTY else EstadoRequisito.FALTA


def _comprobar_pwsh() -> EstadoRequisito:
    """PowerShell 7 (`pwsh`), el shell preferido del IDE (docs/edecan-windows.md
    §1.2 "Shell por defecto") -- mismo orden de búsqueda que
    ``ide_sessions.SessionManager._default_terminal_argv``."""
    encontrado = shutil.which("pwsh.exe") or shutil.which("pwsh")
    return EstadoRequisito.CUMPLIDO if encontrado else EstadoRequisito.FALTA


_CLAVE_RUTAS_LARGAS = r"SYSTEM\CurrentControlSet\Control\FileSystem"
_VALOR_RUTAS_LARGAS = "LongPathsEnabled"


def _leer_long_paths_enabled() -> int | None:
    """Lee ``HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled``
    (docs/edecan-windows.md §2 "Longitud 260", el mismo valor que revisa
    ``edecan doctor``). ``None`` si la clave/valor no existe o no se puede
    leer -- la lectura de HKLM no exige admin, así que un fallo aquí es
    "no se pudo determinar", no un permiso denegado esperable."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CLAVE_RUTAS_LARGAS) as clave:
            valor, _tipo = winreg.QueryValueEx(clave, _VALOR_RUTAS_LARGAS)
        return int(valor)
    except OSError:
        return None
    except (TypeError, ValueError):
        return None


def _comprobar_rutas_largas() -> EstadoRequisito:
    valor = _leer_long_paths_enabled()
    if valor is None:
        # Ausente = deshabilitado por defecto en una instalación limpia de
        # Windows -- es el estado real, no "desconocido".
        return EstadoRequisito.FALTA
    return EstadoRequisito.CUMPLIDO if valor == 1 else EstadoRequisito.FALTA


# GUID publicado por Microsoft para el cliente "Evergreen" del runtime
# WebView2 -- es la forma documentada de detectar si el runtime está
# instalado sin depender del instalador de Tauri:
# learn.microsoft.com/microsoft-edge/webview2/concepts/distribution
# ("Detect if a suitable WebView2 Runtime is already installed").
_WEBVIEW2_CLIENTE_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _version_webview2_instalada() -> str | None:
    """Versión del WebView2 Runtime instalado (máquina o usuario), o ``None``
    si no se encuentra en ninguna de las dos raíces que documenta Microsoft."""
    if winreg is None:
        return None
    subclave_maquina = (
        rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_CLIENTE_GUID}"
    )
    subclave_usuario = rf"Software\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_CLIENTE_GUID}"
    for raiz, ruta in (
        (winreg.HKEY_LOCAL_MACHINE, subclave_maquina),
        (winreg.HKEY_CURRENT_USER, subclave_usuario),
    ):
        try:
            with winreg.OpenKey(raiz, ruta) as clave:
                version, _tipo = winreg.QueryValueEx(clave, "pv")
        except OSError:
            continue
        if version and str(version) != "0.0.0.0":
            return str(version)
    return None


def _comprobar_webview2() -> EstadoRequisito:
    return EstadoRequisito.CUMPLIDO if _version_webview2_instalada() else EstadoRequisito.FALTA


def _comprobar_git() -> EstadoRequisito:
    encontrado = shutil.which("git.exe") or shutil.which("git")
    return EstadoRequisito.CUMPLIDO if encontrado else EstadoRequisito.FALTA


# Flags fijos de winget para instalación no interactiva: esta pantalla solo
# MUESTRA la salida del proceso, no puede responder a un prompt de "¿aceptas
# los términos? (S/N)" que aparezca en medio -- sin estos flags un winget
# interactivo se quedaría esperando una respuesta que nunca llega y el
# requisito parecería "colgado" para siempre. Decisión propia (no citada del
# documento, que no anticipa ejecución automática).
_WINGET_NO_INTERACTIVO = ("--accept-package-agreements", "--accept-source-agreements")


def _instalar_winget(id_paquete: str) -> list[str]:
    return [
        "winget",
        "install",
        "--id",
        id_paquete,
        "-e",
        "--source",
        "winget",
        *_WINGET_NO_INTERACTIVO,
    ]


# ---------------------------------------------------------------------------
# EL MANIFIESTO. Única fuente de comandos ejecutables por esta pantalla --
# ver la regla de seguridad en el docstring del módulo.
# ---------------------------------------------------------------------------

REQUISITOS: tuple[Requisito, ...] = (
    Requisito(
        id="windows_version",
        nombre="Versión de Windows compatible",
        por_que=(
            "El terminal del IDE usa ConPTY, disponible desde Windows 10 versión "
            "1809 (compilación 17763). En una versión anterior el terminal no "
            "puede abrir ninguna sesión interactiva."
        ),
        comprobar=_comprobar_version_windows,
        # No existe un comando de CLI que actualice el sistema operativo con
        # seguridad y sin supervisión -- se avisa, no se automatiza.
        instalar=None,
        requiere_admin=False,
        obligatorio=True,
    ),
    Requisito(
        id="powershell7",
        nombre="PowerShell 7",
        por_que=(
            "Es la terminal preferida del IDE. Si falta, Edecán sigue "
            "funcionando con Windows PowerShell 5.1 (siempre presente en "
            "Windows), solo con un prompt más antiguo."
        ),
        comprobar=_comprobar_pwsh,
        instalar=_instalar_winget("Microsoft.PowerShell"),
        requiere_admin=True,
        obligatorio=False,
    ),
    Requisito(
        id="rutas_largas",
        nombre="Rutas largas de Windows activadas",
        por_que=(
            "Sin esto, un archivo cuya ruta completa pase de 260 caracteres "
            "falla al leerse o guardarse -- típico en repos con carpetas "
            "anidadas como node_modules."
        ),
        comprobar=_comprobar_rutas_largas,
        instalar=[
            "powershell",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem' "
            "-Name LongPathsEnabled -Value 1",
        ],
        requiere_admin=True,
        obligatorio=False,
    ),
    Requisito(
        id="webview2",
        nombre="WebView2 Runtime",
        por_que=(
            "Es el motor con el que Windows dibuja la ventana de Edecán. Si el "
            "instalador no llegó a completarlo o quedó dañado, la aplicación no "
            "puede mostrar ninguna pantalla."
        ),
        comprobar=_comprobar_webview2,
        instalar=_instalar_winget("Microsoft.EdgeWebView2Runtime"),
        requiere_admin=True,
        obligatorio=True,
    ),
    Requisito(
        id="git",
        nombre="Git",
        por_que=(
            "El panel de control de versiones del IDE (estado, diff, commits, "
            "ramas) y clonar repositorios dependen de tener git instalado."
        ),
        comprobar=_comprobar_git,
        instalar=_instalar_winget("Git.Git"),
        requiere_admin=False,
        obligatorio=True,
    ),
)


def _plataforma_soportada() -> bool:
    """``True`` solo en Windows -- la INSTALACIÓN con un clic (``instalar()``,
    más abajo) es exclusiva de esa plataforma: es la única donde el
    manifiesto cerrado de comandos (``REQUISITOS``) tiene sentido de
    seguridad (ver el docstring del módulo). La DETECCIÓN informativa sí se
    extendió a Linux -- ver :func:`detectar` y :data:`REQUISITOS_LINUX`.

    Función propia, en vez de comparar ``os.name`` en línea en cada lugar:
    así los tests pueden fingir "estamos en Windows" monkeypatcheando SOLO
    esta función, sin mutar el ``os.name`` real del proceso -- mutarlo de
    verdad se filtraría a ``pty_compat.abrir_pty`` (que también decide por
    ``os.name`` cuál implementación de terminal usar) y le haría tomar la
    rama Windows en una Mac, donde no hay ``winpty`` importado -- justo la
    clase de acoplamiento que esta función evita.
    """
    return os.name == "nt"


def _plataforma_es_linux() -> bool:
    """Función propia por el mismo motivo que :func:`_plataforma_soportada`
    -- los tests fingen Linux sin tocar ``sys.platform`` real."""
    return sys.platform.startswith("linux")


# ---------------------------------------------------------------------------
# Detección informativa en Linux (encargo del dueño, 1-ago-2026): esta
# pantalla NUNCA instaló nada fuera de Windows y eso no cambia acá -- el
# manifiesto cerrado de ``REQUISITOS`` de arriba es, a propósito, la ÚNICA
# fuente de comandos ejecutables de todo el módulo (ver la regla de
# seguridad del docstring). Lo que sí cambia es que Linux dejaba de decir
# NADA: `detectar()` devolvía `[]` sin haber comprobado una sola cosa, que
# se lee igual que "todo está bien" sin serlo. Cada entrada de abajo SÍ
# comprueba de verdad (mismo patrón que ``_comprobar_git``/``_comprobar_pwsh``)
# y da el comando real de esta plataforma en `por_que` -- nunca `brew`
# (medido en vivo, edecan-prod, 1-ago-2026: `brew: NO EXISTE`) -- pero
# `instalar` se deja en ``None`` siempre: un clic que instale solo en
# Windows sigue siendo la única promesa que este módulo hace.
# ---------------------------------------------------------------------------


def _comprobar_selector_de_carpetas_linux() -> EstadoRequisito:
    encontrado = shutil.which("zenity") or shutil.which("kdialog")
    return EstadoRequisito.CUMPLIDO if encontrado else EstadoRequisito.FALTA


def _comprobar_portapapeles_linux() -> EstadoRequisito:
    """``xclip`` (X11) o ``wl-clipboard`` (Wayland) -- mismo criterio de
    ``WAYLAND_DISPLAY`` que ``actions._linux_clipboard_argv``: se pide la
    herramienta de ESTA sesión, no "cualquiera de las dos"."""
    if os.environ.get("WAYLAND_DISPLAY"):
        encontrado = shutil.which("wl-copy") is not None and shutil.which("wl-paste") is not None
    else:
        encontrado = shutil.which("xclip") is not None
    return EstadoRequisito.CUMPLIDO if encontrado else EstadoRequisito.FALTA


def _comprobar_extra_remote_control() -> EstadoRequisito:
    """El extra opcional ``remote-control`` (``mss`` + ``pynput`` en
    Windows/Linux) que necesitan ``screenshot``/``input_pointer``/``input_key``
    (``actions.py``)."""
    encontrado = (
        importlib.util.find_spec("mss") is not None
        and importlib.util.find_spec("pynput") is not None
    )
    return EstadoRequisito.CUMPLIDO if encontrado else EstadoRequisito.FALTA


def _comprobar_opencode_binario_linux() -> EstadoRequisito:
    """El agente del IDE necesita ``opencode`` resoluble. La búsqueda real
    (``ide_opencode_binario.resolver_binario_opencode``) prueba primero el
    bundle de la app de escritorio -- esta comprobación cubre las dos vías
    que sí aplican a un companion sin empaquetar (el caso de un servidor
    Linux): ``EDECAN_OPENCODE_BIN`` y el PATH."""
    encontrado = bool(os.environ.get("EDECAN_OPENCODE_BIN")) or shutil.which("opencode")
    return EstadoRequisito.CUMPLIDO if encontrado else EstadoRequisito.FALTA


REQUISITOS_LINUX: tuple[Requisito, ...] = (
    Requisito(
        id="linux_selector_de_carpetas",
        nombre="Selector gráfico de carpetas (zenity o kdialog)",
        por_que=(
            "El botón «Elige una carpeta» del IDE necesita uno de los dos para abrir un "
            "diálogo nativo. Instálalo a mano: `sudo apt install zenity` (Debian/Ubuntu), "
            "`sudo dnf install zenity` (Fedora) o `sudo pacman -S zenity` (Arch); "
            "`kdialog` sirve igual si usas KDE."
        ),
        comprobar=_comprobar_selector_de_carpetas_linux,
        # Esta pantalla nunca instala nada fuera de Windows -- ver docstring del módulo.
        instalar=None,
        requiere_admin=False,
        obligatorio=False,
    ),
    Requisito(
        id="linux_portapapeles",
        nombre="Portapapeles del sistema (xclip o wl-clipboard)",
        por_que=(
            "Las acciones de portapapeles necesitan `xclip` en X11 o `wl-clipboard` "
            "(`wl-copy`/`wl-paste`) en Wayland. Instálalo a mano: `sudo apt install xclip` "
            "(X11) o `sudo apt install wl-clipboard` (Wayland); en Fedora, `sudo dnf "
            "install` con esos mismos nombres."
        ),
        comprobar=_comprobar_portapapeles_linux,
        instalar=None,
        requiere_admin=False,
        obligatorio=False,
    ),
    Requisito(
        id="linux_remote_control",
        nombre="Extra 'remote-control' (captura de pantalla y control remoto)",
        por_que=(
            "`screenshot` e `input_pointer`/`input_key` necesitan `mss`, `pynput` y "
            "`Pillow`. `pip install 'edecan-companion[remote-control]'` NO funciona si "
            "instalaste desde PyPI -- este paquete no está publicado en ningún índice "
            "(medido en vivo, edecan-prod, 1-ago-2026: `uv pip install --dry-run` da "
            "'no solution found'). Instala los paquetes directo: "
            "`pip install 'mss>=10.0' 'pynput>=1.7.7' 'Pillow>=10.4'` (o `uv pip install` "
            "si usas uv). Si en cambio corrés desde un clon editable de este repo, "
            "`uv pip install -e 'apps/companion[remote-control]'` sí resuelve el extra."
        ),
        comprobar=_comprobar_extra_remote_control,
        instalar=None,
        requiere_admin=False,
        obligatorio=False,
    ),
    Requisito(
        id="linux_opencode_binario",
        nombre="Binario de opencode (agente del IDE)",
        por_que=(
            "El agente del IDE (chat, ediciones, terminal dirigida) corre sobre "
            "`opencode`, que no se descarga solo. Instálalo con el script del propio "
            "proyecto (`scripts/download-opencode.sh` en el repo) o fija "
            "`EDECAN_OPENCODE_BIN` apuntando a un binario ya descargado."
        ),
        comprobar=_comprobar_opencode_binario_linux,
        instalar=None,
        requiere_admin=False,
        obligatorio=False,
    ),
)


def _requisito_por_id(requisito_id: str) -> Requisito | None:
    for requisito in (*REQUISITOS, *REQUISITOS_LINUX):
        if requisito.id == requisito_id:
            return requisito
    return None


def detectar() -> list[dict[str, Any]]:
    """Estado de cada requisito del manifiesto vigente para esta plataforma,
    sin instalar nada.

    Rápido (cada comprobación es una lectura local: PATH, registro, versión
    del intérprete -- nunca red) y NUNCA lanza: un requisito cuya
    comprobación falle se reporta como "desconocido", no revienta el
    arranque de la app (regla explícita del encargo).

    Windows usa el manifiesto completo (:data:`REQUISITOS`, con instalación
    de un clic). Linux usa :data:`REQUISITOS_LINUX` -- detección real, pero
    SIN instalación automática (ver su docstring): decir qué falta y cómo
    resolverlo a mano es mejor que la lista vacía de antes, que se leía
    igual que "todo está bien" sin haber comprobado una sola cosa (encargo
    del dueño, 1-ago-2026). Cualquier otra plataforma sigue devolviendo `[]`.
    """
    if _plataforma_soportada():
        requisitos = REQUISITOS
    elif _plataforma_es_linux():
        requisitos = REQUISITOS_LINUX
    else:
        return []
    filas: list[dict[str, Any]] = []
    for requisito in requisitos:
        try:
            estado = requisito.comprobar()
        except Exception:  # noqa: BLE001 - "no puede lanzar" es la regla, cualquier excepción cuenta.
            estado = EstadoRequisito.DESCONOCIDO
        filas.append(
            {
                "id": requisito.id,
                "nombre": requisito.nombre,
                "por_que": requisito.por_que,
                "estado": estado.value,
                "instalable": requisito.instalar is not None,
                "requiere_admin": requisito.requiere_admin,
                "obligatorio": requisito.obligatorio,
            }
        )
    return filas


def _es_administrador() -> bool:
    """``True`` si el proceso actual corre elevado. Nunca lanza: fail-closed
    (si no se puede confirmar, se asume que NO hay privilegios) -- coherente
    con "lo que necesita administrador se marca ANTES de pulsar play"."""
    if not _plataforma_soportada():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - cualquier fallo de la API de Win32 es "no confirmado".
        return False


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PreparacionError(ValueError):
    """Solicitud de preparación inválida: ``id`` desconocido, requisito sin
    instalación automática, o plataforma no soportada. Subclase de
    ``ValueError`` a propósito: ``ide_runtime.execute_ide_action`` ya atrapa
    ``ValueError`` y lo traduce a ``{"ok": False, "error": ...}`` -> 422 en el
    router, sin necesitar una rama de excepción propia para este módulo."""


class _Instalacion:
    """Estado en memoria de UNA ejecución de ``instalar`` para un requisito.

    Mismo patrón de cursor que ``ide_sessions.Session`` (evento con
    ``cursor`` creciente, ``leer(cursor)`` devuelve solo lo nuevo) pero sin
    ninguna de las piezas que ese archivo trae para workspaces/turnos de
    agente: esto es una sola ejecución de un argv fijo, no una sesión."""

    def __init__(self) -> None:
        self.estado: str = "ejecutando"  # ejecutando | completado | error
        self.error: str | None = None
        self._eventos: list[dict[str, Any]] = []
        self._cursor = 0
        self._lock = threading.RLock()

    def append(self, tipo: str, texto: str) -> None:
        if not texto:
            return
        with self._lock:
            self._cursor += 1
            self._eventos.append(
                {"cursor": self._cursor, "type": tipo, "text": texto, "timestamp": _now()}
            )

    def leer(self, cursor: int) -> dict[str, Any]:
        with self._lock:
            eventos = [evento for evento in self._eventos if evento["cursor"] > cursor]
            next_cursor = eventos[-1]["cursor"] if eventos else max(cursor, self._cursor)
            return {
                "estado": self.estado,
                "error": self.error,
                "events": eventos,
                "next_cursor": next_cursor,
                "has_more": next_cursor < self._cursor,
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"estado": self.estado, "error": self.error}


class EjecutorPreparacion:
    """Ejecuta el ``instalar`` fijo de un requisito y transmite su salida.

    Un ``id`` no reconocido en :data:`REQUISITOS` se rechaza en
    :meth:`_requisito` -- ANTES de tocar `pty_compat` o el sistema de
    archivos -- con :class:`PreparacionError`. Esa es la frontera de
    seguridad completa de este módulo: el cliente nunca manda un argv, solo
    un identificador que se resuelve contra la lista fija de arriba.
    """

    def __init__(self) -> None:
        self._instalaciones: dict[str, _Instalacion] = {}
        self._lock = threading.Lock()

    def _requisito(self, requisito_id: str) -> Requisito:
        requisito = _requisito_por_id(requisito_id)
        if requisito is None:
            raise PreparacionError(f"Requisito desconocido: {requisito_id!r}.")
        return requisito

    def listar(self) -> dict[str, Any]:
        """``GET /v1/preparacion``: estado de cada requisito más si ESTE
        proceso corre elevado -- lo segundo es lo que permite marcar en la UI
        "esto necesita administrador y no lo tienes" antes de que la persona
        pulse play, no después de que falle."""
        return {"requisitos": detectar(), "elevado": _es_administrador()}

    def instalar(self, requisito_id: str) -> dict[str, Any]:
        """Arranca (o reporta la ya en curso) la instalación de UN requisito.

        Idempotente por diseño: una instalación ya "ejecutando" para este id
        no se relanza -- se devuelve su estado actual. Una que ya terminó
        (bien o mal) SÍ se puede volver a lanzar (reintentar), reemplazando
        su registro anterior.
        """
        requisito = self._requisito(requisito_id)
        if not _plataforma_soportada():
            raise PreparacionError("La preparación de requisitos solo aplica en Windows.")
        if requisito.instalar is None:
            raise PreparacionError(
                f"«{requisito.nombre}» no tiene instalación automática; revísalo manualmente."
            )
        with self._lock:
            existente = self._instalaciones.get(requisito_id)
            if existente is not None and existente.estado == "ejecutando":
                return {"id": requisito_id, **existente.snapshot()}
            instalacion = _Instalacion()
            self._instalaciones[requisito_id] = instalacion

        if requisito.requiere_admin and not _es_administrador():
            instalacion.error = (
                "Este paso necesita permisos de administrador. Cierra Edecán y vuelve a "
                "abrirlo con clic derecho -> «Ejecutar como administrador»."
            )
            instalacion.estado = "error"
            instalacion.append("status", instalacion.error)
            return {"id": requisito_id, **instalacion.snapshot()}

        instalacion.append("status", f"Instalando «{requisito.nombre}»…")
        try:
            terminal = pty_compat.abrir_pty(
                list(requisito.instalar),
                cwd=tempfile.gettempdir(),
                env=os.environ.copy(),
            )
        except pty_compat.PTYError as exc:
            instalacion.error = f"No se pudo iniciar la instalación: {exc}"
            instalacion.estado = "error"
            instalacion.append("status", instalacion.error)
            return {"id": requisito_id, **instalacion.snapshot()}

        threading.Thread(
            target=self._leer_proceso,
            args=(requisito, instalacion, terminal),
            daemon=True,
            name=f"edecan-preparacion-{requisito_id}",
        ).start()
        return {"id": requisito_id, **instalacion.snapshot()}

    def _leer_proceso(
        self, requisito: Requisito, instalacion: _Instalacion, terminal: pty_compat.TerminalPTY
    ) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            try:
                chunk = terminal.leer(4096)
            except pty_compat.PTYError:
                break
            if not chunk:
                break
            texto = decoder.decode(chunk)
            if texto:
                instalacion.append("output", texto)
        codigo = terminal.esperar()
        # Verificación real, no solo el código de salida: un instalador puede
        # devolver 0 sin haber cambiado nada (p. ej. ya estaba instalado bajo
        # otro mecanismo, o un error silencioso de winget). Se vuelve a
        # comprobar el requisito de verdad antes de anunciar éxito.
        try:
            estado_final = requisito.comprobar()
        except Exception:  # noqa: BLE001 - "no puede lanzar", igual que detectar().
            estado_final = EstadoRequisito.DESCONOCIDO
        if codigo == 0 and estado_final == EstadoRequisito.CUMPLIDO:
            instalacion.estado = "completado"
            instalacion.append("exit", f"«{requisito.nombre}» quedó instalado.")
        else:
            instalacion.estado = "error"
            instalacion.error = (
                f"El comando terminó (código {codigo}) pero «{requisito.nombre}» sigue sin "
                "cumplirse. Revisa la salida de arriba."
            )
            instalacion.append("exit", instalacion.error)

    def leer(self, requisito_id: str, cursor: int) -> dict[str, Any]:
        self._requisito(requisito_id)  # valida el id aunque nunca se haya instalado.
        with self._lock:
            instalacion = self._instalaciones.get(requisito_id)
        if instalacion is None:
            return {
                "id": requisito_id,
                "estado": "no_iniciado",
                "error": None,
                "events": [],
                "next_cursor": 0,
                "has_more": False,
            }
        return {"id": requisito_id, **instalacion.leer(cursor)}
