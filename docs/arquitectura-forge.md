# Forge, en una página

*Si solo lees una parte de este documento, que sea esta.*

---

## Qué vas a tener

Edecán seguirá siendo lo que es —chat, llamadas, reservas, redes sociales, negocio— y le añadimos
un **modo ingeniería**: le dices «construye esto» y trabaja horas o días. Escribe el código, lo
ejecuta, ve el error de verdad, lo arregla, lo prueba, abre el navegador para comprobar que la
pantalla se ve bien, hace commit y despliega. Tú entras a entender en diez segundos, corregir el
rumbo y aprobar lo importante.

Si apagas el ordenador a mitad, no se pierde nada. Si le preguntas qué hizo ayer a las cuatro, te
lo enseña con la evidencia. Si te quedas sin crédito en Cloudflare, cambias de modelo tocando un
archivo de configuración.

## Cómo se construye

| Fase | Qué pasa | Cuándo termina |
|---|---|---|
| **0. La sonda** | Medimos qué puede hacer Kimi K3 de verdad. Sin escribir el sistema | ~10 de agosto de 2026 |
| **0.5. Contratos** | Una semana cerrando cómo hablan entre sí las piezas. Sin código | ~17 de agosto |
| **1. Lo irreversible** | Se construye solo lo que no se puede añadir después, y **un agente cierra su primera tarea real, verificada** | ~finales de octubre |
| **2. Que sea útil** | Contexto, revisión de cambios a escala, plugins, jueces independientes | ~enero de 2027 |
| **3. Escala** | Varios agentes en paralelo, verificación visual, medición en bucle cerrado | ~abril de 2027 |

**Y el detalle que lo cambia todo**: desde el final de la fase 1, Forge se construye a sí mismo. El
primer trabajo del agente es la fase 2. Por eso la fase 1 se corta hasta el hueso: cada semana que
se le añade retrasa el momento en que el sistema empieza a acelerarse solo.

## Tres cosas que cambian respecto a lo que pensabas

1. **No hay siete runtimes, hay uno.** Chat, llamadas, redes e ingeniería no son módulos distintos:
   son **perfiles**, archivos de configuración sobre el mismo motor. Así, «publica en Instagram lo
   que acabas de desplegar» funciona sin integrar nada.
2. **Terminar no lo decide quien trabaja.** Cada tarea nace con un contrato de aceptación
   ejecutable, escrito antes de tocar código, y lo comprueba otro. Es la única defensa real contra
   el fallo más caro: el agente que modifica el test para que pase y se declara terminado.
3. **El humano no es un espectador con un botón.** Aprobar 400 archivos con un botón no es una
   decisión. La interfaz se optimiza para que corregir el rumbo cueste segundos.

## Lo que necesito de ti antes de empezar

1. **¿Qué es Acme 2.0?** Stack, tamaño, si tiene pruebas. Sobre eso se construye el banco de
   tareas con el que medimos todo.
2. **¿Tienes acceso a un segundo modelo, de otro proveedor?** Si no, los jueces que revisan el
   trabajo del agente arrancan siendo consultivos, no vinculantes. Se puede vivir con ello, pero
   hay que saberlo.
3. **¿Cuánto piensas gastar al mes en inferencia cuando se acabe el crédito?** Ese número decide
   cuánto esfuerzo merece la pena meter en abaratar cada tarea.

(Hay cuatro preguntas más, menos urgentes, en §14.)

## El riesgo número uno

Entre el 40 % y el 70 % del plan descansa sobre un número que nadie ha medido: **cuánto contexto
útil tiene Kimi K3 de verdad y cuán fiable es llamando herramientas.**

Si su contexto útil resulta ser 20k en vez de 48k, el trabajo de horas seguidas no funciona como
está diseñado y hay que rehacer tres bloques. Medirlo ahora cuesta **dos semanas y unos dos
dólares**. Descubrirlo en el mes nueve cuesta el proyecto.

Por eso la fase 0 va antes que todo, y por eso no empieza escribiendo el sistema.

## El resto del documento

Lo de abajo es el plano para quien lo construya: doce secciones con los contratos exactos, las
alternativas que se descartaron y por qué, y cómo se rompe cada pieza. **No hace falta que lo
leas.** Está ahí para que, cuando haya que decidir algo, la decisión ya esté tomada y argumentada.

---

# Forge — Runtime de Ingeniería de Software de Edecán

> Documento de arquitectura. Nivel: decisión de CTO.
> Estado: **propuesta para validar**. No hay código escrito ni comprometido.
> Fecha: 27 de julio de 2026 · Autor: arquitectura de Edecán · Versión 0.2
>
> **v0.2 — reencuadre**: este documento ya no describe «el IDE de Edecán». Describe
> el **runtime de ingeniería** de un asistente operativo general, y la frontera
> exacta entre lo que es de todos los dominios (el **Kernel de Edecán**) y lo que
> es solo de ingeniería (**Forge**).

---

## 0. Resumen ejecutivo

Edecán no es un IDE con chat. Edecán es un **asistente operativo general y
autónomo**: conversa, llama por teléfono, reserva vuelos y hoteles, gestiona
redes sociales, investiga, automatiza operaciones de negocio — y además
**construye software serio**. El listón declarado no es «generar snippets»: es
poder decir *«construye Acme 2.0»* y que el sistema lo haga en horas o días.

Este documento diseña ese último dominio, **Forge**, y —lo que resulta ser más
importante— la frontera que lo separa del sustrato compartido por todos los demás.

Forge no es un editor de texto con un chat al lado. Es un **sustrato de ejecución
durable para agentes de IA**: un sistema que sostiene procesos autónomos que
trabajan durante horas o días sobre proyectos reales, sobrevive a reinicios,
registra cada acción de forma auditable y permite que un humano entienda, corrija
y apruebe ese trabajo a un coste marginal de segundos.

La tesis económica del proyecto, y la razón por la que merece la pena construirlo:
**el andamiaje pesa más que el modelo**. Un modelo de primera línea que no puede
ejecutar su propio código, no ve el error real y supone que funciona, rinde peor
que un modelo intermedio con herramientas fiables, contexto correcto y
verificación independiente. Kimi K3 con buen andamiaje supera a un modelo mejor
sin él — y el andamiaje es precisamente la parte que no caduca cuando cambias de
modelo.

La tesis técnica es una sola frase: **el sistema es un log de eventos append-only;
todo lo demás —la interfaz, la observabilidad, el estado de un agente, el
historial, la auditoría, la capacidad de retroceder en el tiempo— es una
proyección derivada de ese log.** Si esa decisión es correcta, casi todo lo demás
se sigue. Si es incorrecta, el documento entero se cae.

El sistema se divide en dos planos que nunca comparten memoria:

- **Plano de control**: pequeño, durable, portable. Orquesta agentes, mantiene el
  journal, aplica presupuestos y políticas. Puede vivir en la máquina del usuario
  o en un Durable Object de Cloudflare sin cambiar una línea de los demás módulos.
- **Plano de datos**: pesado, desechable, pegado al disco donde vive el código.
  Ejecuta herramientas, procesos, compilaciones y navegadores dentro de sandboxes.

Y sobre ellos, **diez bloques** con contratos explícitos:

| # | Bloque | Dueño |
|---|---|---|
| A | Perfiles de agente: un runtime, muchos dominios | Kernel de Edecán |
| 1 | Kernel, Event Bus y Journal | Kernel de Edecán |
| 2 | Workspace Manager, VFS, indexado y escala | Forge |
| 3 | Context Engine y memoria de largo plazo | Kernel (política) + Forge (recuperación de código) |
| 4 | Tool ABI, plugins y MCP | Kernel de Edecán |
| 5 | Execution Engine, sandbox y seguridad | Kernel (capacidades) + Forge (sandbox de código) |
| 6 | Agent Runtime, planificación y multi-agente | Kernel de Edecán |
| 7 | Provider Layer, Workers AI/Kimi K3 y economía | Kernel de Edecán |
| 8 | UI y observabilidad | Kernel (journal) + Forge (diff, terminal, preview) |
| 9 | Verificación, aceptación y control de cambios | Forge (contrato genérico en el Kernel) |

Siete de los diez bloques son **del Kernel**, no de Forge. Eso no es un accidente
del reparto: es la medida de cuánto de «hacer un IDE de agentes» es en realidad
«hacer bien el asistente». Construir Forge hace mejor a Edecán entero.

**Lo que este documento defiende con más fuerza**: que la ventaja competitiva de
Edecán no estará en el editor ni en el modelo, sino en cuatro cosas que casi nadie
construye bien —el motor de contexto, la durabilidad del trabajo largo, la
verificación independiente, y el ancho de banda de corrección humana.

**Lo que este documento pide que recortes**: construir los diez bloques antes de
que un agente resuelva una tarea real sería el error más caro posible. El roadmap
(§17) está diseñado para que haya un agente cerrando una tarea de verdad —y
verificada— en la fase 1, con la mayoría de los bloques todavía a medio construir.

---

## 1. Crítica de tus premisas

Me pediste explícitamente que no aceptara tus decisiones solo porque las dijiste
tú. Aquí están las ocho donde no estoy de acuerdo, en orden de impacto.

### 1.1 «Los humanos solo observarán y aprobarán» — incompleto y peligroso

Es correcto que el humano no debe escribir el código. Es incorrecto deducir de
ahí que su papel sea pasivo, y esa deducción envenena todo el diseño de la UI.

El cuello de botella real de los agentes autónomos hoy no es su capacidad de
editar archivos: es la **tasa de corrección**. Un agente competente que trabaja
seis horas sin supervisión produce una fracción sustancial de trabajo inútil —no
porque no sepa programar, sino porque interpretó mal la intención en la hora uno
y construyó cinco horas encima de esa interpretación. El valor de un IDE de
agentes se mide en cuánto baja el coste de detectar y revertir esa desviación.

Si diseñas para «el humano solo aprueba», construyes una cola de aprobaciones con
botones de sí y no. Y el día que un agente te presente 400 archivos modificados,
descubrirás que un botón de «aprobar» sobre 400 archivos no es una decisión, es
una rendición.

**Reformulación que propongo**: el humano no escribe código, pero **es el sistema
de tipos del proyecto**. Aporta intención, restricciones y criterios de
aceptación; corrige trayectoria, no sintaxis. Por tanto la UI se optimiza para
**ancho de banda de corrección**: entender el estado en 10 segundos, intervenir
sin matar al agente, retroceder a un punto concreto del journal, y aprobar por
lotes semánticos en lugar de por archivo.

Consecuencia de diseño concreta: la interrupción y el retroceso son primitivas
del kernel, no funciones de la interfaz.

### 1.2 «Todo debe ser un plugin» — imposible tal como está formulado

Hay un núcleo irreductible que no puede ser plugin, porque **es el que define qué
es un plugin**. Si el sistema de archivos virtual fuese un plugin, no podrías
garantizar la invariante de que toda escritura pasa por él, ni auditarla, ni
aislarla. Si el journal fuese un plugin, un plugin defectuoso podría borrar la
evidencia de su propio fallo. Y hay un coste de rendimiento brutal: leer un
archivo ocurre miles de veces por sesión, y meter una frontera IPC en la
operación más caliente del sistema es un impuesto permanente.

**Regla que propongo**, y que resuelve el criterio sin ambigüedad:

> Si su fallo debe ser **contenido** → es un plugin.
> Si es la cosa que **contiene** los fallos → es núcleo.

Núcleo (confiable, en proceso, sin extensión): journal, bus de eventos, VFS,
gestión de procesos, motor de capacidades, registro de plugins, scheduler.
Todo lo demás —Docker, GitHub, Slack, AWS, Cloudflare, OCR, deploy, MCP,
proveedores, navegador, base de datos, incluso el propio agente— es plugin.

Eso es probablemente lo que querías decir. Pero escrito como «todo es un plugin»,
un implementador construiría un núcleo vacío que no puede garantizar nada.

### 1.3 «MCP como sistema de herramientas» — sí como frontera, no como ABI

MCP es la decisión correcta para **interoperar**: consumir el ecosistema de
servidores que ya existe y exponer Forge a otros clientes. Es la decisión
equivocada como ABI interno, por razones medibles: JSON-RPC sobre stdio serializa
cada resultado como texto JSON, no tiene un modelo de referencias de contenido
(un `readFile` de 2 MB viaja entero, escapado, en cada llamada), su cancelación
es débil, y su streaming no está pensado para el caudal de un `stdout` de
compilación.

**Propuesta**: ABI nativo de herramientas con referencias al CAS, streaming y
cancelación de primera clase; y un **adaptador MCP bidireccional** encima. MCP
entra y sale del sistema, pero no lo gobierna por dentro.

### 1.4 «Reconstruir todo desde cero» — correcto en el sustrato, suicida en el tooling

Estoy de acuerdo en tirar el IDE actual: los ~3.300 líneas de
`apps/companion/edecan_companion/ide_*.py` son un puente remoto de
archivos/terminal/git para el móvil. Es un producto distinto y no es cimiento de
nada de esto.

No estoy de acuerdo en reconstruir desde cero el editor de texto, el resaltado de
sintaxis, el parser de lenguajes, el emulador de terminal, el cliente Git ni el
motor de diffs. Eso son entre cinco y quince años-persona de trabajo ya resuelto
(CodeMirror 6, tree-sitter, xterm.js, libgit2, LSP). Reescribirlos no aporta
ninguna ventaja para agentes y consume exactamente el tiempo que necesitas para
construir lo que sí es diferencial.

**La línea**: construimos el sustrato de agentes desde cero; integramos el
tooling de edición y análisis. Todo lo integrado entra detrás de una interfaz
propia, para que sea reemplazable.

### 1.5 «Una arquitectura capaz de soportar los próximos cinco años» — objetivo mal planteado

Ninguna arquitectura de sistemas de IA sobrevive cinco años intacta; el campo se
reescribe cada doce meses. Diseñar para que el sistema no cambie produce
abstracciones especulativas que envejecen peor que el código simple.

**Lo que sí sobrevive cinco años son los datos y las fronteras.** El journal, el
CAS y los esquemas de evento pueden durar una década; los módulos que los
producen y consumen no.

**Reformulación del objetivo**: no un sistema que no cambie, sino un sistema
donde **cualquier módulo pueda tirarse y reescribirse en dos semanas sin migrar
un solo byte de datos**. Eso es un criterio verificable, y es el que uso en todo
el documento. El activo permanente de Forge es su log, no su código.

### 1.6 «Decenas de agentes» — soportarlo sí, fomentarlo no

Hay que diseñar los contratos para N agentes desde el día cero: eso es correcto y
barato si se hace al principio, y carísimo si se retrofitea. Pero el producto no
debe empujar hacia N alto por defecto.

La razón es empírica: sobre un **mismo** proyecto, más de tres a cinco agentes
concurrentes generan más conflictos de fusión, trabajo duplicado y contexto
divergente que valor marginal. Donde N agentes ganan de verdad es en trabajo
**particionable** —migrar 500 archivos, auditar un repo, generar pruebas por
módulo— donde el particionado es explícito y los conjuntos de archivos son
disjuntos.

**Consecuencia de diseño**: el sistema soporta N; la política por defecto exige
que el paralelismo declare su partición. Un agente que no puede declarar qué
archivos va a tocar no puede ejecutarse en paralelo con otro.

### 1.7 El riesgo que no mencionaste: los 50.000 USD son una trampa de diseño

Un crédito grande en un proveedor es el incentivo más eficaz que existe para
acoplarse a él sin darse cuenta. No por una decisión consciente, sino por
cincuenta decisiones pequeñas —«esto lo hacemos con Vectorize», «el estado lo
guardamos en el Durable Object», «total, el modelo soporta esto»— que
individualmente son razonables y colectivamente son una migración de seis meses.

La buena intención no basta. **La disciplina correcta es estructural**: el
sistema se desarrolla y se prueba en CI contra el proveedor más débil disponible
—un modelo local vía Ollama, sin caché de prefijo, sin tool-calling nativo, con
ventana pequeña— de modo que el acoplamiento sea **imposible por construcción**.
Y se añade una métrica dura al conjunto de pruebas: *tiempo de cambio de
proveedor*, medido como una prueba automatizada que cambia la configuración y
verifica que la suite pasa igual.

Añado un segundo riesgo económico: 50.000 USD en créditos de inferencia es
enormemente más de lo que puede consumir un usuario, y muchísimo menos de lo que
consume un equipo con veinte agentes corriendo en bucle. La diferencia entre
esos dos escenarios son dos órdenes de magnitud, y depende casi por completo del
motor de contexto. El bloque 3 y el bloque 7 son, económicamente, el mismo bloque.

### 1.8 «No quiero hacks ni soluciones temporales» — de acuerdo, con una corrección

Comparto el espíritu: nada de parches que se conviertan en cimiento. Pero la
deuda técnica no se evita diseñando más por adelantado. Se evita **no
construyendo cosas todavía**.

El riesgo más grande de este documento es que se implemente entero antes de que
un agente resuelva una sola tarea real. Un sistema con ocho bloques perfectos y
cero tareas completadas es deuda pura: has invertido meses en suposiciones que
nunca se contrastaron.

Por eso el roadmap (§17) no está ordenado por elegancia arquitectónica, sino por
**el orden en que se descubren los errores de diseño**.

### 1.9 «Un runtime por dominio» — el eje de descomposición equivocado

La propuesta de partir Edecán en Chat Runtime, Voice Runtime, Browser & Booking
Runtime, Social Media Runtime, Business Automation Runtime, Research Runtime y
Software Engineering Runtime es intuitiva y es una trampa.

Si son módulos de código, pasan tres cosas, todas malas:

1. **Arreglas el mismo bug siete veces.** Reanudar tras un reinicio, cancelar a
   mitad de una herramienta, redactar un secreto en streaming, cobrar tokens al
   presupuesto correcto: son problemas idénticos en los siete. Siete
   implementaciones divergen en semanas.
2. **Las tareas reales cruzan dominios.** «Publica en Instagram el cambio que
   acabas de desplegar.» «Llama al proveedor, actualiza el CRM y abre el PR.» El
   community manager necesita editar un repo porque el sitio web *es* un repo.
   Con N runtimes, cada cruce es un proyecto de integración.
3. **La propia propuesta se refuta sola.** Dice que los siete comparten
   identidad, memoria, permisos, journal, agenda, event bus, secretos,
   proveedores y observabilidad. Si comparten *todo eso*, lo que queda distinto
   es un catálogo de herramientas y unas políticas. Eso no es un runtime: **es
   configuración**.

**Corrección**: existe **un solo runtime de agente**. Lo que varía es un
**Perfil de Agente**: un objeto declarativo —datos, no código— que ata catálogo
de herramientas, política de contexto, política de modelo (por *requisito de
capacidad*, nunca por nombre de modelo), política de permisos y aprobación,
contrato de aceptación por defecto, presupuestos, horizonte temporal y topología
de equipo.

Dos consecuencias que valen el cambio por sí solas:

- Añadir «modo community manager» es **escribir un archivo**, no compilar código.
- Los perfiles **se componen**. Una tarea que cruza dominios es la unión de dos
  catálogos bajo la intersección de sus políticas de permiso. No hay integración
  que hacer: hay un álgebra que aplicar.

Y una consecuencia operativa que importa para tus 50k: el mismo runtime tiene que
servir para un turno de 800 ms («escríbele un mensaje a este cliente») y para una
sesión de tres días. Eso no se resuelve con runtimes distintos, se resuelve con
**niveles de esfuerzo** dentro del mismo runtime —L0 reflejo, L1 turno con
herramientas, L2 tarea verificada, L3 proyecto multi-sesión— donde cada nivel
enciende planificación, checkpointing, verificación y subagentes de forma
incremental. Un chat rápido que arrastra el aparato de un proyecto de tres días
es un asistente lento y caro.

### 1.10 La verificación no es una capa: es un contrato que nace con la tarea

«El mismo agente que escribe no debe ser el único que decide que terminó» es
correcto y es la mejor idea que ha entrado en este diseño. Pero dibujarla como
una *capa* debajo de las herramientas (Tools → Verification → Journal) implica
que verificar es algo que ocurre **al final**. Verificar al final es una suite de
tests: encuentra errores tarde, cuando ya hay cinco horas construidas encima.

**Corrección**: cada tarea nace con un **Contrato de Aceptación** ejecutable,
escrito *antes* de tocar código, y evaluado por un actor distinto del que ejecutó.
Declara qué comandos deben pasar, qué invariantes no pueden romperse, qué
evidencia visual se exige y qué criterios necesitan un juez. La diferencia con
una capa de QA es la misma que hay entre encontrar errores y **hacer imposible
declararse terminado sin haberlos resuelto**.

Es también la única defensa real contra el modo de fallo más caro de un agente
autónomo: el auto-engaño. Modificar el test para que pase, marcarlo como skip,
capturar la excepción y devolver vacío, mockear lo que debía ser real, declarar
éxito sin haber ejecutado nada. Ningún prompt evita eso. Un contrato que el
agente no puede editar, sí.

### 1.11 En qué gastar los 50.000 dólares

Estoy de acuerdo con matizar mi propia advertencia de §1.7: el crédito es una
oportunidad extraordinaria y minimizarla sería tan tonto como acoplarse a ella.
La regla es «aprovechar Cloudflare al máximo sin volver a Edecán dependiente de
Cloudflare», y esa regla se hace cumplir con CI contra el proveedor más débil, no
con buenas intenciones.

Pero añado algo que falta en la discusión: **el crédito no debe financiar usar el
producto. Debe financiar descubrir cómo debe ser el producto.** Concretamente, en
las cinco cosas que normalmente son demasiado caras para hacerlas:

1. **Verificación redundante**: tres verificadores independientes por tarea en
   lugar de uno. Normalmente prohibitivo; aquí es el laboratorio que te dice
   cuánto vale realmente la verificación independiente.
2. **Generación N-way con juez**: tres soluciones distintas a un problema de
   arquitectura y un panel que las puntúa. Solo es viable con inferencia barata.
3. **Horizonte largo**: dejar agentes corriendo doce horas para ver *dónde* se
   rompen. Esos datos casi nadie los tiene, y son exactamente lo que decide el
   diseño del checkpointing y del motor de contexto.
4. **Un conjunto de evaluación propio**: convertir sesiones reales en casos
   reproducibles. **Este es el activo que sobrevive al crédito** —cuando se
   acabe, seguirás teniendo el dataset que te dice si un cambio mejora o empeora
   el sistema.
5. **Búsqueda de política**: barrer variantes del motor de contexto y del router
   contra ese dataset, en vez de elegir por intuición.

Nada de eso te ata a Cloudflare. Todo eso te deja, el día que se acabe el
crédito, con un sistema medido en vez de un sistema opinado.

---

## 2. Qué se conserva del Edecán actual y qué se tira

Se tira, sin sustituto directo:

| Componente actual | Por qué se tira |
|---|---|
| `apps/companion/edecan_companion/ide_*.py` (~1.760 líneas) | Es un puente RPC de archivos/terminal/git para el móvil. No tiene journal, ni sandbox, ni contexto, ni concurrencia. |
| `apps/api/edecan_api/routers/ide.py` (786 líneas) | API de superficie humana, con estado en el proceso y sesiones no durables. |
| `apps/web/src/components/ide/*` (473 líneas) | Editor + árbol + terminal pensados para un humano que edita. |

Se conserva como **base conceptual** —no como código intacto:

| Activo | Rol en Forge |
|---|---|
| `packages/llm/edecan_llm/base.py` | El contrato `LLMProvider` con bloques de contenido estilo Anthropic ya es la abstracción correcta. Forge lo extiende (cancelación, capacidades, caché de prefijo, coste real) en vez de reinventarlo. |
| `packages/llm/` (adaptadores) | Anthropic, OpenAI-compat, Bedrock, Vertex, Ollama ya existen y son reusables tras el cambio de contrato. |
| `packages/mcp/` | Cliente MCP existente → se convierte en el adaptador MCP del Tool ABI. |
| `packages/core/edecan_core/memory/` | Embedders y almacenamiento vectorial reutilizables por el Context Engine. |
| Multi-tenancy con RLS en Postgres | El modelo de aislamiento por tenant se hereda tal cual; Forge no inventa uno nuevo. |
| Estilo `ARCHITECTURE.md` de contratos pinneados | Se mantiene: los contratos de Forge se fijan con la misma disciplina. |

Se tira por incompatibilidad de contrato, aunque duela:

| Activo | Por qué |
|---|---|
| `packages/core/edecan_core/tools/base.py` | El `Tool` actual es petición-respuesta sin streaming, sin cancelación, sin deadline, sin referencias de contenido y con `dangerous: bool` como único modelo de riesgo. Es el contrato correcto para un asistente conversacional y el equivocado para un motor de ejecución. Las herramientas concretas de `edecan_toolkit` se migran al nuevo ABI; el ABI no se dobla para acomodarlas. |

---

## 3. La tesis

> **Forge es un log de eventos con un compilador de intención encima.**

Un agente no «tiene estado»: su estado es una proyección del log. Una sesión no
«se guarda»: ya está guardada, porque nunca existió fuera del log. La interfaz no
«se sincroniza»: se suscribe. El replay no es una función, es la operación normal
del sistema ejecutada sobre eventos pasados.

De esa decisión se derivan, casi mecánicamente: la durabilidad frente a
reinicios, la observabilidad total sin instrumentación ad hoc, el time-travel, el
fork de sesiones, la auditoría verificable y la capacidad de reconstruir cualquier
vista sin migraciones.

Lo que **cuesta** esa decisión, y hay que pagarlo con los ojos abiertos: el
volumen de eventos es grande, exige compactación y proyecciones materializadas,
introduce eventual consistency entre el log y el disco real, y obliga a versionar
esquemas de evento durante años. El bloque 1 se dedica íntegramente a que ese
coste sea aceptable.

---

## 4. Las diez invariantes

Estas son las reglas que ningún módulo puede violar. Un cambio que rompa una de
ellas no es un cambio: es un rediseño y exige revisar este documento.

1. **No es un editor.** Forge es un sustrato de ejecución para agentes. El editor
   es un cliente más del núcleo, no el centro.
2. **El journal es la única fuente de verdad.** Todo estado autoritativo vive en
   un log append-only. Todo lo demás es proyección.
3. **Separación de planos.** Control (durable, pequeño, portable) y datos
   (pesado, desechable, pegado al disco) se comunican solo por eventos y
   referencias de contenido.
4. **Contenido direccionado por hash.** El journal transporta referencias, nunca
   payloads grandes.
5. **Workspaces copy-on-write.** El aislamiento entre agentes es el default; la
   fusión es explícita, observable y reversible.
6. **Todo es una herramienta; toda herramienta la provee un plugin** —salvo el
   núcleo confiable definido en §1.2. MCP es un adaptador, no el ABI.
7. **Capacidades, no permisos ambientales.** Ningún actor tiene autoridad
   implícita. El sandbox impone; no se confía en la buena conducta del modelo.
8. **Todo es cancelable, reanudable y con presupuesto.** Toda unidad de trabajo
   tiene deadline, presupuesto y punto de checkpoint.
9. **Multi-agente en el contrato desde el día cero**, aunque la fase 1 corra un
   solo agente. Pasar de 1 a 20 no debe reescribir ninguna interfaz.
10. **Cero acoplamiento directo entre módulos.** Se conocen por contrato de
    eventos e interfaces inyectadas. Prohibido importar el módulo concreto de
    otro plano.

Y una regla de proceso, que no es arquitectura pero gobierna el resto:

> **Regla de portabilidad**: el sistema se desarrolla y se prueba contra el
> proveedor de modelo más débil disponible. Cualquier capacidad superior
> (caché de prefijo, tool-calling nativo, ventana larga, visión) se detecta y se
> aprovecha, pero jamás se asume.

---

## 5. Mapa global

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  CLIENTES  (proyecciones del journal, sin estado propio)                  │
│  Web (Next.js) · Escritorio · Móvil (aprobar/observar) · CLI · API/MCP    │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │  suscripción por cursor (SSE/WS)
┌───────────────────────────────▼──────────────────────────────────────────┐
│  PLANO DE CONTROL   — durable · pequeño · portable · sin acceso al disco  │
│                                                                          │
│   ┌────────────┐  ┌────────────┐  ┌───────────────┐  ┌────────────────┐  │
│   │  JOURNAL   │  │ EVENT BUS  │  │ AGENT RUNTIME │  │  SCHEDULER +   │  │
│   │ append-only│◄─┤ pub/sub    │◄─┤ máquina de    │◄─┤  PRESUPUESTOS  │  │
│   │ hash chain │  │ patrones   │  │ estados       │  │  y política    │  │
│   └─────┬──────┘  └────────────┘  └───────┬───────┘  └────────────────┘  │
│         │                                 │                              │
│   ┌─────▼──────────┐   ┌──────────────────▼───────┐   ┌───────────────┐  │
│   │  PROYECCIONES  │   │     CONTEXT ENGINE       │   │  CAPACIDADES  │  │
│   │ vistas materia-│   │ selección · compresión · │   │  y aprobación │  │
│   │ lizadas        │   │ memoria · presupuesto    │   │               │  │
│   └────────────────┘   └──────────────────────────┘   └───────────────┘  │
└───────────────┬──────────────────────────────────┬───────────────────────┘
                │ eventos + refs de contenido      │ interfaces de proveedor
┌───────────────▼──────────────────┐   ┌───────────▼──────────────────────┐
│  PLANO DE DATOS  — pesado        │   │  PROVIDER LAYER                  │
│  desechable · pegado al disco    │   │  LLM · Vision · Embedding · OCR  │
│                                  │   │  Speech · Image · Rerank · Vector│
│  ┌────────┐ ┌────────┐ ┌───────┐ │   │  ┌────────────────────────────┐  │
│  │  VFS   │ │ EXEC   │ │ CAS   │ │   │  │ descriptor de capacidades  │  │
│  │ CoW +  │ │ ENGINE │ │ blobs │ │   │  │ + shims de degradación     │  │
│  │ índices│ │+sandbox│ │ hash  │ │   │  └────────────────────────────┘  │
│  └────────┘ └───┬────┘ └───────┘ │   │  Workers AI/Kimi · Anthropic ·   │
│                 │                │   │  OpenAI · Bedrock · Vertex ·     │
│      ┌──────────▼──────────┐     │   │  Ollama (referencia de CI)       │
│      │  PLUGINS (aislados) │     │   └──────────────────────────────────┘
│      │ git·docker·browser· │     │
│      │ db·deploy·ocr·MCP…  │     │
│      └─────────────────────┘     │
└──────────────────────────────────┘
```

Tres reglas de lectura del mapa:

1. **Ninguna flecha sube directamente del plano de datos a los clientes.** Todo lo
   que el humano ve pasó primero por el journal. Esto es lo que hace que la
   observabilidad sea total por construcción y no por instrumentación.
2. **El Agent Runtime no toca el disco.** Pide; el plano de datos ejecuta. Por eso
   un agente puede migrar de host o sobrevivir a un reinicio.
3. **El Provider Layer cuelga del plano de control, no del agente.** El agente
   no sabe qué modelo lo está pensando.

---

## A. Perfiles de agente: un runtime, muchos dominios

Seis correcciones estructurales respecto al borrador anterior, porque cada una era un fallo de
diseño y no de redacción:

1. **La autoridad sale del perfil.** El borrador afirmaba "el enrutado nunca decide autoridad" y a
   la vez ponía `autonomy: 3` dentro de un perfil enrutable. Era falso. Ahora la autoridad vive en
   `SessionAuthority` (grants × superficie × ajuste del usuario) y el perfil sólo la estrecha.
2. **El presupuesto sale del perfil.** `budget = min por eje` producía `min($2, $40) = $2` y mataba
   la composición. El presupuesto es una **asignación** del invocante, no una propiedad del dominio.
3. **La persona sale del perfil.** Se resuelve por emisión, no por sesión.
4. **El perfil se parte en núcleo obligatorio (7 campos) + overlays con presets por horizonte.** El
   borrador exigía ~40 campos al autor de cada dominio; eso garantiza copiar-pegar y deriva.
5. **`min_quality_tier` (1..5) se elimina.** Era un número inventado. Se sustituye por umbrales de
   aprobación sobre suites de eval nombradas, con estado `unverified` explícito.
6. **`extends` y `⊕` son el mismo operador.** Dos mecanismos de herencia solapados es deuda de fase
   1 que se paga en fase 3 con diamantes irresolubles.

### A.1 La frontera Kernel/dominio, con un criterio verificable

Dos pruebas ejecutables en una revisión de PR, sin discutir filosofía:

- **Prueba de divergencia observable.** Al Kernel si y sólo si (a) hoy lo necesitan ≥2 dominios con
  semántica *idéntica* y (b) la divergencia se percibiría como **incoherencia del asistente**, no
  como diferencia legítima. "Recordó tu preferencia en el chat pero no en la llamada" → Kernel. "El
  sandbox de código no tiene red y el navegador sí" → dominio.
- **Prueba del borrado.** Si desinstalas el dominio, ¿el journal y los artefactos siguen teniendo
  sentido? Sí → Kernel. No → dominio.

| Kernel | Dominio |
|---|---|
| Journal, event bus, content store por hash | Workspace/VFS y overlays COW (Forge) |
| Identidad, tenant, RLS, grants, vault | Sandbox de procesos y su política de red (Forge) |
| **Registro de Perfiles, composición, router** | Índices de código, LSP, grafo de símbolos (Forge) |
| Runtime de agente único (bucle, resume, cancel, checkpoint, barrera de transición) | Catálogos de herramientas por dominio |
| Registro de modelos y resolución por capacidad | Verificación de software; validación de post; objetivo de llamada |
| **Clasificación de efectos y `ApprovalLedger`** | Renderizado de diffs, preview de post, transcripción |
| `BudgetAllocator` y contabilidad | Checks de aceptación concretos (`kind: tool`/`command`) |
| Memoria y scopes, agenda, observabilidad, `PersonaRenderer` | |

**Aislamiento multi-tenant de los propios perfiles** (ausente en el borrador y es un agujero de
fase 1). Los ids llevan namespace: `edecan.<dominio>/<nombre>` son perfiles de sistema, de sólo
lectura, versionados con el repo; `tenant:<uuid>/<nombre>` son perfiles del tenant, bajo RLS. Un
perfil de tenant puede componer con perfiles de sistema; **un perfil de sistema nunca referencia
uno de tenant**, y dos tenants nunca comparten entrada de caché de resolución porque el
`EffectiveProfile.hash` incluye `tenant_id`. Sin esta regla, la caché de composición es una fuga
cross-tenant a la primera colisión de nombre.

**Cuando un dominio necesita algo de otro** (invariante 10): (1) composición de catálogos, nunca
`import` cruzado — CI lo bloquea con un check de grafo entre `packages/forge-*`, `packages/kernel-*`
y los dominios existentes; (2) al **tercer** consumidor se promueve al Kernel con ADR, dejando
adaptador deprecado ≥2 versiones; (3) nunca por anticipación.

### A.2 El contrato: núcleo mínimo + overlays por horizonte

El error del borrador era pedirle a cada autor de dominio ~40 campos anidados. Nadie escribe eso
dos veces: copia el vecino y cambia tres líneas, que es exactamente el fallo de deriva que luego se
pretendía atajar con un lint de similitud. La solución no es un lint, es **no pedir los campos**.

```python
Horizon    = Literal["reflex", "turn", "task", "project"]      # L0..L3
Readiness  = Literal["experimental", "beta", "stable"]
TaskClass  = Literal["route","chat","plan","code_edit","code_review",
                     "extract","summarize","verify","synthesize","arg_fill"]
EffectClass= Literal["read","write_local","spend_money","send_external",
                     "publish_public","mutate_third_party","irreversible"]

class AgentProfile(BaseModel, frozen=True):
    # --- núcleo: los 7 campos que un autor DEBE escribir ---
    id: ProfileId                       # "edecan.social/community_manager"
    version: str                        # semver
    horizon: Horizon
    tools: ToolCatalog
    routing: RoutingHints
    untrusted_input: bool = False       # ¿el input incluye a un tercero no confiable?
    readiness: Readiness = "experimental"

    # --- overlays: None => se hereda del preset del horizonte ---
    extends: list[ProfileId] = []
    context:     ContextOverlay     | None = None
    model:       ModelOverlay       | None = None
    permissions: PermissionOverlay  | None = None
    acceptance:  AcceptanceOverlay  | None = None
    team:        TeamOverlay        | None = None
    memory:      MemoryOverlay      | None = None
    budget_floor: BudgetFloor       | None = None
```

`HORIZON_PRESETS: dict[Horizon, ProfileDefaults]` es **dato del Kernel**, versionado y direccionado
por hash; su hash entra en el `EffectiveProfile`, así que subir un preset es un cambio auditable y
no una mutación silenciosa de 12 perfiles. Un perfil de dominio típico ocupa 12-20 líneas de YAML.

```python
class ToolCatalog(BaseModel):
    include_bundles: list[str] = []     # "forge.core", "social.publish"
    allow: list[GlobPattern] = []
    deny:  list[GlobPattern] = []       # absorbente
    max_exposed: int = 24               # tope duro por turno
```

**Gramática de glob, pinneada** (el borrador escribía `deny: [*.write]`, que ni siquiera es YAML
válido y deja la semántica al implementador): identificadores segmentados por `.`; `*` casa
**exactamente un** segmento; `**` casa **uno o más** y sólo puede ir en última posición; sin otros
metacaracteres; sensible a mayúsculas. Todo patrón se resuelve **en publicación** contra el
`catalog_manifest_hash` vigente: un `deny` que no casa con nada es **error de publicación**, no un
warning. Motivo: un `deny: "social.**"` que hoy no casa nada y mañana sí, tras registrarse un
plugin, cambia el significado de un perfil ya aprobado. Por eso el `EffectiveProfile` guarda el
**conjunto de `ToolId` resueltos**, no los patrones: registrar una herramienta nueva no puede
alterar una resolución existente sin un evento `profile.resolved` nuevo.

```python
class ContextOverlay(BaseModel):
    window_budget_tokens: int | None = None
    reserve_output_tokens: int | None = None
    retrieval: list[RetrievalSpec] = []   # {source, share, max_tokens, when}
    compaction: Literal["none","rolling_summary","journal_replay"] | None = None
    compaction_trigger_pct: float | None = None
    pinned: list[PinSpec] = []            # {id, priority}
    pinned_max_pct: float = 0.25          # tope duro: los pins no pueden comerse la ventana
    max_tool_result_tokens: int | None = None

class CapabilityReq(BaseModel):           # NUNCA un nombre ni un alias de modelo
    tool_calling: Literal["none","prompted","native"] = "prompted"
    min_context_tokens: int = 16_000
    structured_output: Literal["none","json_best_effort","json_schema"] = "json_best_effort"
    vision: bool = False
    max_latency_p50_ms: int | None = None
    evals: list[EvalGate] = []            # {suite: "forge.code_edit.v1", min_pass_rate: 0.55}
```

**Muerte del `min_quality_tier`.** Un entero 1..5 "medido por la suite propia de evals" es ficción:
`packages/evals/suites/` tiene hoy seis suites (memoria, persona, tool_choice, prompt injection…) y
ninguna mide edición de código ni fiabilidad de tool-calling. Con K3 como único modelo, un
`min_quality_tier: 4` deja el perfil muerto o el número se asigna a ojo y no significa nada. Peor:
un escalar colapsa ejes no comparables. `EvalGate` es honesto y arranca desde cero:

- Si el registro tiene una tasa medida para `(modelo, suite)` → se compara y se admite o se
  descarta.
- Si **no** hay medición → el modelo se admite con evento `model.unverified {suite, model_id}` y el
  `EffectiveProfile` queda marcado `verification_required=true`, lo que fuerza que la aceptación de
  esa ejecución no pueda apoyarse sólo en jueces LLM. Nunca se dead-endea por falta de datos.
- CI ejecuta las suites nombradas por cualquier perfil `readiness: stable`; un perfil estable que
  cite una suite inexistente no publica.

```python
class Budget(BaseModel):                  # ASIGNACIÓN, no propiedad del perfil
    tokens_in: int; tokens_out: int; usd: float
    wall_clock_s: int; tool_calls: int; depth: int; fanout: int

class BudgetFloor(BaseModel):             # esto SÍ lo declara el perfil
    min_usd: float; min_tool_calls: int; min_wall_clock_s: int
```

El perfil declara el **suelo por debajo del cual se niega a arrancar**, y el `BudgetAllocator` del
Kernel asigna el presupuesto real desde (a) el ajuste del usuario por horizonte, (b) el remanente
del padre si es subagente, (c) `max(floors)` de los perfiles compuestos como comprobación previa.
Esto elimina de raíz la aberración `min($2, $40) = $2` y el modo de fallo "murió al 40%".

```python
class EffectiveProfile(BaseModel, frozen=True):
    hash: str
    tenant_id: UUID
    source_profiles: list[tuple[ProfileId, str]]   # linearizados, en orden de aplicación
    preset_version: str
    catalog_manifest_hash: str
    tools: frozenset[ToolId]                       # IDs RESUELTOS
    effects: dict[ToolId, EffectClass]             # asignados por el Kernel, no por el plugin
    context: ContextPolicy
    model: ModelPolicy
    permissions: PermissionPolicy                  # YA intersectado con SessionAuthority
    acceptance: AcceptanceContract
    team: TeamTopology
    memory: MemoryPolicy
    budget: Budget
    verification_required: bool
```

Es lo único que el runtime consume. Se publica en el journal como `profile.resolved` y **nunca se
recalcula en replay**: recomputarlo contra un catálogo mutado rompería la invariante 2.

### A.3 Autoridad: por qué el enrutado no puede escalar privilegios

El borrador decía "el enrutado nunca decide autoridad" y acto seguido metía `autonomy` y `rules`
dentro del objeto que el router elige. Con `investigacion` en `autonomy: 3` y `chat_rapido` en 1,
un fallo de enrutado sí cambiaba la autonomía. Corrección estructural:

```python
class SessionAuthority(BaseModel, frozen=True):   # NO viene del perfil
    grants: frozenset[CapabilityGrant]            # concedidos por el usuario, con TTL
    max_autonomy: int                             # ajuste del usuario para esta superficie
    surface: Surface
    tenant_id: UUID
```

`CapabilityResolver.resolve(effective_profile, session_authority) -> CapabilitySet` intersecta. El
`autonomy` de un perfil se lee como *"este dominio no exige más aprobaciones que N"* — **nunca**
como una concesión de N. La autonomía efectiva es `min(profile.autonomy, session.max_autonomy)` y
el segundo término no lo toca el router. Un error de enrutado ahora es, estructuralmente, incapaz
de escalar privilegio: como mucho expone herramientas que no se usan.

**De dónde sale `EffectClass`.** El borrador nunca lo dijo, y es el mayor agujero del bloque. Hoy
el repo tiene exactamente un bit: `Tool.dangerous: bool` en
`packages/core/edecan_core/tools/base.py`. Si el
plugin se autoclasifica, un plugin descuidado declara `read` y evade todas las reglas. Regla:

1. El Kernel **deriva** la clase mínima desde las capacidades que el manifiesto del plugin solicita:
   egress de red → ≥`send_external`; lectura de credencial del vault → ≥`mutate_third_party`;
   spawn de proceso fuera del sandbox → `irreversible`; escritura en workspace → `write_local`.
2. La declaración del propio plugin sólo puede **subir** la clase, nunca bajarla.
3. Mientras no exista manifiesto firmado (fase 1), todo tool provisto por plugin arranca en
   `irreversible` salvo que esté en una allowlist auditada por el Kernel. Falla cerrado y molesta,
   que es lo correcto.
4. La clase es un **pre-chequeo**. La imposición real la hace el sandbox, que ve el egress de
   verdad (invariante 7): la etiqueta no se confía, se usa para decidir si hay que preguntar.

**Umbrales acumulativos.** `threshold: {usd: 25}` con evaluación por llamada se derrota con diez
compras de $24. `ApprovalLedger` acumula por `(mission_id, effect_class, ventana TTL)`; cruzar el
umbral acumulado vuelve a pedir aprobación. `ttl_seconds` **sólo es legal con `approve_once`**;
`None` significa "la misión". La combinación `approve_each` + `ttl_seconds` que aparecía en el
borrador es un error de publicación, no un campo ignorado — dos implementadores la leerían distinto
(`0` = "caduca ya" vs "sin caducidad").

**Memoria y entrada no confiable.** `untrusted_input: true` (llamadas telefónicas, comentarios de
redes, correo entrante, páginas web) **prohíbe** `memory.write_mode: auto`. El borrador ponía
`write_mode: auto` en el perfil de llamada telefónica: eso es un canal de envenenamiento de memoria
persistente operado por quien esté al otro lado del teléfono. Con `untrusted_input`, la escritura
es `propose` y sólo de **campos estructurados** contra un esquema declarado, nunca texto libre.

### A.4 Álgebra de composición

Distinción central: **capacidad** (qué existe) se une; **autoridad** (qué se hace sin preguntar) se
intersecta. Intersectar capacidades haría inútil componer; unir autoridad es escalada de privilegio.

| Campo | Operador | Nota |
|---|---|---|
| `tools.allow`, `include_bundles` | ∪ (sobre `ToolId` **resueltos**) | join |
| `tools.deny` | ∪, absorbente | gana sobre todo |
| `tools.max_exposed` | min | K3 degrada por encima de ~30 specs |
| `autonomy` | min, y luego ∩ con `SessionAuthority` | meet |
| `permissions.rules` | por efecto, el modo más restrictivo (`forbid`>`approve_each`>`approve_once`>`notify`>`auto`); `threshold` → min | |
| `sandbox` | intersección (fs_roots ∩, red más cerrada, exec ∩) | |
| `context.window_budget` | max, **acotado a la ventana real del modelo resuelto** | |
| `context.retrieval` | **no se une**: se re-resuelve bajo un asignador único | ver abajo |
| `context.pinned` | ∪ **con tope `pinned_max_pct`** | overflow → `context.pin_evicted` |
| `model.by_task_class` | por clase, join de exigencias (max `min_context`, `native`>`prompted`, unión de `EvalGate`, min `max_latency`) | |
| `acceptance.checks` | ∪ **filtrado por `applies_to`** | ver abajo |
| `acceptance.on_fail` | el más conservador; `max_repair_cycles` min | |
| `horizon` | max | |
| `budget_floor` | max por eje; el `budget` real lo asigna el Kernel | |
| `memory.write_scope` | si difieren → `None` + `propose`; `untrusted_input` ∪ | |
| `team.shape` | si difieren y ninguno es `solo` → **no fusionar** | regla de escape |
| `persona` | **no está en el perfil** | se resuelve por emisión |

**Retrieval no se une, se reasigna.** Unir specs de retrieval mete 20k+2.5k+6k de recuperación en
una ventana que ningún perfil dimensionó. Cada `RetrievalSpec` declara `share` (fracción, no tokens
absolutos); al componer, los shares se renormalizan a 1.0 sobre la ventana compuesta menos
`reserve_output_tokens` menos los pins. `pinned_max_pct` es un tope duro: si los pins unidos lo
exceden, se degradan los de menor `priority` a "recuperable" con evento `context.pin_evicted`.
Sin este tope, suficiente composición produce un estado sin solución legal.

**Aceptación por clase de artefacto.** El borrador unía checks a ciegas: componer ingeniería con
community manager obligaba a pasar `uv run pytest` para publicar un post, y `on_fail` conservador
mandaba todo a replanificar por un fallo irrelevante. Cada `CheckSpec` declara
`applies_to: list[ArtifactClass]` (`code_diff`, `social_post`, `itinerary`, `call_transcript`,
`report`). La unión sólo **activa** los checks cuya clase de artefacto aparece realmente en la
ejecución. Un campo, y la unión pasa de peligrosa a correcta.

**Jueces LLM: anti-colusión en el propio contrato.** Un `kind: llm_judge` debe declarar
`blind: true` (ve el artefacto y el criterio, jamás la transcripción del ejecutor) y
`model_disjoint: true` (el resolutor elige un modelo distinto del que produjo el artefacto, o de
otro proveedor). Si con el registro vigente eso es imposible —el caso realista con K3 como único
modelo— el check degrada a `advisory`, cuenta como señal pero **no puede ser el único check**, y se
emite `check.degraded_to_advisory`. `readiness: stable` exige ≥1 check no-`llm_judge`.

**`extends` es `⊕`.** Un solo operador. `extends` se aplica en publicación, de izquierda a derecha,
sobre la **linearización** de la lista (si una base aparece dos veces, se aplica una sola vez, en su
primera posición). No hay resolución de diamantes porque no hay diamantes.

**Regla de escape.** Si dos perfiles difieren en `team.shape` sin que ninguno sea `solo`, o si el
join de `ModelPolicy` es insatisfacible, o si `horizon` difiere en más de un nivel, la composición
**falla explícitamente** y el sistema pasa a **delegación vertical**: un perfil raíz `pipeline` que
invoca cada perfil puro como subagente. Fusionar perfiles incompatibles produce el peor agente
posible; delegar produce dos agentes correctos.

**Ejemplo 1 — "publica en redes el cambio que acabas de desplegar".** `ingenieria ⊕ community`.
Capacidad: `forge.vcs.log` ∪ `social.publish` ∪ `creative.image`. Autonomía: `min(2,1)=1`, luego ∩
con la sesión. `publish_public` queda en `approve_each`. Aceptación: activa `social.validate_post`
y `brand_voice` (clase `social_post` presente) y **no** activa `uv run pytest` (no hay `code_diff`
nuevo en esta ejecución). Retrieval: los shares de `forge.index.symbols` y `social.recent_posts` se
renormalizan sobre 96k. Resultado: un turno L2 que lee el changelog del despliegue, redacta, y
pausa en la aprobación.

**Ejemplo 2 — "llama al proveedor, después actualiza CRM y repo".** Voz exige `max_latency_p50_ms:
350`; ingeniería exige `evals: [{suite: forge.code_edit.v1, min_pass_rate: 0.55}]`. El join es
insatisfacible. Se rechaza la composición → pipeline `[llamada → crm_ops → ingenieria]`,
`share: artifacts`. La transcripción se persiste por hash y es el input de la etapa siguiente;
ningún contexto de modelo se comparte. Además el perfil de llamada lleva `untrusted_input: true`,
así que lo que dijo el proveedor entra al CRM como campos extraídos con esquema, no como prosa que
el siguiente agente obedece.

**Ejemplo 3 — "investiga el stack del competidor y abre un PR con el spike".** Ambos `solo`.
Conflicto aparente: investigación necesita red, ingeniería declara `sandbox.net_policy: deny`. No es
conflicto: `net_policy` gobierna el **sandbox de procesos**; `web.fetch` es herramienta del Kernel
fuera del sandbox. Resultado correcto: red vía herramienta, sandbox sin red. Y el presupuesto ya no
es `min($2,$40)=$2`: el `BudgetAllocator` asigna según el horizonte compuesto (`project`) y verifica
contra `max(floors)` antes de arrancar.

### A.5 Perfiles escritos

Con presets por horizonte, un perfil de dominio son 12-20 líneas. Todo lo omitido lo aporta
`HORIZON_PRESETS[horizon]`, cuyo hash queda en el `EffectiveProfile`.

```yaml
# kernel/profiles/chat_rapido.yaml
id: edecan.core/chat_rapido
version: 1.0.0
horizon: reflex                      # preset: 6k ventana, sin plan, sin checks, sin subagentes
tools: { include_bundles: [core.time, core.memory_read], max_exposed: 4 }
permissions: { autonomy: 1, rules: [{effects: [send_external, spend_money, publish_public,
                                               mutate_third_party, irreversible], mode: forbid}] }
routing: { surfaces: [mobile, web, watch] }
readiness: stable
---
id: edecan.voice/llamada_telefonica
version: 1.0.0
horizon: turn
untrusted_input: true                # => memory.write_mode: auto queda PROHIBIDO
tools: { include_bundles: [voice.dtmf, contacts.read, calendar.read, core.notes], max_exposed: 8 }
context: { pinned: [{id: objetivo_llamada, priority: 10}, {id: guion, priority: 9}],
           max_tool_result_tokens: 800 }
model:
  by_task_class:
    chat:    { tool_calling: prompted, max_latency_p50_ms: 350 }
    extract: { structured_output: json_best_effort, max_latency_p50_ms: 900 }
permissions:
  autonomy: 2
  rules: [{effects: [spend_money], mode: forbid},
          {effects: [mutate_third_party], mode: approve_once, threshold: {usd: 0}, ttl_seconds: 900}]
acceptance: { checks: [{kind: tool, ref: voice.objective_met, applies_to: [call_transcript]}],
              on_fail: escalate_human }
budget_floor: { min_usd: 0.10, min_tool_calls: 6, min_wall_clock_s: 120 }
memory: { read_scopes: [user.identity, contacts], write_scope: domain.calls, write_mode: propose,
          schema: call_outcome_v1 }
routing: { surfaces: [phone], keywords: [llama, marca, teléfono] }
readiness: beta
---
id: edecan.social/community_manager
version: 1.0.0
horizon: task
extends: [edecan.core/base_operativo]
untrusted_input: true                # comentarios y DMs entrantes
tools: { include_bundles: [social.read, social.publish, creative.image, web.search],
         deny: [social.delete_account, social.ads.**], max_exposed: 18 }
context: { retrieval: [{source: memory.tenant.brand, share: 0.2},
                       {source: social.recent_posts, share: 0.3}] }
model:
  by_task_class:
    synthesize: { min_context_tokens: 32000, evals: [{suite: social.brand_voice.v1, min_pass_rate: 0.6}] }
    arg_fill:   { structured_output: json_schema, tool_calling: native }
permissions:
  autonomy: 1
  rules: [{effects: [publish_public], mode: approve_each},
          {effects: [spend_money], mode: forbid}]
acceptance:
  checks: [{kind: tool, ref: social.validate_post, applies_to: [social_post]},
           {kind: llm_judge, ref: brand_voice, applies_to: [social_post],
            threshold: 0.8, blind: true, model_disjoint: true}]
budget_floor: { min_usd: 0.25, min_tool_calls: 12, min_wall_clock_s: 180 }
team: { shape: supervisor_workers, roles: { redactor: {profile_ref: edecan.social/redactor, max_parallel: 3} } }
memory: { read_scopes: [tenant.brand, domain.social], write_scope: domain.social, write_mode: propose }
routing: { keywords: [instagram, tiktok, publicar, calendario, comentarios] }
---
id: edecan.travel/reserva_viaje
version: 1.0.0
horizon: task
tools: { include_bundles: [travel.search, travel.book, calendar.rw, payments.prepare],
         deny: [payments.execute], max_exposed: 20 }
context: { pinned: [{id: restricciones_viaje, priority: 10}, {id: pasajeros, priority: 10}] }
model:
  by_task_class:
    arg_fill: { structured_output: json_schema, tool_calling: native }
    extract:  { structured_output: json_schema }
permissions:
  autonomy: 1
  rules: [{effects: [spend_money], mode: approve_each, threshold: {usd: 0}},
          {effects: [irreversible], mode: approve_each}]
acceptance:
  checks: [{kind: tool, ref: travel.itinerary_consistency, applies_to: [itinerary]},
           {kind: human, ref: confirmar_itinerario, applies_to: [itinerary]}]
  on_fail: escalate_human
budget_floor: { min_usd: 0.80, min_tool_calls: 25, min_wall_clock_s: 300 }
routing: { keywords: [vuelo, hotel, reserva, itinerario] }
---
id: edecan.forge/ingenieria_software
version: 1.0.0
horizon: project
extends: [edecan.core/base_operativo]
tools: { include_bundles: [forge.vfs, forge.index, forge.exec, forge.vcs, forge.verify, web.search],
         deny: [forge.vcs.force_push, forge.deploy.prod], max_exposed: 26 }
context:
  window_budget_tokens: 96000
  compaction: journal_replay
  retrieval: [{source: forge.index.symbols, share: 0.45},
              {source: forge.journal.decisions, share: 0.15}]
  pinned: [{id: spec_ref, priority: 10}, {id: acceptance_ref, priority: 10}, {id: plan_ref, priority: 8}]
model:
  by_task_class:
    plan:      { min_context_tokens: 96000, structured_output: json_schema,
                 evals: [{suite: forge.plan.v1, min_pass_rate: 0.5}] }
    code_edit: { tool_calling: native, min_context_tokens: 64000,
                 evals: [{suite: forge.code_edit.v1, min_pass_rate: 0.55}] }
    verify:    { tool_calling: native }
permissions:
  autonomy: 2
  rules: [{effects: [write_local], mode: auto},
          {effects: [irreversible, publish_public], mode: approve_each},
          {effects: [spend_money], mode: approve_once, threshold: {usd: 20}, ttl_seconds: 86400},
          {effects: [send_external], mode: notify}]
  sandbox: { exec: true, net_policy: deny, fs_roots: [workspace], wall_clock_s: 600 }
acceptance:
  checks: [{kind: command, ref: "uv run pytest", applies_to: [code_diff]},
           {kind: command, ref: "uv run ruff check", applies_to: [code_diff]},
           {kind: command, ref: "uv run mypy", applies_to: [code_diff]},
           {kind: llm_judge, ref: diff_scope_guard, applies_to: [code_diff],
            blind: true, model_disjoint: true}]
  on_fail: replan
  max_repair_cycles: 3
budget_floor: { min_usd: 5.0, min_tool_calls: 200, min_wall_clock_s: 3600 }
team:
  shape: supervisor_workers
  share: artifacts+journal
  roles:
    implementador: { profile_ref: edecan.forge/implementador, max_parallel: 6 }
    revisor:       { profile_ref: edecan.forge/revisor, max_parallel: 2 }
    verificador:   { profile_ref: edecan.forge/verificador, max_parallel: 3 }
memory: { read_scopes: [domain.engineering, project.*], write_scope: project.current,
          write_mode: propose }        # propose, no auto: 6 workers concurrentes sobre un scope
routing: { surfaces: [ide, web], keywords: [repo, bug, PR, refactor, tests, deploy] }
```

Nota honesta: `investigacion` desaparece como perfil de dominio de primer nivel y pasa a ser rol.
Investigar no es algo que el usuario pida como fin; es un medio dentro de otro dominio. Mantenerlo
como dominio enrutable añadía una opción que el router no puede distinguir de "chat con búsqueda".

### A.6 Enrutado de dominio

| Etapa | p50 real | Coste | Acierto | Falla característica |
|---|---|---|---|---|
| E0 sticky `(sesión, superficie)` | 0 ms | $0 | ~65% del tráfico | se pega al dominio equivocado |
| E1 reglas duras + superficie | 2-6 ms | $0 | ~78% de lo restante | ambigüedad léxica |
| E1.5 léxico normalizado local | 1-4 ms | $0 | ~85% acumulado | vocabulario nuevo |
| E2 kNN sobre embeddings | **60-150 ms** (40-120 de la llamada de embedding + 12-25 de búsqueda) | ~$2e-5 | ~91% | dominio sin ejemplos |
| E3 clasificador LLM | 280-700 ms | ~$4e-4 | ~94% | latencia y no determinismo |

El borrador citaba "12-25 ms" para E2 omitiendo que calcular el embedding del mensaje es una
llamada de red al proveedor. En Workers AI eso son 40-120 ms p50. El número honesto cambia el
diseño: **E1.5 no es opcional**, es la etapa que evita pagar red en la mayoría de los turnos.

**E1.5 reutiliza código que ya existe**:
`packages/core/edecan_core/capability_routing.py` ya
hace normalización unicode, stopwords y unión de familias sin red. Se generaliza de "familias de
tools" a "perfiles" y se conserva su sesgo conservador (ante duda, no recorta).

**Cascada con presupuesto de latencia.** E0 → E1 → E1.5 → E2 sólo si el margen de E1.5 < 0.15 →
E3 sólo si E2 sigue ambiguo **y** (coste estimado > $0.05 **o** horizonte estimado ≥ L2). Nunca E3
en L0. Resultado: p50 ~5 ms, p90 ~8 ms, p95 ~140 ms, p99 ~600 ms; coste amortizado ~$6e-5/mensaje.

**Sticky se escopa a `(sesión, superficie)`, no a la sesión.** Con teléfono y web activos sobre la
misma sesión, la regla dura de superficie (`phone → llamada_telefonica`) y el sticky del web se
contradicen. Escopar por superficie elimina la carrera. Una sesión puede tener varias
conversaciones; una **misión** tiene exactamente un `EffectiveProfile`.

**Determinismo del journal.** E2 y E3 son no deterministas (versión del modelo de embeddings,
temperatura del clasificador). La decisión se journaliza como hecho —
`profile.selected {profile_hash, stage, score, router_version, latency_ms}` — y **nunca se recomputa
en replay**. Sin esta regla, la invariante 2 es decorativa: dos replays del mismo journal darían
perfiles distintos.

**`RoutingHints` sin `prioridad: int`.** Un entero mágico es precisión inventada y se convierte en
un campo de batalla ("¿por qué 40 y no 45?"). Los empates se rompen por especificidad: regla de
superficie > mención explícita de herramienta > continuación de misión > mayor score, y en empate
exacto se pregunta al usuario una vez y se hace sticky.

### A.7 Escalado de esfuerzo

| Nivel | Objetivo | Plan | Checkpoint | Verificación | Subagentes | Tokens | USD |
|---|---|---|---|---|---|---|---|
| **L0 reflejo** | <800 ms extremo a extremo | no | sólo mensaje final | no | no | 1-3k | ~$0.002 |
| **L1 turno** | <8 s | implícito | fin de turno | esquema de argumentos | no | 4-15k | $0.01-0.04 |
| **L2 tarea** | 1-20 min | explícito, ≤12 pasos | por paso | contrato ejecutable, ≤2 reparaciones | ≤2, fanout ≤4 | 60k-800k | $0.05-1.50 |
| **L3 proyecto** | horas-días | spec + plan + criterios versionados | por work-item + snapshot COW | contrato + revisión de diffs + judge | ≤6, profundidad 3 | 5M-40M | $5-60 |

Se **apagan** en L0/L1: planificador, checkpointing intermedio, retrieval por índices, verificación,
subagentes, compactación. Un solo runtime, ramas apagadas — no dos runtimes.

**Los disparadores del borrador estaban mal.** "L1→L2 al superar 6 tool calls" pelea directamente
con `budget.tool_calls` y con `max_exposed`, y sobre todo es una señal *retrospectiva*. Disparadores
corregidos, prospectivos:

- L1→L2: el modelo emite un plan explícito, **o** `elapsed > 0.5 × objetivo_del_nivel`, **o** el
  contrato de aceptación del perfil tiene checks activables por la clase de artefacto en curso.
- L2→L3: 2 replans, **o** el plan supera 12 work-items, **o** `wall_clock` consumido > 60% con
  <40% de work-items cerrados.
- Descendente: **sólo explícita**. Degradar en silencio un proyecto a turno es cómo se pierde
  trabajo irrecuperable.

La escalada **no es "seguir con más presupuesto"**: es una re-proyección de contexto. Al pasar de
L1 (ventana 6k) a L2 (32k) cambia la política de contexto bajo la que se cebó el modelo. Se ejecuta
con la misma maquinaria que `profile.switch` (§A.8): una sola barrera, no dos implementaciones.

### A.8 Transiciones: `profile.switch` y `effort.escalated` son la misma barrera

El borrador decía que el switch "conserva el trabajo porque todo vive en el plano de datos" y no
decía nada de llamadas en vuelo, aprobaciones pendientes ni subagentes corriendo. Ahí hay corrupción
de estado y escalada de privilegio reales: si tres subagentes corren bajo el conjunto de capacidades
anterior (más amplio) y el usuario corrige el perfil a uno más estrecho, ¿terminan? ¿con qué
autoridad? Contrato:

```
TransitionBarrier.begin(reason) -> BarrierToken     # emite {profile.switch_requested|effort.escalation_requested}
  1. congela despacho de tools nuevas y spawn de subagentes nuevos
  2. llamadas en vuelo: sólo se dejan terminar si su ToolId está en
     old_capabilities ∩ new_capabilities; el resto se cancela -> tool.cancelled_by_switch
  3. aprobaciones pendientes cuya EffectClass no permite el perfil nuevo: VOIDED
     -> approval.voided_by_switch   (arrastrar una aprobación entre perfiles es una
        primitiva de escalada de privilegio; nunca se hereda)
  4. subagentes: se les envía cancel cooperativo con deadline de 30 s; sus artefactos
     ya escritos por hash sobreviven; su workspace COW queda como rama sin fusionar
TransitionBarrier.commit(token, new_effective) -> emite {profile.switch_committed|effort.escalated}
  5. re-proyección de contexto: 1 llamada de resumen, ~3-8k tokens, 1.5-4 s
  6. el presupuesto consumido NO se devuelve; el nuevo se re-asigna desde el remanente
```

Disparadores del switch: (a) `capability.gap_detected` — el modelo pide una herramienta ausente ≥2
veces → se propone componer el perfil que la aporta; automático sólo si `autonomy≥2` **y** la
composición no amplía autoridad efectiva; (b) corrección explícita del usuario.

**Escrituras de memoria concurrentes.** Con `fanout: 6` y `share: artifacts+journal`, seis
implementadores escribiendo `project.current` son actualizaciones perdidas. Regla del Kernel, no del
perfil: **un subagente nunca escribe memoria en modo `auto`**, siempre `propose`; sólo el supervisor
compromete, y el commit es CAS sobre la versión del scope (`memory.write_conflict` si no casa). Lo
mismo en el plano de datos: cada subagente tiene su overlay COW (invariante 5) y la fusión es acto
explícito del supervisor.

### A.9 Migración desde `packages/agents/edecan_agents/`

**Se conserva** de
`packages/agents/edecan_agents/profiles.py`: el perfil
como dato inmutable (`frozen=True`), `allowed_tools` → `tools.allow` (son referencias verificadas
contra herramientas que existen de verdad, tirarlas sería tirar trabajo real), y los 16 perfiles
**degradados de nivel**: hoy no son dominios, son roles de subagente dentro de una misión
(`research`, `qa`, `devops`). Pasan a `team.roles`: `research`+`data_analyst` → roles de los
dominios que investigan; `developer`+`qa`+`devops`+`security` → roles de `ingenieria_software`;
`marketing`+`social_media`+`design`+`content` → roles de `community_manager`.

**Se generaliza**:
- `permite_dangerous_con_confirmacion: bool` → `PermissionPolicy.rules` por `EffectClass`. Un
  booleano no distingue "gastar $3.000" de "enviar un correo". El mapeo de arranque es mecánico:
  `dangerous=True` → `irreversible` hasta que exista manifiesto firmado (§A.3).
- `RestrictedRegistry` (`registry_view.py`) → `CapabilityView` del Kernel. Misma semántica de fallo
  cerrado, pero intersectando con `SessionAuthority`, no sólo con la lista del perfil. Su duck-typing
  (`.get`/`.specs`) se conserva: es lo que permite testear sin Postgres.
- `model_alias: Literal["principal","rapido","profundo"]` (y `_LLM_ALIAS = "rapido"` en
  `edecan_core/agent.py`, y `Alias` en `edecan_llm/router.py`) → `ModelPolicy.by_task_class` con
  requisitos de capacidad. El alias de tres valores ya es acoplamiento a la topología de un
  proveedor concreto y no expresa lo que la tarea necesita. El degradado por flag de plan que hoy
  hace `LLMRouter.resolve` se conserva, pero como **degradación contabilizada** con evento
  `model.degraded`, no como sustitución silenciosa.
- `waiting_confirmation` de `orchestrator.py` → estado genérico de aprobación del Kernel,
  disponible en cualquier nivel, no sólo dentro de una misión.

**Se tira**: `PROFILES` como `dict` hardcodeado e `IMPLEMENTED_AGENT_KEYS`; `disponible: bool` →
`readiness` de tres valores; la ejecución por olas con `depende_de` deja de ser exclusiva de misiones
(pasa a ser `team.shape: pipeline` del runtime único, conservando `_resolver_depende_de` que ya
garantiza DAG por construcción — es código correcto y probado).

**Corrección al borrador sobre `plan()`.** El borrador proponía que el plan declarase *capacidades
requeridas* y que el resolutor eligiera el rol. Para un modelo modesto eso es **más difícil**, no
menos: hoy el planificador elige entre 16 cadenas de un enum cerrado; mañana tendría que emitir un
conjunto de capacidades bien formado. K3 lo hará peor. Se mantiene el **enum cerrado en la frontera
del planificador**, pero generado desde `team.roles` del perfil activo: 3-6 opciones en vez de 16, y
derivado de dato. El planificador ve un vocabulario diminuto y cerrado; la lista sigue siendo dato.
El fallback a `research` desaparece: si el rol no casa, el paso va al rol marcado `default` del
perfil activo, que es explícito y auditable.

### A.10 Traza: "construye Acme 2.0"

*Supuestos declarados*: Kimi K3 sobre Workers AI a ~$0.55/M in y $2.20/M out (mezcla 85/15 ≈
$0.80/M); existe un repo v1; alcance ≈ 45 work-items.

1. **Enrutado.** E1 no decide; E1.5 da margen 0.09; E2 sigue ambiguo (ingeniería vs negocio); coste
   estimado >> $0.05 → E3, 420 ms, $0.0004. Perfil `edecan.forge/ingenieria_software`, horizonte
   `project` → L3. Se journaliza `profile.selected`.
2. **Suficiencia de presupuesto.** `BudgetAllocator` compara la asignación del usuario contra
   `budget_floor` y contra la estimación con `first_pass_yield` (§abajo). Si no alcanza, se emite
   `budget.insufficient` **antes de gastar un token**.
3. **Intake.** Rol `analista_spec` produce `spec.md` como artefacto por hash. ~180k tokens, **$0.15**.
   **Aprobación humana #1: firma de la spec.**
4. **Mapa del v1.** Indexado determinista (sin LLM) + resumen de ~40 módulos: ~900k, **$0.72**.
5. **Plan y criterios.** 45 work-items con DAG + contrato ejecutable por item: ~250k, **$0.20**.
   **Aprobación humana #2: plan y criterios.**
6. **Ejecución.** `supervisor_workers`, fanout 6, cada item en overlay COW propio: ~12 llamadas ×
   (20k in + 3k out) ≈ 276k por item × 45 ≈ **12.4M tokens, $10**.
7. **Verificación y reparación.** Aquí el borrador metía un número inventado ("~55% pasa al primer
   intento"). Se sustituye por una cantidad **medida y nombrada**: `first_pass_yield`, registrada
   por `(perfil, modelo, clase de artefacto)` en el journal. Hasta N≥30 items medidos se usa un
   prior conservador de **0.35**, no 0.55. Con 0.35 el overhead de reparación es ~+150%, no +80%:
   ≈ **18M tokens, $14**.
8. **Revisión de diffs y fusión.** Rol `revisor` + judge `diff_scope_guard` con `blind` y
   `model_disjoint`; si no hay segundo modelo disponible, degrada a `advisory` y el peso recae en
   `pytest`/`mypy`. 1.2M tokens, **$1**. **Aprobación #3: fusión.** **#4: primer gasto externo o
   credencial de producción.** **#5: despliegue.**

**Total con prior conservador ≈ 33M tokens, ~$26; presupuestar $40-50.** Contra $50.000 de crédito
son ~1.100 ejecuciones: **el dinero no es la restricción**. Las restricciones reales son (a)
concurrencia y rate limits de Workers AI, que fijan el wall-clock — con fanout 6 estimo **12-20
horas de máquina** en 2-4 días de calendario; y (b) el throughput de revisión humana en los 5 puntos
de aprobación, que es lo que convierte "horas" en "días". `first_pass_yield` convierte el punto (a)
en un lazo de control: si la medición sube de 0.35 a 0.55, el presupuesto se reajusta solo.

### A.11 Fases

| Fase | Alcance | Por qué ahí |
|---|---|---|
| **F0 (sem. 1-3): un runtime, un `EffectiveProfile`** | Núcleo de 7 campos, `HORIZON_PRESETS`, `ProfileComposer` sin composición (sólo `extends` linearizado), `SessionAuthority`, `CapabilityResolver`, `EffectClass` derivada mecánicamente de `Tool.dangerous`, router E0+E1, `profile.selected`/`profile.resolved` en journal. Dos perfiles: `chat_rapido`, `asistente_general`. | Todo lo demás consume `EffectiveProfile`. Enviar sin composición prueba que el runtime es de verdad uno solo antes de que la composición pueda esconder duplicación. |
| **F1 (sem. 4-7): dominios reales y `⊕`** | Operador `⊕` completo, validador de publicación (satisfacibilidad, resolución de globs, legalidad de `ttl`), router E1.5+E2, perfiles de voz/social/viajes, `untrusted_input`, `ApprovalLedger` acumulativo, los 16 perfiles actuales convertidos en `team.roles`. | La composición sólo es evaluable con ≥3 dominios reales. El `ApprovalLedger` va aquí porque el primer dominio que gasta dinero (viajes) aterriza aquí. |
| **F2 (sem. 8-12): L2/L3 y transiciones** | `BudgetAllocator`, `TransitionBarrier` (switch + escalada sobre la misma barrera), contratos con `applies_to`, jueces `blind`+`model_disjoint`, memoria CAS, overlays COW por subagente, perfil `ingenieria_software`. | Forge necesita L3, y L3 sin barrera de transición ni CAS de memoria pierde trabajo. Ponerlo antes de F1 sería construir la parte cara sin dominios que la validen. |
| **F3 (sem. 13+): medición y afinado** | Registro de capacidades con `EvalGate` medidos (suites `forge.code_edit.v1`, `social.brand_voice.v1`), lazo de `first_pass_yield`, router E3, recuperación de herramientas (`selection: retrieved`) **sólo si** algún perfil supera 24 tools. | Medir requiere ejecuciones reales. Adelantar E3 o la recuperación de herramientas es optimizar contra tráfico imaginario. |

### Contratos, resumidos

**Interfaces**: `ProfileRegistry.publish/get/list`, `ProfileComposer.compose(profiles, preset_version,
tenant_id) -> EffectiveProfile | CompositionError`, `CapabilityResolver.resolve(effective,
session_authority) -> CapabilitySet`, `DomainRouter.select(message, session_state, surface) ->
RoutingDecision`, `ModelResolver.resolve(task_class, policy, model_registry) -> ModelBinding |
Degradation`, `BudgetAllocator.allocate(horizon, parent_remaining, floors) -> Budget |
Insufficient`, `ApprovalGate.check(call, effective, ledger) -> Decision`, `EffortController.level()
/ escalate()`, `TransitionBarrier.begin/commit`, `PersonaRenderer.render(emission, surface)`.

**Eventos del journal**: `profile.published`, `profile.selected`, `profile.resolved`,
`profile.switch_requested/committed`, `effort.escalation_requested/escalated`,
`tool.cancelled_by_switch`, `approval.requested/granted/denied/expired/voided_by_switch`,
`capability.gap_detected`, `budget.allocated/insufficient/exhausted`, `context.pin_evicted`,
`model.unverified/degraded`, `check.degraded_to_advisory`, `memory.write_conflict`.

**Máquina de estados de una ejecución**: `routed → resolved → allocated → running ⇄ (awaiting_approval
| transitioning) → verifying ⇄ repairing → (accepted | escalated_human | aborted)`. `transitioning`
es el único estado desde el que cambia el `EffectiveProfile`; `awaiting_approval` es el único que
puede vivir indefinidamente.

**Invariantes de datos**: (i) `EffectiveProfile` es inmutable y su hash cubre perfiles fuente,
`preset_version`, `catalog_manifest_hash` y `tenant_id`; (ii) `tools` contiene `ToolId` resueltos,
nunca patrones; (iii) `permissions` ya está intersectado con `SessionAuthority` — el runtime jamás
vuelve a consultar los grants durante la ejecución salvo tras una barrera; (iv) ninguna aprobación
sobrevive a un cambio de `EffectiveProfile`; (v) `sum(pinned) ≤ pinned_max_pct × window_budget`;
(vi) una decisión de enrutado journalizada nunca se recomputa.

### Alternativas descartadas

| Alternativa | Por qué se descartó | Coste si me equivoco |
|---|---|---|
| **Subclases de agente por dominio** (`EngineeringAgent(BaseAgent)`) | Fija la variación en el eje equivocado: la herencia de código no compone (¿qué es `Social ⊕ Engineering`?) y cada bug de reanudación se arregla N veces. | Si los dominios divergieran en el **bucle** y no sólo en política, los perfiles serían configuración anémica sobre condicionales. Mitigación: `runtime_hooks` como plugins del núcleo. Reversión ~6 semanas. |
| **Un agente con todas las herramientas** | Con 200+ tools y K3 la precisión de selección cae por debajo del 60% y el prompt supera el presupuesto antes del primer mensaje. Y hace imposible acotar autoridad (viola invariante 7). | Si el modelo mejorase mucho, pagaríamos complejidad de composición sin necesidad. Barato de revertir: un perfil `todo` con recuperación de herramientas. |
| **Perfil = prompt de sistema** (lo que hay hoy en `system_prompt_extra`) | Un prompt no impone presupuesto, permisos ni aceptación: el modelo es un actor no confiable. | Ninguno; ya sabemos que falla. |
| **`min_quality_tier` escalar 1..5** | Colapsa ejes no comparables (contexto, tool-calling, JSON, latencia) y hoy no hay nada que lo mida: sería un número asignado a ojo que luego se cita como si fuera dato. | Si `EvalGate` resulta demasiado pesado de mantener, se puede derivar un tier *calculado* desde las suites. Barato: es una vista, no un cambio de contrato. |
| **`budget` como campo del perfil compuesto con `min`** | `min($2, $40) = $2` mata la composición y produce muertes a mitad de un L3. El presupuesto es una asignación del invocante, no una propiedad del dominio. | Si resultara que los usuarios quieren topes por dominio, `budget_floor` ya tiene el hueco para un `budget_ceiling` simétrico. Horas. |
| **`persona` dentro del perfil** | Una misión que responde en chat y deja una nota de voz tiene dos superficies; "gana la de la superficie de salida" no está definido con dos salidas. | Ninguno relevante: mover persona al renderizado es estrictamente más general. |
| **Lint de similitud >85% entre perfiles** | Prohíbe lo correcto (dos perfiles que difieren en un eje que sí importa) y no ataca la causa, que era pedir 40 campos. | Si la deriva aparece igual, el lint correcto es de *enrutado*: dos perfiles indistinguibles para el router se fusionan. |
| **Router LLM puro** | 400-700 ms por mensaje mata L0 y es no determinista entre reintentos. | Si reglas+léxico+kNN degradan por debajo del 80%, la cascada ya contempla subir a E3 con más frecuencia. |
| **Router por reglas puro** | No escala más allá de ~8 dominios ni resuelve peticiones compuestas. | Bajo: es la etapa E1. |
| **Que el planificador emita capacidades requeridas en vez de roles** | Es *más* difícil para un modelo modesto que elegir de un enum cerrado. K3 lo haría peor que hoy. | Si el modelo mejora, ampliar el vocabulario del planificador es un cambio de prompt. |
| **`team.shape: "debate"`** | Sin caso de uso, sin modelo de coste. Si hiciera falta revisión adversarial, es un `pipeline` de dos roles. | Nulo: añadirlo después es un valor más en un `Literal`. |

### Cómo se rompe

1. **Explosión combinatoria de composiciones.** 12 perfiles → 66 pares sin probar. *Mitigación*:
   sólo se admiten composiciones cuyo `EffectiveProfile` pase la suite de contrato (satisfacibilidad
   de `ModelPolicy`, catálogo no vacío, `budget_floor` alcanzable, pins bajo el tope); las válidas se
   cachean por `(hash del par, tenant)` y se testean en CI.
2. **Herramienta registrada tarde que cambia el significado de un `deny` viejo.** *Mitigación*: el
   `EffectiveProfile` guarda `ToolId` resueltos y el `catalog_manifest_hash`; un manifiesto nuevo
   invalida la caché y fuerza `profile.resolved` nuevo, auditable.
3. **Efecto mal clasificado = escalada silenciosa.** Un plugin que se declara `read` y hace egress.
   *Mitigación*: clase derivada por el Kernel desde las capacidades solicitadas, el plugin sólo puede
   subirla, y el sandbox impone (la etiqueta es pre-chequeo, no defensa).
4. **Fraccionamiento de umbrales.** Diez compras de $24 bajo un tope de $25. *Mitigación*:
   `ApprovalLedger` acumulativo por `(misión, efecto, ventana TTL)`.
5. **Aprobación arrastrada entre perfiles.** Aprobar `publish_public` bajo un perfil y ejecutarlo
   bajo otro. *Mitigación*: toda aprobación pendiente se anula en la barrera de transición.
6. **Router pegajoso** que se queda 20 turnos en el dominio equivocado. *Mitigación*:
   `capability.gap_detected` fuerza reevaluación tras 2 fallos; el perfil activo es visible en todas
   las superficies; sticky escopado a `(sesión, superficie)`.
7. **Pins que se comen la ventana.** Composición suficiente y no queda espacio para el mensaje.
   *Mitigación*: `pinned_max_pct` con desalojo por prioridad y evento.
8. **Escrituras de memoria concurrentes desde 6 workers.** *Mitigación*: subagentes siempre
   `propose`; commit sólo del supervisor, con CAS sobre versión de scope.
9. **Envenenamiento de memoria desde entrada no confiable** (la persona al otro lado del teléfono,
   un comentario en redes, una página web). *Mitigación*: `untrusted_input` prohíbe `write_mode:
   auto` y exige escritura por esquema estructurado.
10. **Jueces LLM que coluden con el ejecutor.** *Mitigación*: `blind` + `model_disjoint`; si no hay
    segundo modelo, degradan a `advisory` y no pueden ser el único check; `readiness: stable` exige
    ≥1 check no-judge.
11. **Contratos de aceptación decorativos** (`checks: []`) que convierten L2 en L1 caro.
    *Mitigación*: la misma regla de `readiness: stable`, más un reporte semanal de perfiles cuyos
    checks nunca fallaron en 50 ejecuciones (un check que nunca falla probablemente no verifica).
12. **K3 con tool-calling `prompted` rompiendo `arg_fill` en perfiles que exigen `json_schema`.**
    *Mitigación*: cadena de fallback del resolutor + validador/reparador de JSON en el Kernel que
    degrada a `json_best_effort` con reintento acotado, contabilizado como coste real y journalizado
    (`model.degraded`).
13. **Ventana de 96k insatisfacible en el proveedor de hoy.** *Mitigación*: `window_budget` se acota
    a la ventana real del modelo resuelto; el perfil no falla, se degrada a `journal_replay` sobre
    una ventana menor con evento, y el `first_pass_yield` medido reflejará el coste.
14. **Deriva de perfiles.** *Mitigación*: `extends` obligatorio desde `base_operativo` y un lint de
    **indistinguibilidad para el router** (no de similitud textual): dos perfiles que el router nunca
    separa se fusionan.

### Riesgos aceptados

1. **El perfil sigue teniendo ~20 campos efectivos tras la resolución.** Por debajo de eso no se
   puede expresar autoridad, contexto y aceptación. Se acepta porque el *autor* sólo escribe 7 y el
   resto son presets versionados y auditables.
2. **Fase 1 clasifica todo tool de plugin como `irreversible` por defecto.** Molesto: pedirá
   aprobación de más hasta que existan manifiestos firmados. Se acepta porque fallar cerrado en
   autoridad es el único default defendible, y la fricción es visible y medible (contador de
   aprobaciones por sesión).
3. **El prior de `first_pass_yield` = 0.35 sobre-reserva presupuesto en las primeras 30 ejecuciones.**
   Se acepta: con $50.000 de crédito, sobre-reservar es gratis y sub-reservar mata misiones a mitad.
4. **El router seguirá equivocándose ~9% de las veces.** Se acepta porque, tras sacar la autoridad
   del perfil, el peor caso es exponer herramientas que no se usan más el coste de una transición
   (3-8k tokens, 1.5-4 s).
5. **No hay marketplace ni compartición cross-tenant de perfiles en las fases 0-3.** Se acepta:
   la compartición exige revisión de seguridad de perfiles de terceros, que es un proyecto propio.
6. **`team.shape` se limita a `solo | pipeline | supervisor_workers`.** Sin `debate`, sin topologías
   dinámicas. Se acepta hasta que exista un caso con coste medido.
7. **Las suites de eval nombradas por los perfiles de F2 no existirán hasta F3.** Durante ese hueco
   los perfiles corren con `verification_required=true`, lo que fuerza checks ejecutables reales.
   Se acepta: es la degradación honesta, y el repo ya tiene `packages/evals/` como base.
8. **Cambiar `HORIZON_PRESETS` cambia el comportamiento de todos los perfiles que heredan de él.**
   Se acepta porque el `preset_version` entra en el hash del `EffectiveProfile`: el cambio es
   auditable y las ejecuciones en curso conservan su versión, aunque una revisión descuidada del
   preset sigue pudiendo afectar a 12 dominios de golpe.

---

## 1. Kernel, Event Bus y Journal

El Kernel de Forge es un **reducer puro más un host imperativo**. El paquete `packages/forge-kernel/edecan_forge_kernel/` contiene una función total, síncrona y determinista, y nada más. El host (`LocalHost`, `DurableHost`) tiene sockets, disco, relojes y el bucle de eventos. Esa frontera es la única razón por la que el mismo código corre en un proceso Python sobre la Mac del usuario y dentro de un Durable Object sin una sola rama `if cloudflare:`, y es también la única razón por la que el replay determinista es posible.

Árbol de dependencias del kernel, pinneado y verificado en CI con un test que importa el paquete con un `sys.meta_path` restrictivo: `pydantic`, `blake3`, `cbor2`, stdlib. Nada más. Si algún día aparece `edecan_forge_fs`, `httpx`, `anyio` o `asyncio` en ese árbol, el diseño ya se rompió y el test debe fallar antes que la arquitectura.

### 1.1 La forma del kernel: `reduce`, y por qué no hay `await` dentro

El error que hunde a la mayoría de los "núcleos puros" es tener un núcleo puro que llama a un oráculo `async`. Si el kernel escribe `await oracle.completion(...)`, el kernel *es* asíncrono, *es* efectful, y el "núcleo puro" es marketing. Forge lo evita invirtiendo la relación: **el kernel no llama a nadie; devuelve peticiones de efecto y termina.**

```python
def reduce(state: KernelState, cmd: Command) -> Decision: ...   # PURA · SÍNCRONA · TOTAL
```

`reduce` no lanza excepciones de control de flujo: un comando inadmisible produce `Decision.rejection`, no un `raise`. Solo puede lanzar `AssertionError` ante corrupción del propio `state`, que es un bug del kernel y debe matar el proceso.

```python
class Stamp(BaseModel, frozen=True):
    """TODO lo no determinista, precargado por el host antes de entrar al kernel."""
    ts_physical: int          # epoch µs, leído por el host
    id_seed: bytes            # 16 B; el kernel deriva ULIDs deterministas de aquí
    lease_epoch: int          # fencing token monotónico del lease vigente
    observed_lamport: int     # máx. lamport visto por el host en mensajes entrantes

class Command(BaseModel, frozen=True):
    kind: str                       # "session.create" | "tool.request" | "effect.completed" | ...
    actor: Actor
    stamp: Stamp
    correlation_id: str             # obligatorio; en eventos raíz, self-correlating
    causation_id: EventId | None
    args: dict                      # ya validado por el adaptador; el kernel NO valida esquemas
    deadline_us: int | None

class Effect(BaseModel, frozen=True):
    id: str                         # determinista: blake3(cmd_hash || índice)[:16]
    kind: Literal["provider.complete","tool.invoke","cas.put","cas.get",
                  "timer.set","timer.cancel","stream.open","stream.close","proc.signal"]
    capability_id: str              # sin capacidad no hay efecto; el host lo verifica
    args: dict
    payload_ref: BlobRef | None
    deadline_us: int
    idempotency_key: str | None

class Decision(BaseModel, frozen=True):
    state: KernelState
    events: tuple[EventDraft, ...]
    effects: tuple[Effect, ...]
    rejection: Rejection | None      # XOR con (events, effects) no vacíos
```

El ciclo completo: el host recibe algo del mundo → construye un `Command` con su `Stamp` → llama a `reduce` → **persiste `Decision.events` (append) antes de ejecutar `Decision.effects`** → ejecuta los efectos → cuando cada efecto termina, vuelve a entrar con `Command{kind:"effect.completed"|"effect.failed", args:{effect_id, ...}}`. El kernel nunca espera nada. No hay estado "en vuelo" dentro de `reduce`; hay estado "efecto emitido, respuesta no recibida" dentro de `KernelState`, que es persistible, inspeccionable y reproducible.

Consecuencia: **el `Oracle` no es un componente del kernel, es un componente del host.** `LiveOracle` ejecuta efectos y graba los resultados en el journal; `ReplayOracle` los lee del journal y falla con `DivergenceError` si el kernel pide un efecto que no está grabado. El kernel es idéntico en ambos casos porque nunca supo que existían.

`event.id` es determinista: `ulid(ts_physical=stamp.ts_physical, entropy=blake3(stamp.id_seed || journal_id || seq)[:10])`. Dos ejecuciones del mismo comando con el mismo `Stamp` producen los mismos identificadores. Sin esto el replay produce un journal con hashes distintos y la comparación es imposible.

#### Negativas del kernel, cada una como ausencia de función

| Se niega a | Quién lo hace |
|---|---|
| Hablar HTTP, WS, SSE, gRPC | Host |
| Tocar el FS, lanzar procesos, abrir sockets | Plano de datos, vía `Effect` con capacidad |
| Llamar a un LLM o saber qué es un token | Bloque de proveedor, detrás de `Effect{kind:"provider.complete"}` |
| Guardar bytes (>4 KiB) | CAS; el journal transporta `BlobRef` |
| Saber qué es git, MCP, Python o un test | Plugins; el kernel solo conoce el ABI |
| Validar `input_schema` de una herramienta | Adaptador de plugin, antes de `tool.call_admitted` |
| **Decidir** una aprobación | Emite `approval.requested`; el motor de política responde con un `Command` |
| Reintentar nada | El supervisor, emitiendo un `tool.call_requested` nuevo con `attempt+1` |
| **Clasificar qué eventos necesitan `fsync`** | El productor lo declara en `EventDraft.durability`; el kernel obedece |
| Renderizar, paginar, formatear | Proyecciones y clientes |
| Autenticar o autorizar por identidad | El host verifica el token; el kernel solo lee `Actor.capability_id` |

La penúltima fila corrige el error de acoplamiento más grave del diseño anterior: una lista de tipos de dominio (`fs.write_committed`, `proc.spawned`) dentro del kernel para decidir durabilidad viola la invariante 10 tan claramente como un `import`. La durabilidad es un atributo del draft. Que *ciertos* tipos deban declararse siempre `strict` es una regla del **registro de esquemas**, verificada por un lint de CI sobre el registro, no por un `if` dentro del núcleo.

### 1.2 Identidad, tiempo y causalidad

```python
EventId    = str   # ULID-26
JournalId  = str   # "jr_" + ULID
Hash       = str   # "b3:<64 hex>"  (BLAKE3-256)

class Actor(BaseModel, frozen=True):
    kind: Literal["human","agent","kernel","plugin","provider","scheduler"]
    id: str
    capability_id: str | None      # None SOLO para kind == "kernel"

class BlobRef(BaseModel, frozen=True):
    hash: Hash
    size: int
    media_type: str
    codec: Literal["raw","zstd"] = "raw"

class Event(BaseModel, frozen=True):
    v: int                       # versión del esquema de ESTE `type`
    id: EventId
    seq: int                     # uint64, 1-based, CONTIGUO, sin huecos
    lamport: int
    ts_physical: int             # epoch µs; informativo, JAMÁS causal
    type: str                    # "<dominio>.<sustantivo>_<participio>"
    cls: Literal["control","fact","observation"]
    actor: Actor
    correlation_id: str
    causation_id: EventId | None
    lease_epoch: int             # fencing token bajo el que se escribió
    durability: Literal["grouped","strict"]
    payload_inline: dict | None  # XOR con payload_ref
    payload_ref: BlobRef | None
    prev_hash: Hash
    hash: Hash                   # blake3(CBOR canónico del evento sin `hash`)
```

Frente al diseño anterior desaparece `journal_id` de cada evento (es del header; ahorra ~30 B × 500.000 eventos y elimina la posibilidad de un evento que mienta sobre su journal) y desaparece `partition` (redundante con el header, y jamás se usaba para nada operativo). Aparecen `lease_epoch` (indispensable, ver 1.3), `durability` (el productor decide) y `cls` (indispensable para la reserva de emergencia de 1.9).

**Header del journal**, escrito una vez, firmado, e inmutable:

```python
class JournalHeader(BaseModel, frozen=True):
    journal_id: JournalId
    tenant_id: UUID              # RLS de Edecán: un journal sin tenant es un journal huérfano
    workspace_id: str
    session_id: str
    base: ForkBase | None        # ver 1.8
    min_reader_epoch: int        # un lector más viejo se NIEGA a abrirlo
    host_key_id: str             # kid Ed25519 del creador
    created_us: int
    signature: bytes
```

Que `tenant_id` viva en el header y no en cada evento es deliberado: el repo aísla por `tenant_id UUID NOT NULL` + política RLS `USING (tenant_id = current_setting('app.tenant_id')::uuid)` en toda tabla tenant-scoped, y el sumidero secundario en Postgres necesita ese valor. Derivarlo de una tabla mutable de mapeo `workspace → tenant` haría que el aislamiento dependiera de estado fuera del journal, violando la invariante 2 en el punto donde más caro sale. El header es auto-contenido y firmado; el sello (§1.3) también lleva `tenant_id` para que la unidad replicada al plano de control lo sea.

**Invariantes de datos**, todas verificables por un tercero sin ejecutar Forge:

- `seq` contiguo desde 1. Un hueco es corrupción, no consistencia eventual.
- `payload_inline` **xor** `payload_ref`. Registro serializado ≤ **8 KiB**, inline ≤ **4 KiB**.
- **Regla de contenido inline (nueva, y no negociable): un `payload_inline` solo puede contener identificadores, enumerados, números y booleanos. Ningún texto libre, ninguna ruta absoluta del usuario, ningún fragmento de código, ningún argumento de herramienta.** Todo eso va al CAS por `payload_ref`. La razón no es el tamaño: es que **el borrado selectivo solo es posible sobre el CAS** (§1.3, redacción). Un journal append-only con hash chain y datos personales incrustados en línea es un sistema que no puede cumplir una petición de borrado sin destruirse. Esta regla es lo que hace compatibles la invariante 2 y el derecho al olvido.
- `causation_id`, si existe, apunta a un `seq` estrictamente menor **en el mismo journal**, o a un `EventId` de otro journal cuyo `lamport` es estrictamente menor. Nunca al futuro.
- `hash` es determinista sobre CBOR canónico (RFC 8949 §4.2.1: claves ordenadas, enteros mínimos). **Sin floats en ninguna parte del sistema**: el dinero va en `micro_usd: int` (1e-6 USD) y los tokens en `int`. Un journal con floats no es auditable ni comparable entre plataformas.
- `lease_epoch` es monotónico no decreciente a lo largo del journal.

**Lamport, definido de forma implementable.** El diseño anterior decía "reloj lógico global (max(visto)+1)" sin decir global entre qué, lo que lo convertía en un campo decorativo. Aquí: `lamport` es un contador **local al journal**, y al construir un evento vale `max(state.lamport, cmd.stamp.observed_lamport) + 1`. `observed_lamport` lo transporta el host por *piggyback*: todo mensaje cross-journal (un merge, una aprobación, un delegado a otro agente) lleva el `lamport` del emisor, y el host receptor lo mete en el `Stamp`. Sin piggyback no hay causalidad cross-journal, y hay que decirlo en vez de asumirlo.

**Orden total intra-journal, parcial entre journals.** Un journal por sesión. Entre sesiones no hay orden total y no se finge. Se descarta el vector clock porque N agentes efímeros hacen crecer el vector sin techo. Para lo que sí necesita orden global —fusiones, aprobaciones, presupuesto compartido— existe un **meta-journal por workspace** que recibe solo `workspace.*`, `approval.*` y `budget.*`: cardinalidad ~100× menor y un único escritor.

### 1.3 Journal

```python
class Journal(Protocol):
    async def append(self, drafts: Sequence[EventDraft], *,
                     expected_seq: int, lease_epoch: int) -> AppendResult
    def read(self, *, from_seq: int, to_seq: int | None = None,
             types: PatternSet | None = None) -> AsyncIterator[Event]
    async def head(self) -> JournalHead              # (seq, hash, lamport, sealed_upto, lease_epoch)
    async def seal(self, upto_seq: int) -> SealReceipt
    async def verify(self, from_seq: int, to_seq: int, *,
                     budget_events: int = 5_000) -> VerifyReport   # troceable
    async def offload_prefix(self, upto_seq: int, *, snapshot: BlobRef) -> BlobRef
    async def redact(self, target_seq: int, *, reason: str, actor: Actor) -> Event
```

`append` es la **única operación mutante del sistema**. La durabilidad no es un parámetro de `append`: viene declarada por evento en `EventDraft.durability`, y un lote con al menos un `strict` se acaba con `fsync` antes del ACK.

**Un journal tiene exactamente un escritor lógico.** Esto hay que decirlo porque el diseño anterior lo dejaba abierto y era la ambigüedad más peligrosa del bloque: con `expected_seq` como único mecanismo, N agentes concurrentes en una misma sesión compiten por el CAS y entran en livelock bajo contención. La realidad es que `reduce` es síncrono y single-threaded por construcción: los agentes no appendean, **encolan `Command`**, y el bucle del host los serializa. `expected_seq` no es control de concurrencia intra-host; es **detección de split-brain entre hosts**.

Y `expected_seq` por sí solo no basta para el split-brain: un host zombi que leyó `head` justo antes de perder el lease puede ganar la carrera con el `expected_seq` correcto. Por eso el append lleva `lease_epoch`, un **fencing token monotónico** que el registro de leases incrementa en cada concesión. El almacenamiento rechaza cualquier append con `lease_epoch < ` el máximo ya visto, y ese rechazo es la barrera real. `expected_seq` detecta la carrera benigna; `lease_epoch` detiene al zombi.

**Durabilidad graduada, con números.** `grouped` hace group-commit con `fsync` cada 8 ms o cada 64 eventos, lo que llegue antes: p50 < 1,5 ms, p99 < 12 ms sobre NVMe, ~2.000 ev/s por journal. `strict` fuerza `fsync` antes del ACK: p50 ~2,5 ms, p99 ~9 ms. Regla de orden, que es lo que de verdad importa: **ningún efecto externo se ejecuta antes de que su evento de intención esté `fsynced`**, y ningún evento que referencie un blob se appendea antes de que el `cas.put` esté durable (ver "referencia colgante" en §Cómo se rompe). Los eventos `cls="observation"` toleran perder los últimos 8 ms.

**Coste del hash chain.** CBOR canónico + BLAKE3 sobre un evento de ~1 KiB: ~35 µs en CPython 3.12, es decir ~28.000 ev/s de capacidad de hashing en un core; a 2.000 ev/s consume ~7% de un core. Verificación completa de un segmento: ~15.000 ev/s. Por eso **el arranque no verifica el journal entero**: verifica hacia atrás desde `head` hasta el último sello, que son ≤256 eventos, < 20 ms. La verificación completa de 500.000 eventos son ~35 s y es una operación de mantenimiento explícita, troceada en lotes de 5.000 por `budget_events`.

**Sellado y modelo de amenaza honesto.** Cada 256 eventos o 30 s el kernel emite `kernel.seal_written{from_seq, to_seq, chain_hash, tenant_id, host_key_id, signature}`, Ed25519. Lo que hay que decir y el diseño anterior no decía: **si la clave vive en el disco junto al journal, la firma no defiende contra un atacante con acceso a ese disco**, porque reescribe el journal y re-firma. La firma local defiende contra corrupción del FS y contra procesos comprometidos sin acceso al llavero. Lo que hace la reescritura del pasado realmente detectable es el **notario**: los sellos se replican al plano de control, que conserva el primero de cada `host_key_id`; el atacante local no puede reescribir el historial del notario. Por tanto: clave en el llavero del SO (Keychain en macOS — el repo ya opera bajo TCC/Keychain), nunca en el directorio del journal; rotación mediante `kernel.host_key_rotated{old_kid, new_kid}` firmado con la clave saliente. Sin notario, el sellado es un checksum caro y hay que llamarlo así.

**Tres operaciones distintas sobre el pasado, que el diseño anterior mezclaba en dos:**

1. **Descarga (`offload_prefix`)** — el prefijo sale del almacenamiento caliente al frío. Exige `kernel.snapshot_written{cas_ref, at_seq}` verificado, el segmento ya replicado al CAS frío, y política de retención favorable. El evento sigue existiendo y sigue siendo verificable. No se pierde nada.
2. **Evicción de contenido (`ctx.blob_evicted{hash}`)** — el CAS borra un blob grande por presión de espacio (un stdout de 40 MB de hace seis meses). El evento y su hash sobreviven; un replay que necesite ese blob falla con `BlobMissing` citando el hash, y **nunca sustituye por vacío**.
3. **Redacción (`kernel.event_redacted{target_seq, reason, actor, tombstone_hash}`)** — obligación legal o secreto filtrado. **No reescribe el evento**: appendea un evento nuevo que marca el objetivo, y el contenido se destruye en el CAS. El `hash` original se conserva intacto y la cadena sigue verificando; lo que deja de ser recuperable es el contenido. Esto solo funciona gracias a la regla de payload inline de §1.2: si hubiera datos personales en línea, la redacción exigiría reescribir el evento y con él toda la cadena posterior. Es el motivo real de esa regla.

El journal **no se compacta destruyendo eventos** bajo ninguna circunstancia. Orden y causalidad son inmortales; contenido es mortal y con lápida.

### 1.4 Event Bus: dos canales

```python
class EventBus(Protocol):
    async def publish(self, drafts: Sequence[EventDraft]) -> AppendResult   # DURABLE → journal
    def emit(self, frame: StreamFrame) -> None                              # EFÍMERO → nunca al journal
    def subscribe(self, pattern: str, *, mode: DeliveryMode,
                  from_seq: int | None = None, queue: int = 256) -> Subscription

DeliveryMode = Literal["durable_replay", "live_lossy", "digest"]

class StreamFrame(BaseModel, frozen=True):
    anchor: EventId       # evento durable que abrió el stream
    channel: str          # "proc.stdout" | "turn.token_delta" | "fs.watch_hint" | ...
    ordinal: int
    bytes: bytes          # ≤ 64 KiB
```

**Entrega at-least-once en el bus, exactly-once en el efecto.** El exactly-once distribuido es folclore. Lo que sí se garantiza: (i) los eventos son idempotentes por `(journal_id, seq)`; (ii) toda proyección persiste `last_applied_seq` y descarta `seq <= last_applied_seq` en la misma transacción en que escribe su estado; (iii) toda herramienta con efecto externo declara `idempotency_key` en `tool.call_started`. Reentregar es entonces inofensivo y no hace falta mentir.

**Backpressure con histéresis.** El diseño anterior desconectaba al suscriptor lento a los 256 eventos encolados y lo mandaba a catch-up por el journal. Con un productor a 2.000 ev/s y un móvil en 3G, eso es un ciclo infinito desconexión→catch-up→desconexión. Contrato corregido: un suscriptor desconectado hace catch-up leyendo el journal desde `last_applied_seq`, y solo **vuelve a `live` cuando su lag es < 64 eventos durante dos ventanas de 1 s consecutivas**. Si no converge tras 3 intentos, se degrada automáticamente a `digest`: recibe únicamente `cls="control"`, `*.failed`, `approval.*`, `turn.*` y un heartbeat de 1 Hz con `head.seq`. Un cliente en `digest` sabe que está en `digest` y lo muestra; nunca finge estar al día.

Los suscriptores `live_lossy` sufren drop-oldest y reciben `StreamGap{from_ordinal, to_ordinal}` para que la UI muestre "…" honestamente en vez de un stdout con un agujero invisible.

**Presupuesto de ancho de banda, no cuenta de suscriptores.** El límite relevante no es "64 suscriptores" (número arbitrario) sino MiB/s agregados: **5 MiB/s por sesión, 1 MiB/s por suscriptor, coalescing de 50 ms, frame ≤ 64 KiB**. Superarlo degrada al suscriptor más caro a `digest`, en ese orden.

**Alto volumen fuera del journal.** `proc.stdout`, `proc.stderr`, `turn.token_delta`, `fs.watch_hint`, `tool.progress_tick` y `ctx.retrieval_trace` van **solo** por `emit`. Cada stream se ancla a un evento durable de apertura y **termina en un evento durable de cierre** que lleva el contenido íntegro al CAS:

```
proc.spawned{argv_ref, cwd, capability_id}                       → journal (strict)
  ~ 12.400 frames proc.stdout                                     → bus efímero, jamás journal
proc.output_sealed{stream:"stdout", ref:BlobRef, bytes, lines,
                   truncated_head:bool}                           → journal
proc.exited{code, signal, duration_ms}                            → journal (strict)
```

Un `pnpm install` verboso son 3 eventos en el journal, no 12.000. Sin esta regla la invariante 2 es una bomba de disco en la primera semana.

Suscripción por patrón glob de dos niveles (`tool.*`, `*.failed`, `provider.error_observed`), compilado a trie, matching O(1) amortizado. Sin regex, para que el coste del matching no dependa de lo que escriba un cliente.

### 1.5 Taxonomía y registro de esquemas

El diseño anterior enumeraba 13 dominios × ~6 verbos ≈ 78 tipos y los presentaba todos como fase 1. Eso son 78 esquemas, 78 upcasters futuros y 78 journals de oro antes de ejecutar una sola herramienta. Corrección: el **espacio de nombres** se pinnea entero (barato, y la invariante 9 exige que las interfaces asuman N agentes), pero se distingue entre tipos **activos** y **reservados**.

```python
class SchemaRegistry(Protocol):
    def descriptor(self, type: str, v: int) -> TypeDescriptor
    def is_active(self, type: str) -> bool
    def upcasters(self, type: str, from_v: int, to_v: int) -> Sequence[Upcaster]

class TypeDescriptor(BaseModel, frozen=True):
    type: str; v: int
    cls: Literal["control","fact","observation"]
    status: Literal["active","reserved","deprecated"]
    requires_strict: bool          # lint de CI, NO un `if` dentro del kernel
    payload_model: type[BaseModel]
```

Emitir un tipo `reserved` es un error del productor y el adaptador lo rechaza. Un tipo se retira con `kernel.schema_migrated` y `status="deprecated"`, nunca borrándolo.

**Dominios (espacio completo, reservado):**

- **session**: `created, resumed, forked, branched_from, quiesced, closed`
- **agent**: `spawned, role_assigned, paused, resumed, delegated, terminated`
- **turn**: `started, prompt_sealed, completion_received, malformed_output_observed, stop_reason_observed, cancelled, failed`
- **tool**: `call_requested, call_admitted, call_rejected, call_started, call_suspended, call_completed, call_failed, call_cancelled, call_orphaned, call_unknown`
- **fs**: `read_observed, write_committed, delete_committed, rename_committed, snapshot_taken, conflict_detected`
- **proc**: `spawned, output_sealed, exited, killed, signaled`
- **ctx**: `window_assembled, item_admitted, item_evicted, compaction_applied, blob_evicted, blob_dangling`
- **plugin**: `discovered, handshake_completed, capabilities_granted, manifest_changed, quarantined, unloaded`
- **provider**: `selected, request_sent, response_received, error_observed, capability_probed, fallback_applied`
- **approval**: `requested, policy_matched, granted, denied, expired`
- **budget**: `allocated, charged, warned, exhausted, released`
- **workspace**: `created, branched, merge_requested, merge_applied, merge_rejected, discarded`
- **kernel**: `started, seal_written, snapshot_written, recovered, schema_migrated, event_redacted, host_key_rotated, clock_skew_observed, draining, stopped`

**Activos en fase 1 (24 tipos):** `session.created/resumed/closed`, `agent.spawned/terminated`, `turn.started/prompt_sealed/completion_received/malformed_output_observed/failed`, `tool.call_requested/admitted/started/completed/failed/orphaned`, `proc.spawned/output_sealed/exited`, `budget.charged/exhausted`, `kernel.started/seal_written/stopped`. El resto se activa en su bloque.

**`turn.malformed_output_observed` es un tipo de primera clase, no un caso raro.** El cerebro base es Kimi K3 sobre Workers AI: sin garantía de JSON estricto y con tool-calling menos fiable que Claude o GPT. El repo ya lo admite implícitamente — el contrato de herramienta actual documenta que `args` "viene de lo que decidió el modelo, validado solo del lado del proveedor LLM, sin garantías fuertes". Si el reparseo y el reintento de extracción no dejan evento, el coste en tokens de esos reintentos es invisible en `cost_ledger` y el journal miente sobre lo que costó el turno. Payload: `{raw_ref: BlobRef, parser: str, attempt: int, failure: Literal["not_json","schema_mismatch","truncated","no_tool_call"]}`.

También pinneado aquí: `StopReason` del contrato de proveedor existente es `Literal["end","tool_use","max_tokens"]`; el bloque de proveedor debe normalizar a ese conjunto y registrar el valor nativo del proveedor en `provider.response_received`, porque un `content_filter` de Workers AI colapsado a `"end"` es indistinguible de una respuesta legítima y produce agentes que se paran en silencio.

### 1.6 Máquinas de estado

**Kernel:** `cold → loading → replaying → ready → running → draining → quiesced → stopped`, más `degraded`.

`degraded` (que el diseño anterior mencionaba una vez y nunca definía) significa: el journal es legible pero no escribible — no se pudo adquirir el lease, o el disco está lleno, o el `lease_epoch` fue rechazado. En `degraded` el sistema **sirve proyecciones de solo lectura, permite `read`/`verify`/`fork`, y rechaza todo `Command` que produzca eventos con `KernelReadOnly`**. Es un estado visible en la UI, no un cuelgue. Nunca se degrada silenciosamente a "escribir en memoria y ya sincronizaremos": eso es cómo se pierden datos.

**Arranque en frío:** (1) adquirir lease (TTL 30 s, renovación cada 10 s) y recibir `lease_epoch`; sin lease se entra en `degraded`. (2) Verificar la cadena hacia atrás desde `head` hasta el último sello (≤256 eventos). (3) Truncar la cola rota si la hubo y emitir `kernel.recovered{lost_events, from_seq}`. (4) Cargar el snapshot de proyección más reciente y reproducir el delta. (5) Descubrir plugins. (6) Handshake. (7) Reconciliar trabajo huérfano. (8) `kernel.started` → `ready`.

**Handshake de capacidades**, deadline de 3 s por plugin:

```
plugin.discovered{manifest_hash, abi_version}
→ plugin.handshake_completed{tools:[ToolDescriptor], capabilities_requested:[Scope],
                             supports_idempotency_probe: bool}
→ plugin.capabilities_granted{capability_id, scope_hash, ttl_s, budget}
   | plugin.quarantined{reason: "abi_mismatch"|"timeout"|"scope_denied"}
```

Regla no negociable: la concesión se ata a `manifest_hash`. Si el binario o el manifiesto cambian entre arranques se emite `plugin.manifest_changed`, **se revocan todas las capacidades previas** y hace falta re-aprobación. Un permiso que sobrevive a un cambio de identidad del ejecutable es un permiso zombi, y los permisos zombis son la vía estándar de escalada — este repo ya tiene cicatriz de eso en el frente de TCC de macOS.

**Máquina de estados de la llamada a herramienta**, pinneada (el diseño anterior la citaba sin dibujarla, y "suspended"/"unknown" aparecían de la nada):

```
requested ──► admitted ──► started ──► completed   (terminal)
    │            │            ├──────► failed      (terminal)
    │            │            ├──────► cancelled   (terminal)
    │            │            ├──────► suspended ──► started
    └──► rejected (terminal)  └──────► orphaned ──► completed | failed | unknown (terminal)
```

Terminales: `completed`, `failed`, `cancelled`, `rejected`, `unknown`. Cualquier otra transición es un bug del kernel y produce `AssertionError`. `suspended` exige checkpoint escrito antes de la transición: es lo que hace reanudable un turno a mitad de herramienta (invariante 8).

**Reconciliación tras crash.** El caso duro es *efecto ejecutado, evento de cierre perdido*. Al arrancar, toda `tool.call_started` sin terminal produce `tool.call_orphaned{idempotency_key}`. Si el plugin declaró `supports_idempotency_probe: true` en el handshake, el kernel emite un `Effect{kind:"tool.invoke", args:{probe: idempotency_key}}` y cierra con el resultado real. **En fase 1 esperamos que casi ningún plugin lo soporte**, así que el camino normal no es la sonda: es `tool.call_unknown` + `approval.requested{kind:"orphan_resolution"}`. Ese flujo de aprobación tiene que estar bien hecho desde el día uno, no tratado como caso exótico. El kernel jamás adivina si un `git push` ocurrió.

**Quiescencia** (única ventana legal para migrar el lease): cero comandos en vuelo, cero efectos emitidos sin respuesta, toda llamada de herramienta en estado terminal o `suspended` con checkpoint, `fsync` completado, todas las proyecciones con `last_applied_seq == head.seq`, y `kernel.quiesced{at_seq, state_hash}` en el journal.

**Apagado ordenado** (SIGTERM, presupuesto 25 s): `draining` rechaza comandos nuevos con `KernelDraining` → cancela turnos con `checkpoint=True` → cada plugin recibe `on_checkpoint` con 5 s → `seal` → `kernel.stopped`. Agotado el presupuesto, `kernel.stopped{dirty:true}` y el siguiente arranque entra por recuperación.

### 1.7 Proyecciones

```python
class Projection(Protocol[S]):
    name: str
    version: int                       # bump ⇒ rebuild total obligatorio
    interested_in: frozenset[str]
    def initial(self) -> S: ...
    def apply(self, state: S, ev: Event) -> S: ...   # DETERMINISTA y sin I/O
    def state_hash(self, state: S) -> Hash: ...
    def snapshot_every(self) -> int: ...             # p. ej. 2048 eventos
```

`apply` debe ser **determinista y sin I/O**; la mutación in-place de `state` está permitida. La pureza que importa es "misma secuencia de eventos → mismo estado final", no la inmutabilidad estructural. El diseño anterior implicaba `S` inmutable reconstruido por evento y a la vez prometía ≥50.000 ev/s: en CPython eso son dos afirmaciones incompatibles. Números realistas y medibles: **≥20.000 ev/s** con un reducer que trabaje sobre dicts y dataclasses (nada de `model_validate` en el camino caliente); ≥50.000 solo si el reducer se compila. Con 20.000 ev/s, una sesión típica de 3k–40k eventos se reconstruye en 0,15–2 s y una patológica de 500k en ~25 s.

**Qué vive en el kernel:** el `Protocol`, el runner, el snapshotting y la contabilidad de `last_applied_seq`. **Qué no:** ninguna proyección concreta. `session_tree`, `task_state`, `cost_ledger`, `workspace_diff` y `approval_queue` son lógica de dominio y viven en sus módulos (`edecan_forge_orchestrator`, `edecan_forge_workspace`, …). Meter `workspace_diff` dentro de `edecan_forge_kernel` porque "es de fase 1" es exactamente el primer paso hacia el objeto-dios de 3.300 líneas que estamos tirando.

**Reconstrucción:** `DELETE` de la tabla materializada, `last_applied_seq = 0`, lectura secuencial. Se guarda `state_hash` en cada snapshot; si un rebuild produce un hash distinto al del snapshot para el mismo `seq`, hay un bug en la proyección o corrupción del journal, y ambas cosas se gritan (`projection.divergence_detected` en el módulo dueño, no en el kernel).

**Proyección envenenada:** si `apply` lanza en el evento 40.001, la proyección se marca `stalled{at_seq, error}` y **el journal sigue avanzando**. La UI muestra datos rancios con marca de agua. Una proyección rota nunca bloquea a un agente.

### 1.8 Replay, fork y determinismo

Tres clases de comportamiento, declaradas en el tipo:

| Clase | Ejemplos | En replay |
|---|---|---|
| `DETERMINISTIC` | `reduce`, asignación de `seq`, derivación de ULID, política de admisión | Se recomputa |
| `RECORDED` | LLM, red, reloj, aleatoriedad, `os.environ`, salida de herramienta | Se **lee del journal**; jamás se re-ejecuta |
| `EXTERNAL` | escritura de archivo, `git push`, envío de correo | Prohibido en replay; el sandbox lo bloquea |

Todo lo `RECORDED` entra por `Stamp` (reloj, entropía) o por `Effect` → `effect.completed` (todo lo demás). `ReplayOracle` devuelve lo grabado en orden y **falla con `DivergenceError`** si el kernel pide un efecto que no está en el journal. La divergencia es señal de bug, nunca algo que se parchee con un valor por defecto.

**Contrato de fork, pinneado** (el diseño anterior era ambiguo aquí y dos implementadores lo habrían leído distinto — ¿`seq` del hijo empieza en 1 o en `at_seq+1`? ¿`prev_hash` del primero es ceros o el hash del padre?):

```python
class ForkBase(BaseModel, frozen=True):
    parent_journal_id: JournalId
    at_seq: int
    at_hash: Hash            # hash del evento `at_seq` del padre
```

- `fork(journal, at_seq)` crea `jr_nuevo` cuyo **header** lleva `base=ForkBase(...)`.
- El `seq` del hijo **empieza en 1**. Cada journal numera desde 1 siempre; no hay excepciones que romperían todo lector ingenuo.
- El **primer evento del hijo tiene `prev_hash = base.at_hash`**, no ceros. `"b3:" + "0"*64` se reserva exclusivamente para journals sin `base`.
- El prefijo **no se copia**: el hijo apunta al prefijo sellado del padre y el CAS ya deduplica el contenido. Leer `read(from_seq=…)` con `from_seq` anterior a la bifurcación redirige transparentemente al padre, y el lector ve un `seq` **negativo o prefijado** — pinneado: el prefijo heredado se expone como `(parent_journal_id, seq)` explícito, nunca renumerado, para que un hash nunca dependa de por dónde se leyó.
- El primer evento del hijo es `session.branched_from{parent, at_seq, parent_hash}`.
- Coste de un fork: un evento y una fila. Esto es lo que hace viable "reintenta el turno 12 con otro proveedor" y las N ramas de la invariante 5.

**Lo que nunca es determinista** y hay que dejar de fingir: la salida del LLM (aun con `temperature=0` cambia con la versión del modelo del proveedor, y en Workers AI el usuario no controla cuándo se actualiza), el orden de finalización de herramientas concurrentes (por eso el orden autoritativo es el de `append`, no el de finalización real), el reloj de pared y cualquier lectura de la red.

### 1.9 Presupuestos, techos y reserva de control

El diseño anterior fijaba 10.000 eventos por turno y 500.000 por sesión, ambos **por sesión**, y decía que al superarlos "se cancela el agente". Dos fallos: con 20 agentes en una sesión, un agente en bucle mata a los 19 compañeros; y **cancelar requiere emitir eventos, en un journal que acaba de declararse lleno**. Contrato corregido:

| Dimensión | Techo | Titular |
|---|---|---|
| Eventos por turno | 2.000 | turno |
| Eventos por agente | 100.000 | agente |
| Eventos por sesión (journal) | 500.000 | sesión |
| Journal caliente | 2 GiB | sesión |
| Frames efímeros | 5 MiB/s sesión · 1 MiB/s suscriptor | bus |

**Reserva de control: el 1% superior de cada techo (5.000 eventos en la sesión, 20 en el turno) solo admite eventos `cls="control"`.** Al entrar en la reserva se emite `budget.warned{dimension, remaining}`; agotado el cupo ordinario, `budget.exhausted{dimension}` y cancelación **del titular de la dimensión que se agotó**, no de la sesión entera. Sin reserva, el evento que apaga el incendio es el que no cabe.

`budget.charged` lleva la unidad nativa del proveedor, la tarifa aplicada y el resultado en `micro_usd: int`: `{provider, native_unit, native_amount, rate_version, micro_usd}`. Workers AI factura en *neurons* y el próximo proveedor facturará en otra cosa; si el evento guardara solo el USD derivado, un cambio de tarifas reescribiría la historia del gasto o la haría inexplicable.

### 1.10 Versionado del esquema a 5 años

Cada `type` lleva su propia `v`. Dentro de una `v` solo se permiten cambios **aditivos y opcionales**. Renombrar un campo, estrechar un tipo, cambiar una unidad o cambiar la semántica exige `v+1` y un upcaster:

```python
class Upcaster(Protocol):
    type: str; from_v: int; to_v: int
    def upcast(self, payload: dict) -> dict          # PURA, TOTAL, sin I/O
```

Los upcasters se encadenan **al leer**; el journal en disco jamás se reescribe, porque reescribirlo invalidaría la cadena de hashes, que es exactamente el punto.

**Dos defensas separadas, que el diseño anterior mezclaba en una y por eso no defendía:**

1. **Journals de oro (CI, sobre payloads).** Cada `v` retirada deja un journal congelado en `packages/forge-kernel/tests/golden/<type>.v<N>.forgelog` **junto a su payload esperado tras upcastear**. El test compara `upcast_chain(golden_vN.payload) == expected_payload_latest`, dict contra dict. No compara el `state_hash` de una proyección: si lo hiciera, subir `session_tree.version` cambiaría el hash esperado, alguien actualizaría el número y la defensa se habría desactivado sola — que es precisamente el fallo que se quería evitar.
2. **`state_hash` de proyección (runtime, no CI).** Compara snapshot contra rebuild **dentro de la misma versión de proyección**. Detecta corrupción del journal y no determinismo en el reducer. No tiene nada que ver con el versionado de esquemas.

Los upcasters no se borran nunca. Un `type` se retira emitiendo `kernel.schema_migrated` y marcándolo `deprecated`, nunca eliminándolo del registro. El header fija `min_reader_epoch`: un lector demasiado viejo **se niega a abrir el journal** en vez de malinterpretarlo.

### 1.11 Dónde vive el kernel

En ambos planos, pero **un journal tiene exactamente un propietario a la vez**. El kernel es una librería; el lease vive en un Durable Object porque el DO da single-threading real y es el único componente barato con esa garantía.

- **Workspace local** (la Mac del usuario): kernel en proceso, journal en SQLite WAL local. El DO actúa de **notario**: recibe sellos y los eventos del meta-journal (`session.*`, `approval.*`, `budget.*`, `workspace.*`), nada más. Poner el kernel en el DO aquí añadiría 60–120 ms de RTT por evento a un sistema que emite miles y mataría el uso offline.
- **Workspace remoto** (contenedor/sandbox): kernel en el DO, colocalizado con el plano de control; journal sobre el SQLite del DO con el mismísimo esquema.
- **Migración**: solo en `quiesced`. `kernel.quiesced{at_seq, state_hash}` + transferencia del lease (con incremento de `lease_epoch`) + verificación de `state_hash` en destino.

**Límites reales del DO que el diseño tiene que respetar o revienta en producción:**

- 10 GB de SQLite por objeto → coherente con el techo de 2 GiB de journal caliente, pero `offload_prefix` y `verify` **no caben en un request**. Ambos se trocean en lotes de ≤5.000 eventos, con checkpoint en el propio storage del DO y continuación por `storage.setAlarm`. Un `verify()` de 500k eventos escrito como un bucle único es una implementación muerta.
- **El reloj del DO solo avanza con I/O**: `Date.now()` está congelado entre operaciones de E/S por mitigación de Spectre. Un lease cuya expiración se evalúe desde dentro de un DO ocioso **puede no expirar nunca**. Por tanto la expiración se evalúa de forma **perezosa, en el momento de la petición de adquisición** por el pretendiente, y el barrido periódico se hace con `storage.setAlarm`, nunca con un temporizador en memoria.
- Los DO se hibernan y se evictan sin aviso. El lease **no se libera solo**; su TTL absoluto es la única garantía.

El coste de equivocarse en el hosting es medio pero acotado: como el kernel es un reducer puro y `Journal` es un `Protocol`, mover el hosting es reemplazar la cáscara, no el núcleo.

### Alternativas descartadas

| Opción | Orden total | Replay / fork | Coste operativo | Portable CF ↔ local | Append p99 | Veredicto |
|---|---|---|---|---|---|---|
| **Event sourcing sobre log embebido (elegido)** | Trivial (`seq`) | Nativo, fork O(1) | Muy bajo, cero servicios | Sí (SQLite local ≡ SQLite del DO) | 9–12 ms | **Elegido** |
| CRUD + outbox sobre Postgres | Sí, pero el estado autoritativo son tablas mutables | Imposible: el historial es derivado y con pérdida | Medio (Postgres siempre arriba) | No: no hay Postgres en un DO ni offline | 5–20 ms + RTT | Viola la invariante 2 en el primer `UPDATE` |
| Log estilo Kafka (Redpanda / CF Queues) | Por partición | Sí, pero el fork exige copiar el topic | Alto (broker, retención, consumer groups) | No: no hay broker en la máquina del usuario | 10–50 ms | Infraestructura de flota para sesiones de una persona; Queues no da orden total ni lectura por offset arbitrario |
| SQLite local WAL con tablas mutables | Sí | No | Muy bajo | Sí | 2–8 ms | Misma tecnología que elegimos, modelo de datos equivocado |
| Tabla `events` append-only en Postgres con RLS | Sí | Sí | Medio-alto | No offline, no en DO | 6–25 ms + RTT | Descartado como **primario**; se conserva como **sumidero secundario** para auditoría multi-tenant del meta-journal, usando el `tenant_id` del header |
| Journal JSONL plano (el IDE actual) | Sí | Sí | Nulo | Sí | 1–3 ms | Sin índice por tipo, sin lectura por rango, sin transacción con las proyecciones. Su "compactación" **borra eventos reescribiendo el fichero**, y su `deque(maxlen=…)` los tira en memoria: es un journal que pierde el pasado por diseño |

**Kernel puro + host** frente a *kernel `async` con `Oracle` inyectado*: el `Oracle` `async` parece equivalente y no lo es. Un kernel que hace `await` tiene puntos de suspensión, y en cada punto de suspensión el estado es indeterminado, lo que hace el checkpoint (invariante 8) un problema abierto en vez de trivial. Con `reduce` síncrono, `KernelState` es serializable en cualquier instante por construcción. Coste de haberse equivocado: bajo — más código de plomería y más viajes host↔kernel (µs en proceso). Coste del error inverso: reescribir el núcleo, que es exactamente lo que le pasó al IDE actual.

**Hash chain + Ed25519 + notario** frente a *árbol de Merkle por segmento*: el Merkle da pruebas de inclusión O(log n) que ningún consumidor identificado necesita hoy y triplica el código de verificación. La cadena se elige ahora; el Merkle queda para fase 3 si aparece un auditor externo que pida pruebas de inclusión selectivas. **ULID** frente a **UUIDv7**: casi equivalentes; ULID gana por ser ordenable como texto plano en un `.forgelog` que un humano lee con `grep`.

**`lease_epoch` explícito** frente a *solo `expected_seq`*: se consideró confiar únicamente en el CAS de `seq`. No sobrevive al zombi que leyó `head` justo antes de perder el lease. El fencing token cuesta 8 bytes por evento y cierra la ventana; sin él, el split-brain es detectable a posteriori pero no prevenible.

### Cómo se rompe

1. **Split-brain de lease.** Dos hosts se creen dueños. Detección: el almacenamiento rechaza el append del zombi por `lease_epoch` obsoleto (`FencedOut`), no solo por `SeqConflict`. Recuperación: el perdedor forkea a `jr_…#b2` con `session.branched_from`; no se pierde ningún evento, pero el usuario ve dos ramas y **hay que decírselo explícitamente**, no fusionarlas en silencio.
2. **Cola rota tras crash.** Crash entre `write` y `fsync`: los últimos ≤8 ms de eventos `grouped` tienen hash inválido. Se descartan hasta el último sello y se emite `kernel.recovered{lost_events}`. Si lo perdido incluía un evento que debía ser `strict`, es un bug del **productor**, detectable por el lint de `requires_strict` en CI.
3. **Referencia colgante (no estaba en el diseño anterior).** Se appendea un evento con `payload_ref` cuyo `cas.put` no llegó a ser durable. El replay pide un blob que **nunca existió**, indistinguible de uno evicted. Prevención: orden estricto `cas.put fsynced → append`. Detección: `verify` con `check_refs=True` emite `ctx.blob_dangling{hash, at_seq}`. Es distinto de la evicción y hay que poder distinguirlos, porque uno es política y el otro es corrupción.
4. **Efecto sin evento.** Se ejecutó el `rm -rf` y el proceso murió antes del cierre. Mitigado por el orden intención→efecto y por `tool.call_orphaned`; **no eliminado** para herramientas sin `idempotency_key`, que terminan en `tool.call_unknown` y escalan a decisión humana.
5. **Tormenta de eventos.** Un agente en bucle. Techos de §1.9 + reserva de control del 1%; se cancela **el agente**, no la sesión. Sin la reserva, el `budget.exhausted` sería el evento que no cabe.
6. **CAS evicted / replay incompleto.** Falla con `BlobMissing` citando el hash; nunca sustituye por vacío. Coste aceptado: sesiones antiguas dejan de ser reproducibles aunque sigan siendo auditables.
7. **Proyección envenenada.** `apply` lanza en el evento 40.001. Se marca `stalled{at_seq}`, el journal sigue avanzando, la UI muestra datos rancios con marca de agua.
8. **Upcaster incorrecto.** Reescribe mal la semántica de un evento de 2027 y todas las proyecciones históricas mienten en silencio. Única defensa real: los journals de oro comparando **payloads**, no hashes de proyección. Sigue siendo el fallo más caro del bloque porque es indetectable en producción.
9. **Deriva de reloj físico.** `ts_physical` retrocede (NTP, suspensión del portátil). No afecta a la causalidad (Lamport manda) pero rompe los gráficos de duración. Se emite `kernel.clock_skew_observed{delta_us}`.
10. **Suscriptor que nunca converge.** Móvil en red mala: catch-up más lento que la producción. Se degrada a `digest` tras 3 intentos fallidos, y el cliente lo muestra. Sin la histéresis, es un ciclo infinito de reconexión que además consume el fan-out.
11. **Streams efímeros huérfanos.** El proceso muere y nunca llega `proc.output_sealed`: 40 MB de stdout que la UI vio y el journal no tiene. Al reconciliar se emite `proc.output_sealed{truncated_head:true, ref:<lo que se alcanzó a subir>}` y se acepta la pérdida parcial. Es el precio explícito de no journalar stdout, y es el precio correcto.
12. **Reloj congelado del DO.** El lease no expira porque `Date.now()` no avanza en un objeto ocioso. Mitigado por evaluación perezosa en la adquisición + `storage.setAlarm`. Si se implementa con un temporizador en memoria, el sistema se cuelga en un lease inmortal y **no hay señal de error**: es un fallo silencioso, y por eso está pinneado en §1.11.
13. **Petición de borrado sobre un journal firmado.** Sin la regla de payload inline estructural, el borrado exigiría reescribir eventos y con ello toda la cadena. Con ella, `redact` destruye el blob y appendea la lápida. Si algún productor viola la regla y mete texto libre en línea, ese evento **no es redactable** y el sistema se queda sin salida legal. Defensa: lint de CI que rechaza modelos de payload con campos `str` sin `max_length` y sin marca `structural=True`.

### Riesgos aceptados

- **La invariante 2 se cumple sobre estado y causalidad, no sobre contenido observacional transitorio.** Los frames de `emit` que la UI ve y el journal no tiene son un hueco real y consciente. La alternativa —journalar 200k líneas de un `pnpm install`— destruye replay, disco y la utilidad misma de la cadena de hashes. Queda registrado como reinterpretación de la invariante, no como cumplimiento.
- **El sellado local no defiende contra un atacante con acceso de escritura al disco y al llavero.** Defiende contra corrupción del FS y contra procesos comprometidos sin acceso a la clave. La detección real de reescritura del pasado depende del notario remoto. Si el usuario opera 100% offline y sin notario, el sellado es un checksum caro y hay que decirlo en la documentación de usuario.
- **Duplicación de efecto en herramientas sin `idempotency_key`.** Un crash en la ventana entre `tool.call_started` y el terminal puede duplicar un efecto externo si alguien reintenta a ciegas. Mitigado escalando a humano (`tool.call_unknown` + aprobación), no eliminado. En fase 1, con casi ningún plugin soportando sonda de idempotencia, esta ventana se abre a menudo.
- **Sin orden total entre sesiones.** Dos agentes en journals distintos pueden observar órdenes distintos de eventos concurrentes no relacionados causalmente. Aceptado: el meta-journal cubre lo que sí necesita orden global, y la alternativa (secuenciador central) añade un RTT a cada evento.
- **500 ms – 25 s de rebuild de proyección tras un bump de `version`.** Aceptado: es una operación rara y visible, y la alternativa (migración incremental de proyecciones) es código sutil para un caso poco frecuente.
- **El fork por referencia acopla la vida del journal hijo a la del padre.** Borrar el padre invalida el prefijo del hijo. Aceptado en fase 1 con una restricción de retención: un journal con hijos vivos no se elimina, solo se descarga a frío. Materializar el prefijo (`fork --detach`) queda para fase 3.

---

## 2. Workspace Manager, VFS, indexado y rendimiento a escala

Define `packages/forge-workspace/edecan_forge_workspace/` y `packages/forge-index/edecan_forge_index/`. Ambos viven en el **plano de datos** (invariante 3): no importan nada del orquestador, no abren conexiones a Postgres del plano de control y no escriben en el journal por su cuenta — emiten por un puerto `JournalSink` inyectado en el constructor.

Todo lo que sigue está dimensionado contra un caso de referencia único, que se cita como **REF**: 500k ficheros, 4 GB de árbol, 25M LOC, 20 worktrees concurrentes del mismo workspace, host de 8 núcleos y 32 GB de RAM.

---

### 2.0 Dos superficies, no una

El error que hunde este bloque si no se corrige de entrada: diseñar una sola API y dársela al modelo. El VFS que aquí se especifica es **una superficie de máquina** — habla de hashes, de refs, de read-sets, de txns. El cerebro base es Kimi K3 en Workers AI: sin garantía de JSON estricto, con tool-calling menos fiable que Claude y sin capacidad de acarrear un `b3:` de 64 hex a través de tres turnos sin corromperlo. Cualquier contrato que exija que el modelo *recuerde un hash* es una dependencia encubierta de un modelo fuerte y está prohibida por la constitución.

Por tanto se fijan dos capas y la frontera entre ellas:

| | `Vfs` / `VfsTxn` (esta sección) | Herramientas de fichero expuestas al modelo (bloque 4) |
|---|---|---|
| Consumidor | adaptadores de herramienta, indexador, materializador, merge | el LLM |
| Identidad de contenido | `ContentRef` explícito y obligatorio | **ninguna**: el adaptador la resuelve |
| Direccionamiento de edición | anclado por texto o por rango decodificado | anclado por texto, nunca por offset ni por número de línea |
| Concurrencia | `expect` explícito, `TxnConflict` propagado | reintento transparente hasta 3 veces, luego escalada |
| Errores | tipados, sin prosa | una frase accionable en lenguaje natural |

**Regla dura: el adaptador rellena `expect` desde el read-set de la txn (`read-your-reads`), nunca el modelo.** Si una herramienta lee `src/a.py` y después escribe `src/a.py` dentro de la misma txn, el `expect` es el ref que se leyó, sin que nadie lo mencione. Un `expect` pasado a mano existe solo para clientes que ya tienen el hash (el indexador, el merge, la UI web con estado). El modelo nunca ve un hash salvo en un mensaje de conflicto, y ahí es opaco.

---

### 2.1 Modelo de datos

Cinco entidades. La distinción crítica es entre lo *lógico* (durable, portable, pequeño) y lo *materializado* (desechable, pesado, local, reconstruible).

| Entidad | Qué es | Vida | Verdad autoritativa |
|---|---|---|---|
| `Project` | uno o más `RepoSource` montados en prefijos + reglas de clasificación | permanente | journal del control |
| `Workspace` | vista de un `Project` anclada a un `SnapshotId` base; N por tenant | días–meses | journal del control |
| `Worktree` | rama COW de un `Workspace`; una por agente **por defecto** | minutos–horas | **fold del journal** (ver abajo) |
| `Snapshot` | árbol Merkle inmutable (raíz + header + padre) | inmortal hasta GC | el propio CAS (auto-verificable) |
| `VfsTxn` | ventana de lectura consistente + mutaciones staged de un turno | segundos–minutos | lease, ver 2.2 |

**Dónde vive la verdad (corrección respecto al borrador).** El borrador decía que el `Worktree` "vive en datos" sin decir dónde vive su *head*. Un head mutable fuera del journal viola la invariante 2 de forma trivial. Se fija:

> `head(worktree_id)` es **el fold del journal**: el último `vfs.committed | worktree.forked | merge.applied | vfs.external_writes_reconciled` para ese `worktree_id`. El sidecar mantiene un registro `head_cache = {worktree_id: (SnapshotId, journal_seq)}` que es una **proyección con checkpoint**, no un almacén. Al arrancar, el sidecar lee su checkpoint y reproduce el journal desde `journal_seq`. Si el checkpoint no existe o está corrupto, reproduce desde el `worktree.forked`. Nunca hay una escritura al head que no sea consecuencia de un evento ya durable.

**Punto de linealización del commit.** `VfsTxn.commit()` **no retorna** antes de que el `vfs.committed` esté durable en el journal. Orden exacto: (1) los objetos y árboles nuevos se escriben en el CAS — idempotente, sin orden, sin daño si el commit aborta después, solo basura para el GC; (2) se hace `append` al journal de `vfs.committed{worktree_id, parent, snapshot, changed, read_set_digest, txn_id}` y se espera su acuse de durabilidad; (3) se actualiza `head_cache`; (4) se retorna `CommitResult` con el `journal_seq`. Una caída entre (1) y (2) pierde el trabajo del turno pero **no puede** dejar el sistema en un estado en el que el head y el journal discrepen, que es el único fallo inaceptable. Coste medido: el paso (2) añade 2–8 ms al commit y es el motivo de que `vfs.commit` de 20 ficheros presupueste 8 ms p50 y no 3 ms.

**Monorepos y submódulos no son un caso especial.** Un `Project` tiene una lista de `RepoSource` montados en prefijos (`{"": "git@…/edecan", "vendor/foo": "git@…/foo"}`) y el `Snapshot` es un único árbol Merkle que los superpone. El agente ve un solo sistema de ficheros. El mapeo de vuelta a repos reales solo hace falta al hacer `push`, y lo resuelve el plugin de VCS, no el kernel (invariante 6). Prefijos solapados se rechazan al registrar el `Project`, no al montar.

#### Tipos

```python
ContentRef  = NewType("ContentRef",  str)   # "b3:<64 hex>"
SnapshotId  = NewType("SnapshotId",  str)   # "snap:b3:<64 hex>"  = hash(header ‖ root_tree)
WorktreeId  = NewType("WorktreeId",  str)
TxnId       = NewType("TxnId",       str)
```

`NewType`, no `str`. Un `ContentRef`, un `SnapshotId` y un `path` son todos texto; sin tipos nominales dos implementadores los intercambian y el type checker calla. Es la clase de defecto que solo aparece en producción.

```python
class PathClass(StrEnum):
    SOURCE = "source"; DATA = "data"; BINARY = "binary"
    VENDORED = "vendored"; LFS = "lfs"; IGNORED = "ignored"

@dataclass(frozen=True)
class TreeEntry:
    name: str                      # sin "/", sin ".", sin "..", NFC, sin bytes de control
    mode: int                      # 0o100644 | 0o100755 | 0o040000 | 0o120000
    ref: ContentRef                # para symlink: hash del target literal
    size: int
    cls: PathClass

@dataclass(frozen=True)
class FileStat:
    path: PurePosixPath
    ref: ContentRef
    size: int
    mode: int
    cls: PathClass
    lang: str | None               # derivado versionado: (ref, "langdet", v)
    link_target: str | None        # solo mode 0o120000; verbatim, sin resolver
    link_escapes_root: bool        # True si absoluto o si sube por encima de la raíz
```

Se elimina el campo `is_truncatable` del borrador: no tenía semántica definida y dos implementadores lo leerían distinto. La truncabilidad es una propiedad de la *lectura* (`ReadResult.truncated`), no del fichero.

**Hash: BLAKE3 (`b3:`).** El prefijo hace de la migración un problema de compatibilidad, no de rediseño. La justificación honesta no es "3–10 GB/s": en REF, con 500k ficheros pequeños, el cuello de botella es `open`/`read`/`close`, no el hash, y la ingesta se mide en 400–900 MB/s independientemente del algoritmo. La justificación real es la que sí se usa: el árbol de hash interno permite **verificar un `byte_range` traído de `ObjectStore` remoto sin descargar el objeto completo**, que es exactamente lo que hace `read(byte_range=…)` sobre un blob de 2 GB en R2. Si no fuéramos a usar eso, SHA-256 estaría igual de bien.

**Chunking.** Ficheros > 1 MiB se parten con **FastCDC** (media 64 KiB, mín 16 KiB, máx 256 KiB) y el `ContentRef` del fichero apunta a una lista de chunks. Editar una línea de un CSV de 200 MB cuesta ~64 KiB nuevos. Por debajo de 1 MiB el chunking cuesta más de lo que ahorra. Nótese que el chunking de FastCDC (content-defined, para deduplicar) y el árbol interno de BLAKE3 (chunks fijos de 1 KiB, para verificar) son cosas distintas y coexisten sin relación.

---

### 2.2 VFS

```python
class Vfs(Protocol):
    async def stat(self, path: str) -> FileStat | None: ...
    async def read(self, path: str, *, byte_range: tuple[int, int] | None = None,
                   max_bytes: int = 1_048_576) -> ReadResult: ...
    async def read_ref(self, path: str) -> ContentRef | None: ...
    async def list(self, path: str, *, depth: int = 1, limit: int = 1000,
                   cursor: str | None = None) -> DirPage: ...
    async def open_txn(self, *, intent: str, lease_ms: int,
                       cap: CapabilityToken) -> VfsTxn: ...

class VfsTxn(Protocol):
    txn_id: TxnId
    base: SnapshotId
    async def write(self, path: str, data: bytes | ContentRef, *,
                    expect: Expect | None = None) -> None: ...
    async def patch(self, path: str, edits: Sequence[TextEdit], *,
                    expect: Expect | None = None) -> None: ...
    async def delete(self, path: str, *, expect: Expect | None = None) -> None: ...
    async def rename(self, src: str, dst: str, *, expect: Expect | None = None) -> None: ...
    def deps(self) -> Deps: ...
    async def renew(self, *, extra_ms: int) -> None: ...
    async def commit(self, *, policy: ConflictPolicy = "strict") -> CommitResult: ...
    async def abort(self) -> None: ...
```

```python
Expect = ContentRef | Literal["absent"] | Literal["any"]
ConflictPolicy = Literal["strict", "rebase_if_disjoint"]

@dataclass(frozen=True)
class ReadResult:
    ref: ContentRef
    data: bytes | None            # None para BINARY/LFS sin byte_range explícito
    text: str | None              # decodificado si la codificación es conocida
    encoding: str | None          # "utf-8" | "utf-8-sig" | "utf-16le" | "latin-1" | None
    newline: Literal["lf", "crlf", "cr", "mixed"] | None
    truncated: bool
    total_size: int
    summary: str | None           # descripción sintética para BINARY/LFS

@dataclass(frozen=True)
class CommitResult:
    snapshot: SnapshotId
    parent: SnapshotId
    journal_seq: int              # punto de linealización; commit() no retorna antes
    changed: tuple[PathChange, ...]
```

#### 2.2.1 Edición anclada: `TextEdit`

El borrador dejaba `TextEdit` sin definir. Es el contrato que más veces al día ejecuta el sistema y el que un modelo débil rompe primero. Se fija:

```python
@dataclass(frozen=True)
class TextEdit:
    old: str                  # texto exacto a sustituir, >= 1 carácter
    new: str
    anchor_before: str = ""   # contexto inmediatamente anterior, para desambiguar
    occurrence: int = 1       # 1-based, sobre las apariciones de (anchor_before + old)
```

Sin offsets de byte y sin números de línea: un modelo no cuenta bytes y las líneas se desplazan entre turnos. Resolución: se busca `anchor_before + old` sobre el **texto decodificado**; si hay 0 apariciones → `EditNotAnchored(path, preview)`; si hay más de una y `occurrence` no está dentro del rango → `EditAmbiguous(path, found=n)`. Las `edits` de una misma llamada se resuelven **todas contra el texto original** y se aplican de una vez; spans solapados → `EditOverlap`. Es determinista y replayable.

**Normalización.** El CAS guarda bytes verbatim; nunca se reescribe un fichero entero por normalizar. La decodificación usa la codificación detectada en la ingesta (derivado versionado); el resultado se reescribe con **la misma codificación, el mismo BOM y el mismo final de línea dominante** del fichero. Los finales de línea fuera del span editado se preservan byte a byte, incluso en un fichero `mixed`. Un fichero cuya codificación no se detecta se clasifica `DATA` y `patch()` lo rechaza con `NotTextual` — se edita con `write()` de bytes o no se edita.

#### 2.2.2 Concurrencia: OCC con dependencias tipadas

El borrador decía "read-set con granularidad de fichero". Eso deja dos agujeros que hacen el OCC *incorrecto*, no solo grueso.

**Agujero 1: lecturas fantasma.** El agente hace `list("src/")`, concluye que `src/nuevo.py` no existe, lo crea. Otro agente lo creó entretanto. Un read-set de solo `(path, ref)` no puede expresar "observé esta ruta ausente" ni "observé este directorio con este contenido". El tipo `frozenset[tuple[str, ContentRef]]` del borrador es literalmente inexpresivo para esto.

**Agujero 2: read-set no acotado.** Un `list(depth=6)` sobre REF mete 500k entradas en el read-set y hace que cualquier commit posterior colisione con todo.

Corrección: el read-set pasa a ser un conjunto de **dependencias tipadas**, con degradación explícita y acotada.

```python
@dataclass(frozen=True)
class FileDep:    path: str; ref: ContentRef           # leí este fichero con este contenido
@dataclass(frozen=True)
class AbsentDep:  path: str                            # observé esta ruta ausente
@dataclass(frozen=True)
class DirDep:     path: str; digest: str; depth: int   # listé este subárbol con este digest
@dataclass(frozen=True)
class SubtreeDep: path: str; ref: ContentRef           # dependencia degradada: el árbol entero

Dep  = FileDep | AbsentDep | DirDep | SubtreeDep
Deps = frozenset[Dep]
```

Reglas, no heurísticas:

- `read`/`stat`/`read_ref` con resultado → `FileDep`. Sin resultado → `AbsentDep`.
- `list` → `DirDep` con `digest` = hash del árbol listado a esa profundidad. Sound y barato: el digest ya está en el nodo Merkle, no se calcula nada.
- **Cap: 4.096 dependencias por txn.** Al superarlo, las dependencias se colapsan por prefijo común al `SubtreeDep` que las cubra (el hash del nodo del árbol). Es estrictamente más conservador — nunca produce un falso "sin conflicto" —, solo más propenso a falsos conflictos. La degradación se registra en `vfs.txn_degraded{txn_id, from, to}` para que se pueda medir si el cap está mal puesto.
- **La búsqueda (léxica, simbólica, vectorial) NO produce dependencias.** Es una decisión, no un olvido: si un `grep` inyectara un `SubtreeDep` de la raíz, todo commit posterior a cualquier búsqueda conflictaría siempre. Consecuencia asumida y documentada en Riesgos aceptados: dos agentes pueden tomar decisiones basadas en resultados de búsqueda desactualizados sin que el OCC lo note. La red de seguridad es el merge y los tests, no el OCC.

`commit(policy="strict")` verifica cada `Dep` contra el head actual del worktree. `policy="rebase_if_disjoint"` reintenta una vez sobre el head nuevo si el conjunto de escrituras es disjunto del cambio ajeno; es una optimización para el bucle de reintentos del adaptador, no una relajación del aislamiento.

**Por qué no locks.** Un turno de agente dura entre 5 s y 20 min. Un lock de fichero mantenido ese tiempo es un deadlock con otro nombre, y serializa a los 20 agentes que la invariante 9 exige. Los CRDT resuelven un problema que no tenemos (edición humana concurrente carácter a carácter) y destruyen el replay determinista. Último-escritor-gana pierde trabajo en silencio, que es exactamente lo que la invariante 8 prohíbe.

**Livelock.** Backoff exponencial con jitter (base 200 ms, tope 5 s), contador de reintentos en el evento, y **al tercer fallo el commit deja de reintentar y se convierte en una `MergeProposal`**, que es un objeto de primera clase y no un error. Un agente lento sobre un fichero caliente termina, no gira.

#### 2.2.3 La txn es un lease, y su staging va al CAS

El borrador ponía la `VfsTxn` "en memoria del sidecar" y le daba un `deadline_ms` sin decir qué pasa al vencer. Dos defectos:

1. Un turno de 20 minutos que escribe 500 MB de staged deja 500 MB residentes, contra un tope duro de 640 MiB para todo el sidecar. **Corrección: `write()` y `patch()` escriben el contenido al CAS inmediatamente** — es contenido direccionado, así que escribirlo es idempotente y su único coste si la txn aborta es basura para el GC. La txn retiene solo el mapa `{path: (op, ContentRef)}`: unos cientos de bytes por fichero tocado. Una txn de 10.000 ficheros ocupa ~1 MiB, no gigabytes.
2. `deadline_ms` sin semántica es una fuga de recursos. **Corrección: `lease_ms` (renovable con `renew()`).** Al vencer: la txn pasa a `EXPIRED`, toda operación posterior lanza `TxnExpired`, se emite `vfs.txn_expired{txn_id, staged_paths}` y los refs staged quedan como basura recuperable. `commit()` sobre una txn expirada **falla siempre**: es preferible perder un turno a integrar un commit contra un base que ya nadie recuerda. El reloj es monótono, no de pared. Un sidecar que reinicia declara expiradas todas las txns que no encuentra en su checkpoint.

Máquina de estados de `VfsTxn`, completa: `OPEN → (COMMITTING → COMMITTED | CONFLICT) | ABORTED | EXPIRED`. `CONFLICT` es terminal: no se puede seguir escribiendo en una txn que ya falló; el adaptador abre una nueva.

#### 2.2.4 Por qué se estrangula el acceso al FS

Cinco razones, ninguna estética: (1) las capacidades de la invariante 7 solo son imponibles en un punto de estrangulamiento — un `open(2)` no se puede interceptar sin FUSE ni eBPF; (2) el journal debe ser completo (invariante 2) y una escritura cruda no es replayable; (3) las dependencias del OCC y la procedencia del contexto ("¿de qué ficheros dependió esta decisión?") solo se capturan interceptando; (4) el CAS y el fork barato dependen de conocer los hashes; (5) determinismo de replay.

---

### 2.3 Los dos regímenes de consistencia, y la carrera que el borrador no vio

El borrador presentaba `projected` y `materialized` como dos modos y decía que el watcher reconcilia al final. Eso **pierde datos** en el caso más frecuente del sistema, que es exactamente este:

> El agente materializa el worktree, lanza `npm run build` (90 s), y mientras corre edita `src/a.ts` por el VFS en modo proyectado. El build escribe `src/a.ts` (o un `prettier --write` lo hace). Al terminar, la reconciliación toma el estado del disco como verdad y **borra en silencio la edición del agente**. O al revés: la edición del VFS vive en el overlay del CAS y el build compiló la versión vieja, así que el agente lee un resultado que no corresponde a su código.

Además, el borrador afirma que "projected es el default y materialized solo para ejecutar procesos", pero el bucle real de cualquier agente es *editar → ejecutar tests → editar → ejecutar tests*. En la práctica el worktree está materializado la mayor parte del tiempo. El régimen materializado no es la excepción: es el estado normal.

**Corrección: la materialización es una ventana exclusiva y con write-through.**

```python
class ExecWindow(Protocol):
    worktree: WorktreeId
    root: Path                     # directorio real
    writable: PathSpec             # subconjunto declarado por la capacidad
    base: SnapshotId
    async def close(self) -> ReconcileResult: ...
```

Reglas durante una `ExecWindow` abierta sobre un worktree:

1. **`Vfs.read`/`stat`/`list` leen del directorio materializado**, no del overlay del CAS, para todo path bajo `writable`. Un agente nunca lee una versión anterior a lo que acaba de generar el build.
2. **`VfsTxn.commit` sobre paths bajo `writable` es write-through**: se escribe el fichero en el directorio real *y* se registra en el snapshot. No hay dos copias divergentes.
3. **Un `commit` cuyas escrituras caen bajo `writable` mientras hay un proceso corriendo se acepta**, pero la ventana marca esos paths como `agent_authored` para que la reconciliación no los reatribuya a la ejecución.
4. Fuera de `writable`, el árbol se materializa **solo lectura** por permisos del FS. Un proceso que intente escribir ahí falla con `EACCES`, que es un error legible y no una corrupción silenciosa.
5. **Solo una `ExecWindow` por worktree a la vez.** Dos `npm test` concurrentes sobre el mismo worktree se serializan o se lanzan en worktrees hijos (que es lo que la invariante 5 quiere de todos modos). Intentarlo devuelve `WorktreeBusy`.

Al cerrar la ventana: se rescanea el subárbol `writable`, se hashea lo cambiado, se resta lo `agent_authored`, y el resto se materializa como un commit sintético con `author="external"`, emitiendo `vfs.external_writes_reconciled{window_id, snapshot, changed, cause}`. Si durante la ventana el agente hizo commits, la reconciliación **no es un commit lineal sobre `base`, es un merge de tres vías** contra el head actual del worktree — el borrador asumía linealidad y eso es incorrecto en cuanto hay un solo commit en la ventana.

**La marca de "escritura externa no revisada" es de fase 1, no negociable.** Añadirla después invalida retroactivamente toda auditoría anterior. Un agente no puede hacer pasar un `sed -i` por una edición revisada.

**Coste real del rescan, sin adornos.** El borrador decía 400–900 ms para 200k ficheros. Eso es un walk *caliente*, con el page cache lleno. Números honestos en REF (500k ficheros, APFS, NVMe): `stat` walk caliente 1,2–2,5 s; walk frío 4–9 s; hashear lo cambiado añade a 600 MB/s. Por eso el rescan completo es el fallback y no el camino normal, y por eso la ventana declara `writable` como pathspec: rescanear `src/` (2.000 ficheros) cuesta 15–40 ms.

---

### 2.4 Copy-on-write y materialización

Objetivo: fork de un workspace de REF en < 200 ms, y que el número no dependa del número de ficheros.

| Mecanismo | Fork en REF | Portable | Espacio | Veredicto |
|---|---|---|---|---|
| `git worktree add` | 20–90 s (500k inodos) | exige repo git válido | copia completa | descartado |
| APFS `clonefile` / `cp --reflink` | 0,6–3 s | APFS/btrfs/XFS; no ext4, no overlay | ~0 | acelerador |
| OverlayFS | 5–30 ms (montar) | solo Linux, user-ns o CAP_SYS_ADMIN | ~0 | acelerador |
| btrfs/ZFS subvolume | ~10 ms | exige controlar el FS del host | ~0 | descartado |
| **Puntero en el CAS** | **3 ms p50 / 12 ms p99** | total | ~0 | **canónico** |

**El fork canónico es copiar un hash de 32 bytes.** `worktree.fork()` emite `worktree.forked{worktree_id, parent, base}` y crea el registro `{worktree_id, base: SnapshotId, overlay: {}}`. Independiente del tamaño del repo: 3 ms con 200k ficheros y 3 ms con 2M. El worktree es inmediatamente usable en régimen proyectado.

La materialización es **perezosa, parcial por pathspec y enchufable** por el puerto `Materializer`, con tres implementaciones y números medidos en REF:

| Impl | Requisito | 500k ficheros | Notas |
|---|---|---|---|
| `OverlayfsMaterializer` | Linux + user-ns | 8 ms p50 / 30 ms p99 | lower = materialización compartida del base |
| `ReflinkMaterializer` | APFS / btrfs / XFS | 700 ms p50 / 2,4 s p99 | clona desde una materialización del base |
| `HardlinkMaterializer` | universal | 4,5 s p50 / 12 s p99 | fallback; ver 2.9 sobre corrupción |

Dos multiplicadores que hacen que estos números casi nunca se paguen enteros: la materialización es **parcial** (el agente medio toca < 2% del repo y `writable` suele ser `src/` + `tests/`) y es **compartida** (un worktree hijo de un base ya materializado usa esa materialización como capa inferior; solo materializa su overlay, que son decenas de ficheros).

**`DependencyLayer`.** `node_modules/`, `.venv/`, `target/`, `Pods/`, `.gradle/` son típicamente el 70–95% de los inodos de un proyecto y el 0% de su valor semántico editable. No entran en el CAS del workspace, no se forkean y no se indexan simbólicamente. Viven en una capa de solo lectura direccionada por

```
layer_key = b3(lockfile_bytes ‖ platform ‖ arch ‖ libc_abi ‖ installer_id ‖ installer_version ‖ tenant_id)
```

**Corrección de seguridad respecto al borrador: `tenant_id` está en la clave.** Una `DependencyLayer` se produce **ejecutando código arbitrario del proyecto** (`postinstall`, `node-gyp`, `build.rs`). Compartirla entre tenants convierte un paquete malicioso del tenant A en ejecución en el entorno del tenant B: es una vía de contaminación cruzada de cadena de suministro, no una optimización. La deduplicación entre proyectos de un mismo tenant es donde está el 90% del ahorro real (un tenant con 50 servicios en el mismo monorepo) y no cuesta nada. Solo se permite compartir entre tenants una capa marcada `hermetic=true`, que exige que el instalador haya corrido con scripts deshabilitados y que todos los artefactos casen con los hashes de integridad del registro — en la práctica, casi nunca.

Segunda corrección: la capa es de solo lectura, pero herramientas reales escriben dentro (`node_modules/.cache`, `.vite`, `.venv/**/__pycache__`). La capa se monta como base con un **upper writable por worktree** (overlay real en Linux, directorio de sombra + resolución en el `Materializer` en el resto). Sin esto, el primer `vite build` falla y se echa la culpa al sandbox.

Al montarla se verifica el hash del árbol de la capa; una capa cuyo hash no casa se descarta y se reinstala, emitiendo `deplayer.integrity_mismatch`.

---

### 2.5 Merge

`merge(base, ours, theirs) -> MergePlan`, tres vías sobre el CAS.

**Nivel de árbol.** Recorrido paralelo de los tres árboles con **poda instantánea de subárboles con hash idéntico**. Dos agentes que tocaron directorios distintos se fusionan en O(ficheros cambiados), no O(repo): 40 ficheros cambiados → 40 ms p50, 200 ms p99 en REF.

**Detección de renombrado (corrección).** El borrador proponía "identidad de hash, o solapamiento ≥ 60% de chunks FastCDC". Pero FastCDC solo se aplica a ficheros > 1 MiB, es decir, a casi ningún fichero de código: para el 99,9% de los `source` el borrador no tenía ninguna detección más allá de la identidad exacta, y un rename-with-edit es precisamente el caso interesante. Corrección: para `source` se usa un **simhash de 64 bits sobre shingles de 5 tokens normalizados**, calculado en la ingesta y cacheado como derivado con clave `(ref, "simhash", v1)`. Candidato a renombrado si distancia de Hamming ≤ 12 y los tamaños difieren < 40%. Para ficheros > 1 MiB se mantiene el solapamiento de chunks.

**Nivel de fichero, tres clases.** *Trivial* (un lado igual a base → tomar el otro); *textual* (diff3 por hunks, hunks no solapados se auto-fusionan); *conflicto*. El borrador afirmaba "~85% trivial" sin defenderlo; se retira el número y se instrumenta: `merge.applied` lleva el desglose por clase, y la política por defecto de la fase 2 se calibra con datos reales, no con una estimación.

**Conflicto semántico.** El caso que diff3 no ve: A renombra `def foo` → `def bar`, B añade una llamada a `foo`. Textualmente limpio, semánticamente roto. Detección: tras producir el snapshot fusionado se reparsean los ficheros cambiados más su **clausura inversa de dependencias a profundidad 1**, y se compara el grafo de resolución de símbolos. Cualquier referencia que resolvía en `ours` o en `theirs` y queda sin resolver en el merge es un `semantic_conflict`. También: definiciones duplicadas de símbolo top-level y cambios de aridad en firmas con llamadas existentes.

**Corrección de coste.** En un monorepo, la clausura inversa a profundidad 1 de un `types.ts` importado por medio repo son 3.000+ ficheros: 4–12 s de parseo, no 200 ms. Un detector que se cuelga o que se degrada silenciosamente a "limpio" es peor que no tener detector. Se fija: **tope de 500 ficheros y 3 s de presupuesto**. Al superarlo el detector se rinde *en voz alta* con `merge.semantic_check_skipped{reason, closure_size}`, y la política lo trata como conflicto a efectos de escalada bajo `auto_if_clean`. Un merge que no se pudo verificar no es un merge limpio.

Es un **detector, no un resolutor**: alta precisión en la clase renombrado/borrado, coste acotado, emite `merge.semantic_conflict_detected`.

**Quién decide.** La política la declara el orquestador en la petición, no este módulo: `auto_if_clean` (default de fase 1: trivial + textual limpio + chequeo semántico ejecutado y limpio se auto-integra; cualquier otra cosa escala), `agent_resolve` (un agente resolutor con presupuesto acotado; su resolución es un commit normal y auditable) y `human_gate`. **Un conflicto semántico nunca se auto-resuelve por modelo en fase 1**: así es como se produce un merge verde que borró una función.

**Revert (corrección de pérdida de datos).** El borrador decía que `merge.reverted` "produce un snapshot nuevo igual al padre pre-merge". Eso es un reset disfrazado: si después del merge se commiteó trabajo, ese trabajo desaparece sin dejar rastro en el árbol resultante. Corrección: **el revert es un merge de tres vías con el diff inverso** — `apply(head, invert(diff(pre_merge, merge_result)))` —, puede producir conflictos como cualquier merge, y emite `merge.reverted{reverted_snapshot, result, conflicts}`. Un revert que colisiona escala, no aplasta.

---

### 2.6 Clasificación de rutas

Un `PathClassifier` asigna `PathClass` y **etiquetas** en la ingesta:

- `source`: texto, < 2 MiB, lenguaje conocido → CAS + índice completo (léxico, simbólico, vectorial perezoso).
- `data`: texto grande o de lenguaje desconocido → CAS + solo índice léxico.
- `binary`: NUL en los primeros 8 KiB o extensión conocida → CAS en chunks; `read()` devuelve `ContentRef` + descripción sintética, **nunca bytes al contexto del modelo** sin `byte_range` explícito.
- `vendored`: a la `DependencyLayer` (2.4).
- `lfs`: puntero git-lfs; el objeto real se trae bajo demanda con verificación de presupuesto (invariante 8).
- `ignored`: `.gitignore` + `.forgeignore`.

**Corrección de acoplamiento: el clasificador etiqueta, no decide.** El borrador decía que la denylist dura (`.env`, `*.pem`, `id_rsa`, `.ssh/`, `.aws/`) hacía que "la capa de capacidades se niegue a conceder aunque se pida explícitamente". Eso es política de autoridad metida en el plano de datos, contra las invariantes 6 y 10, y además es operativamente falso: "arregla mi `.env.example`" y "rota este certificado" son tareas legítimas. Corrección: el clasificador emite la etiqueta `secret_candidate` (por patrón de ruta y por escaneo de entropía/regex de credenciales en el contenido en la ingesta) y **nada más**. La decisión de conceder o no es del emisor de capacidades (bloque 3), que es quien tiene el contexto de política del tenant. El plano de datos nunca decide autoridad; solo se niega a *ocultar* información al que sí decide.

**Symlinks (hueco del borrador).** `TreeEntry` admitía `0o120000` y el diseño no decía nada más. Un symlink a `../../../../etc/passwd` es una fuga de capacidad en cuanto se materializa. Se fija: el target se guarda verbatim; el VFS resuelve symlinks **solo dentro del árbol**; un symlink absoluto o que sube por encima de la raíz se marca `link_escapes_root=True`, `stat` lo reporta, `read` lo rechaza con `LinkEscapesRoot`, y **ningún `Materializer` lo crea** (escribe un fichero marcador y emite `workspace.symlink_skipped`). `list(depth>1)` lleva un conjunto de nodos visitados: los ciclos terminan, no cuelgan. Los hardlinks no son representables en el árbol Merkle: la materialización siempre copia y la ingesta los trata como ficheros independientes con el mismo contenido, que el CAS deduplica gratis.

**Colisión de mayúsculas.** El CAS es case-sensitive; APFS y NTFS por defecto no. `Foo.py` y `foo.py` colapsan al materializar. Se detecta en la ingesta, se emite `workspace.case_collision_detected` y esas rutas quedan **solo en régimen proyectado**, sin materializar. Además el `name` de `TreeEntry` se normaliza a NFC: macOS entrega NFD y sin normalizar, un mismo fichero tiene dos hashes de árbol distintos según el sistema que lo ingirió.

**Límites duros.** 2M ficheros, 100 GB y 500 MiB por fichero por workspace (por encima, solo puntero). Ingesta en streaming a 400–900 MB/s en caché caliente, limitada por syscalls, no por el hash: un repo de 5 GB tarda 20–60 s la primera vez e incremental después.

**Cuota (hueco del borrador).** Los límites eran por workspace y no había ninguno agregado. 20 agentes × 20 workspaces agotan el disco del host y el GC no ayuda porque todo está vivo. Se fija: cuota por tenant sobre **bytes únicos referenciados** en el CAS local, con `cas.quota_exceeded` al 90% y rechazo de ingesta al 100%; y bajo presión de disco el CAS evita objetos que ya estén subidos al `ObjectStore` (son caché), nunca los que no.

---

### 2.7 Indexado

**Sidecar por workspace, fuera de proceso, escritor único.**

| Opción | Latencia | Aislamiento | RAM | Multi-tenant | Veredicto |
|---|---|---|---|---|---|
| En proceso del runner | mejor | un OOM del índice mata el turno; compite por CPU | duplicada por agente | no | descartado |
| Servicio global compartido | buena | un repo tóxico degrada a todos; fuga cross-tenant a distancia 1 de un fallo de RLS | mejor amortizada | riesgoso | descartado |
| **Sidecar por workspace** | +0,2–0,8 ms por UDS | fallo aislado, reiniciable, reconstruible | acotada, LRU de sidecars | trivial | **elegido** |

Ciclo de vida: arranca en `workspace.open`, se expulsa tras 10 min de inactividad, arranca caliente desde segmentos persistidos.

**Escritor único, de verdad (hueco del borrador).** "Escritor único" era una afirmación sin mecanismo. Dos sidecars vivos para el mismo workspace — un proceso zombi que no murió, un segundo host — corrompen los segmentos y el `head_cache`. Se fija: `flock` exclusivo sobre `<workspace_dir>/.forge/OWNER` **más** un token de fencing monotónico registrado en el journal. Todo `append` al journal desde el sidecar lleva el token; el journal rechaza tokens viejos. Un sidecar que pierde el lock debe auto-terminarse antes de cualquier escritura posterior; el fencing existe porque perder el lock y no darse cuenta a tiempo es exactamente el caso que ocurre.

#### 2.7.1 Base + delta: cómo caben 20 worktrees en RAM

**Este es el hueco de escala más grave del borrador.** El sidecar es por *workspace*, pero el índice es por *snapshot*, y hay 20 worktrees con 20 heads distintos. Mantener 20 índices completos son 20 × 180 MiB = 3,6 GB contra un tope declarado de 512 MiB: falla por un factor de 7 el día que se cumple la invariante 9.

Corrección estructural:

> Se mantiene **un índice completo del snapshot base del workspace** y, por worktree, **un índice delta** que cubre exclusivamente los paths de su overlay.
>
> `query(worktree, q) = (base_query(q) \ shadowed(worktree)) ∪ delta_query(worktree, q)`
>
> donde `shadowed(worktree)` es el conjunto de paths del overlay — el mismo mapa que ya usa el VFS, típicamente < 200 entradas. La resta es una pertenencia en un `set` por cada acierto: O(1), sin coste medible.

Números en REF: base 180 MiB (60 léxico + 70 tablas de símbolos + 30 grafo + 20 varios); delta por worktree con 150 ficheros tocados ≈ 1,5–3 MiB; 20 worktrees ≈ 30–60 MiB. **Total 210–240 MiB, tope duro 640 MiB.** Al superarlo el sidecar tira, en orden: índice vectorial, grafo, segmentos delta de trigrama. Todo reconstruible.

Cuando el head del *workspace* avanza (un merge integrado), el base se reindexa incrementalmente y los deltas se recalculan contra el base nuevo. Si un worktree diverge más de 5.000 paths del base, se le promociona a base propio y se contabiliza como un segundo workspace a efectos de RAM — es un caso raro (una rama de refactor masivo) y merece pagarse explícitamente en vez de degradar a todos.

#### 2.7.2 Léxico

**Corrección de acoplamiento oculto.** El borrador ponía `ripgrep` sobre la materialización como fase 1, mientras el resto del diseño afirma que la materialización es perezosa y parcial. Eso significa que *buscar obliga a materializar el repo entero*, que es entre 0,7 y 12 s según el `Materializer`. La contradicción no estaba señalada.

Fase 1: **escaneo paralelo sobre los blobs `source` del CAS, mmap'd, sin materializar nada.** El árbol da la lista de refs; se escanean con un motor de regex vectorizado. En REF (200 MB de `source`, 8 núcleos): literal 60 ms p50 / 250 ms p99; regex general 180 ms p50 / 700 ms p99. Cero mantenimiento, cero staleness, cero dependencia de la materialización.

Fase 2: postings de **trigramas** (modelo Zoekt) sobre el snapshot base — trigrama → lista ordenada de doc ids, verificación del regex real sobre los candidatos. 25–35% de los bytes de fuente en disco, mmap'd, residente acotado a 200 MiB. Actualización incremental por fichero en segmentos delta estilo LSM; compactación en background cuando el delta supera el 20% de la base. Los deltas por worktree usan el escaneo directo de 2.7.2-fase-1 sobre sus < 200 ficheros: 2–5 ms, no merece un índice.

#### 2.7.3 Simbólico

| | Precisión | Arranque | Fiabilidad | Cobertura | Veredicto |
|---|---|---|---|---|---|
| regex/ctags | baja (sin scopes) | 0 | alta | universal | fallback |
| **tree-sitter propio** | sintáctica alta; resolución por scope + imports | ~1 s | muy alta: parser puro, sin red ni proceso ajeno | 40+ gramáticas | **base obligatoria** |
| LSP real | alta (tipos, herencia, genéricos) | 10–180 s; rust-analyzer puede pasar de minutos y 4 GB | baja: cuelgues, crashes, exige toolchain y deps instaladas | por lenguaje, con config | **opcional, bajo capacidad** |

Un worktree de agente **no es necesariamente un proyecto compilable**: `.venv` puede no existir, el lockfile estar a medias, el toolchain faltar. Hacer depender la corrección del índice de un proceso ajeno que se cuelga es cambiar precisión por disponibilidad, y para un agente manda la disponibilidad. El LSP entra detrás del puerto `SymbolProvider` con contrato estricto: **800 ms de deadline por consulta, 60 s para estar listo, 2 GB de RSS y un circuit breaker que lo desactiva 10 min tras 3 fallos**. Nunca bloquea y nunca es requisito de corrección; si no responde, gana tree-sitter.

Parseo en frío de REF: 60–200 s en 8 núcleos. Por eso es **perezoso y priorizado**: (a) ficheros del diff actual, (b) sus importados directos, (c) los recientes según el VCS, (d) el resto. El agente es productivo en t+2 s; el índice está completo en t+120 s. Durante ese tiempo `symbol.lookup` sobre un fichero no parseado lo parsea en el momento (1–15 ms) en vez de mentir.

#### 2.7.4 Vectorial

Chunking **por frontera de símbolo** (tree-sitter da los spans de función/clase), nunca por ventana fija: una ventana fija parte funciones por la mitad y produce resultados inútiles. Se embebe firma + docstring + cabeza del cuerpo, ≤ 512 tokens.

**Nunca eager.** Embeber REF entero cuesta dinero real contra un proveedor que la constitución declara móvil: cuando se agoten los 50k USD de Workers AI habrá que reembeber. Se embebe bajo presupuesto explícito y solo: el working set, el top-N por centralidad en el grafo de dependencias, y la documentación. Caché con clave `(chunk_hash, embedder_id)` en el CAS.

Almacenamiento tras el puerto `VectorIndex`, con impl local (usearch/sqlite-vec) y remota. **Restricción del puerto, no del proveedor**: `VectorIndex` no promete read-your-writes ni borrado por filtro, porque Vectorize no los da con la latencia que un índice local sí — asumirlos sería atar el contrato a la implementación local y romper la remota. El espacio de vectores está **namespaced por `embedder_id`**: un cambio de proveedor cambia la dimensión y no basta con invalidar entradas, hay que crear un espacio nuevo y GC-ear el viejo.

Honestidad sobre su valor: para código, léxico + simbólico gana en la mayoría de consultas. El vectorial se gana el sitio en "dónde está el código que maneja el backoff de reintentos". **Es el tercer índice consultado, no el primero.**

#### 2.7.5 Grafo de código

Nodos `file|module|symbol|package`; aristas `defines|imports|references|calls|implements|depends_on`. Es una **proyección** del índice simbólico, no un almacén autoritativo.

Frescura sin reindexar todo: cuando cambia F se invalida (a) los nodos y aristas propios de F y (b) el conjunto de *referencias no resueltas* que ahora podrían resolver a símbolos definidos en F, mantenido en un índice invertido `nombre → ficheros_con_referencia_no_resuelta(nombre)`. Eso acota la propagación a los ficheros que mencionan los nombres cambiados (típicamente < 30), no al repo. No hay punto fijo global, a propósito: un punto fijo es un bucle de duración no acotada en el camino caliente.

---

### 2.8 Presupuestos de rendimiento

Todo en REF, salvo indicación.

| Operación | p50 | p99 |
|---|---|---|
| `workspace.open` (ya ingerido, sidecar frío) | 300 ms | 900 ms |
| `workspace.open` (sidecar caliente) | 15 ms | 60 ms |
| `workspace.ingest` (primera vez, 4 GB) | 50 s | 3 min |
| primera lectura útil con ingesta passthrough | 400 ms | 1,5 s |
| `worktree.fork` | 3 ms | 12 ms |
| materialización parcial (`src/`, 2k ficheros) | 15 ms / 40 ms / 120 ms | 40 / 150 / 400 ms |
| materialización completa (overlayfs / reflink / hardlink) | 8 ms / 700 ms / 4,5 s | 30 ms / 2,4 s / 12 s |
| `vfs.read` caliente < 256 KiB | 0,4 ms | 3 ms |
| `vfs.read` frío | 2 ms | 15 ms |
| `vfs.commit` 20 ficheros (incluye durabilidad del journal) | 8 ms | 30 ms |
| verificación de dependencias del OCC (≤ 4.096 deps) | 1,5 ms | 8 ms |
| búsqueda léxica literal (fase 1 sobre CAS / fase 2 trigrama) | 60 / 12 ms | 250 / 60 ms |
| búsqueda léxica regex general (fase 1 / fase 2) | 180 / 25 ms | 700 / 120 ms |
| `symbol.lookup` exacto (parseado) | 0,8 ms | 4 ms |
| `symbol.lookup` sobre fichero no parseado aún | 6 ms | 25 ms |
| `symbol.references` | 5 ms | 40 ms |
| vectorial top-20 (sin latencia del embedder) | 25 ms | 120 ms |
| merge 3 vías, 40 ficheros, con chequeo semántico | 55 ms | 320 ms |
| chequeo semántico con clausura al tope (500 ficheros) | 1,2 s | 3 s (tope) |
| cierre de `ExecWindow`, rescan de `src/` (2k ficheros) | 25 ms | 90 ms |
| cierre de `ExecWindow`, rescan completo tras overflow (caliente) | 1,5 s | 4 s |

RAM del sidecar en REF con 20 worktrees: **210–240 MiB residentes, tope duro 640 MiB**, desglose en 2.7.1.

**Ingesta passthrough (hueco del borrador).** Con `ingest` en 50 s, la pregunta obvia — qué hace el agente durante ese minuto — no tenía respuesta. Para `RepoSource` de tipo ruta local se admite **ingesta passthrough**: el snapshot se construye perezosamente por path bajo demanda (hash al vuelo, se cachea) mientras el walk completo corre en background. El agente lee a los 400 ms; el snapshot es "completo" cuando el walk termina, y hasta entonces `list()` sobre un directorio no visitado lo visita en el momento. `commit` está bloqueado (`IngestIncomplete`) hasta que el walk termina, porque un snapshot con un árbol parcial no es un snapshot.

**Cachés.** L0 por txn; L1 blobs calientes en el sidecar (LRU 256 MiB, clave `ContentRef`); L2 CAS en disco (`~/.forge/cas/<b3[:2]>/<b3>`); L3 CAS remoto tras el puerto `ObjectStore` (R2/S3), que es lo que permite reanudar en otra máquina.

Todo derivado — árboles de parseo, segmentos de trigrama, embeddings, simhashes, detección de codificación, detección de lenguaje — lleva clave `(content_hash, producer_id, producer_version)`. **No existe lógica de invalidación que pueda estar mal: solo GC.** Subir de versión una gramática de tree-sitter invalida exactamente ese lenguaje. Un fichero que vuelve a un estado anterior reutiliza sus derivados gratis. Los propios segmentos del índice son derivados con clave `(snapshot_id, producer_id, producer_version)`, así que un sidecar expulsado y reabierto **hace `mmap`, no reparsea**: 300 ms, no 120 s. Esa era una omisión del borrador con coste operativo directo.

**GC del CAS (corrección).** El borrador inmunizaba "todo objeto tocado en los últimos 7 días", lo que depende de `atime` — desactivado en la práctica por `relatime`/`noatime` en macOS y en la mayoría de Linux. La mitigación, tal como estaba escrita, no funciona. Corrección: mark-and-sweep con **pines explícitos**. Raíces vivas = snapshots referenciados por workspaces y worktrees ∪ `pins`, un registro append-only donde toda txn abierta, `ExecWindow` y fork en vuelo registra un lease con expiración. El barrido además ignora todo objeto cuyo epoch de escritura sea posterior al inicio del mark, con un contador de epoch que mantiene el escritor del CAS, no el sistema de ficheros. Un `worktree.fork` concurrente con un barrido no puede perder su base.

**Durabilidad de la escritura al CAS.** Objeto nuevo → fichero temporal en el mismo sistema de ficheros → `fsync` → `rename` atómico al nombre final. Un temporal truncado por un disco lleno o un corte es basura que el GC recoge, nunca un objeto con nombre correcto y contenido incompleto. Sin esto, un disco lleno corrompe el CAS de todos los workspaces del host, porque el nombre *es* la promesa del contenido.

---

### 2.9 Cómo se rompe

1. **Escritura del VFS durante una `ExecWindow`.** El fallo con más pérdida de datos del bloque, y el que el borrador no veía. Mitigado por diseño en 2.3: ventana exclusiva, lecturas desde el disco materializado, commits write-through, `agent_authored` para no reatribuir. Residual: un proceso que escribe fuera de `writable` falla con `EACCES` y algunos build systems reportan eso mal.
2. **Desbordamiento del watcher.** FSEvents e inotify tienen cola finita; un `npm install` genera 300k eventos y la pierde. FSEvents además coalesce a nivel de directorio y no reporta contenido, así que la reconciliación siempre stat+hashea. Síntoma: índice y snapshot divergen en silencio de la materialización. Mitigación: `watcher.overflow` explícito → rescan forzado del subárbol (1,5 s p50 caliente en REF), e `index.lag_exceeded` si el lag supera 2 s, que degrada la búsqueda al escaneo directo sobre el CAS hasta recuperar.
3. **Hardlink más escritura in-place corrompe el CAS.** Un `sed -i` que trunca en vez de escribir-y-renombrar modifica el objeto del CAS compartido por *todos* los workspaces del host. Mitigación obligatoria y triple: el directorio del store se monta solo-lectura; el `HardlinkMaterializer` rompe el enlace (copia real) en todo path que la capacidad declare escribible; y el CAS reverifica el hash en lectura cuando el `mtime` o el `size` del objeto no coinciden con los registrados, emitiendo `cas.object_quarantined` y recuperando desde `ObjectStore`.
4. **Dos sidecars para el mismo workspace.** Un proceso zombi o un segundo host escriben segmentos y `head_cache` a la vez. Mitigación: `flock` más token de fencing en el journal (2.7); el journal rechaza tokens viejos, así que perder el lock sin enterarse a tiempo no basta para corromper.
5. **Livelock del OCC.** Agente lento sobre fichero caliente. Mitigación: backoff con jitter, contador en el evento, y al tercer fallo el commit se convierte en `MergeProposal`.
6. **Falso negativo del merge semántico.** Detectamos referencias que dejan de resolver; no detectamos cambios de comportamiento con firma idéntica (invertir un booleano, permutar dos parámetros del mismo tipo). No se pretende: la red de seguridad son los tests.
7. **Clausura inversa explosiva en el chequeo semántico.** Un `types.ts` con 3.000 dependientes. Mitigación: tope de 500 ficheros y 3 s, y `merge.semantic_check_skipped` **tratado como no-limpio**, nunca como limpio.
8. **OOM del sidecar en un repo patológico** (400k ficheros en un directorio, un `.json` de 900 MB). `readdir` degrada, el trigrama explota. Mitigación: paginación obligatoria en `list()`, tope de 50k entradas por directorio antes de degradar a listado perezoso, `data`/`binary` fuera del índice simbólico, y el desalojo escalonado de 2.7.1.
9. **GC contra un fork en vuelo.** Mitigado con pines explícitos y epoch de escritura, no con `atime` (2.8).
10. **Colisión de mayúsculas y normalización Unicode.** APFS/NTFS colapsan `Foo.py`/`foo.py`; macOS entrega NFD. Mitigación: detección en ingesta, `workspace.case_collision_detected`, esas rutas solo en régimen proyectado, y normalización NFC obligatoria de `TreeEntry.name`.
11. **Symlink que escapa de la raíz.** Fuga de capacidad al materializar. Mitigación: nunca se sigue, nunca se materializa, se reporta (2.6).
12. **LFS o binario gigante pedido por el agente.** Un `read()` de 2 GB agota memoria y presupuesto. Mitigación: `read()` de `binary`/`lfs` nunca devuelve bytes sin `byte_range` explícito y sin cheque de presupuesto (invariante 8); el `byte_range` se sirve verificado desde el árbol BLAKE3 sin descargar el objeto entero.
13. **`DependencyLayer` compartida entre tenants.** Contaminación cruzada de cadena de suministro vía `postinstall`. Mitigación: `tenant_id` en la clave de la capa; compartir entre tenants solo con `hermetic=true`.
14. **Materialización huérfana.** Un corte durante `exec` deja un directorio sin worktree vivo. Barrido de arranque que reconcilia materializaciones contra el registro de worktrees y las descarta: son caché, no verdad.
15. **Disco lleno a mitad de un commit.** Mitigado por temp + `fsync` + `rename` (2.8): produce basura, nunca un objeto con nombre válido y contenido roto.
16. **Cuota de tenant agotada en un host compartido.** 20 agentes llenan el disco y todo está vivo, así que el GC no ayuda. Mitigación: cuota por tenant sobre bytes únicos referenciados, aviso al 90%, rechazo de ingesta al 100%, y desalojo de objetos ya subidos al `ObjectStore`.

---

### 2.10 Alternativas descartadas

- **Git como motor COW y de merge.** Es el camino obvio y es una trampa: `git worktree add` cuesta decenas de segundos y una copia completa por agente; `.git/index` es un escritor único que serializa a los N agentes que la invariante 9 exige; `git merge` no ve conflictos semánticos; y ata el diseño a que todo proyecto sea un repo git válido, lo que rompe worktrees efímeros y proyectos sin VCS. Git se conserva como **plugin de VCS** para `clone`/`fetch`/`push`. Coste de equivocarnos: reimplementamos diff3, detección de renombrado y simhash — ~2.500 líneas acotadas y muy testeables.
- **FUSE o servidor NFS local para el VFS.** Daría interceptación total, incluso de procesos crudos. Descartado: macOS ya no soporta FUSE sin extensión de kernel firmada, exige permisos de sistema que un usuario final no debe conceder, y añade 50–200 µs por syscall que un `npm install` multiplica por millones. Reevaluable en fase 3 solo para Linux en contenedor, donde además compite con overlayfs, que es más barato.
- **ptrace / eBPF para auditar el FS crudo.** No es portable a macOS, no es concedible por un usuario final, y en Linux exige privilegios que rompen el modelo de capacidades.
- **Reutilizar `edecan_core.memory` (pgvector) para el índice de código.** Ata el plano de datos al Postgres del plano de control, viola la invariante 3 y mete latencia de red en un `symbol.lookup` presupuestado en 0,8 ms.
- **Índice global multi-tenant compartido.** Amortiza mejor la RAM, pero convierte una fuga del índice en una fuga cross-tenant y deja que un repo patológico degrade a todos.
- **Un índice completo por worktree.** Lo obvio, y falla por 7× en RAM con 20 agentes. Sustituido por base + delta (2.7.1).
- **Solo índice vectorial (RAG naive sobre el repo).** Es el default de la industria y es peor: no encuentra un identificador exacto, no responde "quién llama a esto", cuesta dinero por token en un proveedor que vamos a cambiar, y con un modelo base sin garantía de contexto grande la precisión importa más que el recall.
- **SQLite como CAS.** Simplifica el GC pero mete un escritor único en el camino de escritura de N agentes e imposibilita el `mmap` directo y el hardlink de materialización.
- **Offsets de byte o números de línea en `TextEdit`.** Es lo que hace la mayoría de las APIs de edición y es la principal fuente de ediciones destructivas con modelos que no cuentan. El anclaje por texto es más caro de resolver (una búsqueda) y estrictamente más seguro.
- **Meter las búsquedas en el read-set del OCC.** Sound, y hace el sistema inusable: todo commit posterior a un `grep` conflictaría. Documentado como riesgo aceptado.

---

### 2.11 Riesgos aceptados

1. **Las búsquedas no generan dependencias en el OCC.** Un agente puede decidir sobre resultados de búsqueda desactualizados sin que el commit lo detecte. Alternativa (incluirlas) hace el sistema inoperante. Red de seguridad: el merge de tres vías y los tests. Se instrumentará `search.staleness_window` para medir si alguna vez importa.
2. **Granularidad de fichero en el OCC.** Dos agentes editando partes lejanas del mismo fichero producen un falso conflicto. Se paga como reintento, no como fallo, porque el merge sí opera a nivel de hunk. Bajar a granularidad de hunk después es aditivo: se enriquece `FileDep` con spans, sin tocar la firma de `commit()`.
3. **Sin frescura en tiempo real del índice ante escrituras externas.** Ventana de 50–300 ms de latencia del watcher, mayor bajo carga por el coalescing de FSEvents. Aceptado: la corrección se mantiene porque el snapshot es la verdad y el índice es una proyección; solo se degrada la calidad de la búsqueda durante la ventana.
4. **Sin resolución de símbolos con tipos por defecto.** tree-sitter falla en símbolos dinámicos, sobrecargas y genéricos. El agente recibe alguna definición equivocada y lo detecta al leer el fichero. Subir el LSP a primario es cambiar el orden de consulta del `SymbolProvider`, no rediseñar.
5. **Arranque en frío real.** No se elimina, se esconde con ingesta passthrough y parseo priorizado. Un repo de 4 GB recién clonado tiene búsqueda simbólica incompleta durante ~2 min.
6. **La `DependencyLayer` no se comparte entre tenants salvo `hermetic=true`.** Se pierde amortización de disco en hosts multi-tenant. Es el lado correcto del error: contaminación de cadena de suministro es irreparable, disco duplicado es una factura.
7. **Reimplementación de diff3, detección de renombrado y simhash.** ~2.500 líneas propias en vez de reusar git. Contenido y testeable, pero es código nuestro con bugs nuestros.
8. **`merge.semantic_check_skipped` escala a humano.** En un monorepo con ficheros muy importados esto va a escalar más de lo cómodo. Preferimos la fricción al merge verde y roto; el tope de 500 se calibrará con datos.

---

### 2.12 Fases

**Fase 1 — el sustrato mínimo correcto.** CAS con BLAKE3, `fsync`+`rename`, GC con pines. Árbol Merkle, snapshots, `worktree.fork` por puntero. `Vfs`/`VfsTxn` completo con dependencias tipadas, `TextEdit` anclado y commit linealizado en el journal. `HardlinkMaterializer` y `ReflinkMaterializer`. `ExecWindow` exclusiva con write-through, watcher y reconciliación. Clasificador con etiquetas (incluida `secret_candidate`), symlinks, NFC, colisión de mayúsculas. Búsqueda léxica por escaneo sobre el CAS. tree-sitter con parseo priorizado. Merge de tres vías con diff3, simhash para renombrados, detector semántico con tope, `auto_if_clean`, revert como diff inverso. Sidecar con `flock` + fencing, índice base + delta. `DependencyLayer` por tenant. Sin trigrama, sin vectorial, sin LSP, sin `ObjectStore` remoto.

Justificación del corte: todo lo de fase 1 es *estructural* — cambiarlo después rompe el journal, la auditoría o el modelo de datos. Lo excluido es *aditivo*.

**Fase 2 — rendimiento y enriquecimiento.** Índice de trigramas con segmentos LSM. `OverlayfsMaterializer`. `VectorIndex` con impl local y presupuesto. `SymbolProvider` con LSP opcional y circuit breaker. `ObjectStore` remoto (R2/S3) para reanudar entre máquinas. Calibración con datos reales del cap de 4.096 dependencias, del tope de 500 ficheros del chequeo semántico y de la distribución trivial/textual/conflicto.

**Fase 3 — escala y política.** Promoción de worktree divergente a base propio. `agent_resolve` para conflictos textuales bajo presupuesto. Materialización compartida entre hosts. Reevaluación de FUSE, solo Linux en contenedor. Índice vectorial remoto (Vectorize) tras el mismo puerto.

---

## 3. Context Engine y memoria de largo plazo

`packages/forge-context/edecan_forge_context/`. Consume `JournalReader`, `CasReader` y una lista de
`Recaller` inyectados. No importa ningún módulo del plano de datos ni toca el sistema de archivos del
workspace (invariante 3 y 10). No guarda estado autoritativo: todos sus índices y ledgers son
proyecciones reconstruibles del journal (invariante 2).

Premisa dura derivada de la restricción de proveedor: **el modelo base es débil y el crédito es
finito**. Sin caché de prefijo garantizada, sin JSON estricto, sin visión, con tool-calling frágil y
ventana modesta. De ahí tres consecuencias que atraviesan toda la sección: el contexto se renderiza
como texto plano con estructura greppable y nunca como JSON que el modelo deba parsear; el
presupuesto por defecto es **barato** y solo sube cuando el eval demuestra que pagar más resuelve más
tareas; y toda capacidad superior (caché, ventana grande, tokenizer exacto, embedder decente) se
activa por detección y solo *mejora* el resultado.

### 3.0 Determinismo real: `plan` y `render` son dos funciones distintas

El error de diseño más caro sería declarar "el motor de contexto es una función pura del journal" y
luego meter dentro llamadas LLM (los folds), timeouts por reloj y consultas a índices que cambian.
Eso no es puro y el replay divergiría en silencio, que es exactamente el modo de fallo que la
invariante 2 existe para impedir. El contrato se parte en dos:

```python
class ContextEngine(Protocol):
    async def plan(self, q: ContextQuery) -> ContextPlan:
        """Impuro y acotado en tiempo. Consulta índices, puede disparar un fold
        (que es una llamada LLM y produce un blob en CAS). Emite eventos.
        Su salida es un plan CERRADO: lista ordenada de refs con nivel y hash."""

    def render(self, plan: ContextPlan, cas: CasReader) -> RenderedContext:
        """Puro, síncrono, sin red y sin reloj. Solo lee blobs por hash.
        Reproducible bit a bit: render(plan) == render(plan) siempre."""
```

- `plan()` **no** es reproducible y no se pretende que lo sea. Es la parte que ve el mundo.
- `render()` sí lo es, y su salida se verifica con `render_hash = sha256(canonical(messages))`.
- El journal guarda `context.plan_created{plan_id, ...}` y `context.rendered{plan_id, render_hash}`.
  **Replay = releer el plan del journal y re-renderizar**, jamás re-planificar. Recomputar el plan es
  un acto explícito que produce un `plan_id` nuevo y un evento nuevo; nunca se sustituye el viejo.

Esto es lo que hace verdad las afirmaciones de "replay idéntico", "fork barato" y "un fold es
reversible": no reversible porque lo regeneremos igual (no lo haríamos: el LLM no es determinista),
sino porque el plan y los blobs de entrada siguen en CAS y el render los reconstruye exactamente.

Fuentes de no-determinismo eliminadas del interior de `render()` y empujadas a `plan()`, cada una con
su decisión:

| Fuente | Decisión |
|---|---|
| Timeout de recaller por wall-clock | Un recaller que excede `deadline_ms` devuelve **nada** y se marca `degraded` (peso 0) con evento. Un resultado parcial jamás se fusiona: eso haría que el contexto dependiese del scheduler. |
| Corte del repo map "a los 1.5 s" | Sustituido por corte determinista: bola de radio 3 desde las semillas, tope duro de nodos y **20 iteraciones fijas** de PageRank. El tiempo es una alarma que emite evento, no un criterio de corte. |
| Resumen LLM dentro del render | El fold ocurre en `plan()`, produce un blob en CAS y el plan referencia el **hash**. `render()` solo lee ese hash. |
| Versión del embedder / del parser | Va en la clave de caché del índice: `(content_hash, transform_id, transform_version)` donde `transform_id` incluye modelo y versión (`vec/bge-m3@1`, `sym/tree-sitter-py@0.22`). |

#### Contratos pinneados

```python
Trust  = Literal["system", "user", "repo", "tool", "external"]
Level  = Literal["L0", "L1", "L2", "L3", "L4"]
SlotId = Literal["kernel", "charter", "tool_abi", "constraints", "repo_map",
                 "memory", "summary", "retrieval", "recent", "tool_results"]

class Span(BaseModel):
    start_line: int
    end_line: int
    anchor_head: str          # sha256 de la 1a linea normalizada (sin espacios finales)
    anchor_tail: str          # sha256 de la ultima linea normalizada

class ContentRef(BaseModel):
    blob: str                 # "sha256:..." del contenido EXACTO que se va a renderizar
    origin: str               # "file:apps/api/main.py" | "journal:evt_01H..." | "memory:mem_..."
    workspace_id: str         # rama CoW en la que se leyo. NUNCA se cruza entre ramas.
    span: Span | None
    level: Level
    trust: Trust
    tokens: int               # medido por el TokenMeter declarado en el plan

class ContextQuery(BaseModel):
    session_id: str
    agent_id: str             # el ledger y el contexto son POR AGENTE (invariante 9)
    workspace_id: str
    turn: int
    watermark: int            # ultimo event_seq que el plan puede observar
    goal: str                 # objetivo vigente del agente, texto
    seeds: list[str]          # identificadores y rutas mencionados en el turno
    caps: ProviderCapabilities
    budget: BudgetRequest
    policy_id: str            # sha256 de la config de presupuesto+pesos. Va al evento.

class PlannedBlock(BaseModel):
    slot: SlotId
    ref: ContentRef
    cache_hint: Literal["static", "session", "volatile"]
    reasons: list[str]        # para el humano y para la depuracion, no para el modelo

class ContextPlan(BaseModel):
    plan_id: str              # sha256 del plan canonicalizado
    query_hash: str
    watermark: int
    token_meter: str          # "exact:<tok>@v" | "api" | "heuristic:<chars_per_token>"
    blocks: list[PlannedBlock]         # en orden final de render
    dropped: list[DropRecord]          # ref + slot + motivo
    degraded_recallers: list[str]
    est_cost_usd: float

class RenderedContext(BaseModel):
    plan_id: str
    render_hash: str
    messages: list[ChatMessage]        # formato de packages/llm/edecan_llm/base.py
    tokens_by_slot: dict[SlotId, int]
    cache_breakpoints: list[int]       # indices de bloque, <= 2
```

`RenderedContext.messages` usa el `ChatMessage` que ya existe en `edecan_llm`: el motor de contexto no
inventa un formato de mensajes propio ni conoce ningún proveedor.

#### Eventos que emite

`context.plan_created`, `context.rendered`, `context.block_evicted`, `context.recaller_degraded`,
`context.stale_detected`, `context.anchor_lost`, `context.content_missing`,
`context.secret_redacted`, `context.injection_suspected`, `context.budget_downgraded`,
`context.fold_created`, `context.fold_rejected`, `context.constraint_added`,
`context.constraint_superseded`, `memory.candidate_proposed`, `memory.committed`, `memory.merged`,
`memory.injected`, `memory.hit_used`, `memory.contradicted`, `memory.quarantined`.

### 3.1 Presupuesto: dos tramos, cinco caps y cuatro pesos

El presupuesto del diseño anterior tenía cuatro parámetros por cada uno de nueve slots: treinta y
seis números que nadie iba a calibrar y que ningún eval auditaba. Un presupuesto no evaluable es
folklore con aspecto de ingeniería. Se reduce a **quince números** y a un solver de dos pasos.

**Tramos.** `tier_32k` y `tier_128k`. El tramo se elige como
`min(window - output_reserve - margen, tier_cap[model_id])`. El default de `tier_cap` para un
`model_id` desconocido es **32k, no la ventana del proveedor**. Subir de tramo requiere que E1/E2
demuestren para ese modelo concreto que el tramo alto resuelve más tareas por dólar. Rellenar la
ventana porque está disponible es la forma más rápida de quemar 50.000 USD de crédito de Workers AI
sin resolver una tarea más: el coste es lineal en tokens y la atención se degrada en el centro del
contexto. No hay `useful_cap` global de 260k: ese número no tiene evidencia detrás y contradice la
premisa de que el modelo base es débil.

**Caps absolutos** (no participan en ningún reparto; se restan primero):

| slot | contenido | 32k | 128k | procedencia |
|---|---|---|---|---|
| `kernel` | reglas del sustrato, política de vallas, cómo citar refs | 1500 | 1800 | `system` |
| `charter` | AGENTS.md / CLAUDE.md compilado por secciones | 1200 | 3000 | `repo` |
| `tool_abi` | esquemas de herramientas, orden alfabético | 1800 | 5000 | `system` |
| `constraints` | ledger T0 verbatim (§3.6) | 1000 | 1500 | `system`+`user` |
| `memory` | máx. 6 entradas de largo plazo (§3.7) | 600 | 1200 | `user`/aprendido |
| | **suma** | **6100** | **12500** | |

`tool_abi` se dimensiona además como `min(cap, 200 × n_tools_expuestas)`; si no cabe, el kernel
expone un subconjunto y añade una herramienta `tool.describe(name)` para el resto. Un ABI que no cabe
en el presupuesto es un problema del plano de herramientas, no del de contexto, y debe fallar allí.

**Reparto por peso del remanente** (cuatro slots, cuatro pesos, un único techo):

| slot | estabilidad | peso | techo | 32k | 128k |
|---|---|---|---|---|---|
| `repo_map` | session | 1.0 | 12% del remanente | 3.1k | 13.9k |
| `retrieval` | turn | 3.0 | — | 9.9k | 43.8k |
| `recent` | turn | 2.5 | — | 8.1k | 36.1k |
| `tool_results` | volatile | 1.5 | — | 4.8k | 21.7k |

`resolve()`, dos pasos y determinista: (1) `remanente = útiles − Σcaps`; si `remanente < 8000`,
`BudgetInfeasible` — error ruidoso, nunca degradación silenciosa; (2) repartir proporcional al peso,
aplicar el techo de `repo_map` y devolver su sobrante a `retrieval`, redondear cada slot hacia abajo a
múltiplos de 256 tokens para estabilizar fronteras de caché. Un solo techo y una sola devolución: no
hay iteración de convergencia porque no hace falta.

Umbrales de política en `tier_32k` (los únicos condicionales del motor; el algoritmo es idéntico en
ambos tramos): `repo_map < 3.5k` → solo símbolos semilla y sus vecinos directos;
`retrieval < 12k` → prohibido L3, todo esqueleto o diff; `constraints` gana siempre a `summary` en
desalojo.

**Presupuesto de dinero, que faltaba por completo.** La invariante 8 pide presupuesto de tokens,
dinero y tiempo; el diseño anterior solo contabilizaba tokens de ventana, dejando un agujero de gasto
silencioso multiplicado por N agentes concurrentes.

```python
class BudgetRequest(BaseModel):
    window_tokens: int
    output_reserve_tokens: int
    session_usd_cap: float
    turn_input_tokens_cap: int
    plan_deadline_ms: int = 900
```

Al superar el 70% de `session_usd_cap` el resolver baja de tramo automáticamente y emite
`context.budget_downgraded{from_tier, to_tier, spent_usd}`. Al 100%, el siguiente turno requiere
aprobación humana explícita registrada en el journal. El coste estimado va en `ContextPlan.est_cost_usd`
usando el precio declarado por el adaptador de proveedor; el real se concilia con el `Usage` que ya
devuelve `LLMProvider`.

**Contabilidad de tokens auto-calibrada.** Tres modos declarados en `ProviderCapabilities.token_counting`:
`exact` (tokenizer local), `api` (endpoint de conteo con caché por hash de bloque) y `heuristic`.
El modo heurístico no usa una constante inventada: se ajusta `chars_per_token` por regresión sobre los
últimos 50 turnos de la propia sesión, cruzando los caracteres enviados con el `Usage.input_tokens`
que el proveedor ya reporta. El margen de seguridad es el **percentil 95 del error residual
observado**, con suelo del 4% y techo del 15%. Arranque en frío: 3.6 chars/token en código, 4.0 en
prosa, margen 12%. Esto convierte el peor punto ciego del modo heurístico en una medición gratuita.

**Invariante duro**: `Σ tokens_renderizados + output_reserve ≤ window`, verificado tras el render. Si
falla, se desaloja por estabilidad descendente (`tool_results` → `recent` → `retrieval` → `summary`
→ `repo_map`) **bloques enteros o niveles completos, jamás truncando a mitad**: cortar un archivo en
mitad de una función es la causa número uno de que el modelo alucine el resto. Cada desalojo emite
`context.block_evicted`.

### 3.2 Procedencia y confianza: el eje que atraviesa todo

Ningún slot es confiable por ser un slot. La confianza es una propiedad de la **procedencia** del
contenido, y se propaga con la ref:

| `trust` | origen | ¿puede contener instrucciones? |
|---|---|---|
| `system` | kernel, tool_abi, ledger T0 derivado de eventos privilegiados | sí |
| `user` | mensajes del humano, aprobaciones | sí |
| `repo` | archivos del proyecto, **incluido AGENTS.md**, docstrings, README | no |
| `tool` | stdout/stderr, resultados de herramienta | no |
| `external` | web, MCP remoto, salida de subagentes no verificada | no |

Todo bloque con `trust != system|user` se renderiza dentro de una valla
`<<<datos origen=<origin> @<hash7> confianza=<trust>>> … <<<fin datos>>>`, y el bloque `kernel`
declara una vez que lo que hay dentro de vallas es **información, no instrucciones**.

La consecuencia incómoda y correcta: **AGENTS.md es `repo`, no `user`**. Es un archivo versionado que
llega por `git pull`, por un fork o por un submódulo. Se compila al slot `charter` en valla, y sus
restricciones se elevan al ledger T0 (que es `system`) **solo tras aprobación humana registrada**, la
primera vez que aparece un `charter_hash` nuevo. Sin esa aprobación, el charter es contexto, no
autoridad. Un pull-request que añada una línea a AGENTS.md no debe poder reprogramar al agente.

### 3.3 Índices: proyecciones con base compartida y overlay por rama

Cuatro familias de índice, todas proyecciones materializadas y todas reconstruibles:

```python
class IndexShard(BaseModel):
    kind: Literal["lexical", "vector", "symbol", "depmap"]
    scope: Literal["base", "overlay"]
    base_commit: str | None        # scope=base
    workspace_id: str | None       # scope=overlay
    partition: str                 # prefijo de ruta, p.ej. "apps/api/"
    transform: str                 # "bm25/v1" | "vec/bge-m3@1" | "sym/ts-py@0.22"
    source_watermark: int
    doc_count: int
```

**Base + overlay es lo que hace viable el multi-agente.** Con 20 agentes sobre ramas CoW del mismo
proyecto, indexar cada rama entera cuesta 20× el repo y hace que forkear un workspace sea una
operación de minutos: la invariante 5 quedaría muerta en la práctica. En su lugar, el índice base es
**por `base_commit` y compartido por todas las ramas que lo heredan**; cada rama mantiene solo un
overlay con los archivos que ha tocado. La consulta es
`resultados(base) − paths_sobrescritos_por_overlay + resultados(overlay)`. Coste de fork: O(archivos
modificados), típicamente decenas. Los overlays son inmutables por `(workspace_id, watermark)`, así
que dos agentes nunca escriben el mismo shard y no hay carrera posible entre ramas.

**Particionado y escala.** El indexado es perezoso y por partición (primer o segundo nivel de
directorio, tope de 5.000 archivos por partición). Se indexa lo alcanzable desde las semillas de la
sesión más el cierre de dependencias a distancia ≤2. Un monorepo de 500k archivos nunca se indexa
entero, y nunca en la ruta caliente: el indexado es una tarea del plano de datos con su propio
presupuesto, cancelable y reanudable (invariante 8).

Objetivos medibles: partición fría de 5k archivos indexada en <4 s (léxico) y <12 s (símbolos);
consulta con base caliente y overlay de <200 archivos, p95 <180 ms; presupuesto total de `plan()`
900 ms p95.

**El suelo es R1 + R4, y debe bastar.** En un repo recién clonado, sin ningún índice construido, el
motor tiene que producir contexto útil con búsqueda léxica (ripgrep, sin índice previo) y recencia de
edición (lectura del journal, <5 ms). Todo lo demás — vectores, grafo de símbolos, mapa de
dependencias — es mejora que se enciende cuando existe. Este es el mismo principio de "escalar hacia
arriba" aplicado a la infraestructura, no solo al proveedor.

**Deriva.** Cada shard lleva `source_watermark`. Si el watermark queda más de 200 eventos por detrás
del watermark de la query, el recaller se declara `degraded`, su peso de fusión pasa a 0 y se emite
`context.recaller_degraded`. Devolver resultados de una realidad anterior es peor que no devolver
nada, porque el modelo no puede distinguirlos.

**Staleness sin cruzar planos.** El diseño anterior proponía comparar el hash presentado contra "el
HEAD del workspace CoW" en <5 ms. Eso es una RPC al plano de datos en la ruta caliente y un
acoplamiento directo que la invariante 3 prohíbe. En su lugar, el plano de datos publica
`workspace.file_changed{workspace_id, path, new_hash, seq}` y el motor mantiene una proyección local
`(workspace_id, path) → hash @seq`. La barrida de staleness es una consulta a esa tabla en memoria,
O(refs), sin red. Si la proyección va por detrás del watermark de la query, la ref se marca
`possibly_stale` y se degrada a L1 en lugar de emitirse como si fuese actual.

### 3.4 Selección: recuperadores y fusión

```python
class Candidate(BaseModel):
    ref: ContentRef
    recaller: str
    rank: int
    reasons: list[str]

class Recaller(Protocol):
    id: str
    weight: float
    deadline_ms: int
    async def recall(self, q: ContextQuery, k: int) -> list[Candidate]:
        """Devuelve k candidatos COMPLETOS o lanza RecallerDegraded.
        Nunca resultados parciales: eso haria el plan dependiente del scheduler."""
```

| id | fuerte en | débil en | coste | peso | fase |
|---|---|---|---|---|---|
| `r1_lexical` (BM25 / ripgrep) | identificadores exactos, strings de error, rutas | sinónimos, intención | 20-60 ms | 1.0 | 1 |
| `r4_recency` (journal) | continuidad de la tarea en curso | sesión fría | <5 ms | 1.4 | 1 |
| `r3_symbols` (PageRank personalizado) | vecindad estructural, "quién llama a esto" | ficheros sin parser, config, docs | 100-400 ms | 1.1 | 2 |
| `r2_vector` (chunks, coseno) | intención difusa, código en otro idioma | identificadores raros; depende del embedder | 60-200 ms | 0.7 | 2 |
| `r5_deps` (imports ≤2) | contratos rotos por un cambio | ruido en repos con barriles grandes | 30-80 ms | 0.6 | 3 |
| `r6_agentic` | precisión perfecta cuando el modelo sabe qué busca | 3-8 turnos; el modelo débil se pierde | turnos | — | 1 |

**Fusión por Reciprocal Rank Fusion con `k_rrf = 10`, no 60.** `score(d) = Σ_r w_r / (k_rrf + rank_r(d))`.
El 60 canónico proviene de TREC, donde las listas tienen miles de documentos; con `k_recall = 40`
candidatos por recaller, `k_rrf = 60` comprime rank 1 y rank 40 en un factor de 1.65 — es casi
ignorar el ranking. Con `k_rrf = 10` el factor es 5×, que es lo que queremos. RRF y no combinación
lineal de scores normalizados porque las escalas son incomparables (BM25 no acotado, coseno en
[-1,1], PageRank sumando 1) y normalizarlas exige calibración por repo y por embedder que nadie
mantendría y que se desajusta al cambiar de proveedor sin que nadie lo note.

Multiplicadores sobre el score fusionado: `pinned` entra siempre y fuera de competición;
`abierto por el humano` ×1.8; `possibly_stale` ×0 hasta re-renderizar.

Orden de empaquetado de `retrieval`: (1) pinneados; (2) anclas de `r3_symbols` hasta 25% del slot;
(3) top-8 de `r4_recency`; (4) cola de RRF con diversidad: máximo 40% del slot desde un solo
directorio, máximo 3 fragmentos por archivo salvo que el archivo tenga <400 líneas (entonces
esqueleto completo).

**`r6_agentic` no compite: es el suelo y la verdad de campo.** El agente siempre puede buscar, porque
la búsqueda es parte del ABI de herramientas, no una API interna del motor: `context.search(query)`,
`context.read(path, span)`, `context.pin(ref)`, `context.expand(ref, level)`, `context.drop(ref)`.
El motor no le quita al agente la capacidad de buscar; le ahorra los 3-8 turnos de tanteo que un
modelo débil ejecuta mal. Todo lo que `r6` encuentra se inyecta como señal de recencia en `r4`, de
modo que la búsqueda del agente educa la selección del turno siguiente.

**Histéresis anti-thrash.** Una ref admitida permanece admitida ≥3 turnos salvo desalojo por
presupuesto o staleness, y solo cae si su score baja un 25% por debajo del umbral de admisión. Sin
esto el conjunto se reordena cada turno: se destruye el prefijo y se desorienta al modelo, y se paga
dos veces. Contradicción aparente con la barrida de staleness, resuelta explícitamente: **el archivo
que el agente está editando no vive en `retrieval`**, vive en `recent`/`tool_results`, después del
último breakpoint de caché. Lo volátil se coloca al final; no se estabiliza a la fuerza lo que por
naturaleza cambia.

### 3.5 Compresión: escalera de fidelidad y ledger por agente

Cinco niveles; el empaquetador elige el **más alto que quepa** y nunca trunca a mitad:

- **L0** referencia: `apps/api/routers/phone.py @a91f3c2 (312 líneas, py)`.
- **L1** esqueleto: firmas, tipos, primera línea de docstring, cuerpos elididos como `… 24 líneas`.
- **L2** esqueleto + cuerpos de los spans rankeados.
- **L3** archivo completo.
- **L4** completo con números de línea y blame; solo bajo petición explícita.

**Repo map.** PageRank personalizado sobre el grafo de símbolos (nodos = definiciones, aristas =
referencias, peso = nº de referencias), vector de personalización sembrado en `ContextQuery.seeds` y
en los archivos tocados en la sesión. α = 0.85, **20 iteraciones fijas**, y el grafo sobre el que
corre es el **subgrafo inducido por la bola de radio 3 desde las semillas**, con tope de 200.000
nodos. La personalización ya concentra la masa cerca de las semillas, así que el corte por bola es
una aproximación con error acotado que cambia la complejidad de O(|E| global) a O(|E| local) — es lo
que permite que el mismo algoritmo funcione en un repo de 500k archivos. Cacheado en CAS bajo
`(base_commit, "repomap/v1", seed_hash)`. Los recomputos totales nunca ocurren en foreground.

**Ledger de presentación: por `(session_id, agent_id)`, nunca por sesión.** Registra
`(blob_hash, level, turn, tokens)` de todo lo que **ese agente** ha visto. Es un error de corrupción
de estado compartir el ledger entre agentes concurrentes: el agente B recibiría
`— sin cambios desde el turno 7 —` sobre un archivo que nunca vio, y trabajaría sobre un contenido
inexistente. Reglas:

1. Nunca presentar el mismo `blob_hash` dos veces al mismo nivel al mismo agente: se emite
   `— sin cambios desde el turno N —` con la ref.
2. Si el hash cambió y el anterior se presentó a nivel ≥L2, se presenta **diff unificado contra el
   hash presentado a ese agente**, no contra HEAD ni contra el índice de git: el modelo vio una
   versión intermedia, y un diff contra otra base produce un parche que no aplica.
3. El diff solo se usa si `tokens(diff) < 0.6 × tokens(L3)`; si no, se reenvía completo.

Esto es lo que hace viable una ventana de 32k en una sesión larga, y abarata cualquier proveedor sin
caché de prefijo.

**Clasificación y exclusión.** Antes de puntuar, cada path pasa por un clasificador con reglas
fijadas: `generated` (lockfiles, `*.min.*`, `*_pb2.py`, `dist/`, `node_modules/`), `binary`,
`data` (>2 MB o >20% de líneas sin estructura), `vendored`. Excluidos de todos los recallers salvo
petición explícita del humano; `uv.lock` en el contexto es 20.000 líneas de desperdicio puro.
Los notebooks se indexan por celda de código, descartando las salidas.

**Redacción de secretos: obligatoria y en la ruta de render.** El motor de contexto es el punto exacto
por el que un secreto sale del perímetro hacia el proveedor, y el diseño anterior no lo mencionaba.
Antes de escribir cualquier bloque `repo`/`tool`/`external`: denylist de rutas (`.env*`, `*.pem`,
`*.key`, `secrets/`, `.git/config`), detección de patrones conocidos (`sk-`, `AKIA`, JWT, cabeceras
PEM) y de cadenas de alta entropía (≥32 chars, Shannon >4.0). El match se sustituye por
`«redactado:<clase>»` y se emite `context.secret_redacted{origin, pattern_class, count}`. La
redacción se aplica al blob **antes** de calcular su hash de presentación, para que el ledger nunca
guarde un hash de contenido sin redactar.

### 3.6 Resumen de conversación: T0 determinista, T1/T2 por LLM

Disparo por **ocupación proyectada del turno siguiente**, no la actual: si
`summary + recent + tool_results > 78%` de su presupuesto combinado, o ≥40 turnos desde el último
fold, o hay checkpoint/límite explícito de tarea, o se va a forkear la sesión o entregarla a otro
agente. Comprimir tarde es comprimir en pánico.

**T0 — ledger de contratos. Slot propio (`constraints`), verbatim, nunca resumido.** Objetivo,
restricciones duras, decisiones tomadas, contratos de API acordados, comandos de verificación,
errores con causa raíz confirmada. Se construye con un **extractor determinista** sobre el journal,
cuyas reglas se pinnean para que dos implementadores no lean cosas distintas:

| evento | entrada T0 | clase |
|---|---|---|
| `user.message` con negación imperativa (`no `, `nunca`, `tiene que`, `debe`, `jamás`) | frase literal, ≤240 chars | dura |
| `approval.granted` / `approval.denied` | la acción aprobada o vetada | dura |
| `tool.exit` con `exit_code == 0` sobre un comando de verificación | `verify_cmd` + resultado | dura |
| `test.result` con transición rojo→verde | qué pasó a verde | blanda |
| `agent.decision` con `event_id` de evidencia | la decisión | blanda |
| propuesta del LLM con `evidence: evt_...` **de clase privilegiada** | la propuesta | blanda |

Cap de **1500 tokens** (≈25 entradas activas), no 120 entradas de 240 caracteres: ese cap del diseño
anterior son ~8k tokens, que no caben en ningún presupuesto de 32k. Al llenarse, las entradas blandas
más antiguas se archivan (consultables por la herramienta `context.constraints(all=True)`), **las
duras nunca**; si las duras solas exceden el cap, es `BudgetInfeasible` y el humano tiene que podar.
Fallar ruidosamente es correcto: significa que la sesión acumuló más contratos de los que caben y
seguir es adivinar.

Anti-deriva, tres mecanismos:

1. T0 sale de eventos, no de resúmenes. Las propuestas del LLM sin `event_id` resoluble se descartan
   sin discusión.
2. El resumidor nunca ve resúmenes previos como única fuente de T0: el ledger se reconstruye desde
   eventos en cada fold, así que la degradación generacional no puede acumularse.
3. **Test de retención determinista, no LLM.** Como T0 se renderiza verbatim, verificar su retención
   es comprobar por igualdad de cadena que cada entrada dura aparece en el render, y que
   `tokens_by_slot["constraints"] ≥ tokens(T0_duro)`. Coste: microsegundos. El diseño anterior
   proponía interrogar al modelo tras cada fold; con un modelo débil eso produce falsos rechazos, y
   un fold rechazado en bucle lleva directo a desbordamiento de ventana y turno fallido. El
   interrogatorio LLM se conserva **solo como eval offline** (E4).

**T1 — resúmenes de segmento**: 8-20 turnos → ≤400 tokens, con `event_id` de referencia.
**T2 — meta-resumen** cuando hay >12 segmentos. Ambos por LLM, ambos materializados como blob en CAS,
ambos referenciados por hash desde el plan.

El fold es un evento: `context.fold_created{event_range, t0_hash, t1_blob, superseded_blobs}`.
Auditable, replayable y reversible: desplegar un fold es leer los blobs de entrada del CAS, no
regenerarlos.

### 3.7 Memoria de largo plazo

Cuatro tipos. `semantic` y `project` del diseño anterior se fusionan: tenían la misma escritura, el
mismo decaimiento y la misma prioridad de inyección; una distinción sin diferencia operativa es
folklore.

| tipo | qué es | escritura | inyección |
|---|---|---|---|
| `procedural` | "los tests del worker se corren con `uv run pytest apps/worker`" | exige `verify_cmd` observado con `exit_code == 0` | alta |
| `user` | preferencias, estilo, umbral de aprobación | corrección explícita del usuario → confianza 1.0, inmediata | siempre |
| `project` | hechos del repo o del dominio aprendidos | 2 ocurrencias independientes | media |
| `episodic` | qué pasó en la sesión X y cómo acabó | automática | baja; se promueve si se reutiliza |

**Alcance multi-tenant, que faltaba.** El repo ya es multi-tenant con RLS, y una memoria del proyecto
A inyectada en el proyecto B es una fuga y un error:

```python
class MemoryScope(BaseModel):
    tenant_id: UUID           # jamas se cruza
    user_id: UUID | None      # None = compartida en el tenant
    project_id: str | None    # None = transversal (solo tipo "user")
    workspace_id: None        # las memorias NUNCA se atan a una rama CoW
```
Regla: `procedural` y `project` exigen `project_id`; `user` puede ser transversal; `episodic` siempre
lleva `project_id`.

**El modelo nunca escribe memoria.** Propone (`memory.candidate_proposed`) y un gate decide sobre
cuatro criterios: utilidad (¿habría cambiado una decisión? proxy: par error→arreglo, o corrección del
usuario), generalidad (nada anclado a números de línea ni hashes efímeros), verificabilidad y
no-duplicación.

**La verificabilidad exige evidencia de clase privilegiada.** Este era un agujero real: si vale
cualquier `event_id`, entonces el `tool.stdout` de leer un README malicioso es un evento resoluble, y
un archivo puede acuñar un recuerdo que envenena todas las sesiones futuras. Solo cuentan como
evidencia: `user.message`, `approval.granted`, `tool.exit` (código de salida, no su stdout),
`test.result`, `workspace.commit`. Nunca contenido arbitrario.

**No-duplicación por clave, no por coseno.** Con N agentes proponiendo en paralelo, un gate que
deduplica por coseno ≥0.92 en el momento de escribir tiene una carrera clásica: dos propuestas
equivalentes pasan el chequeo simultáneamente y se insertan las dos. La exclusión mutua se hace por
`claim_key = sha256(normalize(claim) + scope)` con upsert idempotente; el coseno se usa después, como
tarea de consolidación en background que produce `memory.merged` con arista de supersesión. La
consolidación puede reutilizar el `Embedder`/`MemoryStore` ya existentes en
`packages/core/edecan_core/memory/` mediante adaptador; el motor de contexto solo conoce el protocolo.

Regla operativa: **aprender dos veces antes de creer**, salvo corrección explícita del usuario
(confianza 1.0) y procedimiento verificado por exit 0.

**Recuperación.** La memoria es `r7_memory` hacia el slot `memory`, puntuada por
`relevancia × confianza × decay`. Máximo 6 entradas, cap del slot, cada una con su `mem_id` para que
el modelo pueda citarla. El uso citado emite `memory.hit_used` y sube la confianza.

**Decaimiento por uso, no por calendario.** El diseño anterior fijaba semividas de 180 y 60 días, que
nadie iba a validar y que no tienen señal detrás. La señal real está disponible y es gratis: si una
memoria se inyecta y **no** se cita, `confidence *= 0.9`; a las 5 inyecciones sin cita pasa a
cuarentena. El tiempo queda como término secundario (semivida de 180 d solo para `episodic`, que sí
caduca por naturaleza). Contradicción (procedimiento que falla, usuario que corrige) →
`confidence *= 0.4`. Por debajo de 0.3, **cuarentena**: nunca se inyecta, nunca se borra — borrar
contradice la invariante 2. La corrección crea una arista de supersesión, no una edición in-place.

**Relación con AGENTS.md/CLAUDE.md.** Forge **no lo escribe nunca**. Se compila al slot `charter` como
contenido `repo` en valla; sus restricciones se elevan a T0 solo con aprobación humana por
`charter_hash`. Una memoria aprendida estable (`confidence ≥0.8`, `hits ≥5`) genera una propuesta de
parche que el humano aprueba. Precedencia de conflicto: `AGENTS.md aprobado > user > project >
episodic`. Un conflicto detectado no se resuelve en silencio: emite
`memory.contradicted` y se muestra al humano.

### 3.8 Deduplicación contra caché: quién gana está decidido

Las dos optimizaciones se pelean. La deduplicación por hash (§3.5) reduce tokens **quitando** bloques
del prompt, lo que rompe el prefijo estable que la caché premia. El diseño anterior describía las dos
sin decir cuál manda; con Workers AI, que hoy no ofrece caché de prefijo, dejar eso abierto significa
que un implementador podría optimizar para una caché inexistente y pagar 3× el coste real.

Política decidida, con aritmética y no con intuición:

```
ahorro_dedupe  = tokens_evitados × precio_input
ahorro_cache   = tokens_prefijo_estable × (precio_input − precio_input_cacheado)
modo = "prefer_cache" if caps.prompt_cache and ahorro_cache > ahorro_dedupe else "prefer_dedupe"
```

Default sin `caps.prompt_cache`: `prefer_dedupe`, siempre. Ambos modos comparten el mismo renderizador
y el mismo orden.

**Orden y higiene** (valen en los dos modos). Los bloques se emiten por estabilidad ascendente y jamás
se coloca un bloque menos estable antes de uno más estable. Fronteras alineadas a cuantos de 1024
tokens. Como mucho **dos** breakpoints: tras el último bloque `static` y tras el último `session` —
los proveedores que soportan caché suelen limitar a 4, y dos deja margen. Cero timestamps, nonces,
ids de request o contadores dentro de bloques `static`/`session`; esquemas de herramientas ordenados
alfabéticamente; `charter` y `repo_map` regenerados solo al cambiar su hash de entrada.

Métrica propia cuando el proveedor no reporta cache hits:
`prefix_stability = tokens_idénticos_al_prefijo_del_turno_anterior / tokens_del_turno_anterior`
(denominador explícito, porque con dedupe el numerador y el total del turno actual no son
comparables). Objetivo ≥0.60 sostenido en sesiones de 20 turnos, **solo exigible en modo
`prefer_cache`**. En `prefer_dedupe` la métrica que manda es `input_tokens_por_turno_resuelto`.

**Repetición de recencia.** El objetivo vigente y las restricciones duras de T0 se repiten al final
del prompt además de al principio. Duplicar ~300 tokens es barato frente a perder la tarea con un
modelo que ignora la estructura del medio del contexto. Estos ~300 tokens salen del cap de
`constraints`, no son gratis, y están contabilizados.

### 3.9 Evaluación

- **E1 — Recall de contexto (oráculo).** 120 tareas derivadas de commits reales del monorepo; gold
  set = archivos tocados por el commit. `recall@budget = |gold ∩ presentado| / |gold|` y
  `token_waste`. Objetivo: ≥0.85 en `tier_128k`, ≥0.70 en `tier_32k`, waste <45%.
- **E2 — Éxito de tarea.** 40 tareas con verificador ejecutable. Métrica primaria **coste por tarea
  resuelta** (USD y tokens); secundarias: tasa de resolución y turnos hasta verde. Se mide con Kimi K3
  *y* con un modelo fuerte, para separar "el motor ayuda" de "el modelo es bueno".
- **E3 — Ablaciones, puerta de release.** Se apaga cada recuperador y se mide el delta.
  **Recuperador que no mueva E1 ni E2 más de 2 puntos, se elimina.** Es la única defensa contra que
  el motor se convierta en folklore acumulado.
- **E4 — Retención de restricciones.** N restricciones al inicio, 3 folds forzados, interrogatorio al
  modelo. Retención <100% en restricciones duras es bug bloqueante. Offline; nunca en ruta caliente.
- **E5 — Barrido de presupuesto.** Cuatro configuraciones de pesos por tramo, medidas contra E1/E2.
  Sin esto los quince números del §3.1 serían tan arbitrarios como los treinta y seis que sustituyen.
- **E6 — Determinismo.** Para 200 planes del corpus: `render(plan)` dos veces en procesos distintos
  debe dar el mismo `render_hash`. Un fallo aquí es bloqueante: significa que el replay miente.

### 3.10 Fases

| fase | alcance | criterio de promoción |
|---|---|---|
| 1 | `plan`/`render`, presupuesto de 2 tramos, `r1_lexical` + `r4_recency` + `r6_agentic`, ledger por agente con dedupe y diff, T0 determinista, redacción de secretos, vallas de procedencia | E1 ≥0.60 en `tier_32k`; E6 verde |
| 2 | `r3_symbols` + repo map con base/overlay, `r2_vector`, T1/T2, memoria `procedural` + `user` | cada recaller supera E3 (+2 puntos) o no entra |
| 3 | `r5_deps`, memoria `project`/`episodic` con consolidación, `prefer_cache`, presupuesto adaptativo | E2 muestra mejora de coste por tarea resuelta |

Construir los seis recuperadores en fase 1 y dejar que E3 mate tres es tirar el trabajo y, peor,
plantar mantenimiento permanente. El contrato `Recaller` está completo desde el día uno; las
implementaciones llegan cuando se ganan el sitio.

### Alternativas descartadas

| Alternativa | Por qué se descarta | Coste si me equivoco |
|---|---|---|
| Una sola función `assemble()` declarada "pura" | No lo es: contiene llamadas LLM, timeouts y consultas a índices. El replay divergiría en silencio, que es el fallo que la invariante 2 existe para impedir. | Ninguno; el corte plan/render es estrictamente más expresivo. |
| Re-planificar en el replay en vez de releer el plan | El LLM del fold no es determinista: el replay produciría un contexto distinto al que produjo la decisión auditada. | Alto y silencioso; por eso el plan se materializa. |
| Índice por rama CoW | 20 agentes = 20× el coste de indexado y forks de minutos: mata la invariante 5 en la práctica. | Alto: rediseño del almacenamiento de índices en fase 3. |
| `useful_cap` global de 260k | Número sin evidencia. Con Kimi K3 no hay razón para creer que 260k son útiles, y llenar ventana es la vía rápida para quemar el crédito. | Bajo: el tramo sube con un número si E1 lo demuestra. |
| Nueve slots con floor+techo+peso+orden (36 parámetros) | Presupuesto no evaluable = folklore. Cinco caps y cuatro pesos dan el mismo resultado con la mitad de superficie. | Bajo: son configuración, no contrato. |
| RRF con k=60 | Con 40 candidatos por lista aplana el ranking a casi uniforme. k=10 es lo correcto a esta cardinalidad. | Bajo: un número, auditado por E5. |
| Combinación lineal de scores normalizados | Exige calibración por repo y por embedder; se desajusta al cambiar de proveedor sin que nadie lo note. | 3-5 puntos de recall; se cambia el fusor sin tocar `Recaller`. |
| Solo agentic search | Asume tool-calling fiable y 4-8 turnos de tanteo; con un modelo débil el coste por tarea se dispara. Se conserva como `r6`. | Mantenemos índices que no aportan; E3 los mata en una semana. |
| Solo RAG vectorial | Falla donde duele: identificadores raros, símbolos nuevos, strings de error. Ata la calidad al embedder barato. | Recall en intención difusa; `r2` está previsto con peso 0.7. |
| Ventana deslizante de N turnos | Borra restricciones antiguas por construcción: el modo de fallo más caro que existe. | Ninguno; descartado con evidencia. |
| Resumen puramente LLM sin ledger determinista | Deriva generacional: la restricción crítica desaparece en el tercer fold y nadie sabe cuándo. | Alto: pérdida silenciosa de contratos. |
| Test de retención por interrogatorio LLM en cada fold | Un modelo débil produce falsos rechazos; fold rechazado en bucle → desbordamiento → turno fallido. Y una llamada extra por fold. | Medio: se detectarían folds malos más tarde (en E4) en vez de al instante. |
| Ledger de presentación compartido por sesión | Con N agentes, el agente B recibe "sin cambios desde el turno 7" sobre algo que nunca vio. Corrupción garantizada. | Alto: alucinación indetectable. |
| Dedupe por coseno como exclusión mutua en la escritura de memoria | Carrera entre agentes concurrentes: dos propuestas equivalentes pasan a la vez. La clave idempotente sí es atómica. | Medio: memoria duplicada que degrada el slot. |
| AGENTS.md tratado como `trust: user` | Llega por git; un PR podría reprogramar al agente. | Alto: vector de escalada de privilegios. |
| Guardar el contexto renderizado como estado autoritativo | Rompe la invariante 2 e impide replay y fork barato. | Alto: el resume tras corte devolvería un contexto distinto. |
| Grafo de conocimiento pesado como memoria principal | Coste de mantenimiento desproporcionado; el grafo de *símbolos* sí paga. | Medio: se añadiría como `r8` si E1 lo señala. |

### Cómo se rompe

1. **Divergencia de replay.** Mitigado por el corte `plan`/`render`, el `render_hash` en el journal y
   E6 como puerta de release. Residual: un cambio en el renderizador cambia el hash de planes
   antiguos; por eso el renderizador lleva `render_version` en el plan y las versiones viejas se
   conservan.
2. **Envenenamiento de contexto.** Vallas por procedencia, `trust` propagado en la ref, AGENTS.md
   tratado como `repo`, resultados de herramienta >4k tokens comprimidos por una llamada auxiliar sin
   herramientas y sin permiso de escribir memoria, y evidencia de memoria restringida a clases
   privilegiadas de evento — un archivo no puede acuñar un recuerdo. `context.injection_suspected` es
   una **marca en la valla, no una degradación de nivel**: un comentario legítimo en español ("no uses
   esta función, usa X") dispararía la heurística, y perder contexto útil por un falso positivo es
   peor que la marca. Residual: un README malicioso sigue pudiendo sesgar el plan; eso lo cubre el
   plano de capacidades.
3. **Exfiltración de secretos.** Denylist de rutas + patrones + entropía, aplicada antes de calcular
   el hash de presentación. Residual: un secreto con formato de identificador normal pasa. Segunda
   línea: el plano de capacidades restringe qué rutas puede leer el agente.
4. **Contexto rancio tras editar.** Proyección local `(workspace, path) → hash` alimentada por eventos
   del plano de datos; barrida O(refs) sin RPC. Bloque rancio no se emite: se re-renderiza o se
   convierte en diff. Si la proyección va por detrás del watermark, la ref se degrada a L1.
5. **Ancla perdida.** Los spans llevan `anchor_head`/`anchor_tail`. Si el ancla no se relocaliza, **no
   se adivina**: se degrada a L1 y se anota `ancla perdida` con evento. Presentar líneas equivocadas es
   peor que no presentar nada.
6. **Contenido ausente en CAS.** El GC del CAS puede haber recogido un blob referenciado por un índice
   viejo. `render()` degrada a L0 con la ruta y emite `context.content_missing`; nunca revienta. La
   política de retención del CAS ancla en las refs vivas del journal.
7. **Ledger cruzado entre agentes.** Ledger por `(session, agent)`; las refs llevan `workspace_id` y
   el renderizador rechaza una ref cuyo `workspace_id` no coincide con el de la query.
8. **Desbordamiento de presupuesto.** Invariante verificada post-render, desalojo por bloques enteros,
   nunca truncado parcial. Si ni con desalojo total cabe, `BudgetInfeasible` y el turno falla
   ruidosamente en vez de mandar basura.
9. **Quiebra por contexto.** Presupuesto en USD por sesión, degradación automática de tramo al 70%,
   aprobación humana al 100%. Sin esto, 20 agentes a 128k por turno vacían el crédito de Workers AI en
   semanas sin que ninguna métrica lo señale.
10. **Ledger T0 que no cabe.** Cap de 1500 tokens; blandas se archivan, duras nunca. Si solo las duras
    exceden el cap, `BudgetInfeasible`: la sesión acumuló más contratos de los que caben y continuar
    sería adivinar cuál sacrificar.
11. **Thrash de recuperación.** Banda del 25% y permanencia mínima de 3 turnos; lo volátil vive
    después del último breakpoint en vez de estabilizarse a la fuerza.
12. **Deriva índice/workspace.** `source_watermark` por shard; >200 eventos de retraso → `degraded`,
    peso 0, evento. Devolver la realidad de hace diez minutos es peor que no devolver nada.
13. **Tormenta de reindexado.** Un merge grande invalidaría muchas particiones a la vez. El indexado
    es una tarea del plano de datos con presupuesto y cancelación; mientras corre, los recallers
    afectados sirven `degraded` y `r1_lexical` (sin índice) cubre el hueco.
14. **El modelo débil ignora la estructura.** Texto plano, cabeceras cortas, y repetición del objetivo
    y las restricciones duras al final del prompt además de al principio, con su coste contabilizado.

### Riesgos aceptados

1. **Los quince números del presupuesto siguen siendo una conjetura inicial.** E5 los audita, pero
   hasta que exista el corpus están puestos a ojo. Se acepta: son configuración y cambiarlos no toca
   contratos ni esquemas de evento.
2. **La detección heurística de inyección tendrá falsos positivos y falsos negativos.** Se acepta
   porque solo marca, nunca degrada ni bloquea. Su valor es alertar al humano, no defender.
3. **`prefer_dedupe` sacrifica caché en proveedores que sí la tienen, hasta que la aritmética del
   §3.8 se calibre con precios reales.** Se acepta: el proveedor de partida no tiene caché, y
   optimizar para una capacidad ausente es exactamente lo que la constitución prohíbe.
4. **El corte por bola de radio 3 en el repo map pierde relaciones lejanas legítimas** (una fábrica
   registrada por reflexión a diez saltos). Se acepta a cambio de que el algoritmo no cambie entre 30k
   y 500k archivos. `r6_agentic` es la vía de escape cuando el modelo sabe qué busca.
5. **La calibración del `TokenMeter` heurístico necesita ~50 turnos para converger.** Durante ese
   arranque el margen es del 12% y se desperdicia presupuesto. Se acepta: es una sesión de rodaje por
   modelo, no por sesión.
6. **`episodic` puede no ganarse nunca su sitio.** Es el tipo de memoria con menos señal de utilidad.
   Está en fase 3 precisamente para que E3 pueda matarlo antes de que nadie dependa de él.
7. **Las memorias `user` transversales entre proyectos pueden ser incorrectas en un proyecto
   concreto.** Se acepta porque la precedencia deja que un `charter` aprobado las anule, y porque el
   caso contrario (repetir la misma preferencia en cada repo) es peor experiencia.

---

## 4. Tool ABI, sistema de plugins y MCP

Paquetes: `packages/forge-tools/edecan_forge_tools/` (ABI, catálogo builtin, planificador de superficie), `packages/forge-plugins/edecan_forge_plugins/` (manifiesto, host de procesos, capacidades), `packages/forge-mcp/edecan_forge_mcp/` (adaptador). El kernel no contiene lógica de dominio: contiene el ABI, el broker de capacidades, el planificador de superficie, el árbitro de recursos y el traductor a journal.

### 4.0 La frontera: qué es núcleo, qué es builtin, qué es plugin

La invariante 6 dice «toda herramienta la provee un plugin». Tomada literalmente contradice §1.2 del propio documento («si es la cosa que contiene los fallos, es núcleo») y mete una frontera IPC en la operación más caliente del sistema. Se resuelve con **tres clases de ejecución**, decididas por el kernel y no negociables por el plugin:

| clase | dónde corre | quién la firma | ejemplos | sobrecoste por llamada |
|---|---|---|---|---|
| `core` | en proceso del plano de datos, enlazado al kernel | el proyecto | VFS, CAS, journal, motor de capacidades, scheduler — **no son herramientas** | n/a |
| `builtin` | en proceso del plano de datos, cargado desde el árbol del proyecto, sin extensión de terceros | el proyecto | `fs.*`, `vcs.git`, `proc.terminal`, `tool.search`, `task.done` | p50 < 0,3 ms, p95 < 1 ms |
| `plugin` | proceso separado, aislado, sin rutas ni secretos | terceros o el proyecto | `ct.docker`, `vcs.github`, `web.browser`, `data.*`, `media.ocr`, todo MCP | caliente p50 < 4 ms, p95 < 12 ms; frío p95 < 400 ms |

Regla operativa: **una herramienta es `builtin` si y solo si (a) su superficie de argumentos es cerrada y auditada por nosotros, (b) su tasa de invocación es > 50 llamadas por sesión, y (c) su fallo tumbaría la sesión de todos modos.** `fs.read_file` cumple las tres: en trazas de agentes de código es el 45-60% de todas las llamadas, y pagar 4-12 ms de IPC × 1.200 llamadas = 5-14 s de latencia pura por sesión a cambio de aislar código que escribimos nosotros es un mal negocio. Todo lo demás es `plugin`, sin excepciones y sin «casos especiales de rendimiento»: la única forma de que esta frontera no se erosione es que la lista de `builtin` sea corta, cerrada y requiera cambiar este documento para crecer.

Ambas clases implementan **el mismo ABI**. Un `builtin` no tiene privilegios de ABI: también recibe `CapabilityToken` atenuado, también pasa por el traductor de journal, también respeta presupuesto. Lo único que gana es no cruzar un pipe. Consecuencia deseada: mover una herramienta de `builtin` a `plugin` (o al revés) es un cambio de registro, no de código.

### 4.1 El ABI de herramienta

El contrato actual (`Tool.run(ctx, args) -> ToolResult` en `packages/core/edecan_core/tools/base.py`) es una corrutina que devuelve un `str` más un `dict` opcional. No tiene streaming, progreso, deadline, cancelación, idempotencia, clases de error, referencias de contenido ni modelo de confianza; su único modelo de riesgo es `dangerous: bool`. Se sustituye entero.

**El ABI normativo es el esquema de wire, no la firma Python.** El manifiesto declara `entrypoint` por runtime y habrá plugins en Node y Go; una `Protocol` de Python es un *binding* de conveniencia para el SDK oficial, no la definición. Todo lo que sigue en Python es la proyección del esquema JSON canónico publicado en `packages/forge-tools/schema/abi-v1/`; ante discrepancia manda el JSON Schema. Este párrafo existe porque la ambigüedad contraria produce dos implementaciones incompatibles del mismo ABI.

```python
# edecan_forge_tools/abi.py  — binding Python del esquema abi-v1
ABI_VERSION = 1

Reentrancy    = Literal["pure", "idempotent", "at_most_once", "sequenced"]
Destructive   = Literal["none", "workspace", "external"]
Reversibility = Literal["reversible", "compensable", "irreversible"]
Atomicity     = Literal["atomic", "restartable", "dirty"]
Trust         = Literal["trusted", "untrusted"]

class ToolDescriptor(BaseModel):
    name: str                     # "<ns>.<verbo_objeto>", [a-z0-9_.]{3,60}
    version: str                  # semver del proveedor
    abi: int = ABI_VERSION
    exec_class: Literal["builtin", "plugin"]
    summary: str                  # <=160 chars: lo único que ve el modelo por defecto
    doc: str                      # largo; indexado por tool.search, leído por humanos
    input_schema: dict            # JSON Schema 2020-12, perfil portable (§4.2)
    output_schema: dict | None
    examples: list[ToolExample]   # obligatorio en clase builtin/verified; sintético permitido en adaptados
    reentrancy: Reentrancy
    destructive: Destructive
    reversibility: Reversibility  # de esto depende la aprobación, no de `destructive`
    atomicity: Atomicity          # `dirty` fuerza snapshot CoW previo
    variants: dict[str, Variant] | None   # multi-acción: clase resuelta por argumento discriminador
    requires: frozenset[str]      # patrones de capacidad: "fs.read:**", "net.http:api.github.com"
    scope_keys: list[str]         # plantillas de alcance de idempotencia: "ws_content:{path}"
    resource_keys: list[str]      # plantillas de recurso compartido: "docker.daemon", "port:{port}"
    cache_ttl_ms: int = 0         # 0 = sin caché de resultado
    cost_hint: CostHint           # semilla; la autoridad es la medición (§4.7)
    grace_ms: int = 2000          # ventana de cancelación cooperativa (tope efectivo en §4.1.5)
    streaming: bool = False
    schema_digest: str            # blake3(canonical_json(input_schema))

class Variant(BaseModel):          # p.ej. vcs.git: {"status": Variant(...), "commit": Variant(...)}
    reentrancy: Reentrancy
    destructive: Destructive
    reversibility: Reversibility
    requires: frozenset[str]
    resource_keys: list[str]

class ToolCall(BaseModel):
    call_id: str; turn_id: str; session_id: str; agent_id: str
    tool: str; args: dict
    idempotency_key: str          # calculada por el kernel, §4.1.6
    attempt: int = 1
    cancel_reason: None = None    # se rellena solo en el evento de cancelación

class ToolCtx(Protocol):          # materializado por el SDK a partir de los params del wire
    workspace: WorkspaceRef       # identificador de rama CoW; NUNCA una ruta absoluta
    fs: FsClient                  # RPC al VFS del kernel; el plugin no tiene el árbol montado
    cas: CasClient                # put_stream()->BlobRef ; open(BlobRef)->AsyncIterator[bytes]
    net: NetClient                # host.net.fetch — único egreso con credenciales
    caps: CapabilityToken         # macaroon atenuado a ESTA invocación
    deadline_epoch_ms: int        # absoluto, no relativo: sobrevive a reintentos y colas
    budget: Budget                # usd, tokens, wall_ms, bytes_out, procesos, ficheros_escaneados
    cancel: CancelScope
    log: StructLogger             # el plugin NUNCA escribe journal

class ToolHandler(Protocol):
    descriptor: ToolDescriptor
    def invoke(self, call: ToolCall, ctx: ToolCtx) -> AsyncIterator[ToolEvent]: ...
    async def reconcile(self, call: ToolCall, ctx: ReconcileCtx) -> Outcome | None: ...
```

`invoke` es un generador asíncrono. Emite cero o más `Progress | Chunk | Note | Checkpoint` y termina **siempre** en exactamente un `Outcome`. Un generador que termina sin `Outcome` es violación de protocolo: el kernel sintetiza `internal_fault` y cuenta un fallo de cuarentena.

#### 4.1.1 Outcome, contenido y confianza

```python
class Outcome(BaseModel):
    kind: Literal["outcome"] = "outcome"
    status: Literal["ok","business_error","transient_error",
                    "policy_denied","internal_fault","canceled"]
    content: list[ContentBlock]  # lo que puede llegar al modelo
    error: ToolError | None      # code, phase, message, retry_after_ms, remediation
    facts: dict                  # estructurado: proyecciones, UI, verificadores; nunca al modelo
    usage: ResourceUsage         # wall_ms, cpu_ms, bytes_in/out, usd, procesos
    effects: list[Effect]        # mutaciones DECLARADAS: paths, hosts, dinero, recursos
    partial: bool = False
```

`ContentBlock` es unión tipada — `text`, `json`, `blob(ref, media_type, bytes, preview)`, `image(ref, w, h)`, `patch(ref, base_hash, new_hash)`, `citation` — y **todo bloque lleva `trust`**. Multimodal por construcción: una imagen es un `BlobRef` más metadata y el formateador de contexto decide si el proveedor puede consumirla o si hay que sustituirla por OCR.

**Tres límites distintos que el diseño anterior confundía en uno:**

| límite | valor | quién lo impone | por qué |
|---|---|---|---|
| bloque individual en tránsito | 16 KiB | SDK del plugin | por encima, se sube a CAS y se sustituye por `blob` con `preview` de 2 KiB |
| `Outcome` completo en journal | 64 KiB | traductor del kernel | mantiene el journal barato de leer y replicar |
| **contenido visible al modelo** | **8 KiB por defecto, 16 KiB techo duro** | `ContextFormatter` (bloque 3) | 64 KiB son ~16-20k tokens de UN resultado; con ventana modesta y sin caché de prefijo eso es un turno entero |

La separación importa: el journal y la UI se quedan con todo, el modelo recibe una proyección. Un resultado recortado **siempre** lleva `blob_ref` y `total_bytes`, de modo que el agente puede pedir el resto explícitamente con `fs.read_file(range=...)` o `cas.read` — recortar sin dejar puerta de vuelta es el error que hace que los agentes alucinen contenido.

**Regla del digest de salida de proceso.** Para herramientas con `streaming=True` el `Outcome.content` no es «el final del stream»: es un digesto normativo — primeras 50 líneas, últimas 200 líneas, más las líneas que casan el extractor de errores del runtime declarado (`ErrorExtractor` es un punto de extensión), más `log_ref` y `total_lines`. Últimas 200 porque los errores viven en la cola; primeras 50 porque ahí viven las versiones y la invocación real. Sin esta regla, cada implementador inventa la suya y el agente ve compilaciones truncadas por el principio.

**Confianza y contaminación (taint).** Todo bloque nace `untrusted` salvo que provenga de un `builtin` sobre contenido del workspace escrito en esta sesión. Contenido de red, de un servidor MCP, de un fichero no autorizado por esta sesión, o de cualquier plugin de clase `local`, es `untrusted` sin excepción. Consecuencias mecánicas, no consejos:

1. Se renderiza al modelo dentro de un marco delimitado y marcado como no autoritativo, con el nombre de la fuente.
2. El kernel marca el turno con `tainted=true` en cuanto un bloque `untrusted` entra al contexto.
3. **Un turno contaminado no puede invocar una herramienta con `reversibility="irreversible"` sin aprobación humana**, aunque exista un override previo. El override se concede sobre herramientas, no sobre turnos contaminados.

Esta regla —seguimiento de contaminación a granularidad de turno— es la única defensa estructural contra inyección por resultado de herramienta, que es hoy el vector de ataque dominante contra agentes y que el diseño original solo cubría para *descripciones* MCP. Cuesta un booleano y un chequeo en admisión.

#### 4.1.2 Errores tipados y su política

`ToolError.phase` distingue `pre_dispatch` (el efecto es imposible: DNS, TLS, conexión rechazada, cola llena, validación local) de `post_dispatch` (la petición salió; el efecto puede haber ocurrido). Esta distinción es la que hace utilizable el reintento sin duplicar efectos, y su ausencia en el diseño original convertía cualquier parpadeo de red en un fallo duro para toda herramienta `at_most_once` —es decir, para todo MCP.

| status | reintenta el kernel | lo ve el modelo | efecto lateral |
|---|---|---|---|
| `business_error` | no | sí, íntegro | ninguno; es información accionable |
| `transient_error` + `pre_dispatch` | sí para **cualquier** reentrancia; backoff exponencial con jitter, máx 3 o hasta `deadline` | solo si se agotan los intentos | cuenta contra `budget.wall_ms` |
| `transient_error` + `post_dispatch` | solo si `pure`/`idempotent`; si no, `reconcile()` o humano | solo si se agotan los intentos | ídem |
| `policy_denied` | nunca | mensaje redactado, sin detalle de la política | escala al plano de control, journal `capability.denied` |
| `internal_fault` | 1 reintento en proceso nuevo | mensaje genérico | cuarentena tras 3 en 60 s |
| `canceled` | no | no | no es error: cierra el ciclo limpio |

Un error transitorio no se le muestra al modelo mientras haya reintentos: un modelo débil que lee «timeout de red» reintenta a mano, en bucle, y quema presupuesto. La clase decide; el texto no.

#### 4.1.3 Ciclo de vida y qué entra al journal

Máquina de estados: `requested -> admitted -> [approval_wait] -> [resource_wait] -> running -> {succeeded | failed | denied | canceled | timed_out}`. Las transiciones las emite **el kernel**: `tool.call.requested`, `tool.call.admitted`, `tool.approval.required`, `tool.approval.resolved`, `tool.resource.acquired`, `tool.call.started`, `tool.progress`, `tool.output.appended`, `tool.checkpoint`, `tool.effect.intended`, `tool.call.finished`.

**Los `Chunk` no son eventos de journal.** Un `proc.terminal` de una compilación produce entre 2 y 200 MB de stdout; a 20 agentes concurrentes, journalizar chunks convierte el log en un fichero de logs y destruye la propiedad que hace valioso al journal (pequeño, replicable, replayable). Corrección: los `Chunk` viajan por el canal binario (§4.4.3), se acumulan en un blob de CAS por invocación, y el journal recibe `tool.output.appended{log_ref, offset, len, lines}` como máximo **1 vez por segundo o cada 256 KiB**, lo que ocurra primero. El streaming en vivo a la UI va por el bus de eventos, que no es el journal: es efímero y no es verdad. La verdad es `log_ref` en CAS, que es exactamente la invariante 4.

Presupuesto de eventos, con semántica de pérdida explícita —la ausencia de esta tabla es lo que produce pérdida silenciosa de datos:

| evento | tasa | semántica |
|---|---|---|
| `Progress` | 2/s por llamada | **con pérdida**: se coalesce quedándose con el último |
| `Note` | 5/s por llamada | con pérdida por encima del límite, con contador `notes_dropped` |
| `Chunk` | sin límite de tasa | **sin pérdida**: la contrapresión la da el pipe/socket; nunca se descartan bytes |
| `Checkpoint`, `Outcome`, `tool.effect.intended` | 1/s, 1, sin límite | **sin pérdida**: si no caben, la llamada falla como `internal_fault` |
| total de eventos de journal | 200/s por sesión, 2.000/s por workspace | por encima: `Progress` y `Note` se descartan antes que nada |

#### 4.1.4 Recursos compartidos y concurrencia entre agentes

La invariante 5 aísla el *workspace*, no el mundo. Con 20 agentes, dos `npm install` simultáneos corrompen la caché global, dos `ct.docker run` compiten por el mismo puerto, y dos `vcs.git merge` sobre la misma rama se pisan. El diseño original mencionaba `sequenced` («exige orden por clave de recurso») y nunca definía la clave: eso es un hueco de multi-agencia en un documento cuya invariante 9 es multi-agencia.

Cada descriptor declara `resource_keys` como plantillas sobre argumentos: `["docker.daemon"]`, `["port:{port}"]`, `["pkgcache:npm"]`, `["branch:{workspace}"]`. Antes de `admitted`, el kernel resuelve las plantillas y adquiere **arriendos** (leases) en el plano de control, siempre en orden lexicográfico (evita interbloqueo por construcción), con TTL de 300 s renovable y expiración forzada al `deadline`. Si una clave está tomada: `business_error RESOURCE_BUSY{holder_agent_id, eta_ms}` tras esperar como máximo `min(30 s, deadline restante)`. El agente ve un error accionable, no un cuelgue. Los arriendos se liberan en `tool.call.finished` y por expiración; un arriendo huérfano de un kernel caído expira solo.

Coste: un round-trip al plano de control (p95 < 15 ms) para las herramientas que declaran recursos. Las que declaran `resource_keys: []` —la mayoría, incluidas todas las `fs.*`— no pagan nada.

#### 4.1.5 Cancelación, deadline y atomicidad

Suave: el kernel cierra el generador; la herramienta recibe `CancelledError` en su punto de suspensión y dispone de la gracia para emitir `Checkpoint` y `Outcome(status="canceled", partial=true)` desde su `finally`. Dura: al expirar, se mata el grupo de procesos y el kernel sintetiza el `canceled`.

Gracia efectiva, que el diseño anterior dejaba abierta a que una tool con `grace_ms=30000` sobreviviera 30 s a su propio deadline:

```
grace_efectiva = min(descriptor.grace_ms,
                     5_000  si cancel_reason == "deadline"
                     30_000 si cancel_reason in {"user", "budget", "policy"})
```

El `deadline` es **absoluto y heredado**: se propaga al subproceso como `FORGE_DEADLINE_EPOCH_MS` y toda herramienta que lance procesos debe restarle su propio margen. Ninguna herramienta lee su timeout de configuración propia.

**Muerte del kernel.** Un proceso plugin huérfano seguiría corriendo con un token vivo. Tres cierres: (1) el plugin muere al recibir EOF en su canal de control —es obligación del SDK y se verifica en la suite de conformidad; (2) los procesos plugin viven en un grupo de procesos que el supervisor mata; (3) el token de capacidad expira en `min(deadline, 60 s)` y se renueva en caliente mientras el kernel viva, de modo que un huérfano pierde autoridad en un minuto aunque los dos primeros cierres fallen.

**Atomicidad declarada.** `temp + rename` protege escrituras de un fichero, no un `npm install` matado a mitad. Por eso `atomicity` es parte del descriptor: `atomic` (escritura por temporal y renombrado, nada que limpiar), `restartable` (una segunda ejecución converge), `dirty` (deja el árbol en estado indeterminado). Una herramienta `dirty` fuerza un **snapshot CoW del workspace antes de invocar**; cancelar o fallar revierte al snapshot. Es caro (p95 < 120 ms en el VFS de bloque 2) y por eso se declara en vez de aplicarse a todo.

#### 4.1.6 Idempotencia, alcance y reconciliación

```
idempotency_key = blake3(tool, schema_digest, canonical_json(args), scope_digest)
scope_digest    = blake3(canonical_json({
                    "tenant": tenant_id, "workspace": branch_id,
                    "scope": [kernel.resolve(k, args) for k in descriptor.scope_keys]}))
```

El alcance no es opaco: es la lista `scope_keys` resuelta por el kernel. `fs.read_file` declara `scope_keys: ["ws_content:{path}"]`, que el kernel resuelve al **hash de contenido de esa ruta en esa rama**. Consecuencia: la caché de lecturas no se invalida porque `workspace.head` se movió por un fichero ajeno —que es lo que ocurriría el 100% del tiempo con 20 agentes escribiendo— sino solo cuando cambia ese fichero. La caché de `Outcome` para `pure`/`idempotent` vive en CAS, con presupuesto de 256 MiB por sesión y desalojo LRU, TTL por `cache_ttl_ms` del descriptor (0 = sin caché) y nunca aplica a `at_most_once` ni a `sequenced`. En trazas de agentes de código esto elimina el 30-50% de relecturas idénticas; con el alcance por `head` que proponía el diseño original, eliminaría cerca de cero.

**`reconcile()` sin evidencia es ficción.** Reconstruir «¿ya se creó el PR?» a partir de `blake3(args)` no es posible. Por eso:

- Una herramienta `at_most_once` **debe** emitir `Checkpoint(effect_intent={...})` inmediatamente antes de la llamada irreversible. El kernel lo journaliza como `tool.effect.intended` con `idempotency_key`. Un plugin que ejecuta un efecto irreversible sin haber declarado intención es un defecto de conformidad y lo detecta la suite (el proxy de egreso ve la petición sin intención previa).
- Al reanudar, `reconcile(call, ReconcileCtx)` recibe el último `effect_intent`. `ReconcileCtx` **no es `ToolCtx`**: el deadline original ya venció y su token ya expiró. Es un contexto nuevo con deadline propio (30 s), token recién acuñado y **estrictamente de solo lectura sobre lo externo** (`requires` del descriptor filtrado a lecturas). Sin esta distinción, `reconcile` es un método que nunca puede ejecutarse.
- Si el plugin ya no está instalado, o `reconcile` devuelve `None` para una herramienta irreversible, el kernel emite `tool.reconcile.unavailable`, la sesión entra en `needs_human` y la UI muestra el `effect_intent` en prosa («iba a crear un PR en `org/repo` desde `rama`»). Bloquear es correcto; adivinar no.

Clasificación por defecto: **todo descriptor sin declaración explícita es `at_most_once` / `external` / `irreversible`**. Promover a `idempotent` exige declaración en el manifiesto más revisión humana registrada.

### 4.2 Lo que ve el modelo

El descriptor es la verdad; lo que se manda al LLM es una **proyección** producida por `SchemaRenderer` a partir de `ProviderCapabilities`:

```python
class ProviderCapabilities(BaseModel):
    tool_calling: Literal["native_strict","native_loose","none"]
    max_tools: int; max_schema_bytes: int
    parallel_tool_calls: bool; streaming_tool_args: bool
    json_mode: bool; vision: bool; prompt_cache: bool
    context_window: int
    source: Literal["probe","pinned_table","default_weak"]
```

**Cómo se obtienen, honestamente.** `max_tools`, `max_schema_bytes` y `context_window` no son sondeables a coste razonable: salen de una **tabla fijada en el repo** por `(proveedor, modelo)` revisada a mano, con el perfil más débil como default para modelos desconocidos. Solo `tool_calling` y `json_mode` se sondean, con una llamada de ~40 tokens que pide una herramienta trivial y comprueba la forma de la respuesta; resultado cacheado por `(proveedor, modelo, versión_reportada)` durante 24 h.

**Degradación acotada, no dogma.** El diseño original prohibía degradar en caliente. Eso es correcto como principio y equivocado como absoluto: si un proveedor cambia y `tool_call_parse_failure_rate` supera el 5% en una ventana de 50 llamadas, la alternativa a bajar un escalón de perfil es una sesión inútil. Regla: la degradación ocurre **solo en frontera de turno**, **como máximo un escalón por sesión**, emite `provider.capability.demoted{from, to, evidence}` al journal, y **no persiste**: la siguiente sesión vuelve a partir de la tabla. Es una transición de estado observable, no un parche.

Cuatro perfiles de render: `native_strict` (esquema completo), `native_loose` (aplana `oneOf`, elimina `$ref`, colapsa enums > 20 a `string` con la enumeración en la descripción, recorta `doc`), `prompted_json` y `prompted_xml`. Perfil portable de `input_schema` para todo descriptor propio: sin `$ref`, sin `oneOf`/`anyOf`, sin `patternProperties`, profundidad máxima 3, máximo 12 propiedades de primer nivel. Para Kimi K3 sobre Workers AI se asume `native_loose` con presupuesto de **24 herramientas y 6 KiB** de esquemas por turno; el perfil fuerte permite 64 y 24 KiB.

**Recorte con 300-500 herramientas.** `SurfacePlanner.plan(turn) -> ToolSurface` compone cuatro señales:

1. **Núcleo residente**, 10-12 herramientas siempre presentes: `fs.read_file`, `fs.apply_patch`, `fs.grep`, `fs.search`, `proc.terminal`, `vcs.git`, `tool.search`, `task.done`.
2. **Afinidad de workspace**, leída de una proyección `WorkspaceFingerprint` (hay `Dockerfile` ⇒ `ct.docker` sube de rango) que se actualiza al cambiar la rama. **Nunca se recorre el árbol al planificar**: con 500k ficheros eso son segundos por turno y por agente.
3. **Frecuencia y recencia** por sesión, con EWMA.
4. **Activación dinámica**: `tool.search(query, k=8)` es una herramienta más; devuelve descriptores resumidos y **activa** esas herramientas para el resto del turno, con evento `tool.activation.granted`. Máximo 3 llamadas por turno; la cuarta devuelve `business_error` enumerando los namespaces.

Ranking BM25 sobre `name + summary + doc + examples`; objetivo p95 < 30 ms, p99 < 80 ms en proceso con 500 descriptores. El plan se cachea por `(fingerprint, perfil, digest de herramientas recientes)`, de modo que 20 agentes en el mismo proyecto comparten cálculo. Embeddings solo como reordenador opcional en fase 3.

**El mapa de namespaces, que es lo que evita el fallo silencioso.** El modo de fallo más caro de este esquema es que el agente concluya «no tengo esa herramienta» en vez de buscarla, y una métrica reactiva no lo evita. Por eso el system prompt lleva, siempre, una línea por namespace —unos 240 tokens constantes para 20 namespaces— del tipo «`ct.*`: contenedores y Docker · `vcs.*`: git, GitHub y revisión · `data.*`: bases de datos». No enumera herramientas: enumera dónde buscar. Y `tool.search` sin resultados **nunca** devuelve éxito vacío: devuelve `business_error NO_MATCH` con la lista de namespaces. Un éxito vacío es precisamente lo que enseña a un modelo débil a rendirse.

**Versionado y replay.** El modelo nunca ve la versión. El kernel fija `tool@major.minor` al abrir la sesión; `patch` y actualizaciones marcadas como de seguridad entran en la siguiente frontera de turno con `tool.surface.updated` journalizado —mantener pinneado indefinidamente un plugin vulnerable es peor que un cambio de superficie. Una sesión reanudada con más de 7 días replanifica su superficie y journaliza el diff. Para el replay, el journal guarda **una referencia CAS a la superficie renderizada completa** por turno (`surface_ref`, `surface_digest`), no un mapa de nombres: con 200 turnos y superficies idénticas, el CAS deduplica a un solo blob. El mangling a `[a-z0-9_]{1,64}` cuando el proveedor lo exige es una **función pura y determinista** de `(namespace, nombre, conjunto de superficie)`, recomputable en replay, no un mapa almacenado.

**Degradación a tool-calling por prompt.** Se reusa el protocolo ya probado en `packages/llm/edecan_llm/prompted_tools.py` (escaneo con `raw_decode` desde cada `{`, tolerante a fences y prosa) y se endurece: una sola llamada por respuesta, prefijo de línea obligatorio, y reparación en tres etapas antes de rendirse — (1) parseo tolerante; (2) coerción contra el esquema (string→number, escalar→array de uno, relleno de opcionales, corrección de nombre por distancia de Levenshtein ≤ 2 contra la superficie activa); (3) un único viaje de reparación con el error de validación incrustado. Si falla, `business_error` con el esquema recortado. Guardia: `tool_call_parse_failure_rate` por modelo, alarma sobre 5%, degradación a superficie de 8 herramientas.

### 4.3 Catálogo mínimo

Argumentos abreviados; todos devuelven `Outcome` con `content` y `facts`.

| tool | clase | args | resultado | destr./revers. | reentrancia | capacidad |
|---|---|---|---|---|---|---|
| `fs.read_file` | builtin | `path, range?{start,end}, encoding?` | `text` o `blob` + `hash`, `truncated`, `total_bytes` | none/rev | pure | `fs.read:<glob>` |
| `fs.write_file` | builtin | `path, content\|content_ref, base_hash?, create_dirs?` | `hash`, `bytes`, `created` | workspace/rev | idempotent (con `base_hash`) | `fs.write:<glob>` |
| `fs.apply_patch` | builtin | `path, base_hash, ops[PatchOp], dry_run?` | `PatchReport` | workspace/rev | idempotent | `fs.write:<glob>` |
| `fs.search` | builtin | `query, path_globs?, semantic?, k=20` | `{path, line, score, snippet}[]`, `semantic_used` | none/rev | pure | `fs.read:<glob>` |
| `fs.grep` | builtin | `pattern(re2), globs?, max_matches=500, max_files=50000, ctx_lines?` | matches, `truncated`, `files_scanned` | none/rev | pure | `fs.read:<glob>` |
| `proc.terminal` | builtin | `argv[], cwd, env_allow[], stdin?, tty?, timeout_ms` | streaming; `exit_code`, `log_ref`, digesto | external/comp | at_most_once | `proc.spawn:<allowlist>` |
| `vcs.git` | builtin | `action(status\|diff\|log\|branch\|commit\|merge\|stash), args` | `diff_ref`, `commits[]`, `head` | por `variants` | por `variants` | `vcs.read` / `vcs.write` |
| `tool.search` | builtin | `query, k=8` | descriptores resumidos + activación | none/rev | pure | — |
| `web.browser` | plugin | `action(goto\|click\|type\|read\|screenshot), selector?, url?` | `dom_ref`, `text`, `image_ref` | external/comp | at_most_once | `net.http:<dominios>` + `browser.session` |
| `data.query` | plugin | `dsn_ref, sql, params[], max_rows=1000` | `table` | none/rev | pure | `db.read:<dsn_ref>` |
| `data.execute` | plugin | `dsn_ref, sql, params[], confirm_token` | `rows_affected` | external/irrev | at_most_once | `db.write:<dsn_ref>` |
| `ct.docker` | plugin | `action(build\|run\|exec\|logs\|rm), image, argv?, mounts?` | streaming; `container_id`, `exit_code` | external/comp | at_most_once | `container.run` + `resource_keys:["docker.daemon"]` |
| `vcs.github` | plugin | `action(pr_create\|pr_comment\|issue\|review), payload` | `url`, `number` | external/comp | at_most_once (+`reconcile`) | `net.http:api.github.com` + `github.write` |
| `media.ocr` | plugin | `blob_ref, lang?, layout?` | `text`, `boxes[]` | none/rev | pure | `cas.read` |
| `net.http` | plugin | `method, url, headers, body_ref?, auth_ref?` | `status`, `headers`, `body_ref` | external/varía | `idempotent` si GET/HEAD; si no `at_most_once` | `net.http:<dominio>` |
| `ship.deploy` | plugin | `target, ref, env, confirm_token` | `deployment_id`, `url` | external/irrev | at_most_once (+`reconcile`) | `deploy.exec:<env>` |

Dos correcciones respecto de la versión anterior del catálogo. **`data.database` se parte en `data.query` y `data.execute`**: la reentrancia y la capacidad no pueden depender de un booleano en los argumentos, porque el kernel resuelve la clase *antes* de validar argumentos y porque `db.read` y `db.write` deben ser capacidades distintas (invariante 7). **`vcs.git` no se parte** porque su superficie de acciones es grande y gastar seis ranuras de un presupuesto de 24 sería peor; en su lugar declara `variants` por acción, que el kernel resuelve leyendo el argumento discriminador en admisión. La regla general: la clase de una herramienta es estática o resoluble por un discriminador declarado; jamás dependiente de lógica del plugin.

**Egreso y SSRF.** Toda salida de red del sistema —`net.http`, `web.browser`, `vcs.github`, MCP por HTTP— pasa por el cliente HTTP del kernel, que **resuelve el nombre una vez, valida las IPs resueltas y se conecta a la IP resuelta** con `Host`/SNI explícito. Revalidar antes de conectar no arregla el rebinding de DNS; conectar a la IP ya validada sí. Se hereda el criterio de `packages/mcp/edecan_mcp/seguridad.py` (bloqueo de privadas, loopback y metadata incluso en modo local) y se aplica a todo el egreso, no solo a MCP.

#### `fs.apply_patch`: la decisión de producto del bloque

Cuatro formatos evaluados:

| formato | tokens de salida | verificable local | fallo autodescriptivo | tasa de fallo, modelo débil | cobertura |
|---|---|---|---|---|---|
| diff unificado | bajo | sí (offsets) | no (`@@` mal contado no dice qué arreglar) | alta: exige aritmética de líneas | total |
| reemplazo anclado exacto | medio (~2× la región) | sí (substring + unicidad) | sí (`ANCHOR_NOT_FOUND` / `ANCHOR_AMBIGUOUS(n)`) | baja | total |
| reescritura completa | O(fichero) | trivial | sí | muy baja | total, pero destruye lo no citado |
| edición por AST | muy bajo | sí (parser) | sí | muy baja donde aplica | parcial: nada de YAML/MD/config |

**Decisión: `anchored_replace` por defecto.** `unified_diff` se acepta para productores programáticos (otro agente, un formateador). `full_rewrite` solo bajo 400 líneas y con flag explícito. `ast_edit` llega como plugin por lenguaje en fase 3.

El diseño original se quedaba en el nombre. La especificación normativa, porque dos implementadores leerían «ancla única» de dos formas:

```python
PatchOp = {
  "op": "replace" | "insert_before" | "insert_after" | "delete" | "append_eof" | "create",
  "anchor": str,              # ignorado en append_eof/create
  "anchor_occurrence": int | "only",   # default "only"
  "expect_count": int,        # default 1; nº de coincidencias esperadas
  "new_text": str,
}
```

Reglas, todas verificables:

1. **Ancla**: mínimo 12 caracteres o una línea completa; máximo 200 líneas u 8 KiB. Máximo 64 ops por llamada.
2. **Unicidad**: con `anchor_occurrence="only"` el ancla debe aparecer exactamente `expect_count` veces en la **preimagen**; si no, `business_error ANCHOR_AMBIGUOUS{found:n, positions:[líneas]}` o `ANCHOR_NOT_FOUND{closest_line, closest_similarity}`. El error trae siempre la línea más parecida: es lo que permite corregir en un intento.
3. **Aplicación en una sola pasada sobre la preimagen**: ninguna op ve el resultado de otra. Rangos solapados ⇒ `OVERLAPPING_OPS`. Sin esta regla el orden de las ops es semánticamente ambiguo.
4. **Todo o nada**: si una op falla, no se escribe nada y el `PatchReport` enumera todas las que habrían fallado, no solo la primera — un viaje de reparación en vez de cinco.
5. **Escalera de fuzz**: `exact` → `trailing_ws` → `crlf` → `indent`. El nivel usado va en `PatchReport` y en el journal. **`indent` está deshabilitado por defecto en lenguajes con espaciado significativo** (`.py`, `.yaml`, `.yml`, `.pyi`, Makefile): reindentar un ancla puede aplicar el cambio en el nivel de anidamiento equivocado sin error, que es corrupción silenciosa.
6. **Preservación**: terminador de línea dominante del fichero, presencia o ausencia de salto final, permisos y atributos extendidos. Escritura por temporal y renombrado atómico en el mismo directorio.
7. **Concurrencia optimista obligatoria**: `base_hash` de la preimagen; si el fichero cambió, `business_error CONFLICT` con la relectura fresca adjunta.
8. **`PatchReport` devuelve `new_hash`**, para que el agente encadene ediciones sin releer el fichero. Ahorra una llamada y su contenido en cada edición sucesiva, que en una sesión larga son miles de tokens.

### 4.4 Sistema de plugins

Manifiesto `forge-plugin.toml`: `id` (DNS inverso), `version` semver, `abi_range` **por punto de extensión**, `entrypoint` por runtime, `tools[]`, `extensions[]`, `capabilities_requested[]` con justificación legible, `limits{mem_mb, cpu_ms, procs, fds, net}`, `secret_delivery`, `publication_class`, `signature`. Instalación pinneada por digest en `forge-plugins.lock`.

#### 4.4.1 Aislamiento: lo que de verdad se puede prometer

| modelo | arranque frío | sobrecoste/llamada | aislamiento | ecosistema | veredicto |
|---|---|---|---|---|---|
| en proceso | 0 | 0,2 ms | ninguno: lee el vault, parchea el kernel, lo tumba | total | solo clase `builtin` |
| subproceso + sandbox del SO | 80-400 ms | 4-12 ms | fuerte en Linux, **parcial en macOS** | total | **elegido** |
| WASM (wasmtime) | 5-20 ms | 0,5 ms | el mejor | pobre: sin subprocess, sin sockets crudos, sin libs nativas | fase 3, plugins puros |
| contenedor | 800-3000 ms | 5-15 ms | muy fuerte | total | solo ejecución de código no confiable |

Hay que decirlo sin adornos: **en macOS no existe seccomp ni Landlock, y `sandbox-exec` está formalmente deprecado por Apple.** El desarrollo primario ocurre en macOS. Si la promesa de aislamiento descansara en el sandbox del SO, sería falsa en la plataforma principal.

Por eso el aislamiento es **arquitectónico primero y del SO después**, y en ese orden:

1. **El plugin no recibe rutas del workspace.** `ToolCtx.workspace` es un identificador; toda lectura y escritura pasa por `host.fs.*`, mediada por el VFS del kernel, que aplica el `CapabilityToken` ruta a ruta. Un plugin sin `fs.mount` no tiene una vista del árbol que corromper. Los que la necesitan de verdad (compiladores, Docker) piden `fs.mount:<subárbol>` y reciben una materialización acotada, con evento en el journal.
2. **El plugin no recibe secretos** (§4.4.4).
3. **El plugin no tiene red propia**: sin `net.direct`, el socket lo abre el kernel.
4. **El entorno se limpia**: sin `env.inject` no hereda nada del proceso padre.
5. **El plugin no escribe journal**: sus eventos los traduce el kernel, así que no puede falsificar historia ni inflar el log.
6. Encima de eso, y solo encima: Linux → user namespace + Landlock + seccomp + cgroup v2 para `limits`. macOS → `sandbox-exec` de forma oportunista, `rlimit` para memoria/FDs/procesos, y usuario separado cuando el despliegue lo permite. Windows → job object.

Lo que esto **no** impide, y hay que decirlo: un plugin puede mentir sobre su resultado, y en macOS puede leer el `$HOME` del usuario si el proceso corre con su uid. La mitigación de lo primero es la clase de publicación y los `Verifier` de fase 3. La de lo segundo es la clase de publicación y el consentimiento; es un **riesgo aceptado**, listado abajo.

#### 4.4.2 Clases de publicación

`builtin` (en el árbol, firmado con la clave del proyecto, sin proceso separado), `verified` (revisado y firmado por el proyecto, proceso separado, capacidades preaprobadas), `local` (traído por el usuario: pantalla de consentimiento que enumera el **diff de capacidades** frente a la versión anterior, TOFU con la clave registrada en el journal, todo su contenido nace `untrusted`). El pin por digest en el lock es el control primario; la firma solo es exigible en `builtin`/`verified`.

#### 4.4.3 Transporte: dos canales, no uno

El diseño original criticaba —con razón— que MCP transporte binarios en base64 por un pipe JSON, y a continuación proponía enviar el stdout de una compilación como eventos JSON-RPC. Es la misma patología. Corrección: **dos canales por invocación**.

- **Canal de control** (FD 0/1): JSON-RPC 2.0 enmarcado por longitud. Métodos: `plugin.hello`, `tools.describe`, `tools.invoke`, `tools.event` (plugin→host, solo `Progress`/`Note`/`Checkpoint`/`Outcome`), `tools.cancel`, `host.fs.*`, `host.cas.open/put_stream`, `host.net.fetch`. Mensajes de control ≤ 256 KiB.
- **Canal de datos** (FD 3 en POSIX, named pipe en Windows): tramas binarias con cabecera de 8 bytes `{stream_id: u32, len: u32}`. Por ahí van los `Chunk` y los cuerpos de `cas.put_stream`. Sin base64, sin JSON, sin bufferizado completo. La contrapresión es la del propio socket: si el kernel no lee, el plugin bloquea, que es exactamente lo que se quiere.

Un `Outcome` grande nunca viaja por el canal de control: el SDK sube a CAS y manda `BlobRef`.

#### 4.4.4 Secretos: sin proxy que intercepte TLS

El diseño original decía «el proxy HTTP añade el `Authorization`». **Eso no es implementable sin MITM de TLS**: en un túnel `CONNECT` no se puede inyectar una cabecera, y un proxy que rompe TLS exige instalar una CA en cada runtime de plugin, rompe el pinning de certificados y añade una superficie de ataque enorme. Corrección:

- **La credencial solo existe dentro del cliente HTTP del kernel.** El plugin llama `host.net.fetch(url, method, headers, body_ref, auth_ref)`; el kernel resuelve `auth_ref` contra el vault, valida dominio y método contra el `CapabilityToken`, conecta a la IP ya validada, y devuelve `status/headers/body_ref`. No hay proxy, no hay CA, no hay MITM. Coste: 1-3 ms por petición y los SDK de terceros que abren sus propios sockets no funcionan con credenciales.
- **`net.direct`** existe como válvula (SDKs que exigen su socket), exige consentimiento explícito registrado en el journal y **jamás lleva credenciales**: un plugin con `net.direct` puede hablar con el mundo, no puede hablar como el usuario.
- **Subprocesos que necesitan la credencial** (git sobre HTTPS, `docker login`): `secret_delivery` declarado en el manifiesto, en orden de preferencia `helper` (patrón credential-helper de git: el proceso pide, el kernel responde por un FD) > `file` (fichero en `tmpfs` con modo 0600, borrado en el `finally`) > `stdin` > `env`. `env` se acepta solo si el binario no ofrece otra vía, porque `/proc/<pid>/environ`, los volcados de fallo y `ps e` lo exponen. Nunca en `argv`.

Comprometer un plugin cuesta, entonces, el uso del token dentro del dominio permitido y durante el `deadline`, no el token en sí.

#### 4.4.5 Ciclo de vida de procesos

Un **zygote por runtime** (no por plugin), forkeado tras importar el SDK y **antes** de arrancar el event loop —forkear un loop en marcha es un fallo conocido. Tamaño del pool: `min(4, núcleos/2)`. Los procesos por plugin se crean bajo demanda a la primera invocación, se desalojan a los **90 s** de inactividad, y hay un tope duro de **12 procesos plugin vivos** con desalojo LRU. El diseño original proponía 2 intérpretes calientes por plugin y 300 s de inactividad: con 30 plugins son 60 procesos y 2,5-5 GB de RSS, inviable en un portátil. Memoria por plugin: `limits.mem_mb` del manifiesto, impuesto por cgroup v2 en Linux y `RLIMIT_AS` en macOS; superarlo es `internal_fault`, no OOM del sistema.

Objetivos: arranque en frío p95 < 400 ms, caliente p95 < 12 ms. **Cuarentena**: 3 `internal_fault` en 60 s apagan todas las extensiones del plugin de golpe. Los `builtin` no comparten proceso con terceros; el catálogo `builtin` no puede entrar en cuarentena porque no es un plugin.

### 4.5 MCP como adaptador

MCP entra por `edecan_forge_mcp` con namespace `mcp.<servidor>.<tool>`. **No puede ser el ABI nativo**, y la razón es factual, no estética: le faltan las cinco cosas que la constitución exige. No tiene streaming tipado del resultado (las notificaciones de progreso llevan porcentaje, no contenido parcial: un `terminal` de 3 minutos es un bloque opaco al final). No propaga deadline. No tiene token de capacidad por invocación: la autenticación es por servidor, no por llamada. No tiene clave de idempotencia ni clase de reentrancia. No tiene contenido direccionado por hash: los binarios viajan base64 inline, con +33% de inflación y bufferizado completo — un artefacto de 50 MB son ~67 MB de JSON por un pipe. Y adoptarlo como ABI ata el kernel al calendario de releases de un estándar externo, que es lo que las invariantes 6 y 10 prohíben.

Mapeo normativo:

| MCP | Forge |
|---|---|
| `tools/list` | descriptores, tras `schema_flatten()` |
| `tools/call` | `invoke` con un solo `Outcome` |
| `notifications/progress` | `Progress` (con pérdida, coalescido) |
| cancelación | `notifications/cancelled` best-effort + cierre de transporte al vencer la gracia |
| `resources` | punto de extensión `ContextSource` — son contexto, no herramientas |
| `prompts` | `CommandProvider` |
| `sampling` | **denegado por defecto**: un servidor que pide al cliente ejecutar un LLM es exfiltración de presupuesto. Si se concede, exige capacidad `llm.sample` con tope propio en USD |
| `elicitation` | plano de aprobación, en marco no autoritativo, **sin campos de tipo secreto**, máx. 1 por llamada y 5 por sesión |

**`schema_flatten()` es normativa, no «lo que salga».** Los servidores MCP publican JSON Schema arbitrario y el perfil portable prohíbe `$ref` y `oneOf`. El adaptador: inlinea `$ref` hasta profundidad 4; convierte `oneOf`/`anyOf` en la primera rama más una descripción que enumera las alternativas; ante ciclo o profundidad excedida, degrada a `{type: "object", additionalProperties: true}` con la forma original en la descripción. Toda transformación con pérdida marca `lossy=true` en el descriptor, visible en la UI. Sin esta especificación, dos implementadores producen dos superficies distintas para el mismo servidor.

**`examples` no puede ser obligatorio para MCP** —los servidores no los publican—, así que el adaptador sintetiza uno degenerado desde el esquema (campos requeridos con valores por defecto de su tipo) marcado `synthetic=true`, que puntúa en BM25 pero nunca se le presenta al modelo como ejemplo real. El campo es obligatorio solo en clase `builtin`/`verified`.

Toda herramienta MCP entra como `destructive=external`, `reversibility=irreversible`, `reentrancy=at_most_once` y por tanto con aprobación —conservando la postura del `tool_adapter.py` actual—, pero **con override por herramienta**, concedido una vez por el usuario y registrado en el journal, para que un servidor de 300 herramientas sea usable y una automatización sin humano sea posible. El override no aplica en turnos contaminados (§4.1.1). Se mantienen la validación SSRF con conexión a IP resuelta y el escaneo heurístico de descripciones de `packages/mcp/edecan_mcp/seguridad.py`, y se corrige el defecto conocido y ya documentado del adaptador: **caché de sesión MCP por `(tenant, servidor)` con expiración de 5 min** y sonda de vivacidad, en vez de un handshake completo —o un subproceso entero, en `stdio`— por invocación.

### 4.6 Puntos de extensión más allá de herramientas

Mismo manifiesto, mismo proceso, mismo protocolo, `abi_range` versionado por punto: `ProviderPlugin` (implementa el `LLMProvider` de `packages/llm/edecan_llm/base.py` extendido con `capabilities()`), `CommandProvider`, `PanelProvider` (árbol de componentes serializado, sin JS arbitrario: el plano de control no ejecuta código de terceros en el navegador del usuario), `LifecycleHook`, `ApprovalPolicy`, `ContextFormatter`, `ErrorExtractor` y `Verifier`.

Dos reglas de autoridad que el diseño original dejaba abiertas y que son agujeros reales:

- **`ApprovalPolicy` forma un retículo y solo se puede subir.** Un plugin de terceros puede transformar `allow → ask → deny`, **nunca al revés**. Solo las políticas `builtin` firmadas por el proyecto y la configuración explícita del usuario pueden relajar. Sin esta regla, instalar un plugin es conceder la facultad de auto-aprobarse.
- **Los hooks son observadores por defecto.** `on_tool_pre` no puede mutar argumentos salvo que declare la clase `mutating`, que exige su propia capacidad y journaliza el diff de argumentos. Un hook que reescribe silenciosamente los argumentos de otra herramienta es una vía de inyección con firma de plugin legítimo.

Presupuesto de hook: p95 < 50 ms. Los hooks observadores **fallan abiertos**; los de política **fallan cerrados**.

### 4.7 Números objetivo

| magnitud | objetivo |
|---|---|
| invocación `builtin` (sobrecoste de ABI) | p50 < 0,3 ms · p95 < 1 ms |
| invocación plugin en caliente | p50 < 4 ms · p95 < 12 ms |
| arranque de plugin en frío | p95 < 400 ms |
| `SurfacePlanner.plan` con 500 descriptores | p95 < 30 ms · p99 < 80 ms |
| acuñar + verificar `CapabilityToken` | < 0,2 ms |
| adquirir arriendo de recurso | p95 < 15 ms |
| snapshot CoW previo a herramienta `dirty` | p95 < 120 ms |
| superficie ofrecida, perfil débil / fuerte | 24 tools / 6 KiB · 64 tools / 24 KiB |
| contenido visible al modelo por resultado | 8 KiB por defecto · 16 KiB techo |
| `Outcome` en journal | 64 KiB |
| eventos de journal | 200/s por sesión · 2.000/s por workspace |
| caché de `Outcome` | 256 MiB por sesión, LRU |
| procesos plugin vivos | 12, LRU |
| `recall@8` de `tool.search` sobre corpus etiquetado | ≥ 0,95 (puerta de regresión en CI) |
| `tool_call_parse_failure_rate` | < 5% por modelo |

`cost_hint` del descriptor es solo semilla: la autoridad es el `ResourceUsage` medido, agregado en una proyección EWMA por herramienta, que el planificador usa en cuanto hay n ≥ 20 muestras. Así el campo no se pudre.

### Alternativas descartadas

- **`ToolResult` con `content: str`, como hoy en `edecan_core`.** Obliga a serializar imágenes, tablas y parches a texto, y no distingue lo que ve el modelo de lo que ve la UI. Coste de mantenerla: cada consumidor reparsea prosa.
- **Todo es plugin, sin clase `builtin`.** Descartada por §1.2 y por medición: 4-12 ms de IPC × 1.200 lecturas por sesión, a cambio de aislar código propio.
- **Un bus de eventos compartido donde el plugin publica directo al journal.** Viola la invariante 2: un plugin con bug corrompe el orden causal; uno malicioso falsifica historia.
- **Journalizar los `Chunk`.** Convierte el journal en un fichero de logs: 2-200 MB por compilación × 20 agentes. Se sustituye por blob en CAS más `tool.output.appended` acotado.
- **Proxy HTTP que inyecta la cabecera `Authorization`.** Imposible sin MITM de TLS; el MITM exige una CA en cada runtime, rompe pinning y crea una superficie mayor que la que resuelve. Se sustituye por `host.net.fetch`.
- **Reentrancia dependiente de argumentos (`readonly?`).** El kernel resuelve la clase antes de validar argumentos, y `db.read`/`db.write` deben ser capacidades distintas. Se sustituye por división de herramienta o `variants` con discriminador declarado.
- **Retrieval de herramientas con embeddings desde fase 1.** Añade dependencia, latencia y coste por turno para ganar poco sobre BM25 en 300-500 descriptores cortos escritos por nosotros. Entra como reordenador en fase 3.
- **MCP como ABI nativo.** Descartada con datos: sin streaming de contenido, sin deadline, sin capacidades, sin idempotencia, sin CAS.
- **WASM como aislamiento primario en fase 2.** Hoy no ejecuta el ecosistema Python real que un plugin de herramientas necesita (subprocess, sockets, libs nativas). Vuelve en fase 3 para plugins puros de transformación, donde 5-20 ms de arranque es imbatible.
- **Diff unificado como formato único de edición.** Tasa de fallo alta con modelos no frontera y errores no autodescriptivos.
- **Aprobación humana para toda herramienta externa, sin override.** Es la postura actual del adaptador MCP; hace inutilizable cualquier servidor grande y toda automatización sin humano.
- **Prohibir toda degradación de perfil de proveedor en caliente.** Correcto como principio, absoluto insostenible: la alternativa a bajar un escalón es una sesión inútil. Se sustituye por degradación acotada, journalizada y no persistente.

### Riesgos aceptados

1. **En macOS el aislamiento del SO es débil.** Un plugin `local` malicioso puede leer el `$HOME` del usuario si corre con su uid. Aceptado porque el aislamiento arquitectónico (sin rutas, sin secretos, sin red propia, sin journal) cubre el daño *al sistema*, y porque exigir contenedores en el portátil del usuario mataría la experiencia. Mitigación: clase de publicación, consentimiento con diff de capacidades, y usuario separado en despliegues servidor.
2. **Un plugin puede mentir sobre su resultado.** No hay defensa dentro del ABI. Mitigación parcial: clase de publicación, `effects` declarados contrastados contra el tráfico real observado en `host.net.fetch`, y `Verifier` en fase 3.
3. **`cost_hint` y `reversibility` los declara quien escribe el plugin.** Una declaración falsa de `reversible` relaja la aprobación. Mitigación: default hostil (`irreversible`), promoción con revisión, y auditoría contra `effects` observados.
4. **La caché de `Outcome` puede servir un resultado obsoleto si un `scope_key` está mal declarado.** Es un fallo silencioso de correctitud. Mitigación: `scope_keys` vacío ⇒ `cache_ttl_ms` forzado a 0; muestreo del 1% de llamadas cacheadas que ejecuta de verdad y compara, con alarma sobre divergencia.
5. **`recall@8 ≥ 0,95` sobre un corpus de 200 intenciones escritas por nosotros es una puerta de regresión, no evidencia de calidad.** Aceptado en fase 2; se sustituye por intenciones extraídas de sesiones reales en cuanto haya volumen.
6. **La suite de conformidad de plugins no puede probar que un `at_most_once` emitió `effect_intent` antes del efecto en todos los caminos.** Solo detecta los que pasan por `host.net.fetch`. Un plugin con `net.direct` puede saltárselo. Aceptado: `net.direct` es consentimiento explícito y journalizado.

### Cómo se rompe

1. **Explosión de la superficie.** El ranking falla y el agente concluye «no tengo esa herramienta» en vez de buscarla. Detección: `tool_not_found_rate` sube sin que suba el error; `recall@8` en CI con umbral 0,95. Contención estructural: mapa de namespaces residente y `NO_MATCH` que enumera namespaces en vez de éxito vacío.
2. **Tormenta de parseo en modo prompted.** El proveedor cambia y el modelo emite JSON inválido sistemáticamente. Síntoma: `tool_call_parse_failure_rate` > 5%. Contención: superficie de 8 herramientas, una llamada por respuesta, y degradación de un escalón en frontera de turno.
3. **`at_most_once` mal clasificado.** Una herramienta declarada `idempotent` que en realidad cobra dinero se reintenta y duplica el efecto. El fallo más caro del bloque. Contención: default hostil, revisión manual para promover, y `effects` contrastados con el tráfico real.
4. **`reconcile` sin evidencia o sin plugin.** Al reanudar no se puede saber si el efecto ocurrió. Contención: `effect_intent` obligatorio antes del efecto; si falta el plugin, `needs_human` con el intent renderizado. Nunca se adivina.
5. **Fuga de gracia en cancelación.** Una herramienta ignora `CancelledError` (bucle CPU-bound sin `await`) y se la mata a mitad de una escritura. Contención: `atomicity` declarada, `temp+rename` para `atomic`, snapshot CoW previo para `dirty`.
6. **Plugin huérfano con token vivo tras caída del kernel.** Contención en tres capas: EOF en el canal de control mata al plugin, grupo de procesos, y expiración del token en ≤ 60 s.
7. **Interbloqueo o inanición de recursos con N agentes.** Dos agentes esperan `docker.daemon` y `pkgcache:npm` en orden opuesto. Contención: adquisición en orden lexicográfico —hace el interbloqueo imposible—, espera máxima de 30 s y `RESOURCE_BUSY` accionable. Riesgo residual: inanición del agente de baja prioridad, detectable por `resource_wait_p99`.
8. **Cuarentena en cascada.** Un plugin del que dependen 8 herramientas cae y el agente se queda sin ellas a mitad de tarea. Contención: los `builtin` no son plugins y no pueden entrar en cuarentena; los de terceros no comparten proceso; 3 fallos en 60 s con un reintento previo en proceso limpio.
9. **Envenenamiento por contenido, no solo por descripción.** Un fichero del repo o una página web contienen instrucciones para el modelo. Contención: `trust` en cada bloque, marco no autoritativo, y la regla dura de que un turno contaminado no puede invocar herramientas irreversibles sin humano.
10. **Agotamiento de CAS.** Una herramienta en bucle sube 10 GB de chunks. Contención: `budget.bytes_out` por llamada y por turno, corte duro con `policy_denied`. Si el CAS falla por disco lleno: `transient_error`; si persiste, el kernel entra en modo degradado (solo herramientas `pure` con contenido inline) y journaliza `cas.degraded`.
11. **Deriva de esquema en replay.** Un plugin se actualiza y el replay ya no reproduce lo que el modelo vio. Contención: `surface_ref` + `surface_digest` por turno en CAS, con la superficie renderizada completa archivada.
12. **Erosión de la frontera `builtin`.** Alguien mueve `ct.docker` a `builtin` «por rendimiento» y el aislamiento se desangra herramienta a herramienta. Contención: la lista es cerrada en este documento; ampliarla exige modificarlo. Detección: prueba en CI que compara la lista de `exec_class="builtin"` contra una constante.

### Fases

| fase | alcance | por qué ahí |
|---|---|---|
| **1** | ABI `abi-v1` congelado; clase `builtin` en proceso; `fs.read_file`, `fs.apply_patch`, `fs.grep`, `proc.terminal`, `vcs.git`, `task.done`; errores tipados con `phase`; deadline absoluto y cancelación en dos fases; `trust` en cada bloque; superficie estática de ≤ 12 herramientas sin planificador | Es el conjunto mínimo con el que un agente cierra una tarea real de código. Sin plugins, sin planificador, sin MCP. Lo que se aprende aquí decide si el ABI está bien cortado, que es lo único irreversible del bloque. |
| **2** | Host de plugins fuera de proceso con dos canales; `CapabilityToken`; `host.net.fetch` con egreso a IP resuelta; adaptador MCP con `schema_flatten` y caché de sesión; `SurfacePlanner` + `tool.search` + mapa de namespaces; idempotencia con `scope_keys`; `effect_intent` y `reconcile`; arriendos de recursos | El aislamiento y la superficie solo se pueden diseñar bien cuando hay herramientas reales que aislar y demasiadas para ofrecer. Los arriendos entran aquí porque el segundo agente concurrente llega aquí. |
| **3** | `ast_edit` por lenguaje; `Verifier`; `PanelProvider`; reordenador por embeddings; WASM para plugins puros; `ContextFormatter` de terceros | Todo optimización o extensión sobre contratos ya validados; nada de esto cambia una firma. |

Regla de fase transversal: **el ABI se congela al final de la fase 1 y cualquier cambio posterior sube `ABI_VERSION`.** Un plugin declara `abi_range` por punto de extensión, de modo que romper el ABI de paneles no deja de servir sus herramientas.

---

## 5. Execution Engine, sandbox, seguridad y secretos

Este es el unico punto del sistema donde algo llega a *ocurrir*. Journal, CAS, workspaces y plugins
son representacion; aqui una representacion se convierte en un proceso con un PID, un socket y un
efecto en el mundo. Todo lo que sigue esta escrito para un cerebro debil (Kimi K3 en Workers AI:
sin prompt caching, contexto modesto, tool-calling poco fiable, JSON no estricto) sobre un portatil
macOS de 8-10 nucleos, y para escalar hacia arriba, nunca hacia abajo.

### 5.0 Reparto en paquetes y frontera de planos

| Paquete | Plano | Responsabilidad |
|---|---|---|
| `packages/forge-exec/edecan_forge_exec/` | control | admision, scheduler, maquina de estados de invocacion, reintentos, `BudgetLedger`, `EffectLedger` |
| `packages/forge-sandbox/edecan_forge_sandbox/` | datos | backends de aislamiento, supervisor de proceso, captura y redaccion de streams, reaper |
| `packages/forge-caps/edecan_forge_caps/` | control | gramatica de capacidades, verificador, trinquete de taint, `ApprovalBroker` |
| `packages/forge-secrets/edecan_forge_secrets/` | control | `SecretStore`, `SecretBroker`, politica de materializacion |
| `packages/forge-egress/edecan_forge_egress/` | control | proxy de salida, resolucion DNS, inyeccion de credencial, escaneo de cuerpo saliente |

`forge-exec` **no importa** `forge-sandbox`: recibe un `SandboxProvider` inyectado. El kernel nunca
contiene la palabra `subprocess`. `forge-secrets` vive **solo en el plano de control**: el plano de
datos jamas obtiene una interfaz de boveda, solo recibe material ya materializado y de un solo uso.

**Tercer canal, declarado en vez de fingido.** La invariante 3 dice que los planos hablan solo por
eventos y refs de contenido. Eso es cierto para todo salvo dos cosas que fisicamente no pueden ir
por el journal: el material de secreto y la contrapresion de streams. Se declara explicitamente un
**canal de materializacion** control -> datos: unidireccional, efimero, no journaleado, sobre socket
unix local (o mTLS en remoto), que transporta exactamente tres cosas: `SecretMaterialization`
de un solo uso, senales de cancelacion, y creditos de backpressure. Todo lo que pasa por ahi deja
en el journal un evento *sobre* el hecho, nunca su contenido. Pretender que este canal no existe es
la forma habitual de que aparezca despues, sin contrato y con secretos dentro.

**Digest de seguridad: SHA-256, no BLAKE3.** Todo digest con valor de seguridad de este bloque
(`decision_digest`, cadena de auditoria, firma de spec de sandbox, claves del `EffectLedger`) usa
SHA-256. Motivo: el plano de control debe ser verificable en el edge, y WebCrypto de Workers ofrece
SHA-256 nativo mientras BLAKE3 exige embarcar WASM y mantener paridad byte a byte entre una
implementacion Rust local y una WASM remota. Ademas hoy el workspace no depende de `blake3` en
ningun `pyproject.toml`; introducirlo como dependencia obligatoria del plano de control es deuda
gratuita. El CAS puede usar BLAKE3 por throughput (decision del bloque 4): son dominios distintos
y no se mezclan; un `CasRef` se transporta con su algoritmo etiquetado (`b3:...` / `sha256:...`).

### 5.1 Scheduler

Tres niveles con presupuestos de latencia distintos:

1. **Admision** — decision en memoria pura, objetivo p99 < 5 ms, p999 < 20 ms. Valida esquema,
   verifica capacidad, evalua taint, consulta presion de recursos y epoca de revocacion. **No hace
   ninguna I/O**: ni disco del workspace, ni red, ni CAS, ni base de datos.
2. **Encolado** — round-robin ponderado con una cola por `agent_id` dentro de una `lane` por
   `ResourceClass`. El contrato es DRR (deficit round robin); la implementacion de fase 1 es
   round-robin simple con deficit acreditado, sin jerarquia.
3. **Despacho** — semaforos contra recursos reales.

```python
class ResourceClass(StrEnum):
    IO_LIGHT = "io_light"                # read, grep, stat  -> 64 concurrentes/workspace
    IO_HEAVY = "io_heavy"                # write, ingest CAS -> 8/workspace
    CPU_HEAVY = "cpu_heavy"              # build, test, lint -> max(2, ncpu-2) GLOBAL
    NET = "net"                          # fetch, api        -> 16 global + token bucket 20 rps
    MODEL = "model"                      # inferencia        -> acotado por presupuesto
    EXTERNAL_EFFECT = "external_effect"  # deploy, push      -> 1 por effect_target (global)
```

`ResourceClass` es campo **obligatorio del manifiesto**, no heuristica.

**Admision sin I/O: el problema de los args.** `args_ref: CasRef` significa que los argumentos
completos viven en CAS. Pero la autorizacion se decide *sobre* los argumentos (que rutas escribe,
que host toca). Si admision los lee del CAS, adios a los 5 ms; si van inline en el journal, adios a
la invariante 4. Se resuelve con una proyeccion acotada:

```python
@dataclass(frozen=True)
class AuthzFacts:
    """Proyeccion de los args relevante para autorizar. <= 4 KiB, inline en el spec."""
    paths_read: tuple[PurePosixPath, ...]
    paths_write: tuple[PurePosixPath, ...]
    hosts: tuple[tuple[str, int, str], ...]   # (host, puerto, esquema)
    effect_targets: tuple[str, ...]           # identificador estable del destino externo
    secret_refs: tuple[str, ...]
    exec_binaries: tuple[str, ...]
    overflow: bool                            # True si la proyeccion se trunco
```

El adaptador de la herramienta produce `AuthzFacts` a partir de los args ya canonicalizados; el
motor los valida contra la capacidad. **`overflow=True` es denegacion**, no advertencia: una
invocacion cuya superficie de autorizacion no cabe en 4 KiB (un `rm` de 3 000 rutas) se parte o se
rechaza. El supervisor re-verifica en el plano de datos que la ejecucion real no salga de
`AuthzFacts` (el sandbox lo impone de todos modos); una divergencia emite `sec.authz.drift` y mata
la invocacion. Sin `AuthzFacts` dos implementadores construyen dos sistemas distintos: uno lento y
otro con payloads en el journal.

**Reparto justo.** `quantum = weight_agent * 10 ms` de CPU acreditado. Un agente que quema su cuota
entra en deficit negativo y deja de recibir despacho de `CPU_HEAVY` hasta reponer; sus invocaciones
`IO_LIGHT` siguen fluyendo porque estan en otra lane con su propio semaforo. Ese es el mecanismo
concreto de aislamiento de ruido: un agente que compila sin parar no vuelve lento el `read_file` de
otro. Anti-inanicion: cada lane tiene `max_wait_ms` (200 en `IO_LIGHT`, 60 000 en `CPU_HEAVY`); al
superarlo la invocacion sube un escalon de prioridad (aging) hasta un techo, para que un agente con
`weight=0.1` no espere para siempre.

**Veinte agentes compilando a la vez.** Tres mecanismos, en este orden:

- **Semaforo global `CPU_HEAVY`** de `ncpu-2`. Nunca hay veinte compilaciones: hay seis en un
  portatil de 8 nucleos y catorce esperando.
- **Cache de compilador compartida.** Un directorio `sccache`/`ccache` montado rw en un volumen
  content-addressed **compartido entre ramas**. Es lo que de verdad hace barato que veinte ramas
  compilen: comparten objetos a nivel de unidad de compilacion, que es donde esta la redundancia.
- **Singleflight de invocaciones identicas en vuelo** (ver 5.3), que resuelve un problema distinto y
  mucho mas pequeno: el mismo comando lanzado dos veces a la vez.
- **Contrapresion en admision**: si `load1 > ncpu*1.5` o RAM libre < 15%, `CPU_HEAVY` entra en
  `queued` y no en `dispatched`. No se rechaza (invariante 8 exige reanudable), se difiere.

**Presion en admision sin I/O.** `load1`, RAM libre y `revocation_epoch` se leen de un cache en
memoria refrescado cada 500 ms por un muestreador aparte, y la lista de revocacion es un conjunto
local con epoca monotona empujada por el plano de control. Admision nunca bloquea; como maximo
decide con datos de 500 ms de antiguedad, que es exactamente la precision que necesita.

**Herramienta que miente sobre su clase.** El supervisor observa CPU y wall real. La reclasificacion
**no cambia la lane de la invocacion en curso** (ya esta corriendo y quemando CPU): baja su
`cpu.weight` en cgroup (Linux) o su `taskpolicy` (macOS), y ademas fija una reclasificacion
**pegajosa por `tool_id`**, journaleada como `exec.class.reclassified`, que aplica a las
invocaciones futuras. Se revierte sola tras 50 invocaciones consistentes con la clase declarada.

**Cardinalidades objetivo de fase 1:** 20 agentes concurrentes, 200 invocaciones en vuelo, 2 000
encoladas persistidas, cola sobreviviendo a reinicio, repositorios de hasta 500 000 archivos.

### 5.2 Ciclo de vida de una invocacion

```
received -> admitted -> validated -> authorized -> [approval_pending] -> queued
         -> dispatched -> running -> (succeeded | failed | cancelled | timed_out | indeterminate)
         -> settled
rejected  # terminal desde admitted / validated / authorized
```

```python
@dataclass(frozen=True)
class InvocationSpec:
    invocation_id: ULID
    session_id: ULID
    workspace_id: ULID
    workspace_epoch: int          # tip de la rama CoW en el momento de admitir
    agent_id: str
    tool_id: str
    args_ref: CasRef              # args canonicos completos, en CAS
    args_digest: str              # sha256 de la forma canonica (JCS, RFC 8785)
    authz: AuthzFacts             # proyeccion inline, <= 4 KiB
    capability: CapabilityToken
    resource_class: ResourceClass
    effect_class: EffectClass
    budget: Budget                # tokens, usd_micros, wall_ms, cpu_ms, bytes_out
    deadline_utc: datetime
    idempotency_key: str | None
    taint: TaintSet
    trace_parent: str

class ExecutionEngine(Protocol):
    async def submit(self, spec: InvocationSpec) -> Admission: ...
    async def dry_run(self, spec: InvocationSpec) -> Admission: ...   # valida y autoriza, no ejecuta
    async def cancel(self, invocation_id: ULID, *, mode: CancelMode, reason: str) -> None: ...
    def stream(self, invocation_id: ULID, *, from_seq: int = 0) -> AsyncIterator[ExecEvent]: ...
    async def settle(self, invocation_id: ULID) -> Settlement: ...
```

**Canonicalizacion de argumentos, especificada y no insinuada.** `args_digest` es
`sha256(JCS(args))` con RFC 8785: claves ordenadas, sin espacios, numeros en forma canonica, UTF-8
NFC en las cadenas, rutas normalizadas a `PurePosixPath` relativa al workspace sin `..`. Se
especifica porque de ella cuelga `decision_digest`: si dos implementadores canonicalizan distinto,
**todas** las aprobaciones fallan por digest y nadie entiende por que.

**Concesiones al modelo debil.** Kimi K3 producira args malformados a menudo. Tres mecanismos:

- `dry_run` valida esquema, capacidad y aprobacion sin ejecutar ni gastar presupuesto de efecto.
  Es el camino barato para que el agente compruebe antes de pedir una aprobacion humana.
- **Techo de reparacion**: 3 fallos de validacion consecutivos sobre el mismo `tool_id` en un turno
  producen `VALIDATION_EXHAUSTED`, que corta el bucle y devuelve al modelo el esquema recortado del
  campo que falla. Sin este techo, un modelo debil quema el presupuesto entero reintentando.
- **Rompe-bucles**: la misma tupla `(tool_id, args_digest)` que falla 3 veces en una sesion produce
  `REPEATED_FAILURE` sin ejecutar. Es el mayor quemador de presupuesto real con modelos flojos, y no
  es detectable por el modelo, que es justo quien esta en el bucle.

**Resultado que vuelve al modelo.** Nunca la salida completa. El `ToolResult` que ve el modelo lleva
como maximo 4 KiB de cabecera + 4 KiB de cola de stdout/stderr, el exit code, la duracion y el
`CasRef` de la salida integra. Para el resto existe una herramienta `output.read(ref, byte_range)`.
Con contexto modesto, devolver 40 MB de `cargo build` al modelo no es una degradacion: es el fin
de la sesion.

**Streaming.** Nunca evento por linea. El supervisor acumula en `StreamSegment` con doble disparador
(256 KiB o 250 ms, lo que llegue antes): a 60 lineas/s por agente el journal recibiria 60 eventos/s;
con segmentacion baja a 4/s. Cada segmento se **redacta**, se escribe a CAS y el journal transporta
`exec.output.segment {invocation_id, stream, seq, ref, bytes, indexed}`. Todos los segmentos van a
CAS; el journal **indexa** refs hasta 8 MiB acumulados por invocacion, y a partir de ahi indexa solo
los primeros 2 MiB, los ultimos 2 MiB y un `overflow_ref` al blob concatenado. `indexed=false`
significa "el journal no lista este segmento", no "el contenido se perdio". Si la salida supera
`budget.bytes_out` (default 64 MiB) la invocacion muere con `OUTPUT_BUDGET_EXCEEDED`: un bucle
verboso no llena el disco del usuario. La UI hace tail del bus efimero (WebSocket, objetivo < 50 ms)
y reconstruye por journal al reconectar.

**Cancelacion, sin mitologia de checkpoints.** Dos modos. `COOPERATIVE`: SIGINT o `cancel_token` con
`grace_ms` (default 5 000, hasta 60 000 si la herramienta declara `checkpointable=True`). `FORCED`:
SIGKILL al grupo de procesos y destruccion del sandbox. Siempre por **grupo de procesos, cgroup o
VM**, nunca por PID individual. En macOS: `posix_spawn` con `setpgid`, `kill(-pgid)`, mas barrido por
la marca `FORGE_INVOCATION=<ulid>` del entorno para hijos que escapan con `setsid`. La verdad
incomoda es que **la mayoria de herramientas no puede hacer checkpoint** (`cargo build` no tiene
estado que guardar). La durabilidad frente a cancelacion no viene de la herramienta: viene de que el
motor registra `workspace_epoch` antes de despachar toda invocacion con `effect_class >= REVERSIBLE`,
de modo que revertir es un snapshot, no una cooperacion.

**Huerfanos y reuso de PID.** Cada sandbox toma un lease de 30 s renovado cada 10 s con **reloj
monotonico local**, nunca reloj de pared. El estado vive en un directorio por runtime:
`{invocation_id, pid, pgid, pid_start_time, sandbox_backend, lease_deadline_monotonic}`.
`pid_start_time` no es opcional: sin el, el reaper mata un PID reciclado por el sistema y **destruye
trabajo ajeno del usuario**, que es un fallo de perdida de datos silencioso y muy dificil de
diagnosticar. El reaper corre cada 15 s, enumera el directorio de estado, verifica `(pid,
pid_start_time)` contra el SO, mata por pgid lo que no tenga lease vigente ni invocacion `running`, y
emite `exec.orphan.reaped`. El primer acto del runtime al arrancar es un reaper completo, que ademas
libera los holds de presupuesto huerfanos. Eso es lo que cubre el corte de luz.

**Timeouts.** Tres relojes independientes: `wall_deadline` absoluto, `idle_timeout` (sin salida ni
syscall observable, default 120 s) y `budget_exhausted`. El primero que dispara gana y **el motivo
se registra**: no existe un `timeout` generico en el journal.

**Interaccion con workspaces concurrentes.** Una rama CoW con invocaciones en vuelo **no se puede
fusionar ni borrar**: merge y delete toman un lease que conflictua y esperan o fallan con
`BRANCH_BUSY`. Un fork de sesion captura `workspace_epoch` pero **no hereda invocaciones en vuelo**:
la rama hija arranca sin trabajo pendiente. Sin esta regla, dos agentes producen un merge sobre un
arbol que un tercero esta mutando, y la corrupcion no aparece hasta el siguiente build.

### 5.3 Reintentos, coalescencia e idempotencia

| Clase de error | Ejemplos | Reintentable | Politica |
|---|---|---|---|
| `TRANSIENT_INFRA` | sandbox no arranco, pool agotado, EAGAIN | si, automatico | 5 intentos, backoff exponencial base 250 ms, factor 2, jitter completo, techo 30 s |
| `TRANSIENT_REMOTE` | 429, 503, timeout de conexion | si, solo si `idempotent=True` | 3 intentos, respeta `Retry-After` |
| `TOOL_FAILURE` | exit != 0, test rojo | no | es un **resultado**, no un error |
| `VALIDATION_EXHAUSTED` / `REPEATED_FAILURE` | bucle del modelo | no | corta el turno con diagnostico |
| `CAPABILITY_DENIED` | falta capacidad, trinquete de taint | no | reintentar es escalada |
| `BUDGET_EXHAUSTED` | | no | exige ampliacion explicita |
| `INDETERMINATE` | timeout tras enviar la peticion externa | **no** | envenena la clave y exige reconciliacion |

**Singleflight, redimensionado a lo que de verdad hace.** La version anterior de este diseno vendia
singleflight como la solucion a "veinte agentes compilando lo mismo". Es falso: veinte agentes en
veinte ramas CoW tienen veinte `workspace_tree_root` **distintos** (esa diferencia es justo el motivo
de que existan las ramas), asi que la clave nunca coincide y el hit rate para builds tiende a cero.
Ademas calcular el root Merkle de 500 000 archivos por invocacion no es gratis salvo que el
workspace lo mantenga incremental. Se conserva el mecanismo, con el alcance honesto:

- Es un **deduplicador de invocaciones identicas en vuelo**, no una cache de resultados. No hay
  persistencia: cuando la ejecucion termina, la entrada muere.
- Clave: `sha256(tool_id || args_digest || workspace_id || workspace_epoch || env_digest)`.
  Coalesce **solo dentro de la misma rama y la misma epoca**. Nunca entre ramas en fase 1.
- `env_digest` es el digest del entorno **declarado en el manifiesto**, ordenado canonicamente, no
  del entorno heredado. El sandbox construye `env` exclusivamente desde la declaracion: no hereda
  nada del proceso padre. Esto es a la vez la correccion de la clave y una correccion de seguridad
  (el `$HOME`, el `AWS_PROFILE` y el `PATH` del usuario no entran al sandbox).
- Requisitos para coalescer: `deterministic=True` declarado, `resource_class not in {NET,
  EXTERNAL_EFFECT}` y `effect_class <= REVERSIBLE`.
- Se elimina el muestreo del 1% con re-ejecucion: duplicar el coste de la operacion mas cara del
  sistema para verificar un mecanismo que ya no cruza ramas es un mal cambio. El riesgo residual
  ("dos invocaciones simultaneas, mismos args, misma epoca, mismo entorno declarado, resultados
  distintos") solo se materializa con no-determinismo real, que es exactamente lo que
  `deterministic=True` declara no tener.

La redundancia de compilacion entre ramas la resuelve la cache de compilador compartida (5.1), que
opera a nivel de unidad de compilacion y no depende de que dos comandos sean identicos.

**Idempotencia de efectos externos.** Protocolo de dos fases sobre un almacen linealizable:

```python
class EffectLedger(Protocol):
    async def reserve(self, target: str, key: str, spec_digest: str, ttl_s: int) -> Reservation: ...
    async def commit(self, key: str, outcome_ref: CasRef) -> None: ...
    async def abandon(self, key: str, reason: str) -> None: ...
    async def poison(self, key: str, evidence_ref: CasRef) -> None: ...          # -> indeterminate
    async def resolve(self, key: str, *, outcome: Literal["happened", "did_not_happen"],
                      principal: HumanPrincipal, note: str) -> None: ...
```

`reserve` es compare-and-set. Clave ya `committed` -> devuelve el resultado anterior sin ejecutar
(dedup real). `in_flight` con lease vivo -> `DUPLICATE_IN_FLIGHT`. `in_flight` con lease vencido ->
`INDETERMINATE`: no sabemos si el deploy salio, y **no se reintenta**. `key` es el
`idempotency_key` declarado por la herramienta o, en su defecto,
`sha256(tool_id || args_digest || workspace_epoch)`.

**`resolve` no es un adorno: es la unica salida del estado `indeterminate`.** El diseno anterior
decia "congela y exige reconciliacion humana" sin decir quien descongela ni con que API. Semantica
exacta de `indeterminate`: la invocacion pasa a terminal, la clave queda `poisoned`, el `target`
queda bloqueado para **todos** los agentes y **todos** los workspaces, el agente recibe un resultado
que dice "resultado desconocido, no reintentar" y sigue trabajando en lo demas, y sale una tarea de
reconciliacion al humano con el `evidence_ref` (peticion enviada, respuesta parcial, timestamps).
Solo `resolve`, firmado por un principal humano, libera el target.

**La unidad linealizable es `effect_target`, no el workspace.** El diseno anterior ponia el semaforo
en `(workspace, target)`: dos agentes en workspaces distintos desplegando al mismo entorno de
produccion corrian en paralelo, que es precisamente el accidente que este bloque existe para
impedir. `effect_target` es un identificador estable y global por tenant (`deploy:prod:api`,
`git:github.com/org/repo:refs/heads/main`) que declara el manifiesto y valida el kernel. Como efecto
lateral, particionar el ledger por target elimina el cuello de botella: un Durable Object por target
o un advisory lock de Postgres por hash del target, en vez de un unico punto serializado por
workspace.

### 5.4 Sandbox

| Nivel | Arranque | Aislamiento | Que corre ahi | Fase |
|---|---|---|---|---|
| **L0** `inkernel` | ~0.1 ms | **ninguno** | funciones puras de primera parte: diff, hash, parseo, busqueda en CAS | 1 |
| **L1** `restricted_proc` | 3-15 ms | uid dedicado + seatbelt (macOS) / seccomp+namespaces (Linux) + rlimits, **sin red** | git, grep, formatters, linters, tests del proyecto | 1 |
| **L2** `container` | 80-150 ms caliente, 600-1200 ms frio | namespaces, cgroup v2, rootfs propio, red solo via proxy | builds, instalacion de dependencias, codigo generado por el modelo, MCP de terceros, **todo lo que necesite red** | 1 |
| **L3** `microvm` | 125-350 ms | kernel propio (Firecracker / Virtualization.framework) | codigo no auditado en multi-tenant real | 3 |
| **L4** `isolate` | 3-8 ms | V8 isolate, sin FS ni proceso | evaluacion de politicas en el edge | 3 |

**L0 no es un nivel de aislamiento, es una declaracion de autoridad de kernel.** Corre en el proceso
del kernel, con su memoria y sus permisos. Por eso: solo codigo de primera parte firmado con la
clave de release, **ningun plugin puede solicitar L0 jamas**, y prohibicion dura de tocar FS del
proyecto o red. Llamarlo "sandbox nivel 0" invita a que alguien meta ahi un plugin "porque es
rapido".

**L3 y L4 salen de fase 1.** Firecracker no existe en macOS y Virtualization.framework exige
entitlements y un helper firmado; un isolate V8 en un monorepo Python es una segunda runtime entera
para un caso de uso que aun no existe. Se reservan los numeros de nivel y el hueco en el enum para
que anadirlos no sea una migracion, y se construyen cuando haya multi-tenant real o edge real.
Construirlos antes es complejidad que no se ha ganado su sitio.

**Asignacion de nivel: una sola funcion, no dos tablas.** El diseno anterior tenia "por procedencia
del codigo" sin decir de donde sale la procedencia. Sale del mismo sitio que todo lo demas: la
etiqueta de taint del blob de codigo (5.8).

```
level = max(
    L0 si first_party_signed y sin FS de proyecto y sin red, si no L1,
    L2 si taint(codigo) >= TOOL_OUTPUT,       # codigo escrito por el modelo o traido de red
    L2 si plugin no firmado o servidor MCP externo,
    L2 si capability incluye net:*,           # en fase 1 no hay red en L1
)
```

Subir es automatico. **Bajar exige la capacidad `sandbox:downgrade`**, que no es delegable a un
agente. Si no hay backend disponible para el nivel requerido se rechaza con `SANDBOX_UNAVAILABLE`:
jamas se ejecuta sin aislamiento "porque no habia otra".

**Instalacion sin Docker.** `SANDBOX_UNAVAILABLE` no puede significar "producto inutil recien
instalado". Sin runtime de contenedores, L0 y L1 siguen disponibles y cubren la mayoria del trabajo
real (leer, buscar, editar, git, tests sin red). Lo unico que se bloquea es "codigo nuevo o con red".
`forge doctor` imprime al instalar exactamente que niveles existen y que se puede hacer con cada uno,
y `available()` devuelve una matriz tipada de **que impone frente a que aconseja**:

```python
@dataclass(frozen=True)
class Availability:
    usable: bool
    enforced: frozenset[Enforcement]   # FS_CONFINEMENT, NET_DENY, MEM_LIMIT, PID_LIMIT, CPU_LIMIT
    advisory: frozenset[Enforcement]
    reason: str | None
```

Regla dura de admision: una capacidad que **ningun backend disponible sabe imponer** se deniega; no
se degrada a "confiamos". El estado se journalea al arrancar como `exec.sandbox.capabilities_probed`.

**Red: deny-all con un unico camino.** Toda salida pasa por `forge-egress`, proxy local del plano de
control que exige capacidad por conexion, **resuelve DNS el mismo** (mata el rebinding), aplica
allowlist por host+puerto+esquema y registra `sec.egress.allowed|denied`. En L2 la unica interfaz con
ruta es la del proxy. **En fase 1, L1 no tiene red en absoluto**: sin ruta, sin proxy, sin
excepciones. Toda herramienta con `net:*` se despacha a L2. Esto elimina de la instalacion el paso de
pedir privilegio de administrador para un ancla `pf`, que es friccion enorme el primer dia a cambio
de una optimizacion de latencia. El ancla `pf` (`block drop out proto {tcp,udp} user _forge_exec` con
`pass` solo al loopback del proxy) es **fase 2**, opcional, y su unico efecto es permitir que
herramientas de red baratas y de confianza bajen de L2 a L1. Si el usuario no la concede, no pasa
nada: el sistema ya funcionaba sin ella. Nunca se finge un aislamiento que no existe.

**FS expuesto:** `/workspace` (rama CoW, rw), `/cas` (ro, solo los refs de la capacidad), `/tmp`
(tmpfs 512 MiB volatil), toolchain ro, cache de compilador rw compartida. Nada del `$HOME` del
usuario: ni `~/.ssh`, ni `~/.aws`, ni `~/Library`, ni `~/.gitconfig`. En macOS lo impone el uid
dedicado `_forge_exec` (defensa dura) mas el perfil seatbelt (defensa en profundidad). El entorno se
construye desde cero: no se hereda ni una variable.

**Limites default por invocacion:** 2 nucleos, 2 GiB RAM (`memory.max`; el OOM mata el cgroup entero,
no un proceso), 512 PIDs, 1 GiB de escritura, 4 096 descriptores, 300 s de pared, 64 MiB de salida
capturada. Solo suben con capacidad explicita.

**macOS, que es el entorno principal.** L1 = `sandbox-exec` (SBPL) + uid dedicado + `setrlimit` +
`taskpolicy`. Limitaciones asumidas por escrito: SBPL esta deprecado y sin API publica estable; no
hay cgroups, asi que el limite de memoria es `RLIMIT_AS`, aproximado y ciego al mmap compartido; no
hay PID namespace, asi que el reaper por pgid con verificacion de `pid_start_time` es la unica red
bajo el trapecio. Consecuencia: nada que sea codigo nuevo o con red corre en L1 en macOS.

```python
class SandboxProvider(Protocol):
    level: SandboxLevel
    async def available(self) -> Availability: ...
    async def spawn(self, spec: SandboxSpec) -> SandboxHandle: ...

@dataclass(frozen=True)
class SandboxSpec:
    level: SandboxLevel
    argv: tuple[str, ...]
    cwd: PurePosixPath
    mounts: tuple[Mount, ...]
    env: Mapping[str, str]              # construido desde el manifiesto, sin herencia, sin SecretRef
    secrets: tuple[SecretMount, ...]    # por fd o tmpfs 0400, NUNCA en argv, NUNCA en env si hay alternativa
    limits: ResourceLimits
    net: NetPolicy                      # NONE | PROXY(allowlist)
    lease_ttl_s: int = 30
```

### 5.5 Modelo de permisos

```
capability := resource ":" verb ("@" scope)? ("?" condition)* ("!" expiry)
resource   := fs | net | proc | secret | tool | budget | journal | sandbox
```

Ejemplos: `fs:write@/workspace/apps/api/**?max_bytes=10485760!2026-07-27T18:00Z`,
`net:connect@https://api.github.com:443?method=GET&max_body_bytes=0`, `secret:use@github_pat`,
`tool:invoke@build.cargo`.

Formato: macaroon. Ed25519 sobre el root grant, cadena HMAC-SHA256 de caveats para **atenuacion
offline** sin round-trip al emisor. Reglas duras:

- **Monotonia.** Un caveat solo restringe. La verificacion es interseccion; el ABI no expone ninguna
  operacion de ampliacion.
- **Delegacion.** `delegate(child_principal, caveats)` produce `depth+1`, con `expiry <= padre` y
  `scope subset padre`. `max_depth = 4`.
- **Anti-diputado-confundido.** El token lleva `audience = agent_id`; el supervisor rechaza tokens
  con audiencia distinta al principal que ejecuta. Un agente no presta su token: delega creando uno
  nuevo, atenuado y auditado (`sec.capability.delegated`).
- **Revocacion.** Conjunto local por `root_grant_id` con `revocation_epoch` monotona, empujada al
  runtime y consultada en admision (nunca en cada syscall), mas `expiry` corto: 15 min para tokens de
  invocacion, renovables. Ventana efectiva de revocacion sin conexion: <= 15 min.
- **El presupuesto es una capacidad**: `budget:spend@usd?limit=250000` (micros), descontado en el
  ledger, no en memoria de proceso.
- **Custodia de la clave raiz.** En self-host, la clave Ed25519 de root vive en el Keychain de macOS
  (o fichero 0600 del usuario) y **nunca es legible por el uid `_forge_exec`**. El proceso del plano
  de control no corre como `_forge_exec`. En hosted, en KMS. Sin esta separacion, un escape de L1
  fabrica sus propias capacidades y todo el capitulo es decorativo.

**Presupuestos con contabilidad real.** El diseno anterior tenia un campo `Budget` sin decir quien lo
descuenta ni que pasa cuando un sandbox muere a medias. Es hold/settle jerarquico:

```python
class BudgetLedger(Protocol):
    async def hold(self, scope: BudgetScope, amount: Budget) -> Hold: ...   # en admision
    async def settle(self, hold_id: ULID, actual: Budget) -> None: ...      # en settlement
    async def release(self, hold_id: ULID, reason: str) -> None: ...        # reaper / cancelacion
```

Cadena `tenant -> session -> agent -> invocation`: un hold consume del padre. Una invocacion
huerfana libera su hold en el reaper; sin eso, veinte agentes con cortes de sandbox agotan el
presupuesto nominal en horas y el sistema se para sin haber gastado un dolar real.

### 5.6 Aprobaciones

`EffectClass` la declara el manifiesto y la **verifica el kernel** contra las capacidades solicitadas.
La declaracion puede subir la clase real, nunca bajarla:

```
effect_class = max(declarada,
                   derivada de las capacidades solicitadas,
                   max(effect_class de todo tool_id en may_invoke))
```

`may_invoke` es campo obligatorio del manifiesto para cualquier herramienta que pueda invocar otras
(un agente-como-herramienta, un servidor MCP que hace de proxy). El valor `ANY` fuerza la clase
maxima de todo el conjunto de capacidades. Esto cierra estructuralmente el hueco de composicion: un
`EXTERNAL_EFFECT` alcanzado a traves de una herramienta etiquetada `REVERSIBLE` deja de ser posible
por construccion, no por vigilancia.

| Clase | Definicion | Asistido | Desatendido |
|---|---|---|---|
| `SAFE` | sin escritura, sin red, sin efecto | auto | auto |
| `REVERSIBLE` | escritura en el workspace CoW (revertible por snapshot) | auto | auto con presupuesto |
| `DESTRUCTIVE` | perdida local recuperable solo por snapshot (`rm -rf`, `reset --hard`) | pregunta | difiere |
| `IRREVERSIBLE` | sin deshacer (force push, drop table, borrado en la nube) | pregunta siempre | **niega** |
| `EXTERNAL_EFFECT` | observable por terceros (deploy, email, pago, publicacion) | pregunta siempre | **niega** |

**Por que el agente jamas se aprueba a si mismo.** No es una regla de comportamiento, es una ausencia
estructural: `approval:grant` no existe en el espacio de capacidades delegables a un agente. La
aprobacion es un objeto distinto, `ApprovalGrant`, firmado por una clave del plano de control que
solo se activa con la accion de un principal humano autenticado.

**El digest no basta: hacen falta precondiciones.** `decision_digest =
sha256(tool_id || args_digest || caps_digest || workspace_epoch || effect_class)` cierra el TOCTOU de
los argumentos: si el agente cambia un byte tras la aprobacion, el gate se vuelve a cerrar. Pero no
cierra el TOCTOU del **mundo**: se aprueba `git push` a `main` y, entre la aprobacion y el despacho,
otro agente mueve el tip de la rama o el remoto avanza. El digest sigue casando y se empuja algo que
el humano nunca vio.

```python
@dataclass(frozen=True)
class ApprovalGrant:
    grant_id: ULID
    decision_digest: str
    preconditions: tuple[Precondition, ...]   # (nombre, valor esperado) verificado al despachar
    scope: CapabilityScope
    uses: int
    expiry: datetime
    principal: HumanPrincipal
    signature: bytes                          # Ed25519 del plano de control
```

Las `preconditions` (por ejemplo `git.branch_tip=<sha>`, `git.remote_ref=<sha>`,
`workspace.epoch=<n>`) se re-evaluan **inmediatamente antes del despacho, dentro del mismo lease que
la reserva del `EffectLedger`**. Cualquier discrepancia reabre el gate con
`PRECONDITION_CHANGED` y le dice al humano exactamente que cambio. Sin esto, la aprobacion protege
los argumentos y deja el estado del mundo al aire.

**Aprobacion parqueada y capacidad expirada.** Un token de invocacion vive 15 min; una invocacion
parqueada espera hasta 8 h. El parqueo guarda la **peticion**, no el token. Al aprobar, el plano de
control acuna un token fresco ligado al mismo `decision_digest`, con los mismos caveats o mas
estrechos, tras re-verificar revocacion y que el principal humano sigue autorizado. Un token no
sobrevive a su expiry por estar esperando: es la clase de detalle que, sin escribir, produce o bien
tokens eternos o bien aprobaciones que fallan al concederse.

**Lotes y permanentes.** `ApprovalGrant` admite `scope`, `uses` y `expiry`. Un permanente legitimo es
`effect_class<=DESTRUCTIVE @/workspace/** !+30d`. El kernel **rechaza** grants con `resource=*` y
`effect_class>=IRREVERSIBLE`: no se puede firmar un cheque en blanco irreversible ni queriendo.

**Las tres de la madrugada.** `unattended` es una politica explicita con ventana horaria, presupuesto
tope y allowlist de clases. Lo que no esta permitido no se aprueba solo: se **aparca**. La invocacion
pasa a `approval_pending`, el motor fija el `workspace_epoch`, el plan continua por otra rama si la
hay, y sale una notificacion durable al movil **con acuse**: si el push no se entrega en 60 s, cae a
notificacion local de escritorio y queda registrado `approval.notify.failed`. Si nadie contesta antes
de `approval_deadline` (default 8 h) el estado es `denied_by_timeout` con el trabajo intacto y
reanudable. El coste de una aprobacion tardia es tiempo; el de una automatica de un force-push es una
semana de trabajo.

### 5.7 Secretos

**La boveda existente no sirve tal cual.** `packages/db/edecan_db/vault.py` cifra `TokenBundle` de
OAuth por `(tenant_id, connector_account_id)`, exige una `AsyncSession` de Postgres con RLS activa y
vive en el paquete de base de datos. Reutilizarla directamente en `forge-vault` habria arrastrado
SQLAlchemy y Postgres al plano de datos (invariante 3 y 10) y habria dejado el runtime local sin
secretos cuando no hay Postgres, que es el caso de una instalacion de escritorio. Se conserva lo que
vale — el esquema de cifrado envolvente y el `KeyProvider` (local Fernet / KMS) — detras de una
interfaz nueva:

```python
class SecretStore(Protocol):
    async def get(self, ref: SecretRef) -> SecretMaterial: ...
    async def versions(self, scope: str, name: str) -> tuple[int, ...]: ...

class SecretBroker(Protocol):
    """Unico punto que ve texto claro. Vive en el plano de control."""
    async def materialize(self, refs: Sequence[SecretRef], *, invocation_id: ULID,
                          capability: CapabilityToken) -> SecretMaterialization: ...
```

Implementaciones: `PostgresSecretStore` (envuelve el vault existente, hosted), `KeychainSecretStore`
(macOS, self-host sin Postgres), `EnvFileSecretStore` (dev). `SecretRef =
"secret://<scope>/<name>#<version>"`. El **valor jamas** entra al contexto del modelo, ni a los args,
ni al journal, ni al CAS. El modelo escribe la referencia opaca; el supervisor materializa.

**Inyeccion**, por orden de preferencia: (1) el secreto **no entra al sandbox en absoluto** y lo pone
el proxy de egress en la cabecera saliente (`sec.egress.credential_injected`) — es la unica opcion
que no tiene modo de fuga; (2) fichero en tmpfs `0400` cuya ruta va por entorno; (3) fd heredado;
(4) variable de entorno. **Nunca argv**, legible por cualquier proceso via `ps`.

**Redaccion: defensa en profundidad, y dicho como tal.** `RedactionFilter` corre en linea **entre el
pty y el escritor de CAS**, con automaton Aho-Corasick sobre los valores montados en esa invocacion y
sus derivadas (base64, base64 con saltos cada 76 bytes, hex, url-encode, escape JSON). El orden
importa mas que el algoritmo: el CAS es inmutable, asi que redactar despues no es redactar, es dejar
de mostrar. Reemplazo: `«redactado:sha256:ab12…»`, correlacionable sin revelar. Tres detalles sin los
cuales el filtro es teatro:

- **Ventana de arrastre.** El automaton conserva `max_secret_len - 1` bytes entre segmentos. Sin eso,
  un secreto partido por el limite de 256 KiB pasa entero. Es el bug mas facil de cometer aqui.
- **Longitud minima registrable.** Un secreto de menos de 16 bytes o con entropia baja **no se acepta
  como redactable**: coincidiria por todas partes y destruiria la salida. Un secreto asi solo puede
  montarse por via de egress (opcion 1); si la herramienta necesita verlo dentro del sandbox, se
  rechaza y se pide rotarlo por uno decente.
- **Limites reconocidos.** El filtro no ve `gzip`, no ve `openssl enc`, no ve un binario compilado, no
  ve el secreto impreso caracter por linea. **La redaccion no es el control primario**; el control
  primario es que el secreto no este dentro. Documentado como mitigacion, no como garantia.

**Fuga contenida frente a fuga escapada.** El diseno anterior revocaba capacidades ante cualquier
`sec.leak.detected`. Eso convierte un `npm config ls` que imprime `_authToken` en un incidente de
produccion, y le regala a un atacante un interruptor de apagado: basta lograr que el agente imprima
la cadena para revocar la credencial de despliegue del workspace. Se separan dos casos:

- `sec.leak.contained` — detectado y redactado **antes** de escribir a CAS. El secreto nunca cruzo la
  frontera. Consecuencia: contador, senal al humano, **ninguna revocacion**.
- `sec.leak.escaped` — detectado en el cuerpo saliente del proxy hacia un destino, en un fichero del
  workspace que entro al CAS, o en el diff de un `git:push`. Consecuencia: se corta la conexion, el
  secreto pasa a `suspect`, se revocan las capacidades `secret:use` que lo referencian, la invocacion
  queda contaminada y se abre tarea de rotacion.

**Un agente imprime la clave en stdout**: es `contained`, sin drama, porque el modelo nunca vio el
valor y el filtro lo tapa antes del CAS. El hueco real es el secreto escrito a un fichero del
workspace y luego commiteado; por eso `git:push` es `EXTERNAL_EFFECT` con aprobacion obligatoria y
escaneo de entropia y patrones sobre el diff antes del gate.

**Rotacion.** `SecretRef` versionado: rotar publica `#v+1` y deja `#v` valido 24 h para invocaciones
en vuelo, luego se destruye.

### 5.8 Inyeccion de prompt

El modelo de amenaza no es un usuario escribiendo "ignora tus instrucciones". Es un atacante que
escribe en algo que el agente **va a leer**: un README, un comentario de issue, la salida de `npm
install`, una respuesta HTTP, un nombre de fichero, un mensaje de commit. Su objetivo es que el
agente use sus capacidades legitimas para exfiltrar o destruir. Con un modelo debil, la probabilidad
de que la instruccion inyectada funcione es **alta**; el diseno no puede depender de que el modelo
resista.

1. **Etiquetado de origen.** Todo blob que entra al contexto lleva `TaintLabel` en su metadata de CAS:
   `SYSTEM > OPERATOR > USER > WORKSPACE_CODE > TOOL_OUTPUT > NETWORK`. La salida de una herramienta
   hereda la union de las etiquetas de sus entradas.
2. **Trinquete de capacidad, con alcance de sesion y no de turno.** El diseno anterior congelaba las
   capacidades "dentro de un turno". Es evadible de forma trivial: la inyeccion le dice al agente que
   termine el turno y actue en el siguiente, o escribe la carga en un fichero del workspace que se
   lee mas tarde con capacidades frescas. El trinquete es sobre el **estado de contexto de la
   sesion**, con marca de agua alta:

   ```
   taint_hwm(session) = max(taint de todo blob ingerido desde el ultimo reset)
   caps_efectivas     = caps_del_token  ∩  techo(taint_hwm)
   effect_class_max   = min(effect_class_max_previo, techo_efecto(taint_hwm))
   ```

   Es monotono no creciente hasta un **reset explicito**, que solo ocurre de dos formas, ambas
   journaleadas como `sec.taint.reset`: una compactacion de contexto que **descarta** los blobs
   contaminados, o una autorizacion humana explicita. "El README dice que hagas curl a evil.com con
   tu token" se convierte en `CAPABILITY_DENIED`, no en una negociacion con el modelo.
3. **El canal de exfiltracion real es el host permitido.** Denegar cuerpos hacia hosts *no aprobados*
   no defiende de nada: `github.com` esta en la allowlist y admite gists. Por eso `net:connect` lleva
   caveats obligatorios `methods` y `max_body_bytes`, y el trinquete pone `max_body_bytes=0` para
   metodos con cuerpo en cuanto `taint_hwm >= TOOL_OUTPUT`. Subir de ahi exige aprobacion humana con
   el destino a la vista. Ademas el proxy limita la longitud de querystring hacia hosts nuevos.
4. **Encapsulado sintactico.** El contenido no confiable llega al modelo dentro de un sobre con
   delimitador aleatorio por turno. Es mitigacion de segundo orden y se documenta como tal: sube el
   coste del ataque, no lo elimina, y con un modelo debil sube poco.
5. **Deteccion de patrones**: emite `sec.injection.suspected` como senal para el humano y el journal.
   **Nunca** decide acceso. Ningun clasificador tiene la tasa de falsos negativos que exige un
   control de seguridad, y su tasa de falsos positivos rompe el producto.

### 5.9 Auditoria

Cada evento lleva `seq` monotonico por sesion y `prev_hash`; `h_n = sha256(h_{n-1} ||
canonical_cbor(event_n))`. Cada 256 eventos o 60 s se emite `audit.checkpoint {seq, root, signature}`
firmado con Ed25519 (KMS en hosted, Keychain local en self-host).

**La cadena es un DAG, no una linea.** Con workspaces CoW y fork de sesion (invariantes 4 y 5), dos
sesiones comparten prefijo. El primer evento de una sesion bifurcada lleva
`forked_from = {session_id, seq, hash}` y su `seq` reinicia en 0. Sin esto, o el fork rompe la
verificacion de completitud o se copian eventos y la cadena miente sobre quien hizo que. Verificar
una sesion bifurcada es verificar su cadena mas la cadena del padre hasta el punto de fork.

Se registra: admision y rechazo con motivo; cada decision de capacidad con el id del token y los
caveats evaluados; solicitud, concesion, denegacion, expiracion y **precondiciones evaluadas** de
cada aprobacion, con el principal humano y el `decision_digest`; spawn de sandbox con el hash de su
spec exacta y la `Availability` del backend; segmentos de salida ya redactados; settlement con exit
code y coste real; cada uso de secreto (ref y version, jamas valor); cada decision de egress; cada
`resolve` de un efecto indeterminado.

Lectura: `journal:read@session/<id>` es una capacidad. Un agente lee su propia sesion, pero **no** los
eventos `sec.*` de otras sesiones ni la cadena de aprobaciones ajena. El operador humano lo lee todo.
Retencion: eventos completos 90 dias, cadena y checkpoints 7 anos, blobs de salida en CAS con GC a
7 dias para invocaciones `succeeded` y 30 dias para las fallidas (el hash sobrevive, asi que la
ausencia del blob es detectable y la cadena sigue verificando).

**Demostrar que un agente NO hizo algo**: la cadena, mas la contiguedad de `seq`, mas el checkpoint
firmado, mas la arista `forked_from`, dan completitud. No se puede borrar un evento sin romper la
cadena desde ese punto, ni reescribirlo sin la clave de firma. Un hueco es prueba de manipulacion, no
ausencia de prueba.

### Fases

| Fase | Alcance |
|---|---|
| **1** | `ExecutionEngine` con las tres etapas, `AuthzFacts`, lanes y semaforos, L0/L1/L2, macaroons con revocacion local, `ApprovalGrant` con precondiciones, secretos por egress y tmpfs, `RedactionFilter` con arrastre, `EffectLedger` particionado por target con `resolve`, `BudgetLedger`, reaper con `pid_start_time`, cadena de auditoria con `forked_from` |
| **2** | Ancla `pf` opcional (L1 con red), singleflight en vuelo, cache de compilador compartida, reclasificacion pegajosa de `ResourceClass`, escaneo de entropia en diffs, aging por lane |
| **3** | L3 microvm, L4 isolate, traduccion de la gramatica de capacidades a Cedar, coalescencia entre ramas con verificacion, multi-tenant real |

### Alternativas descartadas

| Alternativa | Por que se descarta | Coste de habernos equivocado |
|---|---|---|
| Pool unico de workers con prioridad numerica | Una compilacion de 90 s bloquea un `read_file` de 3 ms; el agente parece colgado | Bajo: se reajustan pesos, `ResourceClass` ya existe en el manifiesto |
| Temporal / Celery como scheduler | Dependencia pesada, no portable a Workers, con modelo de estado propio que competiria con el journal | Medio: reimplementar durabilidad que ya venia hecha |
| Singleflight persistente como cache de builds entre ramas | Veinte ramas CoW tienen veinte roots distintos: hit rate ~0 para builds, y una clave incompleta devuelve un build ajeno. La redundancia real esta a nivel de unidad de compilacion | Alto y silencioso: el agente razona horas sobre una realidad falsa |
| Muestreo del 1% con re-ejecucion para validar coalescencias | Duplica el coste de la operacion mas cara para verificar un mecanismo ya acotado a misma rama y misma epoca | Bajo |
| `args` completos inline en el `InvocationSpec` | El journal engorda con payloads y viola la invariante 4 | Medio: migracion del esquema de eventos |
| Leer los args del CAS durante la admision | I/O en el camino caliente: los 5 ms p99 se vuelven 50 | Medio: latencia percibida en cada tool call |
| At-least-once con reintento para efectos externos | Casi ninguna API de despliegue es idempotente de verdad; el reintento ciego de un deploy ambiguo es el fallo mas caro del sistema | Muy alto e irreversible: despliegue duplicado en produccion |
| Semaforo de efecto por `(workspace, target)` | Dos agentes en workspaces distintos despliegan al mismo entorno a la vez | Alto: exactamente el accidente que este bloque existe para evitar |
| Ledger linealizable global por workspace | Un unico punto serializado; un DO single-thread o un `FOR UPDATE` largo bloquea toda la cola `EXTERNAL_EFFECT` | Medio: cuello de botella evitable particionando por target |
| Contenedor siempre, para todo | +80-1200 ms sobre operaciones de 3 ms y una VM permanente en macOS; el agente se vuelve inutilizable en el bucle interactivo | Bajo en seguridad, alto en producto |
| Sin sandbox en la maquina del usuario ("es su maquina") | Una inyeccion en un README llega directo a `~/.ssh` y `~/.aws`. La maquina del usuario es donde estan las credenciales que importan | Catastrofico e irreversible |
| Nivel de sandbox elegido por el modelo | Es entregarle la politica de seguridad al atacante que controla el contexto | Catastrofico |
| L3/L4 en fase 1 | Firecracker no corre en macOS, Virtualization.framework exige entitlements y helper firmado, y un isolate V8 es una segunda runtime entera para un caso que aun no existe | Bajo: los numeros de nivel quedan reservados |
| Ancla `pf` obligatoria el primer dia | Exige privilegio de administrador en la instalacion a cambio de una optimizacion de latencia; sin ella, L1 sin red ya es correcto | Bajo, y en la direccion segura |
| BLAKE3 para los digests de seguridad | Exige WASM y paridad byte a byte para verificar en el edge; SHA-256 es nativo en WebCrypto y en `hashlib` | Medio: dependencia obligatoria y verificacion no portable |
| Reutilizar `edecan_db.vault` directamente como boveda de Forge | Arrastra SQLAlchemy, Postgres y RLS al plano de datos y deja sin secretos la instalacion local sin Postgres | Alto: acoplamiento que se paga en cada despliegue self-host |
| ACL central consultada en cada operacion | Round-trip en el camino caliente y ruptura de la ejecucion offline en local | Medio: latencia y fragilidad |
| JWT con scopes planos en vez de macaroons | No se puede atenuar sin volver al emisor, que es justo lo que exige la delegacion entre agentes | Medio: rediseno del modelo de delegacion |
| OPA / Cedar desde el dia uno | Mejor lenguaje de politica, pero no aporta atenuacion offline y anade una dependencia grande antes de tener casos reales | Bajo: la gramatica es traducible a Cedar en fase 3 |
| Agente supervisor que aprueba a los trabajadores | Comparte el mismo canal de entrada contaminable: fallo correlacionado, no defensa en profundidad | Alto: falsa sensacion de control humano |
| Aprobacion ligada solo a los argumentos | Cierra el TOCTOU de los args y deja abierto el del mundo: el tip de la rama cambia entre aprobacion y despacho | Alto: se empuja algo que el humano nunca vio |
| Trinquete de taint con alcance de turno | Se evade terminando el turno, o escribiendo la carga en un fichero que se lee en el siguiente | Alto: la defensa principal contra inyeccion queda inutil |
| Revocar el secreto ante cualquier deteccion de fuga | Convierte un `npm config ls` en un incidente y regala al atacante un interruptor de apagado de la credencial | Medio-alto: auto-denegacion de servicio |
| Clasificador de inyeccion como control de acceso | Falsos negativos inaceptables para un control de seguridad y falsos positivos que rompen el producto | Alto: seguridad de teatro |
| Redactar secretos al leer el journal | El CAS es inmutable: si el secreto llego, ya esta ahi para siempre, firmado y replicado | Muy alto e irreversible |
| Auto-aprobar de madrugada con presupuesto tope | El presupuesto acota el coste economico, no el semantico: un force-push de 0 USD destruye una semana | Alto |
| Devolver la salida completa de la herramienta al modelo | Con contexto modesto, un `cargo build` verboso termina la sesion | Alto: el producto no funciona con el modelo base |

### Riesgos aceptados

- **Ventana de revocacion de 15 minutos.** Un token robado sigue siendo valido hasta su expiry si la
  maquina esta desconectada. Se acepta a cambio de autorizacion offline sin round-trip. Se acorta
  bajando el expiry, al precio de mas renovaciones.
- **SBPL deprecado en macOS.** L1 depende de una API sin garantias de Apple. La defensa que queda si
  cae es el uid dedicado, que es real pero mas gruesa. Si Apple retira `sandbox-exec`, todo lo que
  hoy corre en L1 sube a L2 y la latencia interactiva empeora.
- **`RLIMIT_AS` en lugar de `memory.max` en L1 macOS.** El limite de memoria es aproximado y ciego al
  mmap compartido; una herramienta puede superar su cuota nominal sin que el sistema lo note.
- **La redaccion no cubre transformaciones arbitrarias.** `gzip`, cifrado, un binario compilado o
  imprimir el secreto caracter por linea la evaden. Se acepta porque el control primario es que el
  secreto no este dentro del sandbox; la redaccion es la segunda linea.
- **Secreto compilado dentro de un artefacto.** El filtro ve stdout, no un bundle `.js`. Solo lo tapa
  el escaneo del diff en `git:push` y la aprobacion obligatoria de `EXTERNAL_EFFECT`.
- **Sin runtime de contenedores no hay codigo nuevo ni red.** Instalacion degradada pero util y
  honesta, en vez de ejecucion sin aislamiento.
- **Falsos `indeterminate`.** Un lease del ledger corto frente a un deploy lento produce despliegues
  congelados que en realidad salieron bien. Es friccion, no dano; se calibra por target.
- **L1 sin red en fase 1 empuja a L2 herramientas baratas.** Un `curl` de 20 ms paga 100-1200 ms de
  contenedor hasta que exista el ancla `pf`. Coste de latencia asumido a cambio de una instalacion sin
  privilegios de administrador.

### Como se rompe

- **Reuso de PID en el reaper.** Si se omite `pid_start_time`, el reaper mata un proceso reciclado del
  usuario. Perdida de datos silenciosa, fuera del sistema, atribuida a "el ordenador".
- **Sesgo de reloj y leases.** Si el reloj del supervisor salta hacia adelante, leases vivos parecen
  vencidos y el reaper mata trabajo en curso. Por eso los leases usan reloj monotonico local.
- **Canonicalizacion divergente de argumentos.** Dos implementadores con dos JCS distintos producen
  `decision_digest` distintos: todas las aprobaciones fallan y el sistema parece roto sin que ninguna
  prueba unitaria lo detecte.
- **`AuthzFacts` incompleto.** Si el adaptador de una herramienta omite una ruta que la herramienta
  realmente escribe, la autorizacion aprueba algo que no vio. El sandbox lo detiene igualmente
  (defensa dura), pero la divergencia solo aparece como `sec.authz.drift` si alguien la mira.
- **Fuga por host permitido.** El trinquete pone `max_body_bytes=0`, pero un GET a un host aprobado con
  el secreto en la ruta sigue siendo un canal. Lo acota el limite de querystring y el escaneo del
  proxy, no lo elimina.
- **Contencion en el ledger de efectos por target caliente.** Particionar por target quita el cuello de
  botella global, pero un unico target de despliegue con veinte agentes queda serializado por
  definicion. Es la semantica correcta y a la vez el limite de throughput.
- **Agotamiento del pool caliente de contenedores.** Con veinte agentes y arranque frio de 1,2 s, la
  cola `CPU_HEAVY` se llena de spawns y la latencia se dispara sin que ninguna metrica de CPU lo
  explique. Sintoma: `time_in_queued` alto con `load1` bajo.
- **Fatiga de aprobacion.** El fallo real del gate humano no es denegar mal: es que a la trigesima
  pregunta el usuario firme un permanente demasiado ancho. La unica defensa es que el boton peligroso
  no exista, de ahi el rechazo de `resource=*` con clase irreversible.
- **Bucle del modelo debil que el rompe-bucles no ve.** `REPEATED_FAILURE` compara `args_digest`; un
  modelo que varia un espacio en cada intento escapa a la deteccion y quema presupuesto igual. El
  techo de presupuesto por sesion es la ultima red.
- **Reset de taint demasiado facil.** Si la compactacion de contexto se dispara sola, se convierte en
  un lavado de contaminacion automatico y el trinquete deja de valer. El reset debe descartar los
  blobs, no solo resumirlos.
- **Amplificacion de escritura en el CAS.** Un build verboso de 40 MB, por veinte agentes, son 800 MB
  de segmentos por ronda. Sin `bytes_out`, GC a 7 dias y deduplicacion, el disco se llena en dias.
- **Escape de seatbelt en macOS.** Si cae, la unica capa restante es el uid dedicado; el material de
  firma del plano de control queda a salvo solo porque `_forge_exec` no puede leerlo. Si algun dia el
  plano de control corre bajo ese uid "para simplificar", todo el modelo de capacidades se derrumba en
  una sola linea de configuracion.

---

## 6. Agent Runtime, planificacion y multi-agente

Paquete: `packages/forge-runtime/edecan_forge_runtime/`. No importa ningun modulo del plano de
datos. Recibe por inyeccion `JournalClient`, `CasStore`, `ToolPlane`, `LLMProvider`,
`WorkspaceManager` y `CapabilityIssuer` (invariante 10). Todo lo que aqui se llama "estado" es un
pliegue (`fold`) de eventos del journal; ninguna estructura de este bloque es autoritativa por si
misma (invariante 2).

Tres reglas transversales que gobiernan el resto de la seccion:

1. **Un unico escritor logico por stream.** Cada `agent_id` tiene un stream de journal y una cola
   serializada de comandos. El bucle, el watchdog, el governor y el supervisor **no escriben**: le
   encolan `AgentSignal`. El fencing por `epoch` protege entre hosts; el escritor unico protege
   dentro del host. El original solo cubria el primero.
2. **Append condicional, no journal con dominio.** El journal expone
   `append(stream_id, events, expected_seq, epoch) -> seq | SeqConflict | EpochFenced`. No sabe que
   es un agente. `transition_guard(prev_state, event) -> Verdict` es una **funcion pura del
   runtime** que se evalua antes del append; la atomicidad la da el `expected_seq`. Meter la maquina
   de estados dentro del journal habria acoplado el plano de control con el dominio del runtime
   (invariante 10).
3. **Limite duro de 32 KB por evento.** La capa de serializacion sustituye automaticamente por
   referencia CAS cualquier campo que lo exceda. Es una regla de codificacion, no de disciplina: un
   `tool_call` con el contenido completo de un archivo de 5 MB no puede llegar al journal ni por
   error.

### 6.1 Maquina de estados, lease y fencing

Once estados. `verifying` existe porque es donde se decide `completed`, y tiene presupuesto,
capacidades y modos de fallo propios; sin el, "terminado" seria autocertificacion del modelo.

```
created → planning → executing ⇄ awaiting_tool → verifying → completed
                         ↓            ↓         ⇅    ↓
                  awaiting_approval  blocked  (tools) planning (replan)
                         ↓
        paused / suspended / failed / cancelled
```

| Desde | Hacia | Disparador | Evento |
|---|---|---|---|
| `created` | `planning` | supervisor concede lease + `CapabilityToken` | `agent.admitted` |
| `planning` | `executing` | plan v1 valido (DAG aciclico, todo objetivo con >=1 criterio) | `plan.published` |
| `planning` | `failed` | plan infactible con evidencia | `agent.failed` |
| `executing` | `awaiting_tool` | el modelo emite >=1 `tool_call` | `tool.requested` |
| `awaiting_tool` | `executing` | todos los `tool.settled` en journal | `turn.continued` |
| `awaiting_tool` | `awaiting_approval` | la herramienta excede el alcance del token | `approval.requested(reason="capability")` |
| `awaiting_approval` | `awaiting_tool` | humano aprueba | `approval.granted` |
| `awaiting_approval` | `executing` | humano deniega (entra como `tool_result` tipado) | `approval.denied` |
| `executing` | `verifying` | `stop_reason == "end"` sin tool calls | `turn.completed` |
| `verifying` | `awaiting_tool` | criterio `command`/`artifact_exists` a ejecutar | `tool.requested(purpose="verification")` |
| `verifying` | `completed` | todos los criterios `required` en PASS | `agent.completed` |
| `verifying` | `planning` | >=1 criterio FAIL y quedan revisiones | `plan.revised` |
| `verifying` | `awaiting_approval` | criterio `kind="human"` o criterio `tainted` | `approval.requested(reason="verification")` |
| `verifying` | `blocked` | criterio en ERROR dos veces (verificador roto, no codigo roto) | `agent.blocked(reason="verifier_broken")` |
| `executing`/`awaiting_tool` | `blocked` | dependencia no satisfecha, claim en conflicto, rate limit largo | `agent.blocked(reason)` |
| `blocked` | `executing` | dependencia satisfecha o `unblock_deadline` | `agent.unblocked` |
| `blocked` | `failed` | deadlock, victima elegida | `agent.failed(reason="deadlock_victim")` |
| cualquiera no terminal | `paused` | humano | `agent.paused` |
| `paused` | `suspended` | ocioso > 300 s | `agent.suspended(reason="idle")` |
| cualquiera no terminal | `suspended` | lease perdido, drain de despliegue, host muerto | `agent.suspended(reason)` |
| `suspended` | estado previo | supervisor re-admite con `epoch+1` | `agent.resumed` |
| cualquiera no terminal | `failed` | presupuesto agotado con `on_exhaustion="fail"`, stall irrecuperable | `agent.failed` |
| cualquiera no terminal | `cancelled` | humano, supervisor, cancelacion transitiva del padre | `agent.cancelled` |

Disparadores validos: `supervisor`, `host`, `human`, `budget_governor`, `watchdog`. Ningun otro
actor puede proponer transiciones. Una transicion ilegal se rechaza con `IllegalTransition`; no se
ignora.

**Hechos contra transiciones.** `transition_guard` devuelve `Advance(new_state)`,
`RecordOnly` o `Reject`. Un `tool.settled` que llega cuando el agente ya esta en estado terminal
devuelve `RecordOnly`: el hecho se registra siempre (ocurrio en el mundo), pero no mueve la maquina.
Sin esta tercera respuesta, cada implementador inventaria su propia excepcion.

**Lease y fencing.** `AgentLease(agent_id, host_id, epoch: int, expires_at)`. Todo append lleva
`epoch`; el journal rechaza `epoch < current_epoch` con `EpochFenced`. TTL 30 s. **El lease se
refresca por append, no por temporizador**: cada escritura del agente lo renueva. Un agente que
lleva mas de 10 s sin escribir (herramienta larga) emite `agent.heartbeat` — que es exactamente el
`soft_checkpoint` de 6.4. Un solo mecanismo, no dos.

Esto define "host" de forma portable: **un ejecutor con identidad estable capaz de sostener
appends periodicos**. Un proceso largo lo cumple; una ejecucion fragmentada dirigida por alarm
(Durable Object, cron worker) tambien, siempre que el alarm emita el heartbeat. El diseno no asume
ninguna de las dos.

`paused` vs `suspended` no es cosmetico: `paused` conserva host y lease (reanudacion < 200 ms,
coste de un slot); `suspended` no tiene host, coste cero, reanudacion p50 < 2 s.

**Presupuesto de ingenieria del propio bucle:** overhead del runtime por turno (todo salvo
inferencia y ejecucion de herramienta) p99 < 60 ms; overhead por tool call p99 < 15 ms; pico de
appends por stream de sesion < 100/s.

### 6.2 El bucle

Un **turno** = una inferencia mas las herramientas que de ella se derivan. El bucle no conoce el
numero de agentes.

```python
class AgentRuntime(Protocol):
    async def admit(self, spec: AgentSpec) -> AgentId
    async def run(self, agent_id: AgentId, lease: AgentLease) -> AgentOutcome
    async def signal(self, agent_id: AgentId, sig: AgentSignal) -> None   # encola, no escribe
    async def checkpoint(self, agent_id: AgentId) -> AgentCheckpoint
    async def restore(self, agent_id: AgentId, lease: AgentLease) -> AgentId

AgentSignal = Literal["pause","resume","cancel","drain","nudge","budget_extended"]

class AgentSpec(BaseModel):
    parent: AgentId | None          # None solo para la raiz; nace igual por spawn
    kind: Literal["worker","supervisor","reconciler","human"]
    goal: Goal
    handoff: HandoffPacket | None
    budget_ask: Budget
    capabilities: CapabilityRequest

class AgentOutcome(BaseModel):
    state: Literal["completed","failed","cancelled","suspended"]
    verdict: Literal["satisfied","partial","impossible","aborted"]
    workspace: WorkspaceRef
    evidence: list[EventRef]
    spent: BudgetState
```

```python
async def _turn(self, st: AgentState) -> TurnOutcome:
    lease = self.budget.reserve_turn(st)           # reserva pesimista, pre-vuelo
    if lease.exhausted: return self._on_exhaustion(st)
    ctx = self.context.build(st.cursor, st.plan, lease.context_tokens, st.inbox)
    await self.journal.append(TurnStarted(
        recipe_ref=ctx.recipe_ref,                 # como se construyo  → resume
        rendered_ref=ctx.rendered_ref,             # que se envio exacto → replay
        pricing_ref=self.pricing_table_ref,
    ))
    resp = await self.llm.complete(ctx.request, timeout=lease.remaining_wall)
    self.budget.settle(lease, resp.usage)          # reconcilia la reserva
    calls = self._normalize_calls(resp)            # ids asignados por el runtime
    await self.journal.append(ModelOutput(text_ref=cas(resp.text), calls=calls,
                                          stop_reason=resp.stop_reason))
    if resp.stop_reason == "max_tokens":
        return self._on_truncation(st, resp)
    if calls:
        await self._dispatch(calls)                # → awaiting_tool
    self.criteria.evaluate_touched(st)             # verificacion oportunista (6.7)
    signals = self.progress.sample(st)             # nunca lo escribe el modelo
    await self.journal.append(ProgressSampled(signals))
    if (s := self.stall.verdict(st)) is not StallVerdict.OK:
        return self._escalate(st, s)
    return TurnOutcome.CONTINUE if calls else TurnOutcome.VERIFY
```

**Los `tool_call_id` los asigna el runtime, no el modelo.** `tool_call_id = f"{turn_seq}:{index}"`,
determinista. El id que venga del proveedor se guarda como metadato para poder responder en el
formato que espera su wire API. Razon concreta: el fallback `prompted_tools` de este repo
(`packages/llm/edecan_llm/prompted_tools.py`) parsea la llamada del texto y **no garantiza id
alguno ni mas de una llamada por respuesta**. Derivar la clave de idempotencia de un id que produce
un modelo debil es construir la durabilidad sobre la alucinacion.

De ahi tambien: `max_tool_calls_per_turn = 8`. Los excedentes se truncan y se le dice al modelo con
un `tool_result` tipado. Sin este limite, el limite de turnos no acota nada.

**Criterios de parada.** (1) `stop_reason == "end"` y verificacion PASS; (2) presupuesto agotado;
(3) stall irrecuperable; (4) cancelacion; (5) `max_turns` = 60 por objetivo, 400 por sesion.
El (5) es **cortafuegos anti-bucle, no politica**: la politica es el presupuesto. Si un despliegue
alcanza (5) de forma habitual, el bug esta en el presupuesto o en la granularidad de herramientas.

### 6.3 Progreso y deteccion de atasco

**El modelo nunca escribe progreso.** Su autoevaluacion se registra como `agent.self_report`, sirve
para observabilidad y para medir calibracion, y no entra en ninguna decision. Un modelo atascado es
exactamente el que peor sabe que lo esta.

**No hay escalar de progreso en el camino de decision.** Colapsar senales de unidades distintas con
pesos inventados y umbralizar sobre el resultado es el fallo clasico. Peor, `criteria_ratio` es
escalonado: en un objetivo con dos criterios solo puede subir dos veces en toda su vida, asi que
cualquier regla de tipo "sin delta>0 durante el 25% del presupuesto" produce falsos positivos
sistematicos en objetivos largos.

La regla de progreso es **disyuncion de senales independientes**. Hay progreso en la ventana W
(ultimos 6 turnos) si se cumple al menos una:

- **(a) `criteria_passed` aumento** — la senal fuerte.
- **(b) el `score` del verificador primario mejoro estrictamente.** `ToolResult` del ABI lleva
  `score: int | None` con semantica **"menor es mejor"**, rellenado por el *plugin* de la
  herramienta (el plugin de pytest sabe contar fallos de pytest). **El runtime no parsea texto de
  herramientas jamas**: eso seria logica de dominio en el kernel (invariante 6). Sin `score`, la
  senal (b) no existe.
- **(c) el `subtree_hash(write_set)` cambio a un valor nunca visto Y (b) no empeoro.**

`progress_score = 0.60·criteria_ratio + 0.30·verifier_delta + 0.10·novelty` se conserva **solo como
metrica de observabilidad y de comparacion entre sesiones**. Nunca dispara nada.

| Senal de atasco | Umbral | Como se calcula (barato gracias al CAS) |
|---|---|---|
| `repeat_error` | 3 en ventana de 8 tool calls | hash de `(tool, error_class, error_norm[:200])`; `error_norm` quita timestamps, PIDs, rutas volatiles, direcciones de memoria |
| `oscillation` | ciclo de largo <=4 en los ultimos 12 `subtree_hash(write_set)` | **hash del subarbol del write_set con las ignore-rules del workspace aplicadas**, no del arbol completo: un `.pyc` o un log no debe contar como cambio |
| `redundant_read` | 3 en ventana de 12 | `(tool, args_hash)` con `result_hash` identico **y sin ningun evento de escritura entre medias**. Releer tras editar es correcto y no debe penalizarse |
| `no_progress_spend` | 30% del presupuesto del objetivo sin ninguna de (a)/(b)/(c) | contador del governor |
| `plan_thrash` | 3 `plan.revised` sin un `criterion.passed` intermedio | pliegue de eventos `plan.*` |
| `truncation_loop` | 2 `stop_reason == "max_tokens"` consecutivos | el modelo esta escribiendo un artefacto en la respuesta en vez de usar una herramienta de escritura |
| `cost_per_progress` | > 4x la mediana movil del workspace para esa clase de tarea | `tokens / max(eventos_de_progreso, 1)` |

Cada senal emite `agent.stall_suspected(signal, evidence)`.

**Escalera de escalado.** Avanza por veredicto repetido, no por tiempo. **Maximo 3 escalones
consumidos por objetivo en total**, para que la escalera no sea un gastadero:

1. `nudge` — se inyecta la **evidencia literal** en contexto ("has ejecutado `pytest -q` con el
   mismo fallo 3 veces; el error fue X"). Max 2 por objetivo. No cuesta turno adicional.
2. `reframe` — turno de replanificacion con ABI restringido a herramientas de solo lectura y
   obligacion de contrastar el plan contra la evidencia. Cuesta un turno.
3. `rollback` — el workspace vuelve al ultimo commit con evidencia de progreso (mover un puntero
   CoW, O(1)) y se reintenta con la evidencia del atasco. Max 1. **Admisible solo si desde ese
   commit no hay ningun evento con `effect_class` externo ni ningun `artifact.published` ya
   consumido por otro agente.** Si lo hay, se salta al escalon 4: revertir el workspace cuando el
   mundo exterior ya vio el efecto deja el sistema inconsistente sin avisar.
4. `assistance.requested` → `awaiting_approval`, y si no hay respuesta,
   `agent.failed(reason="stalled")`.

### 6.4 Durabilidad

**El checkpoint no es una segunda fuente de verdad: es una cache de proyeccion verificable.** Se
emite como evento `agent.checkpointed(payload_ref)` y cumple el invariante
`fold(journal, up_to=journal_seq) == checkpoint`, comprobado por un test obligatorio en CI sobre
trazas grabadas. `restore` no es una API distinta: es `fold` con atajo. Objetivo: <= 4 KB
serializado, escritura p99 < 15 ms.

```python
class AgentCheckpoint(BaseModel):
    agent_id: AgentId; epoch: int; host_id: str
    component_versions: dict[str, int]   # {"plan":1,"budget":2,"stall":1,"progress":1}
    journal_seq: int                     # ESENCIAL
    workspace_ref: WorkspaceRef          # ESENCIAL (workspace_id, commit_hash)
    state: AgentStateName
    plan_ref: Hash
    context_recipe_ref: Hash
    budget: BudgetState
    stall_state: StallState              # ventanas y hashes recientes
    progress: ProgressSignals
    pending: list[PendingUnit]           # tool calls despachadas sin settle
```

**Versionado por componente, no monolitico.** Un cambio en el formato del plan no debe invalidar un
checkpoint que no depende de el. Solo `journal_seq` y `workspace_ref` son esenciales: cualquier otro
componente con version desconocida **se descarta y se reconstruye desde el journal**, con coste pero
sin bloquear. Un `runtime_abi_version` unico haria que casi cualquier despliegue bloqueara agentes,
que es justo lo que se queria evitar. Se elimina `rng_seed`: no reproduce nada util (el LLM no es
determinista) y sugeria una garantia falsa.

Frecuencia: **frontera de turno y frontera de herramienta**, los unicos puntos donde el estado es un
pliegue bien definido. Ademas `agent.heartbeat` cada 10 s durante herramientas largas, que solo
avanza `budget` y los latidos de `pending`, y que es tambien el refresco del lease (6.1).

**`resume` y `replay` son cosas distintas y el original las confundia.**

- **`resume`** re-ejecuta `context_recipe_ref` con el compactador y el presupuesto vigentes hoy.
  Deliberadamente **no determinista**: una sesion de tres dias merece el contexto de hoy.
- **`replay`** reconstruye el contexto exacto desde `rendered_ref`, el prompt literal enviado,
  materializado en CAS. **Determinista.** Sin esto, la entrada real del modelo no esta en el journal
  y "el journal es la unica fuente de verdad" seria falso: un incidente de tipo "el modelo hizo algo
  raro" no seria diagnosticable. El coste es bajo porque el CAS deduplica el prefijo comun; el
  incremento por turno es el delta, no el contexto entero.

**Reanudacion at-least-once.** Toda invocacion lleva
`idempotency_key = H(agent_id, turn_seq, tool_call_id, args_hash)`. La deduplicacion **no es estado
nuevo**: consultar la proyeccion `tool.settled` del propio agente ya responde "esto ya corrio". La
ventana de dedup es la del journal, que nunca se trunca.

**Clases de efecto** — declaradas en el manifiesto del plugin, obligatorias. Una herramienta sin
declaracion se trata como `external_irreversible` (fail-safe):

| `effect_class` | Semantica | Politica ante crash |
|---|---|---|
| `pure` | sin efecto observable fuera del proceso | reintento libre |
| `workspace` | solo escribe en la rama CoW | reintento libre; revertible por rollback |
| `external_idempotent` | efecto externo con clave de deduplicacion aceptada por el destino | reintento con la key |
| `external_irreversible` | correo, push, cobro, despliegue | dos fases, **nunca reintento** |

Dos fases: `tool.intent_recorded` → ejecucion → `tool.settled`. Un intent sin settle tras un crash
va a `assistance.requested(kind="unblock")` diciendo "no se si esto se ejecuto". Es la unica
respuesta honesta. La clase `external_idempotent`, que el original no tenia, saca de ese camino a la
mayoria de efectos externos reales (APIs con `Idempotency-Key`, `PUT` de objetos, `git push --force-with-lease`)
y reduce mucho el spam de preguntas al humano.

**Cancelacion.** `cancel` → señal cooperativa al sandbox → `grace = 15 s` → SIGKILL → destruccion del
sandbox → `agent.cancelled`. **La rama CoW de un agente cancelado se conserva 7 dias** marcada
`workspace.orphaned`; descartarla es un acto explicito. Descartar por defecto el trabajo de un
agente cancelado por error es perdida de datos, y el original no lo cubria.

**Cancelacion transitiva.** Es eventualmente consistente con cota: un hijo observa la cancelacion
del padre en p99 < 5 s (consulta el pliegue cacheado del padre en cada frontera de herramienta,
coste despreciable). **Antes de cualquier herramienta `external_irreversible`, el hijo revalida su
lease y el estado del padre.** Un unico chequeo, en el unico punto donde importa.

**Migracion entre hosts.** No hay migracion en vivo. La unica primitiva es revocar lease →
`suspended` → re-admitir con `epoch+1`. El coste real es hidratar el workspace desde el CAS; se
mitiga con hidratacion perezosa (overlay respaldado por CAS: se materializa O(archivos realmente
leidos), no O(repo)) y `preferred_zone`. Reanudacion caliente p50 < 2 s / p99 < 10 s; fria (dias
suspendida) < 30 s en repos de hasta ~50k archivos.

**Sesiones de dias.** Una sesion suspendida cuesta cero. Lo que caduca es el contexto, no el estado.
El journal se compacta con snapshot de proyeccion cada 5.000 eventos; nunca se trunca, se archiva a
frio a los 30 dias. Sesion de un dia tipica: 20k-80k eventos, ~30 MB (los payloads viven en CAS).

**GC del CAS y referencias colgantes.** Todo blob referenciado por un evento de journal no archivado
esta **pinneado**; el GC solo puede borrar lo no referenciado. Si aun asi falta un `plan_ref` o un
`rendered_ref`, el agente entra `blocked(reason="cas_dangling_ref")`. **Nunca se continua con
contexto parcial**: eso seria corrupcion silenciosa disfrazada de disponibilidad.

**Sobrevivir a un despliegue de Forge.** (1) drain — los hosts dejan de admitir, terminan la
herramienta en curso, hacen checkpoint y emiten `agent.suspended(reason="drain")`; p99 < 90 s,
limite duro 180 s tras el cual se suspende en mitad de herramienta y aplica el protocolo de dos
fases. (2) el supervisor re-admite en hosts nuevos, descartando componentes de checkpoint con
version desconocida. (3) si falta un componente esencial, `blocked(reason="abi_mismatch")` en una
cola de operador — nunca se migra en silencio. El journal es compatible hacia delante por
construccion: los tipos de evento no cambian de semantica, solo se anaden; un tipo desconocido en
replay produce `blocked`, no un `ignore`.

### 6.5 Taxonomia de fallos y peticion de ayuda

| Clase | Deteccion | Politica | Cobra |
|---|---|---|---|
| `provider_unavailable` | transporte, 5xx | backoff 1/2/4/8 s con jitter, max 4; luego failover a proveedor de la misma clase de capacidad | tiempo |
| `provider_rate_limited` | 429 / `Retry-After` | espera respetando la cabecera; tras 2 esperas > 30 s → `blocked` y **libera el host**; el deadline se congela | nada |
| `context_overflow` | pre-vuelo del ContextBuilder | no deberia ocurrir: hay presupuesto. Si ocurre, compactacion forzada + 1 reintento; si repite, `failed(context_budget_violation)` y bug report | si |
| `output_truncated` | `stop_reason == "max_tokens"` | 1 continuacion automatica desde el prefijo, max 2; si repite → `truncation_loop` → `nudge` con esa evidencia exacta | si |
| `malformed_output` | parser (**esperado con K3**) | 1: reparacion determinista (extraccion laxa); 2: reintento con `prompted_tools`; 3: cuenta como `repeat_error` | si |
| `tool_error_business` | `ToolResult` | **no es fallo: es senal.** Vuelve al modelo como observacion | si |
| `tool_broken` | excepcion / esquema invalido | 1 reintento; si repite, `tool.quarantined` para este agente, se retira del ABI ofrecido y se le dice al modelo | si |
| `verifier_error` | criterio en ERROR (no FAIL) | 1 reintento; si repite, `blocked(verifier_broken)`. **Un verificador roto no es un objetivo fallado** | si |
| `sandbox_dead` | latido | rehidratar desde `workspace_ref`, re-ejecutar solo lo idempotente pendiente; cuenta un `restart` (max 3) | si |
| `capability_denied` | gate de capacidades | resultado tipado al modelo; 3 denegaciones distintas → `assistance.requested` | si |
| `budget_exhausted` | governor | ver 6.6 | — |
| `task_impossible` | verificador o modelo con evidencia | `assistance.requested`, nunca `failed` en silencio | si |
| `deadlock` | supervisor | aborta la victima mas barata (6.8) | si |

`CriterionResult.status ∈ {pass, fail, error, skipped}`. **`error` no es `fail`.** Contar el fallo
del sandbox como fallo del codigo dispara replanificacion sobre trabajo correcto y es una de las
formas mas caras de perder presupuesto.

**Peticion de ayuda.** Prosa libre es ruido operativo. Formato obligatorio:

```python
class AssistanceRequest(BaseModel):
    kind: Literal["decision","credential","clarification","unblock","verification"]
    question: str                      # UNA pregunta, <= 280 chars
    options: list[AssistanceOption]    # 2-5 acciones concretas con coste y riesgo estimados
    default: str | None                # option_id que se aplicara si nadie contesta
    evidence: list[EventRef]
    blocking: bool
    expires_at: datetime

class AssistanceOption(BaseModel):
    id: str; label: str
    est_cost_usd_micros: int; est_tokens: int
    risk: Literal["none","reversible","irreversible"]
```

Si `blocking=False` y hay `default`, el agente continua al expirar y registra
`assistance.auto_resolved`. El humano no es un mutex. **La expiracion no la dispara ningun
temporizador**: se evalua de forma perezosa en el siguiente intento de admision, mas un barrido del
supervisor cada 60 s. Decirlo evita que dos implementadores construyan dos mecanismos distintos.

### 6.6 Presupuesto

Dimensiones: `tokens_in`, `tokens_out`, `usd_micros`, `deadline` (wall clock, el que ve el humano),
`billable_seconds` (segundos de sandbox facturables; excluye ventanas en `blocked`), `tool_calls`,
`depth`, `fanout`, `workspace_bytes`.

**El governor es una proyeccion del journal, no un contador en memoria ni una fila en Postgres.**
`budget.reserved`, `budget.charged`, `budget.released` son eventos. `check()` es una funcion pura
sobre el pliegue, con cache en memoria. Un contador fuera del journal seria estado autoritativo
externo (invariante 2) y se perderia en cada reinicio.

Como la contabilidad cruza los streams de padre e hijos, **todos los eventos de presupuesto de una
sesion van a un unico stream `session/{id}/budget`** con compare-and-append. Cardinalidad real: 32
agentes x ~200 tool calls, agrupando cargos por turno, da ~6.400 appends por sesion, pico < 100/s.
Es el numero que hace viable un stream serializado.

```python
class BudgetGovernor(Protocol):
    def reserve(self, unit: WorkUnitRef, ask: Budget) -> BudgetLease
    def settle(self, lease: BudgetLeaseId, usage: Usage) -> BudgetState  # reconcilia
    def release(self, lease: BudgetLeaseId) -> Budget
    def check(self, lease: BudgetLeaseId) -> BudgetVerdict               # ok | warn | exhausted
```

**Reserva pesimista, no cargo posterior.** `reserve` descuenta el peor caso conocido
(`max_tokens` del request mas los tokens de entrada ya contados) **antes** de llamar al modelo;
`settle` devuelve la diferencia. Si el host muere entre la inferencia y el settle, el sistema
sobrecuenta — nunca subcuenta. El error es conservador por construccion. El diseno de "cargar
despues" pierde gasto en cada crash, y con streaming no hay `usage` hasta el final.

**Precios versionados.** `cost_usd_micros` se calcula con una tabla de precios en CAS,
referenciada en cada `TurnStarted` como `pricing_ref`. Sin esto, replayar una sesion de hace tres
meses recalcula costes con precios de hoy y la auditoria financiera miente.

**Reparto entre subagentes por arriendo renovable, no por particion estatica.** El padre retiene el
presupuesto; el hijo obtiene `granted = min(20% del restante del padre, coste estimado de 3 turnos)`
con `expires_at`, y renueva amortizadamente cada 3-5 turnos, no cada turno. Un hijo que muere
devuelve lo no gastado. Partir 1M de tokens entre 5 hijos donde 4 acaban al 10% y el quinto se ahoga
es el fallo clasico que esto evita.

**El padre no es un punto unico de fallo del hijo.** Mientras el arriendo no expire, el hijo corre
sin hablar con nadie. Si al renovar el padre esta `suspended`, el hijo entra
`blocked(reason="lease_renewal_unavailable")` con `unblock_deadline`; **no falla**.

Defaults por objetivo: 400k tokens, 2.00 USD, deadline +30 min, 200 tool calls, `depth <= 3`,
`fanout <= 4`. Al 80% se emite `budget.warning` y **se inyecta el hecho en contexto** ("te queda el
20%, prioriza cerrar"): el presupuesto es informacion que el modelo debe tener, no una emboscada.
`depth=0` no se comprueba con un `if`: la herramienta `spawn_agent` simplemente desaparece del ABI
(invariante 7).

Al agotarse (`on_exhaustion` default `"ask"`): checkpoint + `assistance.requested(kind="decision")`
con opciones {extender, aceptar parcial, abortar y revertir} + `suspended`. `"degrade"` cambia a
modelo mas barato y ABI reducido. **Regla dura: el agotamiento nunca interrumpe una herramienta en
curso; impide el siguiente turno.** Kill duro solo al superar el deadline 3x.

### 6.7 Planificacion

Representacion: **DAG de objetivos con aristas tipadas**. Un arbol no expresa "C necesita los
artefactos de A y B"; una lista no expresa paralelismo, que la invariante 9 exige en el contrato
desde el dia cero. Ciclos rechazados en `plan.published`.

```python
class Criterion(BaseModel):
    id: CriterionId
    kind: Literal["command","predicate","artifact_exists","judge","human"]
    spec: dict[str, Any]         # {"cmd":"pytest -q tests/test_x.py","expect_exit":0}
    spec_hash: Hash              # congelado en plan.published
    watch_set: list[str]         # globs cuyo cambio invalida el ultimo resultado
    guard_set: list[str]         # ficheros que DEFINEN el criterio (los tests mismos)
    required: bool = True
    ever_failed: bool = False
    tainted: bool = False        # el agente modifico algo de guard_set
    last: CriterionResult | None

class CriterionResult(BaseModel):
    status: Literal["pass","fail","error","skipped"]
    score: int | None            # menor es mejor; lo rellena el plugin de la herramienta
    evidence: EventRef
    guard_hash: Hash             # subtree_hash(guard_set) en el momento de evaluar
    at_seq: int

class Goal(BaseModel):
    id: GoalId; parent: GoalId | None
    intent: str                                   # una frase imperativa
    acceptance: list[Criterion]                   # >= 1, obligatorio
    deps: list[GoalDep]                           # kind: artifact | ordering | exclusive
    assignee: AgentId | None
    write_set: list[str]                          # globs; ver enforcement abajo
    status: Literal["pending","ready","running","satisfied","failed","abandoned"]
    evidence: list[EventRef]
```

**Un objetivo sin criterio verificable no se admite.** Si el planificador no sabe producirlo, el
criterio degrada a `kind="human"` y ese objetivo es bloqueante por construccion. Orden de
preferencia: `command` > `predicate` > `artifact_exists` > `judge` > `human`. **`judge` (LLM como
juez) nunca basta en solitario para un criterio `required`**: con el minimo comun denominador de
proveedor es la senal menos fiable y la mas manipulable por el propio agente.

**El agente no puede editar en silencio lo que lo verifica.** Esta es la defensa que faltaba, y es
mas fuerte que `ever_failed`. Cada criterio declara `guard_set` (los ficheros que lo definen: los
tests, el script de verificacion). En cada evaluacion se registra `guard_hash`. Si el `guard_hash`
cambia respecto al de `plan.published`, el criterio queda `tainted` y **exige aprobacion humana**.
Ademas, los criterios se ejecutan con un `CapabilityToken` derivado de solo lectura sobre `guard_set`
y sin escritura al workspace: el proceso de verificacion no puede modificar el objeto de la
verificacion. `ever_failed` se mantiene como senal secundaria: un criterio que nace en PASS se marca
`weak` y no cuenta hasta que se demuestre que sabe fallar.

**Verificacion oportunista, no solo al final.** Un criterio se re-evalua en cuanto un evento toca su
`watch_set` (comparacion de hashes, coste despreciable), no unicamente al entrar en `verifying`.
Esto da tres cosas: senal de progreso continua en vez de escalonada, deteccion temprana de
regresiones, y un estado `verifying` que suele ser una confirmacion rapida en vez de una tanda cara
de comandos. **La ejecucion de un criterio `command` es una tool call normal, por el mismo
`ToolPlane`, con `purpose="verification"** — no hay un segundo camino de ejecucion en el kernel
(invariante 6). Cobra presupuesto como cualquier otra.

**Relacion plan-journal.** El plan es una proyeccion: `plan.published(version, plan_ref)`,
`plan.revised(version, delta_ref, full_ref, reason)`, `goal.status_changed`,
`criterion.evaluated(result_ref)`. `plan.revised` lleva parche JSON **y** el hash completo
resultante: el replay no puede depender de que la aritmetica de deltas sea correcta.

**Replanificacion** solo por disparador: criterio FAIL (nunca ERROR) con evidencia que contradice un
supuesto, escalon 2 de la escalera de atasco, informacion nueva del humano, o dependencia fallida.
Maximo 5 revisiones por objetivo. Replanificar cuesta un turno y cobra presupuesto. Replanificar
cada turno esta explicitamente prohibido: duplica el gasto y produce `plan_thrash` con un modelo
debil.

**El `write_set` es un mecanismo, no una promesa.** Un modelo debil no sabe a priori que archivos va
a tocar, y toda la regla de paralelizacion se apoyaba en esa declaracion. Correccion: el
`CapabilityToken` del agente **limita fisicamente la escritura al `write_set`**. Un intento fuera de
el produce `capability_denied` mas
`assistance.requested(kind="decision", options=[ampliar, redirigir, abortar])`. Si la ampliacion
solapa con el write_set de otro agente vivo, el supervisor serializa. Asi un write_set fantasioso se
detecta en el primer intento de escritura, no en el merge, que es donde cuesta caro.

### 6.8 Multi-agente

| Topologia | Soporte | Razon |
|---|---|---|
| Supervisor-obrero | **SI, primaria (fase 2)** | unica que da contabilidad de presupuesto, cancelacion transitiva y un dueno inequivoco de la fusion |
| Pipeline | **SI (fase 2)** | caso degenerado del DAG con aristas `ordering`: cero mecanismo nuevo |
| Panel de jueces (N candidatos, 1 selector) | **NO planificada** | expresable con los mecanismos existentes (N objetivos hermanos, criterio `command`, seleccion por `score`). No merece contrato propio hasta tener un caso medido |
| Blackboard con memoria compartida escribible | **NO** | crea estado autoritativo fuera del journal (inv. 2) y acoplamiento implicito (inv. 10). Se aproxima con `artifact.published`: un blackboard append-only y direccionado por contenido |
| Mercado / subasta | **NO** | exige que los agentes estimen su propio coste; con modelo base debil las pujas no correlacionan con el coste real |
| Enjambre sin arbitro / debate libre | **NO** | sin arbitro no hay terminacion garantizada (livelock) ni dueno del merge |

**Protocolo: solo eventos.** Nunca llamadas directas. Primitivas: `agent.spawned`,
`message.posted(from, to|topic, body_ref, ttl)` (buzon = suscripcion a una proyeccion),
`artifact.published(agent, kind, cas_ref, goal_id)`, `claim.acquired/released`,
`agent.finished(result_ref, verdict)`.

**Cuota de mensajeria, obligatoria con ventana modesta.** Un agente con 20 hermanos podria recibir
20 mensajes por turno y llenar el contexto de K3 con coordinacion. Limite duro: **4 mensajes y 2.000
tokens de mensajeria por turno**, priorizados (dirigidos a mi > topic, mas reciente primero); el
resto se descarta con `message.dropped` y queda consultable bajo demanda.

**Handoff.** Nunca se copia la ventana de contexto del padre.

```python
class HandoffPacket(BaseModel):
    goal: Goal
    facts: list[FactRef]           # hechos establecidos, cada uno con EventRef de evidencia
    constraints: list[str]
    workspace: WorkspaceRef        # rama CoW propia
    capabilities: CapabilityToken  # incluye journal_read_scope=(parent_stream, max_seq)
    budget: BudgetLease
    forbidden: list[str]           # lo ya probado que no funciona, del StallState del padre
```

`forbidden` es la pieza que impide que N hijos repitan el mismo callejon sin salida.

**Techo duro de 3.000 tokens renderizados** para el paquete completo. Con una ventana de contexto
modesta, un handoff de 8k tokens es un cuarto del presupuesto del hijo antes de empezar. `facts` y
`forbidden` se truncan por recencia y el resto queda accesible bajo demanda con una herramienta
`read_parent_journal`. Contexto tirado, no empujado: con modelos debiles y ventanas pequenas es
estrictamente mejor.

**El limite "puede leer hacia atras, nunca hacia delante" lo impone el `CapabilityToken`**
(`journal_read_scope=(stream_id, max_seq)`), no una convencion. En el original era un `int` en un
paquete de datos, es decir, nada (invariante 7).

**Aislamiento y fusion.** Un agente = una rama CoW (`workspace.forked`). La fusion la ejecuta **el
supervisor, nunca el obrero**: `merge.requested` → `merge.evaluated` (conflictos por diff de
`tree_hash` sobre la base comun) → `merge.applied | merge.rejected`. Los conflictos textuales no se
auto-resuelven: se convierten en un `Goal` nuevo para un agente `kind="reconciler"` con criterio
`command` (ambas suites deben pasar). Revertir un merge es mover un puntero: O(1).

**Trabajo duplicado.** Claims sobre regiones, no sobre "tareas": `claim = (path_glob, mode, goal_id)`.
El supervisor concede escritura en exclusiva; el conflicto produce `blocked` o rechazo de fanout.
Deteccion posterior: dos `artifact.published` con el mismo `tree_hash` de subarbol →
`duplicate.detected`, se descarta el mas caro.

**Deadlock.** Grafo wait-for mantenido por el supervisor; deteccion de ciclos en cada `agent.blocked`
(<= 32 nodos, < 1 ms). Politica: abortar la **victima mas barata** = menor `tokens_spent` con menos
eventos de progreso; se suspende, libera claims y se re-admite con `forbidden` actualizado.
**Livelock**: dos agentes que se deshacen el trabajo mutuamente se detectan con la misma senal
`oscillation` aplicada al `subtree_hash` del merge del padre; politica: revocar el fanout y
serializar con aristas `ordering`.

**Cuando N agentes son peor que uno.** Coste de coordinacion
`C ≈ n·handoff + n·verificacion_merge + C(n,2)·P_conflicto·coste_conflicto`, con
`handoff <= 3k tokens` y `verificacion_merge ≈ 1 corrida de suite`. Paralelizar solo si se cumplen
las tres: (a) `write_set` declarados disjuntos, (b) cada objetivo tiene criterio `command`,
(c) trabajo estimado >= 20k tokens por objetivo. Si falta una, `fanout = 1`. Solape de globs con
`P_conflicto > 0.3` → serializar.

**Numeros coherentes con ese analisis**: el limite operativo es `fanout <= 4` por nodo del DAG y
`depth <= 3`; la concurrencia total de sesion la acota el presupuesto, no un numero magico. El 32 es
el tope del detector de ciclos, **no una recomendacion de despliegue**: con la formula de arriba,
n > 4 casi nunca sale a cuenta.

**Recursos externos compartidos entre sesiones** (puertos, bases de datos de test, servicios de
terceros) **no estan arbitrados**. Ver Riesgos aceptados.

### 6.9 Fase 1 == Fase N: la demostracion

1. El agente raiz tambien nace de `spawn`, con `parent=None`. No existe un camino "modo simple".
2. `run` opera sobre `AgentId` y su propio cursor; nunca sobre "la sesion". El multi-agente es
   planificacion (DAG con `fanout>1`) mas scheduling, no una API distinta.
3. El presupuesto pasa siempre por `BudgetLease`, incluso con un agente (lease raiz sobre el
   presupuesto de sesion), y siempre por el stream `session/{id}/budget`.
4. El workspace es siempre una rama CoW; con un solo agente el merge es fast-forward.
5. Toda herramienta se ejecuta con `idempotency_key` y `claim`, aunque no haya contencion.
6. El humano es un agente `kind="human"` con presupuesto de tokens cero: la peticion de aprobacion
   usa el mismo `message.posted` que la comunicacion entre agentes.

**Guardian del contrato (dos tests obligatorios en CI):**

- **Traza.** Correr el mismo escenario con `fanout=1` y `fanout=4`; el conjunto de tipos de evento
  debe ser identico salvo `agent.spawned`, `claim.*` y `merge.*`, **y ademas** la traza de tipos del
  agente unico debe ser una subsecuencia ordenada de la traza de cada obrero. Comparar solo
  conjuntos es debil: un camino especial puede reutilizar tipos existentes.
- **Estructural (AST).** Ninguna funcion de `edecan_forge_runtime` fuera del planificador y del
  supervisor puede leer `fanout`, `parent is None` ni `len(agents)`. Ese grep es lo que realmente
  cierra el agujero: prohibe escribir la rama especial antes de que exista.

### Alternativas descartadas

| Alternativa | Por que se descarto | Coste si nos equivocamos |
|---|---|---|
| Actor durable con estado propio en memoria (p. ej. un Durable Object autoritativo) como fuente de verdad | Viola la invariante 2 y ata el diseno al proveedor; el replay dejaria de ser posible | Perder replay, auditoria y portabilidad; migrar de Cloudflare seria una reescritura |
| Validar la maquina de estados **dentro** del journal | Acopla el plano de control con el dominio del runtime (inv. 10); el journal dejaria de servir a otros bloques | Cada bloque nuevo tendria que negociar su validacion con el journal |
| Fencing solo por `epoch`, sin escritor unico por stream | El epoch protege entre hosts, no entre corrutinas del mismo host; watchdog y bucle son escritores validos concurrentes | Ordenes de evento imposibles y transiciones que ningun actor decidio, con el mismo epoch |
| ReAct puro sin plan explicito | No hay criterios de aceptacion, luego no hay metrica de progreso objetiva ni deteccion de atasco | El sistema gasta hasta el presupuesto y declara exito por autocertificacion |
| Arbol de objetivos en vez de DAG | No expresa dependencias multiples ni paralelismo; obligaria a reescribir para la fase N | Romper la invariante 9 en el contrato, no solo en la implementacion |
| Escalar unico de progreso con pesos fijos como disparador | `criteria_ratio` es escalonado; con dos criterios solo cambia dos veces por objetivo. Genera falsos positivos de "sin progreso" en objetivos largos | El detector de atasco aborta trabajo sano y deja pasar el atasco real |
| Que el runtime parsee la salida de las herramientas para extraer progreso | Logica de dominio en el kernel (inv. 6); un parser por herramienta y por version | Kernel que hay que tocar cada vez que cambia el formato de salida de pytest |
| `tool_call_id` generado por el modelo como base de la idempotencia | El fallback `prompted_tools` del repo no garantiza id ni multiples llamadas; K3 puede repetirlos | Deduplicacion rota: efectos ejecutados dos veces tras cada reinicio |
| Cargo de presupuesto posterior a la inferencia | Un crash entre inferencia y cargo pierde gasto; con streaming no hay `usage` hasta el final | Subconteo sistematico: el presupuesto miente a la baja y no protege |
| Reparto estatico de presupuesto entre hijos | Los hijos rapidos devuelven nada y el lento se ahoga | Trabajos abortados al 90% con presupuesto global sin usar |
| Solo `context_recipe_ref`, sin el prompt renderizado en CAS | La entrada real del modelo no estaria en el journal; "unica fuente de verdad" seria falso | Incidentes de comportamiento del modelo no diagnosticables ni auditables |
| `runtime_abi_version` monolitico | Cualquier cambio de formato invalida todos los checkpoints | Cada despliegue de Forge bloquea agentes en la cola de operador |
| Migracion en vivo del proceso (CRIU / snapshot de memoria) | Ata a arquitectura y kernel, no cruza zonas, no sobrevive a un cambio de version del runtime | Imposible desplegar Forge sin matar sesiones largas |
| LLM-as-judge como unico criterio `required` | Con K3 la fiabilidad del juez es la peor senal disponible y es manipulable por el propio agente | Aceptar trabajo roto con evidencia falsa; peor que no verificar, porque genera confianza |
| Progreso auto-reportado por el modelo | El modelo atascado es el que peor lo sabe | El detector de atasco se apaga justo cuando hace falta |
| Estado del agente como columna en Postgres actualizada con UPDATE | Estado autoritativo fuera del journal; sin fencing hay split-brain | Dos hosts avanzando el mismo agente y un journal que describe un plan que nadie tomo |
| Panel de jueces como topologia con contrato propio | Expresable con objetivos hermanos y `score`; sin caso medido es superficie sin ganar | Mantener un mecanismo que nadie usa y que hay que versionar |
| Mercado/subasta y blackboard escribible | Ver tabla 6.8 | Gasto no atribuible y estado compartido no auditable |
| Descartar por defecto la rama CoW de un agente cancelado | Una cancelacion accidental destruiria trabajo | Perdida de datos irreversible por un clic |

### Riesgos aceptados

1. **Recursos externos compartidos entre sesiones no estan arbitrados.** Dos sesiones distintas
   pueden competir por el mismo puerto, la misma base de datos de test o el mismo servicio de
   terceros. Un arbitro global de recursos seria un componente centralizado, un punto unico de fallo
   y una fuente de acoplamiento entre sesiones. Mitigacion: cada sandbox recibe su propio namespace
   de red y su propio almacen efimero, de modo que la contencion se limita a servicios externos
   reales. Metrica de vigilancia: `external_conflict_rate` por semana.
2. **La reserva pesimista sobreestima el gasto.** Reservar `max_tokens` por turno hace que el
   presupuesto se agote antes de lo estrictamente necesario en cargas con respuestas cortas.
   Aceptado: preferimos parar antes que gastar de mas sin saberlo. `settle` devuelve la diferencia,
   asi que el error solo dura un turno.
3. **`resume` no es determinista por diseno.** Reanudar re-ejecuta la receta con el compactador de
   hoy, asi que dos reanudaciones de la misma sesion pueden divergir. Se acepta porque la
   alternativa es congelar el contexto de una sesion de tres dias con el compactador de hace tres
   dias. El determinismo se preserva donde importa: en `replay` desde `rendered_ref`.
4. **El vector de progreso es adversarialmente atacable.** Un agente que introduce cambios triviales
   distintos (comentario con timestamp) mantiene la senal (c) viva. La disyuncion de senales lo
   acota (la (c) exige ademas que (b) no empeore) pero no lo elimina. No conocemos defensa completa
   sin coste de inferencia. Metrica: `progress_without_criteria_rate`.
5. **La hidratacion perezosa esconde el coste hasta el primer `grep -r`.** En un monorepo de varios
   GB, la primera herramienta que recorre todo el arbol paga la hidratacion completa. Mitigacion:
   indice de contenido precomputado por el plano de datos para las busquedas mas comunes; si no
   existe, el coste es real y visible en `hydration_bytes` por sesion.
6. **`external_irreversible` sigue sin salida limpia.** Aunque `external_idempotent` saca de ese
   camino a la mayoria de casos, para el resto cada crash con un intent abierto genera una pregunta
   al humano. No hay solucion sin idempotencia del lado del proveedor externo.

### Como se rompe

1. **Criterio falso amigo.** Un `command` que pasa trivialmente (suite vacia, test sin asercion)
   convierte el progreso en teatro. `guard_set` + `tainted` impide que el agente lo *modifique*;
   `ever_failed`/`weak` impide que cuente uno que nunca demostro fallar. Ninguna de las dos impide
   que el planificador lo *escriba mal desde el principio*. Vigilar `weak_criteria_rate`.
2. **El modelo debil no produce criterios utiles.** Todo degrada a `kind="human"` y Forge se
   convierte en una cola de aprobaciones. Alerta: `human_criterion_ratio > 0.4` sostenido.
   Respuesta: biblioteca de plantillas de criterio por clase de tarea, no mas prompting.
3. **Hidratacion de workspaces grandes.** Ver riesgo aceptado 5.
4. **Claims con globs demasiado amplios.** `src/**` como write_set entra en conflicto con todo y el
   sistema multi-agente degenera a serial. Ahora se detecta antes (el token deniega la escritura
   fuera del set), pero un write_set enorme no lo detecta nada. Metrica obligatoria:
   `claim_conflict_rate` por sesion; > 0.5 significa write_sets inutiles.
5. **El journal como cuello de botella de escritura.** Un agente con tool calls de alta frecuencia
   satura el append. Presupuesto: 1 fsync por turno agrupando 6-14 eventos. Si un turno emite > 100
   eventos, el bug es de granularidad de herramienta, no de journal. El stream unico de presupuesto
   por sesion es el candidato mas probable a saturarse primero: vigilar `budget_stream_append_p99`.
6. **Cancelacion no cooperativa.** Una herramienta que ejecuta contra un sistema remoto y no
   responde al `grace` de 15 s deja un efecto huerfano. El sandbox se destruye y el workspace queda
   limpio en su ultimo commit, pero el mundo exterior no.
7. **Fencing o escritura incompletos.** Si el CAS o el journal aceptan una escritura sin `epoch` o
   sin `expected_seq` (un camino nuevo, un script de mantenimiento), el split-brain vuelve y es
   silencioso. Toda ruta de escritura debe pasar por la misma guardia; es un invariante de codigo, y
   se testea con un caso que intenta escribir con `epoch` viejo y otro con `expected_seq` obsoleto.
8. **El limite de 32 KB por evento empuja demasiado al CAS.** Si muchos campos se convierten en
   referencias, leer un evento pasa a ser N lecturas del CAS y la proyeccion se vuelve lenta. Umbral
   de alarma: `cas_deref_per_event > 3` de media.
9. **La cuota de mensajeria descarta el mensaje que importaba.** Con 4 mensajes por turno, un aviso
   critico de un hermano puede caer en `message.dropped`. Los mensajes con `priority="critical"`
   saltan la cuota, lo que reabre el problema si todo el mundo marca critico. Vigilar
   `critical_message_ratio`.
10. **El checkpoint como cache de proyeccion puede divergir del journal sin que nadie lo note.** El
    invariante `fold == checkpoint` solo se comprueba en CI sobre trazas grabadas. Una divergencia
    en produccion se manifiesta como un resume que empieza en un estado ligeramente equivocado.
    Mitigacion: verificacion completa del pliegue en 1 de cada 100 resumes, muestreada.

---

## 7. Provider Layer, estrategia Workers AI / Kimi K3 y economía

Esta sección define la única frontera de Forge que toca a un tercero de inferencia. Todo lo demás
del kernel habla con ella por interfaz y jamás por nombre de proveedor.

Paquete: `packages/forge-provider/edecan_forge_provider/`. Es el **único** paquete del monorepo
autorizado a declarar como dependencia un SDK de inferencia o `httpx` contra un host externo de
inferencia. El contrato actual (`packages/llm/edecan_llm/base.py`, `LLMProvider` con `complete` y
`stream`) se reemplaza por completo: le faltan cancelación, deadline, presupuesto, capacidades
medidas, salida estructurada garantizada y contabilidad de coste real, y ninguna de esas seis cosas
se puede añadir como decorador sin que aparezca en la firma.

**Alcance, y lo que explícitamente NO está aquí.** `forge-provider` contiene solo lo que habla con
un servicio de inferencia: `LLMProvider`, `EmbeddingProvider` y, cuando aparezca un call site real,
`RerankProvider`, `VisionProvider` y `OCRProvider`. `StorageProvider`/CAS (§4), `VectorProvider`
(§9, memoria), `MemoryProvider` (§9) y `SandboxProvider` (§5, plano de datos) **no viven aquí**: son
plano de datos y sustrato, y meterlos en este paquete lo convertiría en el nodo que todo el mundo
importa, que es exactamente el acoplamiento que prohíbe la invariante 10. Aquí solo se referencian
por tipo.

---

### 7.0 Fase 0: la sonda de realidad (bloqueante)

Todo lo que sigue asume que Kimi K3 en Workers AI es utilizable como cerebro de un bucle agéntico.
**Esa premisa no está verificada y es falsable.** El primer entregable del proyecto, antes de
escribir el kernel, es medirla y publicar el resultado con criterios de aceptación numéricos:

| Métrica | Umbral de "sigue el plan" | Si no se cumple |
|---|---|---|
| `native_tools` con perfil `code_blob`, límite inferior IC 95% | ≥ 0.90 | se activa el transporte XML por defecto (§7.3); el plan sigue |
| Contexto útil medido (§7.1) | ≥ 48k tokens | el bucle de 28 pasos no es ejecutable; hay que rediseñar el agente a sub-tareas de ≤ 12 pasos con handoff por CAS, no parchear el prompt |
| `tps_output_p50` | ≥ 25 tok/s | el tier L pasa a un proveedor de pago y Workers AI queda en M/S; el crédito se estira igual pero cambia el plan económico |
| TTFT p95 | ≤ 2.5 s | el tier interactivo no vive en Workers AI |
| Tasa de éxito en el banco de tareas de referencia (§7.6) | ≥ 0.55 sin ayuda humana | el crédito es irrelevante: el cuello es la calidad, y la sección económica se reescribe entera |

El resultado de fase 0 se publica como `provider.card.published` y como una decisión de arquitectura
fechada. No se escribe una línea de router hasta que existe.

---

### 7.1 `ModelCard`: capacidades medidas, no declaradas

La pieza central no es el provider: es la card. Todo lo demás la consulta y nadie asume nada.

**Separación card / binding.** La card describe el **modelo** (lo que es cierto del modelo mismo,
inmutable, direccionable por hash). El binding describe el **acceso** (credencial, precios, cuotas,
pool de crédito: lo que cambia con la factura y con la cuenta). Mezclarlas obligaría a re-emitir la
card cada vez que cambia una tarifa.

```python
class ArgProfile(StrEnum):
    scalar = "scalar"                 # {"path": "a.py", "limit": 20}
    multiline_text = "multiline_text" # prosa con saltos de línea
    code_blob = "code_blob"           # código: comillas, backslashes, backticks, fences
    nested_object = "nested_object"   # objetos anidados con arrays

class Reliability(BaseModel):
    n: int                    # muestras
    successes: int
    lower_95: float           # límite inferior de Wilson. LA CIFRA DE DECISIÓN.
    point: float              # successes/n. Informativa. Nunca se decide con esta.
    p95_latency_ms: int
    measured_at: datetime
    source: Literal["probe", "production"]   # production domina cuando n >= 200

class TokenizerRef(BaseModel):
    kind: Literal["hf", "tiktoken", "heuristic"]
    id: str | None                    # "moonshotai/Kimi-K3" | "o200k_base" | None
    bytes_per_token_p50: float        # calibrado por la sonda contra usage real
    bytes_per_token_p95_err: float    # error relativo p95; el presupuesto usa p50 * (1 + err)

class ModelCard(BaseModel):
    provider_id: str          # "workers_ai" | "anthropic" | "ollama" | ...
    model_id: str             # id EXACTO del wire, nunca un alias mutable
    revision: str             # h(probe_suite_version, provider_id, model_id, results_canonical)
    probe_suite_version: str

    # forma
    context_window: int       # lo que anuncia el proveedor
    useful_context: int       # MEDIDO (definición abajo)
    useful_context_method: str
    max_output_tokens: int
    tokenizer: TokenizerRef

    # capacidades. None = NO SONDEADO. Nunca se asume True; None se trata como False.
    native_tools: Mapping[ArgProfile, Reliability] | None
    parallel_tools: bool
    strict_json: Reliability | None
    grammar: bool                       # constrained decoding real, no "modo JSON"
    vision: bool
    prefix_cache: PrefixCacheSpec | None
    streaming: bool
    reasoning: ReasoningSpec | None     # incluye max_reasoning_tokens y si se factura como salida
    logprobs: bool
    seed: bool                          # necesario para replay determinista (invariante 2)

    # rendimiento
    tps_output_p50: float
    ttft_ms_p50: int
    ttft_ms_p95: int

    # relación de facturación MEDIDA (no el precio: el precio es del binding)
    billing_unit: Literal["token", "neuron", "request", "second"]
    units_per_1k_input: Decimal         # p. ej. neuronas por 1k tokens de entrada
    units_per_1k_output: Decimal
    units_measurement_error: float      # dispersión observada en la sonda
```

```python
class ProviderBinding(BaseModel):       # dato de configuración, no medido
    binding_id: str
    model_ref: ModelRef
    credential_ref: CredentialRef       # NUNCA el secreto (§7.8)
    price_in_mtok: Decimal
    price_out_mtok: Decimal
    price_cached_in_mtok: Decimal | None
    credit_pool: str | None             # "cf_workers_ai_50k"
    rpm: int; tpm: int; max_concurrency: int; max_request_bytes: int
    priority: int                       # orden en la cadena de failover del tier
```

**Definición exacta de `useful_context`.** Es el mayor tamaño de prompt, en tokens, para el que el
modelo resuelve ≥ 0.90 de una tarea **compositiva multi-salto**: tres hechos plantados en posiciones
distintas del contexto que hay que combinar aritméticamente, más dos distractores casi idénticos,
más una instrucción al principio que contradice una al final (para medir recencia). *No* es
needle-in-a-haystack: recuperar una cadena literal no predice razonar sobre contexto largo, y los
modelos abiertos servidos en edge están sobreajustados justo a ese test. La búsqueda es binaria
sobre el tamaño (7-8 evaluaciones desde 50% de `context_window`), no una rejilla fija de 4 puntos,
para que el coste no explote con ventanas de 1M.

**El escalar es una simplificación consciente.** La degradación es una pendiente, no un acantilado,
y depende de la tarea. El router aplica sobre él un `context_factor` por `TaskClass` (extract 1.0,
summarize 0.85, edit 0.7, plan 0.6) que es dato de configuración, no código.

**El estimador de tokens es local y obligatorio.** `count_tokens` no es una llamada de red: Workers
AI no tiene endpoint de conteo, y hacer depender el presupuesto de una llamada extra por paso es
absurdo. `TokenizerRef` resuelve a un tokenizador local; si no hay ninguno conocido, se usa el
estimador de bytes calibrado por la sonda contra el `usage.input_tokens` real de los 43 escenarios.
El presupuesto **siempre reserva con `p50 * (1 + bytes_per_token_p95_err)`**, nunca con la
estimación central.

#### Estadística: por qué 30 muestras no bastan y qué se hace en su lugar

Con n=30 y 27 aciertos, el intervalo de Wilson al 95% es aproximadamente [0.74, 0.97]. El umbral de
decisión de los shims es 0.90. Es decir: **una sonda de 30 muestras no puede distinguir un modelo al
0.75 de uno al 0.97**, que es exactamente la distinción de la que cuelga toda la degradación. Un
diseño que decida con el punto estimado está decidiendo con ruido.

Régimen en dos etapas:

1. **Sonda (n=30 por perfil de argumento, prior).** Es un *gate*, no una medición fina. Se activa la
   ruta nativa solo si `lower_95 >= 0.90` (con n=30 eso exige 30/30). Si `lower_95 < 0.50`, se
   degrada de inmediato y ni se intenta.
2. **Evidencia de producción (posterior).** Cada llamada real actualiza un contador
   Beta(α, β) por `(model_ref, capability, arg_profile)`, alimentado desde el journal. A partir de
   n=200 la evidencia de producción sustituye a la de la sonda en la card efectiva. Esto unifica en
   un solo mecanismo la medición inicial y la detección de drift (modo de fallo 1), en vez de tener
   dos sistemas que pueden discrepar.

**Histéresis obligatoria** para que la ruta no oscile: se degrada si `lower_95 < 0.85` sobre ventana
deslizante de 200; se vuelve a promover solo si `lower_95 > 0.92` sobre 200 nuevas. Cada transición
emite `provider.capability.changed{model_ref, cap, from, to, lower_95, n}`.

#### Dónde vive la card

La card es estado, y por invariante 2 no puede ser estado autoritativo fuera del journal. Concreto:
el JSON canónico de la card se guarda en el CAS y su hash se emite en un evento
`provider.card.published{provider_id, model_id, revision, card_hash, probe_run_hash}` en el journal
de sistema `system/providers`. `CapabilityIndex` es una **proyección** de ese journal, reconstruible
desde cero. Una card nunca se muta: se publica otra revisión.

```python
class CapabilityIndex(Protocol):
    def card(self, ref: ModelRef) -> ModelCard: ...
    def supports(self, ref: ModelRef, cap: Cap, *,
                 arg_profile: ArgProfile = ArgProfile.scalar,
                 min_lower_95: float = 0.90) -> bool: ...
    def budget_tokens(self, ref: ModelRef, task: TaskClass, reserve_out: int) -> int: ...
```

`ModelRef` es opaco (`NewType` sobre `str` con constructor privado dentro de `forge-provider`). El
kernel no puede fabricar uno escribiendo un literal, porque no tiene el constructor. Esa es la
defensa estructural; el grep de CI es solo una red secundaria (§7.5).

#### El `Prober` no es un método del proveedor

La sonda es lógica **común**: los mismos escenarios contra cualquier proveedor, o las cards dejan de
ser comparables. Si cada implementación escribe su propia `probe()`, en tres proveedores hay tres
definiciones distintas de "soporta tools". El `Prober` es un componente único que consume
`LLMProvider.generate`. El proveedor solo aporta `catalog()` (ids disponibles y metadata declarada)
y, si existe, `billing_usage()`. Corolario útil: **el Prober y la suite de conformidad son el mismo
código** ejecutado con dos objetivos distintos.

Coste declarado de la sonda: presupuesto propio de 2.00 USD por modelo, con corte duro. El "menos de
0.50 USD" del diseño previo solo es cierto para modelos baratos con ventana modesta; contra un
modelo frontier con ventana de 1M, la búsqueda de contexto útil sola supera los 20 USD y hay que
saberlo antes, no después.

---

### 7.2 `LLMProvider` v2

```python
class Provenance(BaseModel):
    """Origen de un bloque de contexto. Habilita la cache y la enumerabilidad."""
    kind: Literal["cas", "volatile"]
    hash: Hash | None = None          # obligatorio si kind == "cas"

class ContentBlock(BaseModel):
    type: Literal["text", "tool_result_ref", "image_ref"]
    ...
    provenance: Provenance            # OBLIGATORIO. Sin esto no hay cache correcta.

class GenerateRequest(BaseModel):
    model: ModelRef
    system: Sequence[ContentBlock]
    messages: Sequence[Message]
    tools: Sequence[ToolSpec] = ()            # cada ToolSpec declara su ArgProfile
    tool_choice: ToolChoice = "auto"          # auto | none | required | {"name": ...}
    output_schema: JsonSchema | None = None
    max_output_tokens: int
    temperature: float | None = None
    stop: Sequence[str] = ()
    seed: int | None = None                   # ignorado si card.seed es False, y se reporta
    reasoning: ReasoningRequest | None = None
    cache_hints: Sequence[CacheBreakpoint] = ()
    deadline: Deadline                        # absoluto, reloj monotónico, propagado
    budget: BudgetHandle
    trace: TraceContext   # session_id, agent_id, turn_id, step_index, task_class, request_id

class Usage(BaseModel):
    input_tokens: int; output_tokens: int
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    billed_units: Decimal                     # neuronas/créditos crudos, tal cual los reporta
    cost_usd: Decimal                         # lo calcula CostMeter con el binding. NUNCA el provider.
    cost_source: Literal["measured", "estimated"]

class Degradation(BaseModel):
    what: Literal["cache_hints", "seed", "reasoning", "parallel_tools",
                  "strict_json", "native_tools", "temperature"]
    reason: str
    level: int = 0                            # nivel de shim aplicado (§7.3)

class Generation(BaseModel):
    text: str
    tool_calls: Sequence[ToolCall]
    structured: Mapping[str, Any] | None      # validado contra output_schema o None. NUNCA sin validar.
    stop_reason: Literal["end", "tool_use", "max_tokens", "stop_sequence", "cancelled", "error"]
    usage: Usage
    degradations: Sequence[Degradation]       # ningún parámetro se descarta en silencio
    complete: bool                            # False si el stream se cortó

class LLMProvider(Protocol):
    id: str
    async def catalog(self) -> Sequence[CatalogEntry]: ...
    async def generate(self, req: GenerateRequest, *, cancel: CancelToken) -> Generation: ...
    def stream(self, req: GenerateRequest, *, cancel: CancelToken) -> AsyncIterator[Delta]: ...
    async def billing_usage(self, since: datetime) -> BillingReport | None: ...
```

`EmbeddingProvider` comparte `CancelToken`, `Deadline`, `BudgetHandle` y `TraceContext` en la misma
posición, para que el decorador de reintento/presupuesto/telemetría sea uno solo:

```python
class EmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str], *, kind: Literal["query", "document"],
                    cancel: CancelToken, budget: BudgetHandle,
                    trace: TraceContext) -> EmbedResult    # dim, vectors, usage
```

**Semántica de cancelación, explícita.** `CancelToken.reason ∈ {user, deadline, budget, supervisor,
shutdown}` porque el manejo difiere: `budget` exige checkpoint y parada dura del agente; `deadline`
permite reanudar; `shutdown` exige checkpoint en menos de 5 s. Cancelar **aborta la conexión HTTP**,
y el gasto ya incurrido **se liquida igual** con los tokens contados localmente en los deltas
recibidos y `cost_source="estimated"`. Un ledger que no cobre lo cancelado deriva sistemáticamente a
la baja y hace explotar la reconciliación mensual.

**Un stream cortado nunca produce efectos.** Regla dura del contrato: una `ToolCall` solo es
ejecutable si procede de una `Generation` con `complete=True` y ya committeada en el journal. Esto
prohíbe la ejecución especulativa de herramientas durante el stream. El coste en latencia es
prácticamente nulo (la tool call es lo último que emite el modelo) y es lo único que hace sano el
failover de §7.4.

**Objetivos.** TTFT p50 < 900 ms y p95 < 2.5 s en el tier interactivo; clasificación en tier S p95 <
400 ms; embeddings en lote de 96 p95 < 1.2 s. **Límites.** Petición ≤ 8 MiB; resultado de
herramienta > 32 KiB va al CAS; prompt ≤ `useful_context * context_factor(task) * 0.9`.

---

### 7.3 Degradación: shims concretos

#### Sin tool-calling fiable

Se activa cuando `native_tools[arg_profile].lower_95 < 0.90` **para el perfil de argumento de la
tool concreta**, no para un agregado. Medir "tool-calling: 0.96" en agregado y luego escribir un
archivo de 4 KB con backticks es cómo se falla en producción con una card verde.

Transporte XML con **nonce por petición**, no XML plano. El diseño ingenuo (`<arg name="content">`
… `</arg>`) se rompe con el primer archivo que contenga literalmente `</arg>` — y ese archivo es,
entre otros, el propio parser de Forge. El agente que construye Forge rompería el shim de Forge:

```
<tool name="fs_write" fence="a7f39c2b">
<arg name="path">src/edecan_forge_provider/xml_transport.py</arg>
<arg name="content" fence="a7f39c2b">
def f(x):  # comillas "sin escapar", \backslashes, ```fences```, y hasta </arg>
    return x
</arg:a7f39c2b>
</tool:a7f39c2b>
```

Reglas del transporte:
- El nonce son 8 hex aleatorios por petición. Se verifica que **no aparece en ningún argumento
  serializado de vuelta**; si aparece, es colisión y se rechaza el parseo (va a nivel 3).
- El contenido **nunca se escapa; se delimita**. Es toda la razón de ser del formato.
- El nivel 1 es un lexer de estados, no una regex: no interpreta el interior de un `<arg>`.

Parser en tres niveles, y el gasto de cada nivel se registra:
1. **Estricto.** Lexer con nonce.
2. **Rescate.** Regex por `<arg name=...>` aunque falten cierres exteriores o el nonce; el último
   argumento truncado se marca `partial=True` y **jamás se ejecuta una escritura con un argumento
   parcial** (solo lecturas idempotentes).
3. **Reparación, una sola vez, y con la distribución cambiada.** Reintentar el mismo prompt a
   `temperature=0` cuando la llamada original ya iba a 0 es un reintento con fallo casi perfectamente
   correlacionado. La reparación **debe** cambiar algo material: se reduce a **una sola tool**
   ofrecida con `tool_choice="required"`, se pide **solo el argumento que falló** (reparación campo a
   campo, con los demás ya fijados por el sistema) y se sube a `temperature=0.3` para romper el modo
   determinista.

Cada nivel emite `provider.degradation.applied{level, tool, arg_profile}`. La tasa rodante de nivel
≥ 2 alimenta el posterior Beta de §7.1 y auto-demota el modelo.

#### Sin JSON estricto

Alcance, para que dos implementadores no lo lean distinto: **el bucle principal del agente no usa
`output_schema`**. El bucle usa el transporte de tool-calling (nativo o XML). `output_schema` existe
solo para las `TaskClass` estructuradas: `classify`, `extract`, `judge`.

Escalera: si `card.grammar`, decodificación restringida y se acabó. Si no: prefill del `{` +
`stop` en el cierre + validación `jsonschema` + reparación dirigida que envía **solo** los errores
de validación con su JSON Pointer, máximo 2 intentos, con schema simplificado en el segundo (se
aplanan `oneOf` y opcionales). Tercer fallo: extracción campo a campo con el tier S. **Nunca se
devuelve JSON no validado hacia arriba**: `Generation.structured` es `Mapping` validado o `None`.

#### Sin prefix cache

Honestidad primero: no se puede emular la caché KV de un proveedor remoto. Ninguna caché local
reduce los tokens de entrada facturados. Lo que sí reduce el gasto es no reenviarlos:

- **(a) Descarga al CAS.** Todo resultado de herramienta > 32 KiB va al CAS y entra al prompt como
  referencia + resumen estructural ≤ 200 tokens (primeras y últimas líneas, conteos, diffstat, lista
  de símbolos). Herramienta builtin `context_expand(ref, range)` para recuperar el detalle. Métrica
  de gobierno: **tasa de expansión**; si supera 0.25, el umbral está mal calibrado y se baja.
- **(b) Compactación cada K pasos**, sustituyendo el tramo antiguo por un resumen con checkpoint en
  el journal. No es opcional: con `useful_context` medido, el bucle largo **no cabe** sin ella (§7.6).
- **(c) Caché de respuesta**, con clave
  `h(model_ref, card.revision, prompt_canónico, hashes CAS de todo lo referenciado, params)`.
  Incluye `card.revision` porque un juicio emitido por otra revisión del modelo no es el mismo
  juicio. Es enumerable **por construcción**, porque cada `ContentBlock` lleva `provenance`: si
  cualquier bloque es `volatile`, no se cachea. Rinde en `judge` / `classify` / `extract` / `embed`
  y prácticamente cero en el bucle principal. Se documenta así para que nadie espere magia.

El objetivo de ahorro es **-50% a -65% de tokens de entrada por tarea**, y se declara como objetivo
con método de medición, no como hecho: ratio de tokens de entrada por tarea del banco de referencia
(§7.6) con y sin (a)+(b), sobre el mismo corpus y el mismo modelo. Cualquier cifra de ahorro sin
corpus de referencia es marketing.

#### Sin visión

Diferido a fase 3, sin call site en fases 1-2. Cuando exista (automatización de navegador), la regla
es: `OCRProvider` delante, y para UI se prefiere el árbol de accesibilidad o el DOM sobre los
píxeles. El modelo nunca ve la imagen; ve texto + bboxes + resumen de layout.

---

### 7.4 Router

`TaskClass` = `plan | edit | search | summarize | judge | classify | extract | repair | embed`.
Cuatro tiers; el enlace tier → `ModelRef` es **dato**, no código. La configuración debe declarar al
menos S y L; un tier puede apuntar al mismo `ModelRef` que otro (con dos modelos disponibles, cuatro
tiers son cuatro filas, no cuatro modelos).

| TaskClass | Tier | Política | Señal de escalada (objetiva, siempre) |
|---|---|---|---|
| plan | L (XL si > 200 archivos tocados) | temp 0.3, reasoning si la card lo tiene | precondiciones del plan no validan contra el workspace |
| edit | L | temp 0, tools requeridos | 2 fallos de parseo, o 1 fallo de tests → XL |
| search / classify | S | temp 0, salida enum | valor fuera del enum, o desacuerdo posicional |
| summarize | M | temp 0.2 | entrada > 0.5 × contexto útil → map-reduce en S |
| judge | M, 2 muestras **con posiciones invertidas** | | desacuerdo → L desempata |
| extract | S | schema estricto | fallo de validación tras 2 reparaciones → M |
| repair | M; L si el diff > 40 líneas | temp 0.3 | |

**Nunca se escala por confianza autorreportada.** La calibración de la confianza declarada de un
modelo abierto pequeño es ruido; usarla como señal es un error conocido y caro. Las señales válidas
son: (i) el resultado no valida contra schema/enum, (ii) no compila / los tests fallan, (iii)
desacuerdo entre dos muestras con **posiciones invertidas** (que además mide el sesgo posicional,
grande en modelos abiertos), (iv) logprob del token de decisión bajo umbral, **solo si**
`card.logprobs`.

**`judge` con dos muestras a temp 0 y sin `seed` es dinero tirado**: da dos veces la misma respuesta
y el desacuerdo nunca ocurre. Las dos muestras deben diferir estructuralmente — inversión del orden
de las opciones — no por temperatura.

**Criterio de la cascada, en la métrica correcta.** La cascada no se decide por USD/llamada sino por
**USD por tarea completada con éxito**:

```
E[coste_tarea] = coste_intento / p_éxito  +  coste_de_reparación_humana * (1 - p_éxito)
```

Se baja de tier solo si `coste_S / p_S < coste_L / p_L` medido en el banco de referencia. Un tier S
que ahorra 60% pero baja la tasa de éxito de 0.80 a 0.60 **sube** el coste real. Con este criterio,
"60% de las llamadas en tier S" deja de ser un supuesto de la hoja de cálculo y pasa a ser un
resultado medido. La cascada barata rinde cuando el resultado es **verificable sin modelo**
(compila, valida contra schema, coincide con regex) o cuando `p_S > 0.9` medido en esa clase. **No
rinde en `plan`**: un plan malo cuesta 20 pasos de herramienta, ~40 veces lo ahorrado.

**Auditoría sombra.** El 2% en XL tiene su propio presupuesto (`audit_budget`, 1-2% del presupuesto
de workspace), es lo **primero** que se corta al llegar al 80%, y arranca **apagada** hasta que
exista un tier XL enlazado. Con crédito de Workers AI para el trabajo normal y XL en un proveedor de
pago, la auditoría gasta dinero real desde el día uno y hay que presupuestarla explícitamente.

**Failover a mitad de turno.** El turno es una secuencia de eventos; la petición es una función pura
del prefijo. Ante 5xx, timeout o circuito abierto, el router re-emite el **mismo** `GenerateRequest`
normalizado contra el siguiente binding del tier. La salida parcial se registra como
`provider.request.failed{partial_hash, usage_estimada}` y **nunca se concatena** con la nueva: dos
tokenizadores distintos producen tool-calls a medio cerrar.

**Dos claves, no una — y no se llaman igual.** El diseño previo definía
`idempotency_key = h(turn_id, step_index, prompt_hash, attempt)` y se contradecía solo: con `attempt`
dentro, la clave cambia en cada reintento y ningún proveedor puede deduplicar con ella. Además, la
generación **no tiene efectos externos**; los efectos los tienen las herramientas. Por tanto:

- `request_id = h(turn_id, step_index, prompt_hash, attempt)` — vive en `TraceContext`, sirve para
  correlación y telemetría, y es único por intento. No es idempotencia de nada.
- `effect_key = h(turn_id, step_index, tool_name, args_hash)` — se consulta **contra el journal
  antes de ejecutar cualquier herramienta**. Este, y solo este, es el antídoto contra la doble
  escritura.

Y el caso que el diseño previo no veía: tras un failover, el otro modelo produce una tool call
**distinta**, con `args_hash` distinto, así que `effect_key` no coincide y se ejecutaría igual. Eso
solo es seguro porque una tool call de un stream incompleto **nunca se ejecutó** (§7.2). Sin esa
regla, `effect_key` no protege nada.

**Control de admisión y rate limit.** Los límites son de la **cuenta**, no del modelo, y el diseño
previo se contradecía: decía "semáforo global" mientras ponía la llamada en el nodo local, donde no
hay punto de coordinación. Concreto:

- **Un solo nodo de datos por workspace** (el caso del usuario, y el de fases 1-2): token bucket en
  proceso por `binding_id`. Suficiente, cero infraestructura.
- **N nodos**: el admission control usa el mismo mecanismo que el presupuesto — la sesión adquiere
  del plano de control un **lease de tasa** (X peticiones/min durante T segundos) y lo consume
  localmente. Nunca hay una escritura serializada por llamada.
- En ambos casos: 429 con `Retry-After` → backoff exponencial con jitter completo; circuito por
  `binding_id` con half-open y **una sola sonda**; cadena de failover por tier.

---

### 7.5 Cloudflare: qué encaja de verdad

| Servicio | Encaja | Para qué | Riesgo de acoplamiento | Frontera portable |
|---|---|---|---|---|
| Workers AI | Sí | `LLMProvider`, `EmbeddingProvider` | Bajo (HTTP) | `LLMProvider` v2 |
| Durable Objects | Sí, y es la mejor pieza | Escritor serializado del journal por sesión; alarms = deadlines y barrido de leases; storage transaccional = `BudgetAuthority` | **Alto**: su semántica es tan buena que se filtra sola | `JournalStore` + `SessionCoordinator` |
| R2 | Sí | CAS de blobs, artefactos, snapshots | Bajo (S3, sin egress) | `StorageProvider` (§4) |
| D1 | Marginal | Solo proyecciones e índices reconstruibles | Medio (~10 GB, SQLite) | `KeyIndex`; en local es Postgres, que el repo ya tiene |
| Vectorize | Parcial | Índice vectorial del edge | Medio: filtrado pobre, sin híbrido BM25, reindex caro | `VectorProvider` (§9); alterno pgvector |
| Queues | Sí | Fan-out diferido | Bajo-medio (at-least-once, ya exige idempotencia) | `EventBus` |
| Containers / Sandbox SDK | **No como principal** | Sandbox secundario sin repo local | **Alto** si se vuelve el único | `SandboxProvider` (§5) |

La ejecución pesada ocurre junto al disco del usuario. El plano de datos es un nodo local que abre un
túnel **saliente** al plano de control; nunca hay entrada al equipo. Corolario económico: la llamada
al LLM la emite el plano que tiene el contexto — el nodo local, contra Workers AI por HTTPS — porque
proxiarla por el edge duplicaría el transporte de decenas de miles de tokens por paso a cambio de
nada. El modo proxy queda como bandera (`call_site = "local" | "edge"`) para el día que haya
multi-tenant y la credencial no pueda tocar la máquina del cliente.

**Presupuesto y partición.** Que la llamada salga del nodo local significa que, con el plano de
control caído, **nadie puede reservar presupuesto**. La respuesta honesta es un `offline_allowance`
declarado por workspace (por defecto 2.00 USD) que el nodo puede gastar sin autorización, tras el
cual se detiene y hace checkpoint. No hay una respuesta mejor: cualquier otra o bloquea el trabajo
offline o renuncia al tope.

**Exclusividad de escritura, para que "gana el local" signifique algo.** La unidad de exclusividad
es la **sesión**, con un lease de escritor con TTL emitido por el plano de control. Offline, un nodo
solo escribe en sesiones cuyo lease ya poseía. Sin lease vigente, el trabajo va a una sesión
**forkeada** (`session_id` nuevo con `forked_from`) y la fusión es explícita, observable y reversible
(invariante 5). Así la divergencia se elimina por construcción en vez de resolverse a posteriori con
una política ambigua.

**Prueba de que un cambio de proveedor no toca nada fuera de la capa.** Cuatro mecanismos, de más
fuerte a más débil:

1. **`ModelRef` opaco.** El kernel no puede construir uno; solo `CapabilityIndex` los emite. Un
   literal `"workers_ai/..."` no compila en el resto del monorepo.
2. **Contrato de dependencias declaradas.** Un test lee los `pyproject.toml` y falla si cualquier
   paquete distinto de `forge-provider` declara `httpx`, `anthropic`, `openai`, `boto3` o cualquier
   SDK de inferencia. Es estructural: no se elude con una f-string, a diferencia del grep de AST.
3. **Suite de conformidad + `NullProvider`.** Los mismos escenarios contra cada implementación, y el
   kernel entero corriendo en CI con **cero proveedores reales**. Si algo fuera de la capa necesitase
   un proveedor concreto, el build falla solo.
4. **Allowlist de rutas por etiqueta de PR** (`provider:new` → el diff solo puede tocar
   `packages/forge-provider/**` + `forge.providers.toml`). Es frágil (quien etiqueta, decide) y
   barato: se conserva como señal, no como garantía.

El grep de AST buscando `"workers_ai"` del diseño previo se descarta como *garantía*: no ve
`f"workers_{suffix}"`, ni una tabla de configuración, ni `if pid.startswith("cf")`, y genera falsos
positivos en docs y tests. Se mantiene solo como lint informativo.

---

### 7.6 Economía

**Contabilidad.** Cada llamada emite `provider.request.completed` con `Usage` completo y `trace`
(session, agent, turn, step, task_class, tool_name). El `CostMeter` normaliza `billed_units` a USD
usando `billing_unit` + `units_per_1k_*` de la **card** y `price_*` del **binding** — Workers AI
factura en neuronas, no en tokens — y marca `cost_source`. El proveedor **nunca** reporta coste;
reporta unidades facturadas.

**Reconciliación, sin adornos.** Si el proveedor no expone API de facturación, la reconciliación es
un procedimiento **manual** mensual: `forge providers reconcile <factura.csv>` compara agregados por
`(binding_id, mes)` y emite `provider.billing.reconciled{drift_pct}`. Drift > 5% dispara alerta y
re-sonda de las relaciones `units_per_1k_*`. Llamarlo "reconciliación automática" sería una tarea
humana disfrazada de mecanismo.

**`BudgetAuthority` con leases, no ledger jerárquico distribuido.** El diseño previo pedía
`reserve/settle` con compare-and-swap serializado y una jerarquía workspace > sesión > agente > turno
> herramienta, sin decir dónde está el punto de serialización. Si el ledger de sesión vive en el DO
de sesión y el tope real es del workspace, cada reserva cruza dos objetos serializados: transacción
distribuida, o carrera. Concreto:

- **Un único punto de serialización por workspace**: `BudgetAuthority` (un DO por workspace, o una
  fila con `SELECT … FOR UPDATE` en Postgres; la interfaz es la misma).
- La sesión adquiere un **lease** de N USD en una sola operación serializada
  (`acquire(session_id, amount, ttl)`), y a partir de ahí `reserve`/`settle` son **locales al nodo**
  y no tocan el workspace hasta agotar el lease o vencer el TTL. Con lease de 5.00 USD y coste medio
  de 0.01 USD/llamada, son ~500 llamadas por escritura global, no una por llamada.
- Sobregasto máximo teórico = `sesiones_activas × lease_size`. Es una cifra **declarada y acotada**,
  no un accidente. Con 20 agentes y lease de 5 USD, el peor caso es 100 USD por encima del tope —
  frente a los 20x sin cota de un contador eventual.
- El lease sobrante se devuelve al cerrar la sesión; los caducados se barren por alarm.
- **`settle` tardío se aplica igual** (el gasto ocurrió) y emite `budget.late_settle`. El ledger
  admite saldo negativo temporal: un presupuesto que "no puede ir negativo" fuerza a perder
  contabilidad real, que es peor que un número rojo.
- **Reserva del peor caso** = `in_tokens_estimados * (1 + err_p95) * price_in + (max_output_tokens +
  max_reasoning_tokens) * price_out`. Omitir el razonamiento es el error más caro: en un modelo con
  thinking, los tokens de razonamiento se facturan como salida y son 5-10× la respuesta.
- Corte blando al 80% (se apaga la auditoría sombra, se baja un tier, se desactiva reasoning); duro
  al 100% (se dispara `CancelToken(reason="budget")`, el agente hace checkpoint y emite
  `budget.exceeded`).

**Cuánto rinden 50.000 USD.** Supuestos explícitos, todos falsables:

- **A1** Kimi K3 en Workers AI a 0.30 / 1.20 USD por MTok entrada/salida — banda central a
  recalibrar con la primera factura.
- **A2** Sin prefix caching: el contexto se retransmite entero en cada paso.
- **A3** "Tarea real" = 28 llamadas al modelo, 900 tokens de salida por llamada.
- **A4** El crédito cubre Workers AI, no R2/DO/Queues (~5-40 USD/mes, irrelevante).
- **A5** Overhead de reintentos y shims: ×1.15 sobre las llamadas del bucle.

**Corrección importante sobre el escenario base.** El escenario "sin optimizar" de 45k tokens de
entrada media **no es ejecutable**, no solo caro: con contexto útil medido en el entorno de ~50k y
`context_factor(edit) = 0.7`, el techo de prompt son ~31k tokens, y el paso 28 de un bucle que
acumula linealmente pediría ~82k. La compactación (§7.3.b) no es una optimización opcional: es la
condición para que el bucle exista. La línea base honesta es "con compactación mínima obligatoria".

| Escenario | Entrada media/llamada | USD/tarea (0.30/1.20) | Tareas con 50k |
|---|---|---|---|
| Compactación mínima (base ejecutable) | 28k | 0.32 | ~156.000 |
| + descarga al CAS + caché de respuesta | 18k | 0.21 | ~238.000 |
| + cascada (60% en S a 1/4 de precio, medido) | 16k | 0.12 | ~415.000 |
| Base con reasoning activo en `plan` (4 de 28 llamadas, 5k thinking) | 28k | 0.35 | ~142.000 |

| Precio in/out (USD/MTok) | USD/tarea base | USD/tarea optimizado | Tareas con 50k |
|---|---|---|---|
| 0.15 / 0.60 | 0.16 | 0.060 | 310k - 830k |
| 0.30 / 1.20 | 0.32 | 0.12 | 156k - 415k |
| 0.60 / 2.40 | 0.64 | 0.24 | 78k - 208k |

Embeddings: repo de 20.000 fragmentos × 400 tokens = 8 MTok; a ~0.012 USD/MTok, **0.10 USD por
reindexado completo**. Trescientos reindexados cuestan 30 USD. No es una línea del presupuesto.

**Conclusión honesta.** A ritmo humano (≤ 60 tareas/día, 250 días/año) los 50k duran entre **10 y 27
años**. El crédito no es el recurso escaso; lo son el rate limit, la latencia y la calidad. El
sistema de presupuesto existe por otra razón: un enjambre de 20 agentes en bucle a 3 llamadas/min
con 28k de contexto consume ~1.7 MTok/min ≈ 0.51 USD/min = **735 USD/día**, y se come los 50k en 68
días. **Los cortes duros protegen contra el bug, no contra el uso.**

**El banco de tareas de referencia es un entregable, no un detalle.** 20-30 tareas reales sobre este
mismo repo, con criterio de éxito **automático** (tests que pasan, diff que aplica, extracción que
valida). Sin él no hay router, no hay criterio de cascada, no hay medición de ahorro y no hay
manera de comparar dos proveedores. Es el artefacto más importante de la fase 1 y el diseño previo
ni lo mencionaba.

---

### 7.7 Credenciales y capacidades

La invariante 7 prohíbe la autoridad implícita, y una API key en una variable de entorno que todo el
proceso puede leer es exactamente autoridad implícita. Ni el diseño previo ni el `LLMRouter` actual
lo abordan.

- La credencial **nunca se pasa como valor**: se pasa como `CredentialRef`, resuelto por un
  `SecretBroker` **dentro** de `forge-provider`, en el momento de firmar la petición HTTP.
- El token de capacidad de un agente declara `providers: [ModelRef]` y `spend_cap`, **jamás** la
  clave.
- Un plugin **nunca** recibe una credencial de inferencia. Si un plugin necesita un modelo, invoca la
  herramienta builtin `llm.generate` con su propia capacidad y su propio presupuesto. Efecto
  secundario valioso: contabilidad de gasto **por plugin**, gratis.
- Rotación y rechazo: 401/403 → se invalida el binding, se emite `provider.credential.rejected`, se
  hace failover al siguiente binding del tier y **no se reintenta contra el mismo**. Reintentar con
  una clave mala es cómo se bloquea una cuenta.

---

### 7.8 Plan de salida

El día que se acaba el crédito: (1) `forge providers add <kind>` guarda la credencial en el broker;
(2) `forge providers probe <kind>/<model>` corre la sonda y publica cards medidas; (3) se editan las
4 líneas de `forge.providers.toml` que enlazan tier XL/L/M/S a `ModelRef`; (4) `forge providers
verify` corre la conformidad y un canario del 5% del tráfico durante 24 h comparando **coste por
tarea exitosa** y tasa de éxito sobre el banco de referencia, no solo coste por llamada. Cero
cambios de código.

Si el nuevo modelo tiene tool-calling nativo fiable, los shims **se apagan solos** porque la card lo
dice: no hay que borrar nada. La convivencia de pools es nativa: `credit_pool` vive en el binding,
así que XL en Anthropic con tarjeta y M/S en Workers AI contra el crédito funcionan
simultáneamente durante toda la transición.

---

### Fases

| Fase | Alcance | Por qué ahí |
|---|---|---|
| **0. Sonda de realidad** | `Prober`, `ModelCard`, banco de 20 tareas, medición de Kimi K3 en Workers AI, publicación de la card | El diseño entero cuelga de números que nadie ha medido. Si el contexto útil son 20k, el bucle de 28 pasos no existe y hay que rediseñar el agente, no parchear el prompt |
| **1. Contrato mínimo** | `LLMProvider` v2, `WorkersAIProvider`, `NullProvider`, suite de conformidad, `CostMeter`, `BudgetAuthority` con leases, transporte XML con nonce, descarga al CAS, router de 2 tiers estático | Es lo que se necesita para que un agente corra una tarea con presupuesto real y contabilidad real. Todo lo demás es optimización sobre esta base |
| **2. Economía y resiliencia** | Cascada con escalada por señal objetiva, compactación, caché de respuesta, failover multi-binding, circuito, posterior Beta de fiabilidad, auditoría sombra | Requiere el banco de referencia de fase 0 para decidir con `coste/p_éxito` en vez de con intuición |
| **3. Portabilidad probada** | Segundo proveedor real (la migración es la prueba), router aprendido sobre la misma interfaz, `RerankProvider`/`VisionProvider`/`OCRProvider` si aparece call site | La portabilidad no se demuestra con tests: se demuestra migrando. Y las interfaces sin consumidor son deuda, no previsión |

---

### Alternativas descartadas

| Alternativa | Por qué se descarta | Coste de haberla elegido |
|---|---|---|
| LiteLLM / OpenRouter como abstracción | Su mínimo común denominador no expresa cancelación, presupuesto, cards medidas ni razonamiento; añade un salto de red y un punto de fallo con su propio SLA | El acoplamiento se muda a un tercero: el día de la migración depende de que ELLOS soporten el proveedor |
| Wire de OpenAI como contrato interno | No expresa breakpoints de caché, thinking, ni varios resultados de herramienta por turno | Pérdida de información permanente en la frontera; imposible activar prefix cache después |
| Capacidades declaradas en YAML a mano | Envejece en silencio; el propio repo lo demuestra con los placeholders admitidos de `COSTOS` en `packages/llm/edecan_llm/costs.py` | Shims mal activados, coste ×2-3 invisible |
| Sonda de n=30 decidiendo con el punto estimado | El IC de Wilson al 95% de 27/30 es [0.74, 0.97]: no distingue un modelo al 0.75 de uno al 0.97, que es justo la decisión que hay que tomar | Shims activados o desactivados por ruido, con la card en verde |
| Needle-in-a-haystack para el contexto útil | Recuperar una cadena literal no predice razonar sobre contexto largo, y los modelos abiertos están sobreajustados a ese test | Contexto útil sobreestimado 2×, prompts que caben pero producen basura |
| JSON como transporte de tool-calling degradado | El escape de contenido de archivo es el fallo dominante en modelos abiertos | ~15-30% de pasos con reparación |
| XML sin nonce (`</arg>` fijo) | El primer archivo que contenga `</arg>` rompe el parser, y ese archivo es el propio parser de Forge | Corrupción silenciosa de escrituras en el caso auto-referencial |
| Reintento de reparación con la misma distribución (temp 0) | Si la llamada original ya iba a temp 0, el reintento falla con probabilidad casi idéntica | Se paga una llamada extra por cada fallo sin cambiar el resultado |
| Escalada por confianza autorreportada | La confianza declarada de un modelo abierto pequeño no está calibrada; es ruido | Escala cuando no hace falta y no escala cuando hace falta |
| `judge` con 2 muestras a temp 0 sin `seed` | Da dos veces la misma respuesta; el desacuerdo nunca ocurre | 2× de coste en la clase `judge` por cero información |
| `credit_pool`, precios y cuotas dentro de `ModelCard` | Son propiedades de la cuenta, no del modelo; obligan a re-emitir una card *medida* cuando cambia una tarifa | Cards inmutables que dejan de serlo; drift de precio invalidando capacidades |
| `probe()` como método de `LLMProvider` | Cada implementación escribiría su propia sonda y las cards dejarían de ser comparables | Dos proveedores con "native_tools: 0.95" que significan cosas distintas |
| `count_tokens()` como llamada de red del proveedor | Workers AI no lo ofrece; hacer depender el presupuesto de una llamada extra por paso duplica el RTT | Presupuesto que no se puede aplicar sin gastar para saber cuánto se va a gastar |
| Ledger jerárquico con CAS serializado por llamada | El punto de serialización cruza dos objetos (sesión y workspace): transacción distribuida o carrera | Una escritura serializada global por llamada al modelo: el ledger es el cuello de botella |
| Presupuesto como contador derivado del journal | Consistencia eventual permite que 20 agentes pasen el cheque a la vez | Tope duro que no corta; 20× de sobregasto sin cota |
| `idempotency_key` con `attempt` dentro enviada al proveedor | Cambia en cada reintento, así que no deduplica nada: es un id de traza con nombre engañoso | Falsa sensación de idempotencia mientras el doble efecto real (herramientas) queda sin cubrir |
| Ejecución especulativa de tool calls durante el stream | Un stream cortado ya habría producido efectos, y el failover produce una tool call distinta que `effect_key` no atrapa | Doble escritura en disco del usuario en cada failover |
| Semáforo global de rate limit con la llamada saliendo del nodo local | Contradicción física: no hay punto de coordinación en el nodo local | Un mecanismo de protección que no protege |
| `StorageProvider`, `SandboxProvider`, `MemoryProvider`, `VectorProvider` dentro de `forge-provider` | No hablan con un servicio de inferencia; meterlos convierte este paquete en el nodo que todo el mundo importa | Viola la invariante 10 desde el primer commit |
| `RerankProvider` / `ImageProvider` / `SpeechProvider` / `audio_in` en fase 1 | Sin call site en fases 1-2 | Interfaces que se congelan mal antes de saber para qué sirven |
| Grep de AST buscando `"workers_ai"` como garantía de frontera | No ve `f"workers_{s}"`, ni tablas de config, ni `startswith("cf")` | Defensa de teatro: pasa verde el día que se rompe |
| Alias lógicos `principal`/`rápido`/`profundo` (los del `LLMRouter` actual) | Codifican tamaño, no propósito; obligan al call site a saber qué pedir | Política de routing dispersa por todo el kernel |
| Plano de datos en Containers de CF | El código vive en el disco del usuario; ejecutar en el edge exige sincronizar el árbol entero por comando | Latencia por comando de 100 ms a segundos y una factura de transferencia |
| Vectorize como único store vectorial | Sin híbrido BM25 ni filtrado rico; reindexado caro | Recuperación peor y bloqueo de proveedor en la ruta de búsqueda |
| Hedging permanente a dos proveedores | Duplica el coste por un percentil de latencia no crítico | 2× factura por ~200 ms de p95 |

---

### Cómo se rompe

1. **La card miente por drift.** El proveedor cambia el modelo detrás del alias; la card dice 0.96 y
   la realidad es 0.70. *Síntoma:* sube la tasa de reparación, sube el coste, no hay errores.
   *Detección:* posterior Beta sobre ventana de 200 llamadas reales; si `lower_95 < 0.85`, auto-demota
   con histéresis y re-sonda.
2. **Doble efecto en failover.** Se corta después de que la tool call se ejecutó. *Antídoto doble:*
   nunca se ejecuta una tool call de una generación con `complete=False`, y `effect_key =
   h(turn_id, step_index, tool_name, args_hash)` se consulta contra el journal antes de ejecutar.
   Sin la primera regla, la segunda no cubre el caso del failover a otro modelo.
3. **Gasto invisible por streams cortados.** El proveedor facturó los tokens generados pero el
   `usage` final nunca llegó. Con failover, cada fallo genera gasto fantasma. *Mitigación:* `Usage`
   estimado desde los deltas recibidos, liquidado igual con `cost_source="estimated"`. Si no, el
   ledger deriva a la baja y la reconciliación explota.
4. **Fuga de leases.** El nodo muere entre `acquire` y la devolución; el presupuesto se consume con
   fantasmas hasta bloquear al workspace. *Mitigación:* TTL de lease = deadline + 60 s, barrido por
   alarm, alerta si los leases caducados superan el 2% del gasto.
5. **Sobregasto acotado por diseño.** Los leases permiten `sesiones_activas × lease_size` por encima
   del tope. Con 20 agentes y lease de 5 USD son 100 USD. *No es un fallo: es el precio declarado de
   no serializar cada llamada.* Se ajusta bajando `lease_size` a costa de más escrituras.
6. **Colisión de nonce en el transporte XML.** El contenido del archivo contiene el nonce.
   *Mitigación:* verificación explícita post-parseo; si aparece, se rechaza y va a reparación con
   nonce nuevo. Probabilidad ~2^-32 por llamada, pero es exactamente el caso adversarial cuando el
   agente escribe tests del propio transporte.
7. **Argumento parcial ejecutado.** El nivel 2 del parser rescata una llamada truncada y el ejecutor
   escribe medio archivo. *Mitigación:* `partial=True` prohíbe cualquier herramienta con efectos; solo
   lecturas idempotentes.
8. **Caché envenenada.** Un juicio cacheado se reutiliza tras cambiar el código o el modelo.
   *Mitigación:* la clave incluye los hashes CAS de todo lo referenciado **y** `card.revision`; si
   algún bloque es `volatile`, no se cachea. La regla es ejecutable porque cada `ContentBlock` lleva
   `provenance`.
9. **Cascada que falla en silencio.** El tier S produce algo plausible y equivocado en una tarea que
   no era suya. *Mitigación:* escalada solo por señal objetiva, y la decisión de cascada se toma con
   `coste/p_éxito` medido en el banco de referencia, nunca con USD por llamada.
10. **Contexto útil sobreestimado.** El modelo anuncia 128k, colapsa al 40%, y el needle decía que
    estaba bien. *Mitigación:* la métrica es compositiva multi-salto, no recuperación literal; el
    router presupuesta contra `useful_context * context_factor(task)` y compacta antes de enviar.
11. **Tormenta de rate limit.** Los 20 agentes chocan contra el RPM de la **cuenta** (no del modelo)
    y todos bloquean. *Mitigación:* leases de tasa emitidos por el plano de control en el caso
    multi-nodo, bucket en proceso en el caso de un nodo, circuito por `binding_id` con half-open y
    una sola sonda.
12. **Semántica de Durable Objects filtrada al kernel.** Alguien usa `storage.transaction()` o el
    modelo de alarms desde fuera de `JournalStore`/`BudgetAuthority`. Es el fallo más caro porque no
    da síntoma hasta el día de la migración. *Mitigación:* contrato de dependencias declaradas en CI
    y suite de conformidad del `JournalStore` corriendo también sobre Postgres en cada build.
13. **Deriva de precio.** `cost_usd` diverge de la factura por cambio de tarifa de neuronas.
    *Mitigación:* `forge providers reconcile` mensual (procedimiento manual, declarado como tal),
    alerta a 5%, re-sonda de `units_per_1k_*`.
14. **Nodo local sin plano de control gastando sin tope.** *Mitigación:* `offline_allowance` de 2.00
    USD por workspace; agotado, el nodo hace checkpoint y para.
15. **Bifurcación de sesión por partición.** Dos nodos escriben la misma sesión offline.
    *Mitigación:* lease de escritor por sesión; sin lease vigente el trabajo va a una sesión forkeada
    con `forked_from` y la fusión es explícita. La divergencia se previene, no se resuelve.

---

### Riesgos aceptados

1. **`useful_context` como escalar.** La degradación depende de la tarea y un solo número es tosco.
   Se acepta porque el `context_factor` por `TaskClass` cubre el 80% del efecto a coste cero, y
   modelar una curva por tarea exigiría una sonda 10× más cara sin un consumidor que la use.
2. **Sobregasto acotado por leases** (riesgo 5). Se acepta explícitamente a cambio de eliminar una
   escritura serializada global por llamada. La cota es configurable y publicada.
3. **Reconciliación de facturación manual.** Mientras Workers AI no exponga API de uso, es un
   comando que un humano corre una vez al mes. Se acepta y se documenta como tal, en vez de
   disfrazarlo de mecanismo automático.
4. **La sonda cuesta dinero real y puede quedarse obsoleta entre corridas.** Se acepta porque el
   posterior de producción la corrige de forma continua; la sonda solo tiene que ser buena como
   prior.
5. **Cuatro tiers con dos o tres modelos disponibles.** Filas duplicadas en una tabla de datos. Se
   acepta: el coste es cero y evita una migración de esquema el día que aparezca el cuarto modelo.
   La configuración valida que al menos S y L existan.
6. **`temperature=0.3` en la reparación reduce la reproducibilidad del replay.** Se acepta: la
   reparación se registra en el journal con su salida, y el replay reproduce el evento, no la
   llamada.
7. **`RerankProvider`, visión y OCR diferidos a fase 3.** Si aparece un caso de uso de navegador
   antes, habrá que diseñarlo a destiempo. Se acepta: congelar hoy una interfaz sin consumidor sale
   más caro que diseñarla tarde.

---

## 8. UI y observabilidad

### 8.1 Tesis: una sola cosa, vista dos veces

Si el agente escribe el código, el humano deja de necesitar un editor y pasa a necesitar
tres capacidades: **entender qué está pasando** con retraso acotado y sin ambigüedad,
**intervenir barato** —corregir sin destruir el trabajo hecho— y **aprobar rápido** sin
firmar a ciegas. Todo panel que no responda a una de esas tres preguntas es decoración y
se corta.

La consecuencia arquitectónica es que UI y observabilidad no son dos sistemas: son dos
consumidores del mismo journal. La UI es la proyección síncrona de baja latencia; la
observabilidad es la proyección asíncrona y agregada. Ninguna tiene estado autoritativo,
y las acciones humanas —aprobar, corregir, retroceder— entran al sistema como **eventos
del journal de primera clase**, no como mutaciones. Eso es lo que hace que «qué hizo el
agente ayer a las 4» y «qué está haciendo ahora» sean la misma consulta con distinto
cursor.

Hay **una excepción declarada** a la invariante 2, y se declara aquí en vez de descubrirse
en producción: cuando el journal está caído, la UI no puede saberlo leyendo el journal.
Existe un canal de salud fuera de banda (§8.9) que es la única lectura que la UI hace sin
pasar por una proyección. Todo lo demás pasa.

---

### 8.2 El contrato de proyección

Se separan explícitamente tres roles que el diseño ingenuo mezcla y que, mezclados,
destruyen el determinismo:

- **Projector** — puro, por evento, sin reloj ni red. Produce deltas.
- **Materializer** — persiste estado y snapshots, decide cadencia, sirve hidratación.
- **Fanout** — por conexión, dependiente del tiempo. Conflaciona, presupuesta y degrada.

```python
S = TypeVar("S")

class Projector(Protocol[S]):
    name: str                       # "agent_tree", "changeset_review", "approval_queue"
    state_schema_version: int       # cambia -> el cliente DEBE rehidratar
    apply_version: int              # cambia -> rebuild server-side; cliente sigue
    scope: Literal["session", "workspace", "tenant"]

    def initial(self, scope_id: str) -> S: ...
    def apply(self, state: S, ev: JournalEvent) -> tuple[S, list[ProjectionDelta]]: ...
    def encode(self, state: S) -> bytes: ...    # JSON canónico (RFC 8785), determinista

class ProjectionDelta(BaseModel):
    op: Literal["set", "merge", "remove", "append", "append_summary"]
    path: str                 # JSON Pointer; ver regla de mapas keyed
    value: Any | None
    cursor: ProjectionCursor  # monótono en la proyección, NO en el journal
    coalesced: int = 0        # nº de deltas fusionados; solo lo pone el Fanout
    dropped: int = 0          # nº de elementos NO representados (solo append_summary)
```

**Reglas duras del `apply`**, todas verificables:

1. `apply` no recibe reloj, ni RNG, ni cliente HTTP, ni acceso al CAS. Se ejecuta con un
   entorno inyectado que no los expone. Todo dato temporal viene dentro del `JournalEvent`.
2. **Prohibidos los arrays en cualquier path que reciba `set`/`remove`.** El estado usa
   mapas con clave estable (`/agents/{agent_id}`) y un campo `order` separado. Un `remove`
   sobre `/agents/3` desplaza índices y corrompe a cualquier cliente con un delta de
   diferencia; ese bug aparece con 20 agentes y jamás con uno.
3. `encode` es **JSON canónico** (claves ordenadas, sin NaN/Inf, enteros no float). Sin
   esto dos procesos producen hashes distintos del mismo estado y el CAS deja de deduplicar
   y de verificar.
4. El test de CI no compara solo incremental contra rebuild: ejecuta el rebuild **en otro
   proceso, con `PYTHONHASHSEED` aleatorio**, y compara `encode(state)` byte a byte. La
   versión ingenua del test (mismo proceso) es vacua: no detecta orden de sets ni de dicts
   heredado de la sesión.

**Coalescencia como álgebra, no como buena intención.** El `Fanout` puede fusionar deltas
solo si la operación es asociativa por path: `set` y `merge` lo son (último gana, con
`coalesced` contando los absorbidos). `append` **no** lo es: fusionar N `append` en uno
pierde elementos. Por eso existe `append_summary`, que declara `dropped: N` y un
`blob_ref` al detalle completo en el CAS. Un cliente que recibe `append_summary` sabe que
tiene un agujero y sabe dónde está el relleno. El diseño original, que ponía `coalesced`
sobre un `append` genérico, es ambiguo: dos implementadores leen «fusionados» y
«descartados» y ninguno está equivocado.

**Cadencia de snapshot por coste, no por conteo.** «Snapshot cada 1.000 eventos» es
correcto para `session_timeline` y ruinoso para `cost_ledger` con `scope="tenant"`, cuyo
estado crece sin techo: serializar 200 MB cada 1.000 eventos quema más CPU que el sistema
entero. Regla: se materializa cuando `coste_estimado_de_rebuild > 2 × coste_de_snapshot`,
con dos cotas duras — nunca más de 5.000 eventos sin snapshot, nunca un snapshot con menos
de 200 eventos de separación. `cost_ledger` y demás proyecciones de tenant usan además
**cubetas temporales** (`/2026-07-27/...`) para que el snapshot sea incremental por día.

**Hidratación.** Un cliente nunca replica 800 eventos al conectarse. `GET
/v2/forge/projections/{name}/{scope_id}/snapshot` devuelve `{blob_ref, cursor,
state_schema_version}`; el blob es inmutable y cacheable por hash en CDN; el cliente abre
el SSE con `Last-Event-ID: <cursor>` y recibe únicamente el delta. Si el snapshot más
reciente está a más de 500 eventos del HEAD, el servidor materializa uno bajo demanda
antes de responder, con un techo de 800 ms; superado, responde `202` con reintento.

---

### 8.3 Cursores, orden y transporte

**El cursor del stream no es el cursor del journal.** Es la ambigüedad más cara del diseño
original y se resuelve así: el journal puede estar particionado en shards por workspace, y
`(shard, seq)` no es un orden total. El **Projector es el punto de serialización**: consume
de N shards, aplica una regla de merge determinista (por `(event_ts_hlc, shard_id, seq)`,
con reloj lógico híbrido en el evento) y asigna un `proj_seq` monótono y denso. El `id:` del
frame SSE es ese `proj_seq`. Consecuencia práctica: `Last-Event-ID` funciona sin lógica de
cliente, y un evento que llega tarde de un shard rezagado no se inserta en el pasado —
produce un delta correctivo con `proj_seq` nuevo. Es la única forma de tener reanudación
trivial sobre un log particionado.

| Opción | Reconexión/backfill | Bidireccional | Coste operativo | Veredicto |
|---|---|---|---|---|
| **SSE + cursor de proyección** | Nativo (`Last-Event-ID`) | No | Bajo, atraviesa CDN/proxy | **Elegido** para proyecciones |
| WebSocket | Manual (reimplementar orden, dedupe, backfill en 4 clientes) | Sí | Medio (sticky sessions, hibernación) | Solo PTY y take-over de navegador |
| Long-poll con cursor | Trivial | No | 1 RTT por lote, N conexiones | Fallback degradado, solo TUI sobre proxies hostiles |

**Una sola conexión SSE por cliente, multiplexada.** El diseño de «una conexión por panel»
choca con un límite real que el original ignoraba: HTTP/1.1 permite 6 conexiones por origen,
y con 10 paneles abiertos el usuario se queda sin canal para las escrituras. El cliente abre
**un** stream y envía `subscribe`/`unsubscribe` por POST; cada frame lleva
`{p: "<projection>", s: "<scope_id>", d: [...deltas]}`.

**Presupuesto por cliente, con prioridad.** 300 mensajes/s y 512 KB/s **totales**, repartidos
por el Fanout: 60% al panel enfocado (el cliente reporta `focus`), el resto proporcional. Al
excederse, la proyección afectada pasa a modo resumen y marca `degraded: true`; la UI muestra
un badge honesto («emisión reducida · 4.310 eventos agrupados») en vez de mentir por omisión.

**Frontera de payload**: nada mayor de 8 KB viaja inline; se sustituye por `blob_ref`
(`cas://sha256-...`). El cliente mantiene un anillo de 2.000 deltas por proyección; el
histórico se pagina.

**Escrituras**: siempre `POST` idempotente con `Idempotency-Key`. El canal de lectura nunca
transporta comandos. Además, toda escritura que actúe sobre un objeto revisable lleva
`base_cursor`; el servidor responde `409 stale` si el objeto avanzó (§8.6).

**Expiración de credencial en streams largos.** Un SSE de 4 horas con un JWT de 15 minutos
no puede seguir vivo. El servidor emite `auth.expiring` 60 s antes del vencimiento; el
cliente renueva por el flujo que ya existe en `apps/web/src/lib/session-refresh.ts` y hace
`Last-Event-ID` sobre la nueva conexión. Sin esto la UI «se congela» misteriosamente
justo en las sesiones largas, que son las que importan.

---

### 8.4 Superficies

| Panel | Pregunta que responde | Proyección | Latencia objetivo | Cardinalidad / techo |
|---|---|---|---|---|
| Árbol de agentes/tareas | Quién trabaja, en qué, bloqueado por qué | `agent_tree` | p95 < 300 ms | 200 nodos vivos; colapso automático |
| Timeline de eventos | Qué hizo, en orden, con qué coste | `session_timeline` | p95 < 500 ms | virtualizado, 60 filas en viewport |
| **Diff review** | Qué va a cambiar y qué riesgo tiene | `changeset_review` | primer pintado p95 < 1,5 s | 400 archivos / 20k líneas; techo duro 5.000 archivos |
| Terminal multiplexada | Qué dijo el proceso de verdad | `terminal` (WS) | < 80 ms de eco | 8 PTY simultáneos |
| Previews en vivo | Funciona lo que hizo | `preview` (URL efímera del sandbox) | — | 1 por workspace |
| Navegador embebido | Qué vio y tocó el agente en la web | `browser_flipbook` | — | 1 screenshot por acción |
| **Inspector de contexto** | Qué recibió el modelo, literal | `context_frame` | < 400 ms | 1 por turno |
| Consola de herramientas | Qué hay, cuánto tarda, cuánto falla | `tool_console` | — | catálogo + 200 invocaciones |
| Cola de aprobaciones | Qué espera mi firma | `approval_queue` | push < 3 s | única superficie con SLA móvil |
| Tablero de costes | Cuánto llevo quemado y cuánto queda | `cost_ledger` | — | agregado por día, nunca por evento |
| Estado del sistema | Qué está roto | `system_health` | — | planos, colas, sandboxes, proveedor |
| Árbol de archivos | Dónde está X | `file_tree` | — | **lazy por directorio**; 500k archivos jamás viajan |
| Editor de código | (secundario) tocar una línea concreta | `file_view` | — | sin LSP hasta fase 3 |

El editor es deliberadamente pobre: la edición humana genera un changeset sobre el
workspace copy-on-write y pasa por el mismo registro que la del agente. Si escribe directo
al filesystem, el replay diverge y el journal miente.

**La UI no conoce herramientas por nombre.** Prohibido el `switch` sobre `tool_name` en el
frontend: eso sería lógica de dominio en un cliente del kernel, en contra de la invariante
10. Cada plugin declara en su manifiesto un `render_hint` (`{layout: "diff" | "table" |
"log" | "image" | "kv", fields: [...], summary_template: "..."}`) y la UI solo implementa
los layouts. Herramienta desconocida → layout `kv` genérico, nunca una excepción.

**Ausencia de datos calientes.** Si el rango de cursores pedido fue compactado o movido a
almacenamiento frío, la proyección responde `cursor_range_cold` con el rango afectado y una
acción de restauración. Nunca se pinta un timeline con huecos silenciosos.

---

### 8.5 Inspector de contexto

Es el panel que decide si el sistema es depurable. Con una ventana modesta y tool-calling
poco fiable, la causa raíz mayoritaria está en el ensamblado del prompt, no en el
razonamiento.

```python
class ContextFrame(BaseModel):
    frame_id: str; turn_id: str; attempt: int          # los reintentos NO se sobrescriben
    provider: str; model: str; model_params_hash: str
    request_body_hash: str      # CAS de los bytes que EMITIÓ el adaptador
    wire_bytes: int
    stored_form: Literal["verbatim", "masked"]
    masked_ranges: list[tuple[int, int]] = []
    wire_digest: str            # digest de los bytes reales, calculado antes de enmascarar
    wire_format: Literal["chat_messages", "text_completion"]
    tokenizer_id: str
    total_tokens: int; budget_tokens: int
    tokens_estimated: bool
    token_error_p95: float | None   # medido por el adaptador; None = desconocido
    segments: list[ContextSegment]
    evicted: list[EvictedSegment]

class ContextSegment(BaseModel):
    segment_id: str
    kind: Literal["system","instruction","memory","file","tool_result",
                  "summary","message","tool_schema","scratch"]
    source_ref: str                  # cas://... | journal://<event_id>
    reason: SelectionReason
    tokens: int
    byte_range: tuple[int, int]      # offset dentro del cuerpo almacenado
    order_index: int
    truncated: bool
    provenance_chain: list[str]

class SelectionReason(BaseModel):
    strategy: Literal["pinned","explicit_mention","retrieval_topk",
                      "recency","tool_schema_required","policy_injected"]
    score: float | None; rank: int | None; rule_id: str | None

class EvictedSegment(BaseModel):
    candidate_ref: str; kind: str; tokens_would_cost: int
    reason: Literal["budget_exceeded","lower_score","stale",
                    "dedup_identical","policy_blocked"]
    score: float | None
```

Cuatro correcciones sobre la versión ingenua, todas con consecuencias:

1. **Honestidad de nombre.** No es «lo que vio el modelo»: es **lo que emitió el adaptador**.
   El proveedor puede prefijar un system propio, normalizar o truncar del lado servidor, y
   nadie del lado cliente puede verificarlo. Llamarlo `rendered_hash` invita a creer una
   garantía que no existe. `request_body_hash` dice la verdad.
2. **Los reintentos son frames distintos.** Un reintento con prompt recortado que
   sobrescribe el frame original es exactamente cómo se pierde la evidencia del fallo.
   `attempt` es parte de la identidad.
3. **El CAS de prompts es un depósito de secretos.** Este es el agujero que el diseño
   original no nombraba: aunque el journal guarde `secret_ref`, el cuerpo renderizado es
   *post-sustitución*; si un resultado de herramienta arrastró un token, queda en el CAS en
   claro, para siempre y direccionable. Regla: los cuerpos de prompt van a un **namespace CAS
   separado**, cifrado en reposo con clave por tenant, con TTL propio (por defecto 30 días,
   configurable), y pasan por `RedactionPolicy` **en escritura**. Lo almacenado puede ser
   `masked`; se guarda también `wire_digest` de los bytes reales para poder demostrar
   integridad sin conservar el texto. La UI etiqueta `MASKED` y pinta los `masked_ranges`.
4. **El ±8% inventado se sustituye por medición.** El adaptador mantiene un error rodante
   comparando su estimación contra el `usage` reportado por el proveedor y publica
   `token_error_p95`. Si el proveedor no reporta nada —caso real de Workers AI en algunos
   modelos— el error es `None` y el motor de presupuesto aplica un **multiplicador
   conservador de 1,25×** en vez de un porcentaje ficticio. Un número sin esa marca envenena
   la contabilidad de presupuesto y, con 50.000 USD de crédito, la contabilidad *es* el
   producto.

La UI **no reconstruye** el prompt: descarga el blob y pinta los `byte_range` encima. Si el
hash no verifica, la vista se marca `UNVERIFIED` y se niega a presentarse como fiel.
`evicted[]` importa tanto como `segments[]`: la pregunta real casi siempre es «por qué NO vio
el archivo X».

---

### 8.6 Diff review a escala

La unidad de revisión no es el archivo: es el `hunk_group`. Pipeline determinista, sin LLM en
el camino crítico.

**1. Agrupación por intención — asignada por el kernel, no por el modelo.** El diseño original
pedía al agente adjuntar un `intent_id` a cada escritura. Kimi K3 lo olvidará o lo reutilizará
para todo, y entonces la agrupación se degrada sin avisar. Corrección: el `intent_id` **es el
`plan_step_id` vigente en la máquina de estados del Agent Runtime**, adjuntado automáticamente
por la herramienta de escritura desde el contexto ambiente del turno. El agente puede aportar
una etiqueta legible, nunca la identidad. Cero dependencia de fiabilidad del modelo.

**2. Clasificación determinista.**
- `generated`: lockfiles, snapshots, `dist/`, migraciones autogeneradas — por globs de
  `review_policy.yaml`; se aprueban en bloque.
- `mechanical`: (a) movimientos puros por **igualdad de hash de contenido** (gratis, primera
  pasada); (b) renombrados por similitud ≥ 0,9 mediante *buckets* simhash, nunca comparación
  todos-contra-todos —25M de pares con 5.000 archivos de un codemod es inaceptable—; (c)
  reformateo, detectado porque el diff desaparece tras normalizar; (d) el mismo hunk
  normalizado repetido en N archivos colapsa a un grupo «aplicado en 37 sitios». **Por encima
  de 2.000 archivos cambiados se salta la fase de similitud** y se clasifica por rutas, con
  `classification: "degraded"` visible.
- `semantic`: el resto, agrupado por `intent_id`.

**3. Riesgo determinista por archivo.**

```
risk = 0.30*blast_radius_norm + 0.25*sensitivity + 0.20*coverage_delta
     + 0.15*capability_touch  + 0.10*churn_anomaly        →  risk_band ∈ {low,medium,high,critical}
```

`sensitivity` sale de globs (auth, pagos, migraciones, CI, infra, secretos) y es el único
componente obviamente correcto. `capability_touch` marca código que ejerce capacidades
peligrosas. `blast_radius_norm` se calcula sobre un grafo de dependencias **a nivel de
archivo**, incremental y cacheado en el CAS por hash del árbol; para lenguajes sin parser
disponible el valor es **`unknown`, no 0**, contribuye 0 al score y marca el grupo como
`partial_signal`. Puntuar como «seguro» lo que no se pudo analizar es la forma más común de
que un sistema de riesgo mienta.

**Los pesos son una hipótesis con criterio de refutación, no una verdad.** `review_policy.yaml`
está versionado, y hay una métrica de calibración: `P(revert | critical) / P(revert | low)`.
Si tras 200 changesets ese cociente no supera 3, el modelo no tiene señal y se **desactivan las
bandas**, dejando solo `sensitivity`. Una banda de riesgo sin poder predictivo es peor que
ninguna: produce confianza falsa.

**4. SLA partido.** Primer pintado —clasificación, riesgo, lista de archivos, cacheado por
`changeset_hash`— p95 < 1,5 s para 400 archivos / 20k líneas. Los resúmenes por grupo llegan
en streaming y **nunca bloquean**. Los genera el modelo barato sobre el diff normalizado; si
no hay presupuesto o el proveedor falla, cae a resumen estructural por AST con los grammars
de tree-sitter que tengamos (~10 lenguajes), y si el lenguaje no está soportado, a un resumen
puramente textual (símbolos por regex, conteo de hunks, archivos tocados). La revisión nunca
se bloquea por falta de LLM ni por falta de parser.

**5. Techo declarado.** Changesets con más de 5.000 archivos o 500k líneas cambiadas no se
revisan hunk a hunk: producen un informe estructural y exigen política `bulk_approval` con
justificación. Es mejor decir «esto no se puede revisar» que fingir que se revisó.

**6. Aprobación parcial y concurrencia.** Aprobar un subconjunto de `hunk_group` produce un
changeset **nuevo derivado**; lo rechazado vuelve al agente como `review.rejected` con motivo
estructurado. El changeset original nunca se muta. Y —hueco grave del diseño original— toda
aprobación lleva `base_cursor` y `changeset_hash`: si el agente siguió trabajando y el objeto
avanzó, el servidor responde `409 stale_review` con el diff de lo que cambió desde que se
abrió la revisión. Sin esto, dos pestañas o dos revisores firman un diff que ya no existe, en
silencio.

**7. Honestidad medida.** `blind_approval_ratio` = líneas aprobadas / líneas realmente en
viewport más de 400 ms, alimentada por eventos `review.viewport_sample` (muestreo a 2 Hz,
coalescado). Es **advisoria y nunca bloquea**: es telemetría reportada por el cliente y por
tanto falsificable. Se usa para avisar y para agregados, jamás como control de seguridad.

---

### 8.7 Interrupción sin matar

```python
class InterventionCommand(BaseModel):
    kind: Literal["steer", "redirect", "rewind", "pause", "cancel"]
    target: AgentRef | TaskRef | TurnRef
    apply_at: Literal["immediate", "next_tool_boundary", "next_turn"] = "next_tool_boundary"
    payload: str | None = None
    rewind_to: ProjectionCursor | None = None
    on_current_branch: Literal["keep_running", "pause", "cancel"] = "pause"
    acknowledge_irreversible: list[str] = []   # ids de IrreversibleEffect
    base_cursor: ProjectionCursor
```

- **`steer`** inyecta un mensaje humano. Los steers **se acumulan en una cola ordenada** y se
  entregan todos juntos como un `human_steer_batch` en el siguiente punto de interrupción.
  Nunca se descarta uno: si dos correcciones llegan con 10 ms de diferencia y el agente
  consume solo la última, el humano ve desaparecer su instrucción sin explicación. Objetivo:
  < 500 ms hasta que el agente lo ve, cuando hay frontera disponible.
- **Los puntos de interrupción son una propiedad de la herramienta, no una promesa temporal.**
  Prometer «un `interrupt_point` obligatorio cada 30 s» es incumplible: un `pytest` de 90 s no
  se puede interrumpir por dentro. El Tool ABI declara `interruptible: bool` y
  `checkpointable: bool`; la UI presenta la verdad y dos opciones explícitas: «se aplica al
  terminar `pytest` (~62 s)» o «cancelar la herramienta ahora». Una cuenta atrás honesta
  produce paciencia; una promesa incumplida produce que el humano mate la tarea.
- **`redirect`** invalida el plan actual, conserva workspace y contexto. Emite `task.redirected`.
- **`rewind` es un fork, y hay que decir qué pasa con la rama viva.** Crea una rama de sesión
  desde el cursor con un **workspace CoW nuevo**; no hereda sandboxes, PTY ni URLs de preview.
  La rama nueva arranca **en pausa**. `on_current_branch` decide el destino de la original, y
  por defecto la pausa: dejarla corriendo por omisión produce dos agentes escribiendo con la
  misma intención y nadie entendiendo cuál ganó.
- **Restauración exacta y su límite.** Rewind = snapshot CAS más cercano + replay determinista
  de los eventos `workspace.write` hasta el cursor. Si algún blob intermedio fue recolectado,
  ese cursor **no es alcanzable**: el sistema conoce y expone el `earliest_rewindable_cursor`
  y la UI deshabilita el resto del timeline en vez de fallar a mitad de la restauración.
- **Efectos irreversibles: fail-safe por defecto.** El Tool ABI declara `effects:
  list[EffectClass]`; `tool.completed` lleva `irreversible_effects`. Una herramienta que **no
  declara** se considera **irreversible**. El default optimista contrario convierte el rewind
  en una trampa que parece segura. Antes de confirmar, la UI lista los efectos entre el cursor
  y HEAD y exige reconocerlos: el código vuelve, el correo enviado no.
- **`pause`/`cancel`**: pause congela el presupuesto y mantiene el sandbox N minutos; cancel
  coopera hasta el deadline y luego mata, emitiendo siempre `turn.cancelled` con checkpoint.

---

### 8.8 Tecnología y hosts

Dos paquetes, no uno, porque «framework-agnóstico y escrito en React» es una contradicción:

- **`packages/forge-protocol`** — tipos TS y cliente de proyección (`ProjectionClient`), cero
  dependencias de UI. Los tipos se **generan en CI desde los modelos Pydantic**; una copia
  escrita a mano diverge en dos meses, y el repo ya tiene la evidencia en
  `apps/web/src/lib/types.ts`. La generación falla el build si hay drift.
- **`packages/forge-ui-client`** — la SPA React/TS con todas las superficies pesadas, montada
  como ruta de `apps/web` con client components: el estado en vivo no gana nada con RSC.

Los hosts aportan **capacidades, no pantallas**: navegador; `apps/local` (Tauri, ya existente)
que añade PTY local, acceso a filesystem, atajos globales y notificaciones nativas; TUI
(`forge watch`, `forge approve`) como cliente del mismo SSE para SSH. **La extensión de VS Code
se aplaza a fase 3** y se condiciona a que alguien la pida: es un tercer ciclo de release y no
enseña nada que no enseñen los otros dos.

**Móvil** (Kotlin/Swift existentes) implementa tres superficies: cola de aprobaciones, árbol de
agentes y timeline resumida. Nunca el diff completo; sí el resumen por grupo con banda de riesgo.
El push reutiliza el `deeplink` que ya existe en `apps/worker/edecan_worker/push.py`.

**La regla de «critical no se aprueba desde móvil» se reescribe como política de evidencia,
verificada en servidor.** Como regla por tipo de cliente era teatro —el mismo humano abre el
navegador del teléfono y firma igual— y además un riesgo de disponibilidad el día que hace
falta una aprobación urgente. Forma correcta: `approval_policy` por tenant y banda exige
`review_evidence` firmada `{rendered_hunk_group_ids, surface_class, viewport_lines,
elapsed_ms}`. El móvil no puede producirla para un `critical` porque no renderiza el diff; un
navegador que sí lo renderiza, pasa. La regla expresa lo que de verdad significa: no firmes lo
que no se te mostró.

---

### 8.9 Observabilidad

**Los spans se derivan del journal**, con un `JournalOtelExporter`; `trace_id`/`span_id` viajan
dentro de cada evento. Nunca hay instrumentación paralela: dos fuentes divergen siempre.
Taxonomía: `forge.session` > `forge.turn` > {`forge.llm_call`, `forge.tool_call` >
`forge.sandbox_exec`, `forge.workspace_op`, `forge.approval_wait`}. `forge.approval_wait` existe
porque en un sistema que funciona el cuello de botella es el humano, y hay que poder demostrarlo.

**Spans huérfanos.** Si un proceso muere a mitad de herramienta, el evento de cierre no llega
nunca y el span queda abierto para siempre — el modo de fallo natural de derivar trazas de un
log en un sistema que se cae por diseño. El proyector emite un `span.abandoned` sintético
cuando expira el *lease* del actor emisor (TTL 60 s), con `status = UNKNOWN` y la causa.

**Canal de salud fuera de banda.** Si el journal está caído, la UI no puede enterarse leyendo el
journal. `GET /v2/forge/health` es la **única** lectura de la UI que no pasa por una proyección:
devuelve estado del journal, de las colas, del proveedor y del plano de datos, servido por un
camino que no depende del journal. Los logs estructurados (JSON con `trace_id`, `session_id`,
`event_seq`) son telemetría de operador, no fuente de verdad: **la UI tiene prohibido leer logs**.

Métricas (labels de baja cardinalidad; `session_id`/`task_id` van en exemplars, jamás como label;
lista blanca de labels validada en CI):

| Métrica | Tipo | Objetivo / definición operativa |
|---|---|---|
| `forge.time_to_first_diff` | histogram ms | p50 < 90 s |
| `forge.task_autonomy_ratio` | gauge | tareas cerradas con 0 `human.steer` |
| `forge.cost_per_task_usd` | histogram | por modelo y tipo de tarea |
| `forge.revert_rate` | ratio | changesets revertidos < 7 días |
| `forge.risk_calibration` | ratio | `P(revert\|critical)/P(revert\|low)`; objetivo ≥ 3 |
| `forge.tools_per_task` / `forge.tool_error_rate` | histogram / ratio | por herramienta |
| `forge.context_waste_ratio` | ratio | ver definición abajo |
| `forge.blind_approval_ratio` | ratio | advisoria; < 0,6 |
| `forge.tokens_per_accepted_line` | histogram | eficiencia económica real |
| `forge.projection_rebuild_ms` | histogram | p95 < 2 s por sesión del corpus |
| `forge.unverified_context_ratio` | ratio | < 0,001 (§8.12, fallo 3) |

**`context_waste_ratio` necesita una definición o no es una métrica.** Un segmento cuenta como
*referenciado* si (a) la salida del modelo o algún argumento de herramienta del turno contiene
una secuencia literal de ≥ 12 tokens del segmento (comparación por huellas Rabin), o (b) es el
esquema de una herramienta efectivamente invocada. Sesgo declarado: la paráfrasis cuenta como
desperdicio, así que la métrica **sobreestima**. Es útil como serie temporal comparada consigo
misma, no como valor absoluto. Sin esta definición, dos implementadores producen números
incomparables y la métrica es decorativa.

**«Qué hizo el agente ayer a las 4» en < 30 s**: índice `(ts, scope_id, proj_seq)` sobre el
journal; `GET /v2/forge/journal/at?ts=...&scope=...` resuelve timestamp → cursor en p95 < 200 ms;
la UI abre el timeline en ese cursor e hidrata el workspace desde el snapshot CAS más cercano en
< 2 s. Encima, un digest diario precomputado hace que la consulta típica sea una sola lectura;
los digests se **clavan a `(scope_id, fecha, apply_version)`, son inmutables y se recalculan
perezosamente si faltan**. Un digest mutable es una caché con problema de invalidación disfrazada
de optimización.

**Atención y notificaciones.** Con 20 agentes, algo tiene que decidir qué despierta al humano.
Política mínima: aprobaciones pendientes agrupadas por workspace en una sola notificación;
escalado cuando `forge.approval_wait` de una tarea supera 15 min; silenciamiento horario por
usuario; y una regla dura — si **todos** los agentes de un workspace están bloqueados esperando
firma, se notifica siempre, ignorando el silenciamiento. Un sistema que se para en silencio es
indistinguible de uno roto.

---

### 8.10 Registro obligatorio por turno

```python
class TurnRecord(BaseModel):          # proyección, no evento; siempre < 8 KB
    turn_id: str; session_id: str; agent_id: str; parent_turn_id: str | None
    seq_range: tuple[int, int]; trace_id: str
    started_at: datetime; ended_at: datetime; duration_ms: int
    provider: str; model: str; model_params_hash: str
    context: ContextFrameRef                 # request_body_hash + tokens + n_segments + attempt
    thinking: BlobRef | None                 # CAS, sujeto a RedactionPolicy
    output: BlobRef                          # respuesta literal del modelo
    tool_calls: list[ToolCallRecord]         # name, args_hash, result_ref, ms, ok, effects
    files_changed: list[FileChangeRef]       # path, before_hash, after_hash, +/-
    commands: list[CommandRecord]            # argv, cwd, exit_code, ms, stdout_ref
    usage: TokenUsage                        # in/out/cached/reasoning + estimated
    cost_usd: Decimal
    cost_source: Literal["provider_reported", "computed_from_price_table"]
    price_table_version: str | None
    capabilities_used: list[str]
    budget_after: BudgetSnapshot
    outcome: Literal["completed","tool_error","model_error","cancelled",
                     "budget_exhausted","awaiting_approval"]
    interventions: list[InterventionRef]
    tenant_id: str                           # toda proyección se autoriza por tenant
```

`files_changed` está acotado a 200 entradas; por encima, se sustituye por un `blob_ref` al
listado completo más un contador, para que la cota de 8 KB sea real y no una aspiración.

Los secretos nunca entran al CAS en claro: el journal guarda `secret_ref`, y los cuerpos que
pueden contenerlos por sustitución (§8.5) van al namespace cifrado con TTL. `thinking` y
`output` pasan por `RedactionPolicy` antes de servirse a un cliente con menos capacidad (móvil
compartido, pantalla proyectada).

**Aislamiento multi-tenant, explícito.** Toda suscripción a proyección se autoriza contra
`(tenant_id, scope_id, capability)`; el modelo RLS de Postgres que ya usa Edecán se hereda tal
cual. Las lecturas del CAS por la API se autorizan contra un índice de propiedad
`(tenant_id, blob_hash)`: un hash filtrado no es una capacidad de lectura. La contrapartida —la
deduplicación es intra-tenant— se acepta explícitamente en §8.13.

---

### 8.11 Evaluación continua

`forge eval capture --session S --from <cursor> --to <cursor>` —o el botón «convertir en test»
del timeline, que es lo que hace que alguien lo use— produce un `EvalCase`: instrucción inicial,
capacidades otorgadas, hash del árbol de workspace inicial y cassettes de todas las respuestas de
herramientas y del modelo.

**Las cassettes son punteros con pin, no copias.** Referencian los blobs del CAS que ya existen y
les ponen un `pin` que impide al recolector borrarlos; heredan la política de redacción del
tenant. Duplicar gigabytes de stdout por cada caso capturado hace que la captura sea cara y por
tanto que no se use.

**El replay determinista exige un kernel determinista, y eso hay que construirlo en fase 1 o no
existirá nunca.** El kernel recibe `Clock` e `IdGen` como capacidades inyectadas; en replay son
deterministas. Las aserciones comparan un journal **normalizado** con timestamps e identificadores
proyectados fuera. Sin esto, el test de replay está rojo el día 2 y borrado el día 3: es la deuda
de fase 1 más previsible de todo el bloque.

- **`replay_deterministic`**: modelo y herramientas grabados. Verifica que el kernel no cambió.
  Segundos, bloqueante en CI.
- **`replay_live`**: solo herramientas grabadas, modelo real. Mide regresión de prompt/modelo.
  Nocturno, informativo, **obligatorio al cambiar de proveedor** — exactamente el escenario del
  día en que se agoten los 50.000 USD de crédito.

Aserciones sobre la proyección final, nunca sobre texto: `files_changed_contains`, `tests_pass`,
`no_capability_escalation`, `cost_under_usd`, `turns_under`, `no_human_intervention`,
`diff_equals_normalized(hash)`.

**Contra el sesgo del corpus**: la captura de `EvalCase` es **automática y obligatoria** para todo
turno con `outcome != "completed"`, todo changeset revertido en < 7 días y todo `human.steer` que
preceda a un `task.redirected`. Un corpus formado solo por sesiones que salieron bien da verde y
oculta la regresión.

---

### 8.12 Fases

| Fase | Alcance | Por qué ahí |
|---|---|---|
| **1** | `ProjectionClient` + SSE multiplexado + cursor de proyección; `session_timeline`, `context_frame`, `approval_queue` mínima, `terminal` (WS), `TurnRecord`; `Clock`/`IdGen` inyectados; test de rebuild determinista | Es el conjunto mínimo para depurar un agente real. El inspector de contexto va en fase 1 porque sin él no se entiende por qué falla Kimi K3, y el determinismo del kernel porque retrofitearlo es imposible |
| **2** | `changeset_review` completo (clasificación, riesgo, aprobación parcial con `base_cursor`), `agent_tree`, `rewind`/fork con efectos irreversibles, `cost_ledger`, `JournalOtelExporter`, captura de `EvalCase` + `replay_deterministic` en CI | Todo esto solo tiene sentido cuando ya hay changesets grandes y más de un agente. Antes es especulación |
| **3** | Móvil (3 superficies), `tool_console`, `browser_flipbook`, `preview`, `file_view` con LSP, digests nocturnos, extensión VS Code, `replay_live` como gate de proveedor, calibración de riesgo | Superficies de conveniencia y de escala. La extensión de VS Code entra solo si alguien la pide |

---

### Alternativas descartadas

| Alternativa | Por qué se descartó | Coste de haberla elegido |
|---|---|---|
| UI con store propio y CRUD contra tablas de dominio | Segunda fuente de verdad; cualquier divergencia con el journal es indetectable | Replay, fork y auditoría dejan de ser fiables; se descubre tarde porque con un agente todo parece coherente |
| Proyectar el journal crudo en el navegador | 50k eventos en JS son segundos, y hay que rehacerlo en web, móvil, TUI y VS Code con semánticas divergentes | Cuatro verdades y bugs de sincronización intermitentes |
| Una conexión SSE por panel | HTTP/1.1 limita a 6 conexiones por origen; con 10 paneles el cliente se queda sin canal de escritura | Fallos aleatorios e irreproducibles al abrir «un panel de más» |
| Cursor = cursor del journal `(shard, seq)` | No es orden total sobre un log particionado; `Last-Event-ID` es un escalar | Reanudación rota con multi-workspace, con pérdida o duplicación silenciosa de deltas |
| `coalesced` sobre `append` genérico | Ambiguo entre «fusionados» y «descartados»; dos implementadores divergen | Terminal que pierde líneas sin decirlo, que es la peor forma de perderlas |
| Arrays en el estado de proyección | `remove` desplaza índices y corrompe clientes desincronizados | Corrupción de UI que aparece con 20 agentes y jamás en desarrollo |
| Snapshot cada N eventos fijo | Ruinoso para proyecciones de tenant con estado grande | CPU del plano de control quemada en serializar lo mismo mil veces |
| Inspector que reconstruye el prompt desde metadatos | Responde «qué creemos que vio», no «qué se envió» | Semanas depurando una ficción; evals irreproducibles |
| Guardar el prompt renderizado verbatim sin namespace ni TTL | El CAS se convierte en el mayor depósito de secretos del sistema, permanente y direccionable | Incidente de fuga sin plan de borrado, porque el CAS es inmutable por diseño |
| `intent_id` aportado por el agente | Depende de la fiabilidad de un modelo débil; se degrada en silencio | Agrupación de diffs inservible justo cuando el changeset es grande |
| Riesgo y agrupación por LLM | No determinista y se cae cuando falta presupuesto | Revisión inconsistente entre ejecuciones; imposible de auditar |
| Pesos de riesgo fijos sin calibración | Astrología con decimales | Confianza falsa: peor que no mostrar banda |
| Similitud todos-contra-todos para renombrados | O(n²): 25M de pares con un codemod de 5.000 archivos | La revisión tarda minutos exactamente cuando más urge |
| «Prohibido aprobar critical desde móvil» por tipo de cliente | Teatro: el navegador del teléfono lo esquiva; y bloquea aprobaciones urgentes | Falsa sensación de control más un incidente de disponibilidad |
| `interrupt_point` garantizado cada 30 s | Incumplible dentro de una herramienta bloqueante de 90 s | El humano concluye que la UI está rota y mata la tarea |
| Efectos irreversibles solo si la herramienta los declara | Default optimista: lo no anotado parece seguro | Rewind que parece limpio tras haber enviado correos o hecho un push |
| Instrumentar OTel en el kernel en paralelo al journal | Dos verdades que divergen | Investigaciones que terminan en «los datos no cuadran» |
| Un solo `packages/forge-ui-client` «framework-agnóstico» en React | Contradicción; y los tipos escritos a mano derivan (ya pasó con `apps/web/src/lib/types.ts`) | Móvil y TUI reimplementan la semántica y aparecen tres verdades |
| Extensión de VS Code en fase 1 | Tercer ciclo de release sin aprendizaje diferencial | Meses de mantenimiento antes de saber si alguien la usa |
| Cassettes de eval como copias de los blobs | Gigabytes por caso; capturar sale caro y deja de hacerse | Corpus vacío el día que hay que migrar de proveedor |
| Editar el journal para «rehacer» un turno | Viola append-only y destruye la auditoría | Pérdida irrecuperable de trazabilidad |

---

### Riesgos aceptados

1. **Deduplicación del CAS solo intra-tenant.** Dos tenants con el mismo `package-lock.json`
   lo almacenan dos veces. Se acepta: el almacenamiento es barato y la fuga entre tenants no
   lo es. Coste estimado: < 15% de sobre-almacenamiento con la mezcla de repos prevista.
2. **`blind_approval_ratio` es telemetría de cliente y por tanto falsificable.** Se acepta
   porque es advisoria; no se usará jamás como control de seguridad, y esa restricción está
   escrita en el contrato, no solo en la intención.
3. **La fórmula de riesgo puede no tener poder predictivo.** Se acepta con criterio de muerte
   explícito (`forge.risk_calibration ≥ 3` tras 200 changesets); si falla, se degrada a
   `sensitivity` por globs.
4. **Las proyecciones consumen CPU del plano de control.** En un Durable Object de Cloudflare
   hay techos de CPU por invocación; las proyecciones caras (`changeset_review`) se calculan
   en el plano de datos y solo su resultado entra al journal. Se acepta el salto de latencia.
5. **Los bytes almacenados del prompt pueden estar enmascarados**, así que el inspector no
   siempre muestra el literal exacto. Se acepta a cambio de la integridad verificable vía
   `wire_digest` y de no mantener un depósito permanente de secretos.
6. **El digest diario es una optimización con dependencia de un cron.** Si no corre, la
   consulta cae al camino lento (< 30 s en vez de < 3 s). Se acepta: degrada, no rompe.
7. **`context_waste_ratio` sobreestima** (la paráfrasis cuenta como desperdicio). Se acepta
   como serie temporal comparada consigo misma, nunca como valor absoluto ni como SLO.
8. **Cada escritura es una petición HTTP separada** al no usar WebSocket bidireccional para
   proyecciones. Se acepta: las escrituras son a ritmo humano; el único caudal alto real es el
   PTY, que ya va por WS.

---

### Cómo se rompe

1. **Tormenta de eventos.** Un proceso emite 30k líneas en 4 s. Sin conflación el cliente
   encola, el heap crece y la pestaña muere; al reconectar pide backfill y vuelve a morir.
   Mitigación: conflación algebraica + `append_summary` con `dropped` + presupuesto por cliente
   + `degraded: true` visible. Síntoma temprano: `coalesced` creciendo sin techo.
2. **Deriva de proyección.** Se cambia `apply` sin subir versión y los clientes con
   materialización vieja pintan un árbol de agentes que no existe. Mitigación:
   `state_schema_version` en el handshake con rehidratación forzada, `apply_version` para
   rebuild server-side sin cortar al cliente, y ventana de drenaje sirviendo ambas versiones
   —el corte duro provoca una estampida de rehidratación justo durante el despliegue.
3. **Contexto no verificable.** El ensamblador reporta un hash que no corresponde a lo emitido
   (reintento con prompt distinto, truncado del proveedor). La UI marca `UNVERIFIED`; si
   `forge.unverified_context_ratio` supera 0,1%, el sistema deja de ser depurable y hay que
   parar el despliegue.
4. **Aprobación sobre un objeto muerto.** El humano revisa mientras el agente sigue escribiendo
   y firma un changeset que ya cambió. Mitigación: `base_cursor` + `409 stale_review` con el
   diff de lo que se movió. Sin esto es corrupción silenciosa, no un error.
5. **Aprobación ciega.** El humano firma 400 archivos en 20 s; la reversión aparece días
   después en producción. Mitigación: `blind_approval_ratio`, orden por riesgo descendente y
   política de evidencia server-side.
6. **Rewind con efectos irreversibles o con blobs recolectados.** Se retrocede antes de un
   correo enviado, o el snapshot necesario ya no está. Mitigación: reconocimiento explícito de
   `IrreversibleEffect` (irreversible por defecto si no se declara) y
   `earliest_rewindable_cursor` expuesto antes de confirmar.
7. **Doble rama viva tras un rewind.** La rama original sigue corriendo y dos agentes persiguen
   la misma intención. Mitigación: `on_current_branch = "pause"` por defecto y workspace CoW
   nuevo, sin herencia de sandbox ni de PTY.
8. **Steer perdido.** Dos correcciones seguidas y el agente consume solo la última. Mitigación:
   cola ordenada entregada como `human_steer_batch`; ningún steer se descarta.
9. **Interrupción que llega tarde.** El agente está en una herramienta de 90 s no interrumpible;
   el humano concluye que la UI está rota y mata la tarea. Mitigación: mostrar la verdad
   —«se aplica en ~62 s» o «cancelar la herramienta»— en vez de prometer un intervalo.
10. **Spans que nunca cierran.** El proceso muere a mitad de herramienta y el APM se llena de
    spans abiertos. Mitigación: `span.abandoned` sintético al expirar el lease (60 s).
11. **Journal caído.** La UI no puede diagnosticarlo leyendo el journal. Mitigación: `/health`
    fuera de banda, la única lectura de la UI que no es una proyección.
12. **Cardinalidad de métricas.** Alguien mete `session_id` como label y el backend explota en
    coste. Mitigación: lista blanca de labels validada en CI.
13. **Journal frío.** El rango pedido fue compactado o movido a almacenamiento frío. Mitigación:
    `cursor_range_cold` explícito con acción de restauración; jamás un timeline con huecos que
    parecen «no pasó nada».
14. **Rebuild lento.** Sin snapshots adecuados, reconstruir 200k eventos tarda minutos y «ayer a
    las 4» incumple su SLA. Mitigación: cadencia por coste y `forge.projection_rebuild_ms` como
    test de tiempo en CI.
15. **Sesgo del corpus de evaluación.** Solo se capturan las sesiones que salieron bien; la
    suite da verde y el modelo nuevo es peor. Mitigación: captura automática obligatoria de todo
    `outcome != "completed"`, todo revert y todo steer que preceda a un redirect.
16. **Fuga por hash.** Un `cas://` filtrado en un log o una captura de pantalla concede lectura
    cross-tenant. Mitigación: autorización `(tenant_id, blob_hash)` en toda lectura por API; el
    hash no es una capacidad.

---

## 9. Verificación, aceptación y control de cambios

Paquete: `packages/forge-verify/edecan_forge_verify/`. Recibe por inyección `JournalClient`,
`CasStore`, `ToolPlane`, `WorkspaceManager`, `CapabilityIssuer` y `PredicateRegistry`. No importa
`edecan_forge_runtime` (invariante 10): la relación es al revés — el runtime consume este bloque.

### 9.0 Este bloque NO define un vocabulario nuevo

El borrador de esta sección inventaba `Clause`, `ClauseKind`, `ClauseVerdict`, `clause.verdict` y un
módulo `VerdictAuthority`. El bloque 6 ya había pinneado `Criterion`, `CriterionResult`,
`Goal.acceptance`, `criterion.evaluated`, `guard_set`/`guard_hash`/`tainted`, `watch_set`, el estado
`verifying` y la regla de que un criterio se evalúa como *tool call* normal por el `ToolPlane` con
`purpose="verification"`. Dos vocabularios para lo mismo son dos implementaciones, dos bugs de
reanudación y dos contabilidades de presupuesto. **Se retira el vocabulario nuevo entero.**

Lo que este bloque aporta, y es exactamente el hueco que el bloque 6 dejó abierto:

| El bloque 6 dejó | El bloque 9 lo cierra |
|---|---|
| `Criterion.spec: dict[str, Any]` sin esquema | `VerificationSpec`: la unión tipada que va dentro, congelada por `spec_hash` |
| `kind="predicate"` sin decir qué es un predicado | `PredicateRegistry`: predicados con nombre, versión y JSON Schema, provistos por plugins |
| "un objetivo sin criterio verificable no se admite" sin decir de dónde salen los criterios | El **Contrato de Aceptación**: línea base congelada del perfil + propuesta del planificador + aprobación |
| "`judge` nunca basta en solitario" sin decir cómo se ejecuta un juez | Perfil `auditor` con contexto amputado, citas verificadas y degradación a `human` |
| "el modelo no autocertifica" como intención | Un invariante de datos del journal, comprobable por un tercero (§9.2) |
| Nada sobre repos sin tests | La escalera de sustitutos y el estado honesto `unverifiable` (§9.7) |

Dos reglas gobiernan el resto.

**Regla 1 — el ejecutor nunca declara.** No por desconfianza moral: por conflicto estructural. Un
modelo que acaba de generar 400 líneas argumentando por qué son correctas es el peor evaluador
posible de esas 400 líneas, porque su contexto contiene la narrativa que las justifica.

**Regla 2 — "terminado" se escribe antes.** Un criterio redactado a posteriori se acomoda al
resultado; uno redactado a priori revela la ambigüedad cuando cuesta una pregunta, no un rollback.
El bloque 6 ya lo impone parcialmente (`spec_hash` congelado en `plan.published`); aquí se completa
con quién lo escribe y cuánto cuesta escribirlo.

---

### 9.1 `VerificationSpec`: lo que va dentro de `Criterion.spec`

El enum de `Criterion.kind` del bloque 6 —`command | predicate | artifact_exists | judge | human`—
**queda cerrado para siempre**. El borrador proponía cinco *kinds* nuevos (`invariant`, `empirical`,
`data_state`…) y un `Literal` cerrado de seis métricas. Eso convierte "quiero comprobar el p99 de una
cola" en editar un enum del núcleo y recompilar, justo lo contrario de la tesis del proyecto. La
extensibilidad vive en `predicate`, y un predicado es **dato + plugin**.

```python
# edecan_forge_verify/spec.py
PredicateId = str          # "<ns>.<nombre>@<major>"  p.ej. "metric.threshold@1"
CriterionId = str
SnapshotId  = str          # el del bloque 2 (CAS), NO un commit de git

class Tier(StrEnum):
    GUARD  = "guard"       # p95 <= 1.5 s, se dispara por escritura
    CHEAP  = "cheap"       # p95 <= 45 s, debounced sobre watch_set
    FULL   = "full"        # solo en la puerta `verifying`
    JUDGED = "judged"      # solo si todo FULL está en PASS

class VerificationSpec(BaseModel, frozen=True):
    tier: Tier
    enforcement: Literal["blocking", "advisory"] = "blocking"
    origin: Literal["baseline", "planner", "human"] = "planner"
    frozen: bool = False              # baseline del perfil: el planificador ni la ve
    hermetic: bool = False            # sin red, sin servicios vivos, sin reloj -> cacheable
    read_set: list[str] = []          # globs que el criterio LEE; base de la clave de caché
    resource_keys: list[str] = []     # "port:5432", "docker.daemon" -> claims (bloque 4)
    budget: Budget                    # wall_ms, usd_micros, tokens, bytes_out
    body: CommandBody | PredicateBody | ArtifactBody | JudgeBody | HumanBody

class CommandBody(BaseModel, frozen=True):
    kind: Literal["command"] = "command"
    argv: list[str]; cwd: str; expect_exit: int = 0
    parser: str = "exitcode"          # id de parser de plugin: "junit@1", "coverage_xml@1", "tap@1"
    floor: CollectionFloor | None = None

class CollectionFloor(BaseModel, frozen=True):
    """Anti "verde por vacío". Los números se PINNEAN, no se comparan contra un base móvil."""
    collected_min: int
    skipped_max: int
    pinned_at: SnapshotId             # solo se re-pinnea vía contract.amended

class PredicateBody(BaseModel, frozen=True):
    kind: Literal["predicate"] = "predicate"
    predicate: PredicateId
    args: dict                        # validado contra el args_schema del predicado
    baseline: SnapshotId | None = None

class JudgeBody(BaseModel, frozen=True):
    kind: Literal["judge"] = "judge"
    profile: str                      # perfil de agente (bloque de perfiles)
    rubric_ref: BlobRef
    evidence_from: list[CriterionId]  # el juez SOLO ve la evidencia de estos criterios
    min_score: int                    # 0..10, entero
```

**Sin floats en ninguna parte.** El bloque 1 lo prohíbe en todo el sistema y el borrador lo violaba
tres veces (`patch_coverage: float`, `baseline_diff: float`, `min_score: 4`). Toda métrica de este
bloque es `int`: cobertura y porcentajes en **puntos básicos** (`6000` = 60,00 %), dinero en
`micro_usd`, tiempos en `ms`. Un veredicto que no es reproducible bit a bit entre macOS y Linux no es
un veredicto.

#### El `PredicateRegistry`

```python
class PredicateRunner(Protocol):
    id: PredicateId
    args_schema: dict                 # JSON Schema 2020-12, perfil portable (bloque 4 §4.2)
    hermetic: bool
    resource_keys: list[str]
    async def evaluate(self, args: dict, ctx: VerifyCtx) -> PredicateOutcome

class PredicateOutcome(BaseModel, frozen=True):
    status: Literal["pass", "fail", "error"]
    score: int | None                 # menor es mejor; alimenta Criterion.score del bloque 6
    metrics: dict[str, int]           # enteros; se proyectan a la UI y a las series temporales
    evidence: list[BlobRef]
    detail: str                       # <= 512 chars, lo único que puede llegar al modelo
```

Un `PredicateRunner` es un `ToolHandler` del ABI del bloque 4 con `destructive="none"`,
`reversibility="reversible"` y una capacidad de solo lectura sobre `read_set ∪ guard_set`. No hay un
segundo camino de ejecución (invariante 6). Predicados de fase 1, deliberadamente pocos:

| `PredicateId` | Qué decide | Coste |
|---|---|---|
| `metric.threshold@1` | `{source, metric, op, value}` contra un extractor de métrica registrado | el del extractor |
| `secrets.absent@1` | entropía + regex de credenciales sobre las **líneas añadidas** | < 300 ms |
| `symbols.stable@1` | tabla de símbolos públicos (AST) inalterada vs. `baseline` | < 2 s |
| `deps.declared@1` | toda dependencia nueva del lockfile está en una allowlist o exige aprobación | < 500 ms |
| `process.healthy@1` | levanta un `ServiceSpec`, espera health, mata por lease | 5–40 s |
| `http.probe@1` | request + aserciones sobre status/headers/JSONPath | < 5 s |

Fase 2 añade `db.migrations@1` (forward + rollback contra Postgres efímero **con RLS activo**, nunca
SQLite) y `db.query@1`. Fase 3 añade `ui.observe@1`. Añadir "comprobar el p99 de una cola" es
publicar un plugin con `id="queue.p99@1"` y escribir una línea de YAML — no tocar este bloque.

---

### 9.2 Que el modelo mienta es un imposible sintáctico, y se demuestra con un `grep`

El borrador resolvía esto con un módulo nuevo, `VerdictAuthority`, "que vive en el núcleo
confiable". El núcleo confiable de la invariante 6 son seis cosas y ninguna es ésta; y el bloque 5 ya
tiene el motor que ejecuta y liquida (`ExecutionEngine.settle -> Settlement`). Un séptimo módulo de
núcleo es alcance inventado. La propiedad se obtiene sin código nuevo, con dos piezas que ya existen:

1. **ACL de escritor en el `TypeDescriptor`** (bloque 1 §1.5, campo añadido):

```python
class TypeDescriptor(BaseModel, frozen=True):
    ...
    writable_by: frozenset[Literal["human","agent","kernel","plugin","provider","scheduler"]]
```

`criterion.evaluated.writable_by = {"kernel"}`. Un `Event` con `actor.kind == "agent"` y
`type == "criterion.evaluated"` lo rechaza el adaptador de journal antes del append, igual que ya
rechaza un tipo `reserved`.

2. **Un invariante de datos del journal**, comprobable por un tercero sin ejecutar Forge, en el mismo
   registro que los invariantes de §1.2:

> Todo evento `criterion.evaluated` tiene `causation_id` no nulo apuntando a un
> `tool.call_completed` o `tool.call_failed` del mismo journal con `purpose="verification"` y
> `spec_hash` idéntico al del criterio. Un `criterion.evaluated` huérfano es corrupción.

Eso es todo. El verificador de journals (`forge journal verify`) lo comprueba en una pasada lineal.
**El coste de equivocarse aquí es catastrófico y no tiene mitigación posterior**, así que hay dos
tests obligatorios en CI desde el día uno, en el estilo de los guardianes del bloque 6 §6.9:

- **Estructural (AST)**: ninguna función fuera de `edecan_forge_verify.settle` construye un
  `EventDraft` con `type="criterion.evaluated"`. Ese *grep* es lo que cierra el agujero: prohíbe
  escribir el camino paralelo antes de que exista.
- **Adversarial**: un agente de prueba intenta emitir el evento por los cuatro caminos plausibles
  (append directo, plugin, `tool_result` con `facts` que la proyección pudiera leer como veredicto,
  y `contract.amended` que reescriba `spec_hash`); los cuatro deben fallar tipados.

El agente sí puede emitir `acceptance.claimed`. Es una **afirmación**, no un veredicto, y su único
efecto es mover el estado a `verifying`. Un `acceptance.claimed` sin `criterion.evaluated` para cada
criterio `required` se rechaza estructuralmente: ni siquiera llega al juez, y no cuesta un token.

---

### 9.3 El Contrato de Aceptación, y su coste real de escritura

```python
class AcceptanceContract(BaseModel, frozen=True):
    goal_id: GoalId
    version: int
    task_class: Literal["trivial", "standard", "guarded"]
    criteria: list[CriterionId]        # los Criterion viven en el Goal (bloque 6); esto es el set
    limits: ChangeLimits
    effect_scopes: list[EffectScope]   # efectos irreversibles declarados; ver §9.6
    approval: Literal["policy", "human"]
    approved_by: Actor | None
    contract_hash: Hash                # b3(CBOR canónico); congela el set, no solo cada spec

class ChangeLimits(BaseModel, frozen=True):
    authored_source_files: int = 25
    net_source_lines: int = 800
    counted_classes: frozenset[str] = frozenset({"source"})   # PathClass del bloque 2 §2.6
```

**La crítica que más duele: nadie va a escribir esto.** Un contrato que cuesta "30-90 s y una posible
pregunta al humano por tarea" mata el producto, porque Forge no recibe tickets: recibe conversación
desde un asistente general. "Renombra esta variable" y "¿por qué falla esto?" son la mayoría del
tráfico. Corrección: **tres clases de tarea con coste de contrato radicalmente distinto**, decididas
por política determinista antes de invocar al planificador.

| Clase | Umbral (estimado del planificador, verificado por capacidad) | Contrato | Coste añadido | Aprobación |
|---|---|---|---|---|
| `trivial` | ≤ 3 ficheros `source`, ≤ 40 líneas netas, sin `effect_scopes`, sin tocar API pública | **solo la línea base congelada del perfil** | 0 tokens, 0 ms (es YAML estático) | ninguna |
| `standard` | ≤ 25 ficheros, sin efectos irreversibles | base + criterios propuestos por el planificador | ~600 tokens, ~4 s | política; el humano lo ve, no lo bloquea |
| `guarded` | efectos irreversibles, migraciones, API pública, > 250 líneas, o límites ampliados | base + propuesta + revisión | ~600 tokens + espera humana | `contract.approved` explícito |

La pieza que hace esto viable es que **el contrato de una tarea trivial no está vacío: es la línea
base del perfil**, un archivo YAML que ya existe y no cuesta ni un token generar. Build, lint, tipos,
`secrets.absent`, `symbols.stable` y los límites de cambio se aplican siempre. La diferencia entre
clases es cuánto *añade* el planificador, no si hay red.

**Línea base congelada.** `origin="baseline"`, `frozen=True`. No entra en el contexto del
planificador —no puede debilitar lo que no ve— y `contract.amended` la rechaza. Ejemplo real del
perfil `forge.dev.python`:

```yaml
# profiles/forge.dev.python.yaml -> acceptance.baseline
baseline:
  - id: build
    kind: command
    tier: guard
    spec: {argv: ["uv","run","ruff","check","--output-format","json","."], parser: "ruff@1"}
  - id: types
    kind: command
    tier: cheap
    spec: {argv: ["uv","run","mypy","--output","json","{touched}"], parser: "mypy@1"}
  - id: secrets
    kind: predicate
    tier: guard
    spec: {predicate: "secrets.absent@1", args: {scope: "added_lines"}}
  - id: api.stable
    kind: predicate
    tier: cheap
    spec: {predicate: "symbols.stable@1", args: {roots: ["packages/*/edecan_*/__init__.py"]}}
limits: {authored_source_files: 25, net_source_lines: 800}
```

Y lo que el planificador añade para una tarea `standard` concreta:

```yaml
task: "Paginar GET /v1/contactos"
proposed:
  - id: tests.affected
    kind: command
    tier: cheap
    spec:
      argv: ["uv","run","pytest","--junitxml=r.xml","{selected}"]
      parser: "junit@1"
      floor: {collected_min: 7, skipped_max: 0, pinned_at: "snap_01J..."}
  - id: coverage.patch
    kind: predicate
    tier: full
    spec: {predicate: "metric.threshold@1",
           args: {source: "coverage_xml@1", metric: "patch_coverage_bp", op: ">=", value: 6000}}
  - id: api.contrato
    kind: predicate
    tier: full
    spec: {predicate: "http.probe@1",
           args: {service: "api", path: "/v1/contactos?page=2", assert: {status: 200,
                  jsonpath: {"$.meta.page": 2}}}}
  - id: ux.mensaje_error
    kind: judge
    tier: judged
    enforcement: advisory
    spec: {profile: "forge.auditor", rubric_ref: "b3:9f…", evidence_from: ["api.contrato"],
           min_score: 7}
```

**Frontera máquina/juicio, por subsidiariedad.** Si existe un comando o un predicado que lo decide,
el juez no opina. El orden de preferencia lo fijó el bloque 6: `command > predicate >
artifact_exists > judge > human`. Formalmente: un criterio `judge` cuyo `title` sea reescribible como
`command` es un defecto del contrato, y el aprobador de política lo degrada automáticamente a
`enforcement="advisory"` — porque rechazarlo pararía la tarea por un problema de redacción.

**Enmiendas.** Debilitar es posible, caro y visible: `contract.amended{diff_ref, reason}` con el diff
del contrato renderizado al humano, y **siempre** un `contract.weakened` adicional si baja un
`floor`, sube un umbral o pasa un criterio de `blocking` a `advisory`. La métrica "criterios
debilitados por tarea" se publica por perfil: un planificador que se pone metas fáciles se ve en una
serie temporal, no en una anécdota.

---

### 9.4 Un solo disparador, cuatro tiers de presupuesto

El borrador definía cuatro niveles (V0…V3) con disparadores propios. El bloque 6 ya tiene el
disparador: *"un criterio se re-evalúa en cuanto un evento toca su `watch_set`"*. Dos planificadores
sobre los mismos criterios es doble ejecución y doble facturación. Corrección: **el disparador es
uno**; `tier` es un atributo del criterio que decide *elegibilidad* y *presupuesto*, no un motor.

```
evento que toca watch_set(c)  ─┬─ tier=guard   → ejecutar ya, sin debounce
                               ├─ tier=cheap   → encolar, debounce 8 s / 5 escrituras
                               ├─ tier=full    → marcar stale; se ejecuta al entrar en `verifying`
                               └─ tier=judged  → marcar stale; solo tras todos los FULL en PASS
```

| Tier | p95 objetivo | Tokens | Al fallar |
|---|---|---|---|
| `guard` | 1,5 s | 0 | nota tipada en el siguiente `tool_result`; **nunca bloquea en `executing`** |
| `cheap` | 45 s | 0 | `criterion.evaluated{status:"fail"}`; señal de progreso, no corta |
| `full` | 10 min agregado | 0 | impide `agent.completed`; el bloque 6 lleva a `planning` (replan) o `awaiting_approval` |
| `judged` | 3 min | ≤ 20k por rol | `advisory` por defecto en fase 1-2 |

**`guard` NUNCA revierte la edición.** El borrador decía "revierte la edición y se lo dice al
agente". Es el error más peligroso del texto original, por dos razones. (a) **Pérdida de datos**: un
refactor multi-fichero está roto por construcción entre la línea 1 y la línea N —renombras la
definición y las llamadas quedan colgando—, y un auto-revert lo hace literalmente imposible. (b)
**Livelock**: con un modelo débil, escribir → revertir → reescribir igual es un bucle que consume el
presupuesto entero y no aparece en ningún detector. Revertir es `rollback.requested`, lo pide el
supervisor o el humano, y siempre deja evento. El guard produce una anotación, no una acción.

**Cómo se ejecuta barato.** Tres mecanismos, en orden de rendimiento:

- **Selección por diff.** El `TestSelector` consume la proyección del grafo de código (bloque 2
  §2.7.5) y expande transitivamente los tests que alcanzan los símbolos tocados. Degrada a suite
  completa si: el diff alcanza > 15 % del grafo, algún módulo está marcado opaco (`importlib`, DI,
  fixtures dinámicas), o **el `snapshot_id` de la proyección del índice no coincide con el del
  worktree** — un índice retrasado es exactamente cómo la selección produce un falso verde.
- **Caché de veredictos.** La clave del borrador (`tree_hash + toolchain + env + argv`) tiene dos
  defectos fatales. Primero, `tree_hash` del workspace entero: en un monorepo, tocar un fichero
  invalida todos los veredictos, y el objetivo de 70 % de acierto es inalcanzable por construcción.
  Segundo, omite la `DependencyLayer` (bloque 2 §2.4), que **no está en el CAS del workspace**: dos
  `npm install` con el mismo lockfile y distinto `installer_version` producen árboles idénticos y
  binarios distintos. Clave corregida:

  ```
  verdict_key = b3( criterion.spec_hash ‖ subtree_hash(read_set ∪ guard_set)
                    ‖ deplayer_key ‖ toolchain_digest ‖ env_digest ‖ abi_version )
  ```

  **Solo se cachea si `hermetic=True`.** Un criterio que toca red, reloj o un servicio vivo no es
  cacheable y declararlo cacheable es mentir. Un veredicto servido de caché nace igualmente como
  `criterion.evaluated` nuevo con `from_cache=true` y `evidence` apuntando al blob original: la
  caché es proyección, jamás fuente de verdad (invariante 2).
- **Suite completa periódica** cada 10 checkpoints o 30 min, como red contra la selección.

**Presupuesto, y qué pasa al agotarlo.** Techo del 20-35 % del wall clock objetivo de la tarea y
≤ 20 % de los tokens, cap duro al 40 %; se contabiliza en el mismo `BudgetLease` del bloque 7, no en
un contador aparte. El borrador decía "degrada a V1 muestreado y lo anuncia": eso es un falso verde
con aviso, y el aviso lo lee nadie. Corrección: al superar el cap se **omiten los criterios
`advisory`** y, si aún no alcanza, el agente entra en `awaiting_approval` con
`assistance.requested{kind:"decision", options:[ampliar presupuesto, aceptar sin verificar, abortar]}`.
Un criterio `blocking` no se salta nunca; se pide permiso para no ejecutarlo, que es distinto.

---

### 9.5 Verificación independiente: un rol en fase 1, no cuatro

El borrador definía QA, Reviewer y Security como perfiles separados, con un Reviewer que arbitra el
desacuerdo entre developer y QA. Tres objeciones:

1. **`Security` no es un juez.** Lo que de verdad encuentra secretos filtrados, dependencias nuevas y
   sinks de deserialización son `secrets.absent@1`, `deps.declared@1` y una consulta AST. Convertir
   eso en una opinión de K3 es cambiar certeza por ruido. En fase 1 `security` **desaparece como rol**
   y se convierte en tres predicados deterministas de la línea base.
2. **El `Reviewer` como árbitro es autocontradictorio.** El propio borrador afirma "nunca se resuelve
   por mayoría de modelos: tres instancias del mismo modelo no son tres opiniones", y acto seguido
   pone una tercera instancia del mismo modelo a arbitrar. Con un solo proveedor no hay tercera
   opinión. Corrección: hallazgo del auditor + refutación del developer → **humano**, siempre, con
   ambos textos y la evidencia citada. Si hay un segundo proveedor configurado, el árbitro corre en
   él y solo entonces existe el rol (fase 3).
3. Queda **un** rol adversarial, `forge.auditor`, un perfil declarativo sobre el runtime único.

| | Ve | No ve | Se convoca cuando | Presupuesto |
|---|---|---|---|---|
| `forge.dev.*` | tarea, contrato, workspace, herramientas de escritura | veredictos de otros agentes | siempre | base |
| `forge.auditor` | diff, contrato, `evidence` de los criterios `full` | razonamiento, transcripción ni plan del developer | todos los `full` en PASS **y** (diff > 80 líneas `source` **o** toca API pública) | ≤ 20k tokens |

El aislamiento es **política de contexto del perfil, no cortesía**: el auditor nace con un contexto
nuevo cuyo `ContextQuery` (bloque 3) tiene prohibido el stream del developer salvo los eventos
`criterion.evaluated` y el diff. Se comprueba con un test: correr el auditor y verificar que
`RenderedContext.blocks` no contiene ningún bloque con `provenance.agent == developer_id`.

**Citas verificadas, a prueba de K3.** El bloque 7 dice que no hay JSON estricto garantizado. Por eso
la validación de citas no depende de parsear JSON: los `evidence_id` son ULID-26, se extraen por
regex del texto libre y se intersectan con el conjunto real de `BlobRef` que se le entregó. Reglas:

- ≥ 1 id citado que **no** está en el conjunto entregado → el veredicto se **descarta entero**, no se
  reintenta hasta que salga bien. Un juez que alucina evidencia en un hallazgo no es fiable en los
  otros.
- 0 ids citados → descartado igual.
- Dos descartes consecutivos → el criterio degrada a `kind="human"` y emite
  `contract.weakened{reason:"judge_unreliable"}`.

**Colusión.** El riesgo real cuando developer y auditor son el mismo K3 es que el auditor ratifique.
Mitigaciones, y una corrección estadística importante:

- Prompt adversarial cuya métrica declarada es *defectos confirmados*, no aprobaciones.
- Contexto amputado (arriba).
- **Calibración por mutantes, pero como suite offline, no como impuesto por ejecución.** El borrador
  proponía 1 de cada 8 verificaciones con un mutante y un umbral de 0,6. Distinguir 0,6 de 0,5 con
  intervalo razonable pide ~100 ensayos; a 1 de cada 8 y 30 verificaciones/día son 25 días por
  medición, y el bloque 7 ya argumenta que 30 muestras no bastan. Corrección: `forge verify calibrate`
  corre **60 mutantes en un lote** (invertir condición, quitar `await`, borrar validación, off-by-one,
  intercambiar argumentos del mismo tipo) contra el perfil auditor; ~15 min y ~1,20 USD; se dispara
  al cambiar el perfil, el modelo o el proveedor. Publica `qa_sensitivity` **con intervalo de Wilson
  al 95 %**. Inyección en banda: 1 de cada 32, solo como detector de deriva, **nunca como puerta**.
- Si `qa_sensitivity` cae por debajo de 0,6 (límite superior del intervalo), el veredicto del auditor
  pasa a `advisory` en todo el tenant y se avisa. No se escala a humano automáticamente: eso convierte
  un problema de calibración en una avalancha de interrupciones.

---

### 9.6 Control de cambios: snapshots del CAS, no commits de git

El borrador definía el checkpoint como "commit en un worktree dedicado" y lo justificaba con que
"git da direccionamiento por contenido y una herramienta de bisección conocida". **El bloque 2 midió
`git worktree add` en 20-90 s con 500k inodos y lo descartó explícitamente**; el fork canónico es
copiar un hash de 32 bytes en 3 ms. Reintroducir git como almacén es deshacer la decisión más cara
del bloque 2.

```python
class Checkpoint(BaseModel, frozen=True):
    checkpoint_id: str
    snapshot: SnapshotId              # el árbol Merkle del bloque 2
    parent: str | None
    journal_seq: int
    deplayer_key: Hash                # sin esto, restaurar el árbol no restaura el entorno
    build_state: Literal["green", "red", "unknown"]
    passing: frozenset[CriterionId]   # qué estaba verde EN ESE PUNTO
```

`deplayer_key` es la corrección menos vistosa y la que evita el bug más confuso: volver al snapshot
de hace 40 pasos con `node_modules` de ahora produce fallos que no corresponden a ningún código.
Rollback: `rollback.requested(checkpoint_id)` → el worktree apunta al `snapshot` → `rollback.applied`.
O(1), es mover un puntero. La legibilidad humana no se pierde: `forge export --git <checkpoint>`
produce un repo git de verdad para que el humano use `git log`/`git bisect` si quiere. Exportar es
barato y opcional; almacenar en git es caro y obligatorio.

**Bisección.** El `Bisector` corre búsqueda binaria sobre la cadena usando como oráculo el criterio
que falla: 40 checkpoints → ≤ 6 ejecuciones. Se **niega a correr** si el oráculo está en cuarentena
por flaky (§9.8) o si `hermetic=False`, y lo dice, en vez de acusar a un checkpoint inocente.

**Límites de cambio, con la corrección que evita el odio del usuario.** "25 ficheros y 800 líneas"
como universal duro es falso: un codemod, un cliente generado, una actualización de lockfile o un
`ruff format` tocan legítimamente 300 ficheros. Corrección: solo cuentan ficheros con
`PathClass == "source"` **escritos por el agente**; `generated`, `vendored`, `data`, `lfs` y los
resultados de una herramienta declarada `formatter` no cuentan. Y superar el límite **no detiene al
agente**: lo lleva a `awaiting_approval` (estado que ya existe en el bloque 6) con una ampliación de
un clic que queda como `contract.amended`.

**Efectos irreversibles.** El borrador decía "denegados hasta `acceptance.granted`", lo cual es
circular para una tarea *cuyo criterio de aceptación es el efecto* (desplegar). Corrección, expresada
con el mecanismo que ya existe en el bloque 5 (atenuación de macaroons), no con una regla nueva:

- El `CapabilityToken` del agente nace con `reversibility_max="compensable"`. Toda invocación con
  `reversibility="irreversible"` produce `capability_denied`, no una comprobación de política dentro
  de la herramienta.
- Cuando todos los criterios `full` del objetivo están en PASS, el **supervisor** emite un token
  atenuado adicional, alcance = exactamente los `effect_scopes` declarados en el contrato, TTL ≤ 300 s,
  un uso por `idempotency_key`. `effect.authorized{scopes, ttl, contract_hash}` queda en el journal.
- El caso "desplegar" se modela como dos objetivos con arista `ordering`: el primero verifica contra
  un `EffectScope` de staging; el segundo, ya con criterios verdes, obtiene el token de producción.
  Ningún caso legítimo se rompe y la asimetría se respeta: lo reversible se intenta barato, lo
  irreversible no se intenta hasta que la verificación terminó.

---

### 9.7 Cuando no hay tests (que es el caso normal)

La mayoría de repos reales no tiene suite, y exigirla convierte la primera tarea en un proyecto de
semanas. El contrato nunca queda vacío, pero **tampoco finge**. La escalera, en orden de coste y de
probabilidad de existir:

1. **Golden de importación y símbolos (siempre disponible, < 5 s).** ¿Los módulos tocados siguen
   importando/compilando y la tabla de símbolos públicos sigue igual? Es `symbols.stable@1` y no
   requiere saber nada del dominio.
2. **Golden de función pura (2-4 min, cuando aplica).** Para las funciones tocadas cuyo AST no
   contiene I/O, se generan entradas y se registran salidas *antes* de editar. Restricción honesta que
   el borrador omitía: solo funciona en funciones sin efectos, que en legacy hostil son la minoría.
3. **Golden de proceso (2-6 min, si hay comando descubrible).** Solo si el repo declara cómo se
   arranca: `Makefile`, `scripts` de `package.json`, `docker-compose.yml`, `Procfile`, `justfile`. Se
   capturan respuestas HTTP o salida CLI de los puntos de entrada alcanzados por el diff.
4. **Invariantes negativas (gratis).** Sin secretos, sin dependencias no declaradas, firma pública
   inalterada, migraciones reversibles. No requieren conocer el dominio.

**Y el escalón que faltaba: `unverifiable`.** Si ninguno de 2-3 es aplicable, el contrato lo declara
—`coverage_substitute: "none"`— y el objetivo se marca `unverifiable` en la UI y en
`agent.completed{verified: false}`. El humano ve, antes de aceptar, que lo único que se comprobó es
que compila. Una capa de verificación que aparenta una red que no existe es peor que no tener capa:
produce confianza sin base, que es exactamente el fallo que este bloque existe para evitar.

**Regla del boy scout.** Toda tarea que toca un módulo sin tests deja **un** test del comportamiento
que cambió: criterio `enforcement="advisory"` con tope del 15 % del wall clock. Si no da tiempo,
`contract.debt_recorded{module, reason}`. La deuda es un evento, no un bloqueo — un sistema que
bloquea por falta de tests se desinstala en una semana.

---

### 9.8 Auto-engaño: tres detectores que bloquean, siete que anotan

El borrador tenía nueve detectores, todos bloqueantes o con "revisión obligatoria". Varios son
máquinas de falsos positivos: `except: pass` es legítimo en cierres, importaciones opcionales y
limpieza; "un literal raro que aparece en el test y en el código" son constantes, códigos de error y
versiones de API. Un sistema que acusa de fraude por un `except ImportError: pass` se ignora en dos
semanas, y entonces también se ignoran los tres detectores que sí importan. Además `deception.flagged`
presupone intención, que ningún AST puede establecer. Corrección: se separan y se renombran.

**Bloqueantes (tres, y solo tres):**

| Patrón | Detector | Por qué bloquea |
|---|---|---|
| Editar lo que te verifica | `guard_hash != guard_hash@plan.published` → `tainted` (mecanismo del bloque 6) | Es el único ataque que invalida *todos* los demás controles |
| Nuevos `skip`/`xfail`/`t.Skip`/`@Ignore` | `collected < collected_min` o `skipped > skipped_max` del `CollectionFloor` **pinneado** | Sin suelo pinneado no hay comparación posible: el base se mueve |
| Declarar sin ejecutar | `acceptance.claimed` sin `criterion.evaluated` por criterio `required` | Estructural; cuesta 0 tokens y ni llega al juez |

**Anotaciones (`diff.annotation{pattern_id, path, span, confidence}`), nunca bloqueantes**, que la UI
pinta sobre el diff y el auditor recibe como pistas priorizadas: excepción tragada, mock en ruta de
producción, dependencia declarada pero no ejercitada (`services: [postgres]` y `pg_stat_activity` sin
conexión registrada), `TODO`/`NotImplementedError` añadidos, función nueva de ≤ 1 sentencia que
devuelve literal vacío, coincidencia de literal raro test↔producción, y cobertura que sube por
borrado de código (se mide `patch_coverage_bp` sobre líneas **nuevas**, nunca cobertura global).

**El test que modifica el test para que pase** se cubre entero por `tainted` más una segunda pasada:
los tests que pasaron de `fail → pass` se **re-ejecutan desde el snapshot base**, con el contenido de
test pinneado por hash. Si el test pinneado sigue fallando, el arreglo fue al test, no al código.

---

### 9.9 Evidencia y métricas

Cada `criterion.evaluated` lleva `evidence: list[BlobRef]` apuntando a la salida real (stdout
íntegro, `junit.xml`, `PredicateOutcome`, transcripción SQL). El journal transporta refs, jamás
payloads (invariantes 3 y 4) y jamás texto libre inline (bloque 1 §1.2). El humano navega de
"aceptada" al byte exacto de stdout.

**Retención sin reescribir el journal.** 30 días de evidencia general, 180 para la que sustenta un
`agent.completed`. El vencimiento es GC del CAS; el evento permanece intacto y una proyección lo
marca `evidence_expired=true`. Reescribir el journal para borrar evidencia rompería la cadena de
hashes; el bloque 1 ya fijó que el borrado selectivo solo ocurre en el CAS.

**Métricas medidas contra el banco de tareas de referencia del bloque 7** (20-30 tareas reales sobre
este mismo repo con criterio de éxito automático), no contra sensaciones de producción. Sin ese
denominador, los objetivos de abajo son deseos.

| Métrica | Definición | Objetivo (F4) |
|---|---|---|
| Autonomía | tareas del banco aceptadas sin intervención humana | ≥ 55 % |
| Reversión post-aceptación | revertidas ≤ 7 días tras `agent.completed` | < 8 % |
| Falsos "terminado" peligrosos | `completed` seguido de rollback | < 5 % |
| Falsos "terminado" sanos | `acceptance.claimed` rechazado en `verifying` | 30-50 % (señal de que la capa trabaja) |
| Fracción de verificación | `usd_micros` de `purpose="verification"` / total | 20-35 % |
| TTFV | tiempo hasta el primer criterio `cheap` en PASS | p50 < 4 min |
| Acierto de caché | `from_cache=true` / evaluaciones de criterios `hermetic` | ≥ 55 % |
| Criterios debilitados | `contract.weakened` por tarea, por perfil | < 0,2 |
| `qa_sensitivity` | mutantes detectados / inyectados, con Wilson 95 % | límite inferior ≥ 0,6 |
| Tareas `unverifiable` | objetivos con `coverage_substitute="none"` | se reporta, no se optimiza |

**Flaky.** Un reintento; si pasa al segundo, **no cuenta como verde**. El criterio pasa a
`enforcement="advisory"` con TTL de 14 días y emite `contract.weakened{reason:"flaky"}` — la
cuarentena nunca reduce el contrato en silencio. Al vencer el TTL vuelve a `blocking`. Un criterio
que entra en cuarentena dos veces genera un `Goal` de reparación, no una exención permanente.

---

### 9.10 Eventos y fases

**Dominio `contract` (nuevo; amplía la taxonomía del bloque 1 §1.5, que no lo contemplaba):**
`proposed, approved, amended, weakened, debt_recorded`. Se reutilizan sin cambios `criterion.evaluated`
(bloque 6), `plan.published/revised`, `approval.*`, `budget.*`, `workspace.*`, `assistance.requested`.
Nuevos en este bloque, mínimos: `acceptance.claimed` (`writable_by={"agent"}`),
`effect.authorized` (`{"kernel"}`), `rollback.requested/applied`, `bisect.culprit`, `diff.annotation`.

| Fase | Alcance | Por qué ahí |
|---|---|---|
| **F1** | `writable_by` + invariante de causalidad del veredicto + `spec_hash` congelado + `VerificationSpec` + tier `guard`/`cheap`/`full` + línea base congelada + 4 predicados (`secrets`, `symbols`, `metric.threshold`, `deps`) + `ChangeLimits` + clases `trivial`/`standard` + checkpoints sobre `SnapshotId` | Todo lo de F1 es **irretroactivable**: añadir la ACL de escritor o el congelado de `spec_hash` después obliga a reescribir el bus y a invalidar todos los journals anteriores |
| **F2** | `PredicateRegistry` como plugins + caché de veredictos + selección por diff + `Bisector` + escalera de sustitutos + perfil `forge.auditor` + `process.healthy@1`/`http.probe@1` + Postgres efímero con RLS + clase `guarded` | Dependen de que el grafo de código (bloque 2 §2.7.5) y el ABI de plugins (bloque 4) estén estables; construirlos antes es construir contra una interfaz que va a cambiar |
| **F3** | `ui.observe@1` (a11y + consola + fallos de red + cajas de layout) + `qa_sensitivity` como puerta + árbitro en segundo proveedor + coordinación de recursos externos entre sesiones | El bloque 7 difiere visión y OCR a F3 sin *call site* en F1-F2; adelantarlo sería construir el consumidor antes que el productor |

**Percepción de UI sin visión del modelo (F3, especificado ahora para no rediseñar después).** El
modelo base puede no tener visión y la restricción de proveedor prohíbe asumirla. `ui.observe@1`
devuelve **texto y números**: árbol de accesibilidad (≤ 8 KiB, truncado por relevancia), digest del
DOM (selectores + texto visible), entradas de consola, fallos de red, y cajas de layout — porque
"`ancho_contenido > ancho_viewport` a 375 px" es un booleano, no una impresión estética. Los píxeles
producen un único entero, `baseline_diff_bp`, que no requiere modelo. OCR entra como **capacidad
detectada** (`CapabilityIndex` del bloque 7) que *añade* una señal; si mañana hay visión, entra por el
mismo camino. Nunca como dependencia.

---

### Alternativas descartadas

| Alternativa | Por qué se descartó | Coste de habernos equivocado |
|---|---|---|
| **Vocabulario propio (`Clause`, `ClauseVerdict`, `clause.verdict`)** | Colisiona con `Criterion`/`criterion.evaluated` del bloque 6: dos motores de verificación, dos contabilidades, el mismo bug de reanudación arreglado dos veces | Muy alto y estructural: se detecta en integración, cuando ambos ya tienen usuarios |
| **Módulo `VerdictAuthority` en el núcleo confiable** | Séptimo módulo de núcleo no previsto por la invariante 6, y duplica `ExecutionEngine.settle` del bloque 5. La propiedad se obtiene con `writable_by` + un invariante de causalidad | Medio: complejidad y superficie de confianza extra sin ganancia |
| **`ClauseKind` extensible con specs tipadas en el núcleo** | Añadir una comprobación nueva = editar un enum del núcleo y recompilar. Contradice "añadir capacidad es escribir un archivo" | Alto a 3 años: el enum crece a 20 variantes y cada dominio nuevo toca el núcleo |
| **Contrato caro y uniforme para toda tarea** | Forge recibe conversación, no tickets; 30-90 s + una pregunta por "renombra esta variable" mata la adopción | Peor desenlace posible: la capa es correcta y el usuario la desactiva |
| **V0 que revierte la edición** | Imposibilita refactors multi-fichero (rotos por construcción a mitad) y produce livelock escribir↔revertir con modelo débil | Alto: pérdida de trabajo del agente y del usuario, difícil de diagnosticar |
| **Checkpoints como commits de git** | El bloque 2 midió `git worktree add` en 20-90 s con 500k inodos y lo descartó; el snapshot del CAS es O(1) | Rendimiento inaceptable en el repo grande, que es donde la capa importa |
| **Clave de caché sin `read_set` ni `deplayer_key`** | Con `tree_hash` global el acierto tiende a 0 en monorepo; sin la capa de dependencias se sirven verdes de otro entorno | Doble: caché inútil (barato) y caché envenenada (caro y silencioso) |
| **QA + Reviewer + Security como tres jueces LLM** | `security` es determinista y sale más barato y más fiable como predicados; el árbitro con un solo proveedor es la misma opinión tres veces, cosa que el propio diseño prohíbe | Tokens, latencia y falsa confianza; se detecta tarde porque "parece" riguroso |
| **Mutantes al 1 de cada 8 como puerta** | Estadísticamente inútil: ~25 días por medición significativa, y se actuaría sobre ruido (mismo argumento que el bloque 7 sobre 30 muestras) | Medio: decisiones de política sobre una métrica no significativa |
| **Juez LLM como verificador general** | Sin JSON estricto ni tool-calling fiable, cambiar un exit code por una opinión es cambiar certeza por ruido | Falsos verdes en masa; el humano deja de confiar y revisa todo a mano |
| **Solo aserciones deterministas** | Deja fuera criterios cualitativos reales que el usuario valora (claridad de errores, intención preservada) | Verificación técnicamente correcta y prácticamente insuficiente |
| **Nueve detectores de auto-engaño bloqueantes** | Varios son máquinas de falsos positivos (`except: pass`, literales compartidos); el usuario aprende a ignorar el canal y con él los tres que sí importan | Alto e insidioso: la señal buena muere ahogada por la mala |
| **Delegar en el CI del repo** | Latencia de minutos, no todos los repos lo tienen, no expone evidencia estructurada citable ni permite selección por diff | Bucle demasiado lento para `guard`/`cheap`; se pierde el incremental |
| **Suite completa siempre** | Verificar cuesta más que construir; el usuario apaga la capa | El peor desenlace: capa correcta y desactivada |

---

### Riesgos aceptados

- **Una ejecución de verificación no es reanudable.** `pytest` a mitad no tiene checkpoint (el bloque
  5 ya lo admite para herramientas en general). Al reanudar un agente, los criterios en vuelo se
  re-ejecutan desde cero. Se acepta: el coste es un reintento de ≤ 45 s en el tier `cheap`; hacerlo
  reanudable exigiría un protocolo de checkpoint por runner de test que ninguna herramienta real
  soporta.
- **`patch_coverage_bp` no existe uniformemente en este repo.** Python sí (`coverage_xml@1`); Kotlin y
  Swift exigirían runners específicos. En F1-F2 los módulos móviles usan `symbols.stable@1` +
  `command` de build, y el criterio de cobertura simplemente no se propone. Se acepta explícitamente
  en vez de fingir paridad.
- **La caché puede servir un verde válido pero irrelevante.** Un criterio `hermetic` cuyo `read_set`
  esté mal declarado (declarado estrecho) cachea de más. El `read_set` se propone por el planificador
  y no se verifica dinámicamente en F1-F2. Mitigación parcial: la suite completa periódica y el hecho
  de que `read_set` esté congelado en `spec_hash` y visible en el diff del contrato. La verificación
  real (instrumentar el runner para registrar ficheros abiertos) es F3.
- **`qa_sensitivity` puede subir por memorización de las familias de mutantes.** Semilla rotatoria y
  familias nuevas por trimestre reducen, no eliminan. Con un solo proveedor, el veredicto del auditor
  se trata como señal, no como puerta.
- **Recursos externos compartidos entre sesiones no están arbitrados** (mismo riesgo que declara el
  bloque 6 §6.8). Dos sesiones que levantan `process.healthy@1` sobre el mismo puerto se pisan. Dentro
  de una sesión lo cubren los `resource_keys` y los claims; entre sesiones, no. Mitigación de F1:
  puertos efímeros asignados por el sandbox. Arbitraje real, F3.
- **Los `guard` producen ruido en refactors largos.** Entre la primera y la última escritura de un
  refactor, `build` estará rojo y el agente recibirá anotaciones que no debe atender. Se acepta:
  suprimirlas exigiría que el modelo declarase "estoy a mitad de un refactor", que es exactamente la
  clase de declaración en la que un modelo débil no es de fiar.
- **La clase `trivial` se autoaprueba con la estimación del planificador.** Un planificador que
  subestima el alcance obtiene un contrato flojo. Mitigación: la estimación no concede nada — el
  `CapabilityToken` limita físicamente la escritura al `write_set` (bloque 6) y superar
  `ChangeLimits` reclasifica la tarea a `standard` en caliente. El riesgo residual es una tarea que
  cabe en 3 ficheros y aun así rompe algo sutil.

---

### Cómo se rompe

- **La selección de tests miente.** Dependencias dinámicas (`importlib`, DI, fixtures) no aparecen en
  el grafo de imports → falso verde en el tier `cheap`. *Mitigación*: suite completa cada 10
  checkpoints o 30 min, marcado de módulos opacos, y degradación a suite completa si el `snapshot_id`
  de la proyección del índice no coincide con el del worktree.
- **Flaky corrompe el oráculo.** Un test intermitente convierte veredictos en ruido y hace que el
  bisector acuse inocentes. *Mitigación*: un reintento, cuarentena con `contract.weakened` visible, y
  el `Bisector` se niega a correr con oráculo en cuarentena o no hermético.
- **Caché envenenada por el entorno, no por el árbol.** Cambia `installer_version` de npm, el
  `tree_hash` no se mueve, se reutiliza un verde inválido. *Mitigación*: `deplayer_key` y
  `toolchain_digest` son componentes obligatorios de `verdict_key`, y solo se cachea `hermetic=True`.
- **Carrera entre agentes sobre el mismo recurso de verificación.** Dos ramas CoW hermanas levantan
  Postgres en el mismo puerto y ambas fallan, o peor, una ve datos de la otra. *Mitigación*:
  `resource_keys` en cada criterio, claims del supervisor, puertos efímeros. Entre sesiones distintas
  del mismo host: riesgo aceptado.
- **Restaurar el árbol no restaura el entorno.** Rollback a un checkpoint de hace 40 pasos con las
  dependencias de ahora produce fallos que no corresponden a ningún código, y el humano persigue un
  fantasma durante horas. *Mitigación*: `Checkpoint.deplayer_key` y remontaje de la capa correcta;
  si esa capa ya fue recogida por el GC, el rollback avisa en vez de restaurar a medias.
- **El contrato se escribe blando.** El planificador —mismo modelo que ejecutará— se pone metas
  fáciles. *Mitigación*: línea base `frozen` invisible al planificador, `contract.weakened` como
  evento de primera clase, y la métrica "criterios debilitados por tarea" publicada por perfil.
- **El aislamiento del auditor se rompe por accidente.** Alguien añade la transcripción del developer
  al `ContextQuery` del auditor "para dar contexto" y el rol se convierte en sello de goma sin que
  nada falle. *Mitigación*: test que inspecciona `RenderedContext.blocks` por `provenance.agent`, y la
  suite de mutantes como detector de la caída de sensibilidad.
- **El sandbox no es producción.** Migración verde en efímero, muerta contra RLS real con datos.
  *Mitigación*: Postgres efímero **con RLS activo** y datos sintéticos representativos; nunca SQLite
  como sustituto. Residual: los datos sintéticos no cubren la forma real de los datos del tenant.
- **Deriva del oráculo.** "Actualizar goldens" en masa mata la red de caracterización sin ruido
  alguno. *Mitigación*: actualizar un golden es `contract.amended` + `contract.weakened` con el diff
  del golden mostrado; el conteo entra en la métrica de criterios debilitados.
- **Verificar cuesta más que construir en tareas triviales.** *Mitigación*: la clase `trivial` no
  propone criterios nuevos y salta el tier `judged` entero; para diffs ≤ 10 líneas no se convoca al
  auditor.
- **El humano aprueba `guarded` sin leer.** El contrato tiene 12 criterios y el diff 400 líneas: la
  aprobación se vuelve un clic reflejo. *Mitigación*: la UI muestra primero el **delta** respecto a la
  línea base y los `effect_scopes`, no el contrato entero; lo que cambió es lo que se lee.

---

## 10. Lista de materiales: qué se toma, qué se estudia y qué se construye

Forge es, en volumen de código, un producto propio: alrededor del **85% de las líneas del repositorio
serán nuestras** y un 15% será cola de integración (wrappers, adaptadores, scripts de build de
tokens, empaquetado de binarios). El reparto por *esfuerzo evitado* es el inverso y por eso la lista
importa: las veinte dependencias de abajo nos ahorran del orden de 12-18 meses-persona de trabajo que
no queremos hacer nunca —parsers incrementales para 300 lenguajes, un editor de texto con
contenteditable e IME, un emulador VT, primitivas accesibles con foco y colisiones, un motor de
automatización de navegador— mientras que el trabajo que sí define el producto (el visor de 400
archivos, el journal, el índice de símbolos, el formato de edición, el contrato de aceptación, el
tema) no existe en ningún sitio y suma **~50 semanas-persona** para una fase 1 defendible. Regla que
gobierna toda la tabla: nada entra como plataforma, todo entra como librería detrás de una interfaz
nuestra, y **ninguna pieza dibuja un píxel por su cuenta**.

Criterio de recorte aplicado: si una dependencia no se usa en los primeros tres meses, no está en la
Tabla 1. Por eso quedan fuera de fase 1 (y no por ser malas) `ast-grep`, `pygit2`, `lsprotocol`,
`pgvector`, `sqlite-vec`, `model2vec`, `fastembed`, `fastcdc`, `difftastic`, `Opengrep`, `Trivy`,
`mutmut`, `pytest-testmon`, `inspect-ai`, `apple/container`, `Lima`, `Firecracker`, `Cloudflare
Sandbox`, `Tauri` y `Textual`. Y quedan fuera **para siempre** las piezas que meterían Rust o Zig en
la cadena de build por una sola función (`portable-pty`, `libghostty-vt`, `alacritty_terminal`,
`redb`): el stack es Python + TypeScript y se queda así.

### Tabla 1 — Se toma (fase 1)

| # | Componente | Pieza elegida | Licencia | Por qué | Alternativa si falla |
|---|---|---|---|---|---|
| 1 | Editor de código | CodeMirror 6 (`@codemirror/state`, `view`, `language` + Lezer) | MIT | No impone ni una regla de CSS: el tema es un objeto nuestro. 67 KB gzip en mínimo útil vs 1.15 MB de Monaco. Edita sobre `contenteditable` real, así que IME y teclado virtual funcionan en el iPhone desde el que se aprueba. | Mantener nuestro fork del monorepo (MIT lo permite; ya vendorizamos espejo). Monaco está descartado por estética y por móvil. |
| 2 | Diff y merge por archivo | `@codemirror/merge` | MIT | +9.7 KB gzip sobre el editor y da la vista "el agente propone este hunk, acepta/rechaza". Exporta el algoritmo de diff suelto, que reutilizamos a nivel de palabra. | `jsdiff` (BSD-3) para el algoritmo desacoplado. |
| 3 | Resaltado de todo lo de solo lectura | Shiki (`createHighlighterCore` + `codeToTokens`) | MIT | Única fuente de verdad de color para diffs, chat, búsqueda y previews sin instanciar 400 editores. Devuelve tokens, no HTML opaco: las clases y el CSS son nuestros. Corre en RSC y en Worker. | Colorear con Lezer en servidor (misma gramática que el editor, más trabajo). |
| 4 | Virtualización | TanStack Virtual | MIT | Headless: devuelve índices y offsets, cero DOM y cero estilo. Su modo *end-anchored* (may-2026) es literalmente el caso "log que crece por abajo y se carga historia por arriba". | `virtua` o `react-window` 2.x. |
| 5 | Primitivas accesibles | Base UI (`@base-ui/react`) | MIT | Comportamiento, foco, ARIA y colisiones sin un solo estilo propio. Nos ahorra 6-9 meses de combobox y diálogos accesibles. Nunca se importa en una pantalla: se envuelve en `packages/ui`. | Radix Primitives (MIT, WorkOS). La migración es mecánica. |
| 6 | Motor de tokens y estilo | Tailwind CSS v4 (`@theme`, OKLCH) | MIT | Ya está en `apps/web`: coste de integración cero. Se usa como motor de tokens, no de estética: borramos la paleta por defecto y se prohíbe por lint el color crudo. | Panda CSS (zero-runtime, tipado). `vanilla-extract` descartado por bus factor declarado. |
| 7 | Animación donde CSS no llega | Motion (ex Framer Motion) | MIT irrevocable | Solo tres casos: layout animations (FLIP), muelles interrumpibles en gestos y salidas coordinadas. El 90% del movimiento es CSS puro + View Transitions. | CSS puro y ya. GSAP está **prohibido** (ver trampas). |
| 8 | Identidad visual (tipografía + iconos) | Inter Variable + instancia propia de Commit Mono ("Forge Mono") + Lucide | OFL 1.1 / OFL 1.1 / ISC | Autoalojadas, sin Google Fonts. Inter con `cv01/cv05/ss03` deja de parecer Inter; commitmono.com genera una mono renombrada y propia, que es la única forma de tener carácter sin encargar una tipografía. Lucide como set único, con ~15 iconos dibujados por nosotros sobre su misma rejilla. | JetBrains Mono (OFL) como mono segura; Phosphor si queremos peso de icono como señal de estado. |
| 9 | Terminal | `@xterm/xterm` v6 + `addon-webgl` (cliente, carga diferida) y `@xterm/headless` (servidor) | MIT | El 90% de la salida no necesita emulador, necesita ser un evento estructurado; `@xterm/headless` es lo que convierte 12 KB de barras de progreso en la pantalla resuelta que va al journal y al modelo. xterm.js entra solo como panel de TTY crudo (vim, REPL, instalador). El PTY lo posee Python con el módulo `pty` de la stdlib: cero dependencias y cero lenguajes nuevos. | Normalizador ANSI propio en Python (~200 líneas) si el sidecar Node molesta. `pyte` está **prohibido** (LGPL-3.0). |
| 10 | Parsing multi-lenguaje | `tree-sitter` + `py-tree-sitter` + `tree-sitter-language-pack` | MIT (cada gramática, la suya) | Única capa que parsea código roto, que es donde vive un agente. Wheels arm64, sin compilar gramáticas. Medido: 16 MB/s por hilo. Arquitectura obligada: parsear, extraer hechos, **tirar el árbol** (retenerlo cuesta 22.6x el tamaño del fuente). | No hay. Es la elección forzada del proyecto. |
| 11 | Render de código por símbolo | `grep-ast` (`TreeContext`) | Apache-2.0 | Imprime una definición con sus cabeceras de scope padre y elide el resto: es el render correcto tanto para el repo map como para enseñarle resultados de búsqueda a un modelo débil. Son pocos cientos de líneas, absorbibles si muere. | Vendorizarlo en `packages/toolkit` con atribución. |
| 12 | Búsqueda de texto | ripgrep (subproceso, `--json`) | Unlicense OR MIT | Cero índice que mantener: funciona sobre un worktree recién creado. Da spans byte a byte que mapeamos a nodos del AST y respeta `.gitignore` sin reimplementarlo. | Índice propio de trigramas posicionales (ver Tabla 2, Zoekt) cuando midamos `rg` > 1 s. |
| 13 | Watcher de ficheros | `watchfiles` (sobre notify-rs) | MIT (notify: CC0) | Único wrapper Python de FSEvents/inotify con wheel sin dependencias y API async, que encaja con el worker que ya existe. Reindexar un fichero suelto es sub-milisegundo. | Reconciliación periódica contra `git status` + mtime, que hay que tener igualmente. |
| 14 | Git | Binario `git` en subproceso | GPL-2.0 (ejecutable, sin enlazado) | Es más rápido que la librería en lo que hacemos miles de veces (`worktree add --detach` 265 ms vs 461 ms de `pygit2.add_worktree`), y hace falta de todos modos para `push` y hooks. Evita meter GPL con excepción de linking en el árbol. | `pygit2` (fase 2) solo cuando necesitemos merge de tres vías en memoria; `dulwich` (Apache-2.0) si la GPL estorba. |
| 15 | Journal y bus | SQLite en modo WAL (stdlib `sqlite3`) + Redis Streams para fan-out | Dominio público / AGPLv3 como servicio | La verdad es un `journal.db` por sesión con lectura por cursor (`WHERE seq > ?`), que es a la vez log y bus. Redis ya es dependencia de `apps/api`: solo reparte en vivo a web y móvil, con `MAXLEN`, y **nunca** es fuente de verdad. | Valkey (BSD-3) si algún día empaquetamos el binario. `LISTEN/NOTIFY` de Postgres para quitar una pieza. |
| 16 | Ejecución durable | DBOS Transact (Python) | MIT | Una tarea de tres días sobrevive a un reinicio checkpointeando en la Postgres que ya tenemos. Sin Cassandra, sin Kubernetes, sin BSL. Todo lo no determinista (la llamada al modelo) vive dentro de un `@DBOS.step`. | Máquina de estados propia sobre el journal (varias semanas). Temporal descartado por coste operativo; Restate por BSL. |
| 17 | Aislamiento en macOS | `sandbox-exec` (Seatbelt) + perfiles `.sbpl` de `codex-rs/sandboxing` vendorizados | Sistema Apple / Apache-2.0 | Único nivel con coste de arranque cero que trabaja sobre los ficheros REALES del usuario. virtiofs penaliza 6-9x el I/O: una VM arruina el ciclo compilar-ver-error-arreglar. Los perfiles de OpenAI están auditados en producción y derivan del sandbox de Chrome; escribirlos nosotros son semanas y agujeros. | `apple/container` 1.1 (Apache-2.0, macOS 26+, esta Mac califica) como escalón inmediato detrás de la misma interfaz `SandboxDriver`. |
| 18 | Verificación de UI web | Playwright (`aria_snapshot(mode='ai')` + `tracing`) | Apache-2.0 | Las dos únicas APIs que resuelven el problema: el árbol de accesibilidad como YAML con handles `[ref=eN]` deja que un modelo **sin visión** valide una interfaz, y `trace.zip` es la unidad de evidencia (film-strip, DOM por acción, red, consola) que se adjunta al journal y se sirve desde nuestra UI. | Ninguna seria. Puppeteer no tiene equivalente a ninguna de las dos. |
| 19 | Puerta de calidad Python | ruff + pyrefly | MIT / MIT | Milisegundos, salida JSON/SARIF, y pyrefly convierte la alucinación de API típica de un modelo débil en un error localizado en <5 s. Ruff ya es del ecosistema Astral/uv que usamos. | `ty` (Astral, MIT) cuando salga de beta; mypy como red de conformidad, nunca como camino principal. |
| 20 | Secretos | gitleaks (`protect --staged` y `detect`) | MIT | Es el único fallo de la lista que es **irreversible** una vez publicado, y un agente que trabaja horas con acceso a `.env` acabará pegando una clave. Binario Go único, arranque instantáneo, SARIF por el mismo adaptador. | Ninguna: TruffleHog es AGPL-3.0 y no se toca. |

### Tabla 2 — Se estudia (no se integra; se copia una idea concreta)

| Proyecto | La idea concreta que copiamos |
|---|---|
| Aider (Apache-2.0) | El **repo map**: tree-sitter extrae tags `def`/`ref`, se monta un multigrafo fichero→fichero, se corre **PageRank personalizado** (masa en los ficheros del chat, pesos x10 a identificadores mencionados y bien nombrados, x50 a ficheros abiertos), se reparte el rank entre símbolos y se rellena hasta el presupuesto de tokens por búsqueda binaria, renderizando con `TreeContext`. Es el mejor contexto por token que existe y se reimplementa en un día sobre las dependencias 10 y 11. |
| SWE-agent + mini-swe-agent (MIT) | La tesis ACI, como **ley de diseño de nuestras herramientas**: visor con ventana de ~100 líneas y comandos explícitos de scroll (nunca `cat`); el comando de edición lleva linter dentro y **rechaza** la edición si el resultado no es válido; la búsqueda global lista solo ficheros, sin contexto por coincidencia (medido: enseñar más empeora al modelo); y feedback explícito ante salida vacía. De mini-swe-agent: historia lineal estricta y una sola herramienta, para no depender de tool-calling nativo fiable. |
| oh-my-pi / hashline (MIT) | El **formato de edición anclado por hash**: cada línea que ve el modelo lleva un hash corto y el parche referencia hashes en vez de reteclear el texto. Elimina los fallos por whitespace, baja los tokens de salida y regala control de concurrencia optimista (si el fichero cambió, los hashes divergen y el parche se rechaza). Se implementa en Python en un par de días; SEARCH/REPLACE queda como respaldo por modelo. |
| OpenHands SDK (MIT) | **Condensación como evento**: en vez de truncar la conversación, la compresión se escribe como un evento más (`forgotten_event_ids`, `summary`, `offset`) y la vista que ve el LLM se reconstruye reproduciendo el log. Reanudar una tarea de 12 horas pasa a ser replay puro, y la UI puede mostrar lo que el modelo ya no ve. |
| Codex CLI (Apache-2.0) | El **modelo de permisos de dos ejes ortogonales**: modo de sandbox (`read-only` / `workspace-write` / `full-access`) × política de aprobación (`untrusted` / `on-request` / `never`). Un comando permitido dentro del sandbox no pregunta; la aprobación aparece solo al pedir escalar. Y la regla de oro: el sandbox se aplica a los **procesos hijos**, no solo al `edit_file`. |
| `@playwright/mcp` (Apache-2.0) | El **contrato de herramientas de navegador**: `snapshot` → refs → `act(ref)`, con re-snapshot obligatorio tras cada acción (los `ref` caducan con la primera mutación del DOM) y gating por capacidades para separar lo barato y textual de lo caro y visual. Se copia el contrato sin adoptar MCP. |
| hermes-ide (**BSL 1.1**, solo desde su `ARCHITECTURE.md`, jamás desde el código) | Tres decisiones: el **pool de terminales a nivel de módulo** (las instancias de xterm.js nunca dentro de un componente React, o cada re-render destruye el scrollback); la **máquina de estados explícita del PTY** con `NeedsInput` como estado de primera clase; y el escaneo de proyecto en tres niveles por presupuesto de tiempo, con el contexto materializado como fichero en disco más una señal de invalidación. |
| Pi (MIT) | **Lazy skills**: cada capacidad ocupa una línea en el prompt de sistema y sus instrucciones y esquema se cargan solo al invocarla. Con un modelo débil el prompt de sistema es el recurso más escaso y hoy se malgasta describiendo 30 herramientas que no se van a usar. Es una tarde de trabajo. |
| Agentless / AutoCodeRover (MIT, código parado desde 2024) | El **pipeline fijo como modo degradado** de Forge: localización jerárquica (fichero → símbolo → posición), reparación por muestreo de N candidatos, y sobre todo la fase que nadie implementa: generar un test de reproducción y **re-rankear** los parches con los resultados. Para tareas de forma conocida, un pipeline fijo bate a un agente libre en coste y acierto. |
| Zoekt (Apache-2.0) + stack-graphs (**archivado** 2025-09-09) | De Zoekt, el **trigrama posicional** y el shard inmutable por repo (1.2x el corpus en RAM, reindexado incremental trivial). De stack-graphs, modelar el scope como una **pila** y hacer que el subgrafo de cada fichero sea independiente, que es la única forma conocida de tener resolución cross-file incremental sin compilar. Que GitHub abandonara el enfoque es la señal de que nuestra versión será heurística y debe poder decir "no estoy seguro". |

### Tabla 3 — Se construye a mano

| Qué | Por qué no existe | Semanas-persona |
|---|---|---|
| Visor de revisión de 400 archivos: virtualización anidada (lista de ficheros **y** hunks dentro), estado revisado/aprobado por hunk, colapso de lo trivial, búsqueda y copia propias | Todo lo OSS (`react-diff-view`, `@git-diff-view`, `diff2html`) renderiza un archivo entero para que un humano lea un PR de tres ficheros, con estética GitHub y sin virtualizar. Virtualizar además rompe Ctrl-F y "seleccionar todo": eso hay que reimplementarlo o el humano odiará la herramienta | 6 |
| Journal de eventos: esquema versionado append-only, bus por cursor con ack y backpressure, índice de hitos, plegado por comando, permalink a una línea de salida, redacción de secretos antes de persistir | No hay componente OSS para "comando → salida → exit code → duración → cita enlazable". OpenHands tiene el mejor log y está diseñado para reproducir contexto de LLM, no para pintar una UI en vivo | 5 |
| Índice de símbolos incremental en SQLite (defs/refs/imports + FTS5), repo map con PageRank, invalidación por watcher con reconciliación contra hash tras cada ráfaga de FSEvents | `universal-ctags` es GPL-2.0 y sin rangos ni jerarquía; el repo map de Aider cachea por mtime para un proceso, un repo y un usuario. Nada es concurrente ni multiproyecto | 5 |
| Formato de edición y su validador: hashline + SEARCH/REPLACE de respaldo, re-parseo con tree-sitter, rechazo si aparecen nodos ERROR/MISSING nuevos, transacción sobre N ficheros y rollback atómico | `ast-grep` da `commit_edits` sobre un string. El anclaje difuso, la ambigüedad de múltiples matches, la transacción y el journal para deshacer no los da nadie | 4 |
| Protocolo de sesión cliente/servidor sobre WebSocket (el agente corre horas, el humano se engancha desde web, móvil o CLI y se desconecta sin matar la tarea) + persistencia multi-inquilino en Postgres con RLS | Todos los runtimes del sector persisten en SQLite local o JSONL en el home: mono-usuario, mono-máquina. ACP existe pero modela una sesión de chat, no el estado de un IDE, y lo gobierna Zed | 4 |
| Sistema de tokens y tema: rampas OKLCH de 12 pasos con roles, escala tipográfica, ladder de espaciado, y un **tema de sintaxis casi acromático** (keywords por peso, no por color) para que el rojo, el verde y el ámbar signifiquen borrado, añadido y esperando aprobación | Ningún tema OSS hace esto: todos gastan el presupuesto de color en la sintaxis. Radix Colors da el contrato paso→rol, culori la matemática; los valores son la identidad del producto | 3 |
| Puente PTY → eventos estructurados: emisión propia de marcadores tipo OSC 133 desde el ejecutor (pytest o cargo lanzados sin shell no cooperan), correlación de stdout/stderr con proceso, cwd, exit code y duración | OSC 133 solo delimita si el shell colabora. Ghostty da los scripts para el shell del humano; el ejecutor del agente es nuestro | 3 |
| `SandboxDriver` + detección de capacidades en el arranque + generación de perfiles SBPL por tarea + cupo de recursos por sesión (en macOS no hay cgroups: `setrlimit` es por proceso y se elude con fork) | `codex-rs/sandboxing` es lo más cercano, es Rust, es interno de su workspace y cubre tres backends. Degradar en silencio a un sandbox más débil es el fallo de seguridad típico de estos sistemas | 3 |
| Proxy de allowlist de red (HTTP con inspección de SNI/Host + SOCKS5), sin terminar TLS, con inyección de confianza en pip, npm, cargo y git, y modo diagnóstico que registra cada decisión con su motivo | Ningún mecanismo del SO sabe de dominios: Seatbelt permite o deniega la red entera, Landlock no toca red hasta kernel 6.7, pf trabaja con IPs. `srt` de Anthropic lo resuelve pero es npm-only | 3 |
| Contrato de aceptación como dato ejecutable (`ruff.errors == 0`, `gitleaks.findings == 0`, `pytest.failed == 0`, `aria_snapshot(#checkout) matches …`) + normalizador SARIF de todas las herramientas | No existe. Sin esto el criterio de "terminado" es prosa que el agente reinterpreta a su favor, y cada herramienta nueva es un parche | 3 |
| Detector de auto-engaño sobre el **diff**: skips/xfail nuevos, asserts debilitados, `except: pass`, `noqa`/`type: ignore`/`eslint-disable` nuevos, entradas nuevas en `.gitleaksignore`, umbrales bajados, densidad de mocks + partición visible/oculta de la suite de aceptación | Es el aporte diferencial de Forge y no hay ni un proyecto OSS mantenido que lo haga. La literatura de 2026 da el método (suite retenida, datos base de mockeo) pero no hay implementación | 3 |
| Timeline de eventos con jerarquía (12 lecturas colapsan en una fila), altura fija, tres densidades, coalescing por `requestAnimationFrame` delante del stream | TanStack Virtual da offsets, no semántica. 50 eventos/segundo no son 50 renders/segundo | 3 |
| Cola de aprobación (una a la vez, acción citada literalmente, badge de reversibilidad como señal visual más fuerte) + superficie móvil de revisión de hunks con el pulgar | No existe patrón establecido; una lista de 30 pendientes produce aprobación en masa, que es lo mismo que no aprobar | 3 |
| Registro de herramientas en proceso con esquemas Pydantic v2 y permisos por herramienta; MCP relegado a integraciones externas | Goose delega todo en MCP, que cuesta un proceso y una serialización por llamada: inaceptable en las rutas calientes (leer, buscar, editar) | 2 |
| Tolerancia a modelo débil: parser de acciones tolerante a JSON roto, bucle de reparación de formato sin gastar el turno, degradación automática a un formato de edición más simple tras N fallos | Salvo mini-swe-agent, todos los runtimes asumen tool-calling nativo fiable | 2 |
| CAS mínimo de evidencia (traces, capturas, salidas SARIF) direccionado por contenido, con packs estilo restic para no crear millones de ficheros ni millones de PUT | No hay librería Python de content-addressable store con packs, índice y GC | 2 |
| **Total fase 1** | | **~50** |

### Trampas y licencias

1. **APCA no es libre.** `apca-w3`, `bridge-pca` y `SAPC-APCA` prohíben el uso comercial sin acuerdo escrito y firmado, y exigen dar acceso gratuito a Myndex Research para auditar cualquier integración comercial. Verificado en su `LICENSE.md`. Van a la denylist del repo con el motivo escrito. El gate de contraste se hace con la fórmula pública de WCAG 2.1 sobre culori, asumiendo que miente en modo oscuro.
2. **GSAP gratuito no es open source.** Licencia propietaria de Webflow, revocable, con cláusula que prohíbe usarlo en herramientas que permitan construir animaciones sin código y en "productos competidores". Forge construye interfaces: demasiado cerca. Motion (MIT irrevocable) cubre todo lo que necesitamos.
3. **Las reglas de Semgrep, no su motor.** El motor sigue LGPL-2.1; las reglas mantenidas por Semgrep pasaron a la *Semgrep Rules License v1.0*, limitada a uso interno, **no competitivo y no-SaaS**. Edecán es SaaS: copiar una sola regla contamina el producto. Si algún día hace falta SAST, Opengrep (LGPL-2.1, consorcio de appsec) con reglas escritas por nosotros.
4. **Nombres que engañan.** `Crush` se llama `FSL-1.1-MIT` y **no es MIT** (prohíbe uso competidor dos años). `hermes-ide` es BSL 1.1 con cláusula anti-IDE/terminal: se lee su `ARCHITECTURE.md` y se reimplementa, jamás se mira el código ni se copian identificadores. `mypy` y `pyright` aparecen como `NOASSERTION` en la API de GitHub, que no significa "sin licencia": hay que abrir el fichero.
5. **AGPL y BSL disfrazadas de herramienta CLI.** TruffleHog (AGPL-3.0), MegaLinter (AGPL-3.0), Daytona (AGPL-3.0 y core movido a repo privado en junio de 2026), runtime de Restate (BSL 1.1), Arize Phoenix (Elastic 2.0), `elkjs` (`EPL-2.0 OR GPL-3.0-or-later`), `pyte` (LGPL-3.0). Ninguna entra.
6. **GPL con excepción de linking.** `pygit2`/`libgit2` son GPL-2.0 con excepción que cubre **enlazar y distribuir la combinación**, no modificar y distribuir la modificación. Fase 1 los evita usando el binario `git`; el día que se usen, no se toca su código y se revisa antes de empaquetar Forge dentro de las apps nativas.
7. **CodeMirror archivó GitHub.** Los repos `codemirror/*` están archivados desde el 15-16 de abril de 2026 y el desarrollo vive en un Forgejo propio (`code.haverbeke.berlin`). La licencia sigue MIT y npm publica cada semana, pero ya no hay issues ni PRs públicos y el bus factor es de **una persona**. Vendorizar espejo del monorepo es obligatorio, no opcional.
8. **`sandbox-exec` está deprecado y no tiene sustituto.** Apple lo marcó hace años, sigue funcional en macOS 26/27, y el issue `apple/containerization#737` pidiendo calendario y reemplazo sigue sin respuesta a mediados de 2026. No existe otro mecanismo documentado para aplicar Seatbelt a procesos arbitrarios fuera del App Store. Vive detrás de `SandboxDriver` desde el día uno.
9. **Proyectos que parecen vivos y no lo están.** `comby` (3 commits en 16 meses, sin release desde 2022), `pytest-testmon` (8 meses sin commits, clasificado *Inactive*), Monaspace (sin release desde marzo de 2024 y declarada "not a supported product"), `rapidocr-onnxruntime` (congelado; el vivo es `rapidocr`), `pexpect`/`ptyprocess`, `google/zoekt` (archivado; vivo solo el fork de Sourcegraph), `stack-graphs` (archivado por GitHub en septiembre de 2025). Mirar solo "último push" da falsos positivos: hay que mirar releases y frecuencia real.
10. **Piezas que resuelven el problema del humano.** `react-diff-view` y familia (PR de tres ficheros con estética GitHub), Aider como runtime (pair programming interactivo, un commit por turno, git obligatorio), Semgrep (findings priorizados para un analista), `watchexec`, `tmux`, y sobre todo **Stagehand y browser-use**: queman una llamada al LLM por paso y hacen que el examinador sea el examinado. La verificación debe ser determinista; el LLM interpreta el resultado, no lo produce.
11. **Ataduras de nube.** Cloudflare Sandbox y Workflows no ven el disco del Mac y traen límites duros (1 MiB por resultado de step, 30 min por step, retención de 30 días, 10 GB por Durable Object, tope de 1.000 subrequests por request). E2B self-hosted exige Terraform sobre Nomad y Consul en GCP. `microsandbox` es pre-1.0 con un solo vendor y nube comercial en beta: exactamente el perfil de Daytona antes de cerrar. Regla: Forge depende de primitivas del sistema operativo, no de plataformas de sandbox de startups.
12. **Detalles que se descubren tarde y cuestan un rediseño.** El ABI de tree-sitter rompe entre minors (0.25→0.26 eliminó `Language.version` y `Parser.timeout_micros`): core y pack se suben en el mismo commit. Las 306 gramáticas tienen cada una su licencia. Shiki (TextMate) y CodeMirror (Lezer) colorean el mismo fichero ligeramente distinto. Las ligaduras convierten `!=` en un glifo y **ocultan un cambio de un carácter en un diff**: apagadas por defecto. Animar lo que llega del agente funde la CPU: solo se anima lo que inicia el humano. Y no se reporta jamás una cifra de SWE-bench Verified: está saturado cerca del 88% y OpenAI dejó de usarlo en febrero de 2026 por contaminación.

### La pieza más arriesgada

**CodeMirror 6.** Es la única dependencia que es a la vez estructural (todo el editor cuelga de ella), insustituible por requisito (Monaco está descartado porque su aspecto es parte de su API pública y porque no soporta táctil, y el usuario aprueba desde el iPhone) y mantenida por **una sola persona** que además archivó todos sus repos de GitHub en abril de 2026 y se llevó el tracker a un Forgejo propio. El proyecto no está muerto —npm publica semanalmente y la licencia sigue MIT—, pero ya no hay comunidad visible donde reportar un bug ni cola de PRs donde ver venir una regresión. El plan B **no es migrar de editor**, porque no hay a dónde: es asumir el mantenimiento. Concretamente, desde el primer sprint: (a) espejo vendorizado del monorepo `codemirror/dev` y `lezer` en nuestra infraestructura, con versiones fijadas por hash, no por rango semver; (b) todo el uso de CM6 pasa por `<ForgeEditor>` y una capa de extensiones propia, sin `basicSetup`, de modo que la superficie de API que tocamos sea pequeña y auditable; (c) presupuesto explícito de mantenimiento —una semana-persona por trimestre— para portar parches propios si el upstream se detiene. Como riesgo secundario del mismo tipo queda `sandbox-exec`: deprecado por Apple sin reemplazo publicado, pero ahí sí existe escapatoria real (`apple/container` sobre Virtualization.framework, Apache-2.0, funcional en esta Mac), y por eso el aislamiento nace detrás de `SandboxDriver` con dos backends desde el día uno.

---

## 11. Dirección de arte: cómo se ve y cómo se siente

### 11.1 La tesis en tres adjetivos: **sereno, denso, táctil**

La interfaz de agente típica es un muro de texto gris con un spinner. No es fea por accidente:
es fea porque nadie decidió nada. Aquí se decide.

**Sereno.** Forge muestra cientos de eventos por minuto. Si cada uno tiene derecho a color, a
movimiento o a una notificación, la pantalla es una alarma permanente y el humano deja de mirarla
—que es exactamente el fallo que mata al producto—. La serenidad no es decoración: es la única
forma de que el rojo signifique algo. Regla derivada: **el ruido base es acromático; el color es
un presupuesto escaso que se gasta en estado, nunca en identidad**. Rechazo explícito de
«vibrante», «enérgico» y «vivo»: un runtime que gasta dinero mientras duermes no puede parecer una
consola de videojuego.

**Denso.** La densidad es respeto por el tiempo del lector. El humano entra a entender en diez
segundos: necesita ver 40 filas, no 6 tarjetas con mucho aire. La estética de dashboard SaaS
—tarjetas de 120 px de alto, padding de 24, tres KPIs enormes— está diseñada para vender el
producto en una captura, no para operarlo a las tres de la mañana. Rechazo explícito de
«minimalista» y «espacioso»: aquí el vacío no es elegancia, es información que alguien tuvo que ir
a buscar a otra pantalla. La densidad se paga con jerarquía tipográfica estricta y con un grid de
4 px, no con letra pequeña.

**Táctil.** Todo lo accionable se siente accionable: tiene estado presionado, tiene blanco de
toque real, responde en menos de 100 ms y sobrevive a un pulgar. Este adjetivo existe para
defender un requisito duro del producto —se aprueba desde el teléfono— contra la deriva natural
de «panel de observabilidad de solo lectura». Forge no es un tablero que se mira: es una sala de
control que se agarra. Rechazo explícito de «etéreo», «flotante» y de todo el vocabulario del
glassmorphism, que hace que nada parezca tocable porque nada parece sólido.

Los tres se tensionan a propósito: *sereno* frena a *denso* (densidad sin ruido), *táctil* frena a
*denso* (44 pt de blanco de toque contra filas de 28 px: por eso hay dos densidades y no una), y
*sereno* frena a *táctil* (responde, pero no rebota). Un cuarto adjetivo rompería el sistema.

---

### 11.2 Las siete reglas

Cada una es accionable y tiene una forma de comprobarse. Si una regla no se puede verificar en CI
o en revisión con un criterio binario, no es una regla, es un deseo, y no entra.

**R1. Tres niveles de jerarquía tipográfica por panel. Ni uno más.**
Cada panel usa como máximo tres de los roles `title` / `body` / `meta`, con dos pesos (400 y 560)
y tres tamaños. Un cuarto nivel significa que el panel está respondiendo a dos preguntas y hay que
partirlo.
*Verificación:* los roles son clases utilitarias cerradas (`t-title`, `t-body`, `t-meta`, `t-code`);
un test de CI parsea el TSX de cada panel y falla si aparecen más de tres roles distintos o
cualquier `text-[...]` arbitrario.

**R2. El color solo dice estado, y nunca lo dice solo.**
Existen seis roles semánticos (§11.3) y ninguno más. Nada se colorea por decoración, por marca ni
por «alegrar». Y ningún estado se comunica únicamente por color: siempre color + glifo + palabra.
*Verificación:* lint que prohíbe literales de color (`#`, `rgb(`, `oklch(`) fuera de
`tokens.css`; suite de snapshots renderizada en escala de grises y con simulación de deuteranopia
en la que todos los estados siguen siendo distinguibles.

**R3. Nada gira y nada parpadea.**
Cero spinners, cero puntos suspensivos animados, cero *skeletons* pulsantes. El progreso es
determinado (barra, contador, n/m) o es un **verbo de estado + tiempo transcurrido**
(«Ejecutando pytest · 12 s»). Un tiempo que sube ya comunica que el sistema está vivo, y además
comunica cuánto lleva vivo, que es la información que el spinner esconde.
*Verificación:* `grep` en CI para `animate-spin`, `<Spinner`, `Loader`, `…` animado = 0 ocurrencias.

**R4. Un solo movimiento a la vez, ≤ 180 ms, y jamás sobre contenido que llega.**
Solo hay tres duraciones (§11.3). Lo que entra del modelo, del journal o de la red aparece con
*fade* en su sitio; no se desliza, no empuja lo que hay debajo y no reflowea la lista mientras el
cursor está encima. El movimiento se reserva para lo que el humano provoca.
*Verificación:* las únicas duraciones permitidas son `var(--dur-1|2|3)` (lint sobre CSS); test de
scroll-anchoring que inserta 200 eventos con el cursor a media lista y afirma que `scrollTop` del
elemento enfocado no cambia ni un píxel.

**R5. Todo cabe en 390 px y todo lo accionable mide ≥ 44 pt.**
Cada superficie tiene una forma legible en un iPhone sin scroll horizontal. No es «responsive» en
el sentido de encoger: la cola de aprobaciones y la revisión de hunks tienen un diseño móvil
propio (§11.4).
*Verificación:* Playwright a 390×844 sobre las siete rutas principales afirmando
`document.scrollingElement.scrollWidth === clientWidth`; auditoría de blancos de toque que recorre
todo elemento con handler de click y falla si su caja es menor de 44×44 px con puntero grueso.

**R6. Cada píxel de información es citable, buscable y copiable.**
Toda fila del journal, todo hunk y todo evento del timeline lleva `data-event-id` y tiene
permalink. La virtualización rompe el Ctrl-F del navegador y el «seleccionar todo»: por eso la
búsqueda propia (sobre el índice del servidor, no sobre el DOM) y el copiado propio (que devuelve
texto plano con los eventos completos, no solo los renderizados) son parte del día uno, no del
backlog.
*Verificación:* test que monta 5.000 eventos, afirma cobertura del 100 % de `data-event-id`, busca
un término presente solo fuera del viewport y comprueba que se encuentra y se salta a él; test de
copiado que selecciona un rango y compara con el texto canónico.

**R7. Presupuestos medidos, y degradación en vez de caída de frames.**
Ruta de sesión sin editor: **≤ 250 KB gzip**. Primer render útil con datos: **< 1 s** en la Mac de
referencia. Sostener **5.000 eventos** con p95 de frame **< 16 ms**. Cuando el caudal supera el
presupuesto, la UI **resume** (fila `+247 eventos menores`), no baja el frame rate ni tira eventos
en silencio.
*Verificación:* budget de bundle en CI (falla el build al superarlo) y una prueba de rendimiento
con traza de eventos grabada que mide p95 de frame y afirma que el contador de `dropped` es
visible cuando es distinto de cero.

---

### 11.3 Decisiones cerradas

Todo lo de esta sección está decidido. Se puede discutir con datos nuevos; no se puede dejar
abierto «para más adelante», porque un sistema visual a medio decidir se convierte en el promedio
de todas las opciones, que es exactamente el muro gris.

#### Tipografía

| Uso | Familia | Vía | Licencia | Verificado |
|---|---|---|---|---|
| Interfaz | **Inter Variable 4.1** | `@fontsource-variable/inter` 5.3.0 | SIL OFL 1.1 (`OFL-1.1` en el paquete) | npm 2026-07-19 |
| Código y datos | **Commit Mono** | `@fontsource/commit-mono` 5.3.0 | SIL OFL 1.1 | npm 2026-07-19 |

**Por qué Inter y no `system-ui`.** La misma UI vive en macOS, en Android, en WKWebView (Tauri) y
en un WebView de la app Kotlin. `system-ui` significa cuatro métricas distintas, cuatro alturas de
fila distintas y una densidad que no se puede garantizar. Se autoaloja subconjunto latin +
latin-ext (~110 KB woff2, dos ejes) con `size-adjust` sobre el fallback para que el CLS sea cero.
Features obligatorias: `"tnum" 1` (cifras tabulares en **todo** número que cambia: costes,
duraciones, contadores, líneas), `"cv05"` (la `l` con cola, que desambigua `1lI` en nombres de
archivo) y `"ss03"`.

**Por qué Commit Mono y no JetBrains Mono ni Berkeley Mono.** Commit Mono es deliberadamente
anónimo: no tiene personalidad que compita con la interfaz, tiene cursiva real (no oblicua
sintética, que en un tema de sintaxis se nota) y aguanta 12,5 px sin empastarse. JetBrains Mono
tiene una x-height y unas ligaduras que *son* una marca —y la marca de Forge no es la de otro—.
Berkeley Mono queda **descartada por licencia**: es comercial por puesto, incompatible con un
producto que se distribuye. SF Mono no es licenciable para web. Las ligaduras de código van
**desactivadas por defecto** (`"liga" 0, "calt" 0`): en un diff, `!=` convertido en `≠` cambia lo
que el humano cree que está aprobando.

#### Escala tipográfica

Base **13 px** —producto denso, no landing—. Pasos en píxeles enteros, no en `rem` fraccionarios,
porque el redondeo del subpíxel es lo que hace que una tabla se vea sucia:

```
--t-meta:    11px / 16px   400   +0.005em   (timestamps, tokens, rutas secundarias)
--t-small:   12px / 18px   400    0
--t-body:    13px / 20px   400    0          (por defecto)
--t-strong:  13px / 20px   560    0
--t-title:   15px / 20px   560   -0.006em    (cabecera de panel)
--t-head:    18px / 24px   560   -0.011em    (cabecera de vista)
--t-hero:    24px / 28px   560   -0.018em    (una por pantalla, o ninguna)
--t-code:  12.5px / 20px   400    0          (rejilla vertical de 20px, igual que el body)
```

Dos pesos: 400 y 560. Un tercero, 680, existe **solo** para el número del medidor de coste. Ancho
de medida en prosa (mensajes del agente, descripciones): máximo 82ch. El interlineado de código y
el del cuerpo comparten rejilla de 20 px a propósito: así una cita de código dentro del journal no
descuadra la fila.

#### Color

Espacio: **OKLCH nativo** con fallback hex por cascada. Verificado: `oklch()` y `color-mix()` son
baseline ampliamente disponible en 2026 (~93 % global; Safari desde 15.4). Se usa OKLCH porque
permite generar rampas donde el paso 9 de rojo y el paso 9 de verde tienen la *misma* luminosidad
percibida —imposible en HSL—, y eso es lo que hace que una lista de estados mezclados no parpadee.

**Neutral.** Rampa fría de 12 pasos, croma 0.004–0.018 en hue 255. Ningún gris puro: un neutral
con una traza de azul en claro y de azul-violeta en oscuro es lo que separa «sereno» de «muerto».

```
Claro                                   Oscuro
--bg-canvas   oklch(98.8% 0.003 255)    oklch(17.5% 0.012 265)   ← nunca #000
--bg-panel    oklch(100%  0     0  )    oklch(20.5% 0.013 265)
--bg-subtle   oklch(97%   0.004 255)    oklch(23.5% 0.014 265)
--bg-hover    oklch(95%   0.006 255)    oklch(26%   0.015 265)
--bg-active   oklch(92%   0.008 255)    oklch(29%   0.016 265)
--line        oklch(89%   0.008 255)    oklch(32%   0.016 265)
--line-strong oklch(80%   0.010 255)    oklch(40%   0.016 265)
--fg-faint    oklch(62%   0.012 255)    oklch(60%   0.015 265)
--fg-muted    oklch(50%   0.014 255)    oklch(72%   0.014 265)
--fg          oklch(28%   0.016 255)    oklch(90%   0.008 265)
--fg-strong   oklch(18%   0.018 255)    oklch(97%   0.004 265)
```

**Roles semánticos.** Seis, y cada uno con glifo obligatorio (R2):

| Rol | Significado exacto | Hue (claro / oscuro) | Glifo |
|---|---|---|---|
| `--ok` | terminó y el resultado es correcto | `oklch(58% 0.14 152)` / `oklch(72% 0.15 152)` | check |
| `--fail` | terminó y el resultado es incorrecto | `oklch(55% 0.19 27)` / `oklch(68% 0.18 27)` | x |
| `--wait` | **bloqueado esperando a un humano** | `oklch(64% 0.13 78)` / `oklch(80% 0.14 78)` | reloj |
| `--run` | en curso ahora mismo (índigo de marca, `#6366f1`) | `oklch(56% 0.19 277)` / `oklch(70% 0.16 277)` | punto |
| `--cost` | dinero y consumo | `oklch(58% 0.11 220)` / `oklch(74% 0.11 220)` | moneda |
| `--danger` | acción **irreversible** a punto de ejecutarse | `oklch(50% 0.21 27)` / `oklch(62% 0.20 27)` | triángulo |

Cuatro decisiones que hay que sostener:

1. **`--wait` es ámbar y el ámbar no significa nada más.** Ni «warning», ni «degradado», ni
   «obsoleto». Ámbar en Forge significa exactamente una cosa: *te está esperando a ti*. Esto
   convierte el color en un mapa de dónde poner los ojos, que es todo el punto.
2. **`--fail` (un test rojo) y `--danger` (borrar 3 archivos) no son el mismo rojo ni el mismo
   componente.** Un fallo es información y se pinta como texto e icono. Un peligro es una
   *superficie*: borde izquierdo de 3 px rayado a 45°, fondo `color-mix(in oklch, var(--danger)
   8%, var(--bg-panel))`, y es el **único** lugar del producto donde existe un botón sólido
   relleno de rojo. Confundirlos es cómo se entrena a un humano a ignorar el rojo.
3. **El coste no es rojo ni verde.** Verde/rojo en dinero significa ganancia/pérdida, que aquí es
   falso: gastar está bien, gastar de más no. El cian `--cost` colorea el carril y la esparcida
   del gasto; el **número** se pinta en `--fg-strong` neutro y solo cambia a `--wait` al 60 % del
   presupuesto y a `--fail` por encima del 90 %.
4. **Un solo acento.** `--run` es el índigo de Edecán que ya está en `tailwind.config.ts`
   (`brand-500 #6366f1`). No hay paletas de acento configurables, no hay «elige tu color». Una
   marca, un acento.

**Rampas categóricas** (inspector de contexto, carriles del timeline): 8 tonos generados en OKLCH
con L fija en 62 % (claro) / 72 % (oscuro) y croma 0.11, hues 25/60/110/155/200/245/290/330,
distinguibles bajo deuteranopia por rotación de hue + patrón de trama en el relleno. No comparten
espacio visual con los roles semánticos: los categóricos solo aparecen dentro de un gráfico, nunca
en una fila de estado.

**Contraste.** El *gate* de CI es **WCAG 2.2 AA medido** (4,5:1 texto, 3:1 elementos de UI y
estados de foco). APCA se usa solo como señal orientativa, nunca como criterio de aceptación:
verificado que APCA fue retirada del borrador de WCAG 3 en 2023 y que la especificación sigue
diciendo que el algoritmo de contraste está por determinar. Diseñar contra un estándar que no
existe es cómo se justifica el gris claro sobre blanco.

#### Densidad

**Dos densidades, no tres, y no configurables por el usuario.** Se eligen por
`@media (pointer: fine|coarse)`, no por un ajuste:

- `compact` (puntero fino): fila 28 px, `--t-body` 13 px, padding de panel 12 px, gutter 8 px.
- `touch` (puntero grueso): fila 44 px, `--t-body` 15 px, padding 16 px, gutter 12 px.

Rejilla base de **4 px** para todo espaciado: `4 8 12 16 24 32 48`. No existe el 6, ni el 10, ni el
14. Un panel de sesión en `compact` muestra ≥ 34 filas de journal a 900 px de alto: ese es el
número que se defiende en cada revisión de diseño.

#### Radios y forma

```
--r-1: 4px    controles, filas, celdas, pills de estado pequeñas
--r-2: 8px    paneles, tarjetas, hunks
--r-3: 12px   modales, sheets móviles, popovers
--r-full      solo avatares y el anillo de coste
```

Decisión de carácter: **el código es cuadrado, el chrome es redondo.** El editor, el gutter, el
minimapa temporal y los bloques de salida de comando tienen radio 0 y llegan al borde de su
contenedor. El contraste entre la superficie técnica (recta, a sangre) y la superficie de control
(redondeada, con margen) es lo que da identidad sin recurrir a color ni a adornos.

#### Elevación

**Tres niveles y ninguno se define con sombra en oscuro.**

- **E0 · lienzo**: `--bg-canvas`, sin borde.
- **E1 · panel**: `--bg-panel` + borde 1 px `--line`. En claro, además, `0 1px 2px rgb(0 0 0/.04)`.
  En oscuro, **no hay sombra**: la elevación se lee como superficie más clara (+3 % L). Poner
  sombras negras sobre fondo oscuro es la razón por la que la mayoría de temas oscuros parecen
  sucios.
- **E2 · flotante** (menús, popovers, sheets): sombra `0 8px 24px -8px rgb(0 0 0/.18)` en claro,
  `0 12px 32px -12px rgb(0 0 0/.5)` + borde `--line-strong` en oscuro, y `backdrop-filter: blur(12px)`
  **solo** aquí, sobre un área que no scrollea.

Prohibido: *glow*, sombras de color, bordes de neón, doble capa de cristal, y `backdrop-filter`
sobre cualquier superficie con scroll (destruye el presupuesto de frame de R7).

#### Movimiento

```
--dur-1:  90ms   feedback de estado (hover, press, foco, check)
--dur-2: 160ms   entrada/salida de elementos, plegado de una fila
--dur-3: 260ms   cambio de layout, panel que se abre, sheet móvil
--ease-out: cubic-bezier(.2, 0, 0, 1)      entra
--ease-in:  cubic-bezier(.3, 0, 1, 1)      sale
--spring:   solo manipulación directa (splitter, swipe de hunk), stiffness 380 damping 34
```

**Se anima:** el plegado/desplegado de un comando en el journal (altura); el `check` de un hunk
aprobado y su colapso; el número del medidor de coste (§11.4.5); el borde del agente enfocado; la
transición de elemento compartido entre la lista de archivos y el archivo abierto; el *sheet* de
aprobación en móvil; el deshacer de 5 s (una barra que se vacía, determinada).

**No se anima jamás:**

- **El texto del modelo.** Nada de efecto máquina de escribir. El stream se pinta en su sitio a la
  cadencia real; simular escritura es mentir sobre la latencia y hace ilegible lo que ya llegó.
- **Números que cambian más de 4 veces por segundo.** Por encima de eso, el valor se actualiza en
  cadencia fija de 500 ms sin rodillo. Los céntimos no giran.
- **Filas de una lista virtualizada.** Ni al entrar, ni al reordenar. Una lista que se mueve sola
  mientras se lee es una lista que se odia.
- **La ruta crítica de aprobación.** El botón de aprobar no tiene retardo, ni animación de
  confirmación, ni diálogo que se desvanezca. Tiene un estado de confirmación y un deshacer.
- **Nada durante la carga.** Por debajo de 300 ms no se muestra nada; por encima, estructura real
  con los datos que ya se conocen, estática.

`prefers-reduced-motion: reduce` pone `--dur-2` y `--dur-3` a 0, deja solo *crossfades* de 90 ms,
convierte el auto-scroll en salto instantáneo y desactiva los muelles. No es una degradación: es
un modo probado en CI con su propia tanda de snapshots.

**Regla de scroll (transversal):** el journal sigue la cola **solo** si el usuario ya está a menos
de 40 px del final. En cuanto sube, aparece una píldora `↓ 213 eventos nuevos` y el scroll deja de
moverse. Ningún panel secuestra el scroll, nunca.

---

### 11.4 Los seis patrones

#### 11.4.1 El agente pensando: la **tira de intención**

**Problema.** Los modelos emiten razonamiento a chorro. Volcarlo produce el muro gris; ocultarlo
produce la caja negra. Y con varios agentes, el volumen es imposible.

**Cómo se ve.** Una tarjeta fija de **dos líneas** por agente:

```
▸ ●  Ejecutando  pytest -k phone -x                                    00:12
     Objetivo: que test_phone pase sin tocar la API pública      ▸ razón
     ▁▂▃▅▃▂▁ (carril de actividad, 40×8 px, canvas)
```

- **Línea 1 — verbo + objeto.** El verbo sale de un vocabulario **cerrado de doce**: `leyendo`,
  `buscando`, `escribiendo`, `ejecutando`, `probando`, `pensando`, `esperando`, `preguntando`,
  `aplicando`, `revirtiendo`, `publicando`, `pagando`. Cada uno tiene su icono y su color de rol.
  Doce verbos con doce iconos se leen sin leer: a los dos días el humano reconoce la forma.
- **Línea 2 — la intención**, una frase, generada una vez al abrir la tarea y no reescrita en cada
  turno. Es la respuesta a «¿por qué está haciendo esto?».
- **El razonamiento crudo** vive detrás del chevron y **nunca está expandido por defecto**. Cuando
  se expande, se renderiza en un contenedor de **altura máxima de 3 líneas con máscara de
  degradado inferior**: el texto se reemplaza, no crece. El muro es físicamente imposible porque
  el contenedor no puede crecer.
- Al terminar, la tarjeta colapsa a **una** línea en el timeline: `✓ Ejecutó pytest -k phone · 12,4 s · exit 0`.

**A diez veces.** Con 10 agentes, la tira es una pila de 10 tarjetas de dos líneas ordenadas por
**urgencia para el humano**, no por tiempo: `esperando` > `preguntando` > `fallo reciente` >
`ejecutando` > `terminado`. Solo la tarjeta enfocada hace stream; el resto muestra verbo + tiempo.
Con 100, se agrupan por tarea y la fila del grupo muestra `7 ejecutando · 2 esperando · 1 fallo`,
con el peor estado dominando el color.

#### 11.4.2 El timeline de miles de eventos

**Problema.** Una sesión larga son 20.000 eventos. El humano necesita la *forma* de la ejecución
—dónde se atascó, dónde falló, dónde le pidió permiso— y saltar ahí en un gesto.

**Cómo se ve.** Dos superficies acopladas:

1. **Minimapa temporal**, 56 px de alto, canvas, ancho completo, **es la barra de scroll**. Cada
   columna es un *bucket* temporal; su **altura** codifica densidad de eventos (nunca la opacidad:
   los degradados de opacidad se leen como suciedad) y su **color** la clase dominante. Encima,
   marcas de 2 px: roja por fallo, ámbar por espera de humano, cian por gasto significativo. La
   forma de la sesión se ve de un vistazo.
2. **Journal virtualizado** (TanStack Virtual), rejilla monoespaciada de tres columnas:
   `tiempo relativo (tabular) · icono de verbo · contenido`. Los comandos vienen **plegados**:
   una fila `pytest -k phone · 12,4 s · exit 1`; al desplegar aparece la salida **resuelta** por
   `@xterm/headless` —sin escapes ANSI, sin barras de progreso, sin `\r`— con su propia
   virtualización interna.

Cada fila lleva `data-event-id`, tiene permalink y se puede citar al chat con `⌘⇧C` (R6).

**A diez veces.** El minimapa **nunca** dibuja más de 800 columnas: al crecer, ensancha buckets.
El journal cambia de «todo evento» a «eventos que cambiaron algo» y mete filas de resumen
plegables: `+247 eventos menores`. La búsqueda es propia y va contra el índice del servidor, con
resultados marcados en el minimapa como marcas violetas: buscar es también ver dónde está lo que
buscas.

#### 11.4.3 La revisión de un diff de 400 archivos

**Problema.** Nadie revisa 400 archivos. Si la UI se limita a listarlos, el humano aprueba a
ciegas —el peor resultado posible—. El trabajo de la interfaz no es mostrar el diff: es **producir
un orden y un presupuesto**.

**Cómo se ve.** Tres columnas en escritorio.

**(a) Carril de triaje.** Los 400 archivos se reparten automáticamente en cuatro cubos, con conteo
y acción de cubo:

- **Requiere ojos** — cambió la lógica (lo decide `difft --display json`: cambios semánticos > 0).
- **Mecánico** — renombrados, reindentado, orden de imports, formateo (cambios semánticos = 0).
- **Generado** — lockfiles, snapshots, migraciones, `*.pb.go`: plegado por defecto, con el hash.
- **Borrado** — separado siempre, porque borrar es lo que no se puede deshacer barato.

**(b) El archivo.** CodeMirror 6 + `@codemirror/merge`, hunks con aceptar/rechazar individuales.
Un archivo de 400 líneas mecánicas se resume arriba: *«3 cambios semánticos, 412 líneas de
reindentado»*, con enlace a los tres.

**(c) Contexto del hunk.** Qué agente lo escribió, en respuesta a qué tarea, y **qué prueba lo
cubre**: un punto verde si un test que toca esas líneas pasó después del cambio, gris si ninguno
lo toca. Esta columna es la que convierte revisar en decidir.

Arriba, una barra de **400 segmentos** (revisado / pendiente / aprobado): el humano ve el final
desde el principio. Teclado: `j/k` archivo, `n/p` hunk, `a` aprobar hunk, `A` archivo, `x`
rechazar con comentario —y el comentario **entra al journal como evento** y le llega al agente, no
va a un hilo aparte.

**A diez veces (4.000 archivos).** El carril vira a agrupación por directorio y se virtualiza; la
virtualización es anidada (lista de archivos + hunks dentro del abierto). Y hay un límite honesto:
**si «Requiere ojos» supera 50 archivos, la UI deja de fingir** que se puede revisar y ofrece dos
salidas explícitas —resumen estructural agregado (qué símbolos cambiaron en todo el changeset) y
aprobación por política («todo lo que solo añade casos bajo `tests/`»)—, ambas registradas como lo
que son: aprobación no exhaustiva.

**En móvil.** No hay tres columnas. Una tarjeta por hunk, diff **unificado** a 15 px, swipe
derecha aceptar / izquierda rechazar, y un botón permanente `Aprobar los 312 mecánicos`. El
teléfono aprueba; no audita. Prometer auditoría en un móvil es cómo se consigue que alguien apruebe
un `rm -rf` con el pulgar.

#### 11.4.4 La cola de aprobaciones

**Problema.** El agente se bloquea y el cuello de botella pasa a ser la latencia del humano. Pero
aprobar rápido y aprobar a ciegas se parecen mucho; la UI tiene que hacer lo primero fácil y lo
segundo difícil.

**Cómo se ve.** **Una sola cola global**, por workspace, no notificaciones dispersas por sesión.
Cada tarjeta responde a cuatro preguntas en cuatro líneas, siempre en el mismo orden:

```
Ejecutar  git push --force origin main                        [peligro]
Si apruebas   reescribe 3 commits en el remoto; no reversible
Si rechazas   el agente propondrá un merge; 1 agente sigue parado
Pide          agente «refactor-phone» · tarea T-114 · lleva 4 m
```

- **Reversible**: un toque, y **deshacer de 5 s** (barra determinada). Deshacer, no confirmar: un
  diálogo de confirmación en cada acción entrena a decir que sí.
- **Irreversible**: contenedor `--danger` (§11.3), y es el único sitio con botón rojo sólido. Y el
  botón está **al otro lado** de donde está el de aprobar en las tarjetas reversibles: la memoria
  muscular no debe poder ejecutar un `--force`.
- **Orden por coste de bloqueo**, no por llegada: primero lo que tiene más agentes parados detrás.
  El número de agentes bloqueados va en la tarjeta.

**A diez veces (40 pendientes).** Se agrupan por clase —`12 escrituras en apps/web/`,
`8 comandos de red`— con aprobación en bloque que muestra la **unión** de consecuencias antes de
ejecutar. Y hay un límite duro: superado el umbral, **el runtime pausa el spawn de agentes nuevos
en vez de dejar crecer la cola**, y la UI lo dice con todas las letras: *«Forge pausó 3 agentes:
tu cola está llena»*. Una cola infinita es un producto que ya perdió.

**Móvil es la superficie primaria de este patrón**, no una adaptación: push → tarjeta → pulgar.

#### 11.4.5 El medidor de coste y presupuesto

**Problema.** El dinero se gasta mientras duermes. Un número que cambia veinte veces por segundo
no se lee: genera ansiedad y se ignora, que es lo peor de ambos mundos.

**Cómo se ve.** Un solo elemento en la barra superior: un **anillo** (no barra: el anillo dice
«fracción de un total» sin necesitar escala) con el porcentaje de presupuesto de sesión consumido
y, en el centro, el importe en cifras tabulares con peso 680. Debajo, dos datos que son los que
convierten el número en acción:

```
      ╭───────╮
      │ 4,82  │   1,80 USD/h
      ╰───────╯   agota el límite en ~2 h 10 m
```

Al abrir: gráfico de carriles apilados por causa (**modelo / herramientas / cómputo**) sobre el
tiempo, con la rampa categórica, más las tres líneas más caras del día.

- **Cadencia.** `@number-flow/react` anima el importe **solo** cuando cambia menos de 4 veces por
  segundo; por encima, actualización en cadencia fija de 500 ms sin rodillo (R4).
- **Color.** Neutro hasta el 60 %, `--wait` de 60 a 90 %, `--fail` por encima. El anillo es el
  único sitio del producto donde aparece rojo sin una confirmación detrás, y por eso vale.
- **La proyección** («agota en ~2 h 10 m») se calcula sobre los últimos 15 minutos, no sobre toda
  la sesión: una tasa promediada sobre 8 horas no predice nada.

**A diez veces.** El anillo pasa a ser del workspace y se despliega en un carril por agente,
ordenados por gasto, para poder matar al caro. Histórico: heatmap de calendario de **90 celdas
como máximo** —tres meses—; más allá, agregado mensual. Nunca un gráfico de línea de todo el
tiempo: nadie ha tomado nunca una decisión con eso.

#### 11.4.6 El inspector de contexto

**Problema.** La calidad del agente es una función de lo que entró en su ventana. Hoy eso es
invisible, y es la causa número uno de «¿por qué hizo eso?». Es también el patrón que ningún
producto de la competencia tiene bien, así que es donde se gana.

**Cómo se ve.** Dos vistas.

**(a) La ventana como barra apilada.** Una barra horizontal a ancho completo, un segmento por
fuente —sistema, instrucciones del repo, archivos, salida de herramientas, historial, resumen—,
con porcentaje y tokens absolutos, en rampa categórica (no semántica: aquí el color no es estado).
Al pulsar un segmento, la lista de sus elementos ordenada **por coste en tokens**, y cada elemento
con tres acciones: **ojo** (ver exactamente el texto que entró, no una aproximación), **pin**
(forzar permanencia) y **tijera** (expulsar ahora). Como todo es direccionable por contenido, un
elemento muestra `también en 3 sesiones`, que es cómo se detecta el prompt basura que se arrastra
en todas partes.

**(b) El libro de lo que se cayó.** La vista que de verdad importa: el registro de compactación y
expulsión. `Se resumió el turno 12–34 (18.400 → 900 tokens)`, con enlace al original completo.
Esta es la respuesta literal a «por qué se le olvidó», y hoy no existe en ningún producto.

**A diez veces (ventana de 1M, 200 elementos).** Los segmentos se agrupan por tipo con «top 10 por
coste» y una cola larga resumida; la lista se virtualiza. Y la vista primaria para depurar deriva
pasa a ser el **diff entre el contexto del turno N y el N+1**: qué entró, qué salió, qué se
resumió —renderizado con el mismo componente de diff del patrón 3, porque es el mismo problema.

---

### 11.5 La pila técnica de la interfaz

Todo verificado en el registro de npm el 2026-07-27.

| Pieza | Versión | Licencia | Por qué |
|---|---|---|---|
| **Next.js 15.5 + React 19** | 15.5.21 / 19.2.3 | MIT | Ya está en producción en `apps/web`; los RSC permiten colorear con Shiki en servidor y que la revisión de 400 archivos llegue pintada sin JS. |
| **Tailwind CSS** | 4.3.3 (2026-07-16) | MIT | Los tokens de §11.3 se declaran una vez en `@theme` como variables CSS nativas; sin config JS, y el lint de «nada de colores literales» se vuelve trivial. Sube desde el 3.4.4 actual. |
| **Base UI** (`@base-ui/react`) | 1.6.0 (2026-06-18) | MIT | Primitivas accesibles **sin un solo estilo** (diálogo, popover, menú, tooltip, select): la accesibilidad viene resuelta y el aspecto sigue siendo 100 % nuestro. Lo mantiene a tiempo completo el equipo que construyó Radix. |
| **Radix Primitives** (`radix-ui`) | 1.6.7 (2026-07-24) | MIT | Solo como red: si falta una primitiva en Base UI, se toma de aquí. Sigue vivo pero con cadencia menor tras la adquisición por WorkOS. |
| **Motion** (`motion`) | 12.42.2 (2026-06-30) | MIT | Única dependencia de animación; se importa con `LazyMotion` + `m` para no pagarla en el bundle base, y sus muelles solo se usan en manipulación directa. |
| **Lucide** (`lucide-react`) | 1.27.0 (2026-07-25) | ISC | Un solo grosor de trazo, 1.600+ iconos, tree-shakable. Congelamos un subconjunto de ~40 en un sprite local: el set de iconos es una decisión de diseño, no un import libre. |
| **TanStack Virtual** | 3.14.8 (2026-07-22) | MIT | Headless: devuelve índices y offsets, cero DOM y cero CSS impuestos. La belleza sale gratis por construcción. |
| **CodeMirror 6** + `@codemirror/merge` | view 6.43.7 / merge 6.12.2 | MIT | El editor no trae CSS: el tema es un objeto nuestro. Y edita sobre `contenteditable` real, así que el teclado del iPhone funciona. |
| **Shiki** | 4.3.1 (2026-07-03) | MIT | Coloreado de todo lo de solo lectura, en servidor, vía `codeToHast` para que las clases sean nuestras. |
| **`@number-flow/react`** | 0.6.2 (2026-07-18) | MIT | Solo el importe del medidor de coste. Nada más del producto tiene derecho a números animados. |
| **xterm.js** + webgl | 6.0.0 | MIT | Carga diferida, solo el panel de TTY crudo. Es el componente menos tematizable de la lista y por eso va enmarcado y aislado. |
| **Inter Variable** | `@fontsource-variable/inter` 5.3.0 | SIL OFL 1.1 | Métricas idénticas en macOS, Android y WKWebView; cifras tabulares. |
| **Commit Mono** | `@fontsource/commit-mono` 5.3.0 | SIL OFL 1.1 | Monoespaciada anónima, con cursiva real, legible a 12,5 px. |
| **OKLCH + `color-mix()`** | nativo | — | Baseline ampliamente disponible en 2026; permite rampas con luminosidad percibida constante entre tonos. |
| **Gráficos** | ninguno | — | Los cuatro gráficos del producto (minimapa, anillo, carriles apilados, heatmap) son SVG/canvas propio de menos de 200 líneas cada uno. Ver §11.6. |
| **Tauri v2** | 2.11.5 | Apache-2.0 OR MIT | Fase 2, para el residente de macOS; implica probar en Safari/WKWebView en CI desde el primer día. |

Presupuesto objetivo, medible en CI: **ruta de sesión sin editor ≤ 250 KB gzip**; con editor
`≤ 250 + 186` KB; con terminal, `+123` KB solo para quien la abre.

---

### 11.6 Lo que NO vamos a hacer

- **Terminal verde sobre negro, neón, «cyberpunk», rejillas y *scanlines*.** Es la tentación
  número uno de un IDE de agentes y produce una interfaz que se ve genial en una captura y es
  ilegible a las dos horas. Contraviene *sereno* y falla WCAG 2.2 AA casi por definición.
- **Negro puro `#000` en modo oscuro.** Con texto blanco produce halación en OLED y, sobre todo,
  impide construir elevación por luminosidad (§11.3). Nuestro lienzo oscuro es 17,5 % de L.
- **Un solo spinner.** Ninguno. Ver R3.
- **Efecto máquina de escribir en la salida del modelo.** Miente sobre la latencia, impide leer lo
  que ya llegó y no aporta ni un bit de información.
- **Glassmorphism sobre contenido con scroll.** Se permite `blur` en E2 y en ningún otro sitio;
  todo lo demás es destruir el presupuesto de frame a cambio de una captura bonita.
- **Una librería de gráficos (Recharts, visx, Chart.js, ECharts, Nivo).** Todas traen su DOM, sus
  ejes por defecto, sus tooltips y su aspecto —el mismo aspecto que tienen otros mil productos—.
  Tenemos cuatro visualizaciones, todas pequeñas y todas muy específicas; una librería genérica
  costaría más en pelearla que en escribirlas.
- **Una librería de componentes con estilo (MUI, Chakra, Ant, Mantine).** Adoptar cualquiera es
  renunciar al requisito. Tomamos Base UI, que no tiene aspecto, y escribimos los componentes.
- **El aspecto de `shadcn/ui` como sistema.** Sus recetas se leen; su estética —zinc + `rounded-lg`
  + borde de 1 px— se ha convertido en el uniforme de las startups de IA de 2025-2026. Copiarla es
  ser indistinguible justo en la dimensión donde el usuario pidió lo contrario.
- **El aspecto de VS Code.** Ya está razonado el descarte de Monaco; la prohibición se extiende al
  tema: nada de barra de actividad de 48 px a la izquierda, ni pestañas con el icono del lenguaje,
  ni minimapa de código. El minimapa de Forge es temporal, no espacial.
- **Emoji como estado.** Ni en la UI, ni en los mensajes del agente, ni en el journal. El estado se
  dice con doce iconos de trazo consistente y una palabra.
- **Ilustraciones, mascotas y *empty states* con personaje.** Un estado vacío dice qué falta y da
  la acción para llenarlo, en una línea.
- **Toasts apilados.** Las aprobaciones son una cola, no notificaciones que se van solas. El único
  toast permitido es el de deshacer, uno cada vez, 5 segundos, con barra determinada.
- **Skeletons pulsantes.** Por debajo de 300 ms no se muestra nada; por encima, estructura real y
  estática.
- **Barras de progreso falsas.** Si no hay denominador, se muestra tiempo transcurrido. Nunca se
  inventa un porcentaje.
- **Temas configurables, selector de acento, densidad ajustable, *theme builder*.** Dos temas
  (claro y oscuro), un acento, dos densidades elegidas por el tipo de puntero. La estética
  configurable es cómo un producto deja de tener estética.
- **Auto-scroll que secuestra la vista.** Ver la regla de scroll de §11.3.
- **Copiar el layout de tres paneles del IDE clásico** (árbol · editor · terminal). En Forge el
  humano casi nunca es quien escribe: la superficie por defecto es sesión + journal + cola de
  aprobaciones, y el editor es un panel que se invoca, no el centro de la pantalla.

---

**Fuentes verificadas** (julio de 2026): registro npm para versiones y licencias de `motion`
(12.42.2, MIT), `lucide-react` (1.27.0, ISC), `tailwindcss` (4.3.3, MIT), `@base-ui/react` (1.6.0,
MIT), `radix-ui` (1.6.7, MIT), `@number-flow/react` (0.6.2, MIT), `@tanstack/react-virtual`
(3.14.8, MIT), `shiki` (4.3.1, MIT), `@fontsource-variable/inter` y `@fontsource/commit-mono`
(5.3.0, OFL-1.1); [Lucide 1.0 y su licencia ISC](https://lucide.dev/license) y
[la cobertura del cambio](https://www.infoq.com/news/2026/06/lucide-v1-icons/);
[Base UI](https://base-ui.com/) y su estado frente a
[Radix Primitives](https://www.radix-ui.com/primitives/docs/overview/releases);
[Motion](https://github.com/motiondivision/motion) (MIT);
[Inter, SIL OFL](https://github.com/rsms/inter/blob/master/LICENSE.txt) y
[Commit Mono, SIL OFL 1.1](https://commitmono.com/);
[estado de APCA y WCAG 3 en abril de 2026](https://adrianroselli.com/2026/04/wcag3-contrast-as-of-april-2026.html);
soporte de [`oklch()` en CSS](https://css-tricks.com/almanac/functions/o/oklch/).

---

## 12. Superficies: Mac, iPhone y Android

Tres pantallas, un solo sistema. Esta sección existe porque la tentación obvia
—hacer que el teléfono muestre el Mac— es la decisión equivocada, y conviene
cerrarla por escrito antes de que alguien la implemente.

### 12.1 La regla que gobierna las tres

Por la invariante 2, ningún cliente tiene estado autoritativo: **todas las
superficies son proyecciones del mismo journal, suscritas por cursor.** De ahí
se sigue algo que parece un regalo y es en realidad la consecuencia lógica del
diseño: *el espejo en vivo entre Mac y teléfono no hay que construirlo.* Si el
iPhone y el Mac están suscritos al mismo journal, ya están sincronizados. Lo
único que hay que diseñar es qué proyecta cada uno.

Y de ahí se sigue el rechazo explícito: **el teléfono no es una vista remota del
escritorio.** Nada de streaming de píxeles, VNC ni una web embebida encogida. Eso
es lo que hace un puente remoto —lo que ya tenemos hoy y estamos tirando— y falla
por lo mismo de siempre: latencia, texto ilegible, gestos que no existen, y una
dependencia dura de que el Mac esté encendido para poder *mirar*.

### 12.2 El Mac: dos vistas, no veinte paneles

La referencia es Antigravity y Cursor, y de ellas se toma una cosa concreta: la
separación entre **trabajar** y **supervisar**.

**Vista Editor** — para cuando estás metido en el código.

```text
┌────────────┬────────────────────────────────────┬──────────────────┐
│ Explorador │  Editor  (ForgeEditor / CodeMirror)│  Agente          │
│ + búsqueda │  pestañas · diff en línea          │  plan · pasos    │
│            ├────────────────────────────────────┤  herramientas    │
│            │  Terminal · Problemas · Preview     │  coste           │
└────────────┴────────────────────────────────────┴──────────────────┘
```

**Vista Agentes** — mission control. Es la vista por defecto cuando hay trabajo
largo en marcha, y es donde vive la diferencia con un editor normal.

```text
┌──────────────────────────────────────────────────────────────────┐
│  ● migrar-pagos      ejecutando   paso 12/28   $0.84   ⏱ 41 min   │
│  ● tests-android     esperando aprobación ⚠     $0.11   ⏱  6 min   │
│  ○ auditar-sql       completado   ✓ 4/4 criterios  $0.31          │
├──────────────────────────────────────────────────────────────────┤
│  Línea de tiempo         │  Artefactos           │  Aprobaciones   │
│  minimapa de eventos     │  plan · diff · captura│  cola con lote  │
└──────────────────────────────────────────────────────────────────┘
```

Del diseño de Antigravity se toma también el concepto de **artefacto**: el agente
no entrega un log, entrega cosas revisables —un plan, un diff agrupado, una
captura, una grabación del navegador, la salida de las pruebas—. Un humano
verifica un artefacto en segundos y un log en minutos.

«Simple pero inteligente» se traduce en una regla ejecutable: **la pantalla
muestra por defecto lo que necesita decisión, y esconde lo que no.** Un agente que
va bien ocupa una línea. Un agente atascado o esperando aprobación se expande
solo. La densidad no la eliges tú: la elige el estado del trabajo.

### 12.3 iPhone y Android: el IDE como pestaña

El IDE es una pestaña más del tab bar, junto a las que ya existen. No es una app
aparte y no intenta ser un escritorio.

Cuatro pestañas dentro del IDE móvil, y **el orden importa**, porque declara para
qué sirve el teléfono:

| Pestaña | Para qué |
|---|---|
| **Agentes** | Qué está pasando ahora mismo. Es la pantalla de inicio |
| **Revisar** | La cola de aprobaciones y los diffs. Es *la* razón de que el IDE esté en el teléfono |
| **Terminal** | Solo lectura por defecto; escribir exige un gesto deliberado |
| **Archivos** | Explorar y editar. Deliberadamente el último |

La decisión de fondo: **en el teléfono el trabajo no se hace, se dirige.** Aprobar
un despliegue desde la cama, ver por qué se atascó un agente en un semáforo, o
mandar una corrección de dos frases: eso sí. Editar 400 archivos: no, y no
pasa nada porque no.

Consecuencias técnicas de esa decisión, que ya estaban en el diseño y ahora se
concretan:

- **Nativo, no web.** SwiftUI y Compose consumiendo la misma proyección. Ya hay
  base en `apps/mobile/ios/EdecanApp` y `apps/mobile/android`.
- **Diff táctil.** Revisar en una pantalla de 6 pulgadas exige agrupación
  semántica por paso del plan, no lista plana de archivos. Es el mismo motor de
  agrupación de la vista de escritorio, con otra densidad.
- **Aprobación con contexto suficiente.** Un botón de aprobar sin ver qué se
  aprueba es peor que no tener botón. La cola muestra qué cambia, qué es
  irreversible y qué criterios ya pasaron.

### 12.4 El espejo en vivo

Hay dos cosas que se sincronizan y conviene no confundirlas.

**Lo que ya está sincronizado, gratis**: el estado del trabajo. Ambos clientes
leen el mismo journal por cursor; el teléfono no le pregunta nada al Mac. Si el
Mac se apaga a mitad, el teléfono sigue mostrando el estado real hasta el último
evento escrito, y lo dice claramente en vez de fingir que sigue vivo.

**Lo que sí hay que diseñar: el modo seguir.** Un interruptor explícito que ata
el foco del teléfono al del Mac —qué agente miras, qué archivo, qué punto de la
línea de tiempo—. Se transporta como evento efímero, del canal `emit`, y **nunca
entra al journal**: dónde estabas mirando no es un hecho del proyecto.

Tres detalles que deciden si esto se siente bien o mal:

1. **Late join.** Al abrir el teléfono, la proyección se rehidrata desde el
   cursor guardado, no desde el principio. Un journal de 40.000 eventos no se
   descarga entero.
2. **Sin red no miente.** La superficie muestra el desfase real («al día hace
   3 s» / «sin conexión desde las 20:14»), y las aprobaciones se bloquean si el
   cursor está rancio. Aprobar sobre un estado viejo es la forma más fácil de
   autorizar algo que ya no es lo que creías.
3. **La aprobación es idempotente y con dueño.** Si apruebas desde el teléfono y
   el Mac tenía el mismo diálogo abierto, gana la primera y la segunda se cierra
   sola, sin doble ejecución. Esto sale del `EffectLedger` (§5), no de un truco
   de interfaz.

### 12.5 Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| Streaming de píxeles del Mac al teléfono | Latencia, ilegibilidad, gestos inexistentes, y exige que el Mac esté encendido para poder *mirar* |
| App móvil separada del IDE | Duplica identidad, sesión y notificaciones. El IDE es una capacidad de Edecán, no un producto aparte |
| Web embebida en el móvil | Ahorra trabajo hoy y arruina el diff táctil y las notificaciones, que son justamente lo que hace útil el teléfono |
| Paridad total de funciones entre Mac y móvil | Un objetivo que suena a rigor y produce dos interfaces mediocres. Cada superficie hace bien lo suyo |

### 12.6 Cómo se rompe

- **El teléfono se vuelve un log gris.** Es el fallo más probable: la cola de
  aprobaciones se llena de cosas triviales, el usuario aprende a aprobar sin
  leer, y la supervisión se convierte en teatro. Defensa: la clasificación de
  efectos (§5) tiene que ser buena de verdad, y hay un contador visible de
  aprobaciones por sesión — si sube, algo está mal clasificado.
- **El modo seguir se pelea con el usuario.** Si el foco salta mientras lees, es
  insoportable. Defensa: seguir es opt-in, se rompe solo en cuanto tocas la
  pantalla, y se vuelve a atar con un gesto explícito.
- **Divergencia de dos implementaciones nativas.** iOS y Android derivando en
  comportamiento es el riesgo clásico. Defensa: la proyección y sus reglas viven
  en el servidor; los clientes pintan, no deciden.

---

## 13. Coherencia: contradicciones resueltas y huecos asignados

Los diez bloques fueron diseñados por equipos independientes y endurecidos cada uno por un
adversario. Un revisor final los leyó juntos y encontró lo que ninguno podía ver solo. Esta
sección no es un anexo: **es la que evita que el sistema se construya con diez dialectos**.

### 13.1 Las doce contradicciones y su resolución

| Entre | El problema | Resolución vinculante |
|---|---|---|
| Bloques 1, 4, 5, 6 | **Cuatro máquinas de estados** para la misma cosa: una llamada a herramienta | Una sola, propiedad del bloque 1. Los estados del bloque 5 (`validated/authorized/queued/dispatched`) pasan a ser subestados declarados de `admitted`. `unknown` e `indeterminate` se unifican |
| Bloques 4, 5, 6, 7 | **Cuatro claves de idempotencia con semánticas opuestas**, no variantes | Se separan dos conceptos con dos nombres: `cache_key` (centrada en contenido, para cachear resultados de herramientas puras) e `idempotency_key` (centrada en efecto, para no ejecutar dos veces un despliegue). Solo el bloque 7 lo tenía bien |
| Bloques 3, 5, 6, 7 | **Cuatro autoridades de presupuesto**, dos de ellas contradiciéndose por nombre | Gana el `BudgetAuthority` del bloque 7: un punto de serialización por workspace, arriendos de N USD consumidos localmente. Los demás lo consumen, no lo reimplementan |
| Bloques 1, 2, 3, 5, 8 | **Tres algoritmos de hash y cuatro nombres** para el mismo concepto | Un único `CasRef` con algoritmo etiquetado (`b3:` / `sha256:`). Contenido = BLAKE3 |
| Bloques 3, 4, 5 | **Tres taxonomías de confianza** con cardinalidades 2, 5 y 6 — y el bloque 5 declara roto por escrito lo que el 4 implementa | Gana la del bloque 5: seis niveles, alcance de sesión, marca de agua alta, reset solo por descarte explícito o autorización humana. Es la única que resiste el ataque de inyección de prompt que las otras no ven |
| Bloques 4, 5, 6, 8 | **Cuatro taxonomías de riesgo de efecto**, una de ellas un producto cartesiano de 108 combinaciones | Gana el orden total de cinco valores del bloque 5 (`SAFE < REVERSIBLE < DESTRUCTIVE < …`), porque es la única que compone con `max()` y la única que gobierna la decisión de aprobar |
| Bloques 3, 4, 7 | **Tres `ProviderCapabilities`** con el mismo nombre y campos incompatibles | Una sola `ModelCard`, propiedad del bloque 7, ampliada con `max_tools_effective` y `max_schema_bytes` — **medidos por la sonda, no puestos a mano** |
| Bloque 1 vs. 5 | El `EffectLedger` necesita append condicional atómico; el journal no lo ofrece | El bloque 1 añade `append_if(...)` con guardia evaluada contra una proyección en la misma transacción. Sin esto, el bloque 5 es inconstruible sin violar la invariante 2 |
| Bloque 1 vs. 2 y 6 | Un journal con un escritor único choca con un stream por agente | El bloque 1 añade `stream_id` de primera clase: journal = colección de streams, orden total dentro de cada uno, Lamport entre ellos |
| Bloque 1 vs. 5 | El payload inline no admite texto libre; el bloque 5 mete `AuthzFacts` en línea | Gana el bloque 1, con una excepción acotada y nombrada. La razón no es estética: **un journal con datos personales incrustados y cadena de hashes no puede atender una petición de borrado sin destruirse a sí mismo** |
| Bloque 2 vs. 5 | El sandbox monta el workspace en escritura mientras el agente edita por el VFS | El `SandboxSpec` deja de recibir una ruta y recibe una `ExecWindow` ya abierta. `spawn()` falla si no hay ventana. Esto cierra el fallo con más pérdida de datos de todo el diseño |
| Bloque 3 vs. 8 | El inspector de contexto consume un objeto que nadie produce | El bloque 3 emite lo que el bloque 8 consume: `SelectionReason` tipado y `DropRecord` con el coste de lo descartado |

### 13.2 Los huecos: cosas que todos daban por hechas por otro

| Hueco | Dueño asignado |
|---|---|
| Quién mantiene fresco el índice cuando un agente edita — y había **dos sistemas de indexado completos** sobre el mismo repo que no se conocían | Bloque 2. El índice vive pegado al CAS |
| Quién reconcilia el journal con el disco real cuando el proceso corre dentro del sandbox | Bloque 2 el mecanismo, bloque 5 la obligación de usarlo |
| Quién redacta secretos en la salida **en streaming**, antes de llegar a la pantalla | Bloque 5, con el filtro un escalón más arriba: entre el PTY y *cualquier* consumidor |
| Quién decide el presupuesto de un subagente | Bloque 7 la autoridad, bloque 6 la política de reparto |
| Qué pasa con tu contexto cuando otro agente cambia el archivo que estabas leyendo | Bloque 3, con proyección `(workspace, path) → CasRef` |
| Nadie producía `subtree_hash(paths)`, del que depende **toda** la detección de agente atascado | Bloque 2, añadiéndolo al puerto del VFS |
| Nadie producía `ToolResult.score`, `plan_step_id`, `AuthzFacts` ni el espacio de nombres de `effect_target` | Bloque 4 (los tres primeros al descriptor, antes de congelar el ABI) y bloque 6 (`plan_step_id`) |
| Nadie era dueño del recorte del resultado que ve el modelo — había **cuatro números distintos** | Bloque 4 fija el digesto; el bloque 3 solo puede recortar más, nunca menos |
| Quién paga la verificación disparada por un evento que el agente no produjo | Bloque 7 admite un principal `workspace` distinto de los agentes |

### 13.3 El riesgo sistémico

El journal es simultáneamente fuente de verdad, bus de coordinación, libro de presupuesto, cadena
de auditoría, sustrato de la interfaz **y punto de linealización de siete mecanismos distintos de
exclusión mutua**. Cada uno impone requisitos de latencia incompatibles sobre la *misma* operación
`append`: el bucle de edición necesita menos de 8 ms; la admisión de herramientas necesita menos de
5 ms sin tocar disco, mientras el libro de efectos exige linealización, que es disco.

Lo grave no es que sea lento. Es que **cuatro bloques ya diseñaron su propia vía de escape**, cada
una justificada localmente y ninguna consciente de las otras. Ese es el patrón exacto por el que un
sistema con una fuente de verdad acaba teniendo cuatro.

**Mitigación, decidida**: el journal se parte en *streams* con secuencia propia desde el primer día
(§13.1), y el único punto realmente serializado del sistema es el `BudgetAuthority` por workspace.
Cualquier otro mecanismo que quiera linealizar contra el journal tiene que justificarlo por escrito
en la revisión de contratos de la fase 0.5.

---

## 14. El recorte: lo que NO se construye

Este es el hallazgo más incómodo de la revisión, y hay que decirlo sin adornos.

Sumando lo que cada bloque declara como «fase 1» —cada uno individualmente disciplinado, cada uno
recortando su propio alcance con criterio— el agregado son **entre 60.000 y 90.000 líneas de código
con sus pruebas, antes de que un agente cierre una sola tarea**. Nueve a dieciocho meses.

El propio §1.8 de este documento advierte: «el riesgo más grande es que se implemente entero antes
de que un agente resuelva una tarea real». Los diez bloques produjeron colectivamente justo eso.
Ninguno fue irresponsable; la suma sí lo es.

**Se difiere entero a la fase 2, sin excepciones:**

- Sellado criptográfico con notario y migradores de esquema con journals de oro. Basta **reservar el
  hueco** del sello en el evento y congelar la versión por tipo; el mecanismo se añade después.
- Merge de tres vías completo, detector de conflicto semántico, detección de renombrados por
  similitud, índice base+delta, `tree-sitter` con parseo priorizado, capa de dependencias. Con **un
  agente y sin bifurcación, el merge es un fast-forward**: el propio bloque 6 lo dice.
- El planificador con reparto justo, los tres niveles de sandbox, los macaroons atenuables, el proxy
  de salida y la cadena de auditoría multi-tenant. Sirven a un modelo de amenaza que en la fase 1 no
  existe: **un usuario, su propio Mac, su propio código**.
- El motor de contexto completo. Su propio diseño admite que el suelo es *léxico + reciente +
  búsqueda del agente*, y la búsqueda del agente ya existe en cuanto hay `grep` y `read`. Un agente
  cierra tareas sin motor de contexto; lo que el motor compra es **coste por tarea**, y el coste no
  se puede optimizar antes de poder medirlo.

**Y una corrección de rumbo sobre mi propia regla.** El §1.4 dice «construimos el sustrato,
integramos el tooling». Los bloques la desobedecieron sistemáticamente: reimplementaron `diff3`,
detección de renombrados, escaneo léxico en lugar de usar ripgrep, transporte de proyecciones y un
planificador propio. El corte correcto es por capas:

| Capa | Decisión |
|---|---|
| Journal, CAS, VFS/CoW, capacidades, taxonomía de efectos | **Desde cero, sin discusión.** Son datos y fronteras: duran una década y no se retrofitean |
| Bucle del agente, ABI, MCP, capa de proveedores, edición, interfaz | **Envolver lo que ya existe** en `packages/llm` y `packages/mcp` detrás de las interfaces nuevas, y reescribir solo cuando una medición lo exija |

Ese segundo grupo es el 55-60 % del esfuerzo de la fase 1. Reescribirlo por gusto es el error más
caro disponible.

---

## 15. Decisiones abiertas: lo que necesito que decidas

Ninguna de estas se puede resolver desde el código. Son tuyas.

1. **¿Hay un segundo modelo disponible, de otro proveedor?** El diseño de verificación exige que el
   juez no sea del mismo modelo que el ejecutor: dos instancias del mismo modelo tienden a estar de
   acuerdo consigo mismas. Si la respuesta es no, **todos los jueces del sistema arrancan como
   consultivos**, no vinculantes, y hay que decirlo en la interfaz.
2. **¿Qué es exactamente Acme 2.0?** Stack, tamaño aproximado, si tiene pruebas hoy. No es
   curiosidad: el banco de tareas de la fase 0 se construye sobre código real, y si el objetivo real
   es una app móvil el orden de las herramientas cambia.
3. **¿La autonomía se ajusta por superficie o por superficie y dominio?** Sospecho que querrás
   «automático para ingeniería, pregúntame para redes sociales» en la misma web. Eso obliga a una
   clave compuesta desde el principio.
4. **¿Quién escribe las suites de evaluación?** 30 casos representativos de tu repo son semanas de
   trabajo humano, no un subproducto. Si nadie las escribe, la fase de medición no ocurre y el
   sistema vuelve a decidir por intuición.
5. **¿Cuánto historial se guarda?** Una misión larga genera decenas de miles de eventos. Si el
   journal es la única fuente de verdad, la política de retención es una decisión de producto, no
   de infraestructura.
6. **¿Los módulos móviles (Kotlin/Swift) entran en la verificación de la fase 1?** Hoy hay cobertura
   para Python, no para ellos. Se puede aceptar el hueco, pero hay que aceptarlo explícitamente.
7. **¿Cuánto piensas gastar al mes en inferencia cuando se acabe el crédito?** Ese número, y no
   otro, decide cuánta agresividad de optimización de contexto merece la pena construir.

---

## 16. Roadmap

Ordenado por **el orden en que se descubren los errores de diseño**, no por elegancia.

### Fase 0 — La sonda (2 semanas, ~2 USD de inferencia)

No se escribe ni una línea de kernel.

Entre el 40 % y el 70 % del diseño de la fase 1 descansa sobre un número que nadie ha medido: qué
puede hacer de verdad Kimi K3. Esta fase lo mide y **puede falsar el diseño antes de construirlo**.

- Un sondeador que produce una `ModelCard` medida.
- Un banco de 20-30 tareas reales de este repo con criterio de éxito ejecutable.

Umbrales de sí/no: contexto **útil** ≥ 48k · fiabilidad de llamada a herramienta con argumentos de
código ≥ 0,90 (límite inferior del intervalo) · ≥ 25 tokens/s · primer token p95 ≤ 2,5 s · éxito en
el banco ≥ 0,55.

> Si el contexto útil resulta ser 20k, el bucle de trabajo largo **no existe** tal como está
> diseñado, y los bloques 3, 4 y 6 se rediseñan a sub-tareas cortas con relevo. Descubrir eso ahora
> cuesta dos semanas. Descubrirlo en el mes nueve cuesta el proyecto.

### Fase 0.5 — Unificación de contratos (1 semana, cero código)

Los diez bloques emitieron ~150 tipos de evento, cuatro claves de idempotencia, cuatro taxonomías
de riesgo, tres de confianza, tres algoritmos de hash y cuatro autoridades de presupuesto. Nada de
eso se arregla barato **después del primer evento escrito**. Se cierra la §12.1 entera, y se añaden
al contrato las piezas huérfanas de la §12.2 antes de congelar el ABI.

### Fase 1 — El sustrato irreversible y la primera tarea cerrada (~10 semanas)

Solo lo que no se puede añadir después, más lo mínimo para cerrar una tarea de verdad:

journal con `append_if` y `stream_id` · CAS + VFS copy-on-write recortado · ABI congelado con todos
los campos · proveedor v2 **envolviendo** `packages/llm` · contabilidad de coste real · un agente
sin subagentes · contrato de aceptación con cuatro comprobaciones · sandbox de nivel 1 (usuario
dedicado, sin red) · interfaz con línea de tiempo, inspector de contexto y cola de aprobación.

**Criterio de salida, y no se negocia**: un agente cierra una tarea real de este repositorio,
verificada por contrato, que sobrevive a un `kill -9` a mitad, con su coste medido en dólares.

### Fase 2 — Que sea útil de verdad (~12 semanas)

Motor de contexto completo · merge de tres vías · índice con tree-sitter · sandbox por contenedor ·
revisión de diffs a escala · selección de pruebas por diff y bisección automática · plugins reales
y adaptador MCP · composición de perfiles · jueces independientes.

### Fase 3 — Escala, multi-agente y acabado (~14 semanas)

Subagentes con reparto de presupuesto · trabajo particionado con fusión concurrente · verificación
de interfaz por árbol de accesibilidad · evaluación medida en bucle cerrado · retroceso y
bifurcación de sesiones · sellado criptográfico y migradores.

### El multiplicador que cambia todos estos números

Desde el final de la fase 1, **Forge se construye a sí mismo**. En cuanto un agente cierra una
tarea verificada de este repo, el siguiente trabajo es construir la fase 2 — y ese trabajo lo hace
él, supervisado. Ese es el argumento más fuerte para cortar la fase 1 hasta el hueso: cada semana
que se le añade retrasa el momento en que el sistema empieza a acelerarse solo.

Es también la razón por la que las estimaciones de arriba son honestas para la fase 1 y
deliberadamente conservadoras para las fases 2 y 3.

---

## 17. Cómo se mide el éxito

Seis números. Si no mejoran, el sistema no está mejorando por mucho que crezca el código.

| Métrica | Qué responde |
|---|---|
| **Tareas cerradas sin intervención humana** | La métrica norte. Todo lo demás existe para subirla |
| **Coste en dólares por tarea aceptada** | Si baja mientras la anterior sube, el motor de contexto funciona |
| **Tasa de reversión a 7 días** | Cuánto de lo aceptado era falso «terminado» |
| **Tiempo hasta el primer diff verificado** | El ancho de banda de corrección humana |
| **Sesiones que sobreviven a un reinicio** | La durabilidad, medida y no prometida |
| **Tiempo de cambio de proveedor** | Una prueba automatizada que cambia la configuración y verifica que la suite pasa igual. Si tarda más de un día, el acoplamiento ya entró |

---
