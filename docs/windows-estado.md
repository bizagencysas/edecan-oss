# Estado de la consolidación del soporte Windows (28–30 julio 2026)

Este documento es el resultado de consolidar el trabajo de decenas de agentes que
auditaron y tocaron el monorepo en paralelo siguiendo el plan de
[`edecan-windows.md`](./edecan-windows.md). No repite ese plan (sus decisiones son
contrato, ver ese documento) — registra qué quedó realmente en el árbol de trabajo,
qué se verificó de verdad en esta Mac, qué se dedujo/unificó, y qué sigue sin poder
comprobarse porque **no hay ninguna máquina Windows en este entorno**.

Regla de lectura: todo lo de "escrito y razonado" en las tablas de abajo es código
real, revisado y con tests que corren en macOS — pero **nada de eso se ha visto
ejecutar en Windows**. La sección 3 es la más importante del documento.

## 1. Qué se arregló en esta consolidación (además de lo que ya traían los agentes)

| Ámbito | Archivo(s) | Qué estaba mal | Gravedad |
|---|---|---|---|
| Lint | `apps/worker/edecan_worker/handlers/create_linkedin_post.py` | Imports desordenados, `json` sin usar, 5 líneas > 100 cols | cosmético |
| Lint | `packages/creative/edecan_creative/imagen_editorial.py` | 1 línea > 100 cols | cosmético |
| Duplicación | `apps/companion/edecan_companion/platform_paths.py` (nuevo `reemplazar_con_reintentos`) | El mitigador de "archivo bloqueado en Windows" (5 reintentos, backoff 50→800ms, solo `PermissionError` en `nt`) estaba escrito UNA vez en `ide_sessions.py` y los otros dos consumidores obvios (`ide_files.py::FileService.write`, `ide_workspaces.py::WorkspaceStore._save`) seguían con `os.replace` liso — exactamente el hallazgo que dos agentes distintos habían dejado como "pendiente, fuera de mi alcance". Se centralizó en `platform_paths.py` y los tres puntos de escritura ahora importan la misma función. | degrada → corregido |
| Tests | `apps/companion/tests/test_platform_paths.py` | Contrato del reintento (antes vivía en `test_ide_sessions.py`, ligado a la función que se movió) ahora fijado una sola vez contra la implementación compartida; se agregó un tercer caso (agotar los intentos y relanzar) que no existía. | — |
| Tests | `apps/companion/tests/test_ide_sessions.py` | Las dos pruebas del reintento se reemplazaron por una de integración que confirma que `SessionManager._save` de verdad pasa por la función compartida (sin duplicar el contrato de reintento). | — |
| Seguridad/docs | `apps/api/tests/test_v6_sweep_flags.py` | El pin `test_job_types_documentados_coinciden_con_edecan_schemas_queue` no conocía el job type nuevo `create_linkedin_post` (agregado por el trabajo paralelo de LinkedIn) — se documentaron sus dos superficies reales de encolado (atajo de chat en `conversations.py` y delegación desde `run_automation.py`, verificado leyendo el código, incluida la revalidación del flag `automations.rules`). | rompe (test) → corregido |
| Modelo stale | `apps/api/tests/test_credentials_router.py`, `apps/api/tests/test_llm_router_deps.py` | Estas pruebas seguían fijando `@cf/zai-org/glm-4.7-flash` como default del chat, pero el default de producción cambió intencionalmente a `@cf/meta/llama-4-scout-17b-16e-instruct` (ver el comentario extenso en `apps/api/edecan_api/config.py` sobre por qué glm-4.7-flash rompía el motor de LinkedIn). Las pruebas quedaron actualizadas al valor real. | rompe (test) → corregido |
| Contenido stale | `apps/mobile/ios/tests/test_project_config.py` | El test afirmaba que la cámara se pedía "únicamente para escanear el QR", pero desde la función de visión por foto en el chat (commit `330de23`) la cámara sirve para DOS cosas. Se corrigió la aserción para reflejar el texto real (que sigue mencionando el QR) en vez de forzar `project.yml` a mentir. | degrada (test) → corregido |
| Docs | `docs/windows.md` | Documentaba el terminal del IDE en Windows como "modo degradado por pipes" — cierto cuando se escribió, pero ya no: `pty_compat.py` con ConPTY/`pywinpty` reemplazó ese código. Se actualizó el aviso inicial, la tabla de la §7 y las dos entradas de troubleshooting de terminal para decir lo que es verdad hoy: hay una implementación real, pero **nunca ejecutada en Windows**, sin exagerar en ningún sentido (ni "sigue roto" ni "ya funciona"). | degrada (doc desactualizado) → corregido |

Nada de lo anterior se descubrió por sospecha: cada fila viene de correr
`uv run pytest packages apps -q` completo, leer el traceback real, y decidir si el
código o el test era lo que ya no era cierto.

## 2. Duplicación revisada y NO tocada (a propósito)

El encargo pedía buscar funciones repetidas de "matar árbol de procesos" y
"dónde viven los datos". Se encontraron varias, y la decisión, caso por caso:

- **`platform_paths.py` en `edecan_companion` vs `edecan_core`**: duplicación
  **intencional y ya documentada** en ambos módulos — `apps/companion` está
  aislado a propósito de `edecan_core` (es el único paquete pensado para
  instalarse solo, en la máquina del usuario). No se unificó; sería violar una
  decisión de arquitectura ya tomada.
- **`_comando_taskkill`** (`pty_compat.py`) ya lo reutiliza `ide_workers_agent.py`
  para su propio `_terminate_process` — dedup correcta, no había nada que hacer.
- **Kill-tree por triplicado entre paquetes independientes**
  (`packages/toolkit/edecan_toolkit/seguridad.py::_kill_pentestgpt_tree`,
  `packages/design-studio/edecan_design_studio/render.py::_kill_tree_windows`,
  `packages/forge-kernel/edecan_forge_kernel/execution.py::_kill_tree`,
  y el mecanismo de `pty_compat.py`): las cuatro implementaciones son
  pequeñas (armar `taskkill /T [/F] /PID <pid>` + `subprocess.run`), casi
  idénticas, y viven en paquetes que **no se importan entre sí** (cada uno es
  un deployable independiente, ver `ARCHITECTURE.md`). Extraer una quinta
  dependencia compartida solo para esto crearía un acoplamiento nuevo entre
  paquetes que hoy son independientes, sin poder verificar en Windows si el
  resultado sigue siendo correcto en los cuatro sitios. Se deja señalado aquí
  en vez de fusionado a ciegas.

## 3. Lo que queda pendiente de comprobar EN Windows (consolidado, sin duplicados)

Nada de esta lista se ha visto correr en una máquina Windows real. Está en el orden
en que un dueño siguiendo el checklist de la §4 la iría resolviendo.

1. **El terminal del IDE (ConPTY vía `pywinpty`, `apps/companion/edecan_companion/pty_compat.py`, clase `_WindowsPTY`)** —
   la pieza central de todo el encargo. Supuestos sin confirmar, citados en el
   propio docstring de la clase:
   - Si el `CreateProcess` interno de `pywinpty` usa `CREATE_NEW_PROCESS_GROUP`
     (si lo usa, Ctrl+C no llega al hijo aunque el resto funcione).
   - El orden `(rows, cols)` de `PtyProcess.spawn(dimensions=...)` / `setwinsize`.
   - Que `winpty.WinptyError` sea el tipo real que lanza un fallo de
     `CreatePseudoConsole`.
   - Que `taskkill /T` sin `/F` de verdad dé tiempo a un cierre limpio antes de
     los 3s del martillo.
   - Que el backend por defecto de `pywinpty` en la máquina del dueño sea
     ConPTY y no caiga a winpty clásico.
2. **Que `pywinpty>=2.0.14` instale sin fricción** en Windows x64 (dependencia
   con marcador `sys_platform == 'win32'`, agregada a
   `apps/companion/pyproject.toml`) — incluido el runner `windows-latest` de
   CI si/cuando ese job se activa como gate obligatorio.
3. **`_terminate_process` en `ide_workers_agent.py`** (cancelar un comando del
   agente Workers AI) usando el mismo `taskkill /T` — nunca visto matar de
   verdad un árbol tipo `npm test`/`pytest -n auto` en Windows.
4. **Selección de shell por defecto** (`SessionManager._default_terminal_argv`):
   `pwsh.exe` → `powershell.exe` → `%COMSPEC%` con `-NoLogo -NoProfile` — la
   lógica tiene tests de contrato con `shutil.which`/`os.name` mockeados, pero
   nunca arrancó un shell real en Windows.
5. **`reemplazar_con_reintentos`** (ahora centralizada, §1) — el escenario que
   mitiga (antivirus/OneDrive con el archivo abierto un instante) nunca se
   provocó de verdad; solo se simuló con `os.replace` mockeado.
6. **`_sensitive_windows_roots`** en `ide_workspaces.py` (bloquear
   `C:\Windows`, `C:\Program Files`, etc. como workspace) — nunca se probó con
   valores reales de `SystemRoot`/`ProgramFiles` de una máquina Windows.
7. **El selector nativo de carpetas en Windows** (`pick_workspace_folder`, rama
   PowerShell `FolderBrowserDialog`) — solo lectura de código, nunca ejecutado.
8. **Rutas y nombres de archivo**: `validar_nombre_multiplataforma`,
   `advertir_si_ruta_larga`, `cache_dir()`/cambio de `%LOCALAPPDATA%` — probados
   con `monkeypatch`, nunca contra un `%LOCALAPPDATA%`/registro reales.
   `advertir_si_ruta_larga` sigue sin estar cableada a ningún punto real de
   creación de archivos (nadie la llama todavía en producción).
9. **Firma Authenticode** (Fase 3 del plan): no existe ningún certificado
   configurado hoy; el hook `EDECAN_WINDOWS_SIGN_COMMAND` en `build-app.ps1` es
   código sin ejercitar.
10. **El job `tests-windows` de CI** (`.github/workflows/ci.yml`): el YAML es
    sintácticamente válido y se probó localmente que la MISMA invocación de
    pytest pasa en macOS, pero el job en sí nunca corrió en un runner
    `windows-latest` real.
11. **Todo el smoke de empaquetado** (`verify-windows-bundles.ps1`, instalador
    NSIS/MSI, WebView2 en una máquina limpia, SmartScreen): sin cambios desde
    el estado que ya declaraba `apps/desktop/README.md` — nadie lo corrió en
    esta sesión.

## 4. Guion de verificación para la PC (Windows 10 22H2+ / Windows 11, x64)

Seguí estos pasos EN ORDEN. Cada uno dice qué deberías ver si salió bien.

```powershell
# 1) Clonar/actualizar el repo y sincronizar dependencias
cd C:\ruta\a\edecan
git pull
uv sync --all-packages --frozen
# Esperado: termina sin errores. Si "pywinpty" falla al compilar/instalar,
# ese es EXACTAMENTE el punto 2 de la sección 3 -- anotá el error completo.

# 2) Correr la suite completa de los paquetes con supuestos Windows
uv run pytest apps/companion apps/local packages/toolkit packages/schemas -q
# Esperado: verde. Los tests marcados @pytest.mark.skipif(sys.platform != "win32")
# en apps/companion/tests/test_pty_compat.py y test_ide_workers_agent.py corren
# por primera vez de verdad aquí -- si alguno falla, es la primera evidencia real
# de que un supuesto de la sección 3 no se cumple.

# 3) Arrancar el companion suelto (sin la app de escritorio) y abrir el IDE
cd apps\companion
uv run python -m edecan_companion.main
# En el navegador/cliente que uses para probar el IDE: abrí una terminal nueva.
# Esperado: aparece un prompt interactivo real (no un eco plano), y escribir
# `echo %CD%` (o el prompt de PowerShell) responde con el directorio actual.

# 4) Probar Ctrl+C
# Dentro de esa misma terminal del IDE: corré `ping -t 127.0.0.1` (no termina solo)
# y mandá Ctrl+C desde el cliente.
# Esperado: el ping se corta. Si sigue corriendo, es el supuesto #1 de la
# sección 3 (CREATE_NEW_PROCESS_GROUP) fallando -- es EXACTAMENTE lo que hay
# que reportar, con la versión de Windows y de pywinpty instalada
# (`uv run python -c "import winpty; print(winpty.__version__)"`).

# 5) Probar que cerrar la terminal mata el árbol completo
# En la terminal del IDE: `npm run dev` (o cualquier comando que deje un
# proceso hijo vivo) y después cerrá la sesión de terminal desde la UI.
# Esperado: en el Administrador de tareas, node.exe (o el hijo que sea)
# desaparece solo, sin tener que matarlo a mano.

# 6) Probar el shell por defecto
# Abrí una terminal nueva SIN pasar argv (la que arranca por defecto).
# Esperado: si tenés PowerShell 7 instalado (`winget install Microsoft.PowerShell`),
# arranca pwsh; si no, Windows PowerShell 5.1; si ninguno, cmd.exe.

# 7) Compilar el instalador completo y correr el smoke real
cd ..\..\apps\desktop
.\scripts\build-app.ps1
.\scripts\verify-windows-bundles.ps1
# Esperado: el segundo script termina sin errores -- instala en un perfil
# efímero, arranca la app instalada de verdad, espera que el backend real
# conteste, cierra, y confirma cero procesos huérfanos.

# 8) Rutas largas (opcional, solo si tus proyectos viven anidados hondo)
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1
# (requiere PowerShell como administrador, una sola vez)

# 9) Probar un archivo bloqueado de verdad
# Con el IDE abierto, abrí un archivo del proyecto en el Bloc de notas (dejalo
# abierto ahí) y pedile al agente que lo edite.
# Esperado: la edición se aplica sin error visible (los reintentos del punto 5
# de la sección 3 absorben el lock transitorio). Si falla, es evidencia real.

# 10) CI en Windows real (si tenés permisos del repo)
# Empujá un PR que toque pty_compat.py y confirmá que el job "tests-windows"
# de .github/workflows/ci.yml corre y queda verde en un runner windows-latest.
```

Si cualquiera de estos pasos da un resultado distinto al "Esperado", **eso es
justamente la evidencia que falta** — no es un bug tuyo, es exactamente lo que
esta consolidación no pudo comprobar sin una PC Windows delante.

## 5. Lo que se acepta que NO va a funcionar (explícito, no un olvido)

- **Firma Authenticode del instalador** — no hay certificado (Azure Trusted
  Signing) configurado; SmartScreen va a advertir en cada instalación hasta
  que exista uno (Fase 3 del plan).
- **`edecan doctor` / diagnóstico integrado** — no existe todavía (Fase 2).
- **Job Objects como garantía anti-huérfanos** — Fase 1 usa `taskkill /T` con
  escalamiento a `/T /F`; el respaldo con `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
  para sobrevivir a un crash del companion es Fase 3, no implementado.
  `taskkill` no es infalible: un proceso que se desconecta de su árbol antes de
  que llegue la señal puede quedar huérfano igual.
- **`open_app`/`clipboard_get`/`clipboard_set`** (`actions.py`) en Windows —
  devuelven `ActionError` claro ("no soportado en esta plataforma") en vez de
  intentar algo no verificado. No hay backend Windows para estas dos acciones.
- **Personal Mail/Messages/Contacts** (`personal_apps.py`) — exclusivo de
  macOS (`_require_macos()` al entrar a cada función); en Windows siempre da
  un error claro, nunca un intento silencioso.
- **Instaladores de self-host** (`scripts/instalar-*.sh`) en Windows nativo —
  rechazan explícitamente `cmd`/PowerShell puro y piden WSL2; es la única vía
  soportada para el self-host con Docker Compose en Windows, y ya tiene su
  propio test que lo confirma.
- **ARM64** — no hay build ni lo va a haber en el alcance actual; solo x64,
  Windows 10 1809+ (piso que exige ConPTY).
- **Compilar/probar apps móviles desde Windows** — Android sí (Gradle +
  `gradlew.bat` ya existen), iOS nunca (Xcode es exclusivo de macOS,
  estructuralmente imposible en Windows).

## 6. Verificación real ejecutada en esta sesión (macOS)

Ver el JSON de entrega para la salida textual completa de `ruff` y `pytest`.
Resumen:

- `uv run ruff check packages apps`: limpio para todo lo tocado en esta
  consolidación. Quedan 2 errores (`I001` en `ide_runtime.py`, `UP042` en
  `preparacion.py`) que **no son de este trabajo** — aparecieron en el árbol
  mientras esta sesión corría, escritos por otra sesión concurrente sobre
  archivos fuera de este encargo (ninguno de los dos toca terminal/rutas/
  procesos). No se tocaron, siguiendo la regla de "solo los archivos de tu
  encargo". Por la misma razón, la última corrida completa de la suite mostró
  UN failure adicional nuevo (`test_v6_sweep_flags.py::
  test_solo_tres_modulos_de_routers_invocan_send_command`, porque ese mismo
  trabajo concurrente agregó `apps/api/edecan_api/routers/preparacion.py` sin
  actualizar ese pin) que tampoco se tocó: es la misma feature en vuelo,
  todavía no terminada por su propia sesión.
- `uv run ruff format --check packages apps`: 215 archivos pedirían
  reformateo — **preexistente**, confirmado comparando contra el estado sin
  los cambios de esta sesión (`git stash`); no es un efecto de este trabajo ni
  algo introducido por el soporte Windows. Se documenta para que no se
  re-investigue como si fuera nuevo.
- `uv run pytest packages apps -q`: tres corridas completas de principio a fin
  en esta sesión, **1300+ tests de `apps/companion` en verde** (incluida la
  migración completa a `pty_compat`; el número exacto sube en cada corrida
  porque hay trabajo concurrente de otras sesiones agregando tests en vivo).
  El resto del monorepo: entre 25 y 26 fallos residuales según qué tan
  avanzado estaba el trabajo concurrente en el momento exacto de cada corrida
  — todos diagnosticados con causa raíz real (no supuesta), ninguno relacionado
  con el soporte Windows:
  - **23 por drift del `.env` local de esta Mac**, no del código: `.env`
    (gitignored, con credenciales reales de Cloudflare R2) tiene
    `S3_BUCKET=edecan`, pero el código y los tests ya esperan
    `S3_BUCKET=edecan-files` (cambio intencional de otro trabajo en paralelo,
    ver `apps/api/edecan_api/config.py`); y no tiene `SQS_QUEUE_URL`
    configurado. **No se tocó `.env`** — tiene secretos reales y no hay forma
    de saber desde código si el bucket real en R2 se llama "edecan" o
    "edecan-files"; cambiarlo a ciegas podría apuntar la app a un bucket que
    no existe. Quien lea esto: confirmá cuál es el nombre real del bucket en
    tu cuenta de Cloudflare R2 antes de tocar `.env`.
  - **2 por una feature inconclusa y no relacionada con Windows**: la
    resolución "bring-your-own" del LLM de un tenant para jobs de background
    (`apps/worker/edecan_worker/deps.py::Deps.llm_router_for`, hoy un stub que
    siempre devuelve el router de plataforma) — dos tests
    (`test_automation_handlers.py`, `test_run_mission_handler.py`) ya asumen
    que existe, y hasta citan una excepción `TenantLLMNotConnectedError` que
    no está definida en ningún archivo de producción. Se dejó una tarea de
    seguimiento aparte (no es parte del encargo de Windows).
