"""Entorno de Alembic — soporta modo offline (`--sql`, sin conexión real) y
online (async, vía `AsyncEngine.run_sync`), como recomienda la documentación
de Alembic para proyectos con SQLAlchemy 2.0 async.

`target_metadata = edecan_db.models.Base.metadata` habilita `alembic revision
--autogenerate` para futuras migraciones (`0001_initial` en sí está escrita a
mano, ARCHITECTURE.md §10.3).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from edecan_db.models import Base
from edecan_db.settings import get_settings
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Objeto de configuración de Alembic, con acceso a los valores de alembic.ini.
config = context.config

# Logging de Python según alembic.ini (si se está corriendo como script).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata de los modelos, para `--autogenerate` en migraciones futuras.
target_metadata = Base.metadata

# `alembic.ini` deja `sqlalchemy.url` vacío a propósito: la rellenamos aquí
# desde `DATABASE_URL` (`edecan_db.settings`, ARCHITECTURE.md §10.2) para no
# duplicar la variable de entorno en dos lugares. Si alguien SÍ fija
# `sqlalchemy.url` explícitamente (p. ej. para apuntar a una base de test),
# esa configuración explícita gana.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)


def run_migrations_offline() -> None:
    """Modo "offline": emite el SQL de las migraciones sin conectarse a la
    base de datos de verdad (`alembic -c packages/db/alembic.ini upgrade head --sql`).
    Útil para revisar el DDL exacto o generar un script para aplicar a mano.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    """Crea un `AsyncEngine` desde `alembic.ini`/env y corre las migraciones
    dentro de `AsyncConnection.run_sync` (Alembic todavía es sync por dentro)."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Modo "online": se conecta de verdad y aplica las migraciones (el modo normal)."""
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
