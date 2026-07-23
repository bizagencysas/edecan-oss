"""`ensure_postgres(data_dir)` — resuelve el Postgres que usa el resto del
runner local (`ARCHITECTURE.md` §12f, dueño WP-V3-05), en dos modos:

- **Avanzado** (env `EDECAN_DATABASE_URL` fijada): el cliente trae SU PROPIO
  Postgres (uno que ya corría, o uno remoto) — se usa esa URL tal cual, sin
  tocar `pgserver` para nada. Pensado para quien ya tiene infraestructura
  propia y no quiere el Postgres embebido (`docs/desktop-local.md`).
- **Por defecto** (nada fijado): Postgres EMBEBIDO vía `pgserver`, dependencia
  del runtime `edecan-local` — trae binarios de Postgres 16 +
  pgvector, sin Docker ni que el cliente instale nada aparte. La primera vez
  que se llama sobre un `data_dir/pg` vacío, `pgserver.get_server(...)`
  inicializa el cluster; las veces siguientes simplemente lo arranca.

Ambos modos devuelven una tupla `(database_url, handle)`: `handle.cleanup()`
apaga lo que haya que apagar al salir (`edecan_local.runtime` lo llama al
recibir SIGTERM/SIGINT, ARCHITECTURE.md §12f "apagado limpio"). En modo
avanzado `cleanup()` es un no-op — este proceso no es dueño del Postgres del
cliente, así que no lo apaga. `database_url` siempre viaja en formato
SQLAlchemy (`postgresql+asyncpg://...`, ARCHITECTURE.md §10.2): es lo que
consume tanto `edecan_local.migrate.run_migrations` como el `DATABASE_URL`
que `edecan_local.runtime` inyecta antes de importar `edecan_api`/`edecan_db`.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

_EDECAN_DATABASE_URL_ENV = "EDECAN_DATABASE_URL"
_EMBEDDED_SUBDIR = "pg"


def _safe_pg_data_dir(data_dir: Path) -> Path:
    """Resuelve dónde vive de verdad el cluster embebido — normalmente
    `data_dir/pg`, pero NUNCA una ruta con un espacio.

    `pgserver.get_server(pgdata)` arranca Postgres con `pg_ctl ... -o "-k
    <socket_dir>"` — `pg_ctl`/`postgres` NO son quote-aware para el valor de
    `-o` (comportamiento histórico de PostgreSQL: se separa por espacios en
    blanco tal cual), así que un `socket_dir` con un espacio literal revienta
    con `postgres: invalid argument: "..."` (el resto de la ruta después del
    espacio, tratado como un argumento aparte). Esto NO es un caso de borde
    teórico: `app.path().app_data_dir()` de Tauri (`apps/desktop/src-tauri/
    src/backend.rs::data_dir`, lo que le pasa a `--data-dir` en producción)
    resuelve en macOS a `~/Library/Application Support/<bundle-id>/data` —
    "Application Support" SIEMPRE tiene ese espacio. Verificado empíricamente
    corriendo el binario real contra esa ruta exacta (ver
    `HOTFIXES_PENDIENTES.md`): sin este fallback, la app de escritorio nunca
    arranca en ningún Mac real.

    Cuando la ruta natural es segura (sin espacio — el caso de
    `DATA_DIR=~/.edecan/data` por defecto de `apps/local` en modo standalone,
    `ARCHITECTURE.md` §12g), se usa tal cual. Cuando no, se usa un directorio
    alternativo, estable y determinístico (el mismo `data_dir` siempre
    produce el mismo alternativo — no es aleatorio ni depende del proceso)
    bajo el home del usuario, garantizado sin espacios."""
    natural = data_dir.expanduser() / _EMBEDDED_SUBDIR
    if " " not in str(natural):
        return natural
    import hashlib

    digest = hashlib.sha256(str(natural).encode("utf-8")).hexdigest()[:16]
    alternativo = Path.home() / ".edecan-pg" / digest
    logger.warning(
        "El directorio de datos embebido natural (%s) tiene un espacio en la ruta "
        "-- Postgres/pg_ctl no puede arrancar ahí. Usando en su lugar: %s",
        natural,
        alternativo,
    )
    return alternativo


class PostgresHandle(Protocol):
    """Lo que `edecan_local.runtime` necesita al apagar (SIGTERM/SIGINT)."""

    def cleanup(self) -> None: ...


class _NoopHandle:
    """Handle del modo avanzado (`EDECAN_DATABASE_URL`): este proceso no es
    dueño del Postgres del cliente, así que "apagarlo" no hace nada."""

    def cleanup(self) -> None:
        return None


class _EmbeddedHandle:
    """Handle del modo embebido: envuelve el objeto que devuelve
    `pgserver.get_server(...)` y delega `cleanup()` en él."""

    def __init__(self, server: object) -> None:
        self._server = server

    def cleanup(self) -> None:
        try:
            self._server.cleanup()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - apagar nunca debe reventar el shutdown
            logger.warning("Error deteniendo el Postgres embebido (pgserver).", exc_info=True)
        else:
            logger.info("Postgres embebido detenido.")


def _to_asyncpg_url(uri: str) -> str:
    """`postgresql://...`/`postgres://...` -> `postgresql+asyncpg://...`
    (SQLAlchemy exige el sufijo `+asyncpg` para el dialecto async,
    ARCHITECTURE.md §10.2 `DATABASE_URL`). Ya viene con `+asyncpg` -> se deja
    igual (idempotente, por si `pgserver` cambiara de formato)."""
    if uri.startswith("postgresql+asyncpg://"):
        return uri
    if uri.startswith("postgresql://"):
        return "postgresql+asyncpg://" + uri[len("postgresql://") :]
    if uri.startswith("postgres://"):
        return "postgresql+asyncpg://" + uri[len("postgres://") :]
    raise RuntimeError(f"URI de Postgres embebido con esquema inesperado: {uri!r}")


def _to_asyncpg_dsn(database_url: str) -> str:
    """Inverso de `_to_asyncpg_url`, para el `asyncpg.connect` directo de
    `_create_vector_extension` (asyncpg no entiende el sufijo `+asyncpg`)."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _postgres_vector_library_candidates(postgres_executable: Path) -> tuple[Path, ...]:
    """Rutas posibles del módulo ``vector`` para el Postgres de ``pgserver``.

    ``postgres_executable`` vive en ``pginstall/bin`` y los módulos que
    Postgres resuelve mediante ``$libdir`` viven en
    ``pginstall/lib/postgresql``. La extensión cambia de sufijo por sistema,
    pero comprobar los tres hace la detección portable y fácil de probar.
    """

    module_dir = postgres_executable.parent.parent / "lib" / "postgresql"
    return tuple(module_dir / name for name in ("vector.dylib", "vector.so", "vector.dll"))


def _postgres_runtime_is_usable(postgres_executable: Path) -> bool:
    """Confirma que el runtime que sostiene al servidor aún existe completo.

    Un sidecar PyInstaller ``onefile`` ejecuta Postgres desde ``sys._MEIPASS``.
    Si la app termina de forma abrupta, el postmaster puede sobrevivir después
    de que PyInstaller borre esa carpeta temporal. El binario ya cargado sigue
    respondiendo, pero cualquier carga tardía como ``$libdir/vector`` falla.
    """

    return postgres_executable.is_file() and any(
        candidate.is_file()
        for candidate in _postgres_vector_library_candidates(postgres_executable)
    )


def _running_embedded_postgres(data_dir: Path) -> tuple[object, Path] | None:
    """Devuelve el proceso y ejecutable del postmaster de este ``PGDATA``.

    El PID proviene de ``postmaster.pid`` pero se valida también contra el
    argumento ``-D`` del proceso. Así nunca se termina un Postgres ajeno por
    un PID reciclado o un archivo obsoleto.
    """

    pid_file = data_dir / "postmaster.pid"
    try:
        pid = int(pid_file.read_text(encoding="utf-8").splitlines()[0])
    except (FileNotFoundError, IndexError, ValueError, OSError):
        return None

    import psutil

    try:
        process = psutil.Process(pid)
        if not process.is_running():
            return None
        command = process.cmdline()
    except (psutil.Error, OSError):
        return None
    if not command:
        return None

    try:
        data_index = command.index("-D") + 1
        process_data_dir = Path(command[data_index]).expanduser().resolve()
    except (ValueError, IndexError, OSError):
        return None
    if process_data_dir != data_dir.expanduser().resolve():
        logger.warning("Se ignoró un postmaster.pid cuyo proceso no pertenece al PGDATA de Edecán.")
        return None

    # En macOS ``psutil.Process.exe()`` puede devolver vacío cuando la ruta
    # temporal ya fue eliminada. ``cmdline[0]`` conserva la ruta original.
    executable_text = command[0]
    try:
        reported_executable = process.exe()
    except (psutil.Error, OSError):
        reported_executable = ""
    if reported_executable:
        executable_text = reported_executable
    return process, Path(executable_text)


def _recover_orphaned_embedded_postgres(data_dir: Path) -> bool:
    """Detiene un postmaster cuyo runtime temporal ya desapareció.

    Devuelve ``True`` cuando hizo una recuperación. El siguiente
    ``pgserver.get_server`` arranca el mismo cluster con el runtime extraído
    por la ejecución actual, conservando todos los datos del usuario.
    """

    running = _running_embedded_postgres(data_dir)
    if running is None:
        return False
    process, executable = running
    if _postgres_runtime_is_usable(executable):
        return False

    import psutil

    logger.warning(
        "El Postgres embebido seguía vivo desde un runtime temporal incompleto (%s). "
        "Se reiniciará de forma segura para restaurar pgvector.",
        executable,
    )
    try:
        process.terminate()  # type: ignore[attr-defined]
        process.wait(timeout=5)  # type: ignore[attr-defined]
    except psutil.TimeoutExpired:
        process.kill()  # type: ignore[attr-defined]
        process.wait(timeout=5)  # type: ignore[attr-defined]
    except (psutil.Error, OSError) as exc:
        raise RuntimeError(
            "No se pudo reiniciar el almacenamiento local incompleto de Edecán."
        ) from exc
    return True


async def _create_vector_extension(database_url: str) -> None:
    """`CREATE EXTENSION IF NOT EXISTS vector` con `asyncpg` directo, sin
    pasar por SQLAlchemy/Alembic: corre UNA vez al arrancar, antes de que
    exista ninguna sesión de `edecan_db` — pgvector debe estar disponible
    ANTES de que `edecan_local.migrate.run_migrations` aplique
    `0001_initial` (esa migración ya trae su propio
    `CREATE EXTENSION IF NOT EXISTS vector`, ARCHITECTURE.md §10.3, pero
    hacerlo también acá dos veces es inofensivo — `IF NOT EXISTS` — y deja
    la extensión lista incluso si algo más se conecta antes de migrar)."""
    import asyncpg

    dsn = _to_asyncpg_dsn(database_url)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await conn.close()


async def ensure_postgres(data_dir: Path) -> tuple[str, PostgresHandle]:
    """Devuelve `(database_url, handle)` — ver docstring del módulo para los
    dos modos. `data_dir` es la carpeta de datos completa del runner local
    (`~/.edecan/data` por defecto, ARCHITECTURE.md §12g `DATA_DIR`) — el
    cluster embebido vive en el subdirectorio propio `data_dir/pg`.
    """
    import os

    override = os.environ.get(_EDECAN_DATABASE_URL_ENV)
    if override:
        logger.info(
            "EDECAN_DATABASE_URL fijada: modo avanzado, uso el Postgres del cliente tal cual."
        )
        return _to_asyncpg_url(override), _NoopHandle()

    try:
        import pgserver
    except ImportError as exc:
        raise RuntimeError(
            "No hay EDECAN_DATABASE_URL configurada y falta la dependencia 'pgserver'. "
            "Esta arquitectura puede no tener un wheel publicado de pgserver; define "
            "EDECAN_DATABASE_URL apuntando a tu propio Postgres o reinstala "
            "edecan-local desde el lock en una plataforma compatible."
        ) from exc

    pg_data_dir = _safe_pg_data_dir(Path(data_dir))
    pg_data_dir.mkdir(parents=True, exist_ok=True)

    # Un cierre forzado del sidecar puede dejar Postgres vivo después de que
    # PyInstaller elimine su directorio temporal. Reiniciarlo aquí conserva el
    # cluster pero devuelve a ``$libdir`` sus módulos reales, incluido vector.
    await asyncio.to_thread(_recover_orphaned_embedded_postgres, pg_data_dir)

    logger.info("Arrancando Postgres embebido (pgserver) en %s...", pg_data_dir)
    # `pgserver.get_server` es síncrono y bloqueante (arranca un proceso e
    # inicializa el cluster la primera vez) -- `to_thread` para no congelar
    # el loop del runner mientras arranca.
    server = await asyncio.to_thread(pgserver.get_server, str(pg_data_dir))

    # `server.get_uri()`, NUNCA `server.uri`: verificado empíricamente contra
    # el paquete `pgserver` REAL instalado (0.1.4, `apps/local/pyproject.toml`
    # exige `>=0.1.4`) -- `PostgresServer` de esa versión solo expone el
    # connection string vía el MÉTODO `get_uri(user="postgres",
    # database=None)` (default: conecta a la base "postgres", la única que
    # crea `initdb` de por sí); NO existe ningún atributo `.uri`. Acceder a
    # `server.uri` explota con `AttributeError` en el primer arranque real
    # (visto en vivo corriendo `python -m edecan_local` de verdad, WP-V7-11) --
    # invisible para `apps/local/tests/test_pg.py` porque su fake de
    # `pgserver.get_server` (`_FakeServer`) asumía un `.uri` que el paquete
    # real nunca tuvo (mismo patrón de "esquema asumido vs. esquema real" que
    # ya causó un bug crítico distinto en v6, ver HOTFIXES_PENDIENTES.md) --
    # el único test que sí usa el paquete real es
    # `test_ensure_postgres_embebido_real_con_pgserver`, marcado
    # `@pytest.mark.integration` y por tanto excluido de `pytest -m "not
    # integration"`/`make test`.
    database_url = _to_asyncpg_url(server.get_uri())
    await _create_vector_extension(database_url)
    logger.info("Postgres embebido listo (pgvector habilitado).")

    return database_url, _EmbeddedHandle(server)
