"""Rutas de datos/config/caché/temporales — una sola fuente de verdad por plataforma.

¿Por qué existe esto? Antes de este módulo, `"~/.edecan/data"` estaba escrito
como literal repetido en al menos seis archivos (`apps/api/edecan_api/
config.py`, `apps/local/edecan_local/runtime.py`, `apps/companion/
edecan_companion/ide_workers_agent.py`, `packages/toolkit/edecan_toolkit/
{autorreparacion,creator,seguridad}.py`, `packages/design-studio/
edecan_design_studio/studio_tools.py`) y no existía NINGÚN lugar que
resolviera caché o temporales por plataforma. Ver `docs/edecan-windows.md`
§2 ("Dónde viven los datos") para el contrato completo; este módulo lo
implementa.

Decisión ya tomada ahí, no re-discutida aquí: el CLI/servidor local
(`edecan_local` en modo standalone, sin Tauri) mantiene su default
`~/.edecan/data` EN TODAS las plataformas -- `Path.home()`/`expanduser()` ya
resuelven correcto en Windows (`C:\\Users\\<usuario>`) y la consistencia
entre plataformas vale más que la idiomaticidad de cada una (nada de
`%LOCALAPPDATA%` para esto: sería un cambio de diseño no pedido). La app de
escritorio Tauri sigue pasando su propio `--data-dir` bajo
`app_data_dir()` (Rust) — esto no la toca.

Lo que SÍ es nuevo: `cache_dir()`/`temp_dir()` resueltos de verdad por
plataforma (nadie los tenía), y las validaciones de nombre de archivo y de
longitud de ruta que hoy no existían en ningún lado (`docs/edecan-windows.md`
§2 "Nombres prohibidos" y "Longitud 260").

Nota de arquitectura para quien migre `apps/companion`: ese paquete se instala
deliberadamente SIN depender de `edecan_core` (ver `apps/companion/
pyproject.toml`, comentario "Deliberadamente SIN dependencias..."), así que
NO puede importar este módulo. Su equivalente autocontenido vive en
`apps/companion/edecan_companion/platform_paths.py` -- duplicación
intencional, mismo patrón que ya usa `apps/local/edecan_local/runtime.py`
para `DEFAULT_PORT`/`DATA_DIR` (ver el comentario ahí).
"""

from __future__ import annotations

import os
import platform
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Datos del CLI/servidor local — mismo valor en TODA plataforma (ver docstring
# del módulo). Se deja como `str` (no `Path`) porque los consumidores actuales
# lo usan como default de un campo `str` (p. ej. pydantic-settings) o de un
# `getattr(settings, "DATA_DIR", DEFAULT_DATA_DIR)` -- cambiar el tipo aquí
# rompería esos call sites sin necesidad.
# ---------------------------------------------------------------------------
DEFAULT_DATA_DIR = "~/.edecan/data"

# Variable de entorno que, si está fijada, gana sobre `DEFAULT_DATA_DIR` en
# todos los puntos de entrada (`edecan_local.runtime`, `edecan_companion.
# ide_workers_agent`, la app de escritorio Tauri vía `--data-dir`).
DATA_DIR_ENV_VAR = "EDECAN_DATA_DIR"


def resolver_data_dir(
    configurado: str | os.PathLike[str] | None = None,
    *,
    env_var: str = DATA_DIR_ENV_VAR,
) -> Path:
    """Resuelve el directorio de datos: `configurado` > variable de entorno > default.

    Siempre expande `~` (portable: `Path.expanduser()` lee `USERPROFILE` en
    Windows y `HOME` en POSIX) y no crea la carpeta -- eso sigue siendo
    responsabilidad de quien la usa (algunos la crean con `0o700`, permiso
    que en Windows es un no-op aceptado, ver `docs/edecan-windows.md` §2).
    """
    valor = configurado if configurado is not None else os.environ.get(env_var, DEFAULT_DATA_DIR)
    return Path(valor).expanduser()


# ---------------------------------------------------------------------------
# Caché y temporales por plataforma -- resuelto por primera vez aquí.
# ---------------------------------------------------------------------------


def cache_dir(app_name: str = "edecan") -> Path:
    """Directorio de caché idiomático por plataforma (no se crea, solo se resuelve).

    Windows: ``%LOCALAPPDATA%\\<app_name>\\Cache``. macOS: ``~/Library/Caches/
    <app_name>``. Linux/otros: ``$XDG_CACHE_HOME/<app_name>`` o
    ``~/.cache/<app_name>`` si `XDG_CACHE_HOME` no está fijada.
    """
    sistema = platform.system()
    if sistema == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / app_name / "Cache"
    if sistema == "Darwin":
        return Path.home() / "Library" / "Caches" / app_name
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return base / app_name


def temp_dir(app_name: str = "edecan", *, crear: bool = True) -> Path:
    """Subcarpeta propia de Edecán dentro del temporal del sistema operativo.

    NUNCA construir `/tmp/algo` a mano: `/tmp` no existe en Windows, y hasta
    en POSIX `tempfile.gettempdir()` respeta `$TMPDIR`/`$TMP` cuando está
    fijada (por ejemplo en sandboxes de CI) -- un literal `/tmp` lo ignoraría.
    Esta función es la única fuente de verdad del repo para "dame una
    carpeta temporal propia".
    """
    destino = Path(tempfile.gettempdir()) / app_name
    if crear:
        destino.mkdir(parents=True, exist_ok=True)
    return destino


# ---------------------------------------------------------------------------
# Validación de nombres — aplicada en TODAS las plataformas a propósito
# (docs/edecan-windows.md §2 "Nombres prohibidos"): un archivo creado desde
# macOS/Linux con un nombre reservado o carácter prohibido de Windows
# rompería el checkout en la PC del dueño. Mejor rechazarlo donde se crea.
# ---------------------------------------------------------------------------


class NombreInvalidoError(ValueError):
    """Un nombre de archivo/carpeta no es válido en alguna plataforma soportada."""


# '/' y '\\' se incluyen aunque ya los prohíba cualquier filesystem como
# separador: esta función valida un NOMBRE (un solo componente de ruta,
# nunca una ruta completa con separadores) y debe rechazarlos también, no
# partir la cadena en silencio si alguien se equivoca y pasa una ruta.
_CARACTERES_PROHIBIDOS_WINDOWS = frozenset('<>:"/\\|?*') | {chr(c) for c in range(32)}

_NOMBRES_RESERVADOS_WINDOWS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

MAX_PATH_WINDOWS = 260


def validar_nombre_multiplataforma(nombre: str) -> None:
    """Lanza `NombreInvalidoError` si `nombre` no sería válido como archivo/carpeta en Windows.

    Se llama en todas las plataformas (no solo en Windows) por diseño: ver
    docstring de la sección arriba. El mensaje siempre nombra el carácter o
    la razón exacta, nunca un genérico "nombre inválido".
    """
    if not nombre:
        raise NombreInvalidoError("el nombre no puede estar vacío")
    for caracter in nombre:
        if caracter in _CARACTERES_PROHIBIDOS_WINDOWS:
            raise NombreInvalidoError(
                f"{nombre!r} contiene el carácter {caracter!r}, prohibido en Windows"
            )
    if nombre[-1] in (" ", "."):
        raise NombreInvalidoError(
            f"{nombre!r} termina en espacio o punto -- Windows lo recorta o lo rechaza"
        )
    base = nombre.split(".", 1)[0].upper()
    if base in _NOMBRES_RESERVADOS_WINDOWS:
        raise NombreInvalidoError(
            f"{nombre!r} usa el nombre reservado de Windows {base!r} (con o sin extensión)"
        )


def advertir_si_ruta_larga(ruta: Path | str, *, limite: int = MAX_PATH_WINDOWS) -> str | None:
    """Devuelve un mensaje en español si `ruta` alcanza/supera el límite clásico de Windows.

    No trunca ni reescribe nada (fase 1 de `docs/edecan-windows.md` §2 no
    cambia rutas del usuario): solo da un texto listo para loguear o para
    `edecan doctor` (fase 2). `None` cuando la ruta es segura.
    """
    texto = str(ruta)
    if len(texto) >= limite:
        return (
            f"la ruta mide {len(texto)} caracteres (límite clásico de Windows: {limite}); "
            f"si 'LongPathsEnabled' no está activo en el registro, esta operación puede "
            f"fallar: {texto}"
        )
    return None
