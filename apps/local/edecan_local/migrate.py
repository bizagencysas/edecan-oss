"""`run_migrations(database_url)` — aplica `alembic upgrade head`
(`packages/db/alembic/`) contra `database_url`, sin depender de que el
proceso corra desde la raíz del monorepo (`ARCHITECTURE.md` §12f: el runner
local puede vivir empaquetado con PyInstaller, WP-V3-06).

## Orden de descubrimiento del directorio de Alembic (`script_location`)

1. **`EDECAN_ALEMBIC_DIR`** (variable de entorno) — escotilla de escape
   explícita: útil para tests, o para un empaquetado no estándar que el
   dueño del proyecto arme a mano.
2. **`sys._MEIPASS/alembic`** — si el proceso corre CONGELADO con PyInstaller
   (`getattr(sys, "_MEIPASS", None)`, atributo que PyInstaller inyecta en
   tiempo de ejecución apuntando al directorio temporal donde extrae los
   datos empaquetados): WP-V3-06 empaqueta `packages/db/alembic/` como datas
   bajo el nombre `alembic` en la raíz del bundle (ver `ARCHITECTURE.md`
   §12f y el comentario del work package que arma la spec de PyInstaller).
3. **Ruta del propio repo/venv**: `Path(edecan_db.__file__).resolve()
   .parents[2] / "db" / "alembic"` — funciona en dev (`uv run python -m
   edecan_local`) porque `edecan_db` es un paquete del mismo workspace uv,
   instalado en modo editable (`edecan_db.__file__` apunta a
   `packages/db/edecan_db/__init__.py`; sus dos abuelos son `packages/db`).

Cada candidato se acepta SOLO si `<candidato>/env.py` existe — si ninguno
sirve, `RuntimeError` con el detalle de qué se probó (nunca un traceback
críptico de Alembic tipo "path doesn't exist").

## Cómo se aplican las migraciones

Se arma un `alembic.config.Config` EN MEMORIA (sin leer ningún `.ini` de
disco: `script_location` y `sqlalchemy.url` se fijan directo con
`set_main_option`). `packages/db/alembic/env.py` (que no pertenece a este
paquete de trabajo, se reutiliza tal cual) ya sabe usar `sqlalchemy.url` del
`Config` si viene fijado — y solo cae a
`edecan_db.settings.get_settings().database_url` (que lee la env var
`DATABASE_URL`) si NO lo está; como este módulo SIEMPRE fija `sqlalchemy.url`
explícitamente, esa rama de `env.py` nunca se ejercita aquí, pero igual se
fija también la env var `DATABASE_URL` por las dudas (p. ej. si algo más en
el proceso construyera su propio `Config` sin url).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_ALEMBIC_SUBDIR = "alembic"
_ENV_PY = "env.py"
_ALEMBIC_DIR_ENV = "EDECAN_ALEMBIC_DIR"


def _candidate_dirs() -> list[Path]:
    """Candidatos de `script_location`, en el orden de descubrimiento
    documentado arriba (sin filtrar todavía cuáles existen de verdad)."""
    candidates: list[Path] = []

    env_dir = os.environ.get(_ALEMBIC_DIR_ENV)
    if env_dir:
        candidates.append(Path(env_dir).expanduser())

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / _ALEMBIC_SUBDIR)

    import edecan_db

    repo_alembic_dir = Path(edecan_db.__file__).resolve().parents[2] / "db" / _ALEMBIC_SUBDIR
    candidates.append(repo_alembic_dir)

    return candidates


def find_alembic_dir() -> Path:
    """Recorre `_candidate_dirs()` en orden y devuelve el primero que
    contenga `env.py`. Lanza `RuntimeError` (con el detalle de los
    candidatos probados) si ninguno sirve."""
    tried: list[str] = []
    for candidate in _candidate_dirs():
        tried.append(str(candidate))
        if (candidate / _ENV_PY).is_file():
            return candidate
    raise RuntimeError(
        "No encontré el directorio de migraciones de Alembic (busco "
        f"'{_ENV_PY}' dentro de): {', '.join(tried)}. Define "
        f"{_ALEMBIC_DIR_ENV} apuntando a packages/db/alembic si corres desde "
        "una ubicación no estándar."
    )


def run_migrations(database_url: str) -> None:
    """Aplica todas las migraciones pendientes (`upgrade head`) contra
    `database_url` (formato `postgresql+asyncpg://...`, ARCHITECTURE.md
    §10.3).

    Síncrono a propósito: la API de `alembic.command` es síncrona, y
    `packages/db/alembic/env.py` abre su propio loop con `asyncio.run(...)`
    para el modo "online" (ARCHITECTURE.md §12f) — llamar a esta función
    desde código async exige envolverla en
    `asyncio.to_thread(run_migrations, database_url)` (así lo hace
    `edecan_local.runtime`, nunca la llama directo desde el loop principal).
    """
    alembic_dir = find_alembic_dir()

    # Import perezoso: alembic es una dependencia de `edecan-local`
    # (`apps/local/pyproject.toml`), pero no hace falta pagar el import al
    # cargar este módulo si nunca se llega a llamar `run_migrations` (p. ej.
    # tests que solo ejercitan `find_alembic_dir`).
    from alembic import command
    from alembic.config import Config

    # Ver docstring del módulo: `env.py` solo cae a
    # `edecan_db.settings.get_settings().database_url` (que lee esta env var)
    # si `sqlalchemy.url` NO viene fijado en el Config -- lo fijamos abajo,
    # así que esto es un respaldo defensivo, no el camino real.
    os.environ["DATABASE_URL"] = database_url

    cfg = Config()
    cfg.set_main_option("script_location", str(alembic_dir))
    cfg.set_main_option("sqlalchemy.url", database_url)

    logger.info("Aplicando migraciones de Alembic (script_location=%s)...", alembic_dir)
    command.upgrade(cfg, "head")
    logger.info("Migraciones aplicadas (upgrade head).")
