"""Motor async de SQLAlchemy, construido desde `DATABASE_URL` (ARCHITECTURE.md §10.3)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from edecan_db.settings import get_settings


def create_engine(database_url: str | None = None, **kwargs: Any) -> AsyncEngine:
    """Crea un `AsyncEngine` nuevo (no cacheado).

    Si no se pasa `database_url` explícito, se usa `DbSettings.database_url`
    (variable de entorno `DATABASE_URL`).
    """
    url = database_url or get_settings().database_url
    kwargs.setdefault("pool_pre_ping", True)
    return create_async_engine(url, **kwargs)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """`AsyncEngine` singleton por proceso, construido desde `DATABASE_URL`."""
    return create_engine()


def get_sessionmaker(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    """`async_sessionmaker` sobre `engine` (o el engine singleton si no se pasa uno)."""
    return async_sessionmaker(engine or get_engine(), expire_on_commit=False)
