"""Configuración de `edecan_api` (pydantic-settings).

Declara **todas** las variables de entorno listadas en `ARCHITECTURE.md`
§10.2 como campos en MAYÚSCULAS (así los paquetes hermanos que las leen con
`getattr(settings, "NOMBRE_EXACTO")` — p. ej. `edecan_llm.router.LLMRouter` o
`edecan_voice.registry.get_stt/get_tts` — funcionan sin adaptar nombres).

Los defaults de valores no-secretos (URLs locales, nombres de modelo,
proveedores "stub") replican los de `.env.example`. Los defaults de campos
secretos son placeholders `TU_X_AQUI` — nunca un secreto real — para respetar
la regla dura "cero secretos reales" (`ARCHITECTURE.md` §0.1): una instancia
recién clonada arranca (los flags que dependen de esas claves caen a stub),
pero no queda "segura" hasta que el operador las reemplaza en su `.env`.
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
    """Configuración de la plataforma. Ver `ARCHITECTURE.md` §10.2."""

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
    AUTH_RATE_LIMIT_REQUESTS: int = 10
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = 60
    # Capacidad aleatoria por proceso que Tauri entrega al sidecar y a su
    # WebView. Nunca se persiste ni se publica en el QR/túnel.
    LOCAL_DESKTOP_CAPABILITY: str | None = None
    # QR móvil de un solo uso. La sesión durable posterior vive en `devices`
    # y se puede revocar; este TTL solo limita la ventana del QR visible.
    MOBILE_PAIRING_TTL_SECONDS: int = 10 * 60

    # --- Cifrado envolvente de credenciales de tenant (TokenVault) -----------
    LOCAL_MASTER_KEY: str = LOCAL_MASTER_KEY_PLACEHOLDER
    KMS_KEY_ID: str | None = None

    # --- Base de datos y caché -------------------------------------------------
    DATABASE_URL: str = "postgresql+asyncpg://edecan:edecan@localhost:5432/edecan"
    REDIS_URL: str = "redis://localhost:6379/0"
    # Ventana durante la cual un reintento de chat con la misma clave puede
    # recuperar exactamente el flujo SSE ya completado sin ejecutar otro turno.
    CHAT_IDEMPOTENCY_TTL_SECONDS: int = 24 * 60 * 60

    # --- AWS (SQS, S3, KMS) ------------------------------------------------------
    AWS_REGION: str = "us-east-1"
    AWS_ENDPOINT_URL: str | None = None
    S3_BUCKET: str = "edecan-files"
    SQS_QUEUE_URL: str | None = None
    # Límite duro por request, independiente de la cuota acumulada del plan.
    # Evita que un multipart único agote memoria/disco antes de llegar a S3.
    MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024

    # --- LLM -----------------------------------------------------------------
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL_PRINCIPAL: str = "claude-sonnet-4-5"
    ANTHROPIC_MODEL_RAPIDO: str = "claude-haiku-4-5"
    OPENAI_COMPAT_BASE_URL: str | None = None
    OPENAI_COMPAT_API_KEY: str | None = None

    # --- Embeddings / búsqueda web ---------------------------------------------
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
    # Máximo de respuestas LLM por llamada. Evita un Gather/LLM sin límite
    # ante silencio, bots o una llamada olvidada; el último turno cuelga.
    PHONE_MAX_TURNS: int = 8

    # --- OAuth por plataforma (cada tenant autoriza su propia cuenta) --------
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    MS_CLIENT_ID: str | None = None
    MS_CLIENT_SECRET: str | None = None
    META_APP_ID: str | None = None
    META_APP_SECRET: str | None = None
    X_CLIENT_ID: str | None = None
    X_CLIENT_SECRET: str | None = None
    SLACK_CLIENT_ID: str | None = None
    SLACK_CLIENT_SECRET: str | None = None

    # --- Facturación y correo transaccional -------------------------------------
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None
    SES_FROM_EMAIL: str | None = None

    # --- Observabilidad ----------------------------------------------------------
    SENTRY_DSN: str | None = None

    # --- v2 (ROADMAP_V2.md §7.5, dueño WP-V2-01) --------------------------------
    # Convención dura de §7.5: toda tool v2 lee estos campos con
    # `getattr(ctx.settings, "CAMPO", default)`, nunca revienta si el campo
    # falta — así que declararlos aquí es documentación + un default sano
    # para quien construye `Settings()` directo, no un requisito duro para
    # que las tools funcionen.
    BROWSER_FETCH_PROVIDER: str = "httpx"
    BROWSER_USER_AGENT: str = "EdecanBot/1.0"
    BROWSER_MAX_FETCH_BYTES: int = 2_000_000
    BROWSER_TIMEOUT_SECONDS: int = 20

    IMAGES_PROVIDER: str = "stub"
    IMAGES_BASE_URL: str | None = None
    IMAGES_API_KEY: str | None = None
    IMAGES_MODEL: str | None = None

    QUOTES_PROVIDER: str = "stub"

    # Único valor operativo hoy (ROADMAP_V2.md §8.1: dinero real nunca se
    # mueve solo) — un modo "live" queda fuera de este pinning a propósito.
    COMMERCE_MODE: str = "paper"

    MISSIONS_MAX_STEPS: int = 8
    REMOTE_FRAME_MIN_INTERVAL_SECONDS: float = 0.25

    # --- v3 (DIRECCION_ACTUAL.md, ARCHITECTURE.md §12, dueño WP-V3-01) ---------
    # Misma convención dura que v2 (§7.5): toda tool/router v3 lee estos campos
    # con `getattr(ctx.settings, "CAMPO", default)`, nunca revienta si falta uno.
    #
    # `REDIS_URL` (declarado arriba, sección "Base de datos y caché") acepta
    # además el esquema especial `memory://` para el modo escritorio
    # single-user: selecciona un `fakeredis` en memoria en vez de un Redis de
    # verdad. Ese esquema lo INTERPRETA `edecan_api.deps` (WP-V3-02, no este
    # módulo) — aquí no cambia el tipo/default de `REDIS_URL`, solo se deja
    # documentado el contrato para quien construya el cliente Redis.
    #
    # Modo local / app de escritorio Tauri (WP-V3-05, runner `edecan_local`):
    # la app de escritorio fija estos 3 automáticamente al arrancar — un
    # usuario normal nunca los toca a mano.
    EDECAN_LOCAL_MODE: bool = False
    DATA_DIR: str = "~/.edecan/data"
    # Workspace aislado de artefactos/proyectos creados desde chat. `None`
    # deriva a `$DATA_DIR/creator`; una ruta explícita sirve para inspeccionar
    # los proyectos directamente en instalaciones locales.
    CREATOR_WORKSPACE_DIR: str | None = None
    SERVE_WEB_DIR: str | None = None
    LOCAL_API_PORT: int = 8765

    # Acceso local total al propio repo (`edecan_toolkit.codigo_local`,
    # 2026-07-09): `None` = tool desactivada. Cuando el dueño de una
    # instancia de DESARROLLO (nunca el hosted multi-tenant compartido -- ver
    # docstring de esa tool) la configura a la ruta de su propio clon local,
    # el agente puede leer/escribir/ejecutar comandos y hacer commits LOCALES
    # ahí -- nunca hace `git push` por su cuenta, eso queda deliberadamente
    # fuera de la tool.
    EDECAN_LOCAL_REPO_PATH: str | None = None

    # Autorreparación del propio núcleo: opt-in local y fail-closed. Los
    # comandos se expresan como JSON de argv exactos, p. ej.
    # `[["uv","run","--frozen","pytest","packages/toolkit/tests"]]`.
    # No se aceptan strings de shell ni prefijos abiertos: cada ejecución o
    # instalación además pasa por la confirmación humana normal de tools.
    EDECAN_SELF_REPAIR_ENABLED: bool = False
    EDECAN_SELF_REPAIR_TEST_COMMANDS_JSON: str = "[]"
    EDECAN_SELF_REPAIR_INSTALL_COMMANDS_JSON: str = "[]"
    EDECAN_SELF_REPAIR_COMMAND_TIMEOUT_SECONDS: int = 300

    # Cola de jobs en modo local: "sqs" (LocalStack/AWS real, igual que hoy) o
    # "db" (tabla `jobs` como cola, sin SQS/LocalStack — pensado para el modo
    # escritorio de un solo usuario, WP-V3-05).
    QUEUE_PROVIDER: str = "sqs"

    # Proveedores LLM nuevos (DIRECCION_ACTUAL.md "conectar el LLM vía CLI
    # local"): Ollama (modelos locales), y los CLIs de Claude Code/Codex como
    # subproceso. `CLAUDE_CLI_PATH`/`CODEX_CLI_PATH` en `None` significa
    # "autodetectar en PATH" (ver `edecan_llm.detect.detect_local_providers`,
    # ARCHITECTURE.md §12).
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    CLAUDE_CLI_PATH: str | None = None
    CODEX_CLI_PATH: str | None = None
    LLM_CLI_TIMEOUT_SECONDS: int = 300

    # Vertex AI real (bring-your-own proyecto GCP/service account o ADC).
    VERTEX_MODEL_PRINCIPAL: str = "gemini-2.5-pro"
    VERTEX_MODEL_RAPIDO: str = "gemini-2.5-flash"

    # Marketplace abierto de "Agent Skills" (skills.sh) — índice desde el que
    # el toolkit de Edecán puede instalar/usar skills de terceros.
    SKILLS_INDEX_URL: str = "https://skills.sh"

    # Timeout de las llamadas a la API REST de Home Assistant (smarthome).
    HOMEASSISTANT_TIMEOUT_SECONDS: int = 15

    # --- v5 (ARCHITECTURE.md §14, dueño WP-V5-01) -------------------------------
    # Misma convención dura que v2/v3 (§7.5/§12.g): toda tool/router v5 lee
    # estos campos con `getattr(ctx.settings, "CAMPO", default)`, nunca
    # revienta si falta uno. Amplían el presupuesto de misiones de
    # `MISSIONS_MAX_STEPS` (arriba, v2) para que el Orchestrator pueda: (1)
    # cortar un paso individual que se cuelga en vez de solo limitar CUÁNTOS
    # pasos corren, y (2) correr varios pasos independientes en paralelo
    # (dueño real un WP de seguimiento del Orchestrator).
    MISSIONS_STEP_TIMEOUT_SECONDS: int = 300
    MISSIONS_PARALLEL_MAX: int = 3

    # --- v6 (ARCHITECTURE.md §15, dueño WP-V6-01) -------------------------------
    # Misma convención dura que v2/v3/v5 (§7.5/§12.g/§14): toda tool/router v6
    # lee estos campos con `getattr(ctx.settings, "CAMPO", default)`, nunca
    # revienta si falta uno. v6 es 100% bring-your-own — sin credenciales de
    # PLATAFORMA nuevas acá (reuniones/analista/MCP/podcasts se configuran por
    # tenant vía TokenVault, no vía `.env`).
    #
    # Interrupciones naturales por Media Streams (WS `/v1/twilio/media`,
    # `premium`), beta, off por defecto.
    TWILIO_MEDIA_STREAMS_ENABLED: bool = False

    @property
    def is_prod(self) -> bool:
        return self.ENV.strip().lower() == "prod"

    def assert_safe_for_prod(self) -> None:
        """Falla rápido si `ENV=prod` pero `JWT_SECRET`/`LOCAL_MASTER_KEY` siguen
        con el valor placeholder público de `.env.example` (o `JWT_SECRET` es
        inseguramente corto).

        `JWT_SECRET` firma y verifica todos los access/refresh tokens y el
        `state` de OAuth (`edecan_api.security`, `routers/connectors.py`); su
        claim `ten` fija `app.tenant_id` para la Row-Level Security que aísla
        cada tenant (`deps.get_tenant_session`, ARCHITECTURE.md §2). Como el
        placeholder es público (está en este repo), dejarlo sin reemplazar en
        prod permite forjar un token con cualquier `tenant_id` y romper el
        aislamiento multi-tenant por completo — no es solo "hardening", es la
        llave de la RLS. No aplica en dev/test: ahí el placeholder es
        intencional para que el repo arranque recién clonado (ver docstring
        del módulo).
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
    """Devuelve la configuración cacheada del proceso (una sola lectura de env)."""
    return Settings()
