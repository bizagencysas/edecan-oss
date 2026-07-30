# edecan-forge-kernel — el núcleo del runtime de ingeniería de Forge

Sustrato de ejecución para agentes (invariante 1: "no es un editor"). Este paquete es el núcleo
confiable de la fase 1: los tipos que unifican doce contradicciones de diseño
(`docs/arquitectura-forge.md` §13), el reducer puro que decide qué pasa cuando un agente pide
algo, el journal durable que es la única fuente de verdad (invariante 2), el almacén
direccionado por contenido que evita que el journal cargue payloads pesados (invariante 4), y el
bus de eventos con sus dos canales estrictamente separados (durable vs. efímero).

Nada de este paquete llama a la red. Sin dependencias fuera de `pydantic` y la stdlib
(`sqlite3`, `hashlib`, `json`) — regla dura del encargo que lo escribió.

## Qué garantiza

- **El journal es la única fuente de verdad** (`SqliteJournal`, invariante 2). Cadena de hashes
  verificable por stream (`hash = blake2b(json_canónico(evento_sin_hash) || prev_hash)`),
  contigüidad de `seq` bajo contención (CAS optimista por `expected_seq`), fencing preventivo de
  `lease_epoch` (`FencedOut`), y `append_if` con una guardia evaluada contra una proyección
  nombrada dentro de la MISMA transacción SQL — un rechazo nunca deja huella.
- **Contenido direccionado por hash, siempre** (invariante 4). Un único tipo, `CasRef`
  (`"b2b:<64 hex>"`), usado IDÉNTICO por `contracts.py`, `journal.py`, `cas.py` y `bus.py` — el
  sello de un stream efímero (`EphemeralStreamSeal.content_ref`) y lo que produce `Cas.poner()`
  sobre los mismos bytes son, byte a byte, la misma referencia. `demo_sesion.py` lo comprueba en
  vivo, no lo afirma en un docstring.
- **El reducer es puro, síncrono y total** (`reduce(state, cmd) -> Decision`). Nunca lanza para
  control de flujo — un comando inadmisible produce `Decision.rejection`, nunca una excepción.
  Mismo `state` + mismo `Command` (incluido su `Stamp`) ⇒ misma `Decision`, byte a byte: no hay
  reloj, aleatoriedad ni I/O en el camino. `demo_sesion.py` lo demuestra reejecutando la misma
  secuencia de comandos desde `KernelState()` vacío tras "matar" el proceso, y comparando el
  resultado con el estado que había en vivo.
- **Dos canales estrictamente separados** (`EventBus`, §1.4). `publish()` es el ÚNICO camino
  durable — delega íntegro en el `Journal` inyectado. `emit()` es efímero y JAMÁS toca el
  journal: se puede verificar leyendo el cuerpo del método, sin ejecutar nada, que no hay
  ninguna referencia a `self._journal` ahí. Un stream efímero nace anclado a un evento durable
  ya publicado y se cierra con un sello (`CasRef` + estadísticas) — el contenido acumulado nunca
  reside en el bus, solo un hash incremental.
- **Máquina de estados de llamada a herramienta única y completa** (`ToolCallState` +
  `AdmissionSubstate`), con las transiciones legales e ilegales verificadas en
  `validate_tool_call_transition` / `validate_admission_substate_transition` — la única
  excepción que `reduce()` puede lanzar de verdad (bug de programa, no resultado de negocio).
- **Redacción legal sin reescribir historia.** `journal.redact()` destruye el contenido de un
  blob y deja una lápida de auditoría, sin tocar jamás la tabla `events`: la cadena de hashes de
  cualquier stream que lo citó sigue verificando exactamente igual después de redactar, porque
  el hash de un evento es del `CasRef` (el identificador del contenido), nunca una promesa de
  que el contenido sobreviva.
- **Almacén de contenido con escritura atómica y deduplicación real** (`Cas`). Nunca es visible
  un blob a medias (staging + `fsync` + `os.replace`); el mismo contenido nunca se escribe dos
  veces; `gc()` es marca-y-barre sobre el disco real (no sobre la tabla de metadatos), para
  autocurarse si un crash deja un blob huérfano.

## Los módulos

| Módulo | Qué es | Qué NO es |
| --- | --- | --- |
| `contracts.py` | Los tipos (`Actor`, `Stamp`, `Command`, `Effect`, `EventDraft`, `Event`, `Decision`, `KernelState`) y el reducer puro `reduce()`. El `SchemaRegistry` cerrado con el namespace de 13 dominios (84 tipos declarados, 11 activos). | No hace I/O. No conoce SQLite ni el filesystem. |
| `journal.py` | `SqliteJournal` — el journal durable REAL sobre SQLite en modo WAL. | No es async (ver "qué no hace todavía"). |
| `cas.py` | `Cas` — el almacén de contenido REAL sobre el filesystem, con metadatos en SQLite. | No es el mismo almacén de blobs que usa `journal.redact()` (ver más abajo). |
| `bus.py` | `EventBus` — el punto de entrada único de los dos canales, `PatternTrie` para suscripción por patrón, `BandwidthMeter` con histéresis de backpressure. | No decide la cadena de hashes (eso es del `Journal` que recibe inyectado). |
| `projections.py` | `Projection` (ABC con idempotencia por `(stream_id, seq)` centralizada) y dos proyecciones reales: `SessionTimelineProjection`, `BudgetLedgerProjection`. | No reconstruye el `KernelState` completo desde eventos todavía (ver más abajo). |
| `demo_sesion.py` | Orquestación de un host de juguete que ejercita las cuatro piezas juntas. | No es API pública del paquete. |

Todos importan `CasRef` (y el resto de tipos que comparten) del mismo sitio:
`edecan_forge_kernel.contracts`. Nadie se inventó un tipo paralelo — es exactamente lo que este
paquete existe para impedir (ver "Coherencia verificada" más abajo).

## Coherencia verificada entre las partes

Cuatro sesiones distintas escribieron `contracts.py`, `journal.py`, `cas.py` y `bus.py`/
`projections.py` en paralelo. Al unificarlas se encontró y corrigió una incompatibilidad real de
firma, no solo de "mismo tipo":

- **`SqliteJournal.append`/`append_if` no tenían la misma firma que el `Journal` Protocol de
  `contracts.py`** contra el que `EventBus.publish` llama
  (`journal.append(drafts, *, stream_id=..., ...)`, `drafts` posicional). `SqliteJournal` los
  exponía con `stream_id` posicional primero y `drafts` segundo — una llamada estructuralmente
  correcta contra el Protocol habría fallado en tiempo de ejecución
  (`TypeError: got multiple values for argument 'stream_id'`) contra la implementación real,
  aunque los 193 tests de entonces pasaran (usaban un `FakeJournal` de test que sí seguía el
  orden del Protocol, así que el desajuste nunca se ejercitó). Se corrigió la firma de
  `SqliteJournal` para que coincida EXACTAMENTE con el Protocol; `demo_sesion.py` ejercita
  `EventBus.publish` contra un `SqliteJournal` real (con un adaptador async de 6 líneas por
  encima, ver más abajo) para que esto no vuelva a divergir en silencio.
- `edecan_forge_kernel/__init__.py` solo reexportaba `contracts` y `cas`; `journal`, `bus` y
  `projections` existían pero no eran importables desde la raíz del paquete. Se añadieron al
  reexport público (`SqliteJournal`, `EventBus`, `Projection`, y el resto de sus tipos de
  soporte).

Un gap de coherencia que se dejó **documentado, no "arreglado" a la fuerza**: `journal.py`
implementa su propio almacén de blobs (`put_blob`/`get_blob`/`redact`, en una tabla SQLite
`cas_blobs` dentro del mismo archivo que el journal) en vez de delegar en `cas.py::Cas` (que
guarda en el filesystem). Ambos usan el MISMO tipo `CasRef` y el MISMO algoritmo de hash
(`CasRef.from_bytes`), así que no hay divergencia de tipos — pero son dos almacenes físicos
distintos: un blob puesto vía `Cas.poner()` no es visible para `journal.redact()`, y viceversa.
Es probablemente una decisión de diseño correcta (la redacción necesita atomicidad transaccional
con la tabla `events`, que cruzar dos backends de almacenamiento distintos haría imposible sin
un protocolo de dos fases) pero no está resuelta explícitamente en `docs/arquitectura-forge.md`
— queda en "qué no hace todavía".

## Cómo se ejecuta

```bash
uv sync --all-packages   # o: uv sync --package edecan-forge-kernel
uv run pytest packages/forge-kernel -q
uv run ruff check packages/forge-kernel
uv run ruff format --check packages/forge-kernel
```

### La demo

```bash
uv run python -m edecan_forge_kernel.demo_sesion
```

Sin red, sin modelo, sin dejar rastro en disco (todo vive en un `tempfile.TemporaryDirectory`
que se borra solo). Hace, en este orden:

1. Abre una sesión (`session.create` vía `reduce()`) y un workspace de juguete.
2. Un agente pide una herramienta; se admite (con sus tres subestados internos), se despacha,
   arranca y completa — la máquina de estados completa, escrita en un `SqliteJournal` real a
   través de `EventBus.publish`.
3. El resultado grande de la herramienta va al CAS (`Cas.poner`), nunca al journal.
4. Salida de proceso simulada se emite por el canal efímero, anclada a un evento durable ya
   publicado, y se cierra con su `CasRef` — que coincide, byte a byte, con el que produce
   `Cas.poner()` sobre los mismos bytes.
5. Se gasta presupuesto (`Budget`/`BudgetScope`/`Hold`) — simulado LOCALMENTE en el script,
   porque `BudgetAuthority` es solo un `Protocol` todavía (ver abajo).
6. `append_if` con una guardia que se cumple y otra que no — el rechazo no mueve la cabeza del
   stream.
7. Se simula la muerte del proceso (`journal.close()`), se reabre el MISMO archivo SQLite, y el
   estado se reconstruye reejecutando `reduce()` sobre la misma secuencia de comandos desde
   `KernelState()` vacío — comparado byte a byte contra el estado que había en vivo, y contra
   los eventos que de verdad quedaron en el archivo reabierto.
8. Se verifica la cadena de hashes de punta a punta (`verify_chain`).
9. Se redacta un blob citado por un evento y se comprueba que la cadena SIGUE verificando
   exactamente igual.

Termina con un resumen: eventos escritos, blobs en cada almacén, bytes que se ahorraron por no
journalar la salida efímera, y el resultado de cada verificación.

## Qué NO hace todavía

- **`BudgetAuthority`, `EffectLedger` y `Journal` (el Protocol genérico) son solo tipos.** La
  implementación real de "un punto de serialización por workspace" para presupuesto es del
  bloque 7 (necesita un Durable Object o un lock de Postgres — I/O de red, prohibido en este
  paquete). `demo_sesion.py` simula un `hold`/`settle` a mano, en memoria del script, para que
  la demo tenga algo que enseñar; no es una implementación de referencia.
- **`SqliteJournal` es síncrono**, aunque el `Journal` Protocol declara `async def`. La stdlib
  `sqlite3` es síncrona y este paquete no puede sumar `aiosqlite` (regla dura: sin dependencias
  nuevas). Cualquier host async necesita un adaptador (`asyncio.to_thread`, como el que usa
  `demo_sesion.py` en 6 líneas) — no vive en el paquete porque es responsabilidad del host, no
  del journal.
- **`journal.redact()` no encadena un `Event` nuevo.** `kernel.event_redacted` existe en el
  `SchemaRegistry` pero está `reserved`, no `active` — activarlo es tocar el registro sellado de
  `contracts.py`, decisión de otro bloque.
- **No hay un `ExecWindow` real** (workspace copy-on-write, invariante 5). `ExecWindow` es solo
  un `Protocol` mínimo en `contracts.py` (`worktree`, `root`, `writable`, `base`, `close()`); el
  "workspace" que abre `demo_sesion.py` es un directorio cualquiera, con una nota que lo dice.
- **`Cas` (filesystem) y el almacén de blobs interno de `journal.py` son dos backends
  distintos** que comparten tipo (`CasRef`) pero no comparten contenido — ver la sección de
  coherencia arriba.
- **`projections.py` no reconstruye el `KernelState` completo desde eventos.** Solo hay dos
  proyecciones reales (`SessionTimelineProjection`, `BudgetLedgerProjection`, esta última un
  proxy por `EffectClass`/desenlace hasta que `budget.*` pase de `reserved` a `active`). La
  reconstrucción de `KernelState` que hace `demo_sesion.py` en el paso 7 reejecuta comandos
  contra `reduce()`, no folda eventos — es la propiedad correcta a demostrar (el reducer es
  puro), pero un host real necesitaría además persistir o poder re-derivar la lista de comandos
  ejecutados (un WAL de comandos, o volver a pedírselos al agente), que es de otro bloque.
- **No hay sellado criptográfico del journal** (`JournalHeader`, notario, `offload_prefix`) —
  diferido a fase 2 por el propio documento (§14).
- **`EffectCeilingPolicy`** (techo de `EffectClass` según `taint_hwm`, §5.8) es un `Protocol`
  sin tabla concreta: el documento solo da dos puntos ancla, nunca los seis niveles completos.
  Publicar una tabla inventada violaría "nada se rellena a mano sin decir que se rellenó a
  mano".

## Dos desviaciones frente al documento, y por qué

Las reglas duras de este encargo prohíben cualquier dependencia fuera de `pydantic` y la
stdlib. El documento fija BLAKE3 y CBOR; ninguno de los dos está disponible sin una dependencia
nueva. Este paquete usa `hashlib.blake2b(digest_size=32)` etiquetado `b2b:` (en vez de `b3:`) y
canonicaliza con JSON ordenado (en vez de CBOR RFC 8949) — el mismo contrato (un único `CasRef`
con algoritmo etiquetado, una forma canónica determinista para el hash-chain) con otro
primitivo. Ver el docstring de `edecan_forge_kernel/contracts.py` para el detalle completo de
las doce resoluciones de `docs/arquitectura-forge.md` §13 que este paquete materializa en tipos.

## Qué unifica `contracts.py`, y dónde

| # | Contradicción | Tipo que la resuelve |
| --- | --- | --- |
| 1 | Cuatro máquinas de estados de llamada a herramienta | `ToolCallState` + `AdmissionSubstate` + `validate_tool_call_transition` |
| 2 | `cache_key` vs. `idempotency_key` con semánticas opuestas | `CacheKey` / `IdempotencyKey` (tipos distintos, derivaciones distintas) |
| 3 | Cuatro autoridades de presupuesto | `BudgetAuthority` (protocolo; un punto de serialización por workspace) |
| 4 | Tres algoritmos de hash, cuatro nombres | `CasRef` (`b2b:<64 hex>` — ver la nota de desviación arriba) |
| 5 | Tres taxonomías de confianza | `TrustLevel` (seis niveles) + `TaintState` (marca de agua alta, sesión) |
| 6 | Cuatro taxonomías de riesgo de efecto | `EffectClass` (orden total de seis, compone con `effect_class_max`) |
| 7 | Tres `ProviderCapabilities` | Ninguno — se referencia `ModelCard` de `edecan_forge_probe` por `ModelIdentity`, sin redefinirla ni importarla (ver el docstring del módulo) |
| 8 | `append_if` inexistente pero necesario | `Journal.append_if` + `Guard` (protocolo; implementación real en `journal.py`) |
| 9 | Un escritor único choca con un stream por agente | `stream_id` en `Command`/`EventDraft`/`Event`, más `next_lamport` |
| 10 | Payload inline con texto libre | `validate_payload_inline` / `is_structural_value`, con `AuthzFacts` como excepción nombrada |
| 11 | El sandbox recibía una ruta, no una ventana | `ExecWindow` (protocolo; solo el tipo, la implementación es de otro bloque) |
| 12 | El inspector de contexto consumía un objeto que nadie producía | `SelectionReason` / `DropRecord` |

Más los campos huérfanos de §13.2 (`ToolResult.score`, `plan_step_id`, `AuthzFacts`, el
namespace de `effect_target`, `ResourceClass`, `may_invoke`, `deterministic`, `interruptible`,
`checkpointable`, `render_hint`) y el núcleo del kernel: `Event`, `Command`, `Stamp`, `Decision`
y `reduce(state, cmd) -> Decision`, más un `SchemaRegistry` cerrado con el espacio de nombres
completo de eventos por dominio (§1.5).
