# apps/worker — `edecan_worker`

Worker asíncrono que consume la cola SQS (`SQS_QUEUE_URL`, en dev LocalStack `edecan-jobs`) y ejecuta el handler registrado en `edecan_worker.handlers.HANDLERS: dict[str, Handler]` según el `type` de cada `JobEnvelope` (`edecan_schemas.queue`).

Se conecta a Postgres como **owner** de las tablas (`edecan_db.session.get_session(None)`), lo que **bypassa Row-Level Security** — por eso cada handler filtra manualmente por el `tenant_id` del job en cada consulta (`ARCHITECTURE.md` §2). Las únicas excepciones deliberadas son los barridos de sistema sin tenant propio: `send_reminder_scan` (siempre) y `sync_connector` (cuando se dispara sin `tenant_id`).

## Tipos de job (`edecan_schemas.JOB_TYPES`, ARCHITECTURE.md §10.5/§10.11)

| `type` | Payload | Qué hace |
|---|---|---|
| `ingest_file` | `{"file_id": "<uuid>"}` | Descarga el archivo de S3, extrae texto (`pdf`→`pypdf`, `docx`→`python-docx`, `txt`/`md` directo), lo trocea (1200 chars, solape 200), calcula embeddings por lotes de 32 y guarda `file_chunks`. `files.status` pasa a `ready` (o `error` si el mime no está soportado) y registra `usage_events` `kind=storage_bytes`. |
| `sync_connector` | `{}` (o acotado por `tenant_id` del job) | Refresca en el `TokenVault` los `oauth_tokens` que expiran en menos de 10 minutos, vía `edecan_connectors.registry.CONNECTORS[connector_key].refresh`. Un fallo puntual no detiene el resto. |
| `send_reminder` | `{"reminder_id": "<uuid>"}` | Marca el recordatorio `sent` e inserta un mensaje `assistant` en la conversación "Recordatorios" del usuario (la crea si no existe, `channel="api"`). |
| `send_reminder_scan` | `{}` | Barrido global (sin `tenant_id`): busca `reminders` con `status='pending'` y `due_at <= now()`, y encola `send_reminder` por cada uno con su propio `tenant_id`. |
| `run_campaign_step` | definido por `edecan_premium` | Si `edecan_premium` está instalado, delega en `edecan_premium.campaigns.handle(env, deps)`; si no, loggea "premium no instalado" y termina sin error (core self-host no trae campañas). |
| `generate_content` | `{"conversation_id": "<uuid>", "brief": "<texto>"}` | Llama al router LLM (alias `"principal"`, degradado a `"rapido"` si el plan no tiene `models.premium`) y guarda el resultado como mensaje `assistant`; registra `usage_events` `kind=llm_tokens`. |
| `memory_consolidate` | `{"user_id": "<uuid>"}` | Agrupa `memory_items` del usuario con similitud coseno > 0.92 (producto punto puro-Python sobre embeddings normalizados, sin `numpy`) y funde cada grupo: conserva el ítem más antiguo, actualiza su `importance` al máximo del grupo y borra los demás. |

## Reintentos y DLQ (ARCHITECTURE.md §10.11)

Si un handler lanza una excepción:

- `attempt < 5` → se re-encola un mensaje nuevo con `attempt + 1` y `DelaySeconds = min(900, 2**attempt * 30)` segundos, y se borra el mensaje original.
- `attempt >= 5` (ya se agotaron los 5 reintentos) → el mensaje NO se borra: vuelve a quedar visible tras su *visibility timeout* y la política de *redrive* de la cola (configurada en infra — Terraform, fuera de este paquete) termina moviéndolo a la DLQ `edecan-jobs-dlq`.

Un mensaje SQS inválido (no parsea como `JobEnvelope`) o con un `type` sin handler registrado se borra directamente, sin reintentar.

## Estructura

```
edecan_worker/
  config.py     — Settings (pydantic-settings, ARCHITECTURE.md §10.2)
  repo.py       — Repo (Protocol) + SqlRepo: acceso a datos con SQL parametrizado
  deps.py       — Deps (dataclass) + build_deps(settings): recursos compartidos
  main.py       — python -m edecan_worker.main: loop principal de consumo de SQS
  scheduler.py  — python -m edecan_worker.scheduler: encola send_reminder_scan en dev
  handlers/     — un módulo por tipo de job + HANDLERS: dict[str, Handler]
```

`Deps` agrupa lo que necesita cada handler: `session_factory` (=`edecan_db.session.get_session`), clientes `aioboto3` de `s3`/`sqs` (con `endpoint_url=AWS_ENDPOINT_URL` si está definido, p. ej. LocalStack en dev), `embedder` (`OpenAICompatEmbedder` si hay `OPENAI_COMPAT_BASE_URL`, si no `HashEmbedder`, ambos de `edecan_core`), `llm_router` (`edecan_llm.router.LLMRouter`) y `vault` (factory `(session) -> TokenVault` de `edecan_db.vault`).

**Nota sobre paquetes hermanos en construcción**: `edecan_core` y, en este momento del desarrollo, `edecan_db.vault` pueden no existir todavía en este workspace (se construyen en paralelo, ver `ARCHITECTURE.md` §10.1). Este paquete los importa por nombre de módulo dentro de las funciones que los necesitan (nunca al tope de un módulo), para que `edecan_worker` se pueda importar, y sus handlers testear con fakes, sin que esos paquetes existan todavía.

## Scheduler de `send_reminder_scan`

- **Dev / self-host**: `python -m edecan_worker.scheduler` encola `{"type": "send_reminder_scan"}` cada 30 segundos.
- **Producción (AWS)**: este módulo NO se despliega. El mismo trabajo lo hace **EventBridge Scheduler**, enviando directamente a `SQS_QUEUE_URL` el mensaje `{"type": "send_reminder_scan", ...}` cada minuto (`ARCHITECTURE.md` §7).

## Correr localmente

```bash
make deps      # postgres+pgvector, redis, localstack (ARCHITECTURE.md §8)
make db-migrate
make worker            # python -m edecan_worker.main
make worker-scheduler  # python -m edecan_worker.scheduler (dev/self-host, encola send_reminder_scan cada 30s)
```

## Tests

```bash
uv run pytest apps/worker
```

Offline y deterministas (`ARCHITECTURE.md` §10.1/§10.15): `tests/fakes.py` provee `FakeRepo`, `FakeS3`, `FakeSQS`, `FakeEmbedder`, `FakeLLMRouter` y `FakeVault` en memoria — no se importan paquetes hermanos ni se abre conexión real a Postgres/SQS/S3. Cada test de handler monkeypatchea el nombre `SqlRepo` importado en el módulo del handler bajo prueba por un `FakeRepo`.
