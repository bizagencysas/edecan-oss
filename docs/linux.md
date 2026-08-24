# Edecán en Linux — estado real del companion y el IDE (1-ago-2026)

Windows tiene cuatro documentos (`edecan-windows.md`, `windows.md`, `windows-estado.md`,
`opencode-windows.md`); Linux no tenía ninguno hasta este. Todo lo de abajo se **midió en
vivo** contra un servidor real (Ubuntu 26.04, escritorio XFCE sobre **X11** vía xrdp, EC2
`i-07e64b34caa93a0db`), no se dedujo leyendo el código — incluida una segunda pasada de
verificación escéptica, hecha por otra persona, que corrió el código YA arreglado contra
ese mismo servidor y encontró varios hallazgos nuevos (ver §3). El manual completo de ese
servidor vive en `Edecan-Nuevo/servidor-edecan-prod.md` (fuera de este repo).

Regla de lectura, igual que en Windows: "escrito y probado con tests" no es lo mismo que
"confirmado en la máquina real". Cada fila de la tabla de §1 dice cuál de las dos cosas es,
y §3 lista lo que se midió roto y sigue sin resolverse.

## 0. La sesión gráfica de referencia es X11, no Wayland

El servidor de referencia corre **xrdp + XFCE sobre Xorg** (display `:10`), NO Wayland.
Todo lo de abajo se verificó contra X11. Wayland (`wl-clipboard`) se soporta en el código
(detectado por `$WAYLAND_DISPLAY`) pero **nunca se ha probado en vivo** contra una sesión
Wayland real — si tu escritorio es Wayland, trátalo como "escrito y razonado", no como
"confirmado".

## 1. Qué se arregló, medido en vivo

| # | Acción/pieza | Qué estaba mal (medido en vivo) | Qué se cambió |
|---|---|---|---|
| 1 | `open_app` | `xdg-open <nombre-de-app>` espera un archivo/URL, no un nombre de app: abría un diálogo `exo-open` que se quedaba colgado el timeout completo (15s) Y dejaba una ventana huérfana en el escritorio en CADA intento. | `actions.py`: resuelve el `.desktop` él mismo (busca en `XDG_DATA_DIRS` + una lista fija que SÍ incluye snap/flatpak) y ejecuta su `Exec=` directo con `Popen` sin esperar. |
| 1b | `open_app` reportaba éxito con procesos muertos | El `Popen` fire-and-forget del punto anterior no comprobaba nada: con `Exec=/bin/false`, o con un `$DISPLAY` inválido, el proceso moría al instante y la acción igual devolvía `launched: True` — un fallo en silencio de manual, medido en la segunda pasada de verificación. | Tras lanzar, se espera una ventana corta (`LINUX_OPEN_APP_POLL_SECONDS = 0.3s`) y se hace `proc.poll()`; si ya murió con código != 0, se falla con el detalle de `stderr` (capturado a un archivo temporal, nunca a un `PIPE` — ver el punto 2 de abajo). No es una garantía (nada impide que falle medio segundo después de la ventana), pero atrapa el caso medido. |
| 2 | `clipboard_set` | Con `xclip` instalado, el texto SÍ se copiaba pero `subprocess.run` se quedaba esperando el timeout completo igual: `xclip` se demoniza (fork, no exec) y hereda las tuberías de `capture_output=True`, que nunca ven EOF. | `_clipboard_set` en Linux ya no usa `PIPE` para stdout/stderr: stdout a `/dev/null`, stderr a un archivo temporal real. |
| 2b | Portapapeles en Wayland | No había rama para `wl-clipboard` — solo `xclip` (X11). | Se agregó `_linux_clipboard_argv`, que elige `wl-copy`/`wl-paste` si `$WAYLAND_DISPLAY` está puesta. **No probado en vivo** (el servidor de referencia es X11). |
| 2c | Portapapeles sin `$DISPLAY` heredado (systemd) | La primera versión de este arreglo NO quedó cableada al descubrimiento de sesión (§ fila 3+4): `_clipboard_get`/`_clipboard_set` llamaban a `subprocess.run` sin `env=`, y `_linux_clipboard_argv` decidía X11-vs-Wayland leyendo `os.environ` directo. Medido en la segunda pasada: en el MISMO proceso donde `descubrir_variables_de_sesion()` devolvía `DISPLAY=:10.0`, el portapapeles fallaba con `Can't open display: (null)` en las dos direcciones — justo la condición para la que existe `linux_session.py`. | `_clipboard_get`/`_clipboard_set` calculan `linux_session.entorno_fusionado()` una vez y lo usan TANTO para elegir la herramienta (`_linux_clipboard_argv(env=...)`) COMO para el `env=` del `subprocess.run`. |
| 3 | `screenshot` sin `$DISPLAY` | `mss` (backend xcb en esta versión) lanza `mss.linux.xcbhelpers.XError`, que hereda DIRECTO de `Exception` — el `except (OSError, RuntimeError, ValueError)` de antes no lo atrapaba, y el traceback crudo de `mss` llegaba hasta el agente. | El `except` ahora captura `Exception` (tras re-lanzar `ActionError` sin tocar) y da el mensaje con la pista de siempre. |
| 4 | Teclado/mouse remoto sin `$DISPLAY` | `pynput` SÍ estaba instalado, pero en Linux, al no poder conectarse a X11, levanta `ImportError` (así lo diseñó la propia librería) — el mensaje decía "instala el extra 'remote-control'", que ya estaba instalado: instrucción falsa. | Se distingue "el paquete no está" (mensaje de instalación) de "el paquete está pero no hay `$DISPLAY`" (mensaje sobre la sesión gráfica), usando `importlib.util.find_spec`. |
| 4b | Mensaje de pynput afirmaba de más | Con `$DISPLAY=:99` (puesta pero inválida) el mensaje decía "no la tiene ni pudo descubrirla" — falso, sí la tenía, solo que no respondía. Medido en la segunda pasada. | El mensaje ahora distingue "no hay `$DISPLAY` puesta ni descubierta" de "`$DISPLAY` puesta (cita el valor) pero no responde". |
| 3+4 | Descubrimiento de sesión gráfica | El companion corre como servicio systemd, sin `$DISPLAY`/`$WAYLAND_DISPLAY`/`$DBUS_SESSION_BUS_ADDRESS` heredados, AUNQUE la sesión gráfica sí esté corriendo (`who` no la ve; `pgrep -x Xorg` sí). | Módulo nuevo `linux_session.py`: busca esas variables en `/proc/<pid>/environ` de los procesos de sesión gráfica del MISMO usuario y las rellena con `setdefault` (nunca pisa lo explícito). Usado por `screenshot`, teclado/mouse, `open_app`, el selector de carpetas, el portapapeles (2c) y el navegador del IDE (9b). |
| 5 | Selector de carpetas sin DBUS | `zenity` sí estaba instalado, pero sin `$DBUS_SESSION_BUS_ADDRESS` abre una ventana 1×1 invisible que nunca responde — el selector se quedaba colgado los 5 minutos completos de `PICKER_TIMEOUT_SECONDS` antes de decir "agotó el tiempo de espera", sin explicar por qué. | `pick_workspace_folder` usa `linux_session.entorno_fusionado()` para el subprocess, y si sigue sin `$DISPLAY`/`$WAYLAND_DISPLAY` tras eso, falla RÁPIDO con un mensaje claro en vez de esperar 5 minutos. |
| 7 | Pantalla de preparación vacía | `detectar()` devolvía `[]` en Linux sin haber comprobado nada — se lee igual que "todo bien" sin serlo. | `preparacion.py`: manifiesto `REQUISITOS_LINUX`, detección REAL (selector de carpetas, portapapeles, extra `remote-control`, binario de `opencode`), pero **sin instalación automática** — esa sigue siendo exclusiva de Windows por la regla de seguridad del módulo. |
| 8 | Mensajes de linters faltantes | `swiftlint`/`ktlint`/`golangci-lint` decían `Instálalo con \`brew install X\`.` — confirmado en vivo que `brew` no existe en el servidor. | `ide_lint.py`: en Linux, esos tres dan la instrucción real de esa plataforma (compilar con Swift, script oficial de ktlint citando que necesita Java + cómo instalarla, `go install` para golangci-lint). |
| 9 | Chromium sin arrancar (Playwright) | Con Playwright instalado pero Chromium sin descargar (o `headless=False` sin `$DISPLAY`), `chromium.launch()` dejaba escapar la excepción cruda de Playwright. | `ide_navegador.py`: el `launch()` está envuelto; el fallo se convierte en `NavegadorNoDisponibleError` con las dos causas posibles y su comando de arreglo. |
| 9b | Chromium no usaba la sesión descubierta | Sin `env=`, Playwright arranca Chromium con el `os.environ` TAL CUAL del proceso companion — `headless=False` fallaba aunque la sesión gráfica SÍ fuera descubrible desde ese mismo proceso vía `linux_session.py` (medido en la segunda pasada: mismo síntoma que el portapapeles, 2c). | `_motor_playwright` pasa `env=linux_session.entorno_fusionado()` a `chromium.launch()` en Linux. |
| 10 | Terminal cae a `dash` | Sin `$SHELL` (systemd sin sesión de login), caía a `/bin/zsh` si existía, si no a `/bin/sh` (dash en Debian/Ubuntu): sin historial, sin autocompletado, y una secuencia de escape que dash no entiende deja basura pegada al siguiente comando. | `ide_sessions.py`: el orden de respaldo ahora es zsh → bash → `/usr/bin/bash` → `/bin/sh`. |
| — | `BinarioOpencodeNoEncontrado` sin capturar | `/lsp/status` y `/lsp/symbols` sin `opencode` resoluble dejaban escapar esa excepción entera — 500 crudo en la capa HTTP en vez del `{"ok": false, ...}` de siempre. | `ide_runtime.py::execute_ide_action` la captura junto con el resto de errores esperables. |
| — | `CredencialesCloudflareFaltantesError` (y las otras dos de config) sin capturar | La segunda pasada midió que esta excepción es la MISMA familia de bug que la anterior, y se dispara ANTES en el camino real: `generar_opencode_json` corre antes de resolver el binario, así que en una instalación Linux nueva (sin binario Y sin credenciales, el caso típico) el que de verdad se recorre es este, y escapaba crudo igual. | `execute_ide_action` ahora también captura `CredencialesCloudflareFaltantesError`, `CredencialCloudflareInvalidaError` y `ErrorConfiguracionOpencode` (las tres de `ide_opencode_config.py`). |
| — | Mensajes de instalación de `remote-control` citaban un paquete que no existe | `pip install 'edecan-companion[remote-control]'` NO se puede ejecutar: `edecan-companion` no está publicado en ningún índice (medido: `uv pip install --dry-run` da "no solution found"). Aparecía en tres mensajes de error distintos (`screenshot`, teclado/mouse, y la pantalla de preparación). | Los tres mensajes ahora dan el comando que sí funciona (medido): `pip install 'mss>=10.0' 'pynput>=1.7.7' 'Pillow>=10.4'`, con la alternativa de instalar editable desde el repo. |
| — | Plantilla de `companion.yaml` desactualizada | El comentario sobre `open_app` seguía citando `xdg-open` — instrucción falsa desde que el punto 1 se corrigió, misma familia que el `brew install` del punto 8. | `config.py`: el comentario ahora describe la resolución real por `.desktop`/`Name=`. |

## 2. Lo que YA estaba bien en Linux y no se tocó (verificado en vivo, no en teoría)

- `_argv_para_windows` devuelve el argv intacto fuera de `win32`.
- `_pid_vivo_windows`/`_restringir_windows` están detrás de `os.name == "nt"`.
- `pty_compat.py` importa `fcntl`/`pty`/`termios` en la rama no-`nt` — la terminal del IDE
  corre de verdad en Linux (medido: `readlink /proc/$$/exe` da la shell resuelta, y la
  terminal remota escribe y lee comandos reales, con historial, contra un `bash` de
  verdad).
- `platform_paths.cache_dir()` respeta `XDG_CACHE_HOME`.
- El empaquetado Linux existe (AppImage/deb/rpm).
- `personal_apps.py` (Mail/Mensajes/Contactos, AppleScript) es exclusivo de macOS **a
  propósito** — no es una rotura, es una función ausente por diseño, documentada como tal.
- El binario de `opencode` para Linux (`download-opencode.sh`) descarga, verifica SHA y
  corre de verdad; el motor del agente edita archivos reales sobre un workspace de Linux,
  arranca worktrees y checkpoints reales, y **166/166 tests de `ide_opencode*` pasan** una
  vez que el binario está resoluble (`EDECAN_OPENCODE_BIN` o en PATH).
- La sandbox de archivos del IDE (traversal, symlinks, workspace inválido) está probada y
  bloqueada en Linux igual que en las demás plataformas.
- Playwright arranca Chromium **sin** `playwright install-deps` en Ubuntu 26.04 (todas las
  librerías que pide ya las trae Firefox/VS Code/GNOME instalados).

## 3. Lo que sigue sin resolverse (medido, no arreglado, y por qué)

- **En el servidor de referencia, el descubrimiento de sesión no puede ayudar todavía.**
  El servicio `edecan` corre como usuario `edecan` (uid 997) y el escritorio pertenece a
  `ubuntu` (uid 1000). `linux_session._pids_de_sesion` filtra por UID propio a propósito
  (por seguridad — leer `/proc/<pid>/environ` de otro usuario da `PermissionError` del
  propio kernel, no es una vía nueva de fuga), pero eso significa que en ESE despliegue
  concreto `screenshot`, teclado/mouse, `open_app`, el selector de carpetas y el navegador
  siguen sin sesión mientras el companion corra como un usuario distinto del escritorio.
  Medido: `descubrir_variables_de_sesion()` da `{}` como `edecan` y el entorno completo
  como `ubuntu`. El arreglo de esta ronda solo ayuda cuando companion y escritorio
  comparten usuario — el caso normal de una app de escritorio, no el de este servidor de
  pruebas concreto. Corregirlo en ese servidor es un cambio de DESPLIEGUE (correr el
  companion como `ubuntu`, o darle a `edecan` acceso al escritorio), no de código.
- **Credenciales quedaron expuestas en el servidor de pruebas tras una ronda anterior de
  verificación**, medido y NO limpiado por esta ronda (no se tocó el servidor en este
  round; esta es información heredada de la medición previa, dejada aquí para que quien
  tenga acceso la resuelva): `/home/example/pruebas-ide/edecan/.env.bak-r2` con un token
  real de Cloudflare (permisos 0600), y — más urgente — `/tmp/verif_token`, un JWT HS256
  real con permisos **0644 (legible por cualquier usuario de la máquina)**. Hace falta
  borrarlos a mano por SSH; este documento no lo hace por vos.
- **Wayland sin confirmar en vivo.** El código detecta `$WAYLAND_DISPLAY` y usa
  `wl-clipboard`, pero nunca se probó contra una sesión Wayland real — solo contra X11
  (xrdp/XFCE). Si tu escritorio es Wayland, verifica tú mismo antes de confiar en esa rama.
- **`page.screenshot()` en `headless=False`** dio un error de protocolo de Chromium en la
  máquina medida (síntoma de compositor/GPU del propio xrdp, no del companion). El modo
  normal del agente (`headless=True`) no lo sufre.
- **El motor del IDE no se probó de punta a punta con un cambio real en disco.** El
  servidor de referencia no tenía ninguna credencial de IA (`opencode auth list` = 0;
  `secrets.json` de producción solo trae `JWT_SECRET`/`LOCAL_MASTER_KEY`) y esta ronda no
  introdujo ninguna. `ide_agent_start` arranca y falla con el mensaje correcto pidiendo
  `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_TOKEN` — no es un fallo en silencio — pero
  "pedile un cambio real y leé el archivo en disco" sigue sin comprobarse en Linux.
- **`input_key` no espera entre teclas.** Contra una terminal escribe perfecto (36/36
  caracteres medidos), pero contra un diálogo GTK que aparece mientras se escribe (el
  selector de carpetas, por ejemplo) pierde caracteres — medido: de 22 caracteres tecleados
  solo llegaron los últimos 4.
- **Emparejamiento de dispositivo desde un servidor sin `apps/web`.** El flujo
  `edecan://pair` lo emite la web, que un companion standalone sin `EDECAN_WEB_DIR` fijado
  no sirve. Sin emparejar, el IDE del móvil se atasca en silencio en "Autorizar proyecto".
  Es un problema de DESPLIEGUE de ese servidor concreto, no del companion en sí.
- **`ide_lsp_status`/`ide_lsp_symbols` dan un diagnóstico falso antes de que el motor se
  inicialice.** El mensaje "esta instalación todavía corre el motor anterior" aparece en un
  proceso recién arrancado con el motor opencode por defecto, simplemente porque
  `sessions.motor_opencode` es `None` hasta que se arranca un agente (inicialización
  perezosa) — no porque el motor sea de verdad el viejo. Menor, no bloqueante, sin arreglar
  en esta ronda.
- **`ruff check .` sobre TODO el repo no pasa** (25 errores en `scripts/run_t5_bench.py`,
  `apps/api/tests/test_linkedin_atajo_tema.py` y `packages/creative/*`), pero son
  preexistentes y ajenos a este trabajo — confirmado comparando contra un worktree limpio
  de antes de esta ronda: mismos 25 errores, línea por línea. Sobre los archivos que sí se
  tocaron en este trabajo, `ruff check` pasa limpio.

## 4. Paquetes del sistema que este soporte necesita en Linux

Ninguno se instala solo (mismo criterio que Windows con WebView2/PowerShell): hay que
ponerlos a mano. La pantalla de preparación (§1, fila 7) dice cuáles faltan en la máquina
donde corre el companion.

| Para qué | Paquete (Debian/Ubuntu) | Paquete (Fedora) |
|---|---|---|
| Selector de carpetas del IDE | `zenity` (o `kdialog`) | `zenity` |
| Portapapeles (X11) | `xclip` | `xclip` |
| Portapapeles (Wayland) | `wl-clipboard` | `wl-clipboard` |
| Captura de pantalla + teclado/mouse remoto | `pip install 'mss>=10.0' 'pynput>=1.7.7' 'Pillow>=10.4'` (el extra `edecan-companion[remote-control]` NO se puede instalar desde PyPI — el paquete no está publicado en ningún índice; con un clon editable de este repo sí funciona `uv pip install -e 'apps/companion[remote-control]'`) | igual |
| Agente del IDE | binario `opencode` (`scripts/download-opencode.sh` o `EDECAN_OPENCODE_BIN`) | igual |
| Navegador del IDE (opcional) | `uv pip install 'edecan-companion[playwright]'` + `uv run playwright install chromium` (mismo aviso que arriba sobre el índice si no es un clon editable) | igual |
| ktlint (linter Kotlin, opcional) | Java: `sudo apt install default-jre-headless` | `sudo dnf install java-latest-openjdk-headless` |

## 5. Trampas de medición para quien siga

1. **`who` no ve una sesión de xrdp** aunque esté viva — usa `ls /tmp/.X11-unix` +
   `pgrep -x Xorg`.
2. **Un `200 OK` (o `{"ok": true}`) no es una prueba.** Baja el archivo/abre el
   PNG/lee el `stdout` real. Varias de las roturas de esta lista devolvían éxito
   (`returncode 0`, `200`, `launched: true`) mientras fallaban de verdad por dentro — el
   caso de `open_app` con `Popen` (fila 1b) es el ejemplo más reciente: el primer arreglo
   quitó el cuelgue pero introdujo justo este patrón, y hizo falta una SEGUNDA pasada de
   verificación para atraparlo.
3. **`pkill -f <patrón>` por SSH puede matar tu propia sesión** si el patrón coincide con
   tu propia línea de comando — usa `pkill -x`.
4. **Probar sin `$DBUS_SESSION_BUS_ADDRESS` da falsos fallos** en cualquier cosa que abra
   diálogos GTK (zenity incluido).
5. **El UID que corre el companion importa tanto como el `$DISPLAY`.** Si el servicio corre
   como un usuario distinto del dueño de la sesión gráfica, `linux_session.py` no tiene
   nada que descubrir — filtra por UID a propósito (leer el `/proc` de otro usuario ya lo
   bloquea el kernel). Confirma con `systemctl show edecan -p User` y compara contra quién
   es dueño de `Xorg`/`xfce4-session`.
6. **Arreglar un cuelgue no es lo mismo que arreglar un fallo en silencio.** Cambiar
   `subprocess.run(check=True)` por `Popen` fire-and-forget quita el cuelgue, pero si nadie
   comprueba si el proceso sobrevivió, el fallo pasa de "se cuelga" a "miente que
   funcionó" — a veces peor, porque un cuelgue al menos se nota.
7. No pises `/opt/edecan/app` para probar código modificado: copia a una carpeta de
   pruebas del usuario que corresponda y corre desde ahí.
8. Los extras opcionales de `pyproject.toml` (`remote-control`, `playwright`) existen para
   quien clona el repo, pero `pip install 'edecan-companion[extra]'` desde un índice
   público NO resuelve nada — este paquete no está publicado ahí. Si el mensaje de error
   te manda a instalar un extra, comprueba primero si podés instalar los paquetes
   subyacentes directo antes de asumir que el mensaje miente.
