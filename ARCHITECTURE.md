# ARCHITECTURE.md — Edecan

Edecan es un asistente personal de IA local-first. La experiencia de producto
se rige por [`docs/producto-assistant-first.md`](docs/producto-assistant-first.md):
una conversación por texto o voz es la entrada universal y la complejidad
técnica nunca se convierte en un menú que la persona deba aprender.

Este documento es el contrato **técnico** entre paquetes. Los nombres, firmas,
rutas y reglas de la sección 10 siguen siendo obligatorios, pero no definen la
superficie visible del producto. Si una decisión técnica contradice el contrato
assistant-first, debe conservarse la compatibilidad interna y simplificarse la
experiencia pública.

---

## 0. Reglas duras (aplican a TODO el repo)

1. **Cero secretos reales.** Solo placeholders `TU_X_AQUI` y únicamente en `.env.example` y docs. Nunca datos personales de nadie.
2. **LinkedIn PROHIBIDO** en cualquier forma: código, scopes, URLs, texto de UI, docs. Existe un test (`test_no_linkedin`) que falla si aparece la palabra en `packages/connectors/`.
3. **Solo APIs oficiales.** Cada tenant conecta SUS credenciales vía OAuth; jamás scraping, jamás credenciales compartidas o hardcodeadas.
4. **No ejecutar**: `git`, `terraform`, `aws` con efectos, `docker push`, ni llamadas de red reales en tests (usa `respx`/fakes). La infraestructura solo se ESCRIBE.
5. Idioma por defecto de UI/docs: **español**. Tests offline y deterministas.
6. Trabaja solo dentro de la carpeta raíz de este repositorio (nunca leas ni escribas fuera de ella).
7. **Assistant-first.** Chat y voz son la entrada a las capacidades. Nuevas
   verticales se implementan como herramientas/skills internas; no agregan una
   pantalla primaria ni vocabulario técnico salvo que exista una necesidad de
   usuario demostrable.

## 1. Stack elegido (y por qué)

- **Backend: Python 3.12 + FastAPI + Pydantic v2** — el mejor ecosistema para agentes/LLM, async nativo (SSE y WebSocket), tipado suficiente.
- **Monorepo con uv workspaces** — paquetes instalables (`packages/*`) y apps delgadas (`apps/*`); las extensiones comerciales viven fuera del core público.
- **PostgreSQL 16 + pgvector** (RDS en prod) — relacional + embeddings + **Row-Level Security** para multi-tenancy en una sola tecnología.
- **Redis** (ElastiCache) — sesiones refresh revocables, rate-limit, confirmaciones y códigos de emparejamiento.
- **SQS + DLQ** — trabajos asíncronos; **EventBridge Scheduler** — crons en prod.
- **Frontend: Next.js 15 (App Router) + React 19 + TypeScript + Tailwind.**
- **LLM: Anthropic primario** vía REST puro (httpx), con adaptadores OpenAI-compatible y Bedrock detrás de una interfaz común intercambiable.
- **Distribución principal: aplicación local para macOS/Windows** con backend
  local. Un despliegue self-host/hosted es una opción operativa, no la identidad
  ni la interfaz principal del producto.

## 2. Multi-tenancy y aislamiento

- **Pool compartido con RLS**: toda tabla tenant-scoped lleva `tenant_id UUID NOT NULL` y política
  `tenant_isolation: USING (tenant_id = current_setting('app.tenant_id')::uuid)`.
- La API abre cada transacción con `SET LOCAL ROLE app_user` + `SET LOCAL app.tenant_id = '<uuid>'`.
  `app_user` es un rol `NOLOGIN` sin `BYPASSRLS` con grants DML. El **worker** se conecta como owner
  (bypassa RLS por ser dueño de las tablas — las políticas no usan FORCE) pero SIEMPRE filtra por el
  `tenant_id` del job explícitamente.
- **S3**: prefijo por tenant `tenants/{tenant_id}/...` + SSE-KMS.
- **Credenciales del tenant (OAuth, Twilio)**: **cifrado envolvente** — data key AES-256-GCM por tenant
  (tabla `tenant_keys`), envuelta con KMS en prod o con `LOCAL_MASTER_KEY` (Fernet) en dev. Nunca en claro, nunca en logs.
- **Medición por tenant** en `usage_events` → cuotas de plan y facturación.
- Tier «dedicado» futuro: mismo código, despliegue aislado por cliente.

## 3. Abstracción de proveedor LLM

Interfaz única `LLMProvider` (§10.6). Implementaciones: `AnthropicProvider` (primaria),
`OpenAICompatProvider` (cualquier endpoint /chat/completions), `BedrockProvider` (stub v1).
`LLMRouter` resuelve alias lógicos — `"principal"` y `"rapido"` — a (proveedor, modelo) según env y
flag `models.premium` del plan. `usage_events` se registra con llamadas directas a
`repo.add_usage_event(...)` en cada endpoint/job, no vía el hook opcional `on_usage` del router —
ningún call site de primera parte lo conecta hoy (detalle en `edecan_llm.router.LLMRouter.__init__`).

## 4. Voz y telefonía

- **Voz web (core, flag `voice.web`)**: push-to-talk en navegador → `POST /v1/voice/transcribe` (STT) →
  turno normal del agente → `POST /v1/voice/speak` (TTS). Proveedores intercambiables (§10.9):
  Deepgram / ElevenLabs / Polly / stubs offline.
- **Telefonía (premium, flag `voice.telephony`)**: Twilio **por tenant** — el tenant conecta su propia
  cuenta (SID/token → TokenVault, connector key `"twilio"`). Webhooks TwiML (`/v1/voice/twilio/*`) con
  validación de firma. Toda llamada/SMS saliente exige: consentimiento registrado (tabla `consents`),
  ventana horaria 08:00–21:00 local del destinatario, opt-out automático («STOP»/«BAJA»), y el bot se
  identifica como asistente automatizado. Grabación desactivada por defecto.

## 5. Integraciones (conectores oficiales)

- **Núcleo**: Google (Gmail + Calendar), Microsoft (Outlook Mail + Calendar).
- **Sociales**: Meta (Páginas de Facebook + Instagram Business), X (API v2), YouTube (Data API v3).
- Todas OAuth 2.0: la plataforma registra su app; **cada tenant autoriza su propia cuenta**; tokens al
  TokenVault cifrados. REST directo con httpx (testeable con respx). **LinkedIn: excluido permanentemente.**
- Hosted: sociales gateadas por flag `connectors.social` (plan Pro+). Self-host: disponibles con tus propias apps OAuth.

## 6. Modos de distribución

- **Edecan local (principal, Apache-2.0)**: aplicación personal con credenciales
  propias, un solo dueño y datos bajo su control.
- **Self-host avanzado**: el mismo core desplegado por la persona u organización
  con Docker y credenciales propias.
- **Extensión hospedada opcional**: telefonía, campañas, cuotas y herramientas
  operativas que no deben filtrar complejidad de SaaS hacia la experiencia local.
  La API detecta el paquete opcional `edecan_premium` con `importlib.util.find_spec(...)` y monta sus rutas/herramientas cuando está instalado.
  Los flags del plan del tenant gatean cada capacidad en runtime.

## 7. Topología hospedada de referencia (opcional)

Route53 → CloudFront + WAF → ALB (ACM) → **ECS Fargate**: servicios `api` (8000), `web` (3000), `worker` (sin LB).
VPC 3 AZ; subredes privadas para **RDS PostgreSQL 16 Multi-AZ (pgvector)** y **ElastiCache Redis**; VPC endpoints
(S3, SQS, Secrets Manager, KMS). **SQS** `edecan-jobs` + `edecan-jobs-dlq` (redrive tras 5 intentos).
**S3** `edecan-files` (SSE-KMS, privado). **Secrets Manager** para secretos de plataforma inyectados a las task
definitions; **KMS CMK** para vault/S3/RDS. **EventBridge Scheduler**: cada minuto envía a SQS
`{"type":"send_reminder_scan"}` y `{"type":"automation_scan"}` (dos schedules separados
con el mismo rol IAM; el segundo dispara el barrido de agenda
documentado en `docs/automatizaciones.md`). **ECR**, CloudWatch (logs/alarmas/dashboard), AWS Budgets, **SES** para email
transaccional de la plataforma. Opcional: Bedrock como proveedor LLM regional.
Esta sección describe una topología objetivo; sus módulos Terraform, configuración DNS y
provisionamiento de SES no forman parte del repositorio público.

## 8. Entorno local de desarrollo

`docker-compose.yml` levanta SOLO dependencias:
- `postgres` — imagen `pgvector/pgvector:pg16`, user/pass/db = `edecan`, puerto **5432**
- `redis` — `redis:7-alpine`, puerto **6379**
- `localstack` — `SERVICES=s3,sqs`, puerto **4566** + contenedor init que crea bucket `edecan-files` y colas `edecan-jobs`/`edecan-jobs-dlq`

Apps por Makefile: `make api` (uvicorn :8000), `make worker`, `make web` (:3000), `make db-migrate`, `make test`, `make lint`.
`AWS_ENDPOINT_URL=http://localhost:4566` redirige boto3/aioboto3 a LocalStack.

## 9. Flujo de referencia de una conversación

`web` → `POST /v1/conversations/{id}/messages` → la API arma `ToolContext` (sesión RLS, vault, router LLM,
`extras["companion"]`) → `Agent.run_turn`: recupera memorias (`MemoryStore.search`), construye system prompt con
`PersonaConfig`, loop de tool-use (máx. 8 iteraciones), emite eventos SSE → persiste `messages` + `usage_events` →
el worker consolida memoria (job `memory_consolidate`).

---

## 10. CONTRATOS OBLIGATORIOS

### 10.1 Naming y estructura

- Producto codename **Edecán**; paquetes Python con prefijo `edecan_`.
- Layout de paquete: `packages/<dir>/pyproject.toml` (hatchling) + código en `packages/<dir>/edecan_<nombre>/` + `packages/<dir>/tests/` (salvo que se indique otra ruta de tests). Apps: `apps/api/edecan_api/`, `apps/worker/edecan_worker/`, `apps/companion/edecan_companion/`.
- Python ≥3.12, type hints obligatorios, `ruff` (line-length 100), `pytest` + `pytest-asyncio`.
- **Los tests NO importan paquetes hermanos**: usa stubs/fakes que implementen los contratos de esta sección. Importar hermanos en código de producción sí está permitido (por nombre de módulo).

### 10.2 Variables de entorno (pydantic-settings; `.env.example` las lista todas)

`ENV` (dev|prod), `PUBLIC_BASE_URL` (default `http://localhost:8000`), `WEB_BASE_URL` (default `http://localhost:3000`),
`DATABASE_URL` (default `postgresql+asyncpg://edecan:edecan@localhost:5432/edecan`), `REDIS_URL` (default `redis://localhost:6379/0`),
`JWT_SECRET`, `AUTH_RATE_LIMIT_REQUESTS` (default `10`), `AUTH_RATE_LIMIT_WINDOW_SECONDS` (default `60`),
`LOCAL_MASTER_KEY` (Fernet, dev), `KMS_KEY_ID` (prod, opcional), `AWS_REGION` (default `us-east-1`),
`AWS_ENDPOINT_URL` (dev → LocalStack), `S3_BUCKET` (default `edecan-files`), `SQS_QUEUE_URL`, `MAX_UPLOAD_BYTES` (default 25 MiB),
`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL_PRINCIPAL` (default `claude-sonnet-4-5`), `ANTHROPIC_MODEL_RAPIDO` (default `claude-haiku-4-5`),
`OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_API_KEY`, `EMBEDDINGS_MODEL`, `EMBEDDINGS_DIM` (default `1536`),
`SEARCH_PROVIDER` (stub|brave|tavily), `BRAVE_API_KEY`, `TAVILY_API_KEY`,
`VOICE_STT_PROVIDER` (stub|deepgram), `VOICE_TTS_PROVIDER` (stub|elevenlabs|polly), `DEEPGRAM_API_KEY`,
`ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `POLLY_VOICE` (default `Lupe`),
`GOOGLE_CLIENT_ID/SECRET`, `MS_CLIENT_ID/SECRET`, `META_APP_ID/SECRET`, `X_CLIENT_ID/SECRET`, `SLACK_CLIENT_ID/SECRET`,
`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `SES_FROM_EMAIL`, `SENTRY_DSN`, `LOG_LEVEL` (default `INFO`).
Twilio NO va en env: son credenciales **por tenant** en el TokenVault.

### 10.3 Esquema de datos (paquete `edecan_db`)

SQLAlchemy 2.0 async (asyncpg) + Alembic (`packages/db/alembic/`, migración `0001_initial` escrita a mano:
`CREATE EXTENSION IF NOT EXISTS vector`, tablas, índices por `tenant_id`, `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`
+ política `tenant_isolation`, rol `app_user` NOLOGIN con grants).
`edecan_db.session.get_session(tenant_id: UUID | None)` → async context manager; si `tenant_id` no es None ejecuta
`SET LOCAL ROLE app_user` y `SET LOCAL app.tenant_id`.

Tablas (todas con `id UUID PK default gen_random_uuid()`, `created_at`/`updated_at timestamptz`; las tenant-scoped además `tenant_id UUID NOT NULL` + RLS):

- `tenants(name, slug unique, plan_key, status)` — sin RLS (global)
- `users(email unique, password_hash, totp_secret nullable, is_superadmin bool)` — global
- `memberships(user_id, tenant_id, role: owner|admin|member)`
- `personas(tenant_id, user_id nullable, nombre_asistente, idioma, tono, formalidad int, emojis bool, instrucciones text, rasgos jsonb, memoria_activada bool, voice_id nullable)`
- `conversations(tenant_id, user_id, title, channel: web|voice|phone|api)`
- `messages(conversation_id, tenant_id, role, content jsonb, tool_calls jsonb nullable, tokens_in int, tokens_out int)`
- `memory_items(tenant_id, user_id, kind: fact|preference|event|entity, content text, embedding vector(1536) nullable, importance float, source text)`
- `memory_edges(tenant_id, src_id, dst_id, relation text)`
- `connector_accounts(tenant_id, connector_key, external_account_id, display_name, status, scopes jsonb)`
- `oauth_tokens(tenant_id, connector_account_id, ciphertext bytea, nonce bytea, key_version int, expires_at nullable)`
- `tenant_keys(tenant_id unique, encrypted_data_key bytea, kms_key_id nullable, version int)` — global
- `files(tenant_id, user_id, s3_key, filename, mime, size_bytes, status: uploaded|processing|ready|error)`
- `file_chunks(tenant_id, file_id, seq int, text, embedding vector(1536))`
- `reminders(tenant_id, user_id, due_at timestamptz, rrule nullable, message, channel, status: pending|sent|cancelled)`
- `contacts(tenant_id, user_id, nombre, emails jsonb, phones jsonb, empresa, notas, tags jsonb)`
- `transactions(tenant_id, user_id, fecha date, monto numeric(14,2), moneda char(3), categoria, descripcion, cuenta)`
- `campaigns(tenant_id, nombre, kind: voice|sms, script text, status, schedule jsonb)`
- `campaign_targets(tenant_id, campaign_id, contact_id nullable, phone_e164, status: pending|done|optout|error, last_attempt_at nullable)`
- `consents(tenant_id, phone_e164, kind: sms|voice, granted_at, revoked_at nullable, source)`
- `jobs(tenant_id nullable, type, payload jsonb, status: queued|running|done|error, attempts int, last_error nullable)`
- `usage_events(tenant_id, kind: llm_tokens|voice_seconds|storage_bytes|messages, quantity numeric, meta jsonb)`
- `audit_log(tenant_id nullable, actor_user_id nullable, action, target, meta jsonb)`
- `subscriptions(tenant_id, stripe_customer_id, stripe_subscription_id, plan_key, status, current_period_end)`

### 10.4 TokenVault (en `edecan_db.vault`)

```python
class TokenVault:  # se construye con (session, key_provider)
    async def put(self, tenant_id: UUID, connector_account_id: UUID, bundle: TokenBundle) -> None: ...
    async def get(self, tenant_id: UUID, connector_account_id: UUID) -> TokenBundle | None: ...
class KeyProvider(ABC):  # LocalKeyProvider (Fernet + LOCAL_MASTER_KEY) | KmsKeyProvider (boto3 KMS)
    async def wrap(self, data_key: bytes) -> bytes: ...
    async def unwrap(self, wrapped: bytes) -> bytes: ...
```
Cifrado del bundle: AES-256-GCM con la data key del tenant (`tenant_keys`; se crea perezosamente).

### 10.5 `edecan_schemas` (Pydantic v2, sin más dependencias)

- `PersonaConfig(nombre_asistente="Edecán", idioma="es", tono="cálido y profesional", formalidad=1, emojis=False, instrucciones="", rasgos=[], memoria_activada=True, voice_id=None)`
- `TokenBundle(access_token, refresh_token=None, expires_at=None, scopes=[], token_type="bearer")`
- `JobEnvelope(job_id: UUID, tenant_id: UUID | None, type: str, payload: dict, attempt: int = 0)`
- `JOB_TYPES = ("ingest_file","sync_connector","send_reminder","send_reminder_scan","run_campaign_step","generate_content","memory_consolidate")`
- `AgentEvent` = unión discriminada por `type` (§10.7), `ToolSpec`, `ToolCallData`, `TenantOut`, `UserOut`, `ChatMessageIn(text: str)`
- `edecan_schemas.plans`: `PlanDef(key, nombre, precio_usd_mes, flags: dict)` y `PLANES` (§10.13)

### 10.6 `edecan_llm`

`edecan_llm.base`:
`ChatMessage(role: Literal["system","user","assistant","tool"], content: str | list[dict])`,
`ToolSpec(name, description, input_schema: dict)`, `ToolCall(id, name, arguments: dict)`,
`Usage(input_tokens: int, output_tokens: int)`,
`CompletionRequest(model, system=None, messages, tools=[], max_tokens=1024, temperature=0.7, metadata={})`,
`CompletionResponse(text, tool_calls, usage, stop_reason: Literal["end","tool_use","max_tokens"])`,
`StreamChunk(type: Literal["text","tool_call","usage","stop"], text=None, tool_call=None, usage=None)`.

```python
class LLMProvider(ABC):
    name: str
    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...
    def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]: ...
```
`edecan_llm.router.LLMRouter(settings, on_usage=None)`:
`def resolve(self, alias: Literal["principal","rapido"], tenant_flags: dict) -> tuple[LLMProvider, str]`.
Anthropic: POST `https://api.anthropic.com/v1/messages`, headers `x-api-key` y `anthropic-version: 2023-06-01`.

### 10.7 `edecan_core` — herramientas, agente, memoria, cola

```python
class Tool(ABC):
    name: str; description: str; input_schema: dict
    requires_flags: frozenset[str] = frozenset(); dangerous: bool = False
    async def run(self, ctx: ToolContext, args: dict) -> ToolResult
@dataclass
class ToolContext: tenant_id: UUID; user_id: UUID; session: Any; settings: Any; llm: Any; vault: Any; extras: dict
@dataclass
class ToolResult: content: str; data: dict | None = None; requires_confirmation: bool = False
```
- `ToolRegistry`: `.register(tool)` (rechaza con `ValueError` cualquier tool cuyo nombre/descripción contenga «linkedin»), `.get(name)`, `.specs(flags) -> list[ToolSpec]` (filtra por `requires_flags`), `.load_entry_points(group="edecan.tools")`. Cada entry point del grupo resuelve a un callable sin argumentos que retorna `list[Tool]`.
- `edecan_core.persona.build_system_prompt(persona: PersonaConfig, memories: list[str], extra_context: str | None = None) -> str` — plantilla en español; formalidad 0–3 (tú↔usted); las instrucciones del usuario van en sección delimitada y NUNCA anulan reglas de seguridad.
- `edecan_core.agent.Agent(llm_router, registry, *, model_alias: str | None = None)` con
  `run_turn(*, ctx, persona, history: list[ChatMessage], user_text: str, flags: dict) -> AsyncIterator[AgentEvent]`
  — loop tool-use máx. 8 iteraciones; si una tool `dangerous` no está pre-aprobada emite `confirmation_required` y detiene el turno.
  `model_alias` (default `None` → `"principal"`) es el alias que `resolve()` usa para TODO el turno; `edecan_agents.orchestrator.Orchestrator` lo pasa con el `model_alias` del `AgentProfile` de cada paso (§7.9) y los demás invocadores lo omiten y siguen en `"principal"`.
- `AgentEvent` (JSON): `{"type":"text_delta","text"}` | `{"type":"tool_start","name","args"}` | `{"type":"tool_end","name","result_preview"}` | `{"type":"confirmation_required","tool_call_id","name","args"}` | `{"type":"done","usage"}` | `{"type":"error","message"}`.
- **SSE**: el endpoint de chat emite `text/event-stream` con `event:` = `message.delta` | `tool.start` | `tool.end` | `confirmation.required` | `message.done` | `error`; `data:` = JSON del AgentEvent.
- Memoria: `MemoryStore` protocolo (`async search(tenant_id, user_id, query, k=8) -> list[MemoryHit]`, `async add(...)`), impl `PgMemoryStore` (pgvector, cosine); `Embedder` protocolo `async embed(texts: list[str]) -> list[list[float]]` con `HashEmbedder` (determinista, offline, dim 1536) y `OpenAICompatEmbedder`. Grafo: `memory_edges` (`add_edge`, `neighbors`).
- Cola: `edecan_core.queue.enqueue(settings, job_type: str, payload: dict, tenant_id: UUID | None) -> UUID` (aioboto3 + `AWS_ENDPOINT_URL`).
- `ToolContext.extras` claves reservadas: `"companion"` → `async (action: str, params: dict) -> dict` o None; `"memory_store"` → `MemoryStore` opcional, leído por `Agent.run_turn` cuando `persona.memoria_activada`; `"memory_embedder"` → `Embedder` opcional, leído por `ConsultarDocumentosTool` (`edecan_toolkit.documentos`) para buscar por distancia coseno en `file_chunks`; presente solo si el tenant tiene un proveedor de embeddings real configurado (`OPENAI_COMPAT_BASE_URL`/`_API_KEY` + `EMBEDDINGS_MODEL`), si no la tool cae a `ILIKE`; `"approved_tool_calls"` → `set[str]` de `tool_call_id` ya confirmados por el usuario, exigido antes de correr una tool `dangerous`; `"flags"` → `dict` con los flags del plan del tenant (mismo valor que `run_turn(flags=...)`), leído por tools que llaman a `ctx.llm.complete(...)` directamente (p. ej. `GenerarContenidoTool` en `edecan_toolkit.contenido`) para no perder el downgrade a modelo `"rapido"` cuando el plan no tiene `models.premium`.

### 10.8 `edecan_connectors`

```python
@dataclass
class OAuthSpec: auth_url: str; token_url: str; scopes: list[str]; pkce: bool = False; extra_params: dict = field(default_factory=dict)
class Connector(ABC):
    key: str; display_name: str; oauth: OAuthSpec
    def auth_url(self, redirect_uri: str, state: str) -> str: ...
    async def exchange_code(self, code: str, redirect_uri: str, http: httpx.AsyncClient, code_verifier: str | None = None) -> TokenBundle: ...
    async def refresh(self, bundle: TokenBundle, http: httpx.AsyncClient) -> TokenBundle: ...
```
`edecan_connectors.registry.CONNECTORS: dict[str, Connector]` con keys EXACTAS: `"google"`, `"microsoft"`, `"meta"`, `"x"`, `"youtube"`.
El registry importa `edecan_connectors.social` con `try/except ImportError` y mezcla `SOCIAL_CONNECTORS`.
Las funciones de cada conector reciben el `TokenBundle` como argumento — **nunca** almacenan tokens.
Nota v2 (no reabre este contrato v1 — ver §11): fase v2 amplía `CONNECTORS` con una 6ª key (`"slack"`) mediante el mismo patrón `try/except ImportError`, ahora también sobre `edecan_connectors.messaging` (mezcla `MESSAGING_CONNECTORS`); contrato fijado en §11.13.

### 10.9 `edecan_voice`

```python
class STTProvider(ABC):
    async def transcribe(self, audio: bytes, mime: str, language: str | None = None) -> Transcript  # Transcript(text, language, confidence=None)
class TTSProvider(ABC):
    async def synthesize(self, text: str, voice_id: str | None = None, fmt: Literal["mp3","wav"] = "mp3") -> bytes
```
`edecan_voice.registry.get_stt(settings) / get_tts(settings)` según `VOICE_STT_PROVIDER`/`VOICE_TTS_PROVIDER`.
Impls: `DeepgramSTT`, `ElevenLabsTTS`, `PollyTTS`, y stubs offline (`StubSTT` → texto fijo; `StubTTS` → WAV de silencio).
La telefonía NO vive aquí (ver §10.10).

### 10.10 `edecan_premium` (extensión comercial opcional y externa)

- Entry point `edecan.tools` → `edecan_premium.tools:get_all_tools` con: `llamar_contacto`, `enviar_sms` (flag `voice.telephony`), `lanzar_campana` (flag `campaigns`) — todas `dangerous=True`.
- `edecan_premium.telephony.TwilioTenantClient` — credenciales del tenant desde el vault (connector key `"twilio"`; `TokenBundle.access_token`=AUTH_TOKEN, `scopes=[ACCOUNT_SID]`). Métodos `start_call(to, from_, twiml_url)` y `send_sms(...)` que EXIGEN consentimiento (`consents`) + ventana 08:00–21:00 del destinatario y registran `audit_log` + `usage_events`.
- `edecan_premium.twilio_router.router` — APIRouter con prefix `/v1/voice/twilio`: `POST /incoming` (TwiML `<Say>`+`<Gather input="speech">`), `POST /gather` (turno del agente sin streaming → `<Say>` + `<Gather>`), `POST /sms` (SMS entrante; solo reconoce opt-out STOP/BAJA, sin agente conversacional), `POST /status` (duración → `usage_events`). Valida `X-Twilio-Signature`.
- `edecan_premium.compliance.grant_consent(session, tenant_id, phone_e164, kind, source)` — único invocador fuera de `edecan_premium`: `edecan_api.routers.consents.router` (`POST /v1/consents`, Bearer + flag `voice.telephony`; §10.12). Vive en `apps/api`, no en la extensión externa, porque necesita `edecan_api.deps` para la auth JWT/tenant.
- `edecan_premium.limits.check_quota(session, tenant_id, plan_key, kind) -> bool` contra `PLANES`.
- `edecan_premium.campaigns.handle(env: JobEnvelope, deps)` — procesa hasta 10 `campaign_targets` por paso y re-encola.

### 10.11 Jobs y colas

Cola principal `SQS_QUEUE_URL` (dev: LocalStack `edecan-jobs`). Mensaje = JSON de `JobEnvelope`.
Worker: `edecan_worker.handlers.HANDLERS: dict[str, Handler]`, `Handler = Callable[[JobEnvelope, Deps], Awaitable[None]]`.
Reintentos: si `attempt < 5` re-encolar con backoff `min(900, 2**attempt * 30)`s; si no, dejar ir a DLQ.
Tipos: los 12 de `JOB_TYPES` (§10.5). `send_reminder_scan` busca `reminders` vencidos y encola `send_reminder` por cada uno.

### 10.12 API HTTP (`edecan_api`, prefijos pinned)

- `GET /healthz` → `{"status":"ok"}` (liveness) · `GET /readyz` → DB + Redis (readiness)
- `POST /v1/auth/register {email, password, tenant_name}` (crea tenant+owner+persona default; devuelve tokens) · `POST /v1/auth/login` · `POST /v1/auth/refresh` (rotación atómica de un solo uso) · `POST /v1/auth/logout` (revocación) · `POST /v1/auth/totp/enable|verify|disable` (`disable {password}` re-exige la contraseña, no un código TOTP — es la ruta de recuperación para dispositivo perdido)
- `GET /v1/me` · `GET|PUT /v1/persona` · `GET /v1/persona/preview` → `{system_prompt}`
- `GET|POST /v1/conversations` · `GET|DELETE /v1/conversations/{id}` · `POST /v1/conversations/{id}/messages {text}` → **SSE** · `POST /v1/conversations/{id}/confirm {tool_call_id, approved}` → SSE (si `approved`, ejecuta DIRECTO —sin volver a llamar al LLM— la tool `dangerous` que quedó pendiente: la lee de Redis por `tool_call_id` (TTL 15 min, de un solo uso; guardada ahí cuando el turno original se detuvo en `confirmation_required`, ver `edecan_api.routers.conversations`) — una llamada nueva al LLM acuñaría un `tool_call_id` distinto que jamás coincidiría con el aprobado. Sin confirmación pendiente → 409)
- `GET /v1/memory?q=` · `POST /v1/memory` · `DELETE /v1/memory/{id}`
- `GET /v1/connectors` · `GET /v1/connectors/{key}/authorize` → `{url}` · `GET /v1/connectors/{key}/callback` · `PUT /v1/connectors/twilio/credentials {account_sid, auth_token, phone_number}` → `204` (Twilio no es OAuth, no pasa por `authorize`/`callback`; ver §4) · `DELETE /v1/connectors/{key}/{account_id}` · *(v2, fase v2, no reabre este contrato v1 — ver §11.13)* `PUT /v1/connectors/{key}/credentials {bot_token}` → `204`, genérico para bots de mensajería sin OAuth (`key` ∈ `{"telegram", "discord"}`)
- `POST /v1/files` (multipart con tope `MAX_UPLOAD_BYTES` → S3 `tenants/{tid}/files/{fid}/{filename}` + job `ingest_file`) · `GET /v1/files` · `GET /v1/files/{id}`
- CRUD `/v1/reminders`, `/v1/contacts`, `/v1/finance/transactions` · `GET /v1/finance/summary?mes=YYYY-MM`
- `POST /v1/voice/transcribe` (audio → `{text}`) · `POST /v1/voice/speak {text}` → `audio/mpeg` — flag `voice.web` + cuota
- `POST /v1/companion/pair-code` → `{code}` (Redis TTL 600) · `WS /v1/companion/ws?code=` (device WS; la API expone `ConnectionManager.send_command(tenant_id, action, params, timeout=30)` e inyecta `extras["companion"]`)
- `GET /v1/usage` (uso del mes vs límites del plan) · `GET /v1/admin/tenants|usage` (superadmin)
- `POST /v1/billing/webhook` (Stripe, verifica firma) · `POST /v1/billing/portal` → `{url}`
- Si `edecan_premium` está instalado, montar `edecan_premium.twilio_router.router` y `edecan_api.routers.consents.router` — `POST /v1/consents {phone_e164, kind: sms|voice, source}` → `201`, flag `voice.telephony`; único invocador de `edecan_premium.compliance.grant_consent` (§10.10).

JWT HS256 (`JWT_SECRET`), claims `{sub, ten, plan, typ: access|refresh, iat, exp, jti, sid}` (access 30 min, refresh 30 días). Cada refresh se registra en Redis, se consume una sola vez de forma atómica y vuelve a validar membresía/tenant/plan contra PostgreSQL antes de rotar.
Los flags se derivan server-side desde `PLANES[plan]`; el `plan` es un claim firmado por la API y se actualiza contra PostgreSQL en cada refresh. Un access token puede conservar el plan anterior durante sus 30 minutos de vida.

### 10.13 Flags y planes (pinned en `edecan_schemas.plans`)

Flags bool: `voice.web`, `voice.telephony`, `connectors.social`, `campaigns`, `companion`, `models.premium`.
Límites int (−1 = ilimitado): `limits.messages_per_day`, `limits.voice_minutes_month`, `limits.storage_mb`, `limits.phone_numbers`, `limits.seats`.

| plan_key | precio | web voice | social | telephony | campaigns | msgs/día | min voz | storage MB | números | seats | models.premium |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `free_selfhost` | 0 | ✔ | ✔ | ✖ | ✖ | −1 | −1 | −1 | 0 | 1 | ✖ |
| `hosted_basic` | 19 | ✔ | ✖ | ✖ | ✖ | 150 | 60 | 1024 | 0 | 1 | ✖ |
| `hosted_pro` | 49 | ✔ | ✔ | ✔ | ✖ | 600 | 300 | 10240 | 1 | 1 | ✔ |
| `hosted_business` | 149 | ✔ | ✔ | ✔ | ✔ | 2000 | 1000 | 51200 | 3 | 5 | ✔ |

`companion` = ✔ en todos.

### 10.14 Varios pinned

- S3 layout: `s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{filename}`.
- Puertos dev: api 8000, web 3000, postgres 5432, redis 6379, localstack 4566. CORS: permitir `WEB_BASE_URL`.
- Frontend consume `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).
- Compose self-host de referencia: `infra/docker/compose.selfhost.yml`; Dockerfiles en `infra/docker/`.
- Herramientas del toolkit (nombres exactos): `crear_recordatorio`, `listar_recordatorios`, `agenda_eventos`, `crear_evento`, `buscar_correo`, `enviar_correo`, `buscar_contactos`, `gestionar_contacto`, `registrar_transaccion`, `resumen_finanzas`, `consultar_documentos`, `buscar_web`, `generar_contenido`, `publicar_social`, `usar_computadora`, `hora_actual`, `calculadora`, `configurar_credencial`, `acceder_codigo_local`, `diagnosticar_autorreparacion_local` y `gestionar_autorreparacion_local`. `configurar_credencial` y las escrituras de código son `dangerous=True`. `diagnosticar_autorreparacion_local` es solo lectura; `gestionar_autorreparacion_local` es local-only, exige opt-in, checkpoint Git limpio, worktree aislado, ediciones con SHA previo, comandos `argv` exactos, pruebas verdes, integración fast-forward y nunca hace push. Ver `edecan_toolkit.autoconfiguracion`, `edecan_toolkit.codigo_local` y [`docs/autorreparacion-local.md`](docs/autorreparacion-local.md).
- `SearchProvider` (en `edecan_toolkit.research`): `async search(query: str, k: int = 5) -> list[SearchHit(title, url, snippet)]`; impls `BraveSearch`, `TavilySearch`, `StubSearch` según `SEARCH_PROVIDER`.

### 10.15 Calidad

Cada paquete: `README.md` corto, tests offline deterministas (respx para HTTP), `ruff` limpio, sin `print` (usa `logging`).

---

## 11. Contratos v2

Esta sección es **EL CONTRATO técnico público** de la fase v2, con la misma
fuerza normativa que §10 de este documento. No lo reemplaza ni lo reabre:
§10 sigue fijado tal cual para v1 (naming,
env vars, esquema de datos v1, `edecan_llm`/`edecan_core`/`edecan_connectors`/
`edecan_voice`/`edecan_premium` v1, jobs v1, API HTTP v1, planes v1). Todo
 cambio de la fase v2 debe respetar §11 igual que §10 para v1. Los nombres,
rutas, tablas y flags se siguen **al pie de la letra** para que las
contribuciones entre paquetes conserven contratos compatibles.

Resumen de qué fija cada subsección:

| § | Contrato |
|---|---|
| 7.1 | Paquetes nuevos del workspace uv (`packages/{docanalysis,browser,creative,messaging,agents,automations,commerce,advisory,business}`) |
| 7.2 | Flags/límites de plan v2 (`edecan_schemas.plans`) y su matriz exacta por plan |
| 7.3 | Tipos de job v2 (`edecan_schemas.queue.JOB_TYPES`) |
| 7.4 | Las 14 tablas nuevas (`edecan_db.models` + migración `0003_v2_expansion`) |
| 7.5 | Settings/env v2 (`edecan_api.config` + `.env.example`) |
| 7.6 | Routers v2 (montaje defensivo en `edecan_api.main`) y handlers v2 (`edecan_worker.handlers`) |
| 7.7 | Herramientas nuevas (nombres exactos, español, snake_case) |
| 7.8 | Acciones nuevas del companion (IDE embebido) |
| 7.9 | Perfiles del ecosistema de agentes (`AgentProfile`, Orchestrator) |
| 7.10 | Frontend v2 (vertical slices, navegación central) |
| 7.11 | Dependencias Python nuevas permitidas |
| 7.12 | Reglas de calidad v2 (idénticas a §10.15 + recordatorios) |
| 7.13 | Excepción pinned v2 sobre `edecan_connectors`/API v1 (§10.8, §10.12): 6ª key `"slack"` + endpoint genérico de credenciales por bot (responsable de la fase v2) |

Los guardrails de producto no negociables de v2 (dinero real, control
remoto, salud/legal/finanzas informativo, solo APIs oficiales, LinkedIn
prohibido) se resumen en `docs/roadmap.md` y aplican con el mismo peso que
§0.

---

## 12. Contratos v3

Desde la fase v3, esta sección es **EL CONTRATO** de todo lo nuevo — misma
fuerza obligatoria que §10/§11. No los reemplaza ni los reabre: §10 sigue
pinned para v1 y §11 sigue pinned para v2. Todo agente
que escriba código v3 debe leer esta sección completa ANTES de escribir una
línea; los nombres/rutas/tablas/settings de abajo se siguen **al pie de la
letra** porque los demás work packages v3 se escriben en paralelo contra
estos mismos contratos (mismo criterio que dejó fase v2 en §11 — fase v3 es
el "linchpin" equivalente de v3).

### 12.a Routers v3 (montaje defensivo en `main.py`, responsable de la fase v3)

Igual patrón que v2 (§11): cada WP crea su archivo en
`apps/api/edecan_api/routers/` exportando `router`; `edecan_api.main` los
monta con `importlib.import_module` + `try/except ImportError` +
`logger.warning` si falta — tolerante a aterrizajes parciales.

| módulo | prefix | dueño |
|---|---|---|
| `credentials` | `/v1/credentials` | fase v3 |
| `setup` | `/v1/setup` | fase v3 |
| `skills` | `/v1/skills` | fase v3 |
| `smarthome` | `/v1/smarthome` | fase v3 |

`edecan_api.main.V3_ROUTER_NAMES = ("credentials", "setup", "skills", "smarthome")`.

### 12.b Credenciales por tenant bring-your-own (TokenVault, responsable de la fase v3)

Aplica el principio bring-your-own de `docs/roadmap.md`: Deepgram,
ElevenLabs y LLM dejan de leer config de plataforma
(`Settings`/`.env` global) y se resuelven por tenant, mismo mecanismo que ya
usa Twilio (§10.10) — `edecan_db.vault.TokenVault` (§10.4), sin tabla nueva.

- `connector_key ∈ {"llm", "voice_stt", "voice_tts", "homeassistant", "whatsapp"}`
  sobre la tabla v1 `connector_accounts` (§10.3) — una `connector_account`
  por `(tenant_id, connector_key)` (a diferencia de OAuth, que puede tener
  varias cuentas por conector, aquí es singular: un tenant tiene UNA
  configuración de LLM activa a la vez, etc.).
- `TokenBundle.access_token` (§10.5) guarda un **JSON serializado** con la
  configuración completa del proveedor para `"llm"`/`"voice_stt"`/
  `"voice_tts"` (ver `LLMProviderConfig` en §12.c para la forma exacta del
  caso `"llm"`) — y el **token crudo** (string tal cual, sin envolver en
  JSON) para `"homeassistant"` (long-lived access token) y `"whatsapp"`
  (token de la Cloud API). `TokenBundle.token_type` queda en `"config"`
  para `"llm"`/`"voice_stt"`/`"voice_tts"` (distingue en runtime "esto hay
  que `json.loads()`" de `"bearer"`/el `"config"` con `scopes=[ACCOUNT_SID]`
  que ya usa Twilio) y se deja `"bearer"` (default) para
  `"homeassistant"`/`"whatsapp"`.
- El resto del contrato de `TokenVault`/cifrado envolvente no cambia
  (§10.4): cifrado AES-256-GCM con la data key del tenant, nunca en claro,
  nunca en logs.

### 12.c `edecan_llm.config.LLMProviderConfig` (responsable de la fase v3)

```python
@dataclass(frozen=True)
class LLMProviderConfig:
    kind: str  # "anthropic" | "openai_compat" | "vertex" | "claude_cli" | "codex_cli" | "ollama"
    api_key: str | None = None
    base_url: str | None = None
    model_principal: str | None = None
    model_rapido: str | None = None
    extra: dict = field(default_factory=dict)
```

Resuelve la configuración de proveedor LLM **por tenant** (bring-your-own,
`docs/roadmap.md`), leída del `TokenBundle` de `connector_key="llm"`
(§12.b) cuando existe. `LLMRouter.__init__` (§10.6) gana un kwarg opcional
nuevo: `LLMRouter(settings, on_usage=None, provider_config=None)` — con
`provider_config=None` (default) el comportamiento es IDÉNTICO al de v1/v2
(resuelve desde `Settings` de plataforma, `ANTHROPIC_API_KEY` etc.); con un
`LLMProviderConfig` explícito, el router construye el proveedor que indique
`kind` en vez de leer `Settings`. `kind="claude_cli"`/`"codex_cli"` usan
`extra` para el path del binario y el timeout (default a
`CLAUDE_CLI_PATH`/`CODEX_CLI_PATH`/`LLM_CLI_TIMEOUT_SECONDS` de §12.g si
`extra` no los trae); `kind="vertex"` usa `extra` para
`project`/`location`/credenciales de service account o ADC;
`kind="ollama"` usa `base_url` (default `OLLAMA_BASE_URL` de §12.g).

### 12.d `edecan_llm.detect.detect_local_providers` (responsable de la fase v3)

```python
def detect_local_providers(settings: SettingsLike | None = None) -> dict: ...
```

Autodetección para la UX de "pocos clicks" (`docs/roadmap.md`): nunca
lanza, siempre devuelve el shape completo (con `False`/`None`/`[]` donde no
detectó nada), nunca hace red real salvo un ping local a `OLLAMA_BASE_URL`.

```python
{
    "claude_cli": {"installed": bool, "path": str | None, "version": str | None},
    "codex_cli": {"installed": bool, "path": str | None, "version": str | None},
    "ollama": {"running": bool, "base_url": str, "models": list[str]},
}
```

`"installed"` se resuelve con `shutil.which(...)` (o `CLAUDE_CLI_PATH`/
`CODEX_CLI_PATH` de §12.g si están fijados) + `<bin> --version` con timeout
corto; `"running"` de Ollama hace `GET {OLLAMA_BASE_URL}/api/tags` con
timeout corto (offline/caído ⇒ `False`, nunca excepción). El router de setup
(§12.a `setup`, fase v3) expone esto en `GET /v1/setup/detect` para que la
pantalla de Configuración ofrezca "usar mi Claude CLI ya instalado" en un
clic sin pedir ninguna credencial.

### 12.e Tabla nueva — migración `0004_v3_expansion` (responsable de la fase v3)

Mismo patrón que `0001_initial`/`0003_v2_expansion`: `id UUID PK`,
`created_at`/`updated_at`, `tenant_id UUID NOT NULL` + RLS `tenant_isolation`
+ índice por `tenant_id`. `down_revision = "0003_v2_expansion"`.

- `skills(user_id, nombre, slug, source, descripcion default '', version
  nullable, contenido, recursos default '{}', enabled default true)` +
  `UNIQUE(tenant_id, slug)` — un "Agent Skill" del marketplace abierto
  skills.sh (o instalado a mano). `source` guarda el origen (p. ej.
  `"owner/repo"`, mismo formato que `npx skills add <owner/repo>`);
  `contenido` guarda el `SKILL.md` completo tal cual.

El modelo SQLAlchemy correspondiente (`Skill`) vive en `edecan_db.models`
con el mismo estilo (mixins, `Text` para las columnas de texto) que v1/v2.

### 12.f Runner local — app de escritorio Tauri (responsable de la fase v3, `apps/local`)

Empaqueta `edecan_api` + `edecan_worker` + `edecan_db` para correr en la
máquina del cliente (`docs/roadmap.md`: backend local de la app Tauri).
Contrato pinned:

- Se invoca `python -m edecan_local`.
- Bind **SOLO** en `127.0.0.1` — nunca `0.0.0.0` (ningún puerto expuesto
  fuera de la máquina del cliente).
- Puerto `settings.LOCAL_API_PORT` (default `8765`, §12.g).
- Al quedar sano (API respondiendo, migraciones aplicadas), imprime por
  stdout la línea exacta `EDECAN_LOCAL_READY port=<p>` — el proceso Tauri
  que lo lanza como subproceso hace polling de esa línea para saber cuándo
  mostrar la UI en vez de una pantalla de carga.
- Flags de CLI: `--port` (override de `LOCAL_API_PORT`), `--data-dir`
  (override de `DATA_DIR`), `--no-web` (no monta `SERVE_WEB_DIR` aunque esté
  configurado — útil para desarrollo, cuando `apps/web` corre aparte con
  `npm run dev`).
- Apagado limpio ante `SIGTERM`/`SIGINT`: cierra conexiones de base de
  datos/HTTP en curso antes de salir, nunca un `kill -9` como único camino.
- `QUEUE_PROVIDER=db` (§12.g) es la opción recomendada para este runner
  (evita depender de LocalStack/SQS en la máquina del cliente); `sqs` sigue
  disponible si el runner local decide usarlo igual.

### 12.g Settings/env nuevos (responsable de la fase v3: `edecan_api.config` + `.env.example`)

Misma convención dura que v2 (§11): toda tool/router
v3 lee estos campos con `getattr(ctx.settings, "CAMPO", default)`, nunca
revienta si falta uno.

`EDECAN_LOCAL_MODE` (bool, default `False`), `DATA_DIR` (default
`"~/.edecan/data"`), `SERVE_WEB_DIR` (`str | None`, default `None` — si
apunta a una carpeta que existe, `edecan_api.main.create_app()` la sirve en
`"/"`, §12.a/`main.py`), `LOCAL_API_PORT` (default `8765`), `QUEUE_PROVIDER`
(`"sqs" | "db"`, default `"sqs"`), `OLLAMA_BASE_URL` (default
`"http://localhost:11434"`), `CLAUDE_CLI_PATH`/`CODEX_CLI_PATH` (`str |
None`, default `None` = autodetectar en PATH), `LLM_CLI_TIMEOUT_SECONDS`
(default `300`), `VERTEX_MODEL_PRINCIPAL` (default `"gemini-2.5-pro"`),
`VERTEX_MODEL_RAPIDO` (default `"gemini-2.5-flash"`), `SKILLS_INDEX_URL`
(default `"https://skills.sh"`), `HOMEASSISTANT_TIMEOUT_SECONDS` (default
`15`).

`REDIS_URL` (ya pinned en §10.2) gana un esquema especial en v3:
`memory://` selecciona un `fakeredis` en memoria en vez de un Redis real —
pensado para `EDECAN_LOCAL_MODE=True` (single-user, sin infraestructura
propia que levantar). Lo interpreta `edecan_api.deps` (responsable de la fase v3); el
tipo/default de `REDIS_URL` en `Settings` no cambia.

### 12.h Paquetes nuevos del workspace uv (responsable de la fase v3)

`pyproject.toml` raíz agrega a `[tool.uv.workspace].members`: `packages/skills`
(fase v3), `packages/smarthome` (fase v3), `apps/local` (fase v3) — mismo
criterio que v2 (§11): esqueleto mínimo (hatchling +
`edecan_<nombre>/__init__.py` + README de una línea) para que `uv sync` no
rompa mientras el WP dueño de cada uno aterriza su código real en paralelo.

### 12.i Nota de negocio

`docs/roadmap.md` resume las prioridades y los principios de producto
vigentes. Esta sección 12 define sus contratos técnicos para la app de
escritorio Tauri, credenciales bring-your-own, proveedores
CLI/Ollama/Vertex y skills. Ante una ambigüedad técnica sobre nombres,
rutas o tipos, esta sección es la referencia normativa.

Los guardrails de producto no negociables (dinero real, control remoto,
salud/legal/finanzas informativo, solo APIs oficiales, LinkedIn prohibido,
cero secretos reales, cero comandos git, cero infraestructura real aplicada)
se resumen en `docs/roadmap.md` y aplican con el mismo peso que §0.

---

## 13. Contratos v4

Desde la fase v4, esta sección es **EL CONTRATO** de todo lo nuevo — misma
fuerza obligatoria que §10/§11/§12. No los reemplaza ni los reabre: §10 sigue
pinned para v1, §11 para v2 y §12 para v3. Todo agente
que escriba código v4 debe leer esta sección completa ANTES de escribir una
línea; los nombres/rutas/tablas/flags de abajo se siguen **al pie de la
letra** porque los demás work packages v4 se escriben en paralelo contra
estos mismos contratos (mismo criterio que dejaron las fases v2 y v3 en
§11/§12 — fase v4 es el "linchpin" equivalente de v4).

### 13.a Routers v4 (montaje defensivo en `main.py`, responsable de la fase v4)

Igual patrón que v2/v3 (§11, §12.a): cada WP crea su
archivo en `apps/api/edecan_api/routers/` exportando `router`; `edecan_api.main`
los monta con `importlib.import_module` + `try/except ImportError` +
`logger.warning` si falta — tolerante a aterrizajes parciales.

| módulo | prefix | dueño |
|---|---|---|
| `devices` | `/v1/devices` | fase v4 (construido en este WP, ver §13.f) |
| `erp` | `/v1/erp` | fase v4 |
| `ads` | `/v1/ads` | fase v4 |
| `vehiculos` | `/v1/vehiculos` | fase v4 |
| `mensajes` | `/v1/mensajes` | fase v4 |

`edecan_api.main.V4_ROUTER_NAMES = ("devices", "erp", "ads", "vehiculos", "mensajes")`.

### 13.b Tablas nuevas — migración `0006_v4_expansion` (responsable de la fase v4)

Mismo patrón que `0001_initial`/`0003_v2_expansion`/`0004_v3_expansion`:
`id UUID PK`, `created_at`/`updated_at`, `tenant_id UUID NOT NULL` + RLS
`tenant_isolation` + índice por `tenant_id`. `down_revision =
"0005_jobs_type_check_v2_types"`. Los modelos SQLAlchemy correspondientes
(`Product`/`StockMove`/`AdDraft`) viven en `edecan_db.models`, sección "v4",
con el mismo estilo (mixins, `_enum_check`) que v1/v2/v3.

- `products(user_id, sku, nombre, descripcion default '', unidad default
  'unidad', precio numeric(14,2) nullable, costo numeric(14,2) nullable,
  stock numeric(14,3) default 0, stock_minimo numeric(14,3) default 0,
  activo default true)` + `UNIQUE(tenant_id, sku)` — inventario/ERP ligero
  (responsable de la fase v4, flag `erp.inventory`).
- `stock_moves(user_id, product_id UUID FK→products ON DELETE CASCADE, delta
  numeric(14,3), motivo, nota default '', ref nullable)` — movimiento
  (entrada/salida/ajuste) de un `product`.
- `ad_drafts(user_id, provider default 'meta', nombre, objetivo,
  presupuesto_diario numeric(14,2) nullable, moneda char(3) default 'USD',
  payload jsonb default '{}', status default 'draft', external_id nullable,
  error nullable, confirmed_at nullable, pushed_at nullable)` — borrador de
  campaña publicitaria (responsable de la fase v4, flag `tools.ads`).
  `status ∈ draft|confirmed|pushed|error|cancelled` (CHECK, mismo vocabulario
  y mismo espíritu que `orders`, §7.4 v2/§8.1: nace SIEMPRE `draft`, ninguna
  fila publica/gasta nada real por sí sola — empujarla de verdad a un
  proveedor de ads real es una acción del router `ads` que exige
  confirmación humana explícita, nunca una tool por sí sola).

### 13.c Flags nuevos v4 (pinned en `edecan_schemas.plans`, responsable de la fase v4)

Cuatro flags booleanos nuevos, espejo EXACTO de la fila `tools.images` (v2,
§11) en los 4 planes:

| flag | free_selfhost | hosted_basic | hosted_pro | hosted_business |
|---|---|---|---|---|
| `erp.inventory` | ✔ | ✖ | ✔ | ✔ |
| `tools.ads` | ✔ | ✖ | ✔ | ✔ |
| `tools.vehicles` | ✔ | ✖ | ✔ | ✔ |
| `companion.remote_input` | ✔ | ✖ | ✔ | ✔ |

`companion.remote_input` gatea la capacidad de control remoto real
(inyección de mouse/teclado) que fase v4 construyó sobre
`remote_sessions`/`companion.remote_view` (v2, fase v2, que se queda como
el nivel solo-vista): `kind="control"` en `POST /v1/remote/sessions` +
`POST /v1/remote/sessions/{id}/input`
(`apps/api/edecan_api/routers/remote.py::send_input`), ejecutado en el
companion vía `InputBackend`/`_QuartzInputBackend`
(`apps/companion/edecan_companion/actions.py`) tras aprobación local por
comando (`approval.py::_approve_input_action`) y el opt-in explícito
`remote_input_enabled` de `companion.yaml` (`config.py`) — detalle completo
de los 4 candados en serie en `docs/control-remoto.md` §7bis. Aplica el
mismo guardrail no negociable de
`docs/control-remoto.md`:
arquitectura tipo TeamViewer/AnyDesk, emparejamiento explícito + aprobación
humana, NUNCA un backdoor silencioso — ningún flag de plan reemplaza esa
aprobación explícita en el momento de la sesión.

**Corrección (2026-07-09, riesgo-legal-tos)**: `companion.remote_input` y
`companion.ide` (v2) gatean `POST /v1/remote/sessions{,/{id}/input}` y
`/v1/ide/*` — pero `edecan_companion.actions.ACTIONS` es un ÚNICO dispatch
table compartido, y la tool de chat `usar_computadora`
(`edecan_toolkit.computadora`) le reenvía al companion CUALQUIER `accion`
que decida el modelo por el mismo canal (`ConnectionManager.send_command`),
con `requires_flags = frozenset({"companion"})` — solo el flag base, sin
distinguir acción. Un tenant `hosted_basic` (`companion=True`,
`companion.remote_input=False`) podía alcanzar `input_pointer`/`input_key`
igual con solo pedírselo al modelo por chat, saltándose el límite de plan
que sí aplican los dos routers dedicados. `UsarComputadoraTool.run()` ahora
replica, acción por acción, los mismos flags fino que `_require_companion_ide`/
`_require_remote_view`/`_require_remote_control` (`companion.ide` para
`list_tree`/`search_files`/`apply_edit`; `companion.remote_view` para
`screenshot`; `companion.remote_view` + `companion.remote_input` para
`input_pointer`/`input_key`) antes de invocar al companion — fail-closed si
`ctx.extras["flags"]` falta. Las 7 acciones v1 (`open_app`/`read_dir`/
`read_file`/`write_file`/`clipboard_get`/`clipboard_set`/`run_command`) no
cambian: siguen exigiendo solo `companion`. Ver
`packages/toolkit/tests/test_computadora.py`.

**Corrección (2026-07-09, plan-flag-bypass, medium)**: la corrección anterior
dejó `companion.ide` cubriendo solo 3 de las 6 acciones que
`ide._require_companion_ide` protege de verdad (routers/ide.py sirve, además,
`GET /file` -> `read_file`, `PUT /file` -> `write_file` y `POST /run` ->
`run_command` detrás del MISMO gate) — de las "7 acciones v1" de arriba,
`read_file`/`write_file`/`run_command` SÍ están servidas bajo `/v1/ide/*` en
el servidor (aunque `edecan_companion.actions._IDE_ACTIONS`, el gate LOCAL
del companion vía `ide_enabled`, no las trate como "de IDE"), así que un
tenant `companion=True`/`companion.ide=False` podía leerlas/escribirlas o
correr comandos por chat aunque el panel IDE se lo negara con 403. No
explotable con la matriz de planes vigente (`companion.ide` es siempre
`True` cuando `companion` lo es, `edecan_schemas.plans.PLANES`), pero sí una
inconsistencia real del mismo dispatch table que esta sección documenta.
`_ACCIONES_IDE` (`computadora.py`) ahora tiene las 6 acciones; de las 7
acciones v1 originales solo `open_app`/`read_dir`/`clipboard_get`/
`clipboard_set` siguen exigiendo nada más que `companion`.

### 13.d `connector_keys` nuevos del vault — `"ads"` y `"vehicles"`

Mismo mecanismo bring-your-own que §12.b (`edecan_db.vault.TokenVault`,
§10.4, sin tabla nueva): una `connector_account` singleton por
`(tenant_id, connector_key)`.

- `"ads"` (responsable de la fase v4): credenciales de la cuenta de ads del propio
  cliente (p. ej. Meta/Google/TikTok ads — el `provider` de cada `ad_draft`,
  §13.b, decide contra cuál). `TokenBundle.access_token` = JSON serializado
  con al menos una clave `"kind"` discriminadora (mismo criterio que
  `LLMProviderConfig.kind`, §12.c) más lo que cada proveedor necesite; forma
  exacta la fija fase v4. `token_type = "config"` (mismo criterio que
  `"llm"`/`"voice_stt"`/`"voice_tts"` en §12.b: "hay que `json.loads()`
  esto", a diferencia del `"bearer"` de un token crudo).
- `"vehicles"` (responsable de la fase v4): credenciales de la cuenta del
  fabricante/plataforma del vehículo del cliente (p. ej. Tesla/SmartCar).
  Misma forma que `"ads"`: `TokenBundle.access_token` JSON con `"kind"`
  discriminador, `token_type = "config"`.

Ninguno de los dos existe todavía en código (`edecan_ads`/`edecan_vehicles`
son esqueletos, §13.h) — quedan pinned aquí para que su WP dueño no tenga que
inventar el nombre de la clave ni su forma.

### 13.e Herramientas nuevas pinned (nombres exactos, español, snake_case)

Mismo criterio que §10.14/§11: nombres fijados desde
ya aunque el paquete todavía sea un esqueleto (§13.h), para que ningún WP en
paralelo invente un nombre distinto.

| tool | dueño real | `requires_flags` | `dangerous` |
|---|---|---|---|
| `gestionar_inventario` | fase v4 | `{"erp.inventory"}` | `False` |
| `estado_inventario` | fase v4 | `{"erp.inventory"}` | `False` |
| `ads_resumen` | fase v4 (`edecan_ads`) | `{"tools.ads"}` | `False` |
| `ads_preparar_campana` | fase v4 (`edecan_ads`) | `{"tools.ads"}` | `True` |
| `vehiculo_estado` | fase v4 (`edecan_vehicles`) | `{"tools.vehicles"}` | `False` |
| `vehiculo_controlar` | fase v4 (`edecan_vehicles`) | `{"tools.vehicles"}` | `True` |

`gestionar_inventario`/`estado_inventario`: el paquete Python que las aloja
(un `packages/erp` nuevo, o una extensión de `packages/business` ya
existente) queda a criterio de fase v4 — este WP (§13.h) solo reserva
esqueleto de workspace para `ads`/`vehicles`, no para `erp`; el router `erp`
en sí (prefix `/v1/erp`) SÍ es un módulo nuevo pinned en `apps/api/
edecan_api/routers/erp.py` (§13.a), sea cual sea el paquete Python que
termine importando desde ahí.

`ads_preparar_campana` es `dangerous=True`: aunque lo único que hace es
dejar un `ad_draft` en `status="draft"` (§13.b) y nunca gasta presupuesto
real por sí sola, el gate de "esto mueve algo real" es doble — el primer
gate es la confirmación del *tool call* en el chat (`dangerous=True`, loop
de `Agent.run_turn`, `ARCHITECTURE.md` §10.7) y el segundo es la
confirmación humana explícita en el router `ads` antes de empujar el
borrador a Meta (mismo criterio que `preparar_orden`/`preparar_pago` en v2,
§11, que también son `dangerous=True` pese a solo dejar
un borrador — ver también §14.e, mismo criterio para `preparar_reserva`/
`preparar_nomina`). `vehiculo_controlar` también es `dangerous=True`:
cualquier acción que mueva/abra/encienda algo físico real exige
confirmación explícita del usuario (`ARCHITECTURE.md` §10.7, loop de
`Agent.run_turn`), mismo criterio que `usar_computadora`/`llamar_contacto`.

**Nota de alcance (ver `docs/vehiculos.md`)**: `vehiculo_estado`/`vehiculo_controlar`
quedan pinned arriba por completitud histórica, pero el dueño del proyecto
decidió sacar vehículos del alcance del producto. `apps/api/pyproject.toml`
a propósito NO declara `edecan-vehicles` como dependencia, así que estas dos
tools nunca se registran vía el entry point `edecan.tools` en ningún build
real (ver `docs/vehiculos.md`). El código de
`packages/vehicles/edecan_vehicles/tools.py` existe pero no está conectado a
nada — esto NO es trabajo pendiente de un WP futuro, es una exclusión de
producto deliberada. No inviertas más agentes completándolo.

### 13.f Router `devices` (responsable de la fase v4, construido en este WP)

`prefix="/v1/devices"`, sobre la tabla `devices` que ya existe desde
`0003_v2_expansion` (§11) — este WP no le agrega
columnas, solo la primera superficie HTTP CRUD:

- `GET /v1/devices` → lista los dispositivos del tenant (todos los usuarios
  del tenant).
- `POST /v1/devices {nombre, plataforma, kind: companion|mobile,
  fingerprint?}` → `201` con el dispositivo creado; si `fingerprint` viene
  no vacío y ya existe un dispositivo `active` del MISMO usuario con ese
  fingerprint, es idempotente: actualiza `nombre`/`last_seen_at` del
  existente y responde `200` con él en vez de duplicar.
- `POST /v1/devices/{id}/heartbeat` → `204`, refresca `last_seen_at`. `404`
  si no existe.
- `POST /v1/devices/{id}/revoke` → `200`, pasa `status` a `revoked` +
  `repo.add_audit_log` (acción `"devices.revoked"`). `404` si no existe.

Sin gate de flag de plan: `companion` (§10.13) ya es `True` en los 4 planes.

### 13.g `docs/api.md` — nadie lo toca en v4

A diferencia de v1/v2/v3 (donde cada WP dueño de un router nuevo solía
sumar su sección a `docs/api.md`), en v4 **ningún WP edita
`docs/api.md`**: con 5 routers nuevos aterrizando en paralelo (§13.a) más
los que sigan sumándose, mantenerlo sincronizado a mano por WP dejaría de
ser confiable (conflictos de merge conceptuales, secciones a medio escribir
si un WP no aterriza todavía). Queda como deuda aceptada para una pasada de
documentación dedicada posterior, fuera de esta ola — mismo espíritu que el
"enlace roto temporal es deuda aceptada" de la navegación (§13.i).

### 13.h Paquetes nuevos del workspace uv (responsable de la fase v4)

`pyproject.toml` raíz agrega a `[tool.uv.workspace].members`: `packages/ads`
(responsable de la fase v4), `packages/vehicles` (responsable de la fase v4) — mismo
criterio que v2/v3 (§11, §12.h): esqueleto mínimo (hatchling +
`edecan_<nombre>/__init__.py` defensivo + README de una línea) para que
`uv sync` nunca rompa mientras el WP dueño de cada uno aterriza `tools.py`
real en paralelo. El `__init__.py` de ambos sigue EXACTAMENTE esta forma:

```python
try:
    from .tools import get_all_tools
except ImportError:  # el módulo de tools lo aporta fase v4 en paralelo
    def get_all_tools():
        return []
```

Los paquetes responsables de `tools.py` en la fase v4 NUNCA editan este
`__init__.py` — solo agregan `tools.py` (y lo que haga falta) junto a él.
Entry points `[project.entry-points."edecan.tools"]`: `ads =
"edecan_ads:get_all_tools"` / `vehicles = "edecan_vehicles:get_all_tools"`.

`packages/vehicles` sigue siendo miembro del workspace uv (por eso `uv sync`
no rompe) pero, a diferencia de `ads`, su entry point nunca se activa en un
build real: `edecan-vehicles` a propósito no es dependencia de
`apps/api/pyproject.toml` (ver nota de alcance en §13.e y `docs/vehiculos.md`).

### 13.i Frontend v4 (responsable de la fase v4 para la navegación; páginas en paralelo)

`apps/web/src/components/layout/nav-items.ts` gana dos entradas: `{ href:
"/app/inventario", label: "Inventario", icon: BoxIcon }` (junto a Negocios,
página la construye fase v4) y `{ href: "/app/mensajes", label: "Mensajes",
icon: InboxIcon }` (junto a Conectores, página la construye fase v4) —
`BoxIcon`/`InboxIcon` nuevos en `apps/web/src/components/icons.tsx`, mismo
estilo SVG inline que el resto. Enlace roto temporal hasta que esos WPs
aterricen sus páginas es deuda aceptada, mismo criterio que v2 (§11
§11).

### 13.j Nota de negocio

Las capacidades ERP, ads y vehículos de esta sección conservan los
principios de producto resumidos en `docs/roadmap.md`. Los
guardrails no negociables (dinero real nunca se mueve solo — aplica
directo a `ad_drafts`/`ads_preparar_campana`, §13.b/§13.e —, control remoto
con emparejamiento explícito, salud/legal/finanzas informativo, solo APIs
oficiales, LinkedIn prohibido, cero secretos reales, cero comandos git, cero
infraestructura real aplicada) siguen sin cambios y aplican con el mismo
peso que `ARCHITECTURE.md` §0.

---

## 14. Contratos v5

Desde la fase v5, esta sección es **EL CONTRATO** de todo lo nuevo — misma
fuerza obligatoria que §10/§11/§12/§13. No los reemplaza ni los reabre: §10
sigue pinned para v1, §11 para v2, §12 para v3 y §13
para v4. Todo agente que escriba código v5 debe leer esta sección completa
ANTES de escribir una línea; los nombres/rutas/tablas/flags de abajo se
siguen **al pie de la letra** porque los demás work packages v5 se escriben
en paralelo contra estos mismos contratos (mismo criterio que dejaron
las fases v2, v3 y v4 en §11/§12/§13 — fase v5 es el "linchpin"
equivalente de v5).

### 14.a Routers v5 (montaje defensivo en `main.py`, responsable de la fase v5)

Igual patrón que v2/v3/v4 (§11, §12.a, §13.a): cada WP
crea su archivo en `apps/api/edecan_api/routers/` exportando `router`;
`edecan_api.main` los monta con `importlib.import_module` +
`try/except ImportError` + `logger.warning` si falta — tolerante a
aterrizajes parciales.

| módulo | prefix | dueño real |
|---|---|---|
| `rrhh` | `/v1/rrhh` | un WP de seguimiento (extiende `packages/business/edecan_business`, ver §14.f) |
| `viajes` | `/v1/viajes` | fase v5 (`packages/travel`, `edecan_travel`) |
| `voz_avanzada` | `/v1/voz` | fase v5 (`packages/voice`, `edecan_voice`) |

`edecan_api.main.V5_ROUTER_NAMES = ("rrhh", "viajes", "voz_avanzada")` — el
NOMBRE DE MÓDULO (`edecan_api/routers/voz_avanzada.py`) sigue el patrón
`rrhh`/`viajes`/`voz_avanzada` de este WP; su `prefix=` interno (`/v1/voz`,
más corto) es la única excepción a la convención módulo=prefix que siguen
el resto de routers del repo — decisión de su dueño real (fase v5), no de
este WP: el montaje defensivo de `main.py` solo importa por nombre de
módulo, nunca asume el prefix. A diferencia de v4 (donde fase v4 construyó
`devices` de verdad, §13.f), este WP NO construye ningún router v5 real —
los 3 quedan para WPs paralelos.

### 14.b Tablas nuevas — migración `0007_v5_expansion` (responsable de la fase v5)

Mismo patrón que `0001_initial`/`0003_v2_expansion`/`0004_v3_expansion`/
`0006_v4_expansion`: `id UUID PK`, `created_at`/`updated_at`, `tenant_id
UUID NOT NULL` + RLS `tenant_isolation` + índice por `tenant_id`.
`down_revision = "0006_v4_expansion"`. Los modelos SQLAlchemy
correspondientes viven en `edecan_db.models`, sección "v5", con el mismo
estilo (mixins, `_enum_check`) que v1-v4.

- `employees(user_id, nombre, email nullable, puesto default '',
  salario_mensual nullable, moneda default 'USD', fecha_ingreso nullable,
  status default 'active', meta default '{}')` — RRHH ligero (flag
  `erp.hr`). `status` queda texto abierto a propósito (sin vocabulario
  pinned acá, mismo criterio que `tenants.status`).
- `time_off(employee_id FK→employees, kind, desde date, hasta date, status
  default 'pending', notas default '')` — ausencia/vacaciones de un
  `employee`. `status ∈ pending|approved|rejected|cancelled` (CHECK): a
  diferencia de `employees.status`, SÍ es una máquina de estados explícita
  (aprobar/rechazar una solicitud).
- `payroll_runs(user_id, periodo, status default 'draft', total default 0,
  moneda default 'USD', notas default '', approved_at nullable)` — una
  corrida de nómina. `status ∈ draft|approved|paid|cancelled` (CHECK) —
  mismo espíritu que `ad_drafts`/`orders` (§13.b/§11):
  nace SIEMPRE `draft`, ninguna fila paga nada real por sí sola;
  `approved_at` queda `NULL` hasta que un paso explícito y confirmado por
  el humano la apruebe (principio de aprobación humana de
  `docs/roadmap.md`).
- `payroll_items(payroll_run_id FK→payroll_runs, employee_id FK→employees,
  bruto, deducciones default 0, neto)` — la línea de un `employee` dentro
  de un `payroll_run`.
- `voice_consents(user_id, voice_name, provider_voice_id nullable,
  consent_file_id nullable, attestation default false, status default
  'attested', meta default '{}')` — consentimiento de clonación de voz
  (flag `voice.cloning`). `status ∈ attested|revoked` (CHECK).
  `consent_file_id` NO lleva FK (referencia informativa opcional a un
  `File`, no forzada a nivel de base de datos).

Además, dos `ALTER TABLE` sobre tablas ya existentes (misma migración):

- `devices` (v2, §11) gana `push_token text NULL` y
  `push_platform text NULL` — token de push nativo (APNs/FCM) y qué
  plataforma lo emitió, responsable de la fase v5 (`notifications.push`). Sin
  CHECK en `push_platform`: vocabulario abierto a propósito.
- `skills` (v3, §12e) gana `trust_tier text NOT NULL DEFAULT 'sin_revisar'`
  y `capabilities jsonb NOT NULL DEFAULT '[]'` — modelo de trust
  tiers/capacidades adaptado de OpenJarvis (`src/openjarvis/skills/`, ver
  `NOTICE`),
  dueño real un WP de seguimiento. `trust_tier` queda texto abierto a
  propósito (ese WP define su propia escala); nace `'sin_revisar'` para que
  ninguna skill recién instalada quede marcada como confiable por
  accidente.

La misma migración también actualiza el `CHECK` de `jobs.type` (creado en
`0001_initial`, ya extendido una vez por `0005_jobs_type_check_v2_types`)
para sumar el 11º job type de §14.d — mismo patrón `DROP CONSTRAINT` + `ADD
CONSTRAINT` que usó `0005` (Postgres no soporta modificar la expresión de un
CHECK existente in place). Sin esto, `QUEUE_PROVIDER="db"` (§12.g) rechazaría
cualquier `INSERT INTO jobs` con `type='generate_podcast'` con un
`CheckViolationError` — exactamente la clase de bug que ya documentó
`0005_jobs_type_check_v2_types` para los 3 tipos de v2.

### 14.c Flags nuevos v5 (pinned en `edecan_schemas.plans`, responsable de la fase v5)

Cinco flags booleanos nuevos. A diferencia de v4 (§13.c, espejo EXACTO de
`tools.images` en los 4 planes), la matriz v5 NO es uniforme:

| flag | free_selfhost | hosted_basic | hosted_pro | hosted_business |
|---|---|---|---|---|
| `erp.hr` | ✔ | ✔ | ✔ | ✔ |
| `tools.travel` | ✔ | ✖ | ✔ | ✔ |
| `voice.cloning` | ✔ | ✖ | ✔ | ✔ |
| `tools.podcast` | ✔ | ✖ | ✔ | ✔ |
| `notifications.push` | ✔ | ✔ | ✔ | ✔ |

`erp.hr`/`notifications.push` son "básicas" (✔ incluso en `hosted_basic`,
mismo criterio que `companion`, §10.13); `tools.travel`/`voice.cloning`/
`tools.podcast` siguen el patrón premium de `tools.images` (✖ solo en
`hosted_basic`).

### 14.d Canal `"mobile"` de recordatorios (responsable de la fase v5)

`apps/api/edecan_api/routers/reminders.py::ReminderIn.channel`/
`ReminderPatch.channel` (§10.12) suman `"mobile"` al vocabulario de v1
(`web|voice|phone|api`, §10.3) — push nativo a la app móvil, dueño real
fase v5, que consume `devices.push_token`/`push_platform` (§14.b). Este WP
además convierte `channel` en un campo VALIDADO explícitamente (antes de v5
`ReminderIn.channel` era `str` sin restricción, cualquier valor pasaba) —
`Literal["web", "voice", "phone", "api", "mobile"]`.

**Deuda conocida, no resuelta por este WP**: `packages/toolkit/
edecan_toolkit/recordatorios.py::CrearRecordatorioTool` (`_CANALES_VALIDOS`)
es un SEGUNDO allowlist independiente, usado por la tool del agente
`crear_recordatorio` en vez de este router HTTP — sigue sin incluir
`"mobile"` (cae en silencio a `"web"` si el agente lo intenta). Sincronizar
ambos queda pendiente para un WP de seguimiento; `packages/toolkit/` no está
en las rutas que este WP puede tocar.

`edecan_schemas.queue.JOB_TYPES` (§10.5) suma un 11º valor, `generate_podcast`
(al final, después de los 10 de v1+v2) — job del generador de podcasts
(§14.e/§14.f), responsable de la fase v5. `apps/worker/edecan_worker/handlers`
lo registra vía `_register_defensive(HANDLERS, "generate_podcast",
"generate_podcast")`, mismo criterio defensivo que `run_mission`/
`run_automation`/`automation_scan` de v2 (§11).

### 14.e Herramientas nuevas pinned (nombres exactos, español, snake_case)

Mismo criterio que §10.14/§11/§13.e: nombres fijados desde ya para que
ningún WP en paralelo invente un nombre distinto.

| tool | dueño real | `requires_flags` | `dangerous` |
|---|---|---|---|
| `gestionar_empleado` | un WP de seguimiento (`packages/business/edecan_business/rrhh.py`) | `{"erp.hr"}` | `False` |
| `registrar_ausencia` | ídem | `{"erp.hr"}` | `False` |
| `preparar_nomina` | ídem | `{"erp.hr"}` | `True` |
| `buscar_vuelos` | fase v5 (`edecan_travel`) | `{"tools.travel"}` | `False` |
| `buscar_hoteles` | fase v5 (`edecan_travel`) | `{"tools.travel"}` | `False` |
| `estado_vuelo` | fase v5 (`edecan_travel`) | `{"tools.travel"}` | `False` |
| `preparar_reserva` | fase v5 (`edecan_travel`) | `{"tools.travel"}` | `True` |
| `rastrear_paquete` | fase v5 (`edecan_travel`) | `{"tools.travel"}` | `False` |
| `sintetizar_voz` | fase v5 (`edecan_voice`) | `{"voice.web"}` | `False` |
| `listar_voces` | fase v5 (`edecan_voice`) | `{"voice.web"}` | `False` |
| `crear_podcast` | un WP de seguimiento (fase v5) | `{"tools.podcast"}` | `False` |
| `generar_efecto_sonido` | un WP de seguimiento (fase v5) | `{"tools.podcast"}` | `False` |
| `predecir_serie` | un WP de seguimiento (extiende `edecan_docanalysis`, ver nota) | — (sin flag) | `False` |
| `detectar_anomalias` | un WP de seguimiento (extiende `edecan_docanalysis`, ver nota) | — (sin flag) | `False` |

`preparar_nomina`/`preparar_reserva` son `dangerous=True`: cualquier acción
que comprometa dinero real (pagar nómina, reservar un vuelo/hotel) exige
confirmación explícita del usuario (`ARCHITECTURE.md` §10.7, loop de
`Agent.run_turn`) — mismo criterio que `preparar_orden`/`preparar_pago` (v2)
y `vehiculo_controlar` (v4). Ninguna de las dos ejecuta el pago/la reserva
real por sí sola: `preparar_nomina` deja un `payroll_run` en `status="draft"`
(§14.b); `preparar_reserva` deja un borrador (`orders`, ya existente desde
v2 — `packages/travel` no crea tabla propia para esto) — el humano decide
comprar de verdad directo con la aerolínea/hotel/nómina, Edecán nunca
completa esa transacción.

`sintetizar_voz`/`listar_voces` gatean `voice.web`, no `voice.cloning`, aunque
vivan en `edecan_voice`: ninguna de las dos clona nada — clonar vive
EXCLUSIVAMENTE detrás de `POST /v1/voz/clones`
(`apps/api/edecan_api/routers/voz_avanzada.py`), un endpoint de UI con un
humano presente, nunca una tool. Exponerlas al agente es la misma capacidad
que ya gatea `/v1/voice/{transcribe,speak}` (`apps/api/edecan_api/routers/
voice.py`), solo invocada por el modelo en vez de por el usuario desde el
navegador — ver el docstring de `packages/voice/edecan_voice/tools.py` y
`docs/voz-telefonia.md`. `voice.cloning` sigue gateando únicamente la
creación/gestión de clones (`/v1/voz/clones/*`); no gatea la síntesis ni el
listado de voces ya conectadas.

`predecir_serie`/`detectar_anomalias` (predicción de series de tiempo,
detección de anomalías/outliers) encajan temáticamente con "📊 Analista" de
`docs/analista.md` — mismo
dominio que `edecan_docanalysis` (`docs/analista.md`), cuyas 6 tools
existentes tampoco llevan flag de plan ni son `dangerous` ("son un punto de
partida útil, no un reemplazo de una revisión humana"). Este WP (linchpin de
contratos compartidos) solo reserva los NOMBRES — no crea ningún paquete ni
router nuevo para ellas; su dueño real y su paquete final quedan para un WP
de seguimiento.

### 14.f Paquete nuevo del workspace uv: `packages/travel` (responsable de la fase v5 el esqueleto; fase v5 el código real)

`pyproject.toml` raíz agrega a `[tool.uv.workspace].members`:
`packages/travel` — mismo criterio que v2/v3/v4 (§11, §12.h, §13.h):
esqueleto mínimo para que `uv sync` nunca rompa mientras el WP dueño
aterriza su código real en paralelo. Entry point
`[project.entry-points."edecan.tools"] travel = "edecan_travel:get_all_tools"`.
`apps/api/pyproject.toml` y `apps/worker/pyproject.toml` declaran
`edecan-travel` como dependencia (mismo motivo que `edecan-ads` en v4,
§13.h: sin esto, `uv sync --no-dev --package edecan-api`/`edecan-worker`
—los Dockerfiles de producción— la excluirían en silencio).

`edecan_voice` (paquete YA existente desde v1, §10.9) gana su PROPIO entry
point `edecan.tools` en v5 (responsable de la fase v5, sin esqueleto de por medio
— a diferencia de `travel`/`ads`/`vehicles`, este paquete ya existía con
código real): `[project.entry-points."edecan.tools"] voice =
"edecan_voice:get_all_tools"` → `sintetizar_voz`/`listar_voces` (§14.e).
Como consecuencia, `apps/worker/pyproject.toml` (que antes de v5 NO
dependía de `edecan_voice`, solo `apps/api` lo hacía para el registro
STT/TTS) ahora también declara `edecan-voice` — mismo motivo silencioso que
`edecan-ads`: sin la línea, el `ToolRegistry` headless del worker
(`run_automation`/`run_mission`) nunca vería esas 2 tools nuevas.
`apps/desktop/packaging/edecan_local.spec` (`EDECAN_TOOL_PACKAGES`) suma
`edecan_travel` y `edecan_voice` (este último aparece DOS veces en el
`.spec` a propósito — ya estaba en `EDECAN_CORE_PACKAGES` por su import
estático, y ahora también en `EDECAN_TOOL_PACKAGES` por su entry point
nuevo; `collect_all()` corriendo dos veces sobre el mismo paquete es
inofensivo). `edecan_vehicles` NO se agrega a ninguna de estas 3 listas —
exclusión deliberada que sigue vigente (§13.e/§13.h y
`docs/vehiculos.md`).

### 14.g Frontend v5 (responsable de la fase v5 para la navegación; páginas en paralelo)

`apps/web/src/components/layout/nav-items.ts` gana tres entradas: `{ href:
"/app/rrhh", label: "RRHH", icon: TeamIcon }` (junto a Inventario), `{ href:
"/app/viajes", label: "Viajes", icon: PlaneIcon }` (junto a Órdenes) y
`{ href: "/app/voz", label: "Voz", icon: MicIcon }` (junto a Configuración)
— `TeamIcon`/`PlaneIcon` nuevos en `apps/web/src/components/icons.tsx`,
mismo estilo SVG inline que el resto; `"Voz"` REUTILIZA el `MicIcon` ya
existente (usado por el composer de chat y por Configuración) en vez de
declarar un ícono nuevo con el mismo nombre — el trabajo pedía "MicIcon"
sin saber que ya existía; redeclararlo habría roto la compilación. Enlace
roto temporal hasta que cada WP dueño aterrice su página es deuda aceptada,
mismo criterio que v2/v4 (§11, §13.i).

### 14.h `docs/api.md` — SÍ se actualiza en v5 (a diferencia de v4)

v4 (§13.g) decidió que ningún WP tocara `docs/api.md` por el riesgo de
conflictos de merge conceptuales con 5 routers aterrizando en paralelo. v5
retoma la convención de v1-v3 (cada WP dueño de un router nuevo suma su
propia sección): este WP agrega una sección "Rutas v5 (montaje defensivo)"
de alto nivel (los 3 prefijos + una línea por endpoint conocido a la fecha
de este WP) — los detalles finos de cada router los documenta su propio WP
dueño en su propio `docs/<feature>.md` (mismo patrón que `docs/vehiculos.md`,
`docs/ads.md`, etc.), sin que esta sección compartida se vuelva un cuello de
botella de merge.

### 14.i Nota de negocio

RRHH/nómina, viajes y voz avanzada (clonación, podcasts) conservan los
principios de producto resumidos en `docs/roadmap.md`, además de los dos
objetivos de reuso de
OpenJarvis pendientes (`scripts/install/install.sh` → `scripts/
instalar-selfhost.sh`; `src/openjarvis/skills/` → `packages/skills/
edecan_skills`, trust tiers/capacidades, ver `NOTICE`). Los guardrails no
negociables (dinero real nunca se mueve solo — aplica directo a
`payroll_runs`/`preparar_nomina` y a las reservas de `preparar_reserva`,
§14.b/§14.e —, control remoto con emparejamiento explícito, salud/legal/
finanzas informativo, solo APIs oficiales, LinkedIn prohibido, cero secretos
reales, cero comandos git, cero infraestructura real aplicada, y la regla
de §13.e y `docs/vehiculos.md`: cero inversión nueva en
`packages/vehicles`/`routers/vehiculos.py`) siguen sin
cambios y aplican con el mismo peso que `ARCHITECTURE.md` §0.

---

## 15. Contratos v6

Desde la fase v6, esta sección es **EL CONTRATO** de todo lo nuevo — misma
fuerza obligatoria que §10/§11/§12/§13/§14. No los reemplaza ni los reabre:
§10 sigue pinned para v1, §11 para v2, §12 para v3, §13
para v4 y §14 para v5. Todo agente que escriba código v6 debe leer esta
sección completa ANTES de escribir una línea; los nombres/rutas/tablas/flags
de abajo se siguen **al pie de la letra** porque los demás work packages v6
se escriben en paralelo contra estos mismos contratos (mismo criterio que
dejaron las fases v2, v3, v4 y v5 en §11/§12/§13/§14 — fase v6 es
el "linchpin" equivalente de v6). Igual que v5 (§14.a), este WP NO construye
ningún router v6 real — los 3 quedan para WPs paralelos.

### 15.a Routers v6 (montaje defensivo en `main.py`, responsable de la fase v6)

Igual patrón que v2-v5 (§11, §12.a, §13.a, §14.a): cada
WP crea su archivo en `apps/api/edecan_api/routers/` exportando `router`;
`edecan_api.main` los monta con `importlib.import_module` +
`try/except ImportError` + `logger.warning` si falta — tolerante a
aterrizajes parciales.

| módulo | prefix | flag | dueño real |
|---|---|---|---|
| `reuniones` | `/v1/reuniones` | `tools.meetings` | fase v6 |
| `analista` | `/v1/analista` | — (sin flag, paridad con `edecan_docanalysis`, que tampoco declara `requires_flags`, ver §14.e) | fase v6 |
| `mcp` | `/v1/mcp` | `tools.mcp` | fase v6 |

`edecan_api.main.V6_ROUTER_NAMES = ("reuniones", "analista", "mcp")`.

Los endpoints de podcasts (`/v1/voz/podcasts*`) **NO** son un router nuevo:
fase v6 los agrega DENTRO del router `voz_avanzada` ya montado por v5
(§14.a) — no aparecen en `V6_ROUTER_NAMES` (ver §15.e).

### 15.b Tablas nuevas — migración `0008_v6_expansion` (responsable de la fase v6)

Mismo patrón que `0001_initial`/`0003_v2_expansion`/`0004_v3_expansion`/
`0006_v4_expansion`/`0007_v5_expansion`: `id UUID PK`, `created_at`/
`updated_at`, `tenant_id UUID NOT NULL` + RLS `tenant_isolation` + índice por
`tenant_id`. `down_revision = "0007_v5_expansion"`. Los modelos SQLAlchemy
correspondientes viven en `edecan_db.models`, sección "v6", con el mismo
estilo (mixins, `_enum_check`) que v1-v5.

- `meetings(user_id, titulo default '', source_file_id nullable,
  transcript_file_id nullable, resumen nullable, minutos nullable, status
  default 'pending', error nullable, duracion_segundos nullable)` — una
  reunión resumida por la tool `resumir_reunion` (flag `tools.meetings`,
  §15.f). `source_file_id`/`transcript_file_id` NO llevan FK a propósito —
  mismo criterio que `voice_consents.consent_file_id` (v5, §14.b): referencia
  informativa opcional a un `File`, no forzada a nivel de base de datos.
  `minutos` (JSONB) guarda la minuta estructurada que produce el job
  `process_meeting` (§15.d); `status` SÍ lleva CHECK
  (`pending|running|done|error`) — el job la mueve de `pending` a `running` y
  luego a `done`/`error`.
- `podcasts(user_id, titulo, guion nullable, status default 'pending',
  file_id nullable, error nullable)` — un podcast creado vía `POST
  /v1/voz/podcasts` (§15.e, flag `tools.podcast`, ya existente desde v5).
  Mismo vocabulario/CHECK de `status` que `meetings`. `file_id` NO lleva FK,
  mismo criterio que `meetings.source_file_id`. A diferencia de
  `meetings.titulo` (`server_default ''`), `podcasts.titulo` es obligatorio
  SIN default — el endpoint que crea la fila exige el título en el request,
  no lo completa perezosamente un job de fondo.

La misma migración también actualiza el `CHECK` de `jobs.type` (creado en
`0001_initial`, ya extendido por `0005_jobs_type_check_v2_types` y
`0007_v5_expansion`) para sumar el 12º job type de §15.d — mismo patrón
`DROP CONSTRAINT` + `ADD CONSTRAINT` que usaron esas dos migraciones
(Postgres no soporta modificar la expresión de un CHECK existente in place).

### 15.c Flags nuevos v6 (pinned en `edecan_schemas.plans`, responsable de la fase v6)

Dos flags booleanos nuevos, espejo EXACTO de la fila `tools.images` (v2) /
`tools.travel`/`voice.cloning`/`tools.podcast` (v5) en los 4 planes:

| flag | free_selfhost | hosted_basic | hosted_pro | hosted_business |
|---|---|---|---|---|
| `tools.meetings` | ✔ | ✖ | ✔ | ✔ |
| `tools.mcp` | ✔ | ✖ | ✔ | ✔ |

### 15.d Job type nuevo `process_meeting` + payload extendido de `generate_podcast` (responsable de la fase v6)

`edecan_schemas.queue.JOB_TYPES` (§10.5) suma un 12º valor, `process_meeting`
(al final, después de los 11 de v1+v2+v5) — job de `resumir_reunion` (§15.f),
responsable de la fase v6. `apps/worker/edecan_worker/handlers` lo registra vía
`_register_defensive(HANDLERS, "process_meeting", "process_meeting")`, mismo
criterio defensivo que `generate_podcast`/`run_mission`/etc. (§11
§11, §14.d). Payload: DOS shapes válidos desde el
arranque, según quién encole el job — `{"meeting_id": "<uuid>"}` (la fila
`meetings` ya existe; la encoló `POST /v1/reuniones` dentro de la MISMA
transacción HTTP corta que su `INSERT`) o `{"file_id": "<uuid>", "titulo":
"<str>", "user_id": "<uuid>"}` (la fila NO existe todavía; la encoló la tool
`resumir_reunion`, §15.f, que deliberadamente nunca escribe en la base de
datos ella misma — evita una carrera de "lee tu propia escritura" contra un
turno de chat que puede seguir corriendo mucho más allá de esa tool antes de
comitear). El handler carga o crea la fila `meetings` según el shape que
reciba, resuelve `source_file_id`/`transcript_file_id` y escribe
`resumen`/`minutos`/`status`/`error`/`duracion_segundos` de vuelta.

`generate_podcast` (job ya existente desde v5, §14.d) gana un SEGUNDO shape
de payload válido: `{"podcast_id": "<uuid>"}` (encolado por `POST
/v1/voz/podcasts`, §15.e) — el handler
(`apps/worker/edecan_worker/handlers/generate_podcast.py`, fuera del alcance
de este WP) debe aceptar AMBOS payloads sin romper el existente: el viejo
`{"titulo", "segmentos", "formato", "user_id"}` (encolado por
`CrearPodcastTool`, `packages/creative/edecan_creative/tools.py`, tool de
chat `crear_podcast`) sigue funcionando tal cual; el nuevo `{"podcast_id"}`
carga la fila `podcasts` (§15.b) para resolver `titulo`/`guion`/`user_id` y
escribe `file_id`/`status`/`error` de vuelta en esa misma fila en vez de
crear un `file` suelto sin fila padre. Dueño real de esta extensión:
fase v6 (mismo WP que agrega los endpoints de §15.e).

### 15.e Endpoints de podcasts dentro de `voz_avanzada` (responsable de la fase v6)

Router `voz_avanzada` (ya montado por v5, prefix `/v1/voz`, §14.a) gana 3
endpoints nuevos:

- `POST /v1/voz/podcasts` (flag `tools.podcast`) — crea una fila `podcasts`
  (§15.b) con `status='pending'` y encola `generate_podcast` con payload
  `{"podcast_id": "<id de la fila recién creada>"}` (§15.d). `201` con la
  fila creada.
- `GET /v1/voz/podcasts` — lista los podcasts del tenant.
- `GET /v1/voz/podcasts/{id}` — un podcast puntual. `404` si no existe o no
  es del tenant.

### 15.f Herramienta nueva pinned: `resumir_reunion` (responsable de la fase v6)

Mismo criterio que §10.14/§11/§13.e/§14.e: nombre fijado desde ya para que
ningún WP en paralelo invente uno distinto.

| tool | dueño real | `requires_flags` | `dangerous` |
|---|---|---|---|
| `resumir_reunion` | fase v6 (`edecan_meetings`) | `{"tools.meetings"}` | `False` |

Vive en un paquete NUEVO `edecan_meetings` (entry point
`[project.entry-points."edecan.tools"] meetings = "edecan_meetings:get_all_tools"`,
mismo criterio que `edecan_travel`/`edecan_ads` en v4/v5). A diferencia de
`POST /v1/reuniones` (§15.a), esta tool NUNCA inserta la fila `meetings`
(§15.b) ella misma — mismo motivo que separa `crear_podcast` de
`generate_podcast` (§15.d): un turno de chat puede seguir corriendo mucho más
allá de esta tool (más tool calls, la respuesta final del modelo) antes de
comitear, así que crear+encolar de inmediato abriría una carrera de "lee tu
propia escritura" contra el worker. En vez de eso valida el archivo y encola
`process_meeting` con payload `{"file_id": "<uuid>", "titulo": "<str>",
"user_id": "<uuid>"}` (§15.d) — el handler crea la fila `meetings` desde cero
como primer paso — sin bloquear el turno de chat esperando el resultado,
igual que `crear_podcast`/`vehiculo_estado` con jobs de fondo. `dangerous=False`:
solo lee/resume contenido que el propio tenant ya subió, no mueve nada real.

**Nota de alcance para fase v6**: a diferencia de `packages/travel`/
`packages/ads`/`packages/vehicles` (v4/v5, §13.h/§14.f), este WP (fase v6)
**NO** reserva el esqueleto de `packages/meetings` en `pyproject.toml` raíz
ni en `apps/api/pyproject.toml`/`apps/worker/pyproject.toml` — el enunciado
de este WP deliberadamente no incluyó `pyproject.toml` entre sus rutas
editables. fase v6 debe crear `packages/meetings/` completo (con su propio
`pyproject.toml`) Y sumarlo a `[tool.uv.workspace].members` del
`pyproject.toml` raíz Y declarar `edecan-meetings` como dependencia de
`apps/api/pyproject.toml`/`apps/worker/pyproject.toml` (mismo motivo
silencioso documentado en §14.f para `edecan-travel`/`edecan-voice`: sin esas
líneas, `uv sync --no-dev --package edecan-api`/`edecan-worker` —los
Dockerfiles de producción— excluirían el paquete en silencio) — nadie más lo
hace por fase v6 esta vez.

### 15.g MCP: conector dinámico por tenant (responsable de la fase v6)

Model Context Protocol como conector bring-your-own — un tenant conecta
SERVIDORES MCP de terceros (o propios) y sus tools quedan disponibles para el
agente. A diferencia del patrón "singular" de §12.b (`llm`/`voice_stt`/
`voice_tts`/`homeassistant`/`whatsapp`: una `connector_account` por
`(tenant_id, connector_key)`), MCP sigue el patrón "múltiple" de OAuth
(§10.8): un tenant puede conectar VARIOS servidores MCP a la vez, cada uno su
propia fila — el `UniqueConstraint(tenant_id, connector_key,
external_account_id)` que `connector_accounts` ya tiene desde v1 (§10.3)
alcanza sin ninguna migración nueva, usando `external_account_id` como el
**slug** que el tenant elige para ese servidor (p. ej. `"notion"`,
`"github"`).

- `connector_accounts` (tabla v1, sin cambios de esquema): una fila por
  servidor MCP conectado, `connector_key="mcp"`, `external_account_id=<slug>`
  elegido por el tenant, `display_name=<nombre legible>`.
- La config no-secreta y los secretos (headers de auth del transporte HTTP,
  etc.) viajan JUNTOS en el mismo blob cifrado: `TokenBundle.access_token`
  (§10.5) guarda un JSON serializado `{nombre, transporte: "http"|"stdio",
  url?, comando?, headers?}` — mismo criterio que `LLMProviderConfig`/`"ads"`/
  `"vehicles"` (§12.c/§13.d): `TokenBundle.token_type = "config"` ("hay que
  `json.loads()` esto"). Cifrado AES-256-GCM vía `TokenVault` (§10.4) igual
  que cualquier otro secreto de tenant — nunca en claro, nunca en logs
  (§2). `connector_accounts` en sí NO lleva ninguna columna con secretos
  (mismo motivo que `"llm"`: la config completa vive SOLO del lado cifrado).
- Tools dinámicas nombradas `mcp_{slug}_{tool}` (`slug` = el
  `external_account_id` de esa `connector_account`, `tool` = el nombre que
  expone el propio servidor MCP) — a diferencia de las tools estáticas de
  `edecan.tools` (registradas una vez al arrancar el proceso, compartidas por
  todos los tenants, §10.7), estas se resuelven POR TENANT en tiempo de
  turno de chat a partir de sus `connector_accounts` con `connector_key="mcp"`
  — el mecanismo exacto de inyección en `Agent.run_turn`/`ToolRegistry`
  queda a criterio de diseño de fase v6, no pinned aquí.
- SIEMPRE `dangerous=True`, sin excepción — a diferencia del resto de tools
  bring-your-own del repo (que declaran `dangerous` según lo que hacen), una
  tool MCP ejecuta código de un servidor de TERCEROS que Edecán no audita ni
  controla; tratarla como peligrosa por defecto es la única postura
  fail-safe posible.
- Transporte `"stdio"` (ejecutar un comando/binario local como subproceso)
  SOLO se permite con `EDECAN_LOCAL_MODE=True` — mismo gate que
  `claude_cli`/`codex_cli`/`ollama` (§12.c/§12.g): en un hosted multi-tenant
  compartido, `"stdio"` significaría ejecutar un comando arbitrario del
  tenant en la máquina del operador, inaceptable. Transporte `"http"` no
  tiene esa restricción (mismo criterio que cualquier llamada HTTP
  bring-your-own del repo).

### 15.h Settings/env nuevos (responsable de la fase v6: `edecan_api.config` + `.env.example`) y Media Streams

Misma convención dura que v2/v3/v5 (§7.5/§12.g/§14): toda tool/router v6 lee
estos campos con `getattr(ctx.settings, "CAMPO", default)`, nunca revienta si
falta uno. v6 es 100% bring-your-own — sin credenciales de PLATAFORMA
nuevas: reuniones/analista/MCP/podcasts se configuran por tenant vía
TokenVault (§15.f/§15.g), no vía `.env`.

`TWILIO_MEDIA_STREAMS_ENABLED` (bool, default `False`) — gatea la ruta
WebSocket `WS /v1/twilio/media` de la extensión comercial externa (interrupciones naturales
durante una llamada real vía Twilio Media Streams, en vez del ciclo
`<Gather>`/`<Say>` síncrono que ya usa `edecan_premium.twilio_router`,
§10.10). Beta, dueño real un WP de telefonía de seguimiento — a diferencia
de otros settings v3 que solo "reservan el nombre y el gate" antes de que
la ruta exista (ver §12.g), acá la ruta YA se construyó completa y probada:
el módulo externo `edecan_premium.media_streams` (VAD por energía, códec μ-law,
`SesionMediaStream` como máquina de estados de la llamada, y el propio
endpoint WS) se trasplanta a `twilio_router.router` de forma incondicional
al importar el módulo (`router.routes.extend(media_streams.media_router.
routes)` — ver docstring de `twilio_router.py`). Prefix nuevo (`/v1/twilio`,
distinto del `/v1/voice/twilio` ya pinned en §10.10) porque es un endpoint
WebSocket de streaming de audio bidireccional, no un webhook TwiML más —
vive en la extensión comercial (mismo criterio que el resto de
telefonía, §6/§10.10). El endpoint WS se monta siempre que `edecan_premium`
esté instalado (mismo guard `find_spec` de siempre, §10.12/`main.py`) — NO
hay un segundo guard de montaje condicionado al flag.

Al montar la extensión, `create_app()` inyecta `app.state.settings`, por lo
que `_media_streams_enabled()` puede leer el flag en runtime.
`app.state.phone_agent` sigue deliberadamente sin configurar: resolver la
identidad de un llamante entrante no autenticado es una decisión de producto
fuera de este contrato. Esa limitación se documenta en
`docs/voz-telefonia.md` ("Interrupciones naturales (beta)" → "Limitación
conocida") y en los docstrings de `twilio_router.py`/`media_streams.py`.

### 15.i Frontend v6 (responsable de la fase v6 para la navegación; páginas en paralelo)

`apps/web/src/components/layout/nav-items.ts` gana dos entradas: `{ href:
"/app/reuniones", label: "Reuniones", icon: VideoIcon }` (junto a Mensajes) y
`{ href: "/app/analista", label: "Analista", icon: ChartBarIcon }` (junto a
Panel) — `VideoIcon`/`ChartBarIcon` nuevos en
`apps/web/src/components/icons.tsx`, mismo estilo SVG inline (stroke, 24x24,
`currentColor`) que el resto. Enlace roto temporal hasta que fase v6/
fase v6 aterricen sus páginas es deuda aceptada, mismo criterio que v2/v4/v5
(§11, §13.i, §14.g). MCP **NO** va en la navegación
principal: vive dentro de `/app/configuracion` (fase v6, §15.g) como una
tarjeta más de conectores, no como una sección propia.

### 15.j Nota de negocio

Reuniones, analista, podcasts HTTP y MCP conservan los principios de
producto resumidos en `docs/roadmap.md`, con el mismo modelo
bring-your-own reforzado (§15.g: incluso los secretos de servidores MCP de
terceros pasan por `TokenVault`, nunca `.env` de plataforma). Los guardrails
no negociables (dinero real nunca se mueve solo, control remoto con
emparejamiento explícito, salud/legal/finanzas informativo, solo APIs
oficiales, LinkedIn prohibido, cero secretos reales, cero comandos git, cero
infraestructura real aplicada, y la exclusión de §13.e y
`docs/vehiculos.md`: cero inversión nueva en
`packages/vehicles`/`routers/vehiculos.py`) siguen sin cambios y aplican con
el mismo peso que `ARCHITECTURE.md` §0. Guardrail nuevo específico de v6: una
tool MCP ejecuta código de un servidor de terceros no auditado por Edecán —
`dangerous=True` sin excepción (§15.g) es la aplicación directa de "control
remoto/acción real exige confirmación humana explícita" a este dominio
nuevo.
