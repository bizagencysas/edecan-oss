# Empaquetar `opencode` dentro de la app de escritorio

`opencode` ([`github.com/anomalyco/opencode`](https://github.com/anomalyco/opencode), MIT, ver
`NOTICE`) pasa a ser el motor de agentes del IDE de Edecán (decisión del dueño: "vamos a meter
el poder de ese IDE en terminal a Edecán... USALO"). El problema práctico de este documento:
la app de escritorio se congela con PyInstaller/Tauri para correr en la máquina de un cliente
que **nunca instaló opencode** — ni en macOS, ni sobre todo en Windows. Si el único lugar donde
el código busca el binario es `~/.opencode/bin/opencode` (la ruta de esta Mac de desarrollo), la
app queda coja fuera de ella.

Este documento explica, sin asumir que ya sabes cómo está armado el resto del empaquetado de
Edecán, **qué hay que meter en el paquete**, **cómo se descarga la versión correcta para cada
plataforma** y **qué pasa si falta**. No modifica ningún script ni `.spec` — dice exactamente
qué cambiarles, para que quien tenga `cargo`/`rustc` a mano (esta sesión no los tiene, ver
`docs/desktop-local.md` §8/§9) los aplique.

El código que resuelve el binario en tiempo de ejecución ya existe:
`apps/companion/edecan_companion/ide_opencode_binario.py` (`resolver_binario_opencode()`), con
sus tests en `apps/companion/tests/test_ide_opencode_binario.py`. Este documento es el
complemento del lado de empaquetado — el que decide QUÉ le deja disponible ese código.

## 1. La decisión: sidecar de Tauri, no dentro del `.spec` de PyInstaller

Hay dos formas de meter opencode en el paquete final. Se recomienda la primera:

**A) Como sidecar de Tauri (`bundle.externalBin`) — recomendado.** Exactamente el mismo
mecanismo que ya usa Edecán para Ollama (`docs/desktop.md` §10) y para `fydesign-node`: un
binario de terceros, completo, copiado junto al ejecutable de la app, con su ruta pasada al
backend local por variable de entorno cuando lo lanza.

**B) Empaquetado dentro del `.spec` de PyInstaller (`apps/desktop/packaging/edecan_local.spec`,
`datas=[...]`)** — la app corre YA en Python, y el bootloader de PyInstaller se autoextrae en
`sys._MEIPASS` en cada arranque, así que en teoría alcanzaría con sumarlo ahí.

**Por qué A y no B**, en dos motivos concretos:

1. **El bit ejecutable no está garantizado en B.** Se leyó el propio código de PyInstaller
   instalado en este repo (`.venv/lib/python3.12/site-packages/PyInstaller/building/api.py`,
   clase `COLLECT`): un archivo `datas` solo recibe `chmod 0o755` en la fase de *build* si ya
   tenía el bit puesto en el disco de origen — y esa fase no es la que importa: en `onefile`
   quien reescribe los archivos en `sys._MEIPASS` en cada arranque es el bootloader compilado en
   C (no hay fuente Python que auditar en el wheel), y no hay garantía documentada de que
   conserve el bit ejecutable de un `DATA` plano. `ide_opencode_binario.py` ya se defiende de
   esto (`_asegurar_bit_ejecutable`, hace `chmod` a mano sobre cualquier candidato que encuentre
   bajo `_MEIPASS`) pero es un parche sobre un comportamiento no garantizado, no una certeza.
2. **B infla y ensucia el binario congelado ya complejo de `edecan-local`** (ver los
   comentarios largos del propio `.spec` sobre `pgserver`/`alembic`/`boto3`) con ~150 MB de un
   binario de terceros que no tiene absolutamente nada que ver con Python. A no ser por Ollama es
   exactamente el mismo argumento: sidecar aparte, ciclo de vida propio.

Si en el futuro alguien de todas formas prefiere B (por ejemplo, para no depender de que
`cargo tauri build` funcione en la máquina de build), `resolver_binario_opencode()` ya lo
soporta como primer escalón (`sys._MEIPASS/opencode/bin/opencode[.exe]` o
`sys._MEIPASS/opencode[.exe]`) — solo hay que sumar esas rutas a `datas=[...]` en el `.spec` y
tener en cuenta el punto 1 de arriba.

## 2. Cómo descargar la versión correcta para cada plataforma

### 2.1 Qué versión fijar

**`v1.17.18`** — no la última disponible. Es la versión que el dueño verificó a mano antes de
empezar esta migración (escribió de verdad en disco contra la API bajo `/api/`, ver `MEMORY.md`
de esa investigación) y contra la que corre el test de integración obligatorio de
`ide_opencode.py` (`test_prompt_real_modifica_archivo_en_disco`). Verificado en esta sesión,
byte a byte: el asset oficial `opencode-darwin-arm64.zip` del release `v1.17.18` de GitHub,
descargado y descomprimido hoy, tiene el **mismo SHA-256** que el binario ya instalado y
probado en esta Mac (`~/.opencode/bin/opencode`):

```
652a34cab759c0fa348f107aa737df86355a49b1576834864e89ee43c059b25d
```

Al momento de escribir esto existe una versión más nueva publicada en el propio repo
(`v1.18.10`, del mismo día). No se recomienda fijarla sin repetir la verificación de escritura
real en disco que ya se hizo con `1.17.18` — actualizar la versión fijada es una decisión
aparte, deliberada, no un efecto lateral de armar el empaquetado.

### 2.2 Tabla de assets (verificada contra la API de GitHub, release `v1.17.18`)

Cada fila es un asset real del release oficial — nombre, tamaño, y el SHA-256 que reporta la
propia API de GitHub (`GET /repos/anomalyco/opencode/releases/tags/v1.17.18`). El contenido de
cada archivo es **un único ejecutable en la raíz** (`opencode` o `opencode.exe`, verificado con
`unzip -l`/`tar tzf` para los assets de macOS/Windows/Linux x64 — sin subcarpetas, sin DLLs
adicionales como sí necesita Ollama en Windows).

| Target triple                | Asset                                  | Destino dentro del archivo | SHA-256 |
|-------------------------------|------------------------------------------|----------------------------|---------|
| `aarch64-apple-darwin`         | `opencode-darwin-arm64.zip`              | `opencode`                 | `24327f89c103526c0518fc9b797767f318ab85ef3cee8636e722d6138f33aa3d` |
| `x86_64-apple-darwin`          | `opencode-darwin-x64.zip`                | `opencode`                 | `cebf209aad2c0bd998fbac3f8dd1b45eef35da1af18cd698e78b111b73c5fbb0` |
| `x86_64-pc-windows-msvc`       | `opencode-windows-x64.zip`               | `opencode.exe`              | `7d489fd9b314e25bccf9c5dd2f17ef2774902c7b7db9aa34f46b0aab4715c70c` |
| `aarch64-pc-windows-msvc`      | `opencode-windows-arm64.zip`             | `opencode.exe`              | `fcfbd7f82242f47ec7e98bc8819eeebe716654e9bce1fb1bd7f364e887cb95ab` |
| `x86_64-unknown-linux-gnu`     | `opencode-linux-x64.tar.gz`              | `opencode`                 | `e149d32ee5667c0cd5fb84d0bf8393b312e93782eeb4d74d29bbb0392de7133c` |
| `aarch64-unknown-linux-gnu`    | `opencode-linux-arm64.tar.gz`            | `opencode`                 | `db9b53eae485da969a0a855bca465f9901dd84676384f724f320e3ccc5a9b107` |

URL base: `https://github.com/anomalyco/opencode/releases/download/v1.17.18/<asset>`.

Hay además variantes `-baseline` (x64 sin instrucciones AVX2, para CPUs viejas) y `-musl`
(Linux Alpine/musl en vez de glibc) para quien las necesite — mismo patrón de nombres, mismos
targets. No se listan acá porque Edecán hoy no las necesita (Linux se empaqueta glibc, ver
`build-backend.sh`), pero están en el mismo release si algún día hacen falta.

### 2.3 El script que falta: `scripts/download-opencode.sh` (+ `.ps1`)

No existe todavía — hay que crearlo. La forma más segura de hacerlo bien es **copiar
`apps/desktop/scripts/download-ollama.sh` (y su espejo `.ps1`) y adaptarlo**, porque ya resuelve
correctamente los mismos problemas que este script va a tener: detectar el target triple,
descargar con `curl`, verificar el SHA-256 antes de tocar nada, extraer (`.zip` en macOS/Windows
vía `unzip`, `.tar.gz` en Linux vía `tar`), y dejar el resultado en
`apps/desktop/src-tauri/binaries/opencode-<target-triple>[.exe]` — la convención de nombres que
Tauri espera para un sidecar declarado como `"binaries/opencode"` en `externalBin`.

Diferencias concretas contra `download-ollama.sh` al adaptarlo:

- La tabla de versión/SHA-256 es la de la sección 2.2 de este documento, no la de Ollama.
- No hace falta el paso que preserva `lib/ollama` (DLLs) para Windows — el asset de opencode es
  un único ejecutable autocontenido en las tres plataformas (verificado en la sección 2.2).
- Opencode es **el motor**, no una integración opcional — a diferencia de
  `EDECAN_BUNDLE_OLLAMA`, este script no debería necesitar una variable de entorno para
  activarse: se corre siempre, como parte normal de `build-backend.sh` (ver sección 3).
- Sumar una entrada a `NOTICE` citando la licencia MIT de opencode y el origen del script
  adaptado, mismo criterio que ya existe ahí para `download-ollama.sh`.

## 3. Qué archivos hay que cambiar (y qué falta si no se hace)

Ninguno de estos archivos se tocó en este encargo — la regla del encargo es "no modifiques
`build-backend.sh` ni el `.spec`", y por extensión (mismo motivo: son del build real de escritorio,
no de este cimiento de Python) tampoco se tocaron `tauri.conf.json`/`backend.rs`. Se detalla acá
exactamente qué cambio hace falta en cada uno, para aplicarlo aparte.

### `apps/desktop/scripts/build-backend.sh` (y `.ps1`)

Sumar una llamada a `scripts/download-opencode.sh` — **sin** el `if [[ "${EDECAN_BUNDLE_OLLAMA...`
condicional que envuelve a Ollama, porque opencode no es opcional. El lugar natural es junto al
paso `[1/5]` de `build-studio-engine.sh` (antes de construir la web), con el mismo estilo de log
`==> [N/5] ...` (y renumerar los pasos siguientes).

### `apps/desktop/src-tauri/tauri.conf.json`

Sumar `"binaries/opencode"` a la lista `bundle.externalBin` (hoy solo tiene
`"binaries/edecan-local"`). A diferencia de Ollama —que se suma condicionalmente desde
`build-app.sh` vía `cargo tauri build --config <override-json>` porque es opcional—, opencode
puede ir directo en el archivo base: si no es opcional, Tauri debe exigir que exista para
cualquier build, y eso es justamente lo que loguea un `externalBin` faltante con un error claro
en tiempo de `cargo tauri build`.

### `apps/desktop/src-tauri/src/backend.rs`

Agregar el mismo patrón que ya existe para Ollama:

- Una función `resolve_opencode_sidecar() -> Option<PathBuf>` que replique
  `resolve_ollama_sidecar()` (busca `opencode`/`opencode.exe` junto al ejecutable actual, o
  cualquier `opencode-*` en `apps/desktop/src-tauri/binaries/` si el build todavía no corrió).
- Una función `with_opencode_env(cmd)` que replique `with_ollama_env(cmd)`, fijando
  `EDECAN_OPENCODE_BIN` (la variable que ya lee `ide_opencode_binario.py`,
  `VARIABLE_ENTORNO_BINARIO` en ese módulo) a la ruta resuelta.
- Sumar `with_opencode_env(...)` al mismo lugar donde `build_command` ya llama
  `with_expanded_path(with_ollama_env(cmd))` (línea ~328 de `backend.rs` a la fecha de este
  documento).

A diferencia de `EDECAN_OLLAMA_AUTOSTART` (opcional, el usuario decide), no hace falta un
"autostart" para opencode: si `EDECAN_OPENCODE_BIN` llega fijada, ya está disponible para
cuando lo necesite el motor del IDE — no hay un proceso de fondo que arrancar de antemano
(`opencode serve` lo lanza `ide_opencode.py::ServidorOpencode.iniciar` bajo demanda, no al
arrancar toda la app).

## 4. Qué pasa si falta

Con los cambios de la sección 3 aplicados, `EDECAN_OPENCODE_BIN` llega siempre fijada en una
build empaquetada — pero el código de resolución (`resolver_binario_opencode()`) nunca asume
que eso vaya a ser cierto:

- **Nadie tocó nada de empaquetado todavía** (por ejemplo, esta misma sesión, o alguien
  depurando el binario congelado sin pasar por `build-backend.sh`): no hay `_MEIPASS` con
  opencode adentro, no hay `EDECAN_OPENCODE_BIN` — el código cae al `PATH` del sistema. En esta
  Mac de desarrollo eso encuentra `~/.opencode/bin/opencode` igual, así que nada se rompe
  todavía.
- **El empaquetado se aplicó pero algo salió mal** (`download-opencode.sh` no corrió, el
  checksum no coincidió y el script abortó, `tauri.conf.json` no se actualizó): la build final
  no tiene el sidecar. Si además la máquina del cliente no tiene opencode instalado por su
  cuenta, `resolver_binario_opencode()` agota los tres escalones y lanza
  `BinarioOpencodeNoEncontrado` con un mensaje que dice, explícitamente, qué se probó y qué
  hacer — nunca un `FileNotFoundError` pelado. Ese mensaje es la primera pista para diagnosticar
  cuál de los pasos de la sección 3 quedó a medias.
- **`EDECAN_OPENCODE_BIN` llega fijada pero apunta a algo roto** (el sidecar se corrompió al
  copiar, o el script de descarga dejó un archivo incompleto): esto es un error FUERTE a
  propósito — `resolver_binario_opencode()` no cae en silencio al `PATH` del sistema en ese
  caso, porque haría invisible justo el tipo de fallo de empaquetado que este documento existe
  para poder diagnosticar. Ver el docstring de `_resolver_variable_entorno` en
  `ide_opencode_binario.py` para el razonamiento completo.

## 5. Qué se verificó de verdad al escribir este documento (y qué no)

Verificado en esta sesión, con comandos reales (no inventado):

- Los seis assets de la tabla 2.2 existen en el release `v1.17.18` real de
  `github.com/anomalyco/opencode`, con esos tamaños y SHA-256 (leídos de la API de GitHub).
- El contenido de `opencode-darwin-arm64.zip`, `opencode-windows-x64.zip` y
  `opencode-linux-x64.tar.gz` es, en los tres casos, un único ejecutable en la raíz del
  archivo (`unzip -l` / `tar tzf`) — no una carpeta, no dependencias adicionales.
- El binario dentro de `opencode-darwin-arm64.zip` (`v1.17.18`) es **byte a byte idéntico**
  (mismo SHA-256) al binario ya instalado y probado en esta Mac
  (`~/.opencode/bin/opencode`) — la cadena "este release oficial es exactamente lo que el
  dueño ya verificó que escribe de verdad en disco" queda cerrada, no es una suposición.

No verificado (queda para quien aplique los cambios de la sección 3, con `cargo`/Tauri reales):

- Que `cargo tauri build` arme un instalador funcional con `opencode` sumado a `externalBin`.
- El comportamiento real de `resolve_opencode_sidecar()`/`with_opencode_env()` en Rust (no
  escritos todavía, solo especificados arriba).
- El binario `opencode.exe` de Windows corriendo de verdad en un Windows real dentro de la app
  empaquetada — esta sesión corre en macOS, sin acceso a una máquina Windows (ver también la
  aclaración equivalente en `ide_opencode_binario.py` sobre su propia rama Windows de armado de
  argv).
- Que la versión `v1.17.18` siga siendo la recomendada para cuando esto se aplique — si pasó
  tiempo desde la fecha de este documento, vale la pena repetir la verificación de la sección 5
  contra la versión que esté vigente entonces, no asumir que este documento sigue siendo la
  última palabra.

## 6. Windows: estado real del empaquetado (agregado 31-jul-2026)

Esta sección documenta lo que se aplicó de la sección 3 en Windows, sobre un repo donde macOS ya
tenía `tauri.conf.json` (`bundle.externalBin` con `"binaries/opencode"`) y `backend.rs`
(`resolve_opencode_sidecar()`/`with_opencode_env()`) resueltos — ver esos archivos, no este
documento, para el estado exacto de esa parte, que es la misma en las tres plataformas. Lo que
faltaba y se resolvió acá son los tres scripts `.ps1` del lado de Windows.

### 6.1 `scripts/download-opencode.ps1` (nuevo)

Espejo de `download-opencode.sh`, con la misma tabla de versión/SHA-256 de la sección 2.2 de
este documento (`VERSION="1.17.18"`, sin usar `latest`). Mismo criterio de salida temprana que
la versión bash: si `binaries\opencode-<target>.exe` ya existe y su marca (`.opencode-<target>.sha256`)
coincide con el SHA esperado, no vuelve a descargar. El SHA-256 del asset se verifica con
`Get-FileHash` **antes** de extraer nada — igual que en macOS/Linux, un binario que se va a
firmar y distribuir no se acepta por su nombre de archivo. Acepta `-Target` para
`x86_64-pc-windows-msvc` (default, el único que hoy soporta el resto del pipeline) o
`aarch64-pc-windows-msvc` (en la tabla por si algún día hay instalador Windows ARM64).

### 6.2 `scripts/build-backend.ps1` (ya existía; se le sumó el paso de opencode)

Antes de este cambio, `build-backend.ps1` **nunca llamaba a ningún script de opencode** — a
diferencia de `build-backend.sh`, que sí lo hacía siempre desde antes. Se agregó el mismo paso
`[0/5]` (misma numeración que la versión bash, que también le deja "0/5" tanto a Ollama como a
opencode aunque uno sea opcional y el otro no) que llama a `download-opencode.ps1` sin ninguna
variable de entorno que lo active, justo antes de `build-studio-engine.ps1`.

### 6.3 `scripts/build-app.ps1` (bug real encontrado y corregido)

**Este script ya existía y ya reproducía, tal cual, la misma trampa documentada en el
comentario largo de `build-app.sh`**: su `$ExternalBin` base era
`@("binaries/edecan-local", "binaries/fydesign-node")` — sin `"binaries/opencode"`. Como
`cargo tauri build --config <override>` **reemplaza** `bundle.externalBin` entero (no lo
extiende), aunque `tauri.conf.json` sí trae `"binaries/opencode"` en su lista base, este script
lo habría sacado del build en silencio: `cargo tauri build` termina con éxito, el instalador se
genera, y el `.exe`/NSIS/MSI resultante **no** habría traído el motor del IDE. Ningún test ni
build real lo detectó todavía porque este script nunca corrió contra una máquina Windows real
(ver docs/edecan-windows.md y la fase anterior de medición en la VM, que nunca llegó a
`cargo tauri build`).

Corrección aplicada: `$ExternalBin` ahora incluye `"binaries/opencode"` siempre (no
condicionado a ninguna variable, igual que en macOS/Linux).

Se sumó además la comprobación post-build que sí tenía `build-app.sh` y que a este script
todavía le faltaba por completo. La diferencia con macOS es **dónde** se verifica: macOS
inspecciona `Contents/MacOS/` dentro del `.app` ya armado; Windows con NSIS/MSI no deja un
directorio de "app instalada" navegable así antes de ejecutar el instalador. En su lugar, se
verifica `src-tauri\target\release\<nombre-sin-target-triple>.exe` — el directorio donde
`cargo tauri build` copia cada sidecar de `externalBin` (con el sufijo de target triple ya
recortado) **antes** de invocar al bundler de NSIS/MSI, y que es justo el archivo que ese
bundler empaqueta después. Esto no se asumió de memoria: se confirmó contra un reporte real en
el issue tracker de Tauri (`tauri-apps/tauri#15134`, "Tauri copies the sidecar to
`src-tauri/target/release/<name>.exe` (stripping the target triple)" — el mismo autor lo
verificó al depurar por qué un sidecar quedaba obsoleto). Si esta ruta cambiara en una versión
futura de `tauri-cli` (hoy fijada en `2.11.4` en este mismo script), la comprobación fallaría de
forma ruidosa (`Test-Path` da `$false`, el script hace `throw`) en vez de fallar en silencio —
que es justo el defecto que esta comprobación existe para prevenir.

**No se pudo ejecutar `cargo tauri build` de verdad en esta sesión** (sin máquina Windows) —
la fase de validación en la VM debe correr `apps\desktop\scripts\build-app.ps1` completo y
confirmar en la salida la línea `==> Sidecars verificados en ...: edecan-local.exe
fydesign-node.exe opencode.exe` (y `ollama.exe` si `EDECAN_BUNDLE_OLLAMA=1`), y además abrir el
NSIS/MSI generado para confirmar que el motor arranca de verdad — la comprobación de este
script solo garantiza que Tauri vio los binarios antes de empaquetar, no que el instalador final
los conserve intactos (mismo tipo de bug que documenta `tauri-apps/tauri#15134` para reinstalos
sobre una versión vieja cacheada).

### 6.4 Node 22 vs. Node 24 de la VM: decisión y justificación

La VM (`C:\edecan`, ver estado medido por la fase anterior) tiene Node v24.18.1 instalado
globalmente. `build-backend.ps1` (sin tocar en este encargo, ya estaba así) exige Node 22 exacto
y aborta si no lo encuentra. **Se decidió NO relajar ese requisito.** No es una preferencia de
estilo: `apps/web/package.json` y `packages/fydesign-engine/package.json` fijan
`"engines": {"node": ">=22 <23", ...}` y `">=22"` respectivamente — el propio proyecto declara
que Node 22 es lo soportado, no un capricho del script de build. Construir la web estática con
Node 24 sería una build no reproducible: ni siquiera es seguro que `npm run build` (Next.js
15.5.21) se comporte igual, y el objetivo de este empaquetado es justamente evitar sorpresas
"funciona distinto según qué Node encontró el script".

Lo que sí falta, y no se resolvió en este encargo porque exige tocar la VM (regla dura: un solo
agente a la vez sobre la VM, y esta fase no es de VM): **instalar Node 22 en la VM sin desinstalar
el Node 24 que ya tiene**, usando un gestor de versiones. Se confirmó por búsqueda web (no por
memoria) que la recomendación vigente de Microsoft para Windows es **nvm-windows**
(`coreybutler/nvm-windows`), con **fnm** (Fast Node Manager, Rust, instalable por `winget`) como
alternativa moderna más rápida para cambiar de versión. Ver
[Set up Node.js on native Windows — Microsoft Learn](https://learn.microsoft.com/en-us/windows/dev-environment/javascript/nodejs-on-windows).
Comando exacto para la fase de validación en la VM (no ejecutado por esta sesión):

```powershell
# Instalar nvm-windows (una vez), luego:
nvm install 22.17.0
nvm use 22.17.0
node --version   # debe imprimir v22.17.0 antes de correr build-backend.ps1/build-app.ps1
```

`build-studio-engine.ps1` no depende de esto: ya descarga su propio Node 22.17.0 portátil
(`$NodeVersion = "22.17.0"` en ese script) para el sidecar de FyDesign, aislado del Node global
de la máquina. El requisito de Node 22 global es solo para el paso `npm run build` de
`apps/web` dentro de `build-backend.ps1`.

## Ver también

- `apps/companion/edecan_companion/ide_opencode_binario.py` — resolución del binario en tiempo
  de ejecución (el código que consume todo lo que este documento describe).
- `apps/companion/edecan_companion/ide_opencode.py` — el adaptador contra la API HTTP de
  `opencode serve` una vez que ya se tiene una ruta al binario.
- `docs/desktop.md` §10 — el mismo patrón (sidecar de Tauri + variable de entorno) ya aplicado
  para Ollama, con el detalle completo de cómo se activa y cómo se apaga.
- `docs/desktop-local.md` — ciclo de vida del backend local congelado, apagado prolijo.
