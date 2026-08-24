"""Rutas de datos/config/caché/temporales del companion — por plataforma.

Duplicado a propósito de `packages/core/edecan_core/platform_paths.py`: este
paquete se instala deliberadamente SIN depender de `edecan_core` (ver
`apps/companion/pyproject.toml`, comentario "Deliberadamente SIN
dependencias..." -- es el único paquete pensado para instalarse solo, en la
máquina del propio usuario). Mismo patrón que ya usa `apps/local/
edecan_local/runtime.py` para `DEFAULT_PORT`/`DATA_DIR` ("Duplicados a
propósito"): los valores son los mismos, declarados dos veces porque
importar el paquete hermano aquí violaría el aislamiento de dependencias que
el resto del comentario de `pyproject.toml` pide.

Si cambias una constante o regla aquí, cámbiala también en
`edecan_core.platform_paths` (y viceversa) — no hay forma automática de
mantenerlas sincronizadas, así que ambos módulos llevan una prueba de
contrato compartida (`apps/companion/tests/test_platform_paths.py` +
`packages/core/tests/test_platform_paths.py`) que corre los mismos casos
contra las dos implementaciones.

Ver `docs/edecan-windows.md` §2 ("Dónde viven los datos", "Nombres
prohibidos", "Longitud 260") para el contrato completo.
"""

from __future__ import annotations

import os
import platform
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Datos del companion standalone — `~/.edecan/data` en TODA plataforma
# (decisión de docs/edecan-windows.md §2: `Path.home()`/`expanduser()` ya
# resuelven bien en Windows, y la consistencia entre plataformas vale más que
# la idiomaticidad de cada una). La app de escritorio Tauri no usa esto: pasa
# su propio `--data-dir` bajo `app_data_dir()` (Rust).
# ---------------------------------------------------------------------------
DEFAULT_DATA_DIR = "~/.edecan/data"
DATA_DIR_ENV_VAR = "EDECAN_DATA_DIR"

DEFAULT_CONFIG_DIR = Path.home() / ".edecan"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "companion.yaml"
AUDIT_LOG_FILENAME = "companion.log"

DEFAULT_SANDBOX_DIR = "~/EdecanSandbox"
# Carpeta visible donde aterrizan los archivos que el teléfono envía y desde
# donde puede recuperar los que el dueño deja ahí (transferencia de archivos
# del control remoto). A propósito FUERA del sandbox del IDE: es un buzón
# compartido de cara al usuario, no el área de trabajo del asistente.
DEFAULT_TRANSFER_DIR = "~/Edecán/Compartidos"


def resolver_data_dir(
    configurado: str | os.PathLike[str] | None = None,
    *,
    env_var: str = DATA_DIR_ENV_VAR,
) -> Path:
    """Resuelve el directorio de datos: `configurado` > variable de entorno > default."""
    valor = configurado if configurado is not None else os.environ.get(env_var, DEFAULT_DATA_DIR)
    return Path(valor).expanduser()


# ---------------------------------------------------------------------------
# Caché y temporales por plataforma.
# ---------------------------------------------------------------------------


def cache_dir(app_name: str = "edecan") -> Path:
    """Directorio de caché idiomático por plataforma (no se crea, solo se resuelve)."""
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

    NUNCA construir `/tmp/algo` a mano: no existe en Windows, y hasta en
    POSIX `tempfile.gettempdir()` respeta `$TMPDIR`/`$TMP` cuando está fijada.
    """
    destino = Path(tempfile.gettempdir()) / app_name
    if crear:
        destino.mkdir(parents=True, exist_ok=True)
    return destino


# ---------------------------------------------------------------------------
# Validación de nombres — aplicada en TODAS las plataformas a propósito: un
# archivo creado desde macOS/Linux con un nombre reservado o carácter
# prohibido de Windows rompería el checkout en la PC del dueño.
# ---------------------------------------------------------------------------


class NombreInvalidoError(ValueError):
    """Un nombre de archivo/carpeta no es válido en alguna plataforma soportada."""


_CARACTERES_PROHIBIDOS_WINDOWS = frozenset('<>:"/\\|?*') | {chr(c) for c in range(32)}

_NOMBRES_RESERVADOS_WINDOWS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

MAX_PATH_WINDOWS = 260


def validar_nombre_multiplataforma(nombre: str) -> None:
    """Lanza `NombreInvalidoError` si `nombre` no sería válido como archivo/carpeta en Windows."""
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

    `None` cuando la ruta es segura. No trunca ni reescribe nada.
    """
    texto = str(ruta)
    if len(texto) >= limite:
        return (
            f"la ruta mide {len(texto)} caracteres (límite clásico de Windows: {limite}); "
            f"si 'LongPathsEnabled' no está activo en el registro, esta operación puede "
            f"fallar: {texto}"
        )
    return None


# ---------------------------------------------------------------------------
# Reemplazo atómico con reintentos -- decisión del plan (docs/edecan-windows.md
# §"Archivos bloqueados"): en Windows, un antivirus o un cliente de
# sincronización (OneDrive, Dropbox) puede tener el archivo destino abierto un
# instante y `os.replace` revienta con `PermissionError` justo cuando el
# companion intenta guardar su estado -- en POSIX el mismo reemplazo nunca
# falla así. Antes de esta función, esta misma lógica (5 intentos, backoff
# 50->800ms) estaba escrita una vez en `ide_sessions.py` y quedaba pendiente
# para `ide_files.py`/`ide_workspaces.py` (cada uno con su propio `os.replace`
# sin reintentos): se centraliza aquí, el módulo de primitivas portables de
# companion, para que los tres (y cualquier otro que persista un archivo) usen
# la misma implementación en vez de tres copias divergentes.
# ---------------------------------------------------------------------------
DEFAULT_REPLACE_RETRIES = 5
DEFAULT_REPLACE_BACKOFF_INITIAL_S = 0.05
DEFAULT_REPLACE_BACKOFF_MAX_S = 0.8


def reemplazar_con_reintentos(
    origen: Path | str,
    destino: Path | str,
    *,
    intentos: int = DEFAULT_REPLACE_RETRIES,
    espera_inicial: float = DEFAULT_REPLACE_BACKOFF_INITIAL_S,
    espera_maxima: float = DEFAULT_REPLACE_BACKOFF_MAX_S,
) -> None:
    """`os.replace` con reintentos SOLO en Windows ante `PermissionError`.

    En POSIX un `PermissionError` real (permisos de verdad, no un lock
    transitorio) no se arregla reintentando, así que ahí se deja subir en el
    primer intento como siempre -- el reintento es puramente un mitigador del
    "archivo bloqueado un instante" que solo existe en NTFS.
    """
    espera = espera_inicial
    for intento in range(intentos):
        try:
            os.replace(origen, destino)
            return
        except PermissionError:
            if os.name != "nt" or intento == intentos - 1:
                raise
            time.sleep(espera)
            espera = min(espera * 2, espera_maxima)
