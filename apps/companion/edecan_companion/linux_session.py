"""Descubre la sesión gráfica (X11/Wayland) activa de un usuario en Linux.

Por qué existe: cuando el companion corre como servicio systemd (o cualquier
proceso lanzado sin terminal de login -- justo cómo corre en producción, ver
`docs/linux.md`), NO hereda `DISPLAY`/`WAYLAND_DISPLAY`/
`DBUS_SESSION_BUS_ADDRESS` de la sesión gráfica del usuario, aunque esa
sesión SÍ esté corriendo. Medido en vivo (servidor `edecan-prod`, 1-ago-2026):
`who` no la ve porque las sesiones de xrdp no entran en utmp, pero
`pgrep -x Xorg` sí la encuentra, y leer `/proc/<pid>/environ` del proceso de
sesión (`xfce4-session` en ese caso) da el `DISPLAY`/`DBUS_SESSION_BUS_ADDRESS`
reales. Sin esas variables, todo lo que dependa de una ventana -- captura de
pantalla, teclado/mouse remoto, selector de carpetas, `open_app` -- o falla
con un traceback crudo, o se queda colgado varios minutos esperando un
diálogo que nunca puede aparecer (medido: el selector de carpetas sin DBUS
tarda los 5 minutos completos de `PICKER_TIMEOUT_SECONDS` en vez de fallar
rápido con una pista útil).

Esta función imita lo que un `.desktop`/`systemd --user` haría de forma
nativa: busca, entre los procesos del MISMO usuario, el primero que sea una
sesión gráfica conocida y copia sus variables de entorno relevantes. Nunca
sobreescribe una variable que el proceso YA tenga puesta explícitamente --
así una configuración manual (`DISPLAY=:1` en `companion.yaml`/systemd/
`~/.profile`) sigue mandando sobre lo que se descubra aquí.

Solo lee `/proc/<pid>/environ` de procesos que le pertenecen al mismo UID
que corre el companion (el kernel ya lo exige así -- leer el de otro usuario
da `PermissionError`, que se descarta como cualquier otro fallo de lectura),
así que esto no es una vía nueva para fugar datos de otras cuentas.
"""

from __future__ import annotations

import os
from pathlib import Path

# Punto de inyección para pruebas: en producción es siempre `/proc`, pero los
# tests lo apuntan a un árbol de directorios falso bajo `tmp_path` en vez de
# monkeypatchear métodos de `Path` a nivel de clase (que interferiría con
# CUALQUIER otro uso de `Path` durante el test, incluido el propio pytest).
_PROC_ROOT = Path("/proc")

# Variables que de verdad hacen falta para hablar con la sesión gráfica:
# `XDG_RUNTIME_DIR` viaja junto porque muchos backends (zenity/GTK, D-Bus del
# usuario) lo usan para resolver el socket del bus si `DBUS_SESSION_BUS_ADDRESS`
# no está puesta explícitamente.
_SESSION_ENV_VARS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "XAUTHORITY",
)

# Orden de preferencia: el propio servidor gráfico primero (siempre vivo
# mientras haya sesión), luego los gestores de sesión de los escritorios más
# comunes. Un `Xorg`/`Xwayland` encontrado ya trae `DISPLAY` en su propio
# entorno; los gestores de sesión suelen traer además el `DBUS_SESSION_BUS_ADDRESS`
# que `Xorg` normalmente no expone.
_SESSION_PROCESS_NAMES = (
    "xfce4-session",
    "gnome-session",
    "gnome-session-binary",
    "ksmserver",
    "startplasma-x11",
    "startplasma-wayland",
    "lxsession",
    "lxqt-session",
    "mate-session",
    "cinnamon-session",
    "Xorg",
    "Xwayland",
    "X",
)


def _leer_environ(pid: str) -> dict[str, str]:
    """Variables de `/proc/<pid>/environ`, o `{}` si no se puede leer
    (proceso ajeno, ya terminó, `/proc` no montado -- cualquier fallo aquí
    es "no hay pista", nunca un error que deba propagarse)."""
    try:
        crudo = (_PROC_ROOT / pid / "environ").read_bytes()
    except OSError:
        return {}
    entorno: dict[str, str] = {}
    for trozo in crudo.split(b"\x00"):
        clave, separador, valor = trozo.partition(b"=")
        if not separador:
            continue
        try:
            entorno[clave.decode("utf-8")] = valor.decode("utf-8", errors="replace")
        except UnicodeDecodeError:
            continue
    return entorno


def _pids_de_sesion(uid_propio: int) -> list[str]:
    """PIDs, en orden de preferencia, de procesos de sesión gráfica del mismo
    usuario que corre este proceso."""
    try:
        entradas = os.listdir(_PROC_ROOT)
    except OSError:
        return []
    por_nombre: dict[str, list[str]] = {}
    for pid in entradas:
        if not pid.isdigit():
            continue
        try:
            comm = (_PROC_ROOT / pid / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if comm not in _SESSION_PROCESS_NAMES:
            continue
        try:
            propietario = (_PROC_ROOT / pid).stat().st_uid
        except OSError:
            continue
        if propietario != uid_propio:
            continue
        por_nombre.setdefault(comm, []).append(pid)
    ordenados: list[str] = []
    for nombre in _SESSION_PROCESS_NAMES:
        ordenados.extend(por_nombre.get(nombre, ()))
    return ordenados


def descubrir_variables_de_sesion() -> dict[str, str]:
    """Variables de la sesión gráfica activa descubiertas, o `{}` si el
    proceso ya tiene una anunciada (nada que descubrir) o no se encontró
    ninguna (headless de verdad, o plataforma sin `/proc`)."""
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return {}  # ya hay sesión anunciada -- no pisarla con una descubierta
    getuid = getattr(os, "getuid", None)
    if getuid is None:  # pragma: no cover - getuid no existe fuera de POSIX
        return {}
    uid = getuid()
    for pid in _pids_de_sesion(uid):
        entorno = _leer_environ(pid)
        encontradas = {clave: entorno[clave] for clave in _SESSION_ENV_VARS if entorno.get(clave)}
        if encontradas.get("DISPLAY") or encontradas.get("WAYLAND_DISPLAY"):
            _completar_xauthority(encontradas, uid)
            return encontradas
    return {}


def _completar_xauthority(encontradas: dict[str, str], uid: int) -> None:
    """Añade `XAUTHORITY` cuando la sesión no la publica pero el archivo existe.

    Sin esto, X11 rechaza la conexión con "Authorization required, but no
    authorization protocol specified" aunque `DISPLAY` sea correcto.

    Muchos gestores de sesión (xrdp entre ellos) NO ponen `XAUTHORITY` en el
    entorno: los clientes de X caen por convención a `$HOME/.Xauthority`. Eso
    funciona mientras el proceso comparta el `HOME` del dueño del escritorio —
    y deja de funcionar en cuanto corre como servicio con otro `HOME`
    (Edecán usa `HOME=/opt/edecan`), que es precisamente el caso que este
    módulo existe para resolver.

    Por eso el home se resuelve del UID, no de `$HOME`: el UID es el del dueño
    de la sesión que acabamos de encontrar, y es la única fuente fiable aquí.
    """
    if encontradas.get("XAUTHORITY") or "DISPLAY" not in encontradas:
        return
    try:
        import pwd

        home = Path(pwd.getpwuid(uid).pw_dir)
    except (KeyError, OSError, ImportError):  # pragma: no cover - UID sin entrada en passwd
        return
    candidato = home / ".Xauthority"
    try:
        if candidato.is_file():
            encontradas["XAUTHORITY"] = str(candidato)
    except OSError:  # pragma: no cover - home inaccesible
        return


def entorno_fusionado(base: dict[str, str] | None = None) -> dict[str, str]:
    """`os.environ` (o `base`) fusionado con la sesión gráfica descubierta.

    Nunca pisa una variable que `base` ya tenga puesta -- ver el docstring
    del módulo sobre por qué la config manual siempre gana.
    """
    resultado = dict(base if base is not None else os.environ)
    for clave, valor in descubrir_variables_de_sesion().items():
        resultado.setdefault(clave, valor)
    return resultado
