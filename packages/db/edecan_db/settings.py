"""Configuración de `edecan_db` vía `pydantic-settings` (ARCHITECTURE.md §10.2).

Este paquete solo lee las variables de entorno que necesita para conectarse a
Postgres y para el cifrado envolvente del `TokenVault`. El resto de variables
de `.env.example` las leen los paquetes que las usan (`edecan_api`, `edecan_llm`, ...).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://edecan:edecan@localhost:5432/edecan"

    # Cifrado envolvente del TokenVault (ARCHITECTURE.md §10.4).
    local_master_key: str | None = None
    kms_key_id: str | None = None

    # Necesarias solo si se usa KmsKeyProvider.
    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> DbSettings:
    """Settings singleton por proceso (usa `get_settings.cache_clear()` en tests)."""
    return DbSettings()
