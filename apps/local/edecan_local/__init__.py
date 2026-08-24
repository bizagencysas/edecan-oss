"""`edecan_local` — runner local que empaqueta `edecan_api` + `edecan_worker` +
`edecan_db` para correr en la máquina del cliente: el backend de la app de
escritorio Tauri (`DIRECCION_ACTUAL.md`, `ARCHITECTURE.md` §12f).

Implementado por WP-V3-05 (ver `docs/desktop-local.md` para la arquitectura
completa). Piezas del paquete:

- `edecan_local.pg` — Postgres embebido (`pgserver`) o `EDECAN_DATABASE_URL`
  (modo avanzado, cliente con su propio Postgres).
- `edecan_local.migrate` — aplica las migraciones de `packages/db/alembic`
  sin depender de la ruta del repo (funciona empaquetado con PyInstaller).
- `edecan_local.objectstore` — mini servidor S3-compatible sobre filesystem
  (reemplaza LocalStack/S3 real para el modo escritorio de un solo usuario).
- `edecan_local.worker_loop` — consumidor in-process de la tabla `jobs`
  (`QUEUE_PROVIDER="db"`) + scheduler local (`send_reminder_scan`,
  `automation_scan`).
- `edecan_local.runtime` — orquesta todo lo anterior + `edecan_api` (uvicorn)
  en un solo proceso; `python -m edecan_local` (`__main__.py`) es el punto
  de entrada.

Contrato pinned (`ARCHITECTURE.md` §12f), todo cumplido por este paquete:
`python -m edecan_local`, bind SOLO en `127.0.0.1` (nunca `0.0.0.0`), puerto
`settings.LOCAL_API_PORT` (default `8765`, override `--port`), imprime la
línea EXACTA `EDECAN_LOCAL_READY port=<p>` en stdout al quedar sano, acepta
los flags `--port`/`--data-dir`/`--no-web`, y se apaga limpio ante
`SIGTERM`/`SIGINT` (cierra conexiones DB/HTTP en curso, detiene el Postgres
embebido si aplica).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def _installed_version() -> str:
    """Return the package metadata version without duplicating pyproject.toml."""

    try:
        return version("edecan-local")
    except PackageNotFoundError:
        # Source-only imports outside the managed workspace should remain
        # usable, but must never claim a stale release number.
        return "0+unknown"


__version__ = _installed_version()
