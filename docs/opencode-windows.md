# Windows: estado real, cierre de la fase (31-jul-2026)

Este documento responde a una pregunta concreta: **¿Edecán con opencode corre en Windows, de
verdad, y qué falta para empaquetarlo?** Todo lo que sigue está separado en "medido en vivo
contra la VM real" (Windows Server 2022, build 10.0.20348.5386, EC2 `i-0c5071ecb529091a6`) y
"arreglado y probado como lógica en macOS, pendiente de re-confirmar en la VM" — la regla de esta
ronda de cierre fue explícita: no conectarse a la VM (otro trabajo la estaba usando en paralelo).
Nada de lo que sigue está inventado ni suavizado; donde algo no se pudo verificar, se dice.

Ver también: `docs/opencode-motor.md` §5 (puntos 5 y 6, actualizados en esta misma ronda) y
`docs/opencode-empaquetado.md` §6 (scripts de empaquetado de Windows).

## Resumen para quien solo quiere el número

> **ACTUALIZADO 01-ago-2026 — el instalador YA EXISTE.** Las dos cosas que este resumen daba por
> pendientes (la corrección del token sin re-confirmar, y el instalador sin compilar) se cerraron
> ese día contra la VM real. Ver **§9 y §10** al final, que son la versión vigente. El resto del
> documento se conserva tal cual porque el diagnóstico sigue siendo válido.

**No está al 100%, pero ya hay instalador.** El ciclo completo (opencode real + Cloudflare Workers
AI real + escritura en disco + parada limpia sin procesos huérfanos) está **demostrado en vivo
contra la VM Windows real**, el token de Cloudflare **ya queda protegido de verdad** (§9), y el
instalador NSIS/MSI **se compiló y se verificó que lleva `opencode.exe` dentro** (§10).

Lo que sigue faltando para decir 100%: el instalador **no está firmado** (Windows avisará de
"editor desconocido"), **nadie lo ha instalado ni arrancado todavía**, y quedan la capa ConPTY
(`pty_compat.py`, 4 tests fallando, rompe en cascada el instalador de dependencias de un proyecto)
y 35 tests con el patrón `os.access(X_OK)` no-op — deuda de *fixtures*, no del producto, que ya
distingue Windows correctamente.

## 1. Lo que SÍ funciona, medido en vivo contra la VM real

Verificado dos veces, en dos rondas de medición distintas, contra la misma VM
(`ssh -i ~/.ssh/edecan-win.pem Administrator@<ip-de-la-vm>`, la IP cambia en cada arranque —
confirmar con `aws ec2 describe-instances --instance-ids i-0c5071ecb529091a6`):

- **Instalación de opencode**: `npm install -g opencode-ai` deja `opencode 1.18.10` (primera
  ronda) / `1.17.18` (segunda ronda, versión fijada por el empaquetado) operativo. `opencode
  serve` responde `200` en `/config` y `/doc`.
- **El ciclo completo IDE → opencode → Cloudflare → disco funciona**: el test oficial de la suite,
  `test_ide_opencode.py::test_prompt_real_modifica_archivo_en_disco`, **PASSED** en vivo contra
  la VM en la segunda ronda — arranca opencode real, crea una sesión con el proveedor
  `workersai`/modelo `kimi-k2.7-code`, manda un prompt real, opencode llama su herramienta de
  escritura de verdad, y el archivo cambia en disco con el contenido exacto pedido. No hay nada
  simulado en esa prueba.
- **`ServidorOpencode.detener()` ya NO deja procesos huérfanos** (era el hallazgo más grave de la
  primera ronda de medición): antes, 27-29 procesos `opencode.exe` sobrevivían indefinidamente a
  `detener()` porque en Windows el proceso que asyncio rastrea es el `cmd.exe` padre (cuando
  opencode se resuelve vía el shim `opencode.cmd` que instala npm), y `opencode.exe` es su nieto
  — `.terminate()`/`.kill()` de Python solo mata al padre. Arreglo: `taskkill /T [/F] /PID`
  (recorre el árbol real de procesos), reutilizando el mismo primitivo de dos fases
  (cooperativo → martillo) que ya usaba `ide_workers_agent.py` para el mismo problema. **Medido
  en vivo, dos veces, en la segunda ronda**: con mi propio script E2E, 1 proceso `opencode.exe`
  durante la operación y 0 (cero) tras `detener()` + 3s; y con la suite oficial completa, el
  mismo patrón. Antes: 27-29 supervivientes.
- **Empaquetado — `download-opencode.ps1` funciona de punta a punta en Windows real**: descarga
  `opencode-windows-x64.zip` (versión `1.17.18`), verifica el SHA-256 con `Get-FileHash`
  *antes* de extraer, deja `opencode-x86_64-pc-windows-msvc.exe` (184 048 520 bytes) en
  `binaries\`, y ese binario responde `1.17.18` a `--version`. Este script es un espejo
  deliberado de `download-opencode.sh` (misma tabla de SHA-256, mismo criterio de salida
  temprana).
- **`uv run ruff check apps/companion` limpio en Windows real**, mismos 2 errores preexistentes
  que ya había en macOS (deuda vieja, no de Windows) — cero errores nuevos.

## 2. El hallazgo nuevo de esta ronda: `icacls` fallaba en la VM real (workgroup)

La corrección de Windows contra `chmod 0600` (el `opencode.json` lleva el token de Cloudflare en
texto claro) se implementó en una ronda anterior con `icacls <ruta> /inheritance:r /grant:r
<cuenta>:F /grant:r SYSTEM:F`, y se verificó en vivo con una cuenta fija (`Administrator`) — pero
la ronda de **validación** completa (la que corrió después, también contra la VM real) encontró
que la versión de producción, que resuelve la cuenta dinámicamente como
`{USERDOMAIN}\{USERNAME}`, **fallaba en esa misma VM** con el código de salida **1332**
("No mapping between account names and security IDs was done"):

```
$ echo %USERDOMAIN% %USERNAME% %COMPUTERNAME%
USERDOMAIN=WORKGROUP USERNAME=administrator COMPUTERNAME=EC2AMAZ-S5OIDGN

icacls C:\edecan_e2e_v2_workspace\opencode.json /inheritance:r /grant:r WORKGROUP\administrator:F /grant:r SYSTEM:F
-> "icacls terminó con código 1332"
-> stderr: "WORKGROUP\administrator: No mapping between account names and security IDs was done."

icacls del archivo real, DESPUÉS del intento fallido:
C:\edecan_e2e_v2_workspace\opencode.json  NT AUTHORITY\SYSTEM:(I)(F)
                                           BUILTIN\Administrators:(I)(F)
                                           BUILTIN\Users:(I)(RX)   <- el token seguía legible
```

Causa raíz: esta VM es una instancia EC2 standalone, **no unida a ningún dominio Active
Directory** — `USERDOMAIN` en ese caso vale literalmente `WORKGROUP` (el nombre de grupo de
trabajo por defecto de Windows), que **no es un principal de seguridad que `icacls` pueda
resolver a un SID**. El código no reventaba (el manejo "best-effort" lo atrapa y sigue), pero
tampoco protegía nada: silenciosamente, `BUILTIN\Users` seguía pudiendo leer el token.

### El arreglo de esta ronda

`ide_opencode_config.py` e `ide_opencode_motor.py` (cada uno con su propia copia, "cinturón y
tirantes", mismo criterio que el resto del blindaje de estos dos módulos) ahora prueban una
**lista** de cuentas candidatas, en vez de una sola:

1. `{DOMINIO}\{USUARIO}` — solo si `USERDOMAIN` no es el workgroup por defecto (indicio real de
   dominio AD).
2. `.\{USUARIO}` — cuenta **local**, sintaxis documentada por Microsoft para referirse a un
   usuario sin depender de si la máquina está en un dominio o en un workgroup. Es el candidato
   que de verdad cubre el caso medido en vivo.
3. `{DOMINIO}\{USUARIO}` con el workgroup literal — último recurso, por si alguna vez existiera
   de verdad un dominio AD llamado `WORKGROUP`.

`_restringir_windows` prueba cada candidato en orden y se queda con el primero que `icacls`
acepta (código 0). Ver `_cuentas_windows_candidatas()` en ambos módulos para el docstring
completo con la cita exacta del error 1332.

**Estado de esta corrección**: escrita y probada como LÓGICA PURA en macOS — 8 tests nuevos
(4 en `ide_opencode_config.py`, incluido uno que reproduce el código 1332 exacto medido en vivo
y confirma que el segundo candidato lo resuelve; 2 más en `ide_opencode_motor.py`; el resto
cubre el orden de candidatos y los casos sin `USERNAME`/`USERDOMAIN`). **NO se pudo re-verificar
en vivo contra la VM en esta ronda** — la regla explícita de este cierre fue no conectarse
(otro trabajo la estaba usando). Es la pieza más importante que falta confirmar antes de decir
"Windows está al 100%": que `.\administrator` de verdad resuelve donde `WORKGROUP\administrator`
falló con el 1332, en esa misma máquina.

## 3. Otro hallazgo nuevo y arreglado esta ronda: `os.kill(pid, 0)` en `ide_worktrees.py`

No estaba en el mapa de fallos original, pero se encontró leyendo el código en busca de
patrones ya conocidos en este repo: `ide_worktrees.py::_pid_vivo` (decide si el proceso dueño de
una corrida de sub-agentes sigue vivo, para no borrarle el trabajo) usaba `os.kill(pid, 0)`
directamente — el mismo patrón "primitivo POSIX que en Windows hace otra cosa" que ya se había
identificado y arreglado para `ServidorOpencode.detener()`.

La propia documentación de Python lo advierte para Windows: cualquier señal que no sea
`CTRL_C_EVENT`/`CTRL_BREAK_EVENT` "causará que el proceso sea matado incondicionalmente por la
API `TerminateProcess`, y el código de salida se pondrá a `sig`". Es decir: `os.kill(pid, 0)` en
Windows llamaría a `TerminateProcess(handle, 0)` y **mataría de verdad** el proceso que solo
había que consultar. Además, para un PID que no existe, esa misma llamada en Windows no siempre
levanta `ProcessLookupError` (la excepción que la rama POSIX usa para decidir "muerto") — levanta
un `OSError` genérico, que el código existente trataba como "vivo" (pensado para "existe pero sin
permiso") — clasificando un PID **muerto** como **vivo**, exactamente al revés de lo necesario
para barrer huérfanos.

Arreglo: `_pid_vivo_windows()` nuevo, que abre un handle de solo consulta
(`PROCESS_QUERY_LIMITED_INFORMATION`, vía `ctypes.WinDLL("kernel32")`, sin matar nada) para
comprobar existencia real. 3 tests nuevos en `test_ide_worktrees.py` (con `ctypes.WinDLL`
mockeado, ya que no existe en macOS) cubren: que nunca se llama a `os.kill` en esta rama, que un
PID inexistente da `False`, y que "acceso denegado" se sigue tratando como vivo (mismo criterio
conservador que la rama POSIX). **Tampoco se pudo confirmar en vivo contra Windows real** en esta
ronda.

## 4. Otros dos arreglos de esta ronda (menor riesgo, mismo criterio: código, no VM)

- **`actions.py::_run_command`** no manejaba el mismo bug de shims `.cmd`/`.bat` ya documentado
  dos veces en este repo (`edecan_mcp.transport`, `ide_opencode_binario`): `subprocess.run(argv,
  shell=False)` en Windows usa `CreateProcess`, que no lanza un guion por lotes aunque
  `shutil.which` sí lo encuentre. Si un dueño agrega `"npm"` a `allowed_commands` (plausible: es
  una herramienta de tooling típica), en Windows `npm` resuelve a `npm.cmd` y la ejecución
  fallaría con `FileNotFoundError [WinError 2]`. Arreglo: `_argv_para_windows()`, mismo criterio
  ya establecido (resolver con `shutil.which`, envolver en `cmd.exe /c` solo si hace falta, JAMÁS
  `shell=True`). 5 tests nuevos, lógica pura. Nota honesta: esto **no** es lo que hacía fallar
  `test_run_command_never_interprets_shell_metacharacters` en la VM real — ese test usaba
  `echo`/`touch`, que en Windows son builtins de la shell sin archivo propio (ningún wrapper
  `.cmd`/`.bat` de por medio); se corrigió aparte, ver el punto siguiente.
- **Dos tests de la validación reescritos para ser portables, no para esconder nada**:
  `test_actions_commands.py::test_run_command_never_interprets_shell_metacharacters` usaba
  `echo`/`touch` (no existen como ejecutables independientes en Windows) — se cambió a
  `sys.executable` (mismo criterio que los tests vecinos del archivo), preservando exactamente la
  misma comprobación de seguridad (que `;` nunca encadena un segundo proceso). Verificado que
  la intención original del test se mantiene byte a byte, no se debilitó nada.

## 5. Un tercer hallazgo, más especulativo: relojes de VM y orden "más viejo primero"

La validación encontró 3 tests nuevos sin diagnosticar en módulos con lógica de "purgar lo más
viejo" (`ide_checkpoints.py::test_workspace_cap_evicts_oldest_checkpoint`,
`ide_navegador.py::test_tope_de_cantidad_purga_las_capturas_mas_viejas`). Leyendo el código: ambos
ordenan por una marca de tiempo (`time.time()`/`time.time_ns()`) tomada en sucesión rápida, sin
ninguna garantía de que sea estrictamente creciente. Si dos capturas/checkpoints empatan en esa
marca, el desempate cae en `Path.glob()` (que la propia stdlib documenta sin ningún orden
garantizado) o en un UUID aleatorio — "el más viejo" deja de tener significado real.

Esto es un patrón conocido en máquinas virtuales: el reloj de un hipervisor no siempre entrega la
resolución "nominal" que promete la API del sistema operativo, sobre todo bajo carga — un
problema documentado para instancias EC2 en particular. **No se pudo confirmar que esta sea la
causa raíz exacta de los 2 fallos medidos** (no hay VM disponible en esta ronda para reproducirlo
con certeza), pero el patrón de código es objetivamente fragil en cualquier plataforma virtualizada
y el arreglo no tiene efectos secundarios: se agregó un desempate monotónico
(`ide_checkpoints._now_us()`, `ide_navegador.AlmacenCapturas._ultimo_time_ns`) que nunca entrega
un valor menor o igual al anterior dentro del mismo proceso. Tests nuevos que congelan el reloj a
un valor fijo y confirman que la salida sigue siendo estrictamente creciente. **Etiquetado como
"probablemente correcto, no confirmado"** — a diferencia de los hallazgos §2 y §3, que tienen una
causa raíz citada en documentación oficial o medida en vivo con evidencia exacta.

## 6. Lo que sigue exactamente igual que antes de esta ronda (no tocado, documentado ya)

- **`pty_compat.py` (capa ConPTY)** falla sus 4 tests reales en Windows: detección de código de
  salida nunca resuelve, Ctrl+C no interrumpe al hijo, secuencias de escape ANSI sin filtrar
  rompen el parseo de salida. Rompe en cascada `preparacion.py::EjecutorPreparacion.instalar()`
  (el instalador real de dependencias de Windows queda atascado en "ejecutando" para siempre).
  **No tocado en ninguna ronda hasta ahora.**
- **`os.access(ruta, os.X_OK)` es un no-op en Windows** (siempre `True`) — afecta 28 tests de
  `test_ide_lint.py` y 7 de `test_ide_opencode_binario.py` (confirmados en la ronda de
  validación). La función de PRODUCCIÓN (`ide_opencode_binario._es_ejecutable`) ya es consciente
  de la plataforma (`if os.name == "nt": return True`) — es un hueco de FIXTURES de test, no de
  producción, pero sigue bloqueando que esos 35 tests corran limpios en un CI de Windows.
- **Regex `_PATRON_RUTA_ABSOLUTA_UNIX`** (`ide_reglas_verificables.py`) es solo-POSIX (`/Users/`,
  `/home/`) — nunca detecta una ruta Windows tipo `C:\Users\...` hardcodeada. Ciega en Windows.
- **4 fallos de `test_ide_worktrees.py` sin diagnosticar**: uno (relacionado con `os.kill`) se
  resolvió con el arreglo de §3; quedan al menos 3 más sin causa raíz confirmada
  (`test_crear_da_una_copia_completa_y_destruir_no_deja_rastro`,
  `test_la_limpieza_no_toca_los_worktrees_propios_del_dueno`,
  `test_barrer_no_le_arranca_los_worktrees_a_una_corrida_sin_metadatos`). El primero compara
  `git status --porcelain` byte a byte antes/después de crear y destruir un worktree — sospecha
  razonable de CRLF/`core.autocrlf` (mismo patrón ya visto en
  `test_ide_acciones_codigo.py::test_diff_archivo_sin_cambios_no_trae_texto`), pero **no
  confirmado**, no se tocó código sin evidencia real.
- **Separadores de ruta**: `actions.py` devuelve backslash nativo en vez de forward-slash
  normalizado en las respuestas de sandbox — riesgo real si un cliente JSON (iOS/web) espera
  rutas estilo POSIX. Sin investigar a fondo.
- **`test_config.py`** (quoting de YAML con backslash de Windows sin escapar) y
  **`test_ide_workers_agent.py`** (`os.kill(pid, 0)` en el TEST, no en producción) — no se
  confirmó si son solo bugs de fixture o si algún código de producción repite el mismo patrón.

## 7. Empaquetado: qué se probó y qué falta

`docs/opencode-empaquetado.md` §6 tiene el detalle completo. Resumen:

- `download-opencode.ps1` — **probado en vivo, funciona** (§1 arriba).
- `build-backend.ps1` — tenía el paso de opencode agregado, pero **nunca se ejecutó completo**:
  la VM tiene Node v24.18.1 global y el script exige Node 22 exacto (`engines` de
  `apps/web/package.json`/`fydesign-engine/package.json`), y aborta a propósito si no lo
  encuentra. Instalar Node 22 (nvm-windows o fnm, sin desinstalar el 24) sigue pendiente —
  requiere tocar la VM.
- `build-app.ps1` — tenía un bug real y confirmado (`$ExternalBin` no incluía
  `"binaries/opencode"`, así que el instalador habría salido SIN el motor del IDE, en silencio)
  que ya se corrigió, más la comprobación post-build que faltaba. **Nunca se ejecutó
  `cargo tauri build` de verdad** — bloqueado por el mismo problema de Node. El instalador NSIS/MSI
  final de Windows **no existe todavía**.
- **NOTICE** no tiene ninguna entrada de atribución MIT para opencode (tampoco la tiene la
  versión de macOS) — pendiente, fuera del alcance de cualquiera de las rondas hasta ahora.

## 8. Cómo compilar la app en Windows (para cuando la VM esté libre)

```powershell
cd C:\edecan

# 1. Node 22 (el global de la VM es v24, build-backend.ps1 lo rechaza a propósito)
nvm install 22.17.0
nvm use 22.17.0
node --version   # debe imprimir v22.17.0

# 2. uv sync -- NUNCA "uv sync" a secas, rompe el entorno del monorepo
uv sync --all-packages

# 3. Backend + opencode + FyDesign (descarga opencode si falta, valida SHA-256)
.\apps\desktop\scripts\build-backend.ps1

# 4. El instalador completo (NSIS/MSI) -- confirmar en la salida:
#    "==> Sidecars verificados en ...: edecan-local.exe fydesign-node.exe opencode.exe"
.\apps\desktop\scripts\build-app.ps1
```

Después de `build-app.ps1`, falta además: abrir el instalador generado y confirmar a mano que
`opencode.exe` arranca de verdad desde dentro de la app empaquetada (la comprobación del script
solo garantiza que Tauri vio los binarios antes de empaquetar, no que el instalador final los
conserva intactos — ver la cita de `tauri-apps/tauri#15134` en `docs/opencode-empaquetado.md`
§6.3).

## 9. macOS: confirmado sin regresión

Todos los cambios de código de esta ronda (`ide_opencode_config.py`, `ide_opencode_motor.py`,
`ide_worktrees.py`, `actions.py`, `ide_checkpoints.py`, `ide_navegador.py`, y los tests
correspondientes) se verificaron en esta Mac:

```
$ uv run ruff check apps/companion/
All checks passed!

$ uv run --all-packages pytest apps/companion/tests/ -q
2 failed, 1532 passed, 2 skipped, 1 warning in 433.85s (0:07:13)
```

Los 2 fallos de esa corrida son preexistentes, no de esta ronda:
`test_ide_sessions.py::test_permiso_pendiente_en_modo_manual_se_puede_conceder_y_el_turno_sigue`
(uno de los dos flaky de red ya conocidos que advirtió el encargo -- depende de que Cloudflare
Workers AI responda dentro de la ventana esperada) y
`test_pty_compat.py::test_ctrl_c_interrumpe_al_hijo` (flake de timing ya documentado en una ronda
anterior bajo el mismo síntoma exacto -- varias sesiones de Claude Code corriendo en paralelo en
esta Mac durante la corrida, confirmado con `ps aux`). Ninguno de los dos toca ningún archivo que
esta ronda modificó. La baseline (antes de los cambios de `actions.py`/`ide_checkpoints.py`/
`ide_navegador.py`, con solo `ide_worktrees.py`/`ide_opencode_config.py`/`ide_opencode_motor.py`
ya aplicados) había dado **1527 passed, 2 skipped, 0 failed** en la misma Mac — los 1532 passed de
esta corrida final sí incluyen los ~19 tests nuevos de esta ronda, con 2 fallos atribuibles a
flakiness ya conocida, no a regresión.

## 10. Qué falta para el 100%

En orden de impacto real, no de facilidad:

1. **Re-confirmar en vivo, contra la VM real, que el arreglo de `icacls` (§2) de verdad protege
   el `opencode.json`** — es la pieza de seguridad más importante pendiente. Sin esto, no hay
   garantía de que el token de Cloudflare esté protegido en la instancia concreta donde se probó
   el fallo.
2. **Re-confirmar en vivo que `_pid_vivo_windows` (§3) distingue un PID vivo de uno muerto sin
   matar nada** — mismo motivo: la lógica está probada, el comportamiento real contra
   `kernel32.dll` de Windows no.
3. **Compilar el instalador de Windows de verdad** (`cargo tauri build` vía `build-app.ps1`) —
   hoy no existe ningún `.exe`/`.msi` de Edecán para Windows. Requiere instalar Node 22 en la VM
   primero.
4. **Arreglar `pty_compat.py` (ConPTY)** — sin esto, `preparacion.py::EjecutorPreparacion.instalar()`
   (el instalador de dependencias que corre DENTRO de un proyecto de un usuario) se cuelga para
   siempre en Windows. Nadie ha tocado esta capa todavía en ninguna ronda.
5. **Diagnosticar los 3 fallos de `test_ide_worktrees.py` que quedan** (§6) — sin causa raíz
   confirmada, no se tocó código a ciegas.
6. **Arreglar los 35 tests con el patrón `os.access(X_OK)`** (§6) para que corran limpios en un
   CI de Windows — la producción ya es correcta, es deuda de test.
7. Empaquetado: entrada de NOTICE, confirmar el instalador final con el motor arrancando de
   verdad (no solo "Tauri vio los binarios").

**Honestamente, con toda la evidencia de arriba: esto no está al 100%.** Está en un punto sólido
—el ciclo de negocio completo (IDE, opencode, Cloudflare, escritura en disco, parada limpia) se
demostró funcionando en Windows real dos veces, y los dos hallazgos de seguridad/estabilidad más
graves medidos hasta ahora (proceso zombie, token en claro) tienen arreglo escrito— pero falta
re-confirmar esos arreglos en vivo, no existe todavía un instalador de Windows, y hay una capa
completa (ConPTY) sin tocar que bloquea una función real del producto (instalar dependencias)
en esa plataforma.

---

# §9. El token en Windows: por fin protegido de verdad (01-ago-2026)

La corrección anterior (`.\usuario` antes que `DOMINIO\usuario`) **no funcionaba**. Verificado en
vivo contra la VM real, los dos candidatos fallan igual:

```
icacls ... /grant:r .\administrator:F        -> codigo 1332
  ".\administrator: No mapping between account names and security IDs was done."
icacls ... /grant:r WORKGROUP\administrator:F -> codigo 1332  (mismo mensaje)
```

Con los dos fallando, el archivo se quedaba **con los permisos heredados de la carpeta** — es
decir, sin restringir, que era exactamente el problema original.

**Lo que sí funciona: el SID.** No depende del dominio, ni del idioma del sistema, ni de si la
cuenta es local o de dominio:

```powershell
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls <ruta> /inheritance:r /grant:r "*$sid:F" /grant:r "*S-1-5-18:F"
```

Medido en la VM: **código 0**, y la lista final del `opencode.json` generado por
`generar_opencode_json()` queda exactamente en:

```
NT AUTHORITY\SYSTEM:(F)
EC2AMAZ-S5OIDGN\Administrator:(F)
```

Sin `BUILTIN\Users`. Implementado en `_sid_windows_actual()` +
`_cuentas_windows_candidatas()` (SID primero, nombres solo como respaldo por si PowerShell no
está disponible), en `ide_opencode_config.py` **y** `ide_opencode_motor.py`.

Verificado también en la misma sesión: `_pid_vivo` distingue un PID vivo de uno muerto **sin
matarlo** (`ctypes.OpenProcess`) — en Windows, el `os.kill(pid, 0)` anterior terminaba el proceso
al consultarlo.

# §10. El instalador de Windows: compilado y verificado (01-ago-2026)

**Existe.** Compilado en la VM real, con los cinco obstáculos que lo bloqueaban ya corregidos.

```
bundle\msi\Edecán_0.7.4_x64_en-US.msi     460.623.205 bytes  (439,3 MB)
bundle\nsis\Edecán_0.7.4_x64-setup.exe    359.625.655 bytes  (343,0 MB)
```

Copia del `setup.exe` en la Mac del dueño:
`/Users/example/Edecan-Nuevo/instaladores-windows/Edecan_0.7.4_x64-setup.exe`
(sha256 `63ce17def51fd927543cb4e5f860f95b...`).

## El motor va DENTRO — probado, no deducido

No basta con el tamaño. Se abrió la tabla `File` del MSI con `WindowsInstaller.Installer` y se
listaron los 3.267 archivos:

```
  OK  opencode      -> opencode.exe
  OK  edecan-local  -> edecan-local.exe
  OK  fydesign-node -> fydesign-node.exe
  OK  edecan-desktop-> edecan-desktop.exe
```

Esta comprobación existe porque en macOS **no se hizo**: se firmó y notarizó una app **sin el
motor dentro** y el build no dijo nada (ver `build-app.sh`, comprobación post-build).

## Los cinco bugs que lo bloqueaban — todos de la misma familia

Ninguno era visible desde macOS: **era la primera vez que este proyecto se compilaba en Windows.**
Y los cinco son la misma lección.

> **En Windows, los datos estructurados NO viajan por la línea de comandos.**
> PowerShell les quita las comillas al invocar un ejecutable nativo. Se escriben a un archivo y se
> pasa la ruta.

| # | Dónde | Síntoma |
|---|---|---|
| 1 | `build-studio-engine.ps1` — `ffmpeg -L` | El banner de versión va a **stderr**; con `$ErrorActionPreference = "Stop"` y `2>&1` se convertía en error terminante |
| 2 | `build-studio-engine.ps1` — `ffmpeg -buildconf` | Idéntico al anterior, dos pasos después. Se encontró al arreglar el primero |
| 3 | `build-studio-engine.ps1` — 3× `node -e` | `require(node:fs)` en vez de `require("node:fs")` → `SyntaxError: missing ) after argument list` |
| 4 | `build-studio-engine.ps1` — el arreglo de #3 | **Introducido al corregir**: node resuelve `import` relativos a la carpeta **del archivo**, no al cwd (a diferencia de `node -e`) → `ERR_MODULE_NOT_FOUND: playwright` |
| 5 | `build-app.ps1` — `--config <json>` | El JSON llegaba **sin una sola comilla** → `failed to parse config as JSON: key must be a string` |

Arreglos: `Invoke-NodeScript` (helper nuevo, con parámetro `-WorkDir` precisamente por #4) y el
JSON de Tauri por archivo temporal. Los cinco están comentados en el sitio, con el error textual
medido.

## Requisitos del entorno de compilación (medidos)

- **Node 22** junto al 24 global. `nvm-windows` está **sin mantenimiento**; se usó **`fnm`**
  (instalado con Chocolatey — `winget` no está en Windows Server 2022). `fnm use 22` →
  Node v22.23.2 + npm 10.9.8.
- **`tauri-cli` 2.11.4** exacto (`cargo install tauri-cli --version 2.11.4 --locked`), la misma
  que macOS. Compilar desde cero tarda bastante.
- **Rust** ya venía (rustc/cargo 1.97.1).
- **La sesión SSH tiene que seguir viva**: OpenSSH en Windows mete cada sesión `exec` en un Job
  Object con *kill-on-close*, así que un proceso "detached" muere al cerrar la conexión. Usar
  `ServerAliveInterval`.
- **Sincronizar el repo con `COPYFILE_DISABLE=1`**: sin eso, `tar` desde macOS mete miles de
  archivos `._*` que rompen `ruff` y ensucian el diagnóstico.

## Qué falta para decir 100%

1. **Firmar el instalador.** No está firmado: Windows avisará de "editor desconocido". Hace falta
   un certificado de firma de código (se compra). `build-app.ps1` ya tiene el gancho
   (`bundle.windows.signCommand`).
2. **Instalarlo y arrancarlo.** Que exista y contenga lo correcto está probado; que instale y
   levante la app, no.
3. **ConPTY (`pty_compat.py`)** — 4 tests fallando en Windows real; rompe en cascada
   `preparacion.py::EjecutorPreparacion.instalar()`.
4. **NOTICE** sigue sin la atribución MIT de opencode (tampoco en macOS).
