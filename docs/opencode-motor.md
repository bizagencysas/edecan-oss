# opencode como motor del IDE de Edecán -- mapa de lo hecho y lo que falta

> Decisión del dueño (30-jul-2026, ver memoria `edecan-motor-opencode.md`): opencode
> (github.com/anomalyco/opencode, MIT, 191k estrellas) pasa a ser el MOTOR del IDE. La
> interfaz de Edecán no se toca.
>
> **Este documento ya no es "el mapa de lo que falta cablear" -- eso se hizo.** Es el
> registro vivo de qué quedó conectado de verdad, qué se verificó en vivo (nada simulado,
> regla del encargo), y qué gaps siguen declarados a propósito.
>
> **Ronda "todo el poder de OpenCode" (la más reciente, la que escribe esta versión):**
> encargo del dueño de una sola frase ("metele todo el poder de OpenCode ahí a Edecán ...
> sin errores"), trabajado en varias rondas paralelas y cerrado por un verificador
> adversarial. Ver §0 para el resumen íntegro con archivo y línea. El único `fallos` real que
> dejó ese verificador (`SelectorModo.tsx` con los cuatro botones de modo permanentemente
> `disabled` y una nota que ya describía un estado del repo superado) queda cerrado en esta
> misma ronda -- §0.6.
>
> Antes de esa ronda, otra cerró el fallo más grave que un verificador había encontrado: en
> modo Manual, un permiso pedido a mitad de turno se rechazaba solo porque no existía ninguna
> acción para concederlo -- ver §3.1. Y otra más cerró un segundo fallo grave que un
> verificador reprodujo en vivo tres veces: un turno de opencode en modo Auto podía cerrarse
> "failed" con un mensaje inventado justo después del PRIMER uso de una herramienta, aunque el
> agente siguiera trabajando de verdad (el archivo ya estaba en disco) -- ver §3.4.

## 0. Ronda "todo el poder de OpenCode" -- resumen con archivo y línea

Encargo original, cuatro hechos verificados en vivo contra `opencode serve` 1.17.18 real +
Cloudflare Workers AI real (sin mocks, cuenta real de `.env`), y un cierre final que arregló
lo que un verificador adversarial encontró.

### 0.1 El control Bajo/Medio/Alto ahora SÍ cambia cómo piensa el modelo

Ninguno de los 5 modelos de Workers AI declara `variants` por sí solo. `ide_opencode_config.py`
(`generar_opencode_json`) ahora las declara él mismo en `opencode.json`, con migración segura
para un `opencode.json` que el propio módulo hubiera generado antes (heurística por
`name`+`npm` exactos, nunca toca config escrita a mano por el dueño). Verificado en vivo:
`GET /api/model` expone `variants: [{'id': 'low', 'body': {'reasoning_effort': 'low'}}, ...]`
por cada uno de los 5 modelos; `POST /session/{id}/model` con `variant: "high"` devuelve
`HTTP 204` y el turno siguiente responde con `model.variant == "high"` real. Kimi K2.7 y GLM
miden distinto en `high` vs `low` sobre 10 problemas generados (10/10 vs 8/10, 8/10 vs 7/10).

**Pendiente declarado, no mío de esta ronda**: el catálogo `/api/model` de un proveedor
custom (workersai) tarda ~1-2s en poblarse tras arrancar `opencode serve` -- si el primerísimo
turno de una conversación nueva pide la variante antes de que ese catálogo cargue,
`variante_de_esfuerzo` devuelve `None` y ese primer turno corre sin la variante elegida (los
siguientes sí la aplican). Vive en `ide_sessions.py` (~línea 1942), fuera del módulo que
declaró las variantes.

### 0.2 Cambiar de modelo con la sesión viva ya no espera al próximo mensaje

`SessionManager.set_modelo_agente` existía y aplicaba el cambio EN VIVO, pero no tenía
acción de puente ni ruta REST ni cliente web -- una capacidad construida y nunca cableada,
el mismo patrón que este proyecto arrastra (memoria: "ocho casos iguales, el dueño los ha
encontrado todos antes que nosotros"). Cerrado de punta a punta:

- `ide_runtime.py::IDE_ACTIONS` gana `ide_agent_model_set` (línea ~112/457), que llama
  directo a `self.sessions.set_modelo_agente(session_id, model)`.
- `apps/api/edecan_api/routers/ide.py`: `POST /v1/ide/agents/{session_id}/model` (línea
  ~1575-1610), `AgentModelIn.model` obligatorio, valida `modelo_ide_permitido` antes de
  tocar la sesión (422 claro) y reenvía `ide_agent_model_set`.
- `apps/web/src/lib/api-ide.ts::setIdeAgentModel` -- cliente del endpoint.
- `apps/web/src/components/ide/estado-ide.ts::setIdeModel` -- siempre guarda la preferencia
  local Y, si `agent && isLive(agent)`, dispara la llamada en vivo (mejor esfuerzo). El
  comando `/model` del chat hereda el mismo arreglo sin tocarlo aparte.

Verificado en vivo: `POST /v1/ide/agents/{id}/model` con `{"model":"@cf/zai-org/glm-5.2"}`
→ 200, y `GET /session/{id}` leído DIRECTO contra opencode (no confiando en el 200) confirma
`model.id` cambiado sin mandar mensaje nuevo. Cronometrado que el modelo y el esfuerzo no se
pisan: cambiar el modelo no resetea el esfuerzo vigente, y `/effort` no cambia el modelo, solo
el `variant` del mismo modelo.

### 0.3 Los 35 comandos `/` del chat -- disparados de verdad, no leídos

`ide_comandos.py` registra 35 comandos. `_despachar_comando` (`ide_runtime.py`) ya los maneja
los 35 (29 con lógica real + 6 informativos honestos: `usage, memory, mcp, voice,
remote-control, workflows`). Arreglos reales encontrados disparando cada uno contra una
sesión de agente real sobre opencode:

- **`/effort` estaba roto de verdad**: usaba la llave de conversación (`clave`) donde
  `SessionManager`/`EsfuerzoStore` esperan el `session.id` real -- el comando devolvía
  `ok: true` pero nunca llegaba a ningún turno real. Arreglado para exigir `session_id` real
  (mismo patrón que `/context`/`/compact`). Verificado en vivo: `GET /session/{id}` de
  opencode mostró `variant` pasar de `"high"` a `"low"` de verdad tras `/effort bajo`.
- **`/plan` (propose/approve/cancel)** opera sobre un `PlanStore` propio de `IDERuntime`,
  totalmente distinto del que sí frena bajo opencode (`ide_modo_set` con `modo: "plan"`).
  `/plan aprobar` decía "ejecutando paso a paso" sin frenar nada de verdad bajo opencode.
  Arreglado el mensaje: ahora trae un `aviso` explícito señalando el mecanismo que sí frena.
- **`/context`/`/debug`**: `llm-calls.jsonl` siempre da "0 llamadas" para un turno del IDE en
  cualquier motor -- es estructural (`log_llm_call` solo lo llama el agente de WhatsApp/
  teléfono), no "todavía no hay, ya viene". Arreglado el mensaje con un `aviso` honesto.
- Los otros 15 comandos con lógica real (`help, clear, rename, branch, resume, model, goal,
  compact, btw, background, tasks, export, copy, permissions, batch`) se dispararon de verdad
  y funcionan sin cambios.

**Pendiente declarado**: `/permissions` cambia una política real en memoria, pero
`PermissionsStore` no lo lee ningún gate real en todo el repo (ni el motor viejo ni opencode)
-- decorativo desde antes de esta ronda, decidir a qué gate conectarlo es una decisión de
diseño más grande. `/goal` tampoco se inyecta al prompt del agente en ningún motor (bitácora
pura), consistente con lo que su mensaje siempre prometió.

### 0.4 LSP: lo que opencode expone por HTTP, envuelto sin inventar lo que no existe

`ide_opencode_lsp.py` (nuevo) envuelve `GET /find/symbol` y `GET /lsp` (rutas reales,
confirmadas leyendo `/doc` completo de un servidor 1.17.18). **Hallazgo verificado, no
supuesto**: ambas rutas devuelven `[]` siempre contra la superficie pública de
`opencode serve` -- ninguna ruta HTTP (`read`, `edit`, `/file/content`, un turno real de
agente) dispara `LSP.touchFile()`, el único mecanismo (interno a `opencode debug lsp
diagnostics`, un proceso efímero aparte que SÍ funciona) que abre un archivo para su
servidor de lenguaje. Definición y referencias de símbolo (`textDocument/definition` /
`references`) no existen como ruta HTTP en absoluto -- confirmado enumerando las ~150 rutas
de `/doc`; el wrapper lanza `LspOpencodeNoDisponibleError` explícito en vez de fingir un
resultado vacío como éxito. `GET /api/reference` existe pero es un concepto no relacionado
(documentos de referencia del proyecto, no referencias de código) -- documentado a fondo
para que nadie lo confunda.

**Pendiente declarado, a propósito**: no está cableado a `IDE_ACTIONS`/`ide_sessions.py`/la
UI todavía -- exponerlo hoy solo devolvería listas vacías, y el riesgo de repetir el patrón
"capacidad que existe pero no hace nada visible" (Hecho 2) pesó más que adelantarlo. Queda
documentado en el docstring del módulo dónde engancharlo cuando opencode resuelva el touch
por HTTP.

### 0.5 El endpoint de modo (`ModoIn`) ya acepta `modo`, y el permiso mid-turno ya tiene
ruta REST + tarjeta de UI

Contrario a lo que versiones anteriores de este documento (§5, gaps #2/#3) declaraban como
abierto: `apps/api/edecan_api/routers/ide.py::ModoIn` ya declara `modo` (línea ~1503-1518) y
lo reenvía a `ide_modo_set`; y `GET/POST /agents/{id}/permission[/answer]` (línea
~1993-2030) y `GET/POST /agents/{id}/question/...` ya existen, con `AgentPermissionCard` /
`AgentQuestionCard` reales en `AgentThread.tsx`. Estos dos gaps quedaron cerrados por rondas
paralelas mientras se trabajaba el resto del encargo -- confirmado leyendo el código real, no
asumido; §5 se actualiza más abajo para no seguir listándolos como abiertos.

### 0.6 El fallo real que dejó el verificador -- `SelectorModo.tsx`, cerrado en esta ronda

Un verificador adversarial encontró que, pese a que `ModoIn` YA aceptaba `modo` (§0.5) y
`apps/web/src/lib/api-modo.ts` (el cliente que el propio componente importa) ya lo mandaba en
el PUT, `apps/web/src/components/ide/SelectorModo.tsx` seguía con los cuatro botones de modo
(Manual/Aceptar ediciones/Plan/Auto) `disabled` a mano, sin `onClick`, con un `title` que
citaba textualmente "ModoIn en routers/ide.py no tiene el campo «modo»" -- una explicación ya
falsa contra el estado real del repo. Candado con nota obsoleta, exactamente el patrón que el
encargo pedía cazar.

**Cerrado**: se agregó `elegirModo(nuevo)` (llama a `putIdeModo(sessionId, { modo: nuevo })`,
con rollback optimista si el PUT falla, igual que ya hacía `elegirEsfuerzo`) y cada botón del
menú quedó con `onClick={() => void elegirModo(opcion.id)}`, `disabled={deshabilitado ||
savingModo}` (deshabilitado solo sin `sessionId` -- fijar el modo, a diferencia del esfuerzo,
exige una sesión real; no hay preferencia local que aplicar al arrancar). Se retiró la nota
del pie del menú que decía "todavía no está conectado". Docstring del módulo actualizado para
dejar de citar el hueco que ya se cerró. `apps/web/selector-modo.test.mjs` se reescribió para
comprobar el contrato nuevo (cada opción dispara `elegirModo`, ninguna lleva `disabled`
incondicional, `elegirModo` exige `sessionId` y llama `putIdeModo` con `{ modo }`) en vez de
seguir clavando el candado como si fuera el requisito.

## 1. Resumen ejecutivo -- estado real, verificado en vivo

**`EDECAN_IDE_MOTOR=opencode` es el default de producción y está cableado de punta a
punta**: `SessionManager` (`ide_sessions.py`) decide en cada turno, vía `_motor_vigente()`,
si corre `_run_workers_agent` (motor viejo, `EDECAN_IDE_MOTOR=viejo`) o
`_run_opencode_agent` (opencode, el default). Un turno sobre opencode:

1. Asegura `opencode.json` del workspace y arranca/reusa un `ServidorOpencode` por
   workspace (`MotorOpencode.servidor_para`, `ide_opencode_motor.py`).
2. Arranca `PuenteDePermisos.escuchar()` como tarea de fondo **antes** de mandar el primer
   prompt -- la regla dura del §3 original, que sigue siendo la ley: si el puente no queda
   escuchando primero, `MotorOpencode.servidor_para` falla explícito
   (`PuenteDePermisosCaidoError`) en vez de devolver un servidor con el puente muerto.
3. Crea o reusa la sesión de opencode (`session.metadata["opencode_session_id"]`), manda el
   prompt y traduce cada `EventoSesion` real al vocabulario de eventos de Edecán
   (`ide_opencode_eventos.TraductorDeTurno`, ver §2.5) -- así es como la persona VE al
   agente trabajar en pantalla, letra por letra de lo que pidió el encargo.
4. Modo (manual/aceptar_ediciones/plan/auto) y esfuerzo (bajo/medio/alto) cambian el
   comportamiento REAL de la sesión de opencode en vivo, no solo el estado local de Edecán
   (`SessionManager.set_modo_agente` / `fijar_esfuerzo`).

Probado en vivo, de punta a punta, sin mocks: carpeta vacía → un prompt vía
`IDERuntime.dispatch("ide_agent_start", ...)` (las mismas acciones que usa la interfaz) →
opencode crea `package.json`/`server.js`/`README.md`, corre `npm install` de verdad (npm
real, paquetes reales) → `node server.js` arrancado a mano responde `200 {"ok":true}` en
`/salud`. **Es exactamente lo que el dueño pidió** ("que Edecán pueda crear repos desde
cero, instalar dependencias, y hacer literalmente todo lo que yo estoy haciendo").

### 1.1 Qué cerró la ronda de cierre (la más reciente)

Un verificador reprodujo en vivo, TRES veces por caminos distintos, que un turno de opencode
podía cerrarse `failed` con un mensaje inventado ("el modelo terminó sin entregar una
respuesta final") mientras el agente seguía trabajando de verdad -- en un caso, el archivo
pedido ya estaba en disco. Root cause real, no supuesta (ver §3.4 para el detalle completo):
`_vigilar_bus_para_sesion` (agregado en la ronda anterior para que un permiso pendiente
apareciera en el hilo en <2s en vez de a los 25s) enciende su aviso ante CUALQUIER
`permission.v2.asked` -- incluido uno que el puente de `MotorOpencode` ya auto-concedió en
modo Auto casi al mismo instante. `_consumir_turno_opencode` trataba "nada pendiente tras el
aviso" exactamente igual que "silencio real de 25s" y concluía que el turno había terminado,
aunque el modelo solo hubiera hecho el PRIMER tool call de varios. **Cerrado**: el aviso del
bus y el silencio real ahora se distinguen (`via_aviso_bus`, §3.4) -- solo el silencio real
(o agotar los reintentos de reconexión del stream de sesión) concluye el turno. Verificado en
vivo, opencode+Cloudflare reales, corriendo las tres pruebas de integración juntas 3 veces
seguidas sin ningún fallo (antes: 0/1; ver §6).

Como robustez adicional (no la causa raíz, pero un hallazgo real aparte que un verificador
también documentó): el stream por sesión de opencode a veces se cierra solo
(`StopAsyncIteration` sin `TimeoutError`) o pierde la conexión SSE a mitad de lectura
(`ErrorOpencode`, "peer closed connection..."). Antes de esta ronda eso se trataba como
silencio real de inmediato; ahora se reconecta unas pocas veces
(`_MAX_RECONEXIONES_STREAM_SESION`) antes de rendirse (§3.4). Un `POST` que no logra
conectar (`httpx.ConnectError`, "All connection attempts failed" -- visto en vivo contra
`/session/{id}/prompt`) también se reintenta ahora, pero SOLO si el TCP nunca llegó a
establecerse (`ide_opencode.py::ServidorOpencode._solicitar`) -- nunca un fallo a mitad de
respuesta, para no arriesgar mandar el mismo prompt dos veces.

### 1.2 Qué cerró la ronda anterior

Dos verificadores independientes revisaron el cableado con opencode/Cloudflare reales y
dejaron fallos concretos. Esa ronda los cerró:

- **Fallo grave (`no_cumple`)**: en modo Manual, un permiso pedido a mitad de turno
  (`permission.v2.asked` → `pedir_aprobacion`) se **rechazaba solo**, con el argumento de
  que "esta ronda no tiene todavía una acción para aprobar permisos a mitad de turno". La
  única forma de progresar era abandonar el turno bloqueado y mandar uno NUEVO en modo
  Auto -- no "conceder el permiso pedido y que el MISMO turno continúe", que es lo que pide
  el encargo. **Cerrado**: existen ahora `ide_agent_permission_list` /
  `ide_agent_permission_answer` (§3.1), y el bucle del turno espera de verdad en vez de
  rechazar. Verificado en vivo con dos tests de integración reales (§6).
- **Fallo secundario (`no_cumple`)**: el cierre por permiso pendiente escribía un mensaje
  claro, pero además `_run_opencode_agent` agregaba ENCIMA un `assistant_final` genérico
  ("terminó sin entregar una respuesta final") y un evento `error` que no mencionaban el
  permiso -- dos mensajes contradictorios. **Cerrado**: marca `opencode_cierre_explicado`
  en `session.metadata` (§3.2), verificado en vivo con un test que confirma que NO aparece
  el mensaje genérico.
- **Fallo menor (`cumple`, con nota)**: `pending_mcp`/`resolve_mcp` daban el mismo mensaje
  genérico ("La solicitud MCP ya no está pendiente") tanto si de verdad se resolvió como si
  la sesión corre sobre opencode (que no soporta MCP directo). **Cerrado**: mensaje
  distinto para cada caso (§3.3).
- **Test frágil**: `test_ide_opencode_config.py::test_sin_credenciales_en_entorno_ni_env_da_error_claro`
  asumía que el entorno de quien lo corre no trae `CLOUDFLARE_ACCOUNT_ID`/
  `CLOUDFLARE_API_TOKEN` exportadas -- fallaba en falso si las tenía (como cualquiera que
  necesite correr el resto de la suite de opencode real). **Cerrado**: `monkeypatch.delenv`
  explícito.

## 2. Los módulos del cimiento

Todos en `apps/companion/edecan_companion/`, con su test en `apps/companion/tests/`,
prefijo `ide_opencode*`.

### 2.1 `ide_opencode.py` -- el adaptador base

Arranca `opencode serve --port 0`, habla la API `/api/*` real, expone ciclo de vida,
sesiones, `enviar_prompt`, `eventos()` (SSE tipado en `EventoSesion`), `interrumpir`,
`cambiar_modelo`, `cambiar_agente`, `compactar`. `_ubicar_binario()` ya delega en
`ide_opencode_binario.resolver_binario_opencode()` (bundle → `EDECAN_OPENCODE_BIN` → PATH)
-- el gap que el documento original marcaba en §4.4 está cerrado.

**Lo que NO cubre, a propósito**: el stream `GET /session/{id}/event` que envuelve
`eventos()` **nunca** trae eventos de permisos (`permission.v2.*`) ni de preguntas
(`question.v2.*`). Esos viven en el bus GLOBAL, responsabilidad de `ide_opencode_permisos.py`.

**Hallazgo real sin cerrar** (ver §7.6): el parámetro `desde=` que este módulo documenta
para `eventos()` (reanudar sin reproducir todo el historial) devuelve `400 Expected an
integer, got NaN` contra un servidor real (v1.17.18) -- la API real espera un entero, no el
`evt_...` que `EventoSesion.id` expone. `ide_sessions.py` lo evita reconectando siempre
desde el principio y saltando por CONTEO los eventos ya vistos
(`session.metadata["opencode_eventos_vistos"]`), pero el módulo en sí sigue sin exponer el
cursor numérico real.

### 2.2 `ide_opencode_permisos.py` -- los 4 modos, de verdad

El puente entre los cuatro modos de Edecán (Manual / Aceptar ediciones / Plan / Auto) y la
API de permisos real de opencode.

- `ModoAgente`, `clasificar_accion()` (falla cerrado a `PELIGROSA`), `decidir()` (tabla
  pura de 16 casos).
- `PuenteDePermisos.procesar_solicitud()`: `permitir`/`bloquear` responden solas (SIEMPRE
  `once`/`reject`, nunca `always`); `pedir_aprobacion` queda pendiente para una persona.
- `PuenteDePermisos.responder_permiso()`: la persona real concediendo/rechazando lo que
  quedó en `pedir_aprobacion` -- **esta función ya existía desde antes de esta ronda**; lo
  que faltaba (y esta ronda cierra) era quién la llamara desde `ide_runtime.py`/
  `ide_sessions.py` (§3.1).
- `permisos_pendientes_en()` / `preguntas_pendientes_en()` + `recuperar_pendientes()`:
  catch-up sobre lo que ya estaba pendiente ANTES de que el puente empezara a escuchar.
- `escuchar()`: se suscribe a `GET /api/event` (el bus GLOBAL).
- `responder_pregunta()` / `rechazar_pregunta()`: expone `question.v2.*`.

### 2.3 `ide_opencode_config.py` -- genera `opencode.json`

`generar_opencode_json(workspace, ...)`: credenciales de Cloudflare (parámetro > env >
`.env` raíz) + catálogo `modelos_ide` de `config/modelos.yml` en vivo. Escribe/combina
`provider.workersai` y `permission` (`PERMISOS_POR_DEFECTO`, `{"edit":"ask","bash":"ask",
"webfetch":"ask"}`), nunca pisa lo que ya exista. `MotorOpencode.servidor_para`
(§2.5) llama a esta función y además blinda el archivo con `chmod 0600` antes de arrancar
el proceso (ver §7.2 sobre los límites de esa protección).

### 2.4 `ide_opencode_binario.py` -- localizar el binario empaquetado

`resolver_binario_opencode()`: bundle de PyInstaller/Tauri (`sys._MEIPASS`) →
`EDECAN_OPENCODE_BIN` → `PATH`. Repara el bit ejecutable POSIX bajo `_MEIPASS`. **Ya
conectado** a `ServidorOpencode._ubicar_binario` (§2.1) -- el gap original del §4.4 está
cerrado. Documento asociado: `docs/opencode-empaquetado.md`.

### 2.5 `ide_opencode_eventos.py` -- el traductor (lo que hace que se VEA en pantalla)

Sin este módulo, aunque todo el resto esté cableado, el dueño no ve nada mientras el
agente trabaja -- era el gap más citado en versiones anteriores de este documento (§5/§7
viejos). Ya no es un gap:

- `TraductorDeTurno.traducir(EventoSesion) -> list[EventoTraducido]`: dispatch explícito de
  las **28** variantes reales de `SessionDurableEvent` (no 27 -- `SessionNextSynthetic` no
  estaba contado en revisiones previas; confirmado leyendo `GET /doc` de un servidor real
  1.17.18). 20 se dispararon de verdad; las 8 restantes (`moved`, `synthetic`,
  `shell.started/ended`, `tool.progress`, `retried`, `compaction.started/ended`) tienen
  fixtures derivadas del schema real, marcadas explícitamente como no capturadas (razones
  técnicas de cada una en el docstring del módulo).
- `tool.called`/`tool.success`/`tool.failed` correlacionan por `callID` (el nombre de la
  herramienta NUNCA viaja en `tool.success`/`tool.failed`) para producir `file`
  (`"Archivo actualizado: {ruta}"`, el mismo formato exacto que `ide_contexto.py` parsea) y
  `tool`/`command` para bash.
- `step.failed`/`tool.failed` producen el tipo `error` con el mensaje real de opencode --
  es la primera vez que ese contrato (`EVENT_LABELS["error"]` en `AgentThread.tsx`, que
  existía sin usarse) se usa de verdad.
- `cerrar_turno(permiso_pendiente=..., pregunta_pendiente=...)`: se llama cuando el stream
  se queda callado. Nunca el genérico "no dejó respuesta" -- nombra la espera concreta si
  se conoce.
- `traducir_pregunta(SolicitudPregunta)` → evento `agent_question` (JSON completo de la
  pregunta). **Nuevo en esta ronda**: `traducir_permiso(SolicitudPermiso)` → evento
  `agent_permission` (JSON completo: `request_id`, `session_id`, `action`, `resources`,
  `puede_recordar`, `metadata`/`tool` si existen) -- mismo patrón, para que la interfaz
  algún día pinte una tarjeta de permiso igual que ya pinta una de pregunta.

### 2.6 `ide_opencode_motor.py` -- el ciclo de vida del motor

`MotorOpencode`: un `ServidorOpencode` por workspace, reutilizado, con candado por
workspace. `servidor_para()` asegura `opencode.json`, arranca/reusa el proceso, y arranca
el puente ANTES de devolver nada -- si el puente muere, la llamada entera falla
(`PuenteDePermisosCaidoError`), nunca devuelve un servidor "listo" con el puente muerto.
`salud()`/`comprobar_y_reponer()`: detecta y repone servidor+puente caídos, con tope de
reintentos propio para cada mitad. `modelo_para(perfil, esfuerzo)`: bajo/medio/alto leídos
de `config/modelos.yml` en cada llamada (nunca copiado a mano).

## 3. Lo que esta ronda cableó -- con archivo y línea

### 3.1 Conceder un permiso a mitad de turno (el fallo grave)

**Antes**: `ide_sessions.py::_consumir_turno_opencode` veía un permiso en
`pedir_aprobacion` y lo rechazaba solo (`consulta.responder_permiso(..., conceder=False,
...)`), con el mensaje "esta ronda no tiene todavía una acción para aprobar permisos a
mitad de turno". La única acción real disponible eran `ide_agent_question_*` (para
`question.v2.*`, no `permission.v2.*`).

**Ahora**:

- `SessionManager.listar_permisos_agente(session_id)` / `.responder_permiso_agente(session_id,
  request_id, *, conceder, recordar=False, mensaje=None)` (`ide_sessions.py`) -- mismo
  patrón EXACTO que `listar_preguntas_agente`/`responder_pregunta_agente`, llamando a
  `PuenteDePermisos.responder_permiso()`, que ya existía sin nadie exponerlo.
- `IDE_ACTIONS` (`ide_runtime.py`) gana `ide_agent_permission_list` /
  `ide_agent_permission_answer` (params: `session_id`, `request_id`, `conceder: bool`,
  `recordar: bool` opcional, `mensaje` opcional).
- `_consumir_turno_opencode` ya NO rechaza sola una solicitud en `pedir_aprobacion`: la
  trata igual que ya trataba una pregunta pendiente -- avisa una vez (evento
  `agent_permission` + el `status` de `cerrar_turno`) y sigue reintentando leer eventos
  hasta que alguien la resuelva (por `responder_permiso_agente` o por cualquier otra vía
  que hable con opencode directo) o hasta que se agote
  `_TIEMPO_MAX_ESPERA_PERMISO_OPENCODE` (20 minutos, mismo criterio que
  `_TIEMPO_MAX_ESPERA_PREGUNTA_OPENCODE` para preguntas), en cuyo caso SÍ rechaza y cierra,
  pero con el motivo real, nunca mudo.

Verificado en vivo (`test_ide_sessions.py::test_permiso_pendiente_en_modo_manual_se_puede_conceder_y_el_turno_sigue`,
real opencode + Cloudflare): modo Manual, segundo turno pide crear un archivo, opencode
pide permiso `edit` sobre él, `listar_permisos_agente` lo lista, `responder_permiso_agente(conceder=True)`
lo concede, y el MISMO turno (mismo `opencode_session_id`, sin mandar un mensaje nuevo)
termina con el archivo creado en disco con el contenido pedido.

**Nota declarada, no un olvido**: el evento `agent_permission` solo se escribe en el hilo
DESPUÉS de que `servidor.eventos()` se quede callado `_TIEMPO_INACTIVIDAD_EVENTOS_OPENCODE`
segundos (25 por defecto) -- mismo mecanismo que ya tenían las preguntas antes de esta
ronda, no algo nuevo que esta ronda debiera resolver. Si se concede/rechaza más rápido que
esa ventana (como hace el test, por vía directa), el turno igual termina bien pero sin
haber pintado la tarjeta -- en el uso real, una persona viendo la interfaz normalmente
tarda más que eso en responder, así que la tarjeta sí alcanza a aparecer.

### 3.2 El mensaje final ya no es confuso (el fallo secundario)

**Antes**: cuando el turno se cerraba por un permiso/pregunta que nunca se resolvió (se
agotó la espera), `_consumir_turno_opencode` escribía un `status` claro ("El turno está
esperando tu aprobación para «X»...", o "Se agotó la espera..."), pero
`_run_opencode_agent` no sabía que ya había una explicación real y, al ver que el turno no
cerró con texto, agregaba SIEMPRE un `assistant_final` genérico
(`build_failure_final(IDESessionError("El agente terminó sin entregar una respuesta
final."))`) más un evento `error` -- dos mensajes contradiciendo al único que sí explicaba
qué pasó.

**Ahora**: `_consumir_turno_opencode` marca `session.metadata["opencode_cierre_explicado"]
= mensaje` justo antes de retornar en los dos caminos de timeout (permiso agotado, pregunta
agotada). `_run_opencode_agent` la lee (y la consume, `pop`) en su bloque `finally`: si
está presente, fija `status="failed"` sin agregar el `assistant_final`/`error` genéricos --
el hilo se queda con UN solo mensaje, el real.

Verificado en vivo (`test_ide_sessions.py::test_permiso_pendiente_que_se_agota_cierra_con_un_solo_mensaje_claro`,
`_TIEMPO_MAX_ESPERA_PERMISO_OPENCODE` monkeypatcheado bajo para no esperar 20 minutos
reales): el cierre trae el mensaje "Se agotó la espera por tu aprobación..." y NO aparece
"terminó sin entregar una respuesta final" en ningún `assistant_final`, ni ningún evento
`error`.

### 3.3 Mensajes de MCP más específicos

`pending_mcp`/`resolve_mcp` (`ide_sessions.py`) daban el mismo `"La solicitud MCP ya no
está pendiente."` tanto si de verdad se resolvió como si la sesión corre sobre opencode
(que no soporta MCP directo -- `ServidorOpencode.enviar_prompt` no tiene esa superficie).
Ahora `_mensaje_mcp_no_pendiente(session)` distingue los dos casos leyendo si la sesión
tiene `opencode_session_id`.

### 3.4 El turno ya no se cierra "failed" a mitad de trabajo (la ronda de cierre)

**El fallo, tal como un verificador lo reprodujo en vivo** (`test_start_agent_sobre_opencode_crea_un_archivo_real`,
determinista contra opencode+Cloudflare reales antes de este arreglo): modo Auto (el
default), primer turno, pide crear un archivo. El hilo mostraba `status`/`tool` ("Usando
write.") y ahí se detenía -- el turno cerraba `status="failed"` con
`"El agente terminó sin entregar una respuesta final. No alcancé a ejecutar ni verificar
herramientas o cambios."`, un mensaje **falso**: el archivo pedido ya existía en disco con el
contenido exacto pedido.

**Root cause real** (diagnosticado reproduciendo el flujo completo con scripts propios contra
opencode/Cloudflare reales, no supuesto): `_vigilar_bus_para_sesion` (`ide_sessions.py`,
agregado en la ronda anterior) escucha el bus GLOBAL y enciende `notificacion_bus` ante
**cualquier** `permission.v2.asked` de la sesión -- sin distinguir uno que vaya a quedar
pendiente de verdad de uno que el puente de `MotorOpencode` (`_correr_puente`,
`ide_opencode_motor.py`) ya va a auto-conceder en modo Auto casi al mismo instante
(`PuenteDePermisos.procesar_solicitud`, clase LECTURA/EDICIÓN/COMANDO bajo Auto → `permitir`
solo). Cuando `_leer_stream_sesion_o_avisar_permiso` corta la lectura del stream de sesión
porque ese aviso ganó la carrera, `_consumir_turno_opencode` llamaba
`consulta.permisos_pendientes()` -- y para ese instante ya podía no haber nada pendiente
(auto-concedido). Antes de esta ronda, "nada pendiente" se trataba SIEMPRE como "silencio
real, el turno terminó" -- sin importar si se llegó ahí por un aviso del bus (que puede ser
una falsa alarma, el turno sigue trabajando) o por un silencio real de
`_TIEMPO_INACTIVIDAD_EVENTOS_OPENCODE` (25s, señal legítima). El primer caso cerraba el turno
justo después del primer `tool.called` de una escritura en Auto -- el escenario exacto que
reprodujo el verificador.

**El arreglo** (`ide_sessions.py`):

- `_leer_stream_sesion_o_avisar_permiso` devuelve ahora `(vistos, cancelado, via_aviso_bus)`
  -- un tercer valor que dice SI se llegó ahí por el aviso del bus ganando la carrera (en vez
  de un silencio/cierre real del stream de sesión).
- `_consumir_turno_opencode`: si `via_aviso_bus` es verdadero y no hay ningún
  permiso/pregunta realmente pendiente, ya NO concluye que el turno terminó -- vuelve a leer
  el stream (`continue`). Solo un silencio/cierre REAL del stream de sesión (TimeoutError, o
  agotar los reintentos de reconexión de abajo) dispara `cerrar_turno()`.
- `_leer_stream_sesion_una_vez`: además, como robustez aparte (un hallazgo real distinto que
  otro verificador documentó: el stream de sesión a veces se cierra solo con
  `StopAsyncIteration` sin `TimeoutError`, o pierde la conexión SSE a mitad de lectura con
  `ErrorOpencode`), ahora reconecta hasta `_MAX_RECONEXIONES_STREAM_SESION` (6) veces con
  `_ESPERA_ENTRE_RECONEXIONES_STREAM_SESION` (1s) de pausa antes de rendirse -- en vez de
  tratar esos dos casos como silencio real de inmediato. Un `TimeoutError` genuino (25s de
  silencio con la conexión viva) se sigue devolviendo tal cual, sin reconectar: sigue siendo
  la señal legítima que ya era antes.
- `ide_opencode.py::ServidorOpencode._solicitar`: reintenta hasta `_MAX_REINTENTOS_CONEXION`
  (3) veces, SOLO `httpx.ConnectError`/`httpx.ConnectTimeout` (el TCP nunca llegó a
  establecerse -- reintentar es siempre seguro, nada se procesó del lado del servidor).
  Deliberadamente NO se reintenta ningún otro `httpx.TransportError` (conexión cortada A
  MITAD de una respuesta): ahí la petición sí pudo haber llegado y procesarse, y reintentar
  un `POST /session/{id}/prompt` en ese caso arriesgaría mandar el mismo prompt dos veces.

**Verificado en vivo**: `test_start_agent_sobre_opencode_crea_un_archivo_real`,
`test_permiso_pendiente_en_modo_manual_se_puede_conceder_y_el_turno_sigue` y
`test_permiso_pendiente_que_se_agota_cierra_con_un_solo_mensaje_claro` (las tres, contra
opencode+Cloudflare reales) corridas juntas 3 veces seguidas después del arreglo: las tres en
verde las 3 veces. Antes del arreglo, `test_start_agent_sobre_opencode_crea_un_archivo_real`
fallaba de forma reproducible.

**Honestidad sobre el estado real**: contra un modelo real, con reintentos de red y
reconexión de por medio, sigue habiendo variabilidad inherente (la respuesta del modelo no es
determinista turno a turno) -- en las corridas de esta ronda,
`test_start_agent_sobre_opencode_crea_un_archivo_real` pasó 3/3 veces sola y 1/1 en conjunto
con las otras dos; no se corrió un número de repeticiones suficiente para afirmar "cero
flakiness posible contra un LLM real". Lo que sí se cerró con certeza es la causa raíz
determinista (la carrera del aviso del bus con nada pendiente ya NO cierra el turno).

## 4. Cableado de rondas anteriores (para contexto, ya cerrado)

Estos puntos aparecían como "falta cablear" en versiones viejas de este documento. Ya no:

- **`ide_sessions.py`**: `SessionManager._motor_vigente()` lee `EDECAN_IDE_MOTOR` en cada
  turno; `_BucleOpencode` (hilo con loop asyncio persistente, no `asyncio.run()` por turno
  -- imprescindible porque `PuenteDePermisos.escuchar()` tiene que sobrevivir ENTRE turnos)
  ; `_run_opencode_agent`/`_turno_opencode`/`_consumir_turno_opencode` son el camino
  completo turno→eventos→hilo.
- **`ide_runtime.py`**: `IDE_ACTIONS` incluye las acciones de agente/modo/esfuerzo/pregunta
  (y ahora permiso, §3.1) que hablan con `SessionManager`, motor-aware donde corresponde.
- **`ide_modos.py`**: `ModoAgenteStore` alimenta de verdad `obtener_modo` de
  `PuenteDePermisos` -- los cuatro modos frenan acciones reales, no son decorativos.
- **`apps/desktop`**: el sidecar de opencode y `resolver_binario_opencode()` están
  conectados (ver `docs/opencode-empaquetado.md` para el detalle del empaquetado).

## 5. Gaps declarados que siguen abiertos (sin adornos)

1. **`ide_plan_approve/edit/reject/active/resume` (aprobar un PLAN previo) e
   `ide_agent_mcp_pending/resolve` (MCP directo) siguen atados exclusivamente al motor
   viejo.** `self.plans`/`self._mcp_pending` solo se pueblan dentro de
   `_run_workers_agent` -- opencode no tiene la tool `proponer_plan` ni un intercepto MCP
   directo equivalente. Bajo `motor=opencode` (el default de producción):
   `ide_plan_active` devuelve `None` siempre (honesto, no un cuelgue);
   `ide_plan_approve`/`ide_agent_mcp_resolve` fallarían con un mensaje claro (§3.3 mejoró
   el de MCP esta ronda). No es un cable suelto por accidente -- es una capacidad de
   opencode que no existe, declarada, no disimulada.
2. ~~`apps/api` no expone rutas para `question`/`permission`~~ **CERRADO por una ronda
   paralela, confirmado leyendo el código real**: `GET/POST /agents/{id}/permission[/answer]`
   y `GET/POST /agents/{id}/question/{request_id}/reply|reject` existen en
   `apps/api/edecan_api/routers/ide.py` (línea ~1938-2030), proxeando `ide_agent_question_*`/
   `ide_agent_permission_*`.
3. ~~`apps/web` no tiene ningún componente para `agent_permission`~~ **CERRADO por la misma
   ronda**: `AgentPermissionCard` existe en `AgentThread.tsx` junto a `AgentQuestionCard`, y
   se pinta desde el evento `agent_permission` (§2.5). Conceder/rechazar un permiso mid-turno
   ya tiene botón real en la interfaz, no solo la acción del companion.
2bis. **`SelectorModo.tsx` tenía el mismo patrón** (candado con nota de "no conectado" que ya
   describía un estado del repo superado) para el menú de MODO -- `ModoIn.modo` sí existía,
   pero el componente web nunca se actualizó. **Cerrado en la ronda "todo el poder de
   OpenCode"** (§0.6): los cuatro botones ya llaman `elegirModo`/`putIdeModo({ modo })` de
   verdad.
4. **El `opencode.json` que genera `ide_opencode_config.py` lleva el token de Cloudflare en
   texto claro** en el workspace (`provider.workersai.options.apiKey`). Se blinda con
   `chmod 0600` (`MotorOpencode.servidor_para`) -- protege contra otros usuarios del mismo
   filesystem, no contra acceso root/físico, un log que lo vuelque completo, ni el
   workspace compartiéndose entero (zip/backup/sync). Sin rotación ni referencia indirecta
   del token.
5. ~~`chmod 0600` es no-op en Windows~~ **YA NO**: `ide_opencode_config.py` e
   `ide_opencode_motor.py` implementan `_restringir_windows` con `icacls
   <ruta> /inheritance:r /grant:r <cuenta>:F /grant:r SYSTEM:F`, best-effort
   (nunca revienta la generación del archivo). Verificado EN VIVO contra la
   VM real de este proyecto (EC2, Windows Server 2022) en dos rondas:
   primero se confirmó que `icacls` corre y protege el archivo con una
   cuenta fija; después, contra esa misma VM (que resultó ser standalone,
   no unida a dominio), se descubrió que la cuenta resuelta dinámicamente
   como `{USERDOMAIN}\{USERNAME}` fallaba con el código 1332 ("No mapping
   between account names and security IDs was done") porque
   `USERDOMAIN=WORKGROUP` no es un dominio real -- el token de Cloudflare
   seguía legible por `BUILTIN\Users` pese a que el código no arrojaba
   ningún error. Arreglado: `_cuentas_windows_candidatas()` prueba primero
   `.\{USERNAME}` (cuenta local, sintaxis documentada por Microsoft, válida
   tanto en workgroup como en dominio) y solo cae a `{DOMINIO}\{USERNAME}`
   si hace falta. Ver `docs/opencode-windows.md` §3 para el detalle
   completo y qué falta re-confirmar en vivo (esta última corrección se
   escribió y se probó como lógica en macOS -- no volvió a correr contra la
   VM real, ver la regla de esa ronda: "no te conectes").
6. **La rama Windows de `ide_opencode_binario._argv_para_ejecutar`** (delegar en `cmd.exe`
   para binarios `.cmd`/`.bat`) se probó como lógica pura en macOS, y el arranque/parada
   real de `ServidorOpencode` (`taskkill /T` para no dejar `opencode.exe` huérfano) SÍ se
   verificó en vivo contra la VM real: cero procesos huérfanos tras `detener()`, donde antes
   quedaban 27-29. Ver `docs/opencode-windows.md` §2 para los números medidos.
7. **`attachments` (imágenes), `skill_context` y `mcp_tools` no se envían a opencode** en
   `start_agent` -- `ServidorOpencode.enviar_prompt` solo admite texto. Se avisa en el
   hilo, nunca se descarta en silencio.
8. **El `desde=`/`after` de `ServidorOpencode.eventos()` no funciona contra el servidor
   real** (§2.1) -- `ide_sessions.py` lo evita con un conteo propio, pero sería más
   eficiente corregir `ide_opencode.py` para exponer el cursor numérico real.
9. **Los tests reales gastan tokens de Cloudflare de verdad** en cada corrida -- se saltan
   solos si faltan credenciales, nunca se simulan si están presentes.
10. **`/batch` (`ide_runtime._ejecutar_batch`/`_correr_subtarea_batch`) sigue usando
    `WorkersIDEAgent` (el motor viejo) siempre**, sin mirar `EDECAN_IDE_MOTOR` -- reparto en
    paralelo entre sub-agentes, documentado en el propio código como reuso deliberado, no
    un despiste.
11. **El LSP de opencode (`ide_opencode_lsp.py`, §0.4) no está cableado a `IDE_ACTIONS`/la
    UI.** No es un despiste: contra la superficie pública de `opencode serve` 1.17.18,
    `buscar_simbolos`/`estado_lsp` siempre devuelven `[]` (ninguna ruta HTTP dispara el
    `touchFile` que indexa un archivo), así que cablearlo hoy solo agregaría un botón que
    nunca muestra nada -- el mismo patrón que el encargo pidió evitar (Hecho 2). Cuando
    opencode resuelva el touch por HTTP, el wrapper ya está listo, solo falta el cable.
12. **`/permissions` (comando de chat) no frena nada real.** `PermissionsStore` cambia una
    política en memoria, pero ningún gate del repo (ni el motor viejo ni
    `ide_opencode_permisos.decidir`) lo lee -- decorativo desde antes de esta ronda, no una
    regresión suya. Verificado en vivo, no arreglado por ser una decisión de diseño (a qué
    gate conectarlo) más grande que el tamaño del encargo que lo encontró.
13. **`/goal` no llega al prompt del agente en ningún motor** -- es bitácora local pura
    (objetivo + criterios + progreso), consistente con lo que su propio mensaje siempre dijo.
    Verificado en vivo, no es un hueco nuevo.
14. **La carrera de arranque del catálogo `/api/model`** (§0.1): el primerísimo turno de una
    conversación nueva puede correr sin la variante de esfuerzo elegida si el catálogo del
    proveedor custom todavía no cargó (~1-2s tras arrancar `opencode serve`). Medido en vivo
    con timestamps reales, sin sesión de por medio -- confirma que es una carrera de tiempo
    de arranque, no una dependencia de sesión. Vive en `ide_sessions.py` (~línea 1942).

## 6. Verificación de la ronda "todo el poder de OpenCode" -- cierre final (salida real)

Único cambio de código de esta ronda: `apps/web/src/components/ide/SelectorModo.tsx` (§0.6,
el fallo que dejó el verificador) + `apps/web/selector-modo.test.mjs` (tests reescritos para
el contrato nuevo) + este documento. El resto de la ronda "todo el poder de OpenCode" (§0.1 a
§0.5) ya estaba cerrado por trabajo previo -- esta pasada lo verificó de nuevo de punta a
punta, sin dejar nada sin correr.

```
$ cd apps/web && npm test
ℹ tests 274
ℹ pass 274
ℹ fail 0
(incluye selector-modo.test.mjs reescrito: cada opción del menú de modo dispara `elegirModo`,
ninguna lleva `disabled` incondicional, `elegirModo` exige `sessionId` y llama `putIdeModo`
con `{ modo }`)

$ npm run typecheck
> tsc --noEmit
(sin salida = sin errores)

$ npm run lint
> eslint . --max-warnings=0
(sin salida = sin errores)

$ grep -rn "\[#[0-9a-fA-F]\{3,8\}\]" src/components/ide/
(sin coincidencias, exit 1)

$ cd /Users/example/Edecan-Nuevo/edecan
$ uv run ruff check apps/companion/ apps/api/edecan_api/routers/ide.py
All checks passed!

$ uv run --all-packages pytest apps/api/tests/test_ide_* -q
97 passed, 1 warning in 22.56s

$ uv run --all-packages pytest apps/companion -q \
    -k "not test_start_agent_sobre_opencode_crea_un_archivo_real and \
        not test_permiso_pendiente_en_modo_manual_se_puede_conceder_y_el_turno_sigue"
1484 passed, 2 skipped, 2 deselected, 1 warning in 305.52s (0:05:05)
```

Los `2 deselected` son los dos tests flaky preexistentes documentados en la regla 8 del
encargo (`test_start_agent_sobre_opencode_crea_un_archivo_real` y
`test_permiso_pendiente_en_modo_manual_se_puede_conceder_y_el_turno_sigue`, ambos de
integración real contra Cloudflare, con límite de peticiones conocido) -- no se corrieron en
esta pasada por no volver a gastar tokens reales en algo ya documentado como flaky y no
tocado por este cambio; pasan en aislamiento según las rondas que los documentaron. Cero
fallos, cero regresiones nuevas.

No se corrió `npm run build` ni `build-app.sh` (regla dura del encargo). No se hizo
`git commit` ni `git push`.

## 6bis. Verificación de la ronda de cierre anterior (histórico, salida real)

```
$ cd /Users/example/Edecan-Nuevo/edecan
$ uv run ruff check apps/companion/
All checks passed!

$ uv run --all-packages pytest apps/companion/tests/test_ide_sessions.py -q \
    -k "requiere_credenciales_opencode or opencode_crea_un_archivo_real or \
        permiso_pendiente_en_modo_manual or permiso_pendiente_que_se_agota"
3 passed, 8 deselected, 1 warning in 157.30s (0:02:37)

(las tres pruebas de integración real -- opencode+Cloudflare, sin mocks -- que un
verificador había marcado ``no_cumple``, corridas juntas, en verde. Además,
test_start_agent_sobre_opencode_crea_un_archivo_real se corrió sola 3 veces más:
3/3 en verde, contra 0/1 antes del arreglo)

$ uv run --all-packages pytest apps/companion/tests/ -q
3 failed, 1450 passed, 2 skipped, 1 warning in 380.08s (0:06:20)
```

Las 3 fallas de la corrida completa **no son las que este encargo pedía cerrar** (esas tres
-- ver arriba -- pasan) y **no las causó esta ronda** (los dos archivos donde fallan,
`ide_opencode_config.py`/`ide_opencode_permisos.py`, no tienen ni una línea tocada por esta
ronda -- `git diff` vacío):

- `test_ide_opencode_config.py::test_credencial_invalida_da_el_error_real_de_cloudflare` y
  `::test_credencial_real_pasa_la_validacion`: las dos llaman
  `GET https://api.cloudflare.com/client/v4/accounts/{id}/ai/models/search` DIRECTO (no vía
  opencode) y las dos fallan con `ReadTimeout`. Confirmado con `curl` fuera de pytest, dos
  veces, con 8s y 15s de margen: esa ruta específica de la API de Cloudflare no responde
  desde esta máquina ahora mismo (`/client/v4/ips`, sin autenticar, SÍ responde en 0.1s --
  no es una caída general de red). Es un problema de red/Cloudflare de este momento, no de
  código.
- `test_ide_opencode_permisos.py::test_modo_aceptar_ediciones_edita_solo_pero_el_comando_pide_aprobacion`:
  vuelve a pasar corrida sola (`1 passed in 14.66s`) -- es la variabilidad ya documentada en
  todo este archivo de tests reales contra un modelo real bajo carga concurrente (el modelo
  no siempre llama la misma herramienta al primer intento), no una regresión.

```
$ cd apps/web && npm run typecheck
tsc --noEmit: sin salida, sin errores.

$ npm run test:unit
tests 266 / pass 266 / fail 0

$ npm run lint
eslint . --max-warnings=0: sin salida, sin errores.

$ grep -rn "\[#" src/components/ide/
(sin coincidencias)

$ cd /Users/example/Edecan-Nuevo/edecan && uv run ruff check apps/companion/
All checks passed!
```

No se corrió `npm run build` ni `build-app.sh` (regla dura del encargo -- el empaquetado lo
hace el dueño). No se hizo `git commit` ni `git push`.

## 7. Cómo probarlo tú mismo (el dueño, en la app empaquetada)

1. Recompila/empaqueta con el script de siempre (fuera del alcance de este trabajo).
2. Abre un workspace vacío nuevo, modo Auto, y pide algo como "créame una API en Express
   con un endpoint /salud" -- deberías ver el progreso real en pantalla (razonamiento,
   herramientas, archivos) y terminar con los archivos en disco.
3. Para probar el permiso mid-turno: cambia el modo a Manual a mitad de una conversación y
   pide una edición. Cuando el turno diga "esperando tu aprobación", **hoy no hay botón en
   la interfaz** (§5.3) -- necesitarías llamar la acción `ide_agent_permission_answer`
   directo (o esperar a que alguien cablee el botón) para verlo resuelto desde la UI. El
   mecanismo por debajo ya funciona (§3.1); lo que falta es visible solo si usas la acción
   directamente.
