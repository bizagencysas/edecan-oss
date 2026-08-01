# packages/toolkit — `edecan_toolkit`

Herramientas de dominio del agente que no son premium: recordatorios, agenda,
correo, contactos/CRM, finanzas personales, documentos, investigación web,
generación/publicación de contenido y control de la computadora local. Cada
una implementa el contrato `Tool` de `edecan_core` (§10.7): `name`,
`description`, `input_schema`, `requires_flags`, `dangerous` y `async run(ctx, args)`.

`get_all_tools() -> list[Tool]` (en `edecan_toolkit/__init__.py`) es el entry
point que consume `edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`,
declarado en `pyproject.toml` como `[project.entry-points."edecan.tools"]`.

## Herramientas principales

| Módulo | Tools |
|---|---|
| `recordatorios.py` | `crear_recordatorio`, `listar_recordatorios` |
| `agenda.py` | `agenda_eventos`, `crear_evento` |
| `correo.py` | `buscar_correo`, `enviar_correo` (dangerous) |
| `contactos.py` | `buscar_contactos`, `gestionar_contacto` |
| `finanzas.py` | `registrar_transaccion`, `resumen_finanzas` |
| `documentos.py` | `consultar_documentos` |
| `research.py` | `buscar_web` |
| `contenido.py` | `generar_contenido`, `publicar_social` (dangerous, flag `connectors.social`) |
| `computadora.py` | `usar_computadora` (dangerous, flag `companion`) |
| `codigo_local.py` | `acceder_codigo_local` (dangerous, solo modo local) |
| `autorreparacion.py` | diagnóstico + reparación Git aislada (dangerous para cambios) |
| `seguridad.py` | auditoría estática local + PentestGPT autorizado (dangerous para ejecución activa) |
| `creator.py` | `crear_artefactos`: creación compuesta de Markdown, DOCX, PDF, PPTX, web y apps con manifest |
| `utilidades.py` | `hora_actual`, `calculadora` |

`_conectores.py` y `_util.py` son helpers privados compartidos (no forman
parte del contrato público del paquete).

## Decisiones de implementación

- **Acceso a datos**: todas las consultas usan `sqlalchemy.text()` sobre
  `ctx.session` contra los nombres de tabla/columna pinned en `ARCHITECTURE.md`
  §10.3 (`reminders`, `contacts`, `transactions`, `files`/`file_chunks`,
  `connector_accounts`). No se importa `edecan_db.models`: esa forma interna
  (ORM vs. `Table` de SQLAlchemy Core) no está fijada por el contrato, mientras
  que los nombres de tabla/columna sí — acoplarse a ella habría sido adivinar
  la forma de un paquete hermano que, al momento de escribir este paquete,
  todavía no existe.
- **`edecan_llm` como dependencia añadida**: `generar_contenido` necesita
  invocar de verdad `ctx.llm.complete("principal", tenant_flags, req)` (alias
  `"principal"`, §10.6), lo que requiere los tipos `ChatMessage`/`CompletionRequest`
  de `edecan_llm.base`. Se añadió `edecan-llm` a `pyproject.toml` aunque no
  estaba en la lista original de dependencias del paquete, porque sin él la
  tool no podría redactar contenido de verdad.
- **`publicar_social`**: valida la red contra `edecan_connectors.registry.CONNECTORS`
  y una lista fija `("meta", "x", "youtube")`; cualquier otra red se rechaza
  con un mensaje claro. Esas tres son, y seguirán siendo, las únicas
  integraciones sociales de la plataforma (`ARCHITECTURE.md` §0.2 y §5).
- **`consultar_documentos`**: usa `ctx.extras["memory_embedder"]` (protocolo
  `Embedder` de `edecan_core`) + distancia coseno de pgvector (`<=>`) cuando
  está disponible; si no, cae a una búsqueda `ILIKE` — sigue siendo útil en
  self-host sin `EMBEDDINGS_MODEL` configurado.
- **`edecan_toolkit.research`**: `SearchProvider` — protocolo
  `async search(query, k=5) -> list[SearchHit]`; implementaciones `BraveSearch`,
  `TavilySearch`, `StubSearch` según `SEARCH_PROVIDER`, resueltas por
  `get_search_provider(settings)`.
- **`publicar_social` y `usar_computadora`** requieren flags (`connectors.social`
  y `companion`, respectivamente), son `dangerous=True` y pasan siempre por
  APIs oficiales o por el companion local — nunca scraping.

## Tests

`tests/` usa fakes locales por duck typing (`FakeSession`, `FakeVault`,
`FakeLLM`, `ctx` como `SimpleNamespace`) y `respx` para las llamadas HTTP de
los conectores — offline y deterministas (§10.15). No importan `edecan_db` ni
`edecan_llm` para construir sus dobles.
