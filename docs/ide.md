# IDE embebido

El IDE embebido deja explorar, editar y correr comandos en una carpeta de **tu propia computadora** desde el panel web de Edecán, apoyándose por completo en las acciones del companion (`apps/companion/`, ver también [`api.md`](./api.md) sección "Companion de escritorio"). La API nunca toca directamente ese filesystem: cada acción viaja `web → API → companion → tu máquina`. En la app instalada el companion es un bridge in-process; en modo hospedado viaja por WebSocket al proceso emparejado.

Es **P0: real y funcional hoy**, no un diseño. Sus acciones (`list_tree`, `search_files`, `apply_edit`, `trash_path`, `screenshot`) pasan por el mismo pipeline de sandbox + aprobación humana + auditoría que ya usaban `read_dir`/`read_file`/`write_file`/`run_command` desde v1 — nada de esto es una vía nueva ni más permisiva.

## Requisitos

1. La app de escritorio instalada, que ya contiene el bridge local; o, en modo hospedado, el companion separado corriendo y emparejado (`cd apps/companion && python -m edecan_companion --server ... --code ...`).
2. Tu plan con el flag `companion.ide` (✔ en los 4 planes de `edecan_schemas.plans.PLANES` hoy — `free_selfhost`, `hosted_basic`, `hosted_pro`, `hosted_business`).
3. Los comandos que quieras poder correr desde la terminal del IDE, agregados a `allowed_commands` en el `companion.yaml` que corresponda (ver más abajo) — vacío por defecto.

La app instalada se registra como conectada automáticamente. En modo hospedado, sin companion conectado, la página `/app/ide` muestra un banner con instrucciones y cualquier acción responde `503`.

## Emparejamiento

Igual que el resto del companion (no hay un pairing "especial" para el IDE):

En la app instalada no hay pairing adicional: la sesión local autenticada ya
resuelve al único dueño y registra el bridge al arrancar. Los pasos siguientes
aplican solo al modo hospedado o a un equipo adicional:

1. En **Ajustes → Companion de escritorio** (panel web), genera un código de un solo uso (`POST /v1/companion/pair-code`, válido 10 minutos).
2. Corre `python -m edecan_companion --server <URL de tu API> --code <CÓDIGO>`.
3. La terminal del companion queda esperando comandos — cada acción que el IDE le pida se te pregunta ahí (`¿Permitir «list_tree» con {...}? [y/N]`) salvo que la hayas puesto en `auto_approve`, o que ya la hayas aprobado hace poco con `remember_approvals_minutes` activo (ver abajo).

## Configuración del companion

El IDE embebido no tiene un archivo de configuración propio: usa las mismas claves que el resto del companion (`apps/companion/edecan_companion/config.py`). El proceso separado usa `~/.edecan/companion.yaml`; la app macOS empaquetada usa `~/Library/Application Support/cc.edecan.desktop/data/companion.yaml`. En ambos casos hay que reiniciar el proceso después de editarlo.

```yaml
sandbox_dir: "~/EdecanSandbox"    # única carpeta que el IDE puede ver/editar/correr comandos dentro de ella
allowed_apps: []
allowed_commands: []               # EL USUARIO decide qué binarios permitir aquí para "Ejecutar" en la terminal del IDE
auto_approve: []
remember_approvals_minutes: 0      # 0 = siempre pregunta; N>0 = recuerda un "sí" por acción durante N minutos
ide_enabled: true                  # apaga las acciones del IDE de un tirón si lo pones en false
```

### `sandbox_dir`

Es la carpeta raíz que ves en el árbol de archivos del IDE, en la que puedes abrir/editar/guardar archivos, y en la que corre la terminal (`cwd` fijo). **Todo** — `list_tree`, `search_files`, `apply_edit`, y las rutas que le pases a `GET/PUT /v1/ide/file` — está encerrado ahí: una ruta con `..`, una ruta que "parece" absoluta, o un enlace simbólico que apunte afuera, se rechaza (o, para `list_tree`/`search_files`, simplemente no se recorre/lee — ver "Seguridad" abajo). Cámbiala a la carpeta de tu proyecto real si quieres editar código de verdad en vez del sandbox vacío por defecto.

### `allowed_commands` — la terminal del IDE

`POST /v1/ide/run` (la terminal del IDE) usa exactamente el mismo `run_command` que ya existía en v1: solo corre ejecutables listados en `allowed_commands`, siempre **sin shell** (nunca interpreta `;`, `&&`, tuberías, ni expansión de variables — un `echo hola; rm -rf /` corre `echo` con el argumento literal `"hola;"`, no dos comandos), con timeout de 30 segundos y hasta 10 KB de salida (se trunca, marcado con `truncated: true`).

**Tú decides qué binarios permitir ahí — incluidos `git` o `docker` si quieres.** Edecán no trae ninguno permitido por defecto (la lista empieza vacía, como todo lo demás del companion): si quieres poder correr `git status` o `docker ps` desde la terminal del IDE, agrégalos tú mismo a `allowed_commands`. No hay ninguna integración "de primera clase" con control de versiones o contenedores en este WP — es, literalmente, el mismo `run_command` genérico de siempre, y lo que le permitas correr depende enteramente de tu propia lista blanca.

### `remember_approvals_minutes`

Por defecto (`0`) cada acción del IDE pide aprobación en la terminal del companion **siempre**, sin excepción — abrir 10 archivos seguidos son 10 preguntas de `read_file`. Si lo subes a un número > 0, la primera vez que apruebas una acción (por nombre — p. ej. `apply_edit`, no por archivo/parámetros) esa acción queda recordada en memoria (nunca en disco) durante esos minutos: mientras dure, se auto-aprueba sin volver a preguntar. Un **no** nunca se recuerda — decir que no siempre vuelve a preguntar la próxima vez. Reiniciar el companion olvida todo lo recordado.

Esto aplica al companion separado por WebSocket. En la app instalada, la
propia acción autenticada en la pantalla IDE es la aprobación: no existe otra
terminal oculta donde contestar. El bridge mantiene una allowlist cerrada de
acciones IDE y deja que `edecan_companion.actions` aplique los controles reales.

### `ide_enabled`

A diferencia de las demás listas (que empiezan vacías/apagadas), `ide_enabled` empieza en `true`: las acciones del IDE se comportan como cualquier otra acción del companion desde el primer momento (piden aprobación cada vez, salvo `auto_approve`/`remember_approvals_minutes`) — no hace falta que las prendas a mano. Ponlo en `false` si quieres bloquear las 4 acciones del IDE por completo en esta máquina, sin tocar `allowed_apps`/`allowed_commands` una por una. Con `ide_enabled: false`, el companion rechaza `list_tree`/`search_files`/`apply_edit`/`screenshot` **antes** de preguntar nada en la terminal.

## Acciones del companion

| Acción | Qué hace | Límites |
|---|---|---|
| `list_tree` | Árbol recursivo de una carpeta: `{path?, max_depth?, max_entries?}` → `{path, entries, truncated}` | `max_depth` ≤ 5, `max_entries` ≤ 500 (recortados en silencio, nunca fallan); ignora siempre `.git`, `node_modules`, `__pycache__`, `.venv` |
| `search_files` | Busca texto línea por línea: `{query, path?}` → `{query, matches: [{path, line, texto}], truncated}` | substring sin distinguir mayúsculas; hasta 2000 archivos considerados, 200 coincidencias devueltas, líneas cortadas a 200 caracteres; solo archivos de texto UTF-8 < 256 KB |
| `apply_edit` | Reemplaza `old_string` por `new_string`: `{path, old_string, new_string, replace_all?}` → `{path, replacements, bytes_written}` | sin `replace_all`, `old_string` debe ser único en el archivo (si no, error con el conteo); escritura atómica (archivo temporal + `rename`); mismo tope de 256 KB que `read_file` |
| `trash_path` | Envía un archivo o carpeta del sandbox a la papelera recuperable | Siempre exige aprobación local; nunca acepta la raíz del sandbox |
| `screenshot` | Captura y optimiza la pantalla: `{display?, format?, quality?, max_width?}` → `{image_b64, width, height, mime, origin_x, origin_y}` | macOS nativo; Windows/Linux con el extra `remote-control`; respeta los permisos de captura del sistema |

Las tres primeras son nuevas de este WP; la terminal del IDE (`POST /v1/ide/run`) y abrir/guardar archivo (`GET`/`PUT /v1/ide/file`) reutilizan `run_command`/`read_file`/`write_file`, que ya existían desde v1.

## Endpoints (`/v1/ide/*`, Bearer + flag `companion.ide`)

Implementación real en `apps/api/edecan_api/routers/ide.py` (docstring de módulo con el detalle completo); resumen operativo:

| Ruta | Acción del companion | Notas |
|---|---|---|
| `GET /status` | — (`ConnectionManager.is_connected`) | `{"connected": bool}`; nunca falla por "no conectado", es la propia respuesta |
| `GET /tree?path=&max_depth=&max_entries=` | `list_tree` | `path` default: raíz del sandbox |
| `GET /file?path=` | `read_file` | `path` obligatorio |
| `PUT /file` `{path, content}` | `write_file` | reemplaza el archivo completo (crea carpetas padre si hacen falta) |
| `POST /edit` `{path, old_string, new_string, replace_all?}` | `apply_edit` | edición quirúrgica, no reescritura completa |
| `POST /run` `{command}` | `run_command` | responde `{stdout, stderr, exit_code, truncated}` — nota: el companion internamente usa `returncode`, este endpoint lo traduce a `exit_code` |
| `POST /search` `{query, path?}` | `search_files` | `path` opcional (default: todo el sandbox) |

Mapeo de errores, igual en los 7 endpoints:

| Situación | HTTP |
|---|---|
| Sin JWT válido | `401` |
| Plan sin el flag `companion.ide` | `403` |
| Sin companion conectado | `503` |
| Companion conectado pero no respondió a tiempo | `504` |
| El companion respondió `{"ok": false, "error": ...}` (validación, sandbox, rechazo del usuario) | `422` con ese mismo mensaje |
| Body/query mal formado (p. ej. `old_string` vacío, falta `path`) | `422` de validación de FastAPI — ni siquiera llega a llamar al companion |

## Página web — `/app/ide`

`apps/web/src/app/(app)/app/ide/page.tsx` (componentes en `apps/web/src/components/ide/`, fetchers en `apps/web/src/lib/api-ide.ts`):

- **Tabs "Editor" / "Archivos".** "Editor" muestra el árbol lateral (compacto) más el editor central; "Archivos" muestra el árbol y la búsqueda uno al lado del otro, a mayor tamaño — cualquiera de los dos abre un archivo en el editor y cambia a la pestaña "Editor".
- **Árbol de archivos** (`FileTree.tsx`): carga perezosa, carpeta por carpeta (`GET /tree?path=...&max_depth=1` cada vez que se expande una carpeta) — nunca trae el sandbox completo de una sola vez.
- **Editor** (`CodeEditor.tsx`): textarea monoespaciada con una columna de números de línea aparte, sincronizada por scroll. Indicador de "cambios sin guardar" (punto ámbar) + botón **Guardar** (`PUT /file`), deshabilitado si no hay cambios. **Sin resaltado de sintaxis** — queda como mejora futura para mantener la implementación actual sin dependencias npm adicionales.
- **Terminal** (`Terminal.tsx`): panel fijo abajo, siempre visible en las dos pestañas. Input de comando + historial *append-only* mostrando `$ comando`, `stdout`/`stderr` y el código de salida (`POST /run`).
- **Búsqueda** (`SearchPanel.tsx`): caja de texto + lista de resultados clicables (`ruta:línea` + fragmento) que abren el archivo correspondiente en el editor.

Todo en español, cero dependencias npm nuevas, y `lib/api.ts` compartido no se tocó — `lib/api-ide.ts` replica localmente su mismo patrón de autenticación (Bearer + reintento tras refrescar en 401), igual que ya hizo `lib/api-remoto.ts` (fase v2).

## Seguridad

- **Sandbox de archivos.** Las cuatro acciones nuevas respetan el mismo `sandbox_dir` de siempre. `list_tree`/`search_files` además rechazan **recorrer o leer** (aunque sí pueden llegar a *listar el nombre* de, igual que ya hacía `read_dir`) un enlace simbólico que resuelva fuera del sandbox — no solo cuando la ruta pedida explícitamente escapa, sino también cuando el escape aparece a mitad de un recorrido recursivo.
- **Aprobación y procedencia.** El companion WebSocket conserva la aprobación interactiva por acción. La app instalada no abre una segunda terminal escondida: el bridge aprueba solo las seis acciones que exponen las rutas IDE autenticadas (`list_tree`, `search_files`, `apply_edit`, `read_file`, `write_file`, `run_command`). Sandbox, `ide_enabled`, allowlist de comandos y auditoría siguen aplicando; abrir apps, portapapeles y futuras acciones no se habilitan por accidente.
- **`run_command` (la terminal del IDE) nunca usa shell.** Nada de `;`, `&&`, tuberías o expansión de variables — el usuario decide exactamente qué ejecutables permite en `allowed_commands`, sin excepciones de fábrica.
- **Escritura atómica en `apply_edit`.** Se escribe a un archivo temporal en la misma carpeta y se hace `rename` — nunca queda un archivo a medio escribir si algo falla a mitad de camino (disco lleno, permisos, el proceso se interrumpe).
- **`screenshot` se apoya en el permiso nativo, nunca lo evade.** En macOS usa Grabación de Pantalla; Windows/Linux usan el backend `mss` y la sesión gráfica disponible.
- **Nunca se loguea contenido.** Ni la bitácora de auditoría del companion (`~/.edecan/companion.log`) ni los logs de la API guardan el contenido de archivos/comandos — solo tamaños, acción, aprobación y resultado (`ok`/`error`).
- **`ide_enabled: false`** es el interruptor de emergencia: corta las acciones del IDE antes de siquiera preguntar, sin tener que vaciar `allowed_commands`/`allowed_apps` si esas sí las quieres seguir usando para otra cosa.

## Limitaciones conocidas

- **Sin resaltado de sintaxis** en el editor (textarea monoespaciada lisa) — decisión deliberada de este WP, no un olvido (ver "Página web" arriba).
- **Un solo proceso `uvicorn`.** `ConnectionManager` (`companion_manager.py`) es un mapa en memoria del proceso — un despliegue con varios *workers* necesitaría un backend compartido (p. ej. pub/sub de Redis) para enrutar el comando al proceso que tiene el WebSocket del companion. Misma limitación ya documentada para el resto del companion desde v1.
- **Captura en Linux depende de la sesión gráfica.** X11/Wayland y las políticas del escritorio pueden exigir configuración adicional; el error conserva una indicación accionable.
- **La terminal del IDE no es una shell interactiva.** Cada comando es una llamada nueva a `run_command` (sin estado entre comandos más allá de que `cwd` siempre es `sandbox_dir`) — no hay variables de entorno persistentes, ni un proceso de larga duración (un servidor de desarrollo, por ejemplo) que siga vivo entre una llamada y la siguiente.

## Referencias cruzadas

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) §10.7 (`ToolContext.extras["companion"]`), §10.12 (`/v1/companion/*`).
- [`roadmap.md`](./roadmap.md) — estado público y próximas prioridades del IDE.
- [`api.md`](./api.md) sección "Companion de escritorio" — emparejamiento (`POST /v1/companion/pair-code`, `WS /v1/companion/ws`).
- [`control-remoto.md`](./control-remoto.md) — reutiliza directamente la acción `screenshot` de este documento para su prototipo de vista remota (fase v2); ver su "Contrato de degradación con el companion".
- `apps/companion/README.md` — instalación, configuración completa y advertencias de seguridad del companion.
- `apps/companion/edecan_companion/actions.py` — implementación real de las 4 acciones nuevas, extensamente documentada en cada docstring.
- `apps/api/edecan_api/routers/ide.py` — implementación real de los 7 endpoints, con el mapeo de errores completo en su docstring de módulo.
