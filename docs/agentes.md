# Ecosistema de agentes: Orchestrator y misiones

Paquete `edecan_agents` (`packages/agents/`), work package **fase v2** de `docs/roadmap.md`
(ecosistema base + 3 perfiles P0), **fase v4** (activación de 12 perfiles + gate real de confirmación
para herramientas peligrosas), **fase v5** (activación del perfil `voice`, dependencias entre pasos con
ejecución por olas en paralelo, replan acotado y timeout por paso) y **fase v6** (observabilidad
enriquecida — uso/costo y timing por paso, agregados — vía `GET /v1/missions/{id}/detalle`, más plantillas
de misión en la UI web, ver §10/§11). Implementa el patrón multi-agente completo: un **Orchestrator**
planifica un objetivo en pasos (opcionalmente con dependencias entre ellos) y los delega en **sub-agentes
especializados** (perfiles), cada uno con su propio conjunto recortado de herramientas. Este documento
describe qué es real hoy, cómo fluye una misión de punta a punta y cómo funciona el ecosistema completo
(**16 perfiles, los 16 activos** tras `fase v5`).

## 1. Visión general

Una **misión** (`agent_missions`) nace de un objetivo en lenguaje natural — típicamente porque el usuario
le pide al agente principal algo que requiere varios pasos encadenados de investigación, análisis o
generación de contenido, y el agente principal invoca la herramienta `delegar_mision`. La misión corre
**en segundo plano** (en el worker, vía el job `run_mission`), no en el turno de chat que la creó:

```
Usuario → Agente principal → tool `delegar_mision`
                                   │
                                   ├─ INSERT agent_missions (status=planning)
                                   └─ enqueue job "run_mission"
                                          │
                                          ▼
                              apps/worker: handlers/run_mission.py
                                          │
                          ┌───────────────┴────────────────┐
                          │ Orchestrator.plan(objetivo)     │  1 llamada LLM → JSON de pasos
                          │  → resuelve/valida depende_de   │  (opcional, fase v5)
                          │  → INSERT agent_steps (pending) │
                          │  → agent_missions.status=running│
                          └───────────────┬────────────────┘
                                          │
                          ┌───────────────┴────────────────┐
                          │ Orchestrator.run(mission, deps) │  agrupa pasos en OLAS por
                          │  por ola: asyncio.gather         │  orden topológico (depende_de);
                          │  limitado a MISSIONS_PARALLEL_MAX│  dentro de una ola, hasta
                          │  perfil → RestrictedRegistry     │  MISSIONS_PARALLEL_MAX pasos
                          │  → Agent.run_turn(...) con       │  corren EN PARALELO (nunca uno
                          │    timeout por paso              │  con permite_dangerous_con_
                          │  → agent_steps.status=done        │  confirmacion=True, ver §4bis)
                          │  (error de paso -> replan, §4bis) │
                          └───────────────┬────────────────┘
                                          │
                              síntesis final (1 llamada LLM)
                                          │
                              agent_missions.resultado, status=done
```

El resultado de cada paso se antepone como **historial sintético** SOLO a los pasos que dependen de él
(`depende_de`, ver §4bis) — el sub-agente que ejecuta un paso "ve" (como turnos `user`/`assistant`) lo que
produjeron sus dependencias declaradas, aunque las haya corrido un `Agent`/perfil distinto. Sin
dependencias declaradas (plan viejo, o un paso que simplemente no las usa), el comportamiento es
retrocompatible: cada paso depende de TODOS los anteriores, igual que antes de `fase v5`.

## 2. Piezas del paquete `edecan_agents`

| Módulo | Qué hace |
|---|---|
| `profiles.py` | `AgentProfile` (dataclass) + `PROFILES: dict[str, AgentProfile]` con las 16 claves pinned (§7.9). |
| `registry_view.py` | `RestrictedRegistry`: envuelve el `ToolRegistry` completo del proceso y lo recorta, por paso, a `allowed_tools` del perfil que ejecuta ese paso — dentro de eso, oculta las tools `dangerous=True` salvo que el perfil declare `permite_dangerous_con_confirmacion=True` (fase v4). |
| `orchestrator.py` | `Orchestrator.plan()` (planificación vía LLM, incl. `depende_de`) y `.run()` (ejecución por olas en paralelo + replan + timeout + síntesis, fase v5). `Mission`/`RunDeps` son los tipos de datos que cruzan la frontera hacia el worker. |
| `tools.py` | `delegar_mision` — la única herramienta de este paquete: crea la fila `agent_missions` y encola el job. |

Entry point `edecan.tools` (`pyproject.toml`): `agents = "edecan_agents:get_all_tools"`.

## 3. Los 16 perfiles (`profiles.py`)

**Las 16 claves pinned están activas** (`disponible=True` — `Orchestrator.plan()` puede asignarles un
paso) tras `fase v5` (`voice` era la única que seguía `disponible=False`). `AgentProfile` trae un campo
`permite_dangerous_con_confirmacion: bool = False` (fase v4, ver §4): controla si las tools
`dangerous=True` de `allowed_tools` quedan **visibles pero pausadas hasta aprobación humana** (`True`) o
**invisibles** (`False`, el default — comportamiento idéntico al de antes de que existiera este campo).

### 3.1 Sin ninguna tool `dangerous` (9 perfiles, `permite_dangerous_con_confirmacion=False`)

| Perfil | Nombre | Herramientas | Origen |
|---|---|---|---|
| `research` | Investigación | `buscar_web`, `navegar_web`, `extraer_datos_web`, `consultar_documentos`, `hora_actual` | P0 (fase v2) |
| `data_analyst` | Análisis de datos | `analizar_tabla`, `extraer_tablas_pdf`, `generar_grafico`, `exportar_analisis`, `calculadora`, `consultar_documentos`, `predecir_serie`, `detectar_anomalias` | P0 (fase v2) |
| `content` | Contenido | `generar_contenido`, `crear_documento`, `crear_presentacion`, `crear_pdf` | P0 (fase v2) |
| `ceo` | Dirección general | `resumen_finanzas`, `estado_negocio`, `consultar_documentos` | fase v4 |
| `design` | Diseño | `generar_imagen`, `crear_presentacion`, `crear_documento` | fase v4 |
| `legal` | Legal | `analizar_contrato`, `comparar_contratos`, `generar_borrador_legal`, `consultar_documentos` | fase v4 |
| `video` | Video | `analizar_imagen`, `analizar_video` | fase v4 |
| `voice` | Voz | `sintetizar_voz`, `listar_voces` | fase v5 |

`finance` (`resumen_finanzas`, `registrar_transaccion`, `cotizar_activo`, `gestionar_presupuesto`)
**también activa en este grupo** pese a manejar dinero: verificado con grep, ninguna de sus 4
herramientas es `dangerous=True` hoy (las que sí lo son en `edecan_commerce`, `preparar_pago`/
`preparar_orden`, no están en su `allowed_tools`). Si en el futuro se le suman, el flag debe pasar a
`True` en ese mismo cambio — ver el comentario en `profiles.py`.

`voice` (fase v5) deja de ser el único perfil "declarado, no disponible": sus 2 tools pinned
(`sintetizar_voz`/`listar_voces`, nombres fijados por `fase v5`, `ARCHITECTURE.md` §14) son de solo
lectura (listar voces del tenant) o generan un archivo nuevo (sintetizar audio a Archivos) — ninguna
publica, notifica ni gasta nada real, así que se queda en `False` igual que `ceo`/`design`/`legal`/
`video`. Sigue sin cubrir voz **en vivo** (llamadas/conversación por voz): eso sigue siendo un CANAL de
entrada/salida (§4 de `ARCHITECTURE.md`), no una herramienta de misión — si las tools de voz reales
todavía no están instaladas en el runtime (paquete que las aporta aún no aterrizó), `RestrictedRegistry`
simplemente no las ofrece y el paso degrada igual que cualquier perfil sin sus tools reales — seguro por
diseño, mismo criterio que "se activan a medida que existan sus herramientas" (`docs/roadmap.md`).

### 3.2 Con al menos una tool `dangerous` (7 perfiles, `permite_dangerous_con_confirmacion=True`)

| Perfil | Nombre | Herramientas | Cuál(es) es `dangerous=True` |
|---|---|---|---|
| `marketing` | Marketing | `generar_contenido`, `publicar_social`, `generar_imagen`, `buscar_web` | `publicar_social` |
| `sales` | Ventas | `buscar_contactos`, `gestionar_contacto`, `enviar_correo` | `enviar_correo` |
| `social_media` | Redes sociales | `publicar_social`, `generar_contenido`, `leer_mensajes`, `enviar_mensaje` | `publicar_social`, `enviar_mensaje` |
| `developer` | Desarrollo | `usar_computadora`, `consultar_documentos`, `buscar_web` | `usar_computadora` |
| `qa` | Calidad (QA) | `usar_computadora`, `consultar_documentos` | `usar_computadora` |
| `security` | Seguridad | `usar_computadora`, `buscar_web` | `usar_computadora` |
| `devops` | DevOps | `usar_computadora` | `usar_computadora` |

Para estos 7, un sub-agente SÍ puede pedir su tool peligrosa — pero nunca se ejecuta sola: ver §4. Estos 7
son además los únicos perfiles que **jamás entran a una ola paralela** (§4bis): siempre corren solos en su
propia ola, para que `waiting_confirmation` siga siendo un estado único y determinista.

### Guardrail: ningún perfil P0 tiene NINGÚN camino hacia una tool peligrosa

`research`/`data_analyst`/`content` — los que el planificador elige con más frecuencia, para las tareas
más genéricas — no solo no referencian ninguna tool `dangerous`, sino que además conservan
`permite_dangerous_con_confirmacion=False` de forma permanente (`tests/test_profiles.py` lo fija
byte-a-byte). Un sub-agente ejecuta una instrucción **sintética** (generada por el planificador, no
directamente por el usuario) sin supervisión turno a turno, así que estos tres perfiles se quedan sin
ningún camino hacia una acción irreversible, ni siquiera detrás de una confirmación.

`RestrictedRegistry` es la **segunda barrera** (defensa en profundidad) para el resto: si un perfil futuro
sumara una tool `dangerous` a `allowed_tools` sin marcar el flag correspondiente, esa tool queda invisible
(falla cerrado) en vez de ejecutarse sin aprobación.

## 4. El gate de confirmación real (fase v4): de "ocultar" a "pausar + aprobar"

Antes de fase v4, la única defensa posible era ocultar cualquier tool `dangerous` para siempre — lo que
también significaba que ningún perfil con una tool peligrosa en su diseño (`marketing`, `sales`,
`developer`, ...) podía activarse de verdad. `AgentProfile.permite_dangerous_con_confirmacion` resuelve
esto sin debilitar el guardrail: el principio "nada peligroso se auto-aprueba" (`docs/roadmap.md`)
se mantiene igual, pero la defensa pasa de un booleano estático a un punto de control real — un humano.

Si un sub-agente de uno de los 7 perfiles de §3.2 pide su tool `dangerous`, `Orchestrator.run()`:

1. `Agent.run_turn` la detiene ANTES de ejecutarla y emite `confirmation_required` (mismo gate que ya usa
   el chat normal, `ARCHITECTURE.md` §10.7) — nunca corre nada.
2. `Orchestrator.run` persiste `{id, name, args}` de la tool pendiente en `agent_steps.usage` (clave
   `"pending_tool_call"`).
3. Marca el paso y la misión `waiting_confirmation`.
4. Retorna sin ejecutar nada más — **nunca auto-aprueba**.

El usuario aprueba/rechaza desde `POST /v1/missions/{id}/confirm {approved}` (§5):

- `approved=false` → la misión pasa a `cancelled`, el paso pendiente a `skipped`.
- `approved=true` → la misión vuelve a `running` y se encola `run_mission` con
  `{"resume": true, "approved_step_seq": <seq>}`. El worker resetea ese paso a `pending`, inyecta el
  `tool_call_id` guardado en `ToolContext.extras["approved_tool_calls"]`, y el `Orchestrator` retoma la
  misión desde ese paso ejecutando la tool aprobada **directo, contra el `ToolRegistry` completo** (no el
  recortado del perfil — la aprobación la dio un humano, no el perfil) — los pasos previos, ya `done`, no
  se re-ejecutan, su resultado se reconstruye como historial sintético.

**Por qué nunca se reinvoca al LLM para reanudar**: el `tool_call_id` que aprueba el usuario es el que
generó el proveedor LLM en el intento ORIGINAL; si `run()` volviera a llamar al LLM para el mismo paso
antes de llegar a esa tool, el nuevo intento acuñaría un `tool_call_id` distinto que `approved_tool_calls`
jamás reconocería como aprobado, dejando la misión en un loop de "aprobar" que nunca progresa (mismo
problema de fondo que resuelve `edecan_api.routers.conversations` para el chat normal, ver
ARCHITECTURE.md §10.12). Por eso `Orchestrator._run_resumed_step` ejecuta la tool/args aprobados
DIRECTO, sin pasar por `Agent`/LLM.

Para los 9 perfiles de §3.1 (los tres P0 + `ceo`/`design`/`legal`/`video`/`finance`/`voice`) este camino
sigue siendo inalcanzable en la práctica: ninguna de sus tools es `dangerous` hoy, y aunque lo fuera,
`RestrictedRegistry` la seguiría ocultando (`permite_dangerous_con_confirmacion=False`) — es una red de
seguridad para un error humano futuro, no un camino esperado.

## 4bis. Planes con dependencias, paralelismo y replan (`fase v5`)

### Dependencias entre pasos (`depende_de`)

Cada paso del plan acepta una clave OPCIONAL `"depende_de"`: una lista de índices 0-based (posición
dentro de la lista `pasos` de ESE MISMO plan — el primer paso es el índice 0) de pasos ANTERIORES cuyo
resultado necesita este paso. `Orchestrator.plan()` la pide explícitamente en el prompt del planificador
(el LLM puede omitirla, o declarar `[]` para un paso sin dependencias) y la valida/resuelve antes de
persistir el plan; `Orchestrator.run()` la vuelve a resolver de forma DEFENSIVA sobre lo que reciba (mismo
código, `_resolver_depende_de`), así que un plan viejo (persistido antes de este WP, o armado a mano) sigue
funcionando exactamente igual sin tocar nada.

Reglas exactas:

- **Sin la clave en absoluto** (retrocompatible): el paso depende de TODOS los pasos anteriores — reproduce
  byte a byte la acumulación total de historial que ya existía antes de este campo, y de paso fuerza una
  ejecución 100% secuencial (un paso por ola) para cualquier plan que nunca use el campo nuevo.
- **`[]` explícito**: el paso no depende de nada — puede entrar a la primera ola en la que sea elegible
  (así el planificador marca pasos independientes/paralelizables).
- **Índices inválidos** (fuera de `[0, total_pasos - 1]`, o `>=` el índice propio — la única forma en que
  `depende_de` podría formar un ciclo, ya que cada índice solo puede apuntar hacia atrás) → se descarta la
  lista COMPLETA con un `logger.warning` y el paso queda secuencial SOLO tras el paso inmediatamente
  anterior (`[idx - 1]`).

Como `agent_steps` no tiene columna propia para `depende_de` (`docs/roadmap.md`, y este WP NO agrega
ninguna migración — el formato vive en el jsonb `agent_missions.plan`), `depende_de` viaja escondido
dentro de la columna `usage` que YA existía (`{"depende_de": [...]}`) mientras el paso sigue `pending`;
`run_mission.py::_paso_con_depende_de` lo extrae de vuelta al cargar los pasos. En cuanto un paso corre de
verdad, `usage` se sobreescribe con datos reales (`pending_tool_call` o tokens) y `depende_de` deja de
estar disponible — momento en el que ya no hace falta.

### Ejecución por olas y paralelismo

`Orchestrator.run()` agrupa los pasos pendientes en **olas** por orden topológico sobre `depende_de`: una
ola contiene todos los pasos cuyas dependencias ya terminaron. Dentro de una ola, los `Agent` de cada paso
corren con `asyncio.gather`, limitados por `asyncio.Semaphore(getattr(settings, "MISSIONS_PARALLEL_MAX",
3))` (setting agregado por `fase v5`, leído de forma defensiva).

Reglas de seguridad del paralelismo:

1. **Ningún perfil `permite_dangerous_con_confirmacion=True` (§3.2) entra jamás a una ola paralela**:
   siempre corre SOLO, en su propia ola — así `waiting_confirmation` sigue siendo un estado único y
   determinista. Consecuencia: una ola con más de un paso JAMÁS puede producir `confirmation_required`.
2. **Las escrituras se serializan** con un `asyncio.Lock` compartido por toda la ejecución de `run()`:
   varios pasos de una misma ola pueden terminar casi al mismo tiempo, pero todos comparten la MISMA
   `AsyncSession` (una por misión, no por paso) — que no soporta uso concurrente.
3. **El historial sintético es por-dependencia, no por-ola**: cada paso recibe el resultado de SUS
   dependencias declaradas (en orden de índice), nunca el de "todo lo que terminó antes" ni el de sus
   compañeros de ola que no sean también una dependencia declarada.
4. **Si un paso de una ola entra a `waiting_confirmation`**: `run()` espera a que terminen los demás pasos
   de esa misma ola en vuelo, persiste sus resultados, y la misión queda `waiting_confirmation` con el
   `pending_tool_call` de ESE paso — los pasos que ni siquiera se habían lanzado quedan `pending`.

### Replan acotado (máximo 1 por misión)

Si un paso termina en `error` (excepción del `Agent`/tool, o timeout — ver abajo) y la misión no consumió
todavía su único replan (contador persistido en `agent_missions.presupuesto["replans_usados"]`, default
`0`, máximo `1`), `run()` intenta UNA llamada al LLM `"principal"` con el objetivo, un resumen de los pasos
completados y el error, pidiendo un plan NUEVO **solo para lo que falta** (mismo formato JSON, incluido
`depende_de` — local a la sub-lista nueva, desplazado al insertarla), con la restricción dura de que
`pasos_completados + pasos_nuevos <= presupuesto original` (trunca igual que `plan()`).

- Los pasos ya `done` se conservan tal cual.
- Los pasos que quedaron `pending` sin lanzarse (de olas posteriores a la que falló) se marcan `skipped`.
  El paso que falló ya quedó `error` (no se toca de nuevo).
- Los pasos nuevos se insertan como filas `agent_steps` NUEVAS vía `RunDeps.insert_steps` (fase v5: método
  nuevo del "seam" — `save_step` solo ACTUALIZA una fila existente, no crea filas) con `seq` continuando
  después del último usado.
- Si el replan falla (LLM sin JSON usable, o sin presupuesto restante) o la misión ya había usado su
  replan → la misión pasa a `error` con el mensaje del paso que la disparó (comportamiento idéntico al de
  antes de este WP).
- **Nunca se replanea un paso `waiting_confirmation`**: eso es una pausa humana esperada, no un fallo.

### Timeout por paso

Cada ejecución de `Agent.run_turn` (el camino normal de un paso — NUNCA el camino de reanudación de una
tool aprobada, que no construye ningún `Agent`) se envuelve en
`asyncio.timeout(getattr(settings, "MISSIONS_STEP_TIMEOUT_SECONDS", 300))`. Si se agota, el paso se marca
`error` con un mensaje claro y dispara la misma lógica de replan de arriba — un timeout es, a todos los
efectos de `run()`, un tipo más de error de paso.

## 5. API HTTP (`/v1/missions`, `edecan_api.routers.missions`)

Bearer + flag de plan `agents.missions`. Montaje defensivo en `edecan_api.main` (§7.6, responsable de la fase v2).

| Ruta | Qué hace |
|---|---|
| `POST /v1/missions {objetivo}` | Valida `limits.missions_per_day` (`-1` ilimitado, `0` → `403`, cupo agotado → `429`); inserta la misión (`status=planning`) y encola `run_mission`. |
| `GET /v1/missions` | Lista las misiones del usuario actual en el tenant, más recientes primero. |
| `GET /v1/missions/{id}` | Misión + sus `agent_steps`, ordenados por `seq` (tal cual vive en la fila, sin recortar). |
| `GET /v1/missions/{id}/detalle` | **fase v6.** Superset observabilidad del anterior — ver §10. |
| `POST /v1/missions/{id}/confirm {approved}` | Solo si la misión está `waiting_confirmation` (si no, `409`). Ver §4. |
| `POST /v1/missions/{id}/cancel` | Solo si la misión no está en un estado terminal (si no, `409`). |

El router habla SQL parametrizado directo contra `agent_missions`/`agent_steps` (mismo criterio que
`edecan_toolkit.recordatorios`/`edecan_api.routers.consents`: los nombres de tabla/columna están pinned
en el contrato, la forma interna de acceso a datos no) — nunca importa `edecan_agents`: crear la fila y
encolar el job es todo lo que hace en el turno HTTP: planificar y ejecutar corre asíncrono en el worker.
`GET /{id}/detalle` (fase v6) respeta esta regla igual: solo LEE lo que `edecan_agents.orchestrator` ya
dejó escrito en `agent_steps.usage`, nunca importa ese paquete tampoco.

## 6. Worker (`apps/worker/edecan_worker/handlers/run_mission.py`)

`handle(env, deps)` consume el job `"run_mission"` (ya en `edecan_schemas.JOB_TYPES`). Importa
`edecan_agents` de forma perezosa (dentro de la función, no al tope del módulo) — mismo patrón que
`edecan_worker.deps` con `edecan_core`, por si ese paquete hermano todavía no existiera en un workspace
parcial. El worker se conecta como "dueño" (bypassa Row-Level Security, ARCHITECTURE.md §2), así que
**todas** sus queries filtran `tenant_id = env.tenant_id` a mano.

Payload `{"mission_id"}` → planifica + persiste pasos + ejecuta. Payload
`{"mission_id", "resume": true, "approved_step_seq"}` → reanuda (§4). Una misión ya en estado terminal se
ignora sin error (pudo cancelarse mientras el job esperaba en la cola).

`_RunDeps` (la implementación concreta de `edecan_agents.orchestrator.RunDeps` sobre SQL real) gana dos
capacidades en `fase v5` (§4bis): `insert_steps` (crea filas `agent_steps` nuevas para un replan,
reutilizando el mismo helper `_insert_steps` que ya usaba la planificación inicial) y `save_mission(...,
presupuesto=...)` (persiste el contador `replans_usados`) — ninguna cambia el SQL pinned de
`docs/roadmap.md` ni el resto del flujo del handler. `_update_step` (el `SET usage = ...` literal, sin
merge) tampoco cambió en `fase v6`: sigue siendo `edecan_agents.orchestrator._timing_usage` quien arma
el dict completo (tokens + `started_at`/`finished_at`) ANTES de pasarlo a `save_step`, ver §10.

## 7. Web (`/app/misiones`)

`apps/web/src/lib/api-misiones.ts` (fetchers tipados, mismo manejo de auth que `lib/api.ts`; `getMission`/
`MissionDetail` originales intactos — `getMissionDetalle`/`MissionDetalle`/`MissionStepDetalle`/
`MissionAgregados` nuevos desde `fase v6`, ver §10) + `apps/web/src/components/misiones/`
(`MissionStatusBadge`/`StepStatusBadge`, `StepTimeline`, y desde `fase v6`: `MissionResumen`, `olas.ts`,
`plantillas.ts`/`PlantillasMisiones`) + `apps/web/src/app/(app)/app/misiones/page.tsx`: plantillas arriba,
lista + formulario de creación + detalle enriquecido con timeline de pasos. *Polling* cada 2 s mientras la
misión (o alguna de la lista) siga `planning`/`running`, más un botón "Refrescar" manual (`fase v6`).
Botones Aprobar/Rechazar aparecen inline en el paso cuando queda `waiting_confirmation`; botón Cancelar
mientras la misión no esté en un estado terminal.

## 8. Flags y límites de plan (`edecan_schemas.plans`, responsable de la fase v2)

`FLAG_AGENTS_MISSIONS` (`"agents.missions"`) y `LIMIT_MISSIONS_PER_DAY` (`"limits.missions_per_day"`,
`-1` = ilimitado). Tabla completa en `docs/roadmap.md`. `GET /v1/missions/{id}/detalle` (§10) exige el
mismo `FLAG_AGENTS_MISSIONS` que el resto del router — sin flag nuevo propio.

## 9. Tests

- `packages/agents/tests/test_profiles.py`: las 16 claves pinned, **las 16 `disponible=True`** (tras
  `fase v5`, `voice` incluido), el guardrail P0 (`permite_dangerous_con_confirmacion=False` byte-a-byte) y
  el invariante general — todo perfil `disponible=True` cuyo `allowed_tools` toque una tool `dangerous`
  conocida DEBE declarar `permite_dangerous_con_confirmacion=True` — más el valor pinned exacto por
  perfil (incluye los casos `finance`/`voice`, que se quedan en `False` pese a manejar dinero/generar
  archivos, y las tools exactas de `voice`: `sintetizar_voz`/`listar_voces`).
- `packages/agents/tests/test_registry_view.py`: `RestrictedRegistry` con `permite_dangerous_con_confirmacion`
  en `False` (default, comportamiento idéntico al de antes del campo) y en `True` (`get`/`specs` exponen la
  tool `dangerous` si está en `allowed_tools`, la siguen ocultando si no).
- `packages/agents/tests/test_orchestrator_run.py`: ejecución por olas con `Agent`/`ToolRegistry` falsos
  (contexto sintético por-dependencia, presupuesto, manejo de errores), el ciclo completo de un perfil con
  `permite_dangerous_con_confirmacion=True` (`developer`/`usar_computadora`): el paso queda
  `waiting_confirmation` con `pending_tool_call` persistido y la tool JAMÁS se ejecuta en el primer
  intento; la reanudación la ejecuta exactamente una vez, sin reconstruir ningún `Agent` nuevo. **fase v5**
  suma: un plan viejo sin `depende_de` se sigue ejecutando 100% secuencial con historial completo
  (retrocompatibilidad explícita); paralelismo REAL verificado con rendezvous/contadores compartidos (no
  solo estructural) — dos pasos independientes corren de verdad al mismo tiempo, `MISSIONS_PARALLEL_MAX`
  limita cuántos a la vez, y el `asyncio.Lock` impide que dos `save_step` concurrentes se solapen; una ola
  con más de un paso donde uno pausa por confirmación espera a los demás y persiste sus resultados; replan
  acotado (genera plan nuevo tras un error, respeta el presupuesto original, nunca se dispara dos veces, ni
  para un `waiting_confirmation`, y los pasos pendientes no lanzados quedan `skipped`); timeout por paso
  (marca error con mensaje claro, dispara replan, y NUNCA envuelve la reanudación de una tool aprobada, que
  no construye ningún `Agent`). **fase v6** suma: `started_at`/`finished_at` en el `usage` de cada
  guardado terminal (`done` conserva los tokens de `Usage` junto al timing; `waiting_confirmation` conserva
  `pending_tool_call` junto al timing; error/timeout también) y que la transición a `running` sigue sin
  tocar `usage` (`_timing_usage` solo interviene en el guardado terminal).
- `packages/agents/tests/test_dependencias_y_paralelismo.py` (**nuevo, fase v5**): unitarios y directos
  sobre los helpers puros `_resolver_depende_de`/`_validar_depende_de` (default retrocompatible, `[]`
  explícito, índices fuera de rango, auto-referencia, referencia futura, **ciclo de 2 pasos degradado a
  secuencial**, idempotencia) y `_construir_olas` (pasos independientes en la misma ola, cadena secuencial,
  dependencias parciales, perfiles `dangerous`-capable SIEMPRE solos en su ola, salvaguarda anti-loop-
  infinito ante un ciclo defensivo) y `_historial_de_dependencias` (solo las dependencias declaradas, en
  orden de índice, nunca "todo lo anterior").
- `packages/agents/tests/test_orchestrator_plan.py`: parseo tolerante del plan JSON (incl. LLM sin JSON
  válido → fallback a 1 paso `research`), y que `plan()` ya puede elegir cualquiera de los 13 perfiles
  activados por `fase v4`/`fase v5` (`voice` incluido; antes se reasignaban a `research`) mientras
  cualquier clave inventada (o un perfil forzado a `disponible=False` vía monkeypatch, ya que las 16 reales
  están activas) se lo siga reasignando; el system prompt del planificador describe los 13 y menciona
  `depende_de`; **fase v5** suma: `depende_de` se resuelve/valida al planificar (default retrocompatible
  cuando el LLM lo omite, truncado a presupuesto antes de resolver índices, filtrado de pasos vacíos antes
  de resolver índices).
- `apps/worker/tests/test_run_mission_handler.py`: `edecan_agents` fakeado vía monkeypatch de import
  (el `Orchestrator` real ya tiene su propia suite); filtrado por tenant, persistencia SQL de
  `agent_missions`/`agent_steps`, resume. **fase v5** suma: `RunDeps.insert_steps`/`save_mission(
  presupuesto=...)` persisten vía SQL real; round-trip completo de `depende_de` a través de `usage`
  (inserción de una misión nueva Y reanudación — un paso `pending` que nunca llegó a lanzarse conserva su
  `depende_de` original al reanudar).
- `apps/api/tests/test_missions_router.py`: flags/cuota, CRUD, confirm/cancel, aislamiento por
  tenant/usuario. **fase v6** suma la sección `GET /v1/missions/{id}/detalle`: flag/404/aislamiento
  (mismo criterio que `GET /{id}`), `presupuesto` con `replans_usados` pasando tal cual, `usage`/
  `started`/`finished` por paso (con y sin timing guardado), recorte de `resultado_truncado` (largo vs
  corto), agregados (`tokens_totales_por_tipo` sumando solo claves `*_tokens` numéricas — ignora bools y
  claves que no matchean —, `pasos_por_status` con las 6 claves siempre presentes), y que `GET /{id}`
  (sin `/detalle`) no cambió su contrato tras el refactor compartido (`_get_mission_and_steps`).

Ningún árbol de tests importa paquetes hermanos (`edecan_core`/`edecan_db`/`edecan_schemas` en el caso de
`packages/agents/tests/`) — todos usan fakes locales duck-typed (ARCHITECTURE.md §10.1).

## 10. Observabilidad de misiones (`GET /v1/missions/{id}/detalle`, fase v6)

Hasta este WP, `agent_steps.usage` (el uso del LLM por paso, jsonb desde v2) y
`agent_missions.presupuesto["replans_usados"]` (el contador de replanificaciones, desde `fase v5`) ya se
persistían — pero nada los exponía de forma útil: ni la API los enriquecía, ni la UI los mostraba. Este WP
cierra ese hueco con un endpoint puramente ADITIVO (no cambia `GET /v1/missions/{id}`, que sigue
devolviendo exactamente lo mismo que antes — ver `_get_mission_and_steps`, la función que ahora comparten
ambos para no duplicar el `SELECT` de `agent_steps`).

### 10.1 `GET /v1/missions/{id}/detalle`

Mismo Bearer + flag `agents.missions` + aislamiento tenant/usuario que el resto del router. Forma exacta
(`edecan_api.routers.missions.MissionDetalleOut`):

```jsonc
{
  "mission": { /* MissionOut de siempre — "presupuesto" YA incluye "replans_usados" si aplica */ },
  "steps": [
    {
      "seq": 1,
      "agente": "research",
      "instruccion": "...",
      "status": "done",
      "resultado_truncado": "...",   // cap 2000 caracteres + sufijo si se recortó
      "usage": { "input_tokens": 812, "output_tokens": 340, "started_at": "...", "finished_at": "..." },
      "started": "2026-07-09T12:00:00.123456+00:00",  // extraído de usage["started_at"], null si no hay
      "finished": "2026-07-09T12:00:04.987654+00:00"  // extraído de usage["finished_at"], null si no hay
    }
  ],
  "agregados": {
    "tokens_totales_por_tipo": { "input_tokens": 812, "output_tokens": 340 },
    "pasos_por_status": { "pending": 0, "running": 0, "waiting_confirmation": 0, "done": 1, "error": 0, "skipped": 0 }
  }
}
```

- **`resultado_truncado`**: `resultado` recortado a `RESULTADO_TRUNCADO_LIMITE` (2000 caracteres) con un
  sufijo si se recortó — pensado para un panel de UI, no para descargar el resultado íntegro (eso lo sigue
  dando `GET /{id}` sin recortar).
- **`usage`**: tal cual está guardado en la fila (puede traer tokens de `edecan_llm.base.Usage`,
  `pending_tool_call` mientras espera confirmación, y desde este WP `started_at`/`finished_at`) —
  `null` si el paso nunca corrió.
- **`started`/`finished`**: convenience fields extraídos de `usage` para que la UI no tenga que leer
  dentro de `usage` a mano — `null` para pasos que corrieron antes de este WP o que todavía no terminaron
  (ver §10.2).
- **`agregados.tokens_totales_por_tipo`**: suma, en Python sobre las filas ya traídas (sin SQL de
  agregación nuevo), cada clave de `usage` que termine en `_tokens` y tenga un valor numérico (no booleano)
  — no está hardcodeado a `input_tokens`/`output_tokens`, así que cualquier tipo de token nuevo que se
  sume en el futuro (p. ej. de caché) aparece solo, sin tocar este código.
- **`agregados.pasos_por_status`**: cuenta los pasos por cada uno de los 6 valores de
  `MISSION_STEP_STATUSES` (`edecan_schemas.missions`), siempre con las 6 claves presentes (en 0 si ningún
  paso está en ese estado).

### 10.2 `started_at`/`finished_at` por paso (`edecan_agents.orchestrator`, `fase v6`)

`agent_steps` no tiene columnas propias para el instante en que un paso empezó/terminó — este WP NO agrega
ninguna migración (prohibido tocar el esquema del linchpin), así que `Orchestrator._ejecutar_paso_de_ola`
captura `started_at` (ISO-8601 UTC) justo antes de correr el paso (tras adquirir el semáforo de
paralelismo, mide tiempo de EJECUCIÓN, no de espera en cola) y cada camino TERMINAL de un paso (`done`,
`error`, `waiting_confirmation`, incluidos timeout y excepción inesperada, y también la reanudación de un
paso aprobado) lo persiste junto a un `finished_at` fresco dentro del MISMO dict que ya se guardaba en
`agent_steps.usage` (`_timing_usage`, que mezcla en vez de reemplazar). La transición intermedia a
`status="running"` se deja SIN TOCAR a propósito: sigue significando "no toques `usage`", que es
precisamente lo que mantiene vivo el `depende_de` escondido ahí mientras el paso sigue `pending` (§4bis) —
solo el guardado TERMINAL de cada paso gana las dos claves nuevas. Detalle completo en la sección
`started_at`/`finished_at` del docstring de `orchestrator.py`.

Pasos que corrieron ANTES de este WP (o que todavía no terminaron) simplemente no tienen esas claves en su
`usage` — `started`/`finished` quedan `null` en la respuesta, nunca se inventa un valor.

### 10.3 Cómo leer `usage` (para quien consuma la API directo, sin la UI)

`usage` es un jsonb de forma abierta (`docs/roadmap.md`: `agent_steps.usage nullable`) — su contenido
depende de en qué quedó el paso:

| Estado del paso | Qué trae `usage` |
|---|---|
| `pending` (recién planificado, nunca corrió) | `null`, o (si viene de un replan/plan con `depende_de`) solo esa clave mientras sigue pendiente — ver §4bis. |
| `running` | Lo que sea que tuviera antes (la transición no lo toca). |
| `done` | Tokens de `edecan_llm.base.Usage` si el proveedor los reportó (`input_tokens`/`output_tokens`, puede venir vacío) + `started_at`/`finished_at`. |
| `waiting_confirmation` | `pending_tool_call: {id, name, args}` + `started_at`/`finished_at`. |
| `error`/`skipped` | Solo `started_at`/`finished_at` si el paso llegó a intentarse (`skipped` por un replan NUNCA llegó a correr → sin timing). |

### 10.4 Web (panel de detalle enriquecido)

`app/misiones/page.tsx` consume `getMissionDetalle` (no `getMission`) para su panel de detalle:
`MissionResumen` (`components/misiones/MissionResumen.tsx`) muestra los tokens totales, el desglose de
pasos por status y las replanificaciones usadas frente al presupuesto fijo del Orchestrator
(`MAX_REPLANS_PER_MISSION = 1`, duplicado como literal en `lib/api-misiones.ts` con un comentario — el
backend nunca lo expone en la API a propósito, `missions.py` nunca importa `edecan_agents`).
`StepTimeline` muestra, por paso: tokens (genérico, cualquier clave `*_tokens`), duración (`finished -
started`), el resultado colapsable (previsualización + "Ver más/Ver menos" si supera ~220 caracteres), y
una etiqueta "Ola N" cuando 2+ pasos comparten una ola real (`components/misiones/olas.ts::calcularOlas`,
reconstruida del lado del cliente a partir de `started`/`finished` que se solapan — no hay ninguna columna
que guarde el número de ola, así que esto es una aproximación honesta, no una copia exacta de
`_construir_olas`). Un botón "Refrescar" fuerza una recarga del detalle además del *polling* automático
mientras la misión sigue activa.

## 11. Plantillas de misión (`components/misiones/plantillas.ts`, fase v6)

Catálogo estático de 8 plantillas (principio de configuración de pocos clicks, `docs/roadmap.md`) para
que un usuario nuevo lance una misión útil sin tener que redactar el objetivo desde cero: investigación de
mercado, informe de competencia, plan de contenido semanal (siempre queda como borrador — nunca publica
nada por su cuenta), análisis de un archivo de datos ya subido, comparativa de precios de un producto,
borrador de contrato + análisis de riesgos (informativo, nunca asesoría legal vinculante), resumen de
salud del negocio, y plan de aprendizaje de un tema.

**Filosofía — honestas con las tools reales del repo**: cada plantilla describe algo que los perfiles de
`packages/agents/edecan_agents/profiles.py` pueden hacer de verdad hoy con sus `allowed_tools` reales
(`research`: `buscar_web`/`navegar_web`/`extraer_datos_web`; `data_analyst`: `analizar_tabla`/
`calculadora`; `content`: `generar_contenido`; `legal`: `analizar_contrato`/`generar_borrador_legal`;
`ceo`: `resumen_finanzas`/`estado_negocio`) — pueden crear contenido para cualquier red, incluido
LinkedIn. Publicar usa la integración disponible o una sesión local autorizada y conserva la
confirmación puntual de la herramienta. El `Orchestrator.plan()` sigue siendo quien decide qué perfil(es) usar para cada paso; las
plantillas no fuerzan un agente, solo redactan un objetivo claro y concreto para que el planificador lo
divida bien.

**Nota de alcance, corregida (2026-07-09)**: `packages/docanalysis/edecan_docanalysis/forecast.py`
tiene `predecir_serie`/`detectar_anomalias` implementadas (nombres pinned en `ARCHITECTURE.md` §14.e) y
**ya están conectadas** al `allowed_tools` de `data_analyst` (ver tabla §3.1 arriba) — la nota anterior,
que las daba como pendientes de conectar a un perfil, quedó desactualizada. La plantilla "Análisis de un
archivo de datos" sigue describiendo la proyección en lenguaje genérico por simplicidad de copy, pero el
sub-agente `data_analyst` sí puede invocar `predecir_serie`/`detectar_anomalias` directamente dentro de
una misión cuando el planificador lo considere necesario.

`PlantillasMisiones.tsx` (grid de cards, una por plantilla): un clic abre un mini-formulario de
placeholders para esa plantilla; "Usar esta plantilla" arma el `objetivo` (`renderMissionTemplate`,
sustituye cada `{{campo}}`) y lo deja listo en el textarea de "Nueva misión" para que el usuario lo
revise/edite antes de crear la misión — usa el `POST /v1/missions` EXISTENTE sin tocar su contrato, nunca
crea la misión directo desde la plantilla (mismo criterio de "revisar antes de enviar" que el resto del
producto).
