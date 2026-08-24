"""Resolución portable de binarios externos para los `Criterio(kind="command")`.

`subprocess.run([...], shell=False)` en Windows NO hace lo que uno espera con
un nombre pelado como `"npm"` o `"npx"`: esos son en realidad `npm.cmd` /
`npx.cmd` (shims de `.cmd` que instala Node), y `CreateProcess` sin `shell=True`
solo intenta añadir `.exe` al buscar en el PATH — nunca prueba `PATHEXT`
(`.cmd`, `.bat`). El resultado es un `FileNotFoundError` en la primera línea,
no un fallo del criterio: el banco entero se cae en vez de reportar que la
tarea no se resolvió.

`shutil.which` sí resuelve `PATHEXT` en Windows (y es un no-op razonable en
POSIX), así que la ruta completa que devuelve ya apunta al `.cmd`/`.exe`
correcto y `CreateProcess` la ejecuta sin ambigüedad. Si el binario no está en
el PATH se deja el nombre tal cual: el `subprocess.run` fallará con un mensaje
claro («no se encontró X») en vez de que este módulo invente una ruta.
"""

from __future__ import annotations

import shutil


def resolver_binario(nombre: str) -> str:
    """Devuelve la ruta resuelta de `nombre` en el PATH, o `nombre` si no está."""
    return shutil.which(nombre) or nombre
