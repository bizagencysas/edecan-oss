# Edecán en Windows — instalación, datos y problemas comunes

Esta guía es para vos, que vas a usar Edecán en tu PC con Windows como asistente
principal — para charlar y, sobre todo, para el IDE. Cubre requisitos,
instalación paso a paso, dónde quedan tus datos, cómo desinstalar y qué hacer
si algo falla. Para el wizard de bienvenida y la pantalla de Configuración una
vez instalado, ver [`primeros-pasos.md`](./primeros-pasos.md); para el detalle
técnico completo de la app de escritorio en las tres plataformas, ver
[`desktop.md`](./desktop.md).

> **Léelo antes de instalar — el estado real del IDE hoy.** Este documento no
> repite afirmaciones sin haberlas visto correr. El shell nativo (instalador,
> splash, backend local, chat) ya se compila y se prueba en Windows x64 en
> cada cambio (job `desktop-windows` de CI). El terminal del IDE cambió de
> estado desde la primera versión de esta guía: ya NO cae a un respaldo de
> *pipes* planos — `apps/companion/edecan_companion/pty_compat.py` reemplazó
> ese camino con una implementación completa de ConPTY vía `pywinpty`
> (prompt interactivo, Ctrl+C, redimensionado, matar el árbol de procesos con
> `taskkill /T`). Pero sigue siendo **código nunca ejecutado en Windows real**
> — está escrito y razonado contra la documentación pública de `pywinpty` y de
> ConPTY, con los supuestos sin confirmar listados en el propio docstring de
> la clase (`_WindowsPTY` en `pty_compat.py`), no verificado con una máquina
> Windows delante. La causa, la decisión tomada y el plan completo están en
> [`edecan-windows.md`](./edecan-windows.md) — léelo si el IDE es tu caso de
> uso principal: sos vos quien va a generar la primera evidencia real de que
> esto funciona (o de que no), simplemente usándolo. El resto de esta guía
> (chat, instalación, datos) ya corre en Windows real y así se documenta.

## 1. Requisitos

| Qué | Mínimo | Nota |
|---|---|---|
| Sistema operativo | Windows 10 x64, versión 1809 (build 17763) o superior | En la práctica: Windows 10 22H2 o Windows 11. Es el piso que exige ConPTY para el terminal (§ arriba); versiones anteriores de Windows 10 no están soportadas. |
| Arquitectura | x64 | No hay build ARM64; en un Windows ARM el binario x64 corre bajo emulación pero no es la vía soportada ni probada. |
| WebView2 Runtime | Presente | Ya viene de fábrica en Windows 11 y en Windows 10 con actualizaciones recientes. Si falta, el instalador NSIS de Tauri lo agrega — comportamiento por defecto de Tauri v2, **pendiente de confirmar en una máquina realmente limpia** (anotalo si te toca instalar en una PC sin WebView2 y contanos qué viste). |
| Espacio en disco | Unos cuantos cientos de MB | El instalador incluye el backend, la web y (si el build lo empaquetó) el motor de FyDesign con Node y Chromium propios — no necesitás instalar Node, npm ni Chrome vos. |
| Cuenta de usuario | Sin privilegios de administrador para el uso diario | El NSIS instala por-usuario (`installMode: currentUser`), sin UAC. Sí hace falta una consola de PowerShell como administrador una única vez, para el paso opcional de rutas largas (§1.1). |
| PowerShell 7 (`pwsh`) | Opcional pero recomendado | El IDE abre terminales con `pwsh.exe` si está en PATH, y si no cae a `powershell.exe` (Windows PowerShell 5.1, que siempre está presente) y por último a `cmd.exe`. Instalalo con `winget install Microsoft.PowerShell` si querés la terminal más moderna. |

### 1.1 Un ajuste de una sola vez (opcional, recomendado si vas a usar el IDE con rutas de proyecto largas)

Windows limita las rutas a 260 caracteres salvo que actives soporte de rutas
largas a nivel de sistema. Edecán ya evita anidar sus propios datos en rutas
largas, pero si tu carpeta de proyectos vive varios niveles adentro (por
ejemplo `OneDrive\Trabajo\Clientes\...\repo-con-nombre-largo`), conviene
activarlo una vez, en PowerShell **como administrador**:

```powershell
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name LongPathsEnabled -Value 1
```

No es obligatorio para instalar ni para chatear; solo evita errores raros de
"nombre de ruta demasiado largo" al abrir proyectos anidados en el IDE.

## 2. Instalación paso a paso

Tenés dos caminos, según de dónde saliste el instalador.

### 2.1 Ya tenés `Edecán-Setup.exe` (instalador NSIS)

Es el instalador recomendado — instala por-usuario, sin pedir permisos de
administrador, y es el formato con el que mejor trabaja el actualizador
automático de Edecán.

1. Doble clic en `Edecán-Setup.exe`.
2. **SmartScreen puede avisar "Windows protegió tu PC"** la primera vez si el
   binario todavía no acumuló reputación con Microsoft (pasa incluso con
   binarios firmados recién publicados — ver § Troubleshooting). Hacé clic en
   **"Más información"** y después **"Ejecutar de todas formas"**.
3. Seguí el asistente ("siguiente, siguiente"). No pide UAC porque instala
   solo para tu usuario.
4. Al abrir por primera vez ves una ventana de splash ("Arrancando tu
   asistente…") mientras el backend local prepara su base de datos embebida y
   corre migraciones — tarda segundos, no minutos. Si se queda ahí más de un
   minuto, abrí **"Ver detalle técnico"** en esa misma ventana: muestra el
   log en vivo del backend.
5. La app abre directo en el wizard de bienvenida (2–3 pasos: conectar un
   proveedor de LLM y listo). Recorrido completo en
   [`primeros-pasos.md`](./primeros-pasos.md).

Alternativa administrada: si tu organización despliega por GPO/Intune, existe
también un `.msi` — mismo contenido, otro empaquetado. Para uso personal en tu
propia PC, preferí el NSIS.

### 2.2 No tenés el instalador todavía: compilarlo vos en tu propia PC

Si partiste del repositorio en vez de un instalador ya armado, generás el
`.exe` en tu propia máquina Windows x64. Necesitás, además de lo de §1:

- **Rust** estable (`rustup`) + `cargo-tauri` fijado:
  ```powershell
  cargo install tauri-cli --version '2.11.4' --locked
  ```
- **Visual Studio Build Tools (C++)** — lo pide el propio instalador de Rust
  para Windows si falta; es el toolchain de MSVC que usa `cargo`.
- **Node.js 22** y **npm 10** (compila la interfaz web).
- **Python 3.12** + [`uv`](https://docs.astral.sh/uv/), con el workspace
  completo sincronizado: `uv sync --all-packages --frozen` desde la raíz del
  repo (no `uv sync` a secas — el backend descubre las herramientas del
  agente vía entry points de todos los paquetes `edecan_*`).

Con eso instalado:

```powershell
cd apps\desktop
.\scripts\build-app.ps1
```

El script arma la web estática, congela el backend con PyInstaller y produce
los instaladores en `src-tauri\target\release\bundle\`:

- `nsis\Edecán_<versión>_x64-setup.exe` — el de §2.1.
- `msi\Edecán_<versión>_x64.msi`.

Después, corré el smoke real (instala en un perfil efímero, arranca la app
instalada, espera que el backend real conteste, cierra y exige cero procesos
huérfanos):

```powershell
.\scripts\verify-windows-bundles.ps1
```

Si ese script termina sin errores, tenés evidencia real —no solo un build que
compiló— de que el instalador funciona en tu máquina. Detalle completo,
variables opcionales (por ejemplo `EDECAN_BUNDLE_OLLAMA=1` para incluir Ollama
en el instalador) y requisitos de compilación de las otras plataformas: ver
[`desktop.md`](./desktop.md) §3–4 y [`apps/desktop/README.md`](../apps/desktop/README.md).

## 3. Dónde viven tus datos

La app de escritorio le pasa siempre una carpeta de datos explícita al
backend — nunca la adivina. En Windows es:

```
%APPDATA%\cc.edecan.desktop\data\
```

que en tu usuario típicamente resuelve a algo como
`C:\Users\<vos>\AppData\Roaming\cc.edecan.desktop\data\`. Ahí vive todo: la
base de datos embebida (conversaciones, memoria, credenciales cifradas) y los
archivos que subís desde el chat.

No hace falta memorizar la ruta: el icono de Edecán en la bandeja del sistema
(junto al reloj) tiene la opción **"Ver carpeta de datos"**, que la abre
directo en el Explorador de Windows.

Aparte de eso, el WebView2 que renderiza la ventana guarda su propio caché y
cookies en la ubicación estándar de Windows para ese componente — es contenido
regenerable (no tus datos), así que no hace falta respaldarlo.

Nota para quien use el CLI (`edecan`) suelto en vez de la app instalada
(uso de desarrollo, no el caso normal): usa por defecto
`~/.edecan/data`, es decir `C:\Users\<vos>\.edecan\data`, salvo que le pases
`--data-dir` vos mismo — la misma ruta relativa al home que en macOS/Linux, a
propósito, para que la documentación no diverja por plataforma.

## 4. Edecán residente en la bandeja del sistema

Cerrar la ventana principal (la roja) no apaga a Edecán: la oculta y deja el
backend local y el acceso desde tu teléfono corriendo. El icono queda en la
bandeja del sistema. Clic derecho abre un menú corto: **Abrir Edecán**, **Abrir
en el navegador**, **Ver carpeta de datos** y **Salir completamente**. Solo
esta última acción termina de verdad el proceso y apaga el backend de forma
prolija — es la que tenés que usar si vas a, por ejemplo, reinstalar o mover
la carpeta de datos.

Desde **Ajustes → Permisos de esta computadora**, dentro de la app, Windows
solo expone páginas nativas de Micrófono y Notificaciones (Windows no tiene un
permiso global de mouse/teclado/captura como macOS; usa UAC puntual para
acciones administrativas en su lugar).

## 5. Cómo desinstalar

**Configuración → Aplicaciones → Aplicaciones instaladas → Edecán →
Desinstalar** (el instalador NSIS registra un desinstalador estándar de
Windows; también aparece en el Panel de control clásico si lo preferís).

Desinstalar **no borra tus datos** — la carpeta de §3 queda intacta a
propósito, para que una reinstalación posterior no pierda nada. Si además
querés borrar todo tu historial y credenciales guardadas, borrá esa carpeta a
mano (usá "Ver carpeta de datos" en la bandeja mientras la app todavía esté
instalada, así no tenés que buscar la ruta).

## 6. Resolución de problemas

**SmartScreen dice "Windows protegió tu PC" / "Editor desconocido".**
Esperable mientras el binario no acumuló reputación con Microsoft, incluso
firmado — no hay equivalente a la notarización de Apple que apruebe binarios
por adelantado. Clic en "Más información" → "Ejecutar de todas formas". Si el
instalador está firmado con Authenticode (certificado EV clásico), la
advertencia suele desaparecer casi de inmediato; con certificados OV o firma
gestionada la reputación tarda semanas de telemetría de instalaciones en
acumularse — ver el detalle de la decisión de firma en
[`edecan-windows.md`](./edecan-windows.md) §5.

**El antivirus pone en cuarentena el instalador o lo bloquea.**
Frecuente en binarios de PyInstaller sin firmar, incluso más si llevan
compresión UPX (Edecán la deja apagada a propósito para reducir esto). Si tu
antivirus corporativo bloquea la instalación por completo, agregá una
excepción para la carpeta de instalación o para `%APPDATA%\cc.edecan.desktop`,
o instalá una build firmada si tenés una disponible.

**El backend local tardó más de 60 segundos / la app se queda en el splash.**
Abrí "Ver detalle técnico" en la propia ventana de splash — muestra el
stdout/stderr del sidecar en vivo. Si termina en error, aparece un panel rojo
con las últimas líneas de log y un botón "Reintentar" (repite el arranque sin
cerrar la app).

**Falta WebView2 y la ventana no llega a abrir.**
Descargá el instalador oficial de Microsoft ("Evergreen Bootstrapper") desde
la documentación de WebView2 e instalalo; después reabrí Edecán. En teoría el
propio NSIS ya lo instala si detecta que falta (comportamiento por defecto del
instalador de Tauri v2), pero si tu máquina viene muy pelada y ves este
síntoma, contanos qué versión de Windows era — es justo el caso que todavía no
se comprobó en una máquina limpia real (ver §1).

**El terminal del IDE no muestra un prompt interactivo, o un programa dice
que no hay TTY, o Ctrl+C no corta el proceso.**
El código de la terminal (ConPTY vía `pywinpty`, en
`apps/companion/edecan_companion/pty_compat.py`) ya no es un respaldo por
*pipes* planos, pero tampoco se ha visto correr en un Windows real todavía —
si te pasa esto, sos la primera evidencia de que algo de ese código no se
comporta como se razonó (por ejemplo, si `pywinpty` crea el proceso hijo con
`CREATE_NEW_PROCESS_GROUP`, Ctrl+C dejaría de llegarle aunque el resto
funcione — es justamente el supuesto sin confirmar que cita el docstring de
`_WindowsPTY`). Contanos qué viste (versión de Windows, si `pywinpty` se
instaló sin error) — es exactamente el dato que falta en
[`edecan-windows.md`](./edecan-windows.md) §1. Mientras tanto, para tareas de
terminal donde necesites de verdad un TTY, abrí tu propia ventana de
PowerShell fuera de Edecán.

**Después de cerrar una sesión de terminal del IDE, sigue vivo un proceso
`node.exe` (o similar) que arrancaste ahí (por ejemplo `npm run dev`).**
El cierre de una terminal en Windows ya usa `taskkill /T` (mata el árbol
completo, no solo el proceso raíz) con un martillo `/T /F` a los 3 segundos si
no cerró solo — pero, igual que el resto de esta sección, es código nunca
ejecutado en una PC Windows real. Si ves un huérfano así, es la primera señal
de que ese mecanismo necesita ajuste; mientras tanto cerralo a mano desde el
Administrador de tareas (ver el plan en
[`edecan-windows.md`](./edecan-windows.md) §1.2 y §7, fase F1/F3 para
Job Objects como respaldo anti-huérfanos).

**Falla renombrar/restaurar un archivo con "el proceso no puede tener acceso
al archivo porque está siendo utilizado por otro proceso" (a menudo con
OneDrive sincronizando tu carpeta de proyecto, o con un antivirus con
indexador activo).**
Es un comportamiento propio de Windows (a diferencia de macOS/Linux, que
permiten reemplazar un archivo abierto). El sospechoso número uno es el
indexador de tu antivirus o el cliente de sincronización de OneDrive
enganchado al archivo en el momento exacto de la operación — probá pausar la
sincronización de esa carpeta puntualmente y reintentar. Este caso todavía no
tiene reintentos automáticos en el companion (llegan en la fase F2 del plan,
ver [`edecan-windows.md`](./edecan-windows.md) §2 y §7).

**Un nombre de archivo o carpeta se rechaza con un error sobre caracteres o
nombres reservados.**
Windows prohíbe ciertos caracteres (`< > : " | ? *`) y nombres reservados
(`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, con o sin
extensión). Edecán valida esto al crear archivos desde el IDE — el mensaje de
error nombra el carácter o nombre problemático; renombralo y listo.

**Ruta demasiado larga.**
Ver §1.1 (`LongPathsEnabled`). Si ya lo activaste y el error persiste,
probá mover el proyecto a una ruta más corta (por ejemplo `C:\dev\` en vez de
varios niveles bajo `OneDrive\...`).

**No sé qué puerto está usando el backend.**
No debería importarte en el uso normal: la app prueba primero el `8765` y, si
está ocupado, le pide uno libre a Windows automáticamente — nunca falla por
esto. Para verlo igual, abrí "Ver detalle técnico" en el splash (imprime
`EDECAN_LOCAL_READY port=<p>`) o el menú de bandeja → "Abrir en el navegador"
(siempre apunta al puerto vigente).

## 7. Qué esperar hoy vs. qué falta (resumen honesto)

| Funciona hoy en Windows, con evidencia real de CI | Escrito y razonado, pero NUNCA ejecutado en Windows real | Falta directamente |
|---|---|---|
| Instalación, splash, backend local, chat, wizard de bienvenida | Terminal interactivo del IDE vía ConPTY/`pywinpty` (`pty_compat.py`), incluido matar el árbol de procesos con `taskkill /T` | `edecan doctor` / diagnóstico integrado |
| Actualizaciones automáticas firmadas | Reintentos automáticos ante archivos bloqueados por antivirus/OneDrive (`platform_paths.reemplazar_con_reintentos`, ya cableado en el guardado de sesiones/archivos/workspaces del IDE) | Firma Authenticode del instalador (mitiga SmartScreen) |
| Cierre limpio del backend al "Salir completamente" | Selección de shell por defecto pwsh → powershell → cmd | Job Objects como respaldo anti-huérfanos (fase F3) |
| Rutas y nombres de archivo validados contra las reglas de Windows | | |

El detalle técnico completo de cada fila —por qué, la decisión tomada y el
plan para cerrar cada brecha— está en
[`edecan-windows.md`](./edecan-windows.md). Esa lista se actualiza a medida
que cada fase (F0–F3) se compruebe de verdad en Windows, no en macOS.

## Ver también

- [`edecan-windows.md`](./edecan-windows.md) — documento de gobierno técnico:
  arquitectura del terminal (ConPTY/`pywinpty`), rutas, procesos, sandbox,
  empaquetado/firma, CI y el plan de fases con checklist ejecutable.
- [`desktop.md`](./desktop.md) — la app de escritorio en las tres plataformas:
  arquitectura, requisitos de compilación completos, firma de código.
- [`primeros-pasos.md`](./primeros-pasos.md) — wizard de bienvenida y pantalla
  de Configuración, paso a paso.
- [`ide.md`](./ide.md) — qué es y cómo funciona el IDE de Edecán en general
  (independiente de plataforma).
- [`apps/desktop/README.md`](../apps/desktop/README.md) — referencia técnica
  rápida del paquete `apps/desktop`.
