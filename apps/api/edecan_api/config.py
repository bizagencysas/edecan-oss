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

from edecan_core.platform_paths import DEFAULT_DATA_DIR
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
    # La historia operacional no contiene prompts ni payloads. Se activa por
    # defecto solo cuando `ENV=prod`; en dev/test no abre una DB en segundo
    # plano. Requiere la migración `0046_provider_health_events`.
    PROVIDER_HEALTH_PERSISTENCE: bool = True
    # Ventana durante la cual un reintento de chat con la misma clave puede
    # recuperar exactamente el flujo SSE ya completado sin ejecutar otro turno.
    CHAT_IDEMPOTENCY_TTL_SECONDS: int = 24 * 60 * 60
    # Contexto conversacional: Edecán no debe olvidar una conversación larga ni
    # tratar cada chat como una isla. Estos límites son conservadores para
    # proteger coste/latencia; el empaquetado degrada por caracteres, no por
    # cantidad de filas ciegamente.
    CHAT_CONTEXT_ENABLED: bool = True
    CHAT_CONTEXT_RECENT_MESSAGES: int = 60
    CHAT_CONTEXT_MAX_MESSAGES: int = 220
    CHAT_CONTEXT_MAX_CHARS: int = 80_000
    # Chat 1:1 con bots (persistent_agents): un hilo humano↔bot puede crecer
    # a cientos de mensajes sin que el bot "pierda contexto". Independiente del
    # chat principal para no aumentar coste/latencia del asistente general.
    BOT_CONTEXT_MAX_MESSAGES: int = 1_000
    BOT_CONTEXT_MAX_CHARS: int = 200_000
    CHAT_CONTEXT_CROSS_CHAT_ENABLED: bool = True
    CHAT_CONTEXT_CROSS_CHAT_CONVERSATIONS: int = 8
    CHAT_CONTEXT_CROSS_CHAT_MESSAGES_PER_CONVERSATION: int = 4
    CHAT_CONTEXT_CROSS_CHAT_MAX_CHARS: int = 24_000
    # Server-driven mobile/web companion. Si está vacío se sirve el contrato
    # default del OSS. Si se configura, debe ser JSON compatible con
    # MobileServerConfig para cambiar copy, tabs y flags sin TestFlight.
    MOBILE_SERVER_CONFIG_JSON: str | None = None
    MOBILE_SERVER_CONFIG_VERSION: int = 1

    # --- AWS (SQS, S3, KMS) ------------------------------------------------------
    AWS_REGION: str = "us-east-1"
    AWS_ENDPOINT_URL: str | None = None
    S3_BUCKET: str = "edecan-files"
    SQS_QUEUE_URL: str | None = None
    # Límite duro por request, independiente de la cuota acumulada del plan.
    # Evita que un multipart único agote memoria/disco antes de llegar a S3.
    MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024

    # --- Inferencia LLM: Workers AI; el IDE usa su runtime separado -----------
    CLOUDFLARE_ACCOUNT_ID: str | None = None
    CLOUDFLARE_API_TOKEN: str | None = None
    # `glm-4.7-flash` era el default anterior y es un modelo de RAZONAMIENTO:
    # quema ~150 tokens pensando antes de emitir la primera palabra, y con
    # `max_tokens` chico (1200 en el escritor/auditor de posts, por ejemplo)
    # devuelve `content` vacío. Eso rompió el motor de LinkedIn: escritor y
    # auditor recibían texto vacío, se rechazaba dos veces y salía "no
    # devolviste el JSON pedido". Ver la nota completa junto a MODELO_POR_DEFECTO
    # en `packages/llm/edecan_llm/workers_ai.py` -- la medición está ahí.
    WORKERS_AI_CHAT_MODEL: str = "@cf/meta/llama-4-scout-17b-16e-instruct"
    # Modelo del alias "profundo": el ESCRITOR de posts (LinkedIn) y cualquier
    # ruta que pida `alias="profundo"`. Va SEPARADO de `WORKERS_AI_CHAT_MODEL`
    # a propósito: el chat quiere rápido + visión (scout), pero escribir un post
    # de calidad necesita un modelo fuerte. scout devuelve ~3 tokens en esa
    # tarea; nemotron-120B sí escribe (medido). El chat NO se toca. Si queda
    # vacío, "profundo" cae a `WORKERS_AI_CHAT_MODEL` como antes.
    WORKERS_AI_MODEL_PROFUNDO: str = "@cf/zai-org/glm-5.2"
    WORKERS_AI_TIMEOUT_SECONDS: float = 120.0
    WORKERS_AI_FALLBACK_MODEL: str | None = None
    # Switch de proveedor (edecan_llm.router.build_provider_from_settings).
    # `workers_ai` (default) | `azure_openai` | `openai_compat`. Se setea desde
    # platform-config.json; borrar la clave restaura Workers AI.
    LLM_PROVIDER: str | None = None
    AZURE_AI_FOUNDRY_ENDPOINT: str | None = None
    AZURE_AI_FOUNDRY_API_KEY: str | None = None
    AZURE_AI_FOUNDRY_TEXT_DEPLOYMENT: str | None = None
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
    # Webhook post-call ConvAI (ElevenLabs): pegar el signing secret al crear el
    POLLY_VOICE: str = "Lupe"
    # Máximo de respuestas LLM por llamada. Evita un Gather/LLM sin límite
    # ante silencio, bots o una llamada olvidada; el último turno cuelga.
    PHONE_MAX_TURNS: int = 8
    # Base pública por la que TWILIO tiene que poder entrar a ESTE backend.
    #
    # Normalmente es la misma `PUBLIC_BASE_URL` y por eso el default es vacío
    # (= usar `PUBLIC_BASE_URL`, comportamiento de siempre en hosted/prod). Se
    # separa porque en la instalación local `PUBLIC_BASE_URL` cumple OTRO papel:
    # es la puerta "siempre viva" que ve el teléfono del dueño (un Worker que
    # sigue contestando aunque la computadora esté apagada). Esa puerta exige
    # token de dispositivo y solo enruta lo que su lista blanca permite; Twilio
    # no tiene token ninguno, así que recibe 404, nunca consigue el TwiML y
    # reproduce su propio mensaje enlatado en inglés ("an application error has
    # occurred") mientras la llamada se queda colgada en `queued` para siempre.
    # Los webhooks tienen que llegar al backend REAL, que es donde vive el
    # estado de la llamada: normalmente, el túnel directo a la computadora.
    PHONE_WEBHOOK_BASE_URL: str = ""
    # Llamadas en vivo (Media Streams): si el tenant tiene Deepgram+ElevenLabs y
    # el webhook es HTTPS, se usa <Connect><Stream> en vez de Gather/Play.
    PHONE_REALTIME_ENABLED: bool = True

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
    # Content Studio usa el motor FyDesign integrado, pero sus defaults viven
    # separados de IMAGES_* para no contaminar chat, tools simples ni OSS.
    # Referencia-style default: gpt-image-2 primero; el motor cae a Imagen 4/Vertex.
    EDECAN_CONTENT_IMAGE_PROVIDER: str = "fydesign"
    EDECAN_CONTENT_IMAGE_MODEL: str = "gpt-image-2"
    EDECAN_CONTENT_IMAGE_FALLBACK_MODEL: str = "imagen-4"
    EDECAN_CONTENT_IMAGE_QUALITY: str = "standard"
    EDECAN_CONTENT_IMAGE_STYLE: str | None = None
    EDECAN_CONTENT_IMAGE_BRAND: str | None = None

    # FyDesign (autopost de Acme): repo local para el subprocess on-demand
    # del worker (`create_organization_linkedin_post`). Sin `FYDESIGN_DIR` cae a
    # `FYDESIGN_URL` (servidor :3000). El worker recibe `api_settings` (ver
    # `edecan_local.runtime`), por eso estos campos viven en el API Settings.
    FYDESIGN_DIR: str | None = None
    FYDESIGN_URL: str | None = None

    # "coingecko": API pública de CoinGecko, sin clave, sin costo — cotiza cripto real desde
    # el primer arranque, medido funcionando (`packages/commerce/edecan_commerce/quotes.py`).
    # Antes el default era "stub" (precio inventado con sha256 del símbolo, ver
    # `StubQuotes` — medido: BTC con 2,7% de error creíble, AAPL 166 veces el precio real).
    # "stub" sigue disponible para quien lo pida explícitamente en su propio `.env`.
    QUOTES_PROVIDER: str = "coingecko"

    # "paper" usa el simulador local. "alpaca_paper" usa la cuenta simulada
    # propia del usuario. No existe un modo live en Edecán.
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
    DATA_DIR: str = DEFAULT_DATA_DIR
    # Workspace aislado de artefactos/proyectos creados desde chat. `None`
    # deriva a `$DATA_DIR/creator`; una ruta explícita sirve para inspeccionar
    # los proyectos directamente en instalaciones locales.
    CREATOR_WORKSPACE_DIR: str | None = None
    # Studio creativo completo. En fuente se autodetecta el paquete hermano;
    # el instalador nativo fija ambas rutas a sus recursos empaquetados.
    EDECAN_STUDIO_ENGINE_DIR: str | None = None
    EDECAN_STUDIO_NODE_BINARY: str | None = None
    EDECAN_STUDIO_TIMEOUT_SECONDS: int = 1_200
    EDECAN_STUDIO_MAX_OUTPUT_BYTES: int = 16 * 1024 * 1024
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

    # Auditoría de seguridad local. PentestGPT es una dependencia opcional
    # instalada y fijada por el dueño; Edecán nunca descarga ejecutables por
    # su cuenta. Vacío = autodetectar `pentestgpt` en PATH.
    PENTESTGPT_BINARY: str | None = None
    PENTESTGPT_BACKEND: str = "claude"
    PENTESTGPT_TIMEOUT_SECONDS: int = 3600

    # Cola de jobs en modo local: "sqs" (LocalStack/AWS real, igual que hoy) o
    # "db" (tabla `jobs` como cola, sin SQS/LocalStack — pensado para el modo
    # escritorio de un solo usuario, WP-V3-05).
    QUEUE_PROVIDER: str = "sqs"

    # Ajustes locales conservados para subsistemas fuera de la inferencia
    # principal (p. ej. herramientas de ingeniería). Chat y llamadas no leen
    # estos campos.
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

    @property
    def twilio_webhook_base_url(self) -> str:
        """Base de TODA URL que se le entrega a Twilio y de la que se usa para
        verificar su firma.

        Es una sola propiedad a propósito: Twilio firma EXACTAMENTE la URL que
        pidió, así que entregarle una base y verificar con otra deja la firma sin
        cuadrar y el webhook muere en 403 — el mismo síntoma que tener la base
        mala (voz robótica en inglés y llamada colgada), pero por el otro lado.
        Quien construya una URL para Twilio usa esto, nunca `PUBLIC_BASE_URL`.
        """
        configured = (self.PHONE_WEBHOOK_BASE_URL or "").strip().rstrip("/")
        return configured or self.PUBLIC_BASE_URL.rstrip("/")

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
