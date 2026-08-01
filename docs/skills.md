# Skills — marketplace abierto de "Agent Skills"

Edecán se conecta al estándar abierto **"Agent Skills"** — el mismo formato que indexa
[skills.sh](https://skills.sh) — para instalar y usar capacidades de terceros dentro del
agente, en vez de mantener un catálogo propietario cerrado (`ARCHITECTURE.md` §12,
especialmente §12.e).

## Qué es un "Agent Skill"

Un "Agent Skill" es, literalmente, un repositorio (o una carpeta dentro de un repositorio)
con un archivo `SKILL.md`: un frontmatter YAML (`name`, `description`, opcionalmente
`version`/`license`/más metadata) seguido de un cuerpo en markdown que son
**instrucciones para el agente**, en lenguaje natural — no código que se ejecute. Es el
mismo formato que ya usan más de 20 agentes de terceros (Claude Code, Cursor, GitHub
Copilot, Windsurf, Gemini, Cline, VS Code, Zed, entre otros) y que se instala normalmente
con `npx skills add <owner/repo>`.

## Relación con skills.sh

[skills.sh](https://skills.sh) es un directorio open-source de Agent Skills: al momento de
decidir esta integración tenía del orden de **~912,000 instalaciones totales** registradas
(la skill más instalada, por sí sola, con 2.4M), agregando skills de fuentes como OpenClaw
(~13,700 skills) y Hermes Agent. Es gratis, abierto, y expone una API de búsqueda.

Edecán **no depende de que la API de skills.sh esté arriba** para instalar nada: el índice
es solo un atajo de descubrimiento (buscar por palabra clave). El mecanismo real de
instalación replica exactamente lo que hace `npx skills add <owner/repo>` — leer el
`SKILL.md` directo desde `raw.githubusercontent.com` (la API pública oficial de GitHub
para contenido raw) — así que instalar por `owner/repo` funciona siempre, con o sin
skills.sh disponible.

Desde el chat, `buscar_skills` puede además consultar **OpenClaw** y **Hermes Agent**
directamente (las dos fuentes que el propio skills.sh agrega) como índices independientes
— ver "Fuentes OpenClaw y Hermes" más abajo.

## Cómo instalar

### Desde la pantalla "Skills"

En `/app/skills`: un buscador pega tu palabra clave contra el índice de skills.sh y
muestra nombre, fuente (`owner/repo`) e instalaciones reportadas cuando el índice las
tiene; cada resultado trae un botón "Instalar". Si ya sabes el `owner/repo` que quieres,
"Instalar directo" lo instala sin pasar por la búsqueda. Debajo, la lista de instaladas
permite activar/desactivar, ver el `SKILL.md` completo (en un bloque monoespaciado,
perezoso — solo se pide al abrirlo) y desinstalar con confirmación.

### Desde el chat

Cinco herramientas del agente, en español, disponibles en **todos los planes** (no hay
flag de plan nuevo para esto — el marketplace es parte del toolkit base):

| Herramienta | `dangerous` | Argumentos | Qué hace |
|---|---|---|---|
| `buscar_skills` | no | `consulta`, `fuente` (opcional: `skills_sh` default, `openclaw`, `hermes`) | Busca por palabra clave en el índice elegido. Solo descubrimiento. |
| `instalar_skill` | **sí** | `source`, `fuente` (opcional: `directo` default, `skills_sh`, `openclaw`, `hermes`) | Descarga el `SKILL.md` de la fuente y la deja instalada. Exige confirmación humana (ver "Modelo de seguridad"). `fuente` decide `trust_tier` — ver "Seguridad de skills de terceros". |
| `listar_skills` | no | — | Lista tus skills instaladas, con su estado (activa/inactiva). |
| `usar_skill` | no | `nombre` | Trae el contenido completo de una skill instalada y activa, para que el agente siga sus instrucciones en lo que resta de la conversación. |
| `desinstalar_skill` | no | `nombre` | Borra una skill instalada. |

Si acabas de `buscar_skills` con `fuente="openclaw"` (por ejemplo) y quieres instalar uno de
los resultados, pasa el mismo `fuente="openclaw"` a `instalar_skill` — así la skill queda
marcada `trust_tier="indexada"` en vez de `"sin_revisar"`.

### Fuentes soportadas por `source`/`owner/repo`

`instalar_skill`/`POST /v1/skills/install` aceptan cualquiera de estas 4 formas:

- `owner/repo` — instala el `SKILL.md` de la raíz (o de una de las rutas convencionales, ver abajo).
- `owner/repo/sub/path` — un `SKILL.md` que vive en una subcarpeta del repo.
- `https://github.com/owner/repo` o `https://github.com/owner/repo/tree/<branch>/<path>` — una URL de GitHub; el branch de la URL se ignora a propósito, la descarga siempre resuelve contra la rama por defecto del repo (`HEAD`).
- `https://skills.sh/owner/repo` — una URL de la página de detalle de skills.sh.

Al resolver, se prueban en orden 3 rutas candidatas contra `raw.githubusercontent.com` — la
primera que responda `200` gana:

1. `{owner}/{repo}/HEAD/{subpath/}SKILL.md` (o `SKILL.md` en la raíz si no hay subpath)
2. `{owner}/{repo}/HEAD/skills/{repo}/SKILL.md`
3. `{owner}/{repo}/HEAD/skill/SKILL.md`

Si ninguna de las tres responde `200`, la instalación falla con un mensaje claro
("no se encontró SKILL.md en «owner/repo»").

## Modelo de seguridad

- **Instalar exige confirmación humana.** `instalar_skill` es una herramienta
  `dangerous=True` (`ARCHITECTURE.md` §10.7): dentro de una conversación, el agente nunca
  la ejecuta sin que el usuario apruebe explícitamente ese tool call — instalar trae
  instrucciones escritas por un tercero que el agente seguirá literalmente en cuanto se
  active con `usar_skill`. Desde la pantalla `/app/skills` no se repite ese gate porque el
  clic del usuario en "Instalar" **es** la confirmación humana — pero el aviso de que "son
  instrucciones de terceros, revísalas" queda visible en la propia pantalla.
- **El contenido de una skill NUNCA anula las reglas del sistema.** `usar_skill` envuelve
  el `SKILL.md` con un encabezado explícito antes de dárselo al modelo — el mismo
  principio que ya aplica `edecan_core.persona.build_system_prompt` a las instrucciones
  personalizadas del usuario: son una guía, no un reemplazo de los guardrails de
  seguridad de Edecán.
- **Edecán no ejecuta nada de una skill — límite deliberado de v3.** Un Agent Skill puede,
  en teoría, traer scripts o binarios en su repositorio (como haría un plugin real de
  Claude Code). Edecán **solo lee el texto de `SKILL.md`** y se lo entrega al modelo como
  instrucciones; nunca descarga, nunca ejecuta ni un script ni un binario del repo de
  origen. Esto simplifica el modelo de amenazas a "texto que un tercero escribió" (el
  mismo riesgo que ya existe con cualquier instrucción personalizada del usuario o
  contenido leído de la web) en vez de "código de un tercero corriendo en tu máquina".
- **Cap de tamaño.** El `SKILL.md` descargado se corta en streaming apenas supera
  **200,000 bytes** (`SkillDemasiadoGrandeError`, mapeado a `413` en la API) — protege
  contra un archivo enorme o malicioso sin tener que bajarlo completo primero.
- **Hosts permitidos, a propósito una lista corta.** `parse_source()` solo acepta URLs
  cuyo host sea `github.com` o `skills.sh` (o sus variantes `www.`) — cualquier otro host
  se rechaza de entrada. La descarga real, sin excepción, se arma siempre a mano contra
  `raw.githubusercontent.com`: el host que el usuario haya pegado en una URL nunca se usa
  para construir la petición HTTP real, solo para extraer `owner`/`repo`/`subpath`. Esto
  es anti-SSRF (nunca se le pide a Edecán que haga una petición a un host arbitrario) y
  anti path-traversal (`owner`/`repo`/cada segmento del subpath se valida contra una
  regex estricta que rechaza `..`, espacios y caracteres de shell).

Lo de arriba es sobre CÓMO se descarga una skill; la siguiente sección es sobre qué hace
Edecán con lo que descargó — trust tiers, capacidades declaradas y el escáner
anti-inyección (fase v5).

## Seguridad de skills de terceros

Adaptado de `open-jarvis/OpenJarvis` (Apache-2.0, `openjarvis.skills.security` +
`openjarvis.security.scanner`/`injection_scanner`) al modelo multi-tenant de Edecán — ver
`packages/skills/edecan_skills/security.py` para la implementación completa.

### Trust tiers

Cada skill instalada queda clasificada en uno de dos niveles (`edecan_skills.security.
TRUST_TIERS`), visible como badge en `/app/skills` y en el campo `trust_tier` de la API:

| Tier | Cuándo aplica |
|---|---|
| `indexada` | Se instaló a partir de un resultado de `buscar_skills` en un índice curado: skills.sh, OpenClaw o Hermes (`fuente` del argumento/body de instalación). |
| `sin_revisar` | Se instaló directo por `owner/repo`/URL, sin pasar por ningún índice. |

**"Indexada" no significa "auditada por Edecán"** — solo que el índice de origen la listó.
Sigue siendo contenido escrito por un tercero, con el mismo modelo de amenazas que cualquier
otra skill (ver "Modelo de seguridad" arriba). OpenJarvis además distingue tiers `bundled`
(empaquetada con el propio producto) y `workspace` (carpeta de skills del proyecto local del
usuario) que **no aplican todavía a Edecán v5** — hoy solo se instala por red.

### Capacidades declaradas

Un `SKILL.md` puede declarar, en su frontmatter, qué herramientas del agente espera poder
usar con el campo estándar `allowed-tools` (lista YAML o string separado por comas —
`edecan_skills.installer.parse_capabilities`, mismo campo que usan Claude Code y otros
agentes que ya consumen Agent Skills):

```yaml
---
name: enviador-de-recordatorios
description: Manda un correo de recordatorio todos los lunes.
allowed-tools: [enviar_correo, crear_recordatorio]
---
```

Esas capacidades se normalizan a snake_case y se persisten en `capabilities`. **Declarar una
capacidad NUNCA le da a la skill ningún poder real** — Edecán solo lee el texto de
`SKILL.md` (ver "Modelo de seguridad"), nunca ejecuta nada; es únicamente la señal de riesgo
que la UI/el chat muestran antes de activarla. `capabilities_peligrosas` (calculado por la
API, `edecan_skills.security.CAPACIDADES_PELIGROSAS`) es el subconjunto que coincide con una
tool `dangerous=True` real del repo: `usar_computadora`, `enviar_mensaje`, `enviar_correo`,
`enviar_sms`, `llamar_contacto`, `lanzar_campana`, `publicar_social`, `preparar_pago`,
`preparar_orden`, `gestionar_automatizacion`, `preparar_nomina`, `preparar_reserva`.

Cuando `usar_skill` activa una skill con capacidades peligrosas y sigue habilitada, antepone
un banner de advertencia antes de su contenido; y **siempre**, tenga o no capacidades
peligrosas, antepone un recordatorio de una línea de que lo que sigue es texto de un
tercero, no una instrucción del sistema (port del principio de defensa de OpenJarvis).

### Escaneo anti-inyección (heurístico, best-effort)

Al instalar (o reinstalar) una skill, `edecan_skills.security.escanear_inyeccion` revisa el
`SKILL.md` completo buscando patrones típicos de intento de anular instrucciones:

- Frases imperativas de anulación ("ignore previous instructions", "olvida tus
  instrucciones", en inglés y español).
- Suplantación de system prompt / jailbreak ("you are now", "system prompt", "jailbreak",
  "DAN mode").
- Exfiltración: URLs con una plantilla tipo `{api_key}`/`{token}`/`{password}` en vez de un
  valor literal, o `data:` URIs.
- Caracteres de ancho cero (técnica para esconder texto invisible).
- Comentarios HTML (`<!-- ... -->`) que esconden alguna de las frases de arriba — muchos
  renderizadores de Markdown ni siquiera los muestran.
- Bloques de más de 400 caracteres contiguos de aspecto base64.

**Esto es honestamente limitado: son regexes sobre texto plano, sin ningún modelo de
lenguaje ni analizador semántico.** Detecta los patrones más comunes y documentados, pero un
atacante con suficiente esfuerzo puede ofuscar texto para evadirlo — es una capa de defensa
en profundidad, nunca una garantía de que una skill sin hallazgos sea segura.

Si el escaneo encuentra algo, la skill se instala **igual** (Edecán nunca bloquea una
instalación) pero queda `enabled=false` automáticamente, y la respuesta de instalación
(tool o API) lista los hallazgos encontrados. Reinstalar una skill que ya tenías activa con
contenido nuevo que SÍ trae hallazgos también la fuerza a `enabled=false` — una skill limpia
no puede volverse maliciosa en silencio vía reinstalo. Al revés, reinstalar con contenido
limpio nunca reactiva en silencio una skill que habías desactivado a propósito (ver "Qué
pasa si reinstalas" más abajo).

### Activar con `acknowledge`

`PUT /v1/skills/{id}` (el toggle activar/desactivar de `/app/skills`) exige
`{"acknowledge": true}` en el body **solo** para activar (`enabled: true`) una skill que
declara alguna capacidad peligrosa o que tiene hallazgos de inyección — desactivar nunca lo
exige, y activar una skill sin ninguna señal de riesgo tampoco. Sin ese campo, la API
responde `400` con un `detail` que explica exactamente qué se está aceptando (qué
capacidades, cuántos hallazgos). En `/app/skills`, esto se ve como una confirmación inline
("¿Activar de todos modos?") con el mismo mensaje del backend — nunca un `window.confirm`
genérico.

## Fuentes OpenClaw y Hermes

Además del índice de skills.sh, `buscar_skills` (y `POST /v1/skills/search` — hoy solo
skills.sh, ver "Referencia HTTP") puede consultar dos fuentes más directamente:

- **OpenClaw** (`github.com/openclaw/skills`, ~13,700 skills): layout
  `skills/<owner>/<nombre>/SKILL.md`.
- **Hermes Agent** (`github.com/NousResearch/hermes-agent`, ~150 skills): layout
  `skills/<categoria>/<nombre>/SKILL.md`.

`edecan_skills.sources.OpenClawSource`/`HermesSource` son una adaptación de
`openjarvis.skills.sources.openclaw`/`hermes` (Apache-2.0) con una diferencia obligatoria:
el original clona el repo completo con `git clone`/`git pull` a un caché local en disco —
prohibido acá (regla dura: nunca acoplarse a `git`). En su lugar, cada búsqueda descarga el
tarball oficial de GitHub (`codeload.github.com/<owner>/<repo>/tar.gz/HEAD`, en streaming
con un cap de tamaño propio — mucho más generoso que el de un `SKILL.md` individual, un
índice trae miles de archivos) y lo recorre **en memoria** con `tarfile`, sin tocar el
filesystem ni mantener ningún estado entre llamadas. Ante cualquier fallo (red, formato
inesperado) `search()` devuelve `[]` — mismo criterio best-effort que
`SkillsIndexClient.search`, un índice caído nunca debe tumbar `buscar_skills`.

El `source` que devuelve cada resultado ya viene formado como el `"owner/repo/subpath"`
exacto que el pipeline de instalación existente (`edecan_skills.installer.
install_from_source`) resuelve sin ningún cambio — instalar una skill de OpenClaw/Hermes no
es un camino especial, es el mismo `instalar_skill`/`POST /v1/skills/install` de siempre.

## Qué pasa si reinstalas una skill que ya tenías

Instalar una fuente que ya estaba instalada **actualiza** la fila existente (contenido,
versión, descripción, fuente, `trust_tier` y `capabilities`) en vez de crear una duplicada —
cada skill instalada tiene un `slug` único por tenant (derivado de su nombre), y "instalar"
algo que ya estaba ahí es, en efecto, traer la versión más reciente. Reinstalar con
contenido **limpio** nunca reactiva en silencio una skill que habías desactivado a
propósito: el campo activa/inactiva no lo toca ese reinstalado. Si el contenido nuevo trae
hallazgos de inyección, en cambio, la fila se fuerza a `enabled=false` sin importar si ya
estaba activa (ver "Escaneo anti-inyección" arriba).

## Referencia HTTP (`/v1/skills`)

Auth: `Bearer` (access token), sin flag de plan adicional. Ver
[`api.md`](./api.md) para el resto de rutas de la API.

| Ruta | Qué hace |
|---|---|
| `GET /v1/skills` | Skills instaladas por ti en el tenant actual — sin el contenido completo del `SKILL.md` (mismo criterio que otras listas: liviana, sin arrastrar potencialmente ~200 KB por fila). Incluye `trust_tier`/`capabilities`/`capabilities_peligrosas`; NUNCA `hallazgos` (necesita `contenido`). |
| `GET /v1/skills/{id}` | Detalle de una skill instalada, con `contenido` completo y `hallazgos` (resultado de `escanear_inyeccion` sobre ese `contenido`, calculado al vuelo). `404` si no existe o es de otro tenant. |
| `POST /v1/skills/search {"q": "..."}` | Búsqueda best-effort en el índice de skills.sh — nunca falla, `[]` si el índice está caído o sin resultados. Solo skills.sh (a diferencia de `buscar_skills` en el chat, que también puede consultar OpenClaw/Hermes). |
| `POST /v1/skills/install {"source": "owner/repo", "fuente": "directo"}` | Instala (o reinstala) desde cualquiera de las 4 formas de fuente soportadas. `fuente` (opcional, default `"directo"`; también acepta `"skills_sh"`/`"openclaw"`/`"hermes"`) decide `trust_tier`. `201` con la skill creada (incluye `hallazgos`; `enabled=false` si el escaneo encontró algo); `400` fuente inválida, `404` no se encontró `SKILL.md`, `413` demasiado grande. |
| `PUT /v1/skills/{id} {"enabled": bool, "acknowledge": bool}` | Activa/desactiva. `acknowledge` (default `false`) es obligatorio en `true` para activar una skill con capacidades peligrosas o hallazgos de inyección. `204`; `400` si falta `acknowledge` cuando hace falta (el `detail` explica qué se está aceptando); `404` si no existe. |
| `DELETE /v1/skills/{id}` | Desinstala. `204`. |

## Paquete y tests

La implementación vive en `packages/skills/edecan_skills/` (`client.py` para el índice de
skills.sh, `installer.py` para el pipeline de instalación, `security.py` para trust
tiers/capacidades/escaneo anti-inyección, `sources.py` para OpenClaw/Hermes, `store.py`
para la tabla `skills`, `tools.py` para las 5 herramientas) — ver
[`../packages/skills/README.md`](../packages/skills/README.md) para el detalle técnico
módulo por módulo. Tests offline con `respx` (nunca red real) en `packages/skills/tests/` y
`apps/api/tests/test_skills_router.py`.
