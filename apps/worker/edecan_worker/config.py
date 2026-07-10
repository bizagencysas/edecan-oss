"""Configuración de `edecan_worker` (`pydantic-settings`, ver `ARCHITECTURE.md` §10.2).

Declara los mismos nombres EXACTOS en MAYÚSCULAS que `apps/api/edecan_api/config.py`
para las variables de §10.2, incluso las que el worker no usa directamente
(JWT, OAuth de plataforma, Stripe, voz...). Dos razones:

1. Paquetes hermanos que reciben la configuración por duck-typing (p. ej.
   `edecan_llm.router.LLMRouter`, que espera `ANTHROPIC_API_KEY`,
   `ANTHROPIC_MODEL_PRINCIPAL`, `ANTHROPIC_MODEL_RAPIDO`,
   `OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_API_KEY` vía `getattr`) funcionan
   sin adaptar nombres sin importar qué app los construya.
2. Evita que `edecan_api` y `edecan_worker` diverjan en cómo leen `.env.example`.

Los defaults de valores no-secretos replican los de `.env.example`. Los
defaults de campos secretos son placeholders `TU_X_AQUI` — nunca un secreto
real (regla dura `ARCHITECTURE.md` §0.1): el worker arranca recién clonado
(los flags que dependen de esas claves caen a stub/None), pero no queda
"seguro" hasta que el operador las reemplaza en su `.env`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholders públicos (también en `.env.example`) — NUNCA secretos reales.
# `Settings.assert_safe_for_prod()` los usa para negarse a arrancar en prod si
# alguno sigue sin reemplazar (ver ese método para el porqué).
JWT_SECRET_PLACEHOLDER = "TU_JWT_SECRET_AQUI"
LOCAL_MASTER_KEY_PLACEHOLDER = "TU_LOCAL_MASTER_KEY_FERNET_AQUI"

# HS256 (RFC 7518 §3.2): PyJWT emite `InsecureKeyLengthWarning` por debajo de
# esto; lo tratamos como error duro en prod en vez de solo advertir.
_MIN_JWT_SECRET_BYTES = 32


class Settings(BaseSettings):
    """Configuración del worker. Ver `ARCHITECTURE.md` §10.2."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # --- General / plataforma ------------------------------------------------
    ENV: str = "dev"
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    WEB_BASE_URL: str = "http://localhost:3000"
    LOG_LEVEL: str = "INFO"
    JWT_SECRET: str = JWT_SECRET_PLACEHOLDER

    # --- Cifrado envolvente de credenciales de tenant (TokenVault, §10.4) ----
    LOCAL_MASTER_KEY: str = LOCAL_MASTER_KEY_PLACEHOLDER
    KMS_KEY_ID: str | None = None

    # --- Base de datos y caché -------------------------------------------------
    DATABASE_URL: str = "postgresql+asyncpg://edecan:edecan@localhost:5432/edecan"
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- AWS (SQS, S3, KMS) — en local, LocalStack vía docker-compose ------------
    AWS_REGION: str = "us-east-1"
    AWS_ENDPOINT_URL: str | None = None
    S3_BUCKET: str = "edecan-files"
    SQS_QUEUE_URL: str | None = None

    # --- LLM -----------------------------------------------------------------
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL_PRINCIPAL: str = "claude-sonnet-4-5"
    ANTHROPIC_MODEL_RAPIDO: str = "claude-haiku-4-5"
    OPENAI_COMPAT_BASE_URL: str | None = None
    OPENAI_COMPAT_API_KEY: str | None = None

    # --- Embeddings (memoria / pgvector) y búsqueda web ---------------------------
    EMBEDDINGS_MODEL: str | None = None
    EMBEDDINGS_DIM: int = 1536
    SEARCH_PROVIDER: str = "stub"
    BRAVE_API_KEY: str | None = None
    TAVILY_API_KEY: str | None = None

    # --- Voz (STT/TTS web) -----------------------------------------------------
    VOICE_STT_PROVIDER: str = "stub"
    VOICE_TTS_PROVIDER: str = "stub"
    DEEPGRAM_API_KEY: str | None = None
    ELEVENLABS_API_KEY: str | None = None
    ELEVENLABS_VOICE_ID: str | None = None
    POLLY_VOICE: str = "Lupe"

    # --- OAuth por plataforma (cada tenant autoriza su propia cuenta) --------
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    MS_CLIENT_ID: str | None = None
    MS_CLIENT_SECRET: str | None = None
    META_APP_ID: str | None = None
    META_APP_SECRET: str | None = None
    X_CLIENT_ID: str | None = None
    X_CLIENT_SECRET: str | None = None

    # --- Facturación y correo transaccional -------------------------------------
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None
    SES_FROM_EMAIL: str | None = None

    # --- Observabilidad ----------------------------------------------------------
    SENTRY_DSN: str | None = None

    @property
    def is_prod(self) -> bool:
        return self.ENV.strip().lower() == "prod"

    def assert_safe_for_prod(self) -> None:
        """Falla rápido si `ENV=prod` pero `JWT_SECRET`/`LOCAL_MASTER_KEY` siguen
        con el valor placeholder público de `.env.example` (o `JWT_SECRET` es
        inseguramente corto).

        El worker no verifica JWT directamente, pero comparte este `Settings`
        con `edecan_api` (mismo `.env`) y construye su propio `TokenVault` vía
        `LOCAL_MASTER_KEY` (`edecan_worker.deps`). Dejar cualquiera de los dos
        en su placeholder público en prod compromete el aislamiento
        multi-tenant que protege la API (JWT: cualquiera puede forjar un token
        con el `tenant_id` que quiera) o las credenciales de conectores
        cifradas (`LOCAL_MASTER_KEY`). No aplica en dev/test: ahí el
        placeholder es intencional para que el repo arranque recién clonado
        (ver docstring del módulo).
        """
        if not self.is_prod:
            return
        if (
            self.JWT_SECRET == JWT_SECRET_PLACEHOLDER
            or len(self.JWT_SECRET.encode("utf-8")) < _MIN_JWT_SECRET_BYTES
        ):
            raise RuntimeError(
                "JWT_SECRET no está configurado de forma segura para ENV=prod: sigue "
                "siendo el placeholder público de .env.example o mide menos de "
                f"{_MIN_JWT_SECRET_BYTES} bytes. Genera uno nuevo y ponlo en el .env "
                'real, p. ej.: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        if self.LOCAL_MASTER_KEY == LOCAL_MASTER_KEY_PLACEHOLDER:
            raise RuntimeError(
                "LOCAL_MASTER_KEY no está configurado de forma segura para ENV=prod: "
                "sigue siendo el placeholder público de .env.example. Genera uno "
                'nuevo: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración cacheada del proceso (una sola lectura de env).

    En tests, usa `get_settings.cache_clear()` si necesitas releer variables
    de entorno modificadas a mitad de la sesión de pytest.
    """
    return Settings()
