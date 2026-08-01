# Handoff — Forge, el IDE de agentes de Edecán

> Para quien siga construyendo durante la noche del 27–28 de julio de 2026.
> Todo número de este documento está **medido**, no supuesto. Si algo no se midió, lo dice.

---

## 1. Qué se está construyendo

Edecán es un asistente operativo general —chat, llamadas, reservas, redes sociales, negocio— que
además **construye software de verdad**. Ese último dominio se llama **Forge**, y no es un editor
con un chat al lado:

> El listón declarado por el dueño del producto: poder decir *«construye Acme 2.0»* y que el
> sistema lo haga en horas o días. No generar snippets: escribir el código, ejecutarlo, ver el error
> real, arreglarlo, probarlo, abrir el navegador para comprobar que la pantalla se ve bien, hacer
> commit y desplegar.

La tesis económica que justifica el proyecto: **el andamiaje pesa más que el modelo**. Un modelo de
primera línea que no puede ejecutar su propio código, no ve el error y supone que funciona rinde
peor que uno intermedio con herramientas fiables, contexto correcto y verificación independiente.

### 1.1 Las tres superficies que pidió el usuario, textualmente

**Mac** — el IDE dentro de la app, «tipo Antigravity o Cursor. Simple, pero inteligente». Dos vistas,
no veinte paneles:

- **Vista Editor**: explorador a la izquierda, editor al centro, terminal abajo, panel de agente a
  la derecha. Para cuando el humano está metido en el código.
- **Vista Agentes**: *mission control*. Una línea por agente con su estado, paso, coste y tiempo.
  Debajo: línea de tiempo, artefactos revisables (plan, diff, captura, grabación) y cola de
  aprobaciones. Es la vista por defecto cuando hay trabajo largo en marcha.

Regla que traduce «simple pero inteligente» a algo ejecutable: **la pantalla muestra por defecto lo
que necesita decisión y esconde lo que no**. Un agente que va bien ocupa una línea; uno atascado o
esperando aprobación se expande solo.

**iPhone y Android** — el IDE es **una pestaña del tab bar**, como en Aria. No una app aparte.
Cuatro pestañas dentro, y el orden declara para qué sirve el teléfono:

| Pestaña | Para qué |
|---|---|
| **Agentes** | Qué pasa ahora. Pantalla de inicio |
| **Revisar** | Cola de aprobaciones y diffs. *La* razón de que el IDE esté en el teléfono |
| **Terminal** | Solo lectura por defecto |
| **Archivos** | Explorar y editar. Deliberadamente el último |

En el teléfono **el trabajo no se hace, se dirige**. Aprobar un despliegue desde la cama sí; editar
400 archivos no, y no pasa nada.

**El espejo en vivo Mac ↔ móvil** — ya está resuelto por diseño y no hay que construirlo: ambos
clientes se suscriben al mismo journal por cursor, así que ya están sincronizados. Lo único que hay
que hacer es el **modo seguir** (atar el foco del teléfono al del Mac), que viaja por el canal
efímero y **nunca entra al journal**: dónde estabas mirando no es un hecho del proyecto.

**Rechazado explícitamente**: streaming de píxeles del Mac al teléfono, web embebida en el móvil, y
paridad total de funciones entre superficies.

---

## 2. Las diez invariantes

No son estilo, son contrato. Un cambio que rompa una es un rediseño.

1. No es un editor: es un sustrato de ejecución para agentes.
2. **El journal es la única fuente de verdad.** Log append-only; todo lo demás es proyección.
3. Separación de planos: control (durable, pequeño, portable) vs datos (pesado, desechable).
4. **Contenido direccionado por hash**: el journal transporta referencias, nunca payloads grandes.
5. Workspaces copy-on-write: aislamiento por defecto, fusión explícita.
6. Todo es herramienta; toda herramienta la provee un plugin, salvo el núcleo confiable
   (journal, bus, VFS, procesos, capacidades). **MCP es un adaptador, no el ABI.**
7. **Capacidades, no permisos ambientales.** El sandbox impone; no se confía en el modelo.
8. Todo es cancelable, reanudable y con presupuesto.
9. Multi-agente en el contrato desde el día cero, aunque hoy corra un agente.
10. Cero acoplamiento directo entre módulos.

Y una regla de proceso: **el sistema se desarrolla contra el proveedor más débil disponible.**
Cualquier capacidad superior se detecta y se aprovecha; jamás se asume.

El diseño completo son 7.960 líneas en [`docs/arquitectura-forge.md`](arquitectura-forge.md).
No lo leas entero: usa `grep`. La sección `## 13. Coherencia` es la más importante — contiene las
doce contradicciones entre bloques ya resueltas, y es vinculante.

---

## 3. Dónde quedé exactamente

### Construido y verde

| Paquete | Qué es | Estado |
|---|---|---|
| `packages/forge-probe/` | Fase 0: sonda de capacidades + banco de 30 tareas reales | **440 tests verdes**, ruff limpio |
| `packages/forge-kernel/` | Contratos unificados, journal, CAS, bus y proyecciones | **193 tests verdes**, ruff limpio |

`forge-probe` contiene:
- `modelcard.py` — el contrato de qué significa medir un modelo. `Reliability` decide por el
  **límite inferior de Wilson al 95 %**, nunca por la media: el diseño se sostiene en el mal caso.
- `probes/{context,tools,perf}.py` — contexto útil, tool-calling por perfil de argumento, y
  rendimiento/caché/razonamiento.
- `providers.py` — `WorkersAIProvider` (REST, streaming SSE, errores tipados con backoff y jitter,
  deadline absoluto) y `OllamaProbeAdapter` como referencia de proveedor débil.
- `bench/{edecan,acme}.py` — 30 tareas reales sobre los dos repos, en TypeScript, Swift,
  Kotlin, SQL/Prisma, React y Python. **Los 30 criterios se verificaron: todos fallan hoy**, que es
  lo que hace que el banco mida algo.

`forge-kernel` contiene `contracts.py`, `journal.py`, `cas.py`, `bus.py` y `projections.py`. El
journal es SQLite en WAL, con streams de `seq` propia, `append_if` de guardia atómica, fencing por
`lease_epoch` y cadena de hashes verificable.

**El cimiento está demostrado, no prometido.** `uv run python -m edecan_forge_kernel.demo_sesion`
corre una sesión completa sin red y sin modelo, y la salida real dice:

```
estado reconstruido por replay == estado en vivo:  True
verify_chain('agent-1'):                           True
append_if guardia cumplida / rota:                 True / False
bytes efímeros NO journalizados:                   950 B
```

Es decir: se cierra el journal, se reabre sobre el mismo archivo y el estado se reconstruye idéntico;
se destruye un blob con datos personales y el hash del evento que lo citaba **no cambia**, así que la
cadena sigue verificando; y el stdout efímero no entró al journal pero quedó anclado con una
referencia cuyo hash coincide con el que produce el CAS por su cuenta.

Lo honesto: esto demuestra que **el sustrato** aguanta, no que un agente cierre una tarea real. Para
eso faltan el VFS, el ABI de herramientas y el bucle.

### En vuelo al momento de escribir esto

- El barrido completo de la sonda contra GLM-5.2, con tope de 25 USD. Falta
  `usable_context_tokens`.

No toca tu lado de la frontera (§5), así que no bloquea nada de la §6.

### Trampas que ya costaron tiempo y no hay que repetir

1. **`uv sync` a secas rompe el entorno.** Deja instalado solo el paquete que resuelve y se lleva
   por delante `edecan_llm` y el resto. Usa siempre **`uv sync --all-packages`**.
2. La evidencia de la sonda va a `.forge-probe/` en la raíz, **no** a `packages/forge-probe/`.
   Ya está en `.gitignore` (contiene prompts y respuestas en crudo).
3. Un caso de prueba que el modelo no puede acertar no mide al modelo: mide un defecto del caso.
   Ocurrió de verdad — 14 fallos por un punto final ambiguo en el prompt. Antes de concluir que un
   modelo falla, **mira la traza cruda**.

---

## 4. Lo medido contra la API real

Todo verificado el 27-07-2026 contra la cuenta `bd97ab5c87d3d2f6d99f93465aa63679`.

### Modelos

| Modelo | Estado |
|---|---|
| `@cf/moonshotai/kimi-k3` | **403, code 5018** — la cuenta no tiene acceso. Solicitado |
| `@cf/zai-org/glm-5.2` | **En uso para el IDE.** 262k ctx, tools, razonamiento, visión |
| `@cf/zai-org/glm-4.7-flash` | Para chat rápido. 131k ctx |
| `@cf/moonshotai/kimi-k2.7-code` | Alternativa |

El enrutado vive en [`config/modelos.yml`](../config/modelos.yml). **Es dato, no código.** Cambiar
de modelo es editar ahí. `IDE_MODEL: glm-5.2` → `kimi-k3` el día que haya acceso.

### Precios (USD por millón, leídos de la API)

| Modelo | Entrada | Salida | Entrada cacheada |
|---|---|---|---|
| GLM-5.2 | 1,40 | 4,40 | 0,26 |
| GLM-4.7-Flash | 0,0605 | 0,40 | — |
| Kimi K2.7-Code | 0,95 | 4,00 | 0,19 |

### Mediciones de la sonda

| Medición | Valor | Umbral | Veredicto |
|---|---|---|---|
| `native_tools.code_blob.lower_95` | **0,912** | ≥ 0,90 | PASA, justo → riesgo |
| `native_tools.scalar` | 0,97 (39/40) | — | — |
| `throughput_tps` | **95,3** tok/s | ≥ 25 | PASA holgado |
| Latencia p50 por llamada | 12,05 s | — | — |
| **Razonamiento sobre la salida** | **82,2 %** | — | — |
| Llamadas truncadas por `max_tokens` | **27 %** (con tope 1.600) | — | — |
| `usable_context_tokens` | **sin medir** | ≥ 48.000 | bloquea |
| `bench_success_rate` | **sin medir** | ≥ 0,55 | bloquea |

**Veredicto de la fase 0: NO-GO**, y es correcto — tres umbrales sin medir bloquean igual que un
fallo. «Sin dato» jamás se interpreta como aprobado.

### Las tres consecuencias económicas que cambian el diseño

1. **El precio real de salida no es 4,40 $/M.** Si solo el 17,8 % de lo que pagas es respuesta, el
   coste por millón de tokens *de contenido* es **~24,70 $**. Una tarea de agente sale a **~0,87 $**,
   no a 0,25 $. Con 50.000 $ de crédito son ~57.000 tareas.
2. **Un 27 % de las llamadas se corta por presupuesto de tokens.** El razonamiento come del mismo
   `max_tokens` y llega en un campo **separado**, `message.reasoning_content`. Con el tope justo, la
   respuesta llega **vacía y se cobra igual**, sin ningún error. Reserva siempre ≥200 tokens por
   encima del contenido esperado.
3. **La entrada cacheada cuesta 5× menos.** Mantener el prefijo del prompt estable —sistema,
   herramientas, contexto estable, historial, turno actual, **en ese orden y sin reordenar**— vale
   del orden de un 65 % más de trabajo con el mismo crédito. Es de fase 1, no de fase 2.

### Política de razonamiento, ya decidida

Se conserva el `reasoning_content` para depuración, reproducibilidad y auditoría. Con cuatro reglas
que lo hacen viable (están en `config/modelos.yml`):

- **Nunca vuelve al modelo.** Es del turno, no de la conversación.
- **Al CAS por referencia, nunca en línea en el journal.** Es el 82 % de la salida.
- **La redacción de secretos debe cubrirlo**, y con más motivo que el contenido: es justo donde el
  modelo repite en claro la clave que acaba de leer mientras `content` sale limpio.
- **Es evidencia, no memoria.** Guardarlo no hace que el agente aprenda.

Visibilidad: mostrar en el IDE (ahí quien mira es quien desarrolla), ocultar en el chat.

---

## 5. La frontera de archivos

**Léela antes de tocar nada.** Trabajamos en paralelo sobre el mismo repo.

| Tuyo | Mío — no lo toques |
|---|---|
| `packages/llm/` | `packages/forge-kernel/` |
| `packages/core/`, `packages/agents/`, `packages/toolkit/` | `packages/forge-probe/` |
| `apps/api/`, `apps/companion/`, `apps/local/` | `docs/arquitectura-forge.md` |
| La migración de Aria | `docs/handoff-forge.md` (este archivo) |
| `config/modelos.yml` — **léelo y respétalo, no lo reescribas** | `.forge-probe/` |

Zona compartida, con cuidado: `pyproject.toml` de la raíz. Si añades un miembro al workspace,
añádelo y ya; no reordenes la lista.

---

## 6. Trabajo para esta noche

En orden. Cada tarea tiene un criterio que se comprueba con un comando, no con una opinión.

### T1 — Proveedor Workers AI en `packages/llm` (2-3 h)

Hoy Edecán usa `codex_cli`, que **no es un endpoint de inferencia: es un agente de código completo**
(lo dice su propio comentario en `packages/llm/edecan_llm/codex_cli.py:49`). Workers AI es un
endpoint crudo. Cambiar el cerebro no da un agente; eso es el trabajo de Forge.

Escribe `packages/llm/edecan_llm/workers_ai.py` implementando el `LLMProvider` de `base.py`.
**Referencia ya escrita y probada**: `packages/forge-probe/edecan_forge_probe/providers.py` —
cópiale la estructura, los errores tipados, el backoff con jitter y el manejo de
`reasoning_content`. No lo reinventes.

Añade `"workers_ai"` al conjunto permitido en `config.py:26` y a las líneas 39 y 47. Expórtalo en
`__init__.py`. Documenta en `docs/proveedores-llm.md`.

Detalles que muerden, todos verificados:
- La respuesta viene envuelta: `{"result": {...}, "success": true, "errors": []}`. Hay que
  desenvolver **y** comprobar `success` — un fallo puede llegar con HTTP 200.
- `tool_calls[].function.arguments` es un **string JSON**, no un objeto. Con bloques de código
  dentro es donde se rompe. Valida que el texto vuelve byte a byte idéntico.
- `usage.prompt_tokens_details.cached_tokens` y `usage.neurons`: regístralos desde el primer día.

> **Criterio de aceptación:**
> `uv run pytest packages/llm -q` en verde, con tests `respx` que cubran: éxito, `success:false`
> con HTTP 200, 401/403 con el `code` de Cloudflare extraído, 429 con reintento, timeout,
> streaming SSE, `tool_calls` con bloque de código multilínea, y parseo de `cached_tokens`.
> Ningún test toca la red.

### T2 — Enrutado por perfil desde `config/modelos.yml` (1-2 h)

Que `chat_rapido` use GLM-4.7-Flash y `ingenieria_software` use GLM-5.2, leyendo el YAML. Ningún
módulo puede comparar nombres de modelo (`if modelo == "..."`).

> **Criterio:** un test que cambia el modelo en el YAML y comprueba que el router lo respeta, más
> un test que falla si aparece un literal de nombre de modelo fuera de `config/`.

### T3 — Presupuesto de razonamiento en todo el camino (1 h)

`max_tokens` debe reservar espacio para el razonamiento. Una respuesta con `content` vacío y
`finish_reason: length` es un **error recuperable**, no una respuesta.

> **Criterio:** test que simula `content` vacío con `finish_reason: length` y comprueba que se
> reintenta con más presupuesto en vez de devolver cadena vacía al llamante.

### T4 — Orden estable del prompt (1-2 h)

Sistema → herramientas → contexto estable → historial → turno actual. Sin reordenar entre turnos.
Vale 5× en el precio de entrada.

> **Criterio:** test que construye dos turnos consecutivos y afirma que el prefijo del segundo
> **empieza exactamente igual** que el del primero, byte a byte, hasta el punto de divergencia.

### T5 — Si sobra noche: la migración de Aria como banco

Cada capacidad movida de Aria a Edecán es una tarea con verificación evidente: ¿sigue
funcionando? Añádelas a `packages/forge-probe/bench/` con criterio ejecutable, siguiendo el formato
de `bench/edecan.py`. Es trabajo que hay que hacer igual y que además mide el sistema.

---

## 7. Qué NO hacer

- **No toques `packages/forge-kernel/` ni `packages/forge-probe/`.** Estoy dentro.
- **No corras `uv sync` a secas.** Rompe el entorno. `uv sync --all-packages`.
- **No quites `codex_cli` todavía.** No por prudencia —Edecán no está en producción y Aria sigue
  intacto— sino porque es la **línea base de comparación**: cuando el bucle propio de Forge corra el
  mismo banco de 30 tareas, sabremos con números si mejora.
- **No inventes mediciones.** Si un número no se midió, es `None`, no un valor «razonable». Es la
  regla dura de `modelcard.py` y existe porque un valor de catálogo copiado de la documentación del
  proveedor contamina todas las decisiones que cuelgan de él.
- **No lances agentes contra la API sin tope.** Cada llamada cuesta dinero real. Los tests de
  integración van detrás de `FORGE_PROBE_INTEGRACION=1`; la presencia del token no basta.
- **No expongas el token en logs ni en mensajes de error.** Un token truncado en un log sigue siendo
  una fuga.

---

## 8. Lo siguiente, cuando amanezca

1. Cerrar `journal.py` y la demo que mata el proceso y reanuda desde el journal. Ese es el criterio
   de que el cimiento existe.
2. Terminar el barrido de la sonda: falta `usable_context_tokens`, que es el número del que dependen
   entre el 40 % y el 70 % del diseño de la fase 1.
3. Correr el banco de 30 tareas con el bucle real y obtener `bench_success_rate`.
4. Con esos tres, el veredicto de la fase 0 deja de ser NO-GO por huecos y pasa a ser una decisión
   informada sobre si la fase 1 arranca como está diseñada o hay que rediseñar los bloques 3, 4 y 6.
