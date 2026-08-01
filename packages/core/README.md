# packages/core — `edecan_core`

Motor del agente (`ARCHITECTURE.md` §9, §10.7):

- `Tool` (ABC) + `ToolContext` + `ToolResult` (`edecan_core.tools`) — contrato que implementa cada herramienta del toolkit (`edecan_toolkit`) y que pueden reutilizar extensiones externas.
- `ToolRegistry` — registro de herramientas: `register()` rechaza cualquier tool cuyo `name`/`description` mencione la red social vetada (ARCHITECTURE.md §0.2); `specs(flags)` filtra por `requires_flags` y devuelve `edecan_schemas.ToolSpec`; `load_entry_points(group="edecan.tools")` descubre herramientas de otros paquetes.
- `edecan_core.persona.build_system_prompt(persona, memories, extra_context=None)` — arma el system prompt (español por defecto, inglés si `persona.idioma == "en"`) a partir de `PersonaConfig`: identidad, tono, trato tú↔usted según `formalidad` (0-3), emojis, rasgos y memorias; las `instrucciones` del usuario van en una sección delimitada con advertencia explícita de que nunca anulan las reglas de seguridad ni autorizan exfiltrar datos de otros tenants.
- `Agent.run_turn(*, ctx, persona, history, user_text, flags)` — loop de tool-use (máx. `MAX_TOOL_ITERATIONS` = 8) que emite `AgentEvent` (`text_delta`, `tool_start`, `tool_end`, `confirmation_required`, `done`, `error`); las herramientas `dangerous` sin `tool_call_id` en `ctx.extras["approved_tool_calls"]` detienen el turno pidiendo confirmación. No depende de `edecan_llm`: `llm_router` se trata como duck type (ver `edecan_core.llm_types`).
- `edecan_core.memory` (subpaquete aparte, no re-exportado en `edecan_core/__init__.py`): protocolos `MemoryStore`/`Embedder` + `MemoryHit`; `HashEmbedder` (determinista, offline, dim `EMBEDDINGS_DIM`) y `OpenAICompatEmbedder` (`POST {base}/embeddings`); `PgMemoryStore` (pgvector, `ORDER BY embedding <=> :q`, con fallback `ILIKE` si no hay `Embedder`); grafo `add_edge`/`neighbors` sobre `memory_edges`.
- `edecan_core.queue.enqueue(settings, job_type, payload, tenant_id)` — encola un `JobEnvelope` en `SQS_QUEUE_URL` vía `aioboto3`.
- `edecan_core.safety.redact(text)` — enmascara secretos evidentes (`sk-...`, `Bearer ...`, etc.) antes de loguear.

## Dependencias y acoplamiento

Dependencia dura de paquete hermano: solo `edecan_schemas` (los contratos de eventos/tools/persona/jobs, ARCHITECTURE.md §10.5). `edecan_core` NO depende de `edecan_llm` ni de `edecan_db`:

- `ToolContext.session`/`.settings`/`.llm`/`.vault` son `Any` — cada `Tool` concreta sabe con qué tipo real está tratando.
- `Agent` habla con `llm_router` por duck typing (`llm_types.ChatMessage`/`CompletionRequest`, misma forma que `edecan_llm.base` pero sin importarlo).
- `edecan_core.memory.pg`/`.graph` usan `sqlalchemy.text()` con un import diferido (`memory/_sql.py`): si `sqlalchemy` está instalada (siempre lo está en el proceso real, vía `edecan_db`/`apps/api`) lo usan correctamente contra una `AsyncSession` real; si no, pasan el SQL como `str` plano.

## Tests

`tests/` no importa paquetes hermanos reales de runtime (solo `edecan_schemas`, que sí es dependencia declarada): usa `FakeLLMRouter`/`FakeProvider` (scripteados), `FakeTool` y sesiones/SQS falsas locales a cada archivo de test.

Este paquete es consumido por `apps/api` (turnos de chat vía SSE) y por `apps/worker` (job `memory_consolidate`, entre otros).
