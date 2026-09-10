"""Denylist de comandos de terminal peligrosos (defensa en profundidad).

``es_comando_peligroso(command)`` es la ÚNICA puerta que decide si un comando de
terminal es demasiado destructivo para correr, venga de quien venga (bot o
dueño) y tenga el permiso que tenga (``allow_all_commands``, ``allowed_commands``
o ``sudo``). La usan TANTO el companion standalone (``actions._run_command``)
como el puente local instalado (``apps/local/.../companion_bridge.py``), para
que no haya dos listas negras que se desincronizan.

Por qué no subcadenas literales: ``"rm -rf" in command`` es frágil y evadible.
Se colaban ``rm  -rf`` (doble espacio), ``rm -Rf``, ``\\rm -rf``, ``rm -rfv``,
``rm --force --recursive`` o ``rm -r -f``. En su lugar:

1. Se parte el comando con ``shlex`` y se evalúa el EJECUTABLE + sus flags
   normalizados (mayúsculas, flags combinados ``-rf``, alias ``--force``) —
   igual que ``actions._split_command`` parte el comando real.
2. Los patrones que dependen de la forma COMPLETA de la línea (pipes a una
   shell, encadenado con ``;``/``&&``/``||``, redirección a un dispositivo de
   bloque, ``osascript ... do shell``) se chequean con expresiones regulares
   acotadas sobre la cadena cruda: ``shlex`` no los aísla porque el terminal
   compartido del puente SÍ es un shell real (ver ``companion_bridge``).

Fail-closed: un comando que no se puede parsear se reporta como peligroso.
Un falso positivo cuesta una negativa; un falso negativo cuesta la máquina.
"""

from __future__ import annotations

import os
import re
import shlex

# --- Ejecutables peligrosos POR SÍ SOLOS -------------------------------------
# Cualquier invocación de estos binarios es destructiva o una escalada de
# privilegio, sin importar sus argumentos.
_EJECUTABLES_SIEMPRE_PELIGROSOS = frozenset(
    {
        "dd",  # escritura cruda de dispositivos; rara vez legítima en un terminal de bot
        "mkfs",
        "newfs",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "passwd",
    }
)

# Wrappers que NO cambian el binario real que se ejecuta debajo: se saltan para
# llegar al comando efectivo (``sudo rm -rf``, ``env rm -rf``, ``command rm``...).
# ``sudo``/``doas`` NO están acá: se bloquean en seco (escalada de privilegio).
_WRAPPERS = frozenset(
    {"env", "command", "nohup", "nice", "time", "watch", "setsid", "xargs"}
)


def _nombre_ejecutable(token: str) -> str:
    """`rm`, `sudo`, etc. desde `argv[0]`, normalizado (basename, minúsculas)."""
    return os.path.basename(token.strip("'\"") or "").lower()


def _rm_peligroso(args: list[str]) -> bool:
    """`True` si `rm` borra de forma recursiva o forzada.

    Cubre las evasiones del patrón literal ``"rm -rf"``: ``rm  -rf`` (doble
    espacio lo resuelve ``shlex``), ``rm -Rf``, ``\\rm -rf``, ``rm -rfv``,
    ``rm -fr``, ``rm -r -f``, ``rm --force``, ``rm --recursive --force``.
    """
    cortos: set[str] = set()
    largos: set[str] = set()
    for arg in args:
        if arg.startswith("--"):
            # `--force`, `--recursive` → nombre sin el prefijo `--`.
            largos.add(arg[2:].lower())
        elif arg.startswith("-") and len(arg) > 1:
            # `-rf`, `-Rf`, `-fr`, `-rfv` → letras sueltas, en minúscula.
            cortos.update(ch.lower() for ch in arg[1:])
    if {"r", "f"} & cortos:
        return True
    return bool({"recursive", "force"} & largos)


def _chmod_peligroso(args: list[str]) -> bool:
    """`chmod` con modo 777 (o 0777): mundo escribible/ejecutable. Incluye `-R 777`."""
    for arg in args:
        if arg.lstrip("0") == "777":
            return True
    return False


def _find_peligroso(args: list[str]) -> bool:
    """`find <ruta-absoluta> -delete` (el clásico `find / -delete`).

    `find . -delete` (limpieza relativa del workspace) sigue permitido; solo se
    bloquea el borrado masivo anclado en una ruta absoluta.
    """
    if "-delete" not in args:
        return False
    return any(arg.startswith("/") for arg in args)


def _ejecutable_peligroso(argv: list[str]) -> bool:
    """Evalúa el ejecutable efectivo (tras wrappers) y sus argumentos."""
    i = 0
    total = len(argv)
    while i < total:
        nombre = _nombre_ejecutable(argv[i])
        if nombre in ("sudo", "doas"):
            return True  # escalada de privilegio: nunca permitida
        if nombre in _WRAPPERS:
            i += 1
            if nombre == "env":
                # `env KEY=VALUE cmd ...`: saltar las asignaciones de entorno.
                while i < total and "=" in argv[i] and not argv[i].startswith("-"):
                    i += 1
            continue
        break
    if i >= total:
        return False

    nombre = _nombre_ejecutable(argv[i])
    args = argv[i + 1 :]

    if nombre in _EJECUTABLES_SIEMPRE_PELIGROSOS or nombre.startswith(("mkfs.", "newfs.")):
        return True
    if nombre == "rm":
        return _rm_peligroso(args)
    if nombre == "chmod":
        return _chmod_peligroso(args)
    if nombre == "find":
        return _find_peligroso(args)
    if nombre == "diskutil":
        return any(arg.lower() == "erase" for arg in args)
    return False


# --- Patrones sobre la cadena cruda (interpretación de shell) -----------------
# El terminal compartido del puente local es un shell real, así que ``|``,
# ``;``, ``&&``, ``||`` y ``>`` sí encadenan procesos ahí. ``shlex`` no los
# aísla: se chequean con regex acotadas.

# `cmd | bash`, `cmd | zsh`, `cmd | /bin/sh`, `cmd | /usr/bin/bash`, etc.
_SHELL_PIPES = (
    "bash",
    "zsh",
    "dash",
    "ksh",
    "fish",
    "/bin/sh",
    "/bin/bash",
    "/bin/zsh",
    "/bin/dash",
    "/bin/ksh",
    "/usr/bin/sh",
    "/usr/bin/bash",
    "/usr/bin/zsh",
    "/usr/local/bin/bash",
    "/usr/local/bin/zsh",
)
_PIPE_A_SHELL = re.compile(
    r"\|\s*(?:" + "|".join(re.escape(s) for s in _SHELL_PIPES) + r")\b"
)

# `curl -fsSL https://x | sh`: el flag y la URL van en medio, así que la pipe a
# un `sh` PELADO solo se considera peligrosa si hay `curl` presente (evita el
# falso positivo `... | sha256sum`, y además `\b` descarta sufijos).
_PIPE_SH_PELADO = re.compile(r"\|\s*sh\b")

_PATRONES_CRUDOS = (
    re.compile(r"(?:;|&&|\|\|)\s*rm\b"),  # `; rm`, `&& rm`, `|| rm`
    # Redirección a un dispositivo de bloque (no `/dev/null`, `/dev/stdout`...).
    re.compile(r">\s*/dev/(?:sd[a-z]+|disk\d+|nvme\d+n\d+|vd[a-z]+|hd[a-z]+|mmcblk\d+)"),
    _PIPE_A_SHELL,
)


def _patron_crudo_peligroso(command: str) -> bool:
    if any(patron.search(command) for patron in _PATRONES_CRUDOS):
        return True
    if "curl" in command and _PIPE_SH_PELADO.search(command):
        return True
    # Escalado de privilegios vía AppleScript: exige las TRES subcadenas
    # (`osascript` solo, p. ej. para abrir apps, sigue permitido).
    if (
        "osascript" in command
        and "tell application" in command
        and "do shell" in command
    ):
        return True
    return False


def es_comando_peligroso(command: str) -> bool:
    """`True` si `command` nunca debe ejecutarse en el terminal compartido.

    ``command`` es el texto crudo que llega en ``params["command"]`` (no es
    confiable: lo pudo escribir un bot). Vacío/``None`` → ``False`` (no hay
    nada que ejecutar). No lanza: ante cualquier comando que no pueda parsear,
    devuelve ``True`` (fail-closed).
    """
    if not isinstance(command, str) or not command.strip():
        return False

    if _patron_crudo_peligroso(command):
        return True

    try:
        argv = shlex.split(command)
    except ValueError:
        # Comillas sin cerrar u otra forma mal formada: no se puede probar que
        # sea seguro → fail-closed.
        return True

    if not argv:
        return False
    return _ejecutable_peligroso(argv)