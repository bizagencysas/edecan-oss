# Edecán en Windows — arquitectura y contrato de trabajo

**Estado: documento de gobierno.** Lo que aquí se decide es contrato para todo el trabajo de
soporte Windows. Quien implemente algo que contradiga una decisión de este documento tiene que
cambiar primero este documento, con argumento, no el código en silencio.

**Regla de evidencia (no negociable).** Este repositorio se desarrolla en macOS. Nada de lo que
se escriba aquí o en el código puede afirmarse como "funciona en Windows" hasta que se haya
ejecutado en Windows x64 — en el CI (`windows-2025`/`windows-latest`) o en la PC del dueño. Lo
que se puede hacer desde macOS: escribir código portable, correr la suite POSIX (`uv run
pytest`), razonar contra la documentación de la API de Win32/ConPTY, y dejar comprobaciones
ejecutables. Todo lo demás va a la lista del §9 ("pendiente de comprobar en Windows"). Esta regla
ya está declarada en `apps/desktop/README.md` y `docs/desktop.md` §"Importante sobre validación";
este documento la extiende del empaquetado al runtime completo.

## 0. Punto de partida: qué existe y qué está roto

Existe, y no se reinventa:

- `apps/desktop/src-tauri/tauri.windows.conf.json` — configuración Tauri de Windows.
- La cadena PowerShell completa en `apps/desktop/scripts/`: `build-app.ps1` (backend + `cargo
  tauri build` con CLI fijado 2.11.4), `build-backend.ps1`, `build-studio-engine.ps1`,
  `download-ollama.ps1`, y `verify-windows-bundles.ps1` — este último instala el NSIS en un
  perfil efímero, extrae el MSI administrativamente, arranca la app instalada, espera `/healthz`
  del backend real, cierra la ventana y verifica cero procesos huérfanos.
- CI: `ci.yml` ya tiene el job `desktop-windows` (`windows-2025`) que compila los instaladores y
  corre ese smoke; `release-desktop.yml` ya tiene el job `windows` que produce NSIS + MSI
  firmados para el updater de Tauri y publica junto con macOS/Linux.
- `edecan_local` (el sidecar) arranca en Windows: `runtime.py` ya tolera la ausencia de
  `loop.add_signal_handler` (`NotImplementedError` → pass) y el smoke de CI lo confirma vivo
  hasta `/healthz`.

Roto, o degradado:

- **El terminal del IDE** — `apps/companion/edecan_companion/ide_sessions.py` usa
  `pty.openpty()` (líneas 143/545) en POSIX y, en Windows (`os.name == "nt"`), cae a un respaldo
  de **pipes planos** (`_read_pipes`, líneas 567–583): sin PTY no hay prompt interactivo, ni
  editores de terminal, ni programas que detectan TTY, ni tamaño de ventana. Como el dueño quiere
  Edecán en su PC "específicamente para IDE", esto es EL bloqueo. El resto del archivo
  (sesiones de agente, colas de mensajes, checkpoints, planes) es Python puro y portable.
- **Matar procesos** — `close()` usa `os.killpg` en POSIX y un `process.terminate()`/`kill()` a
  secas en Windows, que mata solo el proceso raíz y deja vivo el árbol (el `npm run dev` que
  arrancó un `node` hijo sigue corriendo).
- Supuestos POSIX dispersos (`/tmp/`, `~/`, `shell=True` — mayormente en docstrings que dicen
  "JAMÁS `shell=True`", pero hay que auditar los usos reales —, `sys.platform`), medidos por
  paquete: `apps/companion` 23 archivos es el epicentro; el resto son 1–5 por paquete.

---

## 1. El terminal: ConPTY vía `pywinpty` — la decisión central

**Decisión: `pywinpty`.** No `winpty` (el proyecto viejo pre-ConPTY: procesos agente extra,
mantenimiento muerto) ni una capa propia de `ctypes` sobre
`CreatePseudoConsole`/`ResizePseudoConsole`/`ClosePseudoConsole` (posible, pero es reimplementar
lo que `pywinpty` ya hace en Rust con tests propios, y el manejo del `STARTUPINFOEX` +
`PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` a mano es exactamente la clase de código que no podemos
depurar sin un Windows delante). `pywinpty` usa ConPTY como backend por defecto en Windows 10
1809+ (nuestro piso de soporte, ver §9) y publica wheels para CPython 3.12 x64.

Dependencia: en `apps/companion/pyproject.toml`,
`pywinpty>=2.0.14; sys_platform == "win32"` — marcador de entorno, jamás se instala en macOS/Linux.

### 1.1 El contrato de la capa de abstracción

Módulo nuevo: `apps/companion/edecan_companion/pty_compat.py`. Es la ÚNICA frontera: después de
este trabajo, `ide_sessions.py` no importa `pty`, ni `pywinpty`, ni contiene un solo
`os.name == "nt"` relacionado con terminales.

```python
class TerminalPTY(Protocol):
    """Una terminal interactiva viva. Una implementación por plataforma."""

    def abrir(argv: list[str], *, cwd: str, env: dict[str, str],
              cols: int = 120, rows: int = 32) -> "TerminalPTY": ...
    # Arranca el proceso conectado a un pseudo-terminal del tamaño dado.
    # POSIX: pty.openpty() + Popen(start_new_session=True) — el código actual, movido.
    # Windows: winpty.PTY(cols, rows) + spawn(). Lanza PTYError (ver abajo) si falla.

    def escribir(data: bytes) -> None: ...
    # Entrada cruda del usuario, UTF-8, incluyendo secuencias de control (\x03, \x1b[A...).
    # No traduce nada: el emulador del frontend manda lo que el usuario tecleó.

    def leer(max_bytes: int) -> bytes: ...
    # Bloqueante; devuelve b"" en EOF (proceso terminó y buffer drenado). El hilo lector
    # de ide_sessions le pasa el resultado al MISMO decoder incremental UTF-8
    # errors="replace" que hoy — eso no cambia por plataforma.

    def redimensionar(cols: int, rows: int) -> None: ...
    # POSIX: fcntl.ioctl(fd, termios.TIOCSWINSZ, ...). Windows: pty.set_size().
    # HOY ide_sessions no redimensiona (no hay TIOCSWINSZ en el archivo); entra al
    # contrato desde el día uno porque agregarlo después obligaría a tocar la frontera.

    def cerrar() -> None: ...
    # Cierre cooperativo: POSIX killpg(SIGTERM), espera 3s, killpg(SIGKILL).
    # Windows: taskkill /T /PID (sin /F), espera 3s, taskkill /T /F /PID. Después
    # libera el PTY (os.close(master_fd) / ClosePseudoConsole vía del del objeto).

    def matar_arbol() -> None: ...
    # El martillo: POSIX killpg(SIGKILL); Windows taskkill /F /T /PID. Idempotente:
    # sobre un proceso ya muerto no lanza.

    @property
    def pid() -> int: ...
    @property
    def codigo_salida() -> int | None: ...  # None mientras vive.
```

`abrir_pty(...)` es la fábrica que elige implementación por `os.name`. Errores: una sola
excepción `PTYError(IDESessionError)` — el llamador no distingue plataformas ni en el camino de
error.

### 1.2 Lo que de verdad duele, decidido

**Señales.** En Windows no existe `SIGINT` entregable a otro proceso. La decisión es NO emular
señales en la capa: el Ctrl+C del usuario viaja como el byte `\x03` por `escribir()`, y es
**ConPTY quien lo traduce** a `CTRL_C_EVENT` para el grupo de consola del hijo — exactamente como
un terminal real. `GenerateConsoleCtrlEvent` no se usa (su semántica de grupos es una trampa: sin
`CREATE_NEW_PROCESS_GROUP` mata al padre también, y con él, el hijo arranca con Ctrl+C
deshabilitado). La cancelación programática (botón "detener") no manda señales: llama `cerrar()`.

**Árbol de procesos.** `os.killpg` no existe. Fase 1: `taskkill /T` (sin `/F` primero: da a las
apps la chance de un cierre limpio vía `WM_CLOSE`/`CTRL_CLOSE_EVENT`; con `/F` a los 3s). Se
invoca con `subprocess.run([...], creationflags=CREATE_NO_WINDOW)` y lista de argumentos, nunca
string. Fase 3 (§4) lo reemplaza por Job Objects con `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, que
además garantiza que un crash del companion no deje huérfanos — la garantía que
`verify-windows-bundles.ps1` ya exige del shell Tauri se extiende así al runtime del IDE.

**Fin de línea.** No se normaliza NADA en la capa. La salida de ConPTY trae `\r\n` y secuencias
VT; el emulador del cliente (que ya consume los eventos `output` como flujo VT) las interpreta,
igual que interpreta la salida de zsh. La entrada: Enter del usuario es `\r` en ambas
plataformas (así lo mandan los emuladores). Normalizar aquí rompería programas que pintan con
`\r` (barras de progreso).

**Codificación.** Contrato: la capa habla **bytes UTF-8 en ambas direcciones**. En Windows,
ConPTY convierte entre UTF-16 interno y la página de códigos de salida del PTY; `pywinpty` opera
en UTF-8. El decoder incremental `errors="replace"` existente absorbe cualquier basura de un
programa que escriba en CP-850/CP-1252 crudo: se ve un `�`, no se cae el hilo lector. En el
`env` del proceso hijo se fija `PYTHONIOENCODING=utf-8` y `PYTHONUTF8=1` (para hijos Python) —
no se toca `chcp` global.

**Shell por defecto.** `_default_terminal_argv()` en Windows resuelve, en orden: `pwsh.exe`
(PowerShell 7) si está en PATH → `powershell.exe` (Windows PowerShell 5.1, siempre presente) →
`%COMSPEC%` (cmd). Con `-NoLogo -NoProfile` — el espejo exacto de `zsh -f`/`bash --noprofile
--norc` que ya hace el código: terminal reproducible, sin el perfil del usuario. `PROMPT`/`PS1`
del entorno actual no aplican a PowerShell; se acepta el prompt por defecto en fase 1.

### 1.3 Cómo se prueba cada implementación en su plataforma

- **Suite de contrato compartida**: `apps/companion/tests/test_pty_compat.py`, parametrizada
  sobre `abrir_pty()`, sin una sola rama por plataforma en los asserts: abrir un `python -c` que
  imprime y lee, verificar eco y EOF, `redimensionar` y comprobar que el hijo ve el tamaño
  (`shutil.get_terminal_size` en el hijo), `escribir(b"\x03")` interrumpe un `time.sleep` hijo,
  `cerrar()` sobre un `python -c` que a su vez lanzó un nieto deja **cero** descendientes vivos
  (en Windows se verifica con `taskkill /PID nieto` fallando por "no existe"; en POSIX con
  `os.kill(pid, 0)` lanzando).
- En macOS/Linux la suite ejercita la implementación POSIX (corre hoy, en cada `uv run pytest`).
- En Windows ejercita la de ConPTY — y **solo el job de CI de Windows (§6) o la PC del dueño
  cuentan como evidencia**. En macOS esos tests se saltan con
  `pytest.mark.skipif(os.name != "nt")` y viceversa; ningún test de ConPTY se mockea para
  "pasar" en macOS: un mock aquí es evidencia falsa.

---

## 2. Rutas, archivos y el sistema de ficheros

**Mayúsculas.** No hay problema nuevo: macOS (APFS por defecto) ya es case-insensitive, igual que
NTFS, así que el repo ya vive bajo esa restricción. Regla: prohibido crear dos rutas que
difieran solo en mayúsculas (el índice semántico y los checkpoints usan la ruta como clave — se
normaliza con `Path.resolve()` antes de usarla como clave de diccionario, nunca `str.lower()`,
que rompería en Linux).

**Longitud 260.** Tres capas, todas obligatorias:
1. El manifest del ejecutable PyInstaller (`edecan_local.spec`) declara `longPathAware` (opción
   de manifest de PyInstaller para `edecan-local.exe`).
2. `edecan doctor` (ver §7, F2) comprueba
   `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled == 1` y lo reporta —
   no lo cambia (requiere admin; se le dice al dueño el comando exacto, ver checklist).
3. Los datos van a rutas cortas por diseño: `%LOCALAPPDATA%` + nombres cortos, la misma lección
   que ya aprendió `verify-windows-bundles.ps1` con su `RUNNER_TEMP` + sufijo de 8 chars
   (líneas 102–110: la extracción MSI moría con 1603 por MAX_PATH). Está prohibido anidar
   estado del companion dentro del workspace del usuario.

**Nombres prohibidos.** `FileService` (`ide_files.py`) gana una validación única
`_validar_nombre_windows(nombre)` aplicada **en todas las plataformas** (para que un archivo
creado desde la Mac no rompa el checkout en la PC): rechaza `< > : " | ? *`, caracteres < 32,
punto o espacio final, y los nombres reservados `CON PRN AUX NUL COM1-9 LPT1-9` (también con
extensión: `nul.txt` es NUL). Mensaje de error que nombra el carácter, no un genérico.

**Separadores.** Todo el repo ya usa `pathlib`; la regla se vuelve contrato: prohibido construir
rutas con `+ "/"` o `str.split("/")` sobre rutas del filesystem (sobre claves S3/URLs sí, eso es
protocolo). Las rutas que viajan por la API del IDE al cliente se serializan SIEMPRE con `/`
(`PurePath.as_posix()`), y se rehidratan con `Path` — el móvil no debe ver `\`.

**Enlaces simbólicos.** Crear symlinks en Windows exige privilegio o Developer Mode. Decisión:
Edecán **no crea** symlinks en Windows (los checkpoints copian contenido, no enlaces) y **sí los
resuelve** al leer (`Path.resolve()` ya lo hace, y la invariante anti-fuga del sandbox de
`config.py`/`actions._resolve_in_sandbox` funciona igual). Un repo del usuario que contenga
symlinks (git en Windows los materializa como archivos de texto sin Developer Mode) se trata
como lo trata git: archivos normales.

**Archivos bloqueados — el que más duele.** En Windows, borrar o renombrar un archivo abierto
por otro proceso falla con `PermissionError` (POSIX lo permite). Nos pega en: `os.replace` de
metadatos (`_save` en `ide_sessions.py`, secretos de `runtime.py`), restauración de checkpoints,
y borrados del agente mientras un `node --watch` del terminal tiene el archivo abierto. Decisión:
helper único `edecan_companion.fs_compat.replace_con_reintentos(src, dst)` — hasta 5 intentos
con backoff exponencial (50ms→800ms) SOLO ante `PermissionError` y SOLO en `os.name == "nt"`;
si agota, propaga con un mensaje que dice *quién* suele ser el culpable ("otro proceso —
¿antivirus, el watcher del dev server?— tiene el archivo abierto"). El indexador antivirus es la
causa #1 de fallos fantasma de este tipo; el mensaje debe mencionarlo. La restauración de
checkpoint reporta conflicto (ya lo hace ante contenido cambiado) en vez de pisar; ahora también
reporta bloqueo en vez de reventar.

**Dónde viven los datos.** Sin cambios de diseño, solo se documenta el mapa: la app Tauri pasa
`--data-dir` bajo el directorio de datos de la app (en Windows resuelve a
`%LOCALAPPDATA%\com.edecan.desktop` vía `app_data_dir`), y el smoke de CI ya lo aísla
redefiniendo `%LOCALAPPDATA%`. El CLI `edecan` a secas mantiene su default `~/.edecan/data` en
TODAS las plataformas (`Path.home()` en Windows es `C:\Users\<u>`) — consistencia entre
plataformas vale más que idiomaticidad de cada una. `~/.edecan/companion.yaml` ídem.
`chmod 0600/0700` en Windows es esencialmente un no-op: se ACEPTA en fase 1 — los datos quedan
bajo el perfil del usuario, cuyas ACL ya excluyen a otros usuarios; no se intenta `icacls`
(frágil, y el modelo de amenaza local es un solo dueño en su PC). Queda anotado en §9 como
decisión, no como olvido.

**`/tmp/` y `os.uname`.** Todo uso de `/tmp` migra a `tempfile.gettempdir()`/
`TemporaryDirectory`; `os.uname()` a `platform.uname()`. Son mecánicos; van en la fase 0.

---

## 3. Procesos y shell

**`shell=True`.** La norma del repo ya es "JAMÁS `shell=True`" (así lo declaran los docstrings de
`edecan_mcp.transport`, `edecan_llm.claude_cli`, `edecan_creative.podcast`); la fase 0 audita
los 9 archivos detectados y elimina cualquier uso real que quede. En Windows `shell=True`
significa `cmd.exe /c` con sus metacaracteres (`& | ^ %`) — es inyección esperando argumento.

**`.bat`/`.cmd` — la trampa fina.** `CreateProcess` ejecuta `.bat`/`.cmd` enrutando por
`cmd.exe`, lo que reintroduce metacaracteres AUNQUE se use lista de argumentos (la clase de bug
"BatBadBut"). Regla: **prohibido invocar `.bat`/`.cmd` con argumentos que contengan datos del
usuario o del modelo**. Para npm: se invoca `node.exe` directo o el `fydesign-node.exe`
empaquetado; si un flujo necesita `npm.cmd`, sus argumentos se validan contra una allowlist. La
validación de `argv` existente en `_validate_argv` gana esa regla: si `Path(argv[0]).suffix in
{".bat", ".cmd"}` y algún argumento contiene `& | ^ % < > "`, se rechaza.

**`shutil.which`.** En Windows ya consulta `PATHEXT` (encuentra `git.exe` por `which("git")`).
El bug real es el inverso: código que busca `which("algo")` y luego asume que el resultado es
ejecutable directo — cierto para `.exe`, falso para `.cmd` (párrafo anterior). Regla: tras
`which`, mirar el sufijo.

**Variables de entorno.** `os.environ` en Windows es case-insensitive (CPython lo maneja), pero
los dicts que NOSOTROS construimos no: `_build_env` de `runtime.py` y el `env` de
`start_terminal` copian `os.environ` (bien) — la regla es nunca hacer `env["PATH"]` sobre un
dict propio sin pasar por `os.environ` primero, y nunca crear un `env` desde cero para un
subproceso en Windows (sin `SystemRoot`, medio Win32 falla de formas crípticas).

**Señales del runner.** Ya resuelto en `runtime.py` (el `NotImplementedError` → pass). El
apagado en Windows llega por el cierre del proceso desde Tauri (`backend.rs`), no por SIGTERM;
el smoke de CI verifica exactamente ese camino. No se toca.

---

## 4. El sandbox: la verdad primero, luego la decisión

Verdad verificada en el código: **hoy no existe `sandbox-exec` en ninguna parte del repo.** El
"sandbox" real de Edecán es confinamiento por rutas en Python — `sandbox_dir` en
`companion.yaml`, con la invariante de rutas absolutas resueltas de `config.py` y la detección
de fugas por symlink en `actions._resolve_in_sandbox`. Eso es 100% portable: la misma invariante
funciona sobre NTFS con `Path.resolve()`.

**Decisión en tres tiempos:**

1. **Fase 1: Windows corre con el mismo confinamiento por rutas y sin sandbox de SO.** Es
   exactamente la postura ya aceptada para la Mac del dueño (un solo usuario, su propia máquina,
   el agente es su herramienta, no un inquilino hostil). Declararlo explícito vale más que un
   sandbox de mentira.
2. **Fase 3: Job Objects** — no como sandbox de seguridad sino como **contención de ciclo de
   vida**: cada terminal/agente entra a un Job con `KILL_ON_JOB_CLOSE` (+ límites opcionales de
   memoria/procesos activos). Resuelve huérfanos ante crash del companion y da el `matar_arbol`
   atómico que `taskkill` solo aproxima. Es ~40 líneas de `ctypes`
   (`CreateJobObject`/`SetInformationJobObject`/`AssignProcessToJobObject`) y se prueba con la
   misma suite de contrato del §1.3.
3. **Rechazados con argumento**: **AppContainer** — rompe el caso de uso (un IDE cuyo terminal
   no puede escribir el repo del usuario ni abrir sockets de dev servers no sirve; el perfil de
   permisos necesario lo degradaría a teatro). **WSL2** — sería "Edecán para Linux corriendo
   junto a Windows": dependencia pesada, otra distro que mantener, y el terminal viviría en un
   filesystem distinto al del repo del usuario en NTFS (9p es lento y frágil para watchers).
   Contradice el encargo: el dueño quiere Edecán EN su PC.

---

## 5. Empaquetado y firma

**Hecho** (ver §0): cadena `.ps1` completa, `tauri.windows.conf.json`, job de release que
produce NSIS + MSI con artefactos de updater firmados (minisign de Tauri —
`TAURI_SIGNING_PRIVATE_KEY`, que `build-app.ps1` sabe cargar desde
`TAURI_SIGNING_PRIVATE_KEY_PATH`), verificación de payload archivo por archivo, smoke con
backend real y chequeo de huérfanos.

**Decisión NSIS vs MSI: NSIS es el instalador canónico para personas** (`Edecán-Setup.exe`,
"siguiente, siguiente", per-user sin UAC, y es el formato con el que el updater de Tauri trabaja
mejor). El MSI se conserva porque ya existe, el CI lo verifica, y es lo que pediría un entorno
administrado — pero la documentación de usuario apunta al NSIS. No se invierte esfuerzo nuevo en
el MSI más allá de mantenerlo verde.

**Firma Authenticode — lo que falta.** La firma del updater (minisign) NO es firma de código
Windows: SmartScreen y el "Editor desconocido" se resuelven solo con Authenticode. Decisión:
**Azure Trusted Signing** (certificado EV-equivalente gestionado, ~10 USD/mes, integración
directa con `signtool`, sin llave en el runner) sobre un certificado OV en archivo (la llave
viviría en secretos de GitHub: peor). Pipeline: paso post-build en el job `windows` de release
que firma `edecan-desktop.exe`, `edecan-local.exe`, `fydesign-node.exe`, el NSIS y el MSI con
`signtool sign /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256` — **con sellado de
tiempo siempre**: sin él, los binarios mueren cuando expire el certificado.

**La verdad sobre SmartScreen.** No hay equivalente de la notarización de Apple: Microsoft no
"aprueba" binarios por adelantado. Con firma OV/Trusted Signing, SmartScreen igual marcará
"desconocido" las primeras semanas hasta acumular reputación por telemetría de instalaciones.
Con certificado EV clásico la reputación suele ser inmediata, pero exige hardware token o HSM.
Decisión: aceptar el período de reputación; mientras tanto `docs/desktop.md` ya documenta el
camino "Más información → Ejecutar de todas formas" y así se queda, con la firma reduciendo el
riesgo real (el hash firmado es verificable) aunque no el aviso.

---

## 6. Integración continua: probar Windows sin tener un Windows

Ya existen los dos jobs de instaladores (§0). **Lo que falta es el job que protege el runtime**
— hoy `uv run pytest` solo corre en Linux/macOS, así que una regresión Windows en
`pty_compat.py` sería invisible hasta el release. Se añade a `ci.yml`:

```yaml
  tests-windows:
    name: Python suite on Windows
    runs-on: windows-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@<sha fijado, como los demás jobs>
      - uses: astral-sh/setup-uv@<sha fijado>
        with: { python-version: "3.12" }
      - run: uv sync --all-packages
      - run: uv run pytest apps/companion apps/local packages/toolkit packages/schemas
        env: { EDECAN_TEST_PLATFORM: windows }
```

Alcance inicial deliberadamente acotado (companion + local + los paquetes con supuestos POSIX
detectados); se amplía paquete a paquete a medida que la fase 0 los limpia, hasta llegar al
workspace completo. Los tests que dependen de POSIX real (los de la implementación `pty` Unix,
`os.killpg`) llevan `skipif`; **cada skip nuevo se anota con el motivo**, porque un skip sin
motivo es un hueco escondido. Este job es **gate obligatorio** de cualquier PR que toque
`pty_compat.py`, `fs_compat.py` o `ide_sessions.py`.

**Lo que este CI NO puede cubrir, dicho sin maquillaje:** SmartScreen y la experiencia de primer
arranque en una máquina limpia (el runner ya tiene WebView2 y sin telemetría de reputación);
antivirus de terceros bloqueando el sidecar PyInstaller (falso positivo clásico); GPU/audio
reales; Ollama con modelos reales (pesos de GB); el emparejamiento móvil por QR en una LAN real;
sesiones interactivas largas de terminal con un humano tecleando; y el comportamiento con OneDrive
sincronizando `%USERPROFILE%` (archivos bloqueados por el cliente de sync — pariente del §2).
Todo eso solo lo cubre la checklist del §10 en la PC del dueño.

---

## 7. El orden del trabajo

Cada fase termina con un criterio de aceptación **ejecutable en Windows** y su prueba exigida.
Nada de una fase posterior arranca hasta que la anterior tenga su evidencia.

**F0 — Piso portable (sin UI nueva).** Limpieza mecánica: `/tmp` → `tempfile`, `os.uname` →
`platform`, auditoría de los 9 `shell=True`, `_validar_nombre_windows` en `FileService`,
`fs_compat.replace_con_reintentos`, y el job `tests-windows` en verde con su inventario de skips
anotado. *Aceptación:* el job `tests-windows` pasa en CI; los `desktop-windows` y `windows`
existentes siguen verdes. *Prueba exigida:* CI (esta fase no necesita la PC).

**F1 — El IDE usable en la PC del dueño (la fase que importa).** `pty_compat.py` con las dos
implementaciones, `ide_sessions.py` migrado a la frontera (cero `pty`/`os.name` de terminal),
`cerrar()`/`matar_arbol()` con `taskkill /T`, shell por defecto PowerShell, suite de contrato
del §1.3 pasando en el job Windows. *Aceptación:* en la PC, con el NSIS instalado, el dueño abre
el IDE, crea un terminal, corre `git status` y `npm run dev` de un repo suyo, interrumpe con
Ctrl+C, cierra la sesión y no queda ningún `node.exe` vivo; y una sesión de agente edita un
archivo y su checkpoint lo restaura. *Prueba exigida:* checklist §10 pasos 1–7 ejecutada por el
dueño; CI de contrato en verde.

**F2 — Robustez y diagnóstico.** `edecan doctor` (subcomando del CLI): reporta versión de
Windows ≥ 1809, `LongPathsEnabled`, presencia de `pwsh`/WebView2, escritura en el data-dir, y
prueba un ConPTY de humo. Reintentos ante archivos bloqueados integrados a checkpoints y
`_save`. Mensajes de error que nombran antivirus/sync como sospechosos. *Aceptación:* `edecan
doctor` en la PC imprime todo OK o instrucciones concretas; matar el companion a mitad de un
`npm run dev` y reabrirlo deja la sesión "interrupted" sin huérfanos (checklist paso 8).

**F3 — Contención y firma.** Job Objects (`KILL_ON_JOB_CLOSE`) reemplazando a `taskkill` como
mecanismo primario; Authenticode con Trusted Signing + timestamping en el release. *Aceptación:*
el test de contrato "crash del companion ⇒ cero descendientes" pasa en CI Windows;
`Get-AuthenticodeSignature` sobre el NSIS descargado devuelve `Valid` en la PC (checklist paso 9).

**Explícitamente fuera de todas las fases:** paridad del control remoto de escritorio
(captura/accesibilidad estilo macOS) — ver §8.

## 8. Lo que se acepta que NO funcionará en Windows

Lista honesta; cada punto es una decisión, no un descuido.

1. **Captura de pantalla y control del escritorio del companion** (el flujo
   `_macos_permission_status`/`_macos_capture_check` y la maquinaria TCC): es CoreGraphics/
   ApplicationServices por `ctypes`. En Windows devuelve el default "sin permisos que declarar";
   el "Centro de permisos" ya lo documenta con alcance reducido (`docs/desktop.md` §2). El
   control remoto de la PC es un proyecto aparte, no parte de este encargo.
2. **Sandbox de SO para el agente**: fase 1 corre con confinamiento por rutas solamente (§4),
   igual que en la Mac del dueño. Job Objects (F3) contiene ciclo de vida, no es una frontera de
   seguridad contra un agente hostil.
3. **Permisos POSIX reales** (`0600`/`0700`): en Windows son no-ops; la protección es el perfil
   de usuario NTFS (§2).
4. **Symlinks creados por Edecán**: no se crean en Windows (§2).
5. **`Abrir Edecán.command`** y todo el flujo de instalación desde repo sin instalador: solo
   macOS. En Windows la única vía soportada es el NSIS.
6. **Prompt personalizado del terminal** (`PS1="› "`): PowerShell ignora `PS1`; fase 1 muestra
   el prompt normal de PowerShell.
7. **SmartScreen sin fricción desde el día uno**: imposible sin reputación acumulada (§5); se
   documenta el paso extra.
8. **Reputación de evidencia macOS**: nada probado solo en macOS se declara funcionando en
   Windows — que es la regla que gobierna este documento entero.

## 9. Piso de soporte

Windows 10 x64 versión 1809+ (requisito de ConPTY) — en la práctica, Windows 10 22H2 o
Windows 11. Solo x64 (decisión ya tomada por `build-app.ps1`, que rechaza no-x64; ARM64 queda
fuera). WebView2 Runtime: presente de fábrica en Windows 11 y en Windows 10 actualizado; el
instalador NSIS de Tauri lo instala si falta (comportamiento por defecto de Tauri v2 —
**pendiente de comprobar en una máquina limpia**, ver checklist).

## 10. Lista de comprobación para la PC del dueño

En orden. Cada paso dice qué ejecutar y qué se debe ver. Si un paso falla, se reporta el paso y
el texto exacto — eso es evidencia de la lista "pendiente de comprobar", no un misterio.

1. **Preparar la máquina (una vez, PowerShell como administrador):**
   ```powershell
   winget install Microsoft.PowerShell   # pwsh 7, el shell preferido del IDE
   Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
     -Name LongPathsEnabled -Value 1     # rutas largas (§2)
   [Environment]::OSVersion.Version      # debe ser >= 10.0.17763 (1809)
   ```
2. **Instalar:** doble clic en `Edecán-Setup.exe`. *Se debe ver:* aviso SmartScreen (esperado
   hasta F3/reputación) → "Más información → Ejecutar de todas formas" → instalación sin UAC →
   splash "Arrancando tu asistente…" → wizard de bienvenida en menos de ~60 s.
3. **Backend sano:**
   ```powershell
   $p = (Get-CimInstance Win32_Process |
     Where-Object { $_.Name -eq "edecan-local.exe" }).CommandLine
   $p                                    # debe mostrar --port <N>
   Invoke-RestMethod http://127.0.0.1:<N>/healthz   # debe responder 200/objeto sano
   ```
4. **Terminal del IDE (el corazón):** abrir el IDE → nueva terminal. *Se debe ver:* prompt de
   PowerShell interactivo. Ejecutar `git status` (colores correctos), luego
   `python -c "import sys; print(sys.stdout.isatty())"` → **True** (si dice False, ConPTY no
   está activo: fallo de F1).
5. **Ctrl+C:** en esa terminal, `ping -t 127.0.0.1` → Ctrl+C. *Se debe ver:* el ping se corta y
   el prompt vuelve; la terminal sigue viva.
6. **Árbol de procesos:** correr `npm run dev` (o `node -e "setInterval(()=>{},1e3)"`) en la
   terminal → cerrar la sesión desde la UI → en PowerShell aparte:
   `Get-Process node -ErrorAction SilentlyContinue` → *sin resultados.*
7. **Agente + deshacer:** pedirle al agente un cambio en un archivo de un repo real → verificar
   el diff en la UI → deshacer el archivo → `git diff` en el repo debe quedar limpio.
8. **Resiliencia (F2):** con un `npm run dev` corriendo en el IDE, matar el companion
   (`Stop-Process -Name edecan-local -Force`) → reabrir Edecán. *Se debe ver:* la sesión marcada
   "interrupted", cero `node.exe` huérfanos (F3 lo garantiza por Job Object; en F1–F2 anotar el
   resultado real).
9. **Firma (tras F3):**
   ```powershell
   Get-AuthenticodeSignature "$env:USERPROFILE\Downloads\Edecán-Setup.exe"
   ```
   *Se debe ver:* `Status: Valid` y el sello de tiempo.
10. **Apagado limpio:** bandeja → "Salir completamente" → `Get-Process edecan* , node -ErrorAction
    SilentlyContinue` → *sin resultados.*

Lo que esta lista deje en rojo — y solo eso — es el trabajo restante. Lo que deje en verde es la
primera evidencia real de Edecán en Windows; hasta entonces, todo lo anterior es diseño.
