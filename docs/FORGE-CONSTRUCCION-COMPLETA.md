# Forge — Especificación completa de construcción, del punto A al punto Z

> **Qué es este documento.** La orden de trabajo completa para construir Forge, el IDE de agentes
> de Edecán, desde donde está hoy hasta que funcione entero. Contiene todo lo que hace falta saber
> y todo lo que hay que demostrar. No hay nada más que leer.
>
> **Fecha:** 28 de julio de 2026 · **Autor:** arquitectura de Edecán
> **Diseño de referencia:** [`arquitectura-forge.md`](arquitectura-forge.md) (7.960 líneas; consúltalo
> con `grep`, no lo leas entero)

---

# PARTE I — QUÉ SE CONSTRUYE

## 1. La frase que define el producto

Edecán es un asistente operativo general —chat, llamadas, reservas, redes sociales, negocio,
investigación— que además **construye software de verdad**. Ese dominio se llama **Forge**.

> El listón, en palabras del dueño del producto: poder decir *«construye Acme 2.0»* y que el
> sistema lo haga en horas o días.

No es generar snippets. Es: escribir el código, **ejecutarlo**, ver el error real, arreglarlo,
probarlo, abrir el navegador para comprobar que la pantalla se ve bien, consultar la base de datos,
hacer commit y desplegar. Y sobrevivir a que se apague el ordenador a mitad.

La tesis económica que justifica el proyecto entero:

> **El andamiaje pesa más que el modelo.** Un modelo de primera línea que no puede ejecutar su
> propio código, no ve el error y supone que funciona rinde peor que uno intermedio con
> herramientas fiables, contexto correcto y verificación independiente.

El andamiaje es, además, la parte que **no caduca** cuando cambias de modelo. Por eso se construye
él y no un envoltorio de prompts.

## 2. Lo que Forge NO es

- **No es un editor de texto con un chat al lado.** El editor es un cliente más del núcleo.
- **No es un fork de VS Code.** Se toman librerías; no se hereda una arquitectura pensada para un
  humano con un teclado y una ventana.
- **No es un asistente que redacta código.** Si no puede ejecutar y verificar, no sirve.
- **No es un producto donde el humano mira y pulsa «aprobar».** Ver §4.

## 3. Las tres superficies

### 3.1 Mac — el IDE dentro de la app

«Simple, pero excelente.» **Tres estados, no veinte paneles.** Y el primero es el que define el
producto.

#### Estado 1 — Reposo: el IDE abre en un campo de texto, no en un árbol de archivos

Esta es la decisión visual más importante y hay que respetarla al pie de la letra:

```
┌─────────────────────┬──────────────────────────────────────────────────────┐
│  ＋ Nueva sesión    │  ⌐ Nueva sesión  ⇧⌘O                                 │
│                     │                                                      │
│  ⟲ Historial        │                                                      │
│  ◷ Programadas      │                                                      │
│                     │                                                      │
│  PROYECTOS      ⌕ ＋ │                    ▫ edecan  ⌄                      │
│  ▸ edecan           │      ┌──────────────────────────────────────────┐   │
│    · handoff-forge  │      │  Pregunta lo que sea · @ para mencionar  │   │
│  ▸ organization         │      │  / para acciones                         │   │
│    · migrar pagos ● │      │                                          │   │
│    · voseo      2mo │      │  ＋   GLM-5.2 ⌄   ⚠ 2 aprobaciones   🎙 ➜ │   │
│  ▸ bizagency        │      └──────────────────────────────────────────┘   │
│    · rebuild      ● │              ▫ Local ⌄                              │
│                     │                                                      │
│  ⚙ Ajustes          │                                                      │
└─────────────────────┴──────────────────────────────────────────────────────┘
```

Lo que hay que copiar de aquí, elemento por elemento:

- **Fondo casi negro y muchísimo espacio vacío.** El vacío no es desperdicio: es lo que hace que
  cuando aparezca algo, se vea.
- **Barra izquierda de ~340 px**, con `＋ Nueva sesión` como acción primaria y única destacada.
  Debajo, historial y tareas programadas. Después, el **árbol de proyectos** con sus sesiones
  anidadas, cada una con un punto de estado o su antigüedad relativa. **Ajustes anclado abajo.**
- **El compositor es el producto.** Centrado, ancho contenido, con el proyecto activo como pastilla
  encima (`▫ edecan ⌄`) y el alcance debajo (`▫ Local ⌄`).
- **Dentro del compositor, una sola fila de controles**: `＋` para adjuntar, el selector de modelo
  en texto plano (`GLM-5.2 ⌄`), el estado del sistema en ámbar cuando algo requiere atención, micro
  y enviar. Nada más.
- **Sin barra de menús propia, sin cinta de iconos, sin barra de estado inferior.** Cero adorno.
- **Atajos visibles** junto a la acción (`⇧⌘O`), no escondidos en un menú.

El punto de fondo: **un IDE de agentes no empieza abriendo un archivo, empieza diciendo qué
quieres.** El explorador y el editor existen, pero no son la puerta de entrada.

#### Estado 2 — Trabajando: mission control

En cuanto hay trabajo en marcha, el compositor se encoge a una fila arriba y el centro pasa a ser
esto:

```
┌──────────────────────────────────────────────────────────────────┐
│  ● migrar-pagos      ejecutando   paso 12/28   $0.84   ⏱ 41 min  │
│  ● tests-android     esperando aprobación ⚠     $0.11   ⏱  6 min  │
│  ○ auditar-sql       completado   ✓ 4/4 criterios  $0.31         │
├──────────────────────────────────────────────────────────────────┤
│  Línea de tiempo         │  Artefactos           │  Aprobaciones  │
│  minimapa de eventos     │  plan · diff · captura│  cola con lote │
└──────────────────────────────────────────────────────────────────┘
```

**Artefacto** es el concepto central de esta vista: el agente no entrega un log, entrega cosas
revisables —un plan, un diff agrupado, una captura, una grabación del navegador, la salida de las
pruebas—. Un humano verifica un artefacto en segundos y un log en minutos.

#### Estado 3 — Editor

Solo cuando el humano abre un archivo. Explorador a la izquierda, editor al centro, terminal abajo,
agente a la derecha. **Se entra desde un diff o desde el árbol, nunca es la pantalla de inicio.**

#### La regla que traduce «simple pero excelente» a algo ejecutable

> La pantalla muestra por defecto lo que necesita decisión y esconde lo que no.

Un agente que va bien ocupa una línea; uno atascado o esperando aprobación se expande solo. La
densidad no la elige el usuario: la elige el estado del trabajo. Y los tres estados son el mismo
espacio que se transforma, **no tres pestañas entre las que se navega**.

### 3.2 iPhone y Android — el IDE es una pestaña del tab bar

Como en Referencia. **No una app aparte.** Cuatro sub-pestañas, y el orden declara para qué sirve el
teléfono:

| # | Pestaña | Para qué |
|---|---|---|
| 1 | **Agentes** | Qué pasa ahora. Pantalla de inicio |
| 2 | **Revisar** | Cola de aprobaciones y diffs. *La* razón de que el IDE esté en el teléfono |
| 3 | **Terminal** | El mismo terminal total de §3.5, pero en modo lectura por defecto: escribir exige un gesto deliberado. No es una limitación del sistema, es que un pulgar no debe poder mandar `rm -rf` sin querer |
| 4 | **Archivos** | Explorar y editar. Deliberadamente el último |

**En el teléfono el trabajo no se hace, se dirige.** Aprobar un despliegue desde la cama sí; editar
400 archivos no, y no pasa nada porque no.

Nativo: SwiftUI y Compose consumiendo la misma proyección. Ya hay base en
`apps/mobile/ios/EdecanApp` y `apps/mobile/android`.

### 3.3 El espejo en vivo Mac ↔ móvil

**Ya está resuelto por diseño y no hay que construirlo.** Ambos clientes se suscriben al mismo
journal por cursor, así que ya están sincronizados. Lo único que se construye es el **modo seguir**:
un interruptor explícito que ata el foco del teléfono al del Mac. Viaja por el canal efímero y
**nunca entra al journal** — dónde estabas mirando no es un hecho del proyecto.

Tres detalles que deciden si se siente bien o mal:

1. **Late join**: al abrir, la proyección se rehidrata desde el cursor guardado. Un journal de
   40.000 eventos no se descarga entero.
2. **Sin red no miente**: se muestra el desfase real («al día hace 3 s» / «sin conexión desde las
   20:14») y **las aprobaciones se bloquean si el cursor está rancio**. Aprobar sobre un estado
   viejo es la forma más fácil de autorizar algo que ya no es lo que creías.
3. **La aprobación es idempotente**: si apruebas desde el teléfono y el Mac tenía el mismo diálogo
   abierto, gana la primera y la segunda se cierra sola, sin doble ejecución. Sale del
   `EffectLedger`, no de un truco de interfaz.

### 3.4 Rechazado explícitamente — no lo reintroduzcas

| Alternativa | Por qué no |
|---|---|
| Streaming de píxeles del Mac al teléfono | Latencia, ilegibilidad, gestos inexistentes, y exige el Mac encendido para poder *mirar* |
| App móvil separada del IDE | Duplica identidad, sesión y notificaciones |
| Web embebida en el móvil | Arruina el diff táctil y las notificaciones, que son lo que hace útil el teléfono |
| Paridad total de funciones entre superficies | Suena a rigor y produce dos interfaces mediocres |
| Fork de VS Code | Su arquitectura gira alrededor del editor activo, el cursor y la selección. Un journal y capacidades no se le añaden como extensión. Y paga impuesto de rebase para siempre |

### 3.5 Acceso total al terminal — requisito explícito del dueño

**Literalmente total.** No una lista blanca de comandos, no un intérprete restringido, no un
subconjunto «seguro». Un shell real, el del usuario, con todo lo que el usuario tiene:

- **PTY de verdad**, no tuberías. `top`, `vim`, `ssh`, `docker`, cualquier programa de pantalla
  completa funciona igual que si lo escribiera el dueño.
- **Su shell, su entorno**: `$SHELL`, `$PATH`, alias, `.zshrc`, versiones de `nvm`/`pyenv`, todo lo
  que tenga instalado.
- **Sin confinamiento de directorio.** Puede salir del workspace. Es su máquina.
- **Sin cortafuegos de salida.** Puede instalar paquetes, clonar repos, llamar APIs, desplegar.
- **Sudo si el usuario lo permite**, con la misma confirmación que le pediría el sistema a él.
- **Sesiones persistentes** que sobreviven a cerrar el cliente, como `tmux`. Cerrar el portátil no
  mata un build de veinte minutos.
- **Varias terminales a la vez**, cada una con su estado y su directorio.
- **Salida completa**, ANSI incluido, legible y citable en el journal.

### Lo que hace esto sostenible, y no es restringir el shell

Aquí hay que ser preciso, porque la respuesta intuitiva —«pues métele un sandbox»— es la
equivocada y además no es lo que se ha pedido.

La seguridad de un shell total **no viene de recortarlo**. Viene de cuatro cosas que ya están en la
arquitectura y que hay que construir sí o sí:

1. **Todo queda registrado.** Cada comando, su salida, su código de salida, su duración y quién lo
   pidió, en el journal, con cadena de hashes. No hay acción sin rastro.
2. **La clasificación de efecto la deriva el sistema del comando, no la declara el agente.** `ls` es
   seguro; `rm -rf`, `git push --force`, `DROP TABLE`, `terraform apply` y `curl | sh` son
   irreversibles y **piden aprobación antes**, no después.
3. **Se puede retroceder.** Checkpoint por paso, y el trabajo del agente vive en un workspace
   copy-on-write: deshacer es barato y no depende de que el agente se porte bien.
4. **El techo de efecto baja con la confianza del origen.**

### El único riesgo real, dicho una vez

No es que el dueño se equivoque: es su máquina y su decisión. **El riesgo es la inyección de
prompt.** Si un `README`, una página web o la salida de una herramienta puede hacer que el agente
ejecute un comando, y el agente tiene shell total, entonces **contenido no confiable tiene shell
total**.

La defensa **no** es recortar el terminal. Es el punto 4: todo contenido entra con un nivel de
confianza, la marca de agua sube y no baja sola, y **un comando cuyo origen se remonta a contenido
no confiable sube automáticamente de clase de efecto y exige aprobación humana**, por inocente que
parezca. Eso da shell total sin dar shell total a cualquiera que sepa escribir un README.

### Dónde queda entonces el sandbox

Se reubica, no desaparece. El sandbox **no es para el agente del dueño en la máquina del dueño**.
Es para:

- **los plugins**, que son código de terceros;
- **el código que se está construyendo**, cuando se ejecuta para probarlo;
- y el día que Forge corra para alguien que no es el dueño de la máquina.

> **Modo por defecto en la máquina del dueño: `terminal: total`.** Está declarado en
> `config/modelos.yml` como política del perfil, es dato y no código, y se puede apretar sin tocar
> una línea.

---

### 3.6 Varios agentes a la vez — el modelo puede mandar agentes

Sí: **GLM-5.2 o Kimi pueden lanzar agentes**, igual que un agente de código moderno lanza
subagentes. Es una herramienta más del ABI:

```python
spawn_agent(
    receta: str,              # "investigar" | "implementar" | "revisar" | "probar" | ...
    objetivo: str,            # qué tiene que conseguir
    particion: list[str],     # QUÉ ARCHIVOS va a tocar. Obligatorio
    presupuesto_usd: float,   # sale del presupuesto del padre, no se crea de la nada
    criterios: list[Criterio],
) -> AgentHandle
```

Cuatro reglas que hacen que esto funcione en vez de convertirse en un caos caro:

**1. La partición es obligatoria.** Un agente que no puede declarar qué archivos va a tocar **no
puede correr en paralelo** con otro. Se ejecuta en serie. Sin esta regla, N agentes sobre el mismo
proyecto producen más conflictos de fusión y trabajo duplicado que valor.

**2. Recetas, no improvisación.** Y esta es la decisión práctica que más importa con estos modelos.

Delegar bien es más difícil que programar bien. Los modelos más fuertes improvisan la delegación
razonablemente; con GLM-5.2 o Kimi conviene que **el sistema traiga los equipos ya hechos**:

| Receta | Equipo |
|---|---|
| `funcion_nueva` | investigar → implementar → probar → revisar |
| `arreglar_bug` | reproducir → localizar → arreglar → probar regresión |
| `migracion` | inventariar → transformar por lotes → verificar → fusionar |
| `auditoria` | particionar por módulo → analizar en paralelo → consolidar |

El modelo elige **qué receta**, no **qué equipo inventa cada vez**. Sale mejor, sale más barato y es
reproducible. La delegación libre existe, pero detrás de un interruptor y **medida contra las
recetas en el banco de tareas**: si no gana, no se usa.

**3. El presupuesto se hereda, no se crea.** Un hijo recibe un arriendo del padre —del orden del
20 % de lo que le queda, renovable— y si se agota, el hijo se bloquea y pide. Nadie puede gastar lo
que su padre no tiene.

**4. Se comunican solo por eventos.** Nunca por llamada directa. Cada uno en su workspace
copy-on-write, y la fusión es un acto explícito y reversible.

**Y el límite honesto:** sobre el *mismo* proyecto, más de tres a cinco agentes concurrentes suele
producir más conflicto que valor. Donde N agentes ganan de verdad es en trabajo **particionable**:
migrar 500 archivos, auditar un repo, generar pruebas por módulo. El sistema soporta N; la política
por defecto exige que el paralelismo declare su partición.

En la interfaz esto ya se ve: el mission control de §3.1 es exactamente la pantalla de varios
agentes trabajando a la vez, una línea cada uno, con su paso, su coste y su tiempo.

---

## 4. La corrección a «los humanos solo observan y aprueban»

Es correcto que el humano no escribe el código. Es **incorrecto** deducir que su papel es pasivo,
y esa deducción envenena el diseño de la interfaz.

El cuello de botella real de los agentes autónomos no es su capacidad de editar archivos: es la
**tasa de corrección**. Un agente que trabaja seis horas sin supervisión produce trabajo inútil no
porque no sepa programar, sino porque interpretó mal la intención en la hora uno y construyó cinco
horas encima.

Si diseñas para «el humano solo aprueba», construyes una cola con botones de sí y no. Y el día que
un agente presente 400 archivos modificados, descubrirás que **un botón de aprobar sobre 400
archivos no es una decisión, es una rendición**.

> El humano no escribe código, pero **es el sistema de tipos del proyecto**: aporta intención,
> restricciones y criterios de aceptación; corrige trayectoria, no sintaxis.

Consecuencia de diseño, y no es negociable: **la interrupción y el retroceso son primitivas del
kernel, no funciones de la interfaz.**

---

# PARTE II — LAS REGLAS

## 5. Las diez invariantes

No son estilo. Son contrato. Un cambio que rompa una no es un cambio: es un rediseño y exige
revisar la arquitectura.

1. **No es un editor.** Forge es un sustrato de ejecución para agentes.
2. **El journal es la única fuente de verdad.** Todo estado autoritativo vive en un log
   append-only. La interfaz, la observabilidad, el historial y la auditoría son *proyecciones*.
3. **Separación de planos.** Control (durable, pequeño, portable) y datos (pesado, desechable,
   pegado al disco) se comunican solo por eventos y referencias de contenido. Nunca comparten
   memoria ni sistema de archivos.
4. **Contenido direccionado por hash.** El journal transporta referencias, nunca payloads grandes.
5. **Workspaces copy-on-write.** El aislamiento entre agentes es el default; la fusión es
   explícita, observable y reversible.
6. **Todo es una herramienta; toda herramienta la provee un plugin** — salvo el núcleo confiable
   (journal, bus, VFS, procesos, capacidades, registro de plugins). **MCP es un adaptador, no el
   ABI.**
7. **Capacidades, no permisos ambientales.** Ningún actor tiene autoridad implícita. El sandbox
   impone; no se confía en la buena conducta del modelo.
8. **Todo es cancelable, reanudable y con presupuesto.**
9. **Multi-agente en el contrato desde el día cero**, aunque hoy corra un agente. Pasar de 1 a 20
   no debe reescribir ninguna interfaz.
10. **Cero acoplamiento directo entre módulos.**

Y la regla de proceso que lo gobierna todo:

> **Regla de portabilidad.** El sistema se desarrolla y se prueba contra el proveedor de modelo más
> débil disponible. Cualquier capacidad superior —caché de prefijo, tool-calling nativo, ventana
> larga, visión— se detecta y se aprovecha, pero **jamás se asume**.

## 6. La regla de la línea: qué se construye y qué se integra

Los diseños tienden a reimplementar cosas resueltas. Ya pasó en este proyecto: se reimplementaron
`diff3`, detección de renombrados, escaneo léxico en vez de usar ripgrep, y un planificador propio.
Cada decisión bien argumentada por separado; juntas, contradicen la regla.

| Capa | Decisión |
|---|---|
| Journal, CAS, VFS/CoW, capacidades, taxonomía de efectos, contrato de aceptación | **Desde cero, sin discusión.** Son datos y fronteras: duran una década y no se retrofitean |
| Editor, resaltado, parsing, terminal, git, diff, búsqueda, navegador | **Se integra.** Son años-persona resueltos. Detrás de una interfaz nuestra, para que sean reemplazables |

**Regla para decidir si algo es núcleo o plugin:**

> Si su fallo debe ser **contenido** → es un plugin.
> Si es la cosa que **contiene** los fallos → es núcleo.

## 7. La lista de materiales — lo que se toma

Verificado (licencias y actividad) en julio de 2026. Todo entra **como librería detrás de una
interfaz nuestra**, nunca como plataforma que dicte el diseño.

| Componente | Pieza | Licencia |
|---|---|---|
| Editor de código | **CodeMirror 6** (state/view/language + Lezer) | MIT |
| Diff y merge por archivo | `@codemirror/merge` | MIT |
| Resaltado de solo lectura | **Shiki** (`codeToHast`, en servidor) | MIT |
| Virtualización de listas | TanStack Virtual | MIT |
| Primitivas accesibles | Base UI (`@base-ui/react`) | MIT |
| Estilo y tokens | Tailwind CSS v4 (`@theme`, OKLCH) | MIT |
| Animación | Motion | MIT |
| Tipografía | Inter Variable + Commit Mono | OFL 1.1 |
| Iconos | Lucide | ISC |
| Terminal | `@xterm/xterm` v6 + WebGL (diferido) | MIT |
| Parsing multi-lenguaje | tree-sitter + py-tree-sitter | MIT |
| Búsqueda de texto | **ripgrep** (subproceso, `--json`) | MIT |
| Watcher de ficheros | watchfiles | MIT |
| Git | binario `git` en subproceso | GPL-2.0 (ejecutable) |
| Journal | SQLite en WAL (stdlib) | dominio público |
| Aislamiento en macOS | `sandbox-exec` (Seatbelt) | sistema Apple |
| Verificación de UI web | **Playwright** (`aria_snapshot`, tracing) | Apache-2.0 |
| Calidad Python | ruff | MIT |
| Detección de secretos | gitleaks | MIT |

**Descartado con razón:** Monaco (su aspecto es parte de su API pública, y no soporta táctil — el
usuario aprueba desde el iPhone); cualquier fork de IDE; y todo lo que meta Rust o Zig en la cadena
de build por una sola función. El stack sigue siendo **Python + TypeScript**.

**No se puede tomar:** Hermes IDE. Su licencia BSL 1.1 prohíbe expresamente embeberlo en un
producto que compita con un IDE ofrecido a terceros. Sirve como referencia de arquitectura
(Tauri + Rust + React) y nada más.

## 8. Dirección de arte

Tres adjetivos, y cada uno rechaza algo:

- **Sereno.** El ruido base es acromático y el color es un presupuesto escaso. Es la única forma de
  que el rojo signifique algo cuando entran cientos de eventos por minuto. *(Rechaza «vibrante».)*
- **Denso.** La densidad es respeto por el tiempo del lector: 34 filas de journal en 900 px, no seis
  tarjetas con aire. *(Rechaza «minimalista».)*
- **Táctil.** Todo lo accionable responde en menos de 100 ms y sobrevive a un pulgar. *(Rechaza el
  vocabulario entero del glassmorphism.)*

**El fracaso a evitar tiene nombre:** la interfaz de agente típica es un muro de texto gris con un
spinner. Es fea, ilegible y da ansiedad. La de Forge no puede serlo.

Seis patrones propios que hay que diseñar bien porque no existen en ningún sitio:

1. **El agente pensando**, sin muro de texto.
2. **El timeline de miles de eventos**, legible.
3. **La revisión de un diff de 400 archivos**, con placer.
4. **La cola de aprobaciones**, sin ansiedad.
5. **El medidor de coste y presupuesto.**
6. **El inspector de contexto**: el prompt exacto, por qué entró cada fragmento, cuántos tokens
   costó y qué se descartó.

---

# PARTE III — DÓNDE ESTAMOS

## 9. Estado verificado hoy

| Paquete | Qué es | Estado |
|---|---|---|
| `packages/forge-kernel/` | Contratos, journal, CAS, bus, proyecciones | ✅ **193 tests verdes**, ruff limpio |
| `packages/forge-probe/` | Sonda de capacidades + banco de 30 tareas | ✅ **444 tests verdes**, ruff limpio |

**El cimiento está demostrado, no prometido.** `uv run python -m edecan_forge_kernel.demo_sesion`
corre una sesión completa sin red y sin modelo. Salida real:

```
estado reconstruido por replay == estado en vivo:  True
verify_chain('agent-1'):                           True
append_if guardia cumplida / rota:                 True / False
bytes efímeros NO journalizados:                   950 B
```

Es decir, ya funciona: se cierra el journal, se reabre sobre el mismo archivo y el estado se
reconstruye idéntico; se destruye un blob con datos personales y el hash del evento que lo citaba
**no cambia**, así que la cadena sigue verificando; y el stdout efímero no entró al journal pero
quedó anclado con una referencia cuyo hash coincide con el que produce el CAS por su cuenta.

**Lo honesto: eso demuestra que el *sustrato* aguanta, no que un agente cierre una tarea real.**

## 10. Los números medidos — no supuestos

Todo verificado contra la API real de Cloudflare Workers AI el 27–28 de julio de 2026, cuenta
`bd97ab5c87d3d2f6d99f93465aa63679`. **521 llamadas, 1,72 USD gastados.**

### Modelos disponibles

| Modelo | Estado |
|---|---|
| `@cf/moonshotai/kimi-k3` | **403, code 5018** — la cuenta no tiene acceso. Solicitado |
| `@cf/zai-org/glm-5.2` | En uso para el IDE. 262k ctx, tools, razonamiento, visión |
| `@cf/zai-org/glm-4.7-flash` | Chat rápido. 131k ctx |
| `@cf/moonshotai/kimi-k2.7-code` | Alternativa |

Enrutado en [`config/modelos.yml`](../config/modelos.yml). **Es dato, no código.**

### Precios reales (USD por millón, leídos de la API — no los codifiques a mano)

| Modelo | Entrada | Salida | Entrada cacheada |
|---|---|---|---|
| GLM-5.2 | 1,40 | 4,40 | **0,26** |
| GLM-4.7-Flash | 0,0605 | 0,40 | — |
| Kimi K2.7-Code | 0,95 | 4,00 | **0,19** |

### Mediciones de la sonda

| Medición | Valor | Umbral | Veredicto |
|---|---|---|---|
| `native_tools.code_blob.lower_95` | **0,912** | ≥ 0,90 | PASA, justo → riesgo |
| `native_tools.scalar` | 0,97 (39/40) | — | — |
| `throughput_tps` | **95,3** tok/s | ≥ 25 | PASA holgado |
| Latencia p50 por llamada | 12,05 s | — | — |
| **Razonamiento sobre la salida** | **82,2 %** | — | — |
| Llamadas truncadas por `max_tokens` | **27 %** (tope 1.600) | — | — |
| `usable_context_tokens` | **sin medir** | ≥ 48.000 | **bloquea** |
| `bench_success_rate` | **sin medir** | ≥ 0,55 | **bloquea** |

**Veredicto actual de la fase 0: NO-GO**, y es correcto: «sin dato» jamás se interpreta como
aprobado.

### Las cuatro consecuencias que cambian el diseño

1. **El precio real de salida no es 4,40 $/M.** Si solo el 17,8 % de lo que pagas es respuesta, el
   coste por millón de tokens *de contenido* es **~24,70 $**. Una tarea de agente sale a **~0,87 $**.
   Con 50.000 $ de crédito son **~57.000 tareas**.
2. **Un 27 % de las llamadas se corta por presupuesto de tokens.** El razonamiento come del mismo
   `max_tokens` y llega en un campo **separado**, `message.reasoning_content`. Con el tope justo, la
   respuesta llega **vacía y se cobra igual**, sin ningún error. Reserva ≥200 tokens por encima del
   contenido esperado y trata `content` vacío con `finish_reason: length` como **error recuperable**.
3. **La entrada cacheada cuesta 5× menos.** Mantener el prefijo estable —sistema, herramientas,
   contexto estable, historial, turno actual, **en ese orden y sin reordenar jamás**— vale del orden
   de un 65 % más de trabajo con el mismo crédito. Es de fase 1, no de fase 2.
4. **12 segundos de mediana por llamada.** Una tarea de 28 pasos son casi 6 minutos solo de
   inferencia. El bucle tiene que **solapar** trabajo, no encadenarlo.

### Política de razonamiento — decidida, hay que implementarla

Se conserva el `reasoning_content` para depuración, reproducibilidad y auditoría. Cuatro reglas:

- **Nunca vuelve al modelo.** Es del turno, no de la conversación.
- **Al CAS por referencia, nunca en línea en el journal.** Es el 82 % de la salida.
- **La redacción de secretos DEBE cubrirlo**, y con más motivo que el contenido: es justo donde el
  modelo repite en claro la clave que acaba de leer mientras `content` sale limpio.
- **Es evidencia, no memoria.** Guardarlo no hace que el agente aprenda.

Visibilidad: **mostrar en el IDE** (ahí quien mira es quien desarrolla), ocultar en el chat.

---

# PARTE IV — EL RÉGIMEN DE PRUEBAS

## 11. Esta es la parte más importante del documento

Un informe verde y un sistema que funciona **no son lo mismo**. Ya pasó en este proyecto: se
reportó «127 tests pasando, ruff limpio» y el repo completo tenía **9 tests en rojo** en
`apps/api`, `apps/worker` y `apps/mobile`. No fue mala fe: fue correr un paquete y llamarlo sistema.

### 11.1 Qué cuenta como prueba

| ✅ Cuenta | ❌ No cuenta |
|---|---|
| La salida **pegada** de un comando que cualquiera puede repetir | «Los tests pasan» |
| Un test que **falla antes** del cambio y pasa después | Un test escrito después para confirmar lo que ya hiciste |
| El comando exacto, con su ruta y sus flags | «Verificado mediante pruebas» |
| Un número con su `n` y su intervalo | Un número solo |
| Una traza cruda en disco que respalda el número | Un resumen del número |
| Decir qué NO se pudo comprobar | Silencio sobre los huecos |

### 11.2 Las seis reglas de evidencia

1. **Corre la suite completa del repositorio, no la de tu paquete.**
   `uv run pytest packages apps -q`. Si algo se rompe aguas abajo, es tuyo.
2. **Pega la salida real.** El resumen final de pytest, literal. Si dices «verde», que se lea.
3. **Ningún número sin evidencia.** Toda medición deja traza cruda en disco y el informe la cita
   por ruta. Un número sin traza no entra.
4. **Lo que no se midió es `None`.** Jamás un valor «razonable», jamás un valor de catálogo copiado
   de la documentación del proveedor. Un hueco declarado vale; un hueco rellenado contamina todas
   las decisiones que cuelgan de él.
5. **Antes de concluir que el modelo falla, mira la traza cruda.** Un caso de prueba que el modelo
   no puede acertar mide un defecto del caso. Pasó de verdad: 14 «fallos» que eran un punto final
   ambiguo en el prompt. Si no se llega a mirar, habría salido en el informe que los argumentos
   anidados no son fiables — y de ahí sale una decisión de arquitectura entera.
6. **Los tests no tocan la red.** Hay dinero real detrás del token. Las pruebas contra la API van
   detrás de `FORGE_PROBE_INTEGRACION=1`; la presencia del token **no basta** como condición.

### 11.3 El formato obligatorio de entrega, por cada fase

```markdown
## Fase X — <nombre>

### Qué se construyó
<3-8 líneas. Qué hace y qué NO hace todavía.>

### Comandos que lo demuestran
$ <comando exacto>
<salida real, pegada, sin editar>

$ uv run pytest packages apps -q
<resumen final, literal>

$ uv run ruff check packages apps
<literal>

### La demo
$ <comando de la demo de la fase>
<salida real completa>

### Decisiones que tomé y no estaban escritas
- <cada una, con su por qué>

### Lo que NO funciona / no se pudo comprobar
- <honesto. Un hueco declarado vale; uno escondido no>

### Números
| métrica | valor | n | cómo se midió |
```

Una fase sin ese bloque **no está entregada**, por mucho código que tenga.

### 11.4 La regla de oro

> **El actor que hace el trabajo nunca es el actor que declara que está terminado.**

Cada fase tiene un criterio de aceptación **ejecutable**, escrito **antes** de empezar. Si un
criterio no se puede comprobar con un proceso que devuelve un código de salida, no es un criterio:
es una intención.

### 11.5 Detectores de auto-engaño — actívalos

Los patrones reales de un agente que «termina» sin haber terminado. Búscalos activamente en cada
entrega, en tu propio trabajo:

- modificar el test para que pase;
- marcarlo como `skip` o `xfail`;
- capturar la excepción y devolver vacío;
- mockear lo que debía ser real;
- declarar éxito sin haber ejecutado nada;
- dejar `TODO` donde iba la lógica;
- reportar el resultado de un subconjunto y llamarlo el total.

---

# PARTE V — EL PLAN, DEL PUNTO A AL PUNTO Z

Orden por **dependencia real** y por **el orden en que se descubren los errores de diseño**, no por
elegancia. Cada fase entrega algo que se puede ejecutar.

---

## FASE A — Cimiento · ✅ HECHA

Journal, CAS, bus, proyecciones, contratos unificados. 193 tests. Demo de reanudación verificada.

**No la toques.** Si necesitas un campo que no está en `contracts.py`, **dilo** en vez de añadirlo:
ese archivo materializa doce contradicciones ya resueltas entre bloques de diseño, y modificarlo a
la ligera reintroduce el problema que existe para impedir.

---

## FASE B — Cerrar la medición de la fase 0

Sin esto, entre el 40 % y el 70 % del diseño de las fases siguientes descansa sobre números
inventados.

### B1 · `usable_context_tokens`

Correr la sonda de contexto contra GLM-5.2. Mide a 4k / 16k / 48k / 96k / 160k / 224k / 256k con
tres pruebas por profundidad: aguja simple, multi-salto y **supervivencia de una restricción**
(la más importante: mide si una regla dada al principio sobrevive; su fallo no levanta ningún error,
el modelo simplemente hace lo prohibido).

```bash
uv run python -m edecan_forge_probe sondear --solo context \
  --presupuesto-usd 25 --precio-entrada 1.40 --precio-salida 4.40 --precio-cacheada 0.26
```

> **Criterio:** `usable_context_tokens` publicado con su curva completa y su intervalo.
> **Prueba exigida:** el `informe.md` generado, con la curva y las trazas citadas por ruta.
> **Si sale por debajo de 48.000**: el bucle largo no existe tal como está diseñado. Se rediseña a
> sub-tareas cortas con relevo y estado explícito. **Dilo, no lo escondas.**

### B2 · `bench_success_rate`

Correr las 30 tareas de `packages/forge-probe/bench/` contra el bucle de agente actual. Es la
**línea base que el bucle de Forge tendrá que batir**; sin ella no habrá forma de saber si mejoró.

> **Criterio:** tasa con su `n`, desglose por clase (`trivial` / `standard` / `guarded`), coste real
> por tarea, y la lista de tareas que no se pudieron intentar y por qué.

### B3 · Re-medir `nested`

La serie está medida con un caso defectuoso ya corregido. La huella de código la invalida sola
ahora; solo hay que relanzar.

---

## FASE C — VFS y workspace

El sistema de archivos virtual por el que pasa **toda** lectura y escritura de agentes. Sin esto no
hay aislamiento, no hay auditoría y no hay concurrencia.

### Qué se construye

- **`Vfs` / `VfsTxn`**: lectura consistente por snapshot, escritura transaccional, detección de
  conflictos (write-write y read-write) con dependencias tipadas. **Ningún agente toca el disco
  directamente.** Nunca.
- **`workspace.fork()`** copy-on-write: un workspace nuevo por agente **en menos de 200 ms con
  200.000 ficheros**. Sobre punteros al CAS, no copiando bytes.
- **`ExecWindow`**: ventana exclusiva de ejecución con write-through y reconciliación. Resuelve el
  fallo con más pérdida de datos de todo el diseño: el agente edita por el VFS mientras un build
  corre sobre el mismo árbol, y la reconciliación borra la edición en silencio.
- **`TextEdit` anclado**: ediciones que sobreviven a que el archivo cambie debajo.
- **`subtree_hash(paths)`** con las reglas de ignorado aplicadas. De él depende **toda** la
  detección de agente atascado de la fase G.
- **Clasificador de archivos** con `secret_candidate`: qué es código, qué es binario, qué es
  generado (`node_modules`, `.venv`, `dist`), qué huele a credencial.
- Manejo correcto de symlinks, normalización NFC/NFD (macOS) y colisiones de mayúsculas.

### Fuera de esta fase, deliberadamente

Merge de tres vías completo, detector de conflicto semántico, detección de renombrados por
similitud, índice base+delta. **Con un agente y sin bifurcación, el merge es un fast-forward.**

> **Criterio de aceptación:**
> 1. `fork()` de un repo de ≥200.000 ficheros en **<200 ms**, medido y pegado.
> 2. Dos escrituras concurrentes en conflicto: una gana, la otra recibe un error tipado, **ninguna
>    corrompe nada**.
> 3. Un proceso externo modifica un archivo durante una `ExecWindow` y la reconciliación **no pierde
>    la edición del agente**. Este test es obligatorio.
> 4. Un intento de escribir fuera del workspace (`..`, symlink que escapa, ruta absoluta) **falla**.
>
> **Prueba exigida:** una demo ejecutable `demo_workspace.py` que forkea, edita en dos ramas,
> detecta el conflicto y reconcilia — con la salida real pegada y el tiempo de fork medido en esta
> máquina.

---

## FASE D — Tool ABI

La interfaz única de herramientas. Se congela al final de esta fase, así que **incluye ahora todos
los campos** o pagarás un `abi-v2` en la fase siguiente.

### Qué debe soportar de forma nativa

Streaming de resultados parciales · progreso · **cancelación cooperativa** · deadline absoluto ·
idempotencia · reintento seguro · resultados grandes **por referencia al CAS** · resultados
multimodales · errores tipados que distingan **error de negocio**, **error transitorio** y
**violación de política**.

### Campos que van ahora y no después

`score: int | None` (de él depende la señal de progreso de la fase G) · `plan_step_id` (de él
depende la agrupación del diff review) · `AuthzFacts` · el espacio de nombres de `effect_target` ·
`ResourceClass` · `may_invoke` · `deterministic` · `interruptible` · `checkpointable` ·
`render_hint`.

### El catálogo mínimo

`read_file` · `read_range` · `search` (ripgrep) · `grep` · **`apply_patch`** · `create_file` ·
`delete_file` · `run_command` · `start_process` · `stop_process` · `run_tests` · `build_project` ·
`git_diff` · `git_commit` · `open_browser` · `take_screenshot` · `query_database` ·
`apply_migration` · `deploy_preview` · **`spawn_agent`** (§3.6).

`spawn_agent` va en el ABI **desde ahora**, aunque la fase 1 corra un solo agente: es la invariante 9
y es lo que evita reescribir el contrato entero cuando llegue el momento. En fase 1 se implementa
rechazando `fanout > 1` con un error tipado, no omitiendo la herramienta.

### `apply_patch` es una decisión de producto, no un detalle

Es la herramienta más usada del sistema y la que decide el rendimiento. Formato: **buscar/reemplazar
anclado por texto exacto**, no diff unificado (el modelo se equivoca en los números de línea) y no
reescritura completa (cuesta una fortuna en tokens de salida a 4,40 $/M con 82 % de razonamiento).

Medición ya hecha que lo respalda: `code_blob` da **0,912** de fiabilidad — meter cuarenta líneas de
Python y TypeScript en un campo JSON y que vuelvan byte a byte idénticas funciona. **Verifica el
contenido byte a byte, no que el JSON parsee.**

### Superficie de herramientas

`max_tools_effective` está sin medir de forma fiable. Hasta tenerlo: **ofrece ≤12 herramientas a la
vez**, por perfil y por fase de la tarea, con conmutación explícita de conjunto. El registro puede
ser abierto; lo que se acota es cuántas entran en el prompt.

> **Criterio:** las 19 herramientas implementadas contra el ABI, cada una con test de contrato:
> cancelación a mitad, deadline vencido, resultado grande que va al CAS, error de cada uno de los
> tres tipos.
> **Prueba exigida:** una tabla con las 19 y el resultado de cada test de contrato. Y el ABI
> **congelado**, con un test que falla si alguien le añade un campo sin subir la versión.

---

## FASE E — Ejecución y sandbox

### Qué se construye

- **Scheduler**: cola con prioridades, límites por workspace y por agente, aislamiento de ruido.
  Qué pasa cuando 20 agentes quieren compilar a la vez.
- **Ciclo de vida de una invocación**: admisión → chequeo de capacidad → gate de aprobación →
  ejecución → streaming → timeout → cancelación (cooperativa y forzada) → limpieza de huérfanos →
  registro en el journal.
- **Terminal total** (§3.5), que es el **modo por defecto en la máquina del dueño**: PTY real, su
  shell, su entorno, sin confinamiento de directorio y sin cortafuegos de salida. Sesiones
  persistentes que sobreviven a cerrar el cliente, varias a la vez.
- **Clasificación de efecto derivada del comando, no declarada por el agente.** `ls` es seguro;
  `rm -rf`, `git push --force`, `DROP TABLE`, `terraform apply`, `curl | sh` son irreversibles y
  piden aprobación **antes**. Un agente no puede rebajar su propia clasificación.
- **Techo de efecto por confianza del origen**: un comando cuyo linaje se remonta a contenido no
  confiable —un README, una página web, la salida de una herramienta— **sube de clase y exige
  aprobación**, por inocente que parezca. Esta es la defensa real contra inyección de prompt, y es
  lo que permite dar shell total sin dárselo a cualquiera que sepa escribir un README.
- **Sandbox `sandbox-exec` con perfiles `.sbpl`**, reservado para lo que sí lo necesita: **plugins**
  (código de terceros), **el código que se está construyendo** cuando se ejecuta para probarlo, y el
  día que Forge corra para alguien que no sea el dueño de la máquina. Ahí sí: usuario dedicado,
  deny-all de red con allowlist, y workspace montado desde una `ExecWindow` — nunca desde una ruta
  suelta.
- **Capacidades**: gramática (recurso, verbo, alcance, condición, caducidad). Otorgar, delegar,
  **atenuar** y revocar. Sin escalada por delegación entre agentes.
- **Aprobaciones**: taxonomía por `EffectClass` (orden total de seis, compone con `max()`).
  Aprobación por lote y aprobación permanente con alcance. **El agente nunca se aprueba a sí mismo.**
- **Secretos**: bóveda, inyección sin exposición al modelo, redacción en journal, logs **y
  razonamiento**, detección de fuga en la salida en streaming.
- **`EffectLedger`** con `reserve` / `commit` / `poison` / `resolve` sobre el `append_if` del kernel.
  Es lo que impide desplegar dos veces tras un crash.

### El modelo de amenaza real: inyección de prompt

Un README del repo, una página web o la salida de una herramienta pueden contener instrucciones
dirigidas al agente. **La defensa principal son las capacidades, no la detección de texto.** El
contenido no confiable se marca (taxonomía de confianza de seis niveles, marca de agua alta, alcance
de sesión) y el techo de efecto baja con el nivel de contaminación.

> **Criterio:**
> 1. **Terminal total funcionando**: `vim`, `top` y `ssh` corren en el PTY del agente igual que en
>    el terminal del usuario, con su `$SHELL` y sus alias. Una sesión sobrevive a cerrar el cliente
>    y se recupera con todo su estado.
> 2. `rm -rf`, `git push --force` y `curl … | sh` se clasifican como irreversibles **antes** de
>    ejecutarse y piden aprobación. Un agente que intenta rebajar su propia clasificación **falla**.
> 3. Un comando cuyo origen se remonta a un `README` con instrucciones inyectadas **sube de clase y
>    exige aprobación**, aunque el comando sea trivial. **Test obligatorio: escribe el README
>    malicioso y demuéstralo.**
> 4. Un `run_command` cancelado a mitad **no deja procesos huérfanos** (verificado con `ps`).
> 5. Un secreto inyectado en el contexto que el modelo repite en su razonamiento **no aparece** ni
>    en el journal, ni en los logs, ni en la pantalla. **Test obligatorio.**
> 6. Un efecto irreversible ejecutado dos veces tras un crash simulado ocurre **una sola vez**.
> 7. Dentro del sandbox de plugins —y solo ahí— la red está cerrada y no se puede escribir fuera del
>    workspace.
>
> **Prueba exigida:** los siete, con salida real. El 3, el 5 y el 6 son innegociables.

---

## FASE F — Context Engine

El bloque que decide si el producto sirve. Y el que decide el coste.

### Qué se construye

- **Presupuesto de contexto** contra el `usable_context_tokens` **medido** en B1, no contra la
  ventana anunciada.
- **Selección**: mezcla de léxico (ripgrep) + reciente (historial de edición) + **búsqueda del
  agente** (que busque él con `grep` y `read`). Ese es el suelo y funciona sin nada más.
- **Compresión**: mapa del repositorio con tree-sitter y ranking por grafo de símbolos; esqueletos
  de archivo; diffs en vez de contenido completo; deduplicación por hash entre turnos.
- **Estabilidad de prefijo**: sistema → herramientas → contexto estable → historial → turno actual,
  **en ese orden y sin reordenar jamás**. Vale 5× en el precio de entrada.
- **Resumen de conversación** anclado a evidencia: referencias al journal, no prosa suelta. Qué se
  conserva textual (decisiones, contratos, errores) y qué se resume.
- **`SelectionReason` y `DropRecord`**: por qué entró cada fragmento y qué costó lo que se descartó.
  Los consume el inspector de contexto de la fase I.

### Modos de fallo que hay que nombrar y detectar

Envenenamiento de contexto · contexto rancio tras editar · ancla perdida · **resumen que borra una
restricción crítica**.

> **Criterio:**
> 1. `ratio_cache > 0` en el turno 2 de una conversación, medido contra `cached_tokens` real.
> 2. Coste por tarea del banco **más bajo** que sin motor de contexto, medido en dólares sobre las
>    mismas tareas.
> 3. El inspector muestra el prompt exacto, byte a byte, con la razón de cada fragmento.
>
> **Prueba exigida:** una tabla comparando coste por tarea con y sin motor de contexto, sobre las
> mismas 13 tareas de Edecán. Si no baja, el motor no sirve y hay que decirlo.

---

## FASE G — Agent Runtime · el bucle

Aquí es donde el sistema empieza a hacer algo.

### Qué se construye

- **Máquina de estados**: creado → planificando → ejecutando → esperando herramienta → esperando
  aprobación → bloqueado → pausado → suspendido → fallido → completado → cancelado. Transiciones
  exactas y quién las dispara.
- **El bucle del turno**, con **solapamiento**: 12 s de mediana por llamada obliga a adelantar
  lecturas e indexado mientras se espera al modelo. Encadenar es regalar minutos.
- **Detección de atasco**, con métrica de progreso real: mismo error tres veces, ediciones que se
  deshacen entre sí (vía `subtree_hash`), coste sin avance. Escalera de respuesta: empujón → cambio
  de estrategia → pedir ayuda al humano → parar.
- **Checkpoint y reanudación**: qué se persiste exactamente. Debe sobrevivir a `kill -9`, a un
  reinicio de la máquina y a un despliegue del propio Forge.
- **Presupuesto**: tokens, dinero, tiempo, número de herramientas, profundidad de subagentes. Qué
  pasa al agotarse.
- **Planificación**: representación de la tarea, replanificación, y criterios de aceptación
  verificables atados al plan.

### Multi-agente: contrato sí, implementación no

Las interfaces asumen N agentes. La implementación corre **uno**. Y cuando llegue N: el paralelismo
**debe declarar su partición** — un agente que no puede decir qué archivos va a tocar no puede
correr en paralelo con otro.

> **Criterio — y este es el criterio que define la fase 1 entera:**
>
> **Un agente cierra una tarea real del banco, verificada por contrato, sobreviviendo a un `kill -9`
> a mitad, con su coste medido en dólares.**
>
> **Prueba exigida:** grabación de la sesión completa: el journal exportado, el diff producido, la
> salida de los criterios de aceptación, el coste, y la demostración de la reanudación —matar el
> proceso de verdad, relanzarlo y que termine la tarea.

---

## FASE H — Verificación y aceptación

Lo que separa «redactar código» de «construir software».

> **El actor que hace el trabajo nunca es el actor que declara que está terminado, y la definición
> de terminado se escribe ANTES de tocar código.**

### Qué se construye

- **Contrato de Aceptación**: artefacto ejecutable que nace con la tarea. Declara comandos que deben
  pasar (build, tests, lint, tipos, migraciones), invariantes que no pueden romperse (la cobertura
  no baja, no hay secretos, la API pública no cambia), evidencia visual requerida, y criterios en
  lenguaje natural que exigen un juez.
- **Bucle de verificación barato**: selección de tests por diff, caché de veredictos por hash del
  árbol, ejecución incremental. **Verificar no puede costar más que construir.**
- **Verificación empírica**: levantar el proyecto, abrir el navegador, navegar, rellenar
  formularios, tomar capturas, leer la consola, consultar la base de datos, ejecutar migraciones y
  revertirlas. Con Playwright y **`aria_snapshot`** — árbol de accesibilidad y DOM, no píxeles,
  porque el modelo puede no tener visión y porque un árbol es citable como evidencia.
- **Roles separados**: Developer / QA / Reviewer / Security. El verificador **no ve el razonamiento**
  del developer: ve el diff y el contrato. Si ambos son el mismo modelo, el juez arranca como
  **consultivo, no vinculante**, y hay que decirlo en la interfaz.
- **Detectores de auto-engaño**, automatizados: test debilitado, `skip` añadido, excepción tragada,
  mock que sustituye lo real, éxito declarado sin ejecutar.
- **Control de cambios**: checkpoint, diff, rollback, rama, límite de archivos por tarea, y
  **bisección automática** sobre checkpoints para encontrar qué paso rompió algo 40 pasos atrás.

### Cuando no hay tests

La mayoría de los proyectos reales no tienen suite. Hay que arrancar una red de seguridad desde cero
sin bloquear el trabajo: caracterización del comportamiento actual antes de tocarlo.

> **Criterio:**
> 1. Una tarea con un test que el agente **debilita** para que pase se detecta y se rechaza.
> 2. Una tarea sin tests previos genera su red de seguridad antes de modificar nada.
> 3. El coste de verificar es **inferior** al de construir, medido sobre el banco.
>
> **Prueba exigida:** los tres, con salida real. El 1 se demuestra saboteando a propósito.

---

## FASE I — Interfaz de escritorio

Ahora, y no antes. Todo lo anterior es lo que la interfaz proyecta.

- Las dos vistas de §3.1.
- **Transporte**: SSE multiplexado con cursor de proyección. Reconexión con backfill desde cursor.
  50.000 eventos no pueden saturar el navegador: virtualización con TanStack Virtual.
- **El inspector de contexto** — el panel más importante para depurar: el prompt exacto, por qué
  entró cada fragmento, cuántos tokens costó, qué se descartó.
- **Diff review a escala**: 400 archivos agrupados por paso del plan, con riesgo por archivo y
  **aprobación parcial**.
- **Interrupción sin matar**: inyectar un mensaje, redirigir, o retroceder a un punto del journal.
- Los seis patrones visuales de §8.

> **Criterio:**
> 1. **El estado de reposo es el de §3.1**, píxel a píxel en espíritu: fondo casi negro, barra
>    izquierda de ~340 px con proyectos y sesiones, compositor centrado con su pastilla de proyecto
>    y su fila única de controles, ajustes anclado abajo. **Sin barra de menús, sin cinta de iconos,
>    sin barra de estado.** Si al abrirlo lo primero que se ve es un árbol de archivos, está mal.
> 2. Los tres estados son **el mismo espacio transformándose**, no tres pestañas.
> 3. 5.000 eventos en la línea de tiempo a **60 fps**, medido.
> 4. Un diff de 400 archivos revisable **sin scroll infinito**, agrupado semánticamente.
> 5. Corregir a un agente en marcha **sin matarlo**.
> 6. Claro y oscuro, ambos, con contraste accesible verificado.
> 7. **El terminal es un terminal de verdad**: `vim` y `top` se ven y se usan bien dentro del IDE.
>
> **Prueba exigida:** capturas de los tres estados, medición real de fps con 5.000 eventos, una
> grabación de la corrección en marcha, y una grabación de `vim` funcionando en el terminal
> embebido.

---

## FASE J — Móvil

Las cuatro pestañas de §3.2, nativas, sobre la misma proyección.

- Diff táctil con agrupación semántica.
- Aprobación **con contexto suficiente**: qué cambia, qué es irreversible, qué criterios ya pasaron.
  Un botón de aprobar sin ver qué se aprueba es peor que no tener botón.
- Modo seguir, con las tres reglas de §3.3.
- **Notificaciones**: solo lo que necesita decisión. Si la cola se llena de trivialidades, el usuario
  aprende a aprobar sin leer y la supervisión se vuelve teatro.

> **Criterio:**
> 1. Aprobar un despliegue desde el teléfono, de principio a fin.
> 2. El cursor rancio **bloquea** la aprobación.
> 3. Aprobar desde el móvil con el diálogo abierto en el Mac ejecuta el efecto **una sola vez**.
>
> **Prueba exigida:** vídeo o secuencia de capturas de los tres, en dispositivo o simulador.

---

## FASE K — Plugins y MCP

- Manifiesto, aislamiento **en proceso separado obligatorio**, versionado semántico, permisos
  declarados y consentidos.
- La `EffectClass` de una herramienta la **deriva el kernel** de las capacidades que pide el
  manifiesto. El plugin solo puede subirla, nunca bajarla. Si el plugin se autoclasifica, uno
  descuidado declara `read` y evade todas las reglas de aprobación: sería el mayor agujero del
  sistema, y además invisible.
- **Adaptador MCP bidireccional**: consumir servidores MCP externos y exponer Forge como servidor.
  Reutiliza `packages/mcp`.

> **Criterio:** un plugin malicioso —que intenta leer fuera de su alcance, salir a la red y agotar
> memoria— **no compromete el sistema**, y los tres intentos quedan registrados en el journal.
> **Prueba exigida:** ese plugin escrito a propósito, ejecutado, y el journal mostrando los tres
> rechazos.

---

## FASE L — Multi-agente

Se **activa** aquí; el contrato existe desde la fase D (§3.6). Solo cuando las anteriores estén
verdes.

- Levantar el rechazo de `fanout > 1` en `spawn_agent`.
- **Las cuatro recetas** de §3.6 (`funcion_nueva`, `arreglar_bug`, `migracion`, `auditoria`) como
  dato, no como código: añadir una receta debe ser escribir un archivo.
- Topología supervisor-obrero con **partición declarada obligatoria**.
- Comunicación **solo por eventos**, nunca por llamada directa.
- Workspace copy-on-write por agente y protocolo de fusión explícito.
- Reparto de presupuesto por arriendo del padre, renovable, con bloqueo al agotarse.
- Detección de trabajo duplicado, deadlock y livelock.
- Delegación libre (que el modelo invente el equipo) **detrás de un interruptor**, nunca por defecto.

> **Criterio:**
> 1. Tres agentes migran 60 archivos en particiones disjuntas y la fusión **no pierde ningún
>    cambio**.
> 2. Un agente que intenta lanzar un hijo **sin declarar partición** recibe un error y se ejecuta en
>    serie.
> 3. Un hijo que agota su arriendo **se bloquea y pide**, no gasta del padre por su cuenta.
> 4. **La comparación honesta**, sobre el mismo banco: recetas contra delegación libre, y N agentes
>    contra uno, medidos en tiempo Y en dinero.
>
> **Prueba exigida:** una tabla con los cuatro. Y si resulta que un solo agente gana, **dilo**: es
> un resultado, no un fracaso, y ahorra construir lo que no hace falta.

---

## FASE M — Observabilidad y evaluación continua

- Trazas OpenTelemetry: qué es un span aquí.
- **Registro obligatorio por turno**: pensamiento, herramienta invocada, archivo modificado, comando
  ejecutado, duración, tokens, modelo, contexto recibido, respuesta producida.
- Responder «¿qué hizo el agente ayer a las 4?» en **menos de 30 segundos**.
- **Convertir una sesión real en un caso de prueba reproducible.** Ese es el activo que sobrevive al
  crédito de Cloudflare.

> **Criterio:** la pregunta de las 4 respondida en <30 s con evidencia, y una sesión real convertida
> en caso de banco ejecutable.

---

# PARTE VI — LAS SEIS MÉTRICAS

Si no mejoran, el sistema no mejora por mucho que crezca el código.

| Métrica | Qué responde |
|---|---|
| **Tareas cerradas sin intervención humana** | La métrica norte |
| **Coste en dólares por tarea aceptada** | Si baja mientras la anterior sube, el motor de contexto funciona |
| **Tasa de reversión a 7 días** | Cuánto de lo aceptado era un falso «terminado» |
| **Tiempo hasta el primer diff verificado** | El ancho de banda de corrección humana |
| **Sesiones que sobreviven a un reinicio** | La durabilidad, medida y no prometida |
| **Tiempo de cambio de proveedor** | Un test que cambia la config y verifica que la suite pasa igual. Si tarda más de un día, el acoplamiento ya entró |

---

# PARTE VII — TRAMPAS CONOCIDAS

Cada una ya costó tiempo real en este proyecto.

1. **`uv sync` a secas rompe el entorno.** Deja instalado solo el paquete que resuelve y se lleva
   por delante `edecan_llm` y el resto. Siempre **`uv sync --all-packages`**.
2. **`+sucio` no es una huella.** Si la revisión de sonda no incluye un hash del código fuente, se
   edita una sonda, se relanza y se reusan en silencio los resultados **de antes del cambio**. Ya
   está arreglado en `forge-probe`; no lo desarregles. Una medición silenciosamente obsoleta es peor
   que ninguna: **parece un dato**.
3. **Un caso de prueba imposible de acertar mide un defecto del caso, no del modelo.** 14 «fallos»
   que eran un punto final ambiguo. Mira la traza cruda antes de concluir.
4. **`content` vacío con `finish_reason: length` es un error recuperable, no una respuesta.** El
   razonamiento se comió el presupuesto y se cobró igual.
5. **`tool_calls[].function.arguments` es un string JSON, no un objeto.** Con bloques de código
   dentro es donde se rompe. Compara byte a byte.
6. **La respuesta de Workers AI viene envuelta**: `{"result": {...}, "success": true}`. Hay que
   desenvolver **y** comprobar `success`: un fallo puede llegar con HTTP 200.
7. **La evidencia de la sonda va a `.forge-probe/` en la raíz**, ya ignorado por git — contiene
   prompts y respuestas en crudo.
8. **Correr solo tu paquete y llamarlo verde.** Pasó: 127 verdes en un paquete, 9 rojos en el repo.
9. **El token nunca se imprime**, ni truncado, ni en un mensaje de error.

---

# PARTE VIII — CÓMO SE REPORTA

Al terminar **cada fase**, entrega el bloque de §11.3. Completo. Sin excepciones.

Y tres preguntas que hay que responder explícitamente en cada entrega:

1. **¿Qué decidí que no estaba escrito aquí?** Toda decisión no especificada, con su razón. El
   silencio sobre una decisión propia es el origen de la mitad de las divergencias.
2. **¿Qué no funciona?** Un hueco declarado vale. Uno escondido envenena todo lo que se construya
   encima.
3. **¿Qué me haría cambiar de opinión?** Si una decisión de este documento parece equivocada a la
   luz de lo que descubriste construyendo, **dilo con el dato que lo respalda**. Este documento se
   escribió antes de construir; la medición manda sobre él.

---

# PARTE IX — DEFINICIÓN DE TERMINADO

Forge está terminado cuando **todo esto es cierto a la vez**:

- [ ] Un agente lleva una tarea real del banco de cero a verificada, sin ayuda.
- [ ] Sobrevive a `kill -9`, a un reinicio de la máquina y a un despliegue del propio Forge.
- [ ] El agente tiene **acceso total al terminal** —`vim`, `ssh`, `docker`, `sudo`, salida a
      internet— y aun así ningún comando irreversible se ejecuta sin aprobación, ni un README
      malicioso consigue que se ejecute algo en silencio.
- [ ] Al abrir el IDE, lo primero que se ve es un campo de texto, no un árbol de archivos.
- [ ] El humano entiende qué pasó en 10 segundos y corrige el rumbo en menos de un minuto.
- [ ] Un diff de 400 archivos se revisa sin morir.
- [ ] Se puede aprobar un despliegue desde el teléfono.
- [ ] Cada afirmación de «esto funciona» está atada a un evento del journal con su salida real.
- [ ] Cambiar de modelo es editar `config/modelos.yml` y nada más.
- [ ] La suite entera del repositorio está verde, y la evidencia es la salida pegada, no la palabra
      de nadie.

---

> **Lo último, y es lo que más importa de todo el documento.**
>
> El mayor riesgo de este plan no es que una fase salga mal. Es que se construya entero antes de que
> un agente resuelva una sola tarea real. Un sistema con trece fases perfectas y cero tareas
> completadas es deuda pura: meses invertidos en suposiciones que nunca se contrastaron.
>
> Por eso el orden es este y no otro, por eso la fase B va antes que todo el código, y por eso el
> criterio de la fase G —**un agente cierra una tarea real, verificada, sobreviviendo a que lo
> maten**— es el que de verdad decide si esto sirve.
>
> Construye en ese orden. Demuestra cada paso. No rellenes un número que no mediste.
