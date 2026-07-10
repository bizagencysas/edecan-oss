# Edecán

**Un software de escritorio descargable e instalable (macOS/Windows)**: descarga, instala, conecta tus propias credenciales en pocos clics y tienes tu **mayordomo de IA personal** corriendo 100% en tu máquina — chat, voz web, telefonía e integraciones reales (correo, calendario, redes sociales vía APIs oficiales, WhatsApp, documentos, finanzas personales, contactos/CRM, recordatorios, investigación web, generación de contenido, IDE embebido y misiones multi-agente), con personalización **"nivel Dios"** — cada usuario define el nombre, tono, personalidad, instrucciones permanentes y memoria/perfil vivo de su propio asistente. El código fuente completo va incluido/disponible por si quieres personalizarlo o auto-hospedarlo, pero eso es un extra: el producto es la app instalada y funcionando.

Modelo **TODO bring-your-own**: usa lo que ya tienes — tu suscripción de Claude Code (Claude CLI), tu cuenta de Codex, Ollama local y gratis, tu propia API key de Anthropic/OpenAI-compatible/Gemini-Vertex, tu Twilio, tu Deepgram/ElevenLabs. Cero lock-in, cero markup sobre consumo de terceros — nunca operamos ni pagamos cuentas de terceros a nombre de un cliente. El núcleo además es abierto y self-hosteable (Apache-2.0) para quien prefiera correrlo con `docker-compose` en vez de instalar la app. Ver `PLAN.md` para la propuesta de valor completa y `ARCHITECTURE.md` para el diseño técnico.

## Reglas duras del proyecto

1. **Cero secretos reales.** Solo placeholders `TU_X_AQUI` en `.env.example` y docs. Nunca datos personales de nadie.
2. **LinkedIn está prohibido** en cualquier forma: código, scopes, URLs, UI o documentación.
3. **Solo APIs oficiales.** Cada tenant conecta sus propias credenciales vía OAuth; nunca scraping ni credenciales compartidas o hardcodeadas.
4. **Infraestructura como código que solo se escribe.** `terraform apply`, `aws` con efectos reales y `docker push` nunca se ejecutan de forma automática desde este repo.
5. UI y documentación por defecto en **español**; tests offline y deterministas.

El detalle completo de estas reglas y de todos los contratos técnicos vinculantes está en [`ARCHITECTURE.md`](./ARCHITECTURE.md) §0 y §10.

## Qué NO hace

Para que las expectativas queden claras desde el primer vistazo:

- **No integra LinkedIn** de ninguna forma — ni código, ni scopes OAuth, ni URLs, ni texto de UI o documentación. Es una prohibición permanente (ver `ARCHITECTURE.md` §0, punto 2) reforzada por un test (`test_no_linkedin`) que falla si la palabra aparece en `packages/connectors/`.
- **No hace scraping.** Toda integración con Google, Microsoft, Meta, X o YouTube usa sus APIs oficiales vía OAuth 2.0. Cada tenant autoriza su propia cuenta; nunca se comparten ni se hardcodean credenciales.
- **No llama ni envía SMS sin permiso.** El motor de cumplimiento de `premium/` exige consentimiento registrado, respeta la ventana horaria 08:00–21:00 local del destinatario, ofrece opt-out automático ("STOP"/"BAJA") y siempre se identifica como asistente automatizado.
- **No aplica infraestructura por sí solo.** El código de `infra/terraform` se escribe y se revisa como cualquier otro código; `terraform apply` es siempre un paso manual fuera de este repositorio, nunca algo que un agente o un pipeline de este repo ejecute automáticamente.
- **No guarda secretos reales.** Solo hay placeholders `TU_X_AQUI` en `.env.example` y en la documentación; las credenciales de cada tenant viven cifradas en el TokenVault (AES-256-GCM, envuelto con KMS o Fernet local).

## Estructura del repositorio

```
asistente/
├── README.md
├── ARCHITECTURE.md              # contrato técnico
├── PLAN.md                      # plan de producto y negocio
├── RIESGOS.md                   # registro de riesgos
├── LICENSE                      # Apache-2.0 (core)
├── NOTICE                       # aclara que premium/ es propietario
├── SECURITY.md
├── CONTRIBUTING.md
├── .env.example
├── .gitignore
├── .editorconfig
├── Makefile
├── pyproject.toml               # raíz del workspace uv (Python 3.12)
├── docker-compose.yml           # deps dev: postgres+pgvector, redis, localstack
├── apps/
│   ├── api/                     # FastAPI (edecan_api) — auth, chat SSE, conectores, voz, billing
│   ├── worker/                  # consumidor SQS + handlers de jobs (edecan_worker)
│   ├── web/                     # Next.js 14 + TS + Tailwind (chat, persona, panel, conectores)
│   └── companion/                # agente local de escritorio opt-in (edecan_companion)
├── packages/
│   ├── schemas/                 # edecan_schemas — contratos Pydantic + planes/flags
│   ├── db/                      # edecan_db — SQLAlchemy + Alembic + RLS + TokenVault
│   ├── llm/                     # edecan_llm — Anthropic primario, OpenAI-compat, Bedrock, router
│   ├── core/                    # edecan_core — agente, registry de tools, persona, memoria/grafo
│   ├── toolkit/                 # edecan_toolkit — agenda, finanzas, CRM, docs, research, contenido
│   ├── connectors/               # edecan_connectors — Google, Microsoft + social/ (Meta, X, YouTube)
│   ├── voice/                   # edecan_voice — STT/TTS intercambiables + stubs offline
│   └── evals/                   # edecan_evals — suites de evaluación del agente
├── prompts/                     # plantillas de system prompt versionadas
├── premium/                     # edecan_premium — telefonía Twilio, campañas, cuotas (lic. comercial)
├── infra/
│   ├── terraform/                # módulos AWS (VPC, ECS, RDS, SQS, S3, KMS…) — NUNCA aplicar
│   └── docker/                  # Dockerfiles api/worker/web + compose.selfhost.yml
└── docs/                        # self-hosting, conectores, cumplimiento, runbooks
```

*(desde v3)* Este árbol no es exhaustivo: sumó `apps/desktop/` (`src-tauri/` — empaqueta `apps/web/` con Tauri como la app de escritorio instalable, macOS/Windows), `apps/local/` (`edecan_local` — corre `api`+`worker`+`db` local en la máquina del cliente como backend de esa app) y `apps/mobile/` (proyectos nativos iOS/Android — ver [`docs/movil-ios.md`](./docs/movil-ios.md)/[`docs/movil-android.md`](./docs/movil-android.md)); `packages/` también creció bastante en las olas v2-v6 (analista/docanalysis, navegador, creatividad, mensajería, agentes, automatizaciones, comercio, asesores, negocio/RRHH, skills, casa inteligente, ads, vehículos, viajes, reuniones, MCP y más) — ver `ARCHITECTURE.md` §11-§15 y [`docs/index.md`](./docs/index.md) para el mapa completo. Ver `DIRECCION_ACTUAL.md` y [`docs/desktop.md`](./docs/desktop.md) para la app de escritorio en sí.

## Arquitectura

Flujo de referencia de una conversación (detalle completo en `ARCHITECTURE.md` §7 y §9):

```
   Web (Next.js) · Companion escritorio · Teléfono (Twilio, premium*)
                              │  HTTPS / SSE / WebSocket
                              ▼
                    apps/api (FastAPI, :8000)
                    Agent.run_turn — loop de herramientas (máx. 8 iteraciones)
              ┌───────────┼────────────┬──────────────┐
              ▼           ▼            ▼              ▼
        Postgres 16    Redis        LLMRouter      TokenVault
        + pgvector     caché ·      Anthropic      credenciales
        (RLS por       rate-limit · (primario) ·   cifradas por
        tenant)        pairing ·    OpenAI-compat · tenant (AES-256-GCM,
                       confirms     Bedrock (stub)  KMS o Fernet local)
              ▲
              │                     SQS edecan-jobs (+ edecan-jobs-dlq)
              │                               │
              │                               ▼
              └───────────────────  apps/worker
                                     ingest_file · sync_connector ·
                                     send_reminder(_scan) · run_campaign_step
                                     (premium) · memory_consolidate ·
                                     generate_content — reintentos con backoff
                                     (2^intento·30s, hasta 5, luego DLQ)
                                               │
                                               ▼
                                     S3 edecan-files (prefijo por tenant)
```

`*` La telefonía solo se activa si el tenant conecta su propia cuenta Twilio (paquete `premium/`, flag de plan `voice.telephony`); el núcleo funciona completo sin ella.

*(desde v3)* En la app de escritorio este mismo backend corre empaquetado y local en la máquina del cliente, con Postgres embebido — ver `DIRECCION_ACTUAL.md` y [`docs/desktop-local.md`](./docs/desktop-local.md).

## Núcleo gratuito vs. `premium/` (paquete con licencia comercial)

- **Core (`Apache-2.0`)**: todo el repo salvo `premium/`. Chat, agente con herramientas, memoria/grafo, todo el ecosistema v2 (misiones, automatizaciones, IDE embebido, analista, navegador, negocios, asesores), conectores de Google/Microsoft, sociales con tus propias apps OAuth, voz web con tus propias claves STT/TTS y companion de escritorio. Se instala con la app de escritorio o con `docker-compose`, siempre con tus propias API keys; soporte comunitario.
- **`premium/` (licencia comercial, ver [`NOTICE`](./NOTICE))**: telefonía Twilio por tenant, campañas de voz/SMS con motor de cumplimiento, cuotas de plan y herramientas premium. La API lo monta en runtime solo si el paquete `edecan_premium` está instalado, y cada capacidad queda además controlada por los flags del plan del tenant (ver `ARCHITECTURE.md` §10.13).

## Cómo empezar

Tres caminos, de más simple a más manual — detalle completo del modelo de negocio detrás de cada uno en [`PLAN.md`](./PLAN.md):

1. **App de escritorio (recomendado)** — descarga el instalador de macOS/Windows y sigue el wizard de bienvenida (2–3 pasos: conectar un proveedor LLM y listo). Detecta automáticamente si ya tienes `claude`/`codex`/Ollama instalados y te ofrece usarlos con un clic, sin pedir ninguna API key. Ver [`docs/primeros-pasos.md`](./docs/primeros-pasos.md) (recorrido completo del wizard) y [`docs/proveedores-llm.md`](./docs/proveedores-llm.md) (cuál proveedor LLM elegir); [`docs/desktop.md`](./docs/desktop.md) cubre además el empaquetado/instalación en sí.
2. **Self-host con Docker Compose** — todo el stack (`api`, `worker`, `web` + Postgres/Redis) en contenedores sobre tu propio servidor (VPS, NAS, mini-PC). Guía completa en [`docs/self-hosting.md`](./docs/self-hosting.md) §3.
3. **Modo desarrollador** — cada servicio corriendo directo en tu máquina con `make`, para quien va a modificar código. Detallado justo abajo.

## Modo desarrollador (self-host desde el código fuente)

Requisitos: Docker y Docker Compose, Python 3.12 con [`uv`](https://docs.astral.sh/uv/), Node.js 20+.

1. **Configura tus credenciales.**
   ```bash
   cp .env.example .env
   # Edita .env y coloca TUS propias API keys (LLM, voz, OAuth de cada conector, etc.)
   # Nunca compartas ni subas tu .env real.
   ```
2. **Levanta las dependencias locales** (Postgres+pgvector, Redis, LocalStack para S3/SQS):
   ```bash
   make deps
   ```
3. **Corre las migraciones:**
   ```bash
   make db-migrate
   ```
4. **Arranca cada servicio** (en terminales separadas):
   ```bash
   make api      # FastAPI en :8000
   make worker   # consumidor de jobs
   make web      # Next.js en :3000
   ```
5. Abre `http://localhost:3000`, crea tu tenant y configura la persona de tu asistente ("nivel Dios": nombre, tono, instrucciones, memoria).

El plan `free_selfhost` (ver `ARCHITECTURE.md` §10.13) no tiene límites de mensajes ni de voz web, e incluye conectores sociales con tus propias apps OAuth. La telefonía (`premium/`) requiere instalar ese paquete por separado y no es necesaria para usar el núcleo.

Comandos útiles adicionales: `make test` (pytest offline, sin red real) y `make lint` (ruff).

> **Cuidado con `uv sync`/`uv run` sueltos.** El `pyproject.toml` raíz declara el workspace pero no tiene `dependencies` propias, así que correr `uv sync` o `uv run <comando>` directo (sin pasar por `make`, sin `--all-packages`) **desinstala en silencio** los paquetes editables del workspace (28 hoy — `edecan_core`, `edecan_agents`, etc., ver `[tool.uv.workspace].members` en `pyproject.toml`) — el entorno queda reducido al cierre vacío de la raíz. El síntoma es `ModuleNotFoundError: No module named 'edecan_core'` (o similar) al correr pytest; ojo que `uv run ruff check .` sigue funcionando en ese mismo estado roto porque ruff no necesita los paquetes instalados, así que no lo notarás por ahí. Los targets de `make` (`test`, `lint`, `fmt`, `api`, `worker`, etc.) ya pasan `--all-packages` por vos y no sufren esto — pero si corrés `uv` directo por costumbre, usa siempre `uv sync --all-packages` o `uv run --all-packages <comando>`. Si ya te pasó, `uv sync --all-packages` reinstala todo y arregla el entorno. Los dos scripts fuera de `make` que también invocan `uv run` (`scripts/instalar-selfhost.sh` y `apps/desktop/scripts/dev.sh`) ya pasan `--all-packages` desde v6.

## Planes y flags (referencia técnica)

Los flags/límites por `plan_key` de abajo siguen viviendo en el código (`edecan_schemas.plans`, `ARCHITECTURE.md` §10.13) y siguen gateando funcionalidad, pero ya no representan una oferta hospedada activa: el modelo de negocio vigente es la app de escritorio con licencia, el self-host gratuito y "Tu propia nube" cotizada (detalle completo en [`PLAN.md`](./PLAN.md)) — el hosted multi-tenant compartido (`hosted_basic`/`hosted_pro`/`hosted_business`) queda **pospuesto** hasta que haya demanda real. Esta tabla sirve hoy sobre todo si self-hosteas y quieres simular otro tier a mano:

| Plan | Voz web | Sociales | Telefonía | Campañas | Mensajes/día | Min. voz/mes | Almacenamiento | Números | Asientos | Modelos premium |
|---|---|---|---|---|---|---|---|---|---|---|
| `free_selfhost` (core) | ✔ | ✔ | ✖ | ✖ | ilimitado | ilimitado | ilimitado | 0 | 1 | ✖ |
| `hosted_basic` | ✔ | ✖ | ✖ | ✖ | 150 | 60 | 1 GB | 0 | 1 | ✖ |
| `hosted_pro` | ✔ | ✔ | ✔ | ✖ | 600 | 300 | 10 GB | 1 | 1 | ✔ |
| `hosted_business` | ✔ | ✔ | ✔ | ✔ | 2,000 | 1,000 | 50 GB | 3 | 5 | ✔ |

El flag `companion` (agente local de escritorio) está disponible en los cuatro planes. `-1`/"ilimitado" en `free_selfhost` refleja que el self-host no está limitado por la plataforma — solo por tu propia infraestructura y tus propias API keys.

Detalle completo de precios (licencia de escritorio, soporte, "Tu propia nube"), mercado y estrategia de lanzamiento en [`PLAN.md`](./PLAN.md).

## Documentación adicional

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — arquitectura técnica y contratos obligatorios entre paquetes.
- [`PLAN.md`](./PLAN.md) — propuesta de valor, clientes, competencia, precios y go-to-market.
- [`RIESGOS.md`](./RIESGOS.md) — registro de riesgos técnicos, legales, de producto y de seguridad.
- [`SECURITY.md`](./SECURITY.md) — política de seguridad y reporte de vulnerabilidades.
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — convenciones de código y flujo de contribución.
- [`docs/index.md`](./docs/index.md) — mapa completo de la documentación extendida (self-hosting, API, conectores, cumplimiento, runbooks, ecosistema v2-v6).

Documentación de la ola v3 (escritorio, credenciales bring-your-own, proveedores LLM, marketplace de skills, apps móviles, casa inteligente), ya aterrizada — enlazada desde [`docs/index.md`](./docs/index.md): [`docs/primeros-pasos.md`](./docs/primeros-pasos.md), [`docs/proveedores-llm.md`](./docs/proveedores-llm.md), [`docs/credenciales.md`](./docs/credenciales.md), [`docs/casa-inteligente.md`](./docs/casa-inteligente.md), [`docs/desktop.md`](./docs/desktop.md), [`docs/desktop-local.md`](./docs/desktop-local.md), [`docs/skills.md`](./docs/skills.md), [`docs/movil-ios.md`](./docs/movil-ios.md) y [`docs/movil-android.md`](./docs/movil-android.md). Las olas v4-v6 (inventario/ads/vehículos/mensajería/dispositivos, RRHH/viajes/voz avanzada, reuniones/analista/MCP y el resto del ecosistema de agentes) tienen su propio mapa en la sección "v4-v6" de [`docs/index.md`](./docs/index.md) — algunos enlaces de esa sección pueden dar 404 hasta que su WP dueño aterrice, mismo criterio de montaje defensivo.

## Licencia

El núcleo del repositorio se distribuye bajo **Apache License 2.0** (ver [`LICENSE`](./LICENSE)). El directorio `premium/` es software propietario bajo licencia comercial y **no** está cubierto por la Apache License (ver [`NOTICE`](./NOTICE)).
