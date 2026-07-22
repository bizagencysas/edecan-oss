# App de escritorio (Tauri)

La aplicación local de Edecán es instalable en macOS, Windows y Linux x64: se descarga, se instala, se abre, se conecta con tus propias credenciales en Configuración y queda funcionando — sin servidor propio, sin Docker y sin editar archivos a mano. Este documento cubre instalación, requisitos y build por plataforma, ubicación de datos, desinstalación y troubleshooting. Para el wizard de bienvenida y la pantalla de Configuración, ver [`primeros-pasos.md`](./primeros-pasos.md). Para el backend empaquetado (Postgres embebido, migraciones y colas), ver [`desktop-local.md`](./desktop-local.md).

Código fuente de este paquete: [`apps/desktop`](../apps/desktop) (referencia técnica rápida en su propio `README.md`).

## 1. Arquitectura en 60 segundos

```
apps/desktop (Tauri, Rust) — el shell nativo:
  1. arranca → elige puerto libre (preferencia 8765)
  2. muestra la ventana de splash (HTML embebido, "Arrancando tu asistente…")
  3. lanza edecan_local como sidecar:
       edecan-local --port <P> --data-dir <carpeta de datos de la app>
  4. espera la línea "EDECAN_LOCAL_READY port=<P>" en su stdout (máx. 60s)
  5. abre la ventana principal → http://127.0.0.1:<P>/  y cierra el splash
  6. macOS: cerrar `main` la oculta y deja backend/túnel vivos en la barra
  7. al elegir "Salir completamente" (o Cmd+Q): mata el sidecar SIEMPRE

        │
        ▼ el sidecar sirve, en el mismo origen (http://127.0.0.1:<P>/):

  apps/local (Python, fase v3)        apps/web exportado estático
  API + worker + Postgres embebido,    (Next.js, NEXT_OUTPUT=export,
  todo local                            lo sirve el propio backend en "/")
```

Ni la interfaz ni el backend se reescriben para la versión de escritorio: `apps/desktop` es pura orquestación nativa alrededor de piezas que ya existen. Contrato completo del backend local: `ARCHITECTURE.md` §12.f.

## 2. Instalación (para quien solo usa la app)

En macOS, si recibiste el repositorio en vez del DMG, haz doble clic en
**`Abrir Edecán.command`** en la raíz. La primera vez prepara e instala
`~/Applications/Edecán.app`; después solo abre la app. El proceso que macOS
ve y autoriza es `Edecán`/`edecan-local`, no el intérprete compartido
`python3.x`, evitando que sus permisos se mezclen con Jarvis u otro proyecto.

1. Descargá el instalador de tu plataforma y abrilo.
   - **macOS**: `Edecán.dmg` → arrastrá `Edecán.app` a Aplicaciones. Como el `.dmg` de este repo sale **sin firmar** por defecto (ver §7), macOS va a bloquear la primera apertura ("no se puede abrir porque no se puede verificar el desarrollador"): hacé **clic derecho (o Control-clic) sobre la app → Abrir → Abrir** de nuevo en el diálogo de confirmación. Solo hace falta esa vez.
   - **Windows**: `Edecán-Setup.exe` (NSIS) → siguiente, siguiente. SmartScreen puede avisar "Windows protegió tu PC" la primera vez (mismo motivo: sin firmar) — **Más información → Ejecutar de todas formas**.
   - **Debian/Ubuntu x64**: abrí el paquete `.deb` con el centro de software e instalalo. También podés usar `sudo apt install ./ruta/al/paquete.deb`.
   - **Fedora/openSUSE x64**: abrí el paquete `.rpm` con el instalador gráfico de tu distribución, o instalalo con `sudo dnf install ./ruta/al/paquete.rpm` en Fedora.
   - **Otras distribuciones Linux x64**: hacé clic derecho sobre el `Edecán_*.AppImage` → Propiedades → permitir ejecutar como programa, y después doble clic. Si tu escritorio no ofrece esa opción: `chmod +x Edecán_*.AppImage` una sola vez.
2. Al abrir por primera vez ves la ventana de splash ("Arrancando tu asistente…") mientras el backend local termina de prepararse (crea su base de datos embebida, corre migraciones) — tarda unos segundos, no minutos.
3. La app abre directo en el wizard de bienvenida (2–3 pasos: conectar un proveedor de LLM y listo) — recorrido completo en [`primeros-pasos.md`](./primeros-pasos.md).

Nada de esto pide un `.env`, una terminal ni una base de datos propia — ver §9.

### Edecán residente en macOS

Cerrar la ventana roja no apaga al asistente: oculta la ventana principal y
mantiene vivos el backend local, el acceso móvil/túnel y la escucha que la
persona haya habilitado. Un clic izquierdo en el icono de Edecán en la barra
de menú vuelve a mostrar y enfocar la ventana. El clic secundario abre un menú
corto con el estado **Edecán activo**, **Abrir Edecán**, **Abrir en el
navegador**, **Ver carpeta de datos**, **Escucha siempre** y **Salir
completamente**. Solo esta última acción (o Cmd+Q) termina el proceso y ejecuta
el apagado grácil del sidecar.

Windows y Linux mantienen su semántica histórica de cierre completo cuando la
escucha continua está apagada; si está activa, cerrar también oculta la ventana
para que esa función pueda seguir trabajando.

### Centro de permisos

En la aplicación instalada, abre **Ajustes → Permisos de esta computadora**.
La pantalla consulta estados nativos y ofrece una acción por capacidad:

- En macOS comprueba Accesibilidad y Grabación de pantalla; puede disparar
  el consentimiento nativo de pantalla y micrófono, y abre directamente las
  secciones de Accesibilidad, Notificaciones, Automatización o Acceso total
  al disco cuando Apple exige que la persona active el interruptor.
- Muestra la ruta exacta de la aplicación y ofrece **Mostrar Edecán en Finder**.
- Activa una sola vez **Edecán residente**: en los siguientes inicios de sesión
  arranca oculto en la barra de menú, mantiene disponible el backend para el
  teléfono y respeta si la persona lo desactiva después desde Ajustes.
- Mantiene una sola instancia: volver a abrir Edecán recupera la ventana
  residente en vez de crear otro backend, puerto o túnel.
  Si macOS presenta un botón `+`, la persona selecciona `Edecán.app`; nunca
  necesita adivinar entre Python, Terminal, Jarvis u otro ejecutable.
- En Windows abre las páginas exactas de Micrófono y Notificaciones. Mouse,
  teclado, captura y archivos normales no tienen un permiso global; Windows
  conserva UAC para cualquier acción administrativa puntual.

La pantalla nunca simula que concedió un permiso. El sistema operativo toma
la decisión final y Edecán actualiza el estado al recuperar el foco o pulsar
**Actualizar estados**. Acceso total al disco aparece como opcional porque no
es necesario para el chat ni debe pedirse por defecto.

## 3. Requisitos para compilar

Solo hacen falta si vos mismo vas a **generar** el instalador (no para usarlo ya generado):

| Herramienta | Para qué | Notas |
|---|---|---|
| **Rust** estable (`rustup`) + `cargo-tauri` | El shell nativo | CLI fijado: `cargo install tauri-cli --version '2.11.4' --locked` |
| **Node.js 22** y **npm 10** | Build estático de `apps/web` | Coincide con `apps/web/package.json` |
| **Python 3.12** + [`uv`](https://docs.astral.sh/uv/) | `edecan_local` (backend) y su empaquetado | El workspace uv completo del repo, no un venv aislado |
| **PyInstaller 6.21.0** | Congela `edecan_local` en un binario | **No hace falta instalarlo a mano**: está fijado en el grupo `release` de `pyproject.toml` y resuelto por `uv.lock` |
| macOS: Xcode Command Line Tools | `sips`/`iconutil` (`scripts/make-icons.sh`) | Ya presentes en cualquier Mac con Xcode CLT instalado |
| Windows: Visual Studio Build Tools (C++) | Requisito estándar de compilar con MSVC | El instalador de Rust para Windows ya te lo pide si falta |
| Linux x64: WebKitGTK 4.1 y herramientas de paquetes | Webview nativo y AppImage/deb/rpm | Comando reproducible debajo; ARM64 usa self-hosting con una base externa |

En Debian/Ubuntu 22.04+ instalá las dependencias Linux con:

```bash
sudo apt-get update
sudo apt-get install --yes \
  build-essential curl file gstreamer1.0-libav gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good libasound2-dev libayatana-appindicator3-dev \
  libfuse2 librsvg2-dev libssl-dev libwebkit2gtk-4.1-dev libxdo-dev patchelf pkg-config \
  openbox rpm wget wmctrl xvfb dbus-x11
```

`libfuse2` permite ejecutar AppImage en varias distribuciones. Los paquetes
GStreamer se incluyen al crear el AppImage para que imágenes, audio y video del
Mega Chat no dependan por completo de los plugins multimedia del host.

## 4. Build paso a paso

### 4.1 macOS

```bash
cd apps/desktop
./scripts/build-app.sh
```

Internamente: construye `apps/web` en modo export estático → congela `edecan_local` con PyInstaller (onefile) → copia ambos donde Tauri los espera → `cargo tauri build`. Salida en `src-tauri/target/release/bundle/`:

- `dmg/Edecán_<version>_<arch>.dmg` — el instalador para repartir.
- `macos/Edecán.app` — la app suelta (útil para probar sin montar el dmg).

Sin firmar por defecto (§7). Requiere macOS 11+ para correr (`tauri.conf.json` → `bundle.macOS.minimumSystemVersion`).

### 4.2 Windows

```powershell
cd apps\desktop
.\scripts\build-app.ps1
```

`build-app.ps1` valida Windows x64, Node/npm y la versión fijada del CLI,
ejecuta web+PyInstaller mediante `build-backend.ps1` y luego arma los bundles.
Salida en `src-tauri\target\release\bundle\`:

- `nsis\Edecán_<version>_<arch>-setup.exe` — instalador NSIS (el recomendado; instala por-usuario, sin pedir admin — `tauri.conf.json` → `bundle.windows.nsis.installMode: "currentUser"`).
- `msi\Edecán_<version>_<arch>.msi` — alternativa MSI, útil si tu organización despliega por GPO/Intune.

### 4.3 Linux

En una máquina Linux x64 con los requisitos de §3:

```bash
cd apps/desktop
./scripts/build-app.sh
```

El script construye la web, congela el backend local con PyInstaller y genera
tres formatos en `src-tauri/target/release/bundle/`:

- `appimage/Edecán_<version>_amd64.AppImage` — portable y recomendado para
  distribuciones que no usan Debian o RPM.
- `deb/Edecán_<version>_amd64.deb` — instalación integrada para Debian/Ubuntu.
- `rpm/Edecán-<version>-1.x86_64.rpm` — instalación integrada para
  Fedora/openSUSE y otras distribuciones RPM.

Después del build, `./scripts/verify-linux-bundles.sh` inspecciona los paquetes,
arranca el AppImage en Xvfb, espera el `/healthz` del backend empaquetado, exige
una ventana visible y confirma que el cierre no deja `edecan-local` huérfano.
Ese mismo smoke test corre en GitHub Actions sobre Ubuntu 22.04 en cada cambio.

La app local-first empaquetada requiere Linux x64 porque `pgserver` publica el
Postgres embebido para esa arquitectura. Linux ARM64 sigue soportado mediante
[`self-hosting.md`](./self-hosting.md) con `EDECAN_DATABASE_URL` apuntando a
Postgres externo (ver [`desktop-local.md`](./desktop-local.md) §6).

### 4.4 Modo desarrollo

```bash
cd apps/desktop
./scripts/dev.sh
```

`dev.sh` no corre PyInstaller: genera o reutiliza `apps/web/out`, arranca
`cargo tauri dev` y ejecuta el backend desde fuente con
`uv run --all-packages edecan`. Usa
`EDECAN_REBUILD_WEB=1 ./scripts/dev.sh` después de modificar el frontend, o
`EDECAN_SKIP_DEV_WEB=1 ./scripts/dev.sh` para iterar solo en Rust/backend.
Para hot reload del frontend en el navegador sigue disponible `make web`;
para reproducir el artefacto final usa el script de build de tu plataforma.

## 5. Dónde viven tus datos

Dos ubicaciones posibles — cuál aplica depende de cómo corriste el backend:

- **Corriendo por la app de escritorio (el caso normal, siempre)**: la app SIEMPRE le pasa `--data-dir` explícito al backend, apuntando a la carpeta de datos de la propia app que resuelve Tauri (`app_data_dir()` + `/data`):
  - macOS: `~/Library/Application Support/cc.edecan.desktop/data/`
  - Windows: `%APPDATA%\cc.edecan.desktop\data\` (`C:\Users\<vos>\AppData\Roaming\cc.edecan.desktop\data\`)
  - Linux: `${XDG_DATA_HOME:-~/.local/share}/cc.edecan.desktop/data/`

  Ahí vive la base de datos embebida (conversaciones, memoria, credenciales cifradas, todo) y los archivos que subas.
- **Corriendo el runtime suelto** (`uv run --all-packages edecan` o el binario `edecan-local`, sin pasar por Tauri — solo para desarrollo): usa su propio default, `DATA_DIR=~/.edecan/data` (`ARCHITECTURE.md` §12.g), salvo que también le pases `--data-dir` vos mismo.

El menú de bandeja tiene un atajo directo: **"Ver carpeta de datos"** abre la carpeta correcta en Finder, Explorador o el administrador de archivos de Linux, sin tener que recordar la ruta.

Nota aparte: la ventana principal carga `http://127.0.0.1:<puerto>/` como contenido **externo** (no un asset empaquetado de Tauri) — el motor de webview del sistema (WKWebView en macOS, WebView2 en Windows y WebKitGTK en Linux) guarda su propio caché/cookies para ese origen en la ubicación estándar del SO, separada de lo de arriba. Es contenido regenerable, así que no hace falta incluirlo en backups.

## 6. Cómo desinstalar

- **macOS**: arrastrá `Edecán.app` (en Aplicaciones) a la Papelera. No queda un ícono de desinstalador separado — así funciona cualquier `.app` de macOS.
- **Windows**: **Configuración → Aplicaciones → Aplicaciones instaladas → Edecán → Desinstalar** (el instalador NSIS registra un desinstalador estándar de Windows).
- **Linux**: desinstalá Edecán desde el mismo centro de software con el que instalaste el `.deb`/`.rpm`. Si usaste AppImage, eliminá únicamente ese archivo.

En todos los casos, **tus datos NO se borran** — desinstalar la app deja intacta la carpeta de §5 por defecto, para que reinstalar más adelante no pierda nada. Si además querés borrar tus datos por completo, borrá a mano esa carpeta (usá "Ver carpeta de datos" en la bandeja mientras la app todavía esté instalada para encontrarla rápido, o andá directo a la ruta de §5).

## 7. Firma de código

Este repo genera instaladores **sin firmar** por defecto — es el camino que funciona out-of-the-box para cualquiera que clone el repo, sin depender de un certificado de pago. Bring-your-own, igual criterio que el resto del producto (`ARCHITECTURE.md` §0):

- **macOS**: si tenés tu propio Apple Developer ID, `packaging/edecan_local.spec` ya lee `EDECAN_MACOS_CODESIGN_IDENTITY` (env var, tu `"Developer ID Application: Tu Nombre (TEAMID)"`) para firmar el binario del sidecar; para firmar el `.app`/`.dmg` final, `cargo tauri build` respeta las variables estándar de Tauri (`APPLE_SIGNING_IDENTITY`, y para notarizar además `APPLE_ID`/`APPLE_PASSWORD` o `APPLE_API_KEY` — ver la [guía oficial de firma de Tauri](https://v2.tauri.app/distribute/sign/macos/)). Sin notarizar, Gatekeeper sigue pidiendo el clic derecho→Abrir de §2 aunque esté firmado; con notarización completa, desaparece.
- **Windows**: Authenticode requiere un certificado de firma de código y la configuración de Tauri `certificateThumbprint`, `digestAlgorithm` y `timestampUrl`, o un `signCommand` que invoque tu firmador. `TAURI_SIGNING_PRIVATE_KEY` firma artefactos del updater de Tauri; **no** aplica Authenticode al `.exe`/`.msi`. Consulta la [guía oficial de firma para Windows](https://v2.tauri.app/distribute/sign/windows/). Sin firma, SmartScreen avisa la primera vez (§2).
- **Linux**: el pipeline actual crea AppImage, `.deb` y `.rpm` reproducibles pero no firma repositorios APT/RPM ni publica una clave de distribución. El smoke test comprueba contenido y ejecución; la firma y procedencia de artefactos siguen siendo un requisito para el release estable.

Ninguna identidad ni certificado real vive en este repo — solo el enganche para que vos pongas el tuyo.

## 8. Troubleshooting

**"El backend local tardó más de 60 segundos" / la app se queda en el splash.**
Abrí "Ver detalle técnico" en la propia ventana de splash — muestra el stdout/stderr del sidecar en vivo. Si termina en error, el panel rojo que aparece ya trae las últimas 50 líneas de log y un botón "Reintentar" (repite el arranque desde cero, sin cerrar la app).

**Puerto ocupado.**
No debería pasar nunca en la práctica: la app prueba primero `8765` y, si está ocupado, le pide uno libre al sistema operativo automáticamente (nunca falla por esto). Para saber qué puerto terminó usando, mirá "Ver detalle técnico" en el splash (imprime `EDECAN_LOCAL_READY port=<p>`) o abrí el menú de bandeja → "Abrir en el navegador" (siempre apunta al puerto correcto vigente).

**La ventana principal abre pero el login/chat no responde (mientras el splash sí llegó a mostrarla).**
**Resuelto (verificado 2026-07-09, fase v7)** — este punto describía un bug real de `apps/web/src/lib/api.ts` (resolvía `NEXT_PUBLIC_API_URL` con `||` en vez de `??`, así que un vacío explícito —el que usa el build de escritorio para same-origin— podía recaer en el default hardcodeado `http://localhost:8000` en vez de quedarse relativo), pero **ya no es cierto contra el código actual**: `apps/web/src/lib/api.ts` línea 39 usa `??` (`process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ?? "http://localhost:8000"`), igual que `api-configuracion.ts`/`api-mcp.ts` (que definen su propio `API_BASE_URL`), y el resto de `api-*.ts` importan ese mismo `API_BASE_URL` ya corregido desde `api.ts` — confirmado con `grep -rn "NEXT_PUBLIC_API_URL" apps/web/src/lib/*.ts`, cero ocurrencias de `||` en ese patrón en todo el árbol. No se pudo determinar en qué work package se corrigió (no hay commits que consultar, este repo no tiene `.git`), pero el código real ya no tiene el bug — si alguna vez volvés a ver este síntoma, es una regresión nueva, no este caso ya conocido.

**Antivirus/Defender/Gatekeeper marcan el instalador o lo ponen en cuarentena.**
Esperable en un binario sin firmar (§7) — es el motivo por el que `packaging/edecan_local.spec` deja la compresión UPX apagada a propósito (`upx=False`; UPX es una causa frecuente de falsos positivos en binarios de PyInstaller, incluso más que el resto). Si tu antivirus corporativo bloquea la instalación por completo: agregá una excepción para la carpeta de instalación, o firmá vos mismo el build (§7) — la firma de código es, con diferencia, lo que más baja estos falsos positivos.

**`cargo tauri` no es un comando reconocido.**
Falta el subcomando (no viene con `cargo` ni con `rustup`): `cargo install tauri-cli --version '2.11.4' --locked`.

**PyInstaller no encuentra `edecan_local`/tira `ModuleNotFoundError` para algún paquete `edecan_*`.**
`scripts/build-backend.sh`/`.ps1` corren `uv run --frozen --all-packages --group release pyinstaller packaging/edecan_local.spec` — necesitan el entorno **compartido del workspace uv completo** (todos los `packages/*`/`apps/*`, no solo `apps/local`), porque las herramientas del agente se descubren en runtime vía entry points (`edecan.tools`, `ARCHITECTURE.md` §10.7). Regenera el entorno desde la raíz con `uv sync --all-packages --frozen`; no uses `uv sync`/`uv run` sin `--all-packages`.

## 9. Filosofía: cero `.env` a mano

A diferencia de self-hosting (`self-hosting.md`, pensado para quien ya está cómodo con Docker/`.env`), la app de escritorio asume que quien la instala **no** quiere tocar archivos de configuración. Por eso:

- Ninguna credencial viaja en el instalador ni en el repo — todo se conecta desde la pantalla de **Configuración** dentro de la propia app (misma pantalla web de siempre, servida ahora por el backend local).
- **Configuración → Conexiones** reúne proveedores, voz, imágenes, búsqueda,
  cuentas externas y el QR del teléfono. Cada formulario valida antes de
  guardar y el backend cifra el secreto en el vault del tenant.
- El único paso obligatorio para poder chatear es conectar un proveedor de LLM (wizard de 2-3 pasos); todo lo demás (voz, telefonía, conectores) queda como tarjetas opcionales, nunca bloqueando el primer uso — detalle completo en [`primeros-pasos.md`](./primeros-pasos.md).
- La app detecta automáticamente `claude`/`codex`/Ollama ya instalados en tu máquina y los ofrece con un clic, sin pedir ninguna API key — tiene más sentido todavía en la app de escritorio que en cualquier otro modo, porque acá SIEMPRE hay "tu máquina" de la cual autodetectar (ver [`proveedores-llm.md`](./proveedores-llm.md)).

## 10. Ollama embebido (opcional)

Patrón de auto-provisioning adaptado de [`open-jarvis/OpenJarvis`](https://github.com/open-jarvis/OpenJarvis) (Apache-2.0, ver `NOTICE`): en vez de depender de que el cliente instale Ollama aparte, quien empaqueta la app puede **incluir el binario de Ollama directo en el instalador** — "IA local gratis, cero fricción" (prioridad del roadmap del producto). Es 100% opcional en los dos sentidos: opcional para quien empaqueta (no hace falta para que la app funcione) y opcional para quien la usa (sigue pudiendo conectar Anthropic/OpenAI/Vertex/Claude CLI/Codex CLI/su propio Ollama externo desde Configuración igual que siempre, ver [`proveedores-llm.md`](./proveedores-llm.md)).

**Para quien empaqueta un release:**

macOS:

```bash
cd apps/desktop
EDECAN_BUNDLE_OLLAMA=1 ./scripts/build-app.sh
```

Windows x64 (PowerShell):

```powershell
cd apps\desktop
$env:EDECAN_BUNDLE_OLLAMA = "1"
.\scripts\build-app.ps1
```

Linux x64 detecta Ollama, Claude CLI y Codex CLI ya instalados, pero no incluye
Ollama dentro de AppImage/deb/rpm. `EDECAN_BUNDLE_OLLAMA=1` falla de inmediato
en Linux con una explicación clara para evitar publicar un paquete incompleto.

Esas son las rutas canónicas que **descargan y realmente agregan** Ollama a
`bundle.externalBin`. Los scripts fijan Ollama `v0.32.1`, descargan el asset
oficial del release de GitHub y verifican su SHA-256 publicado antes de
extraerlo; un mismatch aborta el build. En Windows también preservan y
empaquetan el árbol `lib/ollama` del ZIP como recurso junto al ejecutable:
el CLI standalone necesita esas DLLs y helpers para funcionar. Si faltan,
el build falla cerrado. En macOS el artefacto publicado es autocontenido.
Los scripts `download-ollama.*` pueden preparar los archivos por separado,
pero no crean un instalador. Sin `EDECAN_BUNDLE_OLLAMA=1`, el instalador no
incluye Ollama.

**Cómo lo activa el usuario final:** hoy, fijando `EDECAN_OLLAMA_AUTOSTART=true` en el entorno antes de abrir la app (uso avanzado/dev). La pieza de "un solo clic" ya existe del lado de detección: `GET /v1/setup/detect` (`apps/api/edecan_api/routers/setup.py`, fase v3/`edecan_llm.detect.detect_local_providers`) ya reporta si Ollama está corriendo en `OLLAMA_BASE_URL`, y la pantalla de Configuración ya ofrece "usar Ollama" con un clic apenas lo detecta corriendo — no importa si ese Ollama lo arrancó el usuario a mano, ya estaba corriendo de antes, o lo arrancó `edecan_local.ollama_supervisor` (ver abajo) por él: para la pantalla de Configuración es indistinguible, simplemente "ya está corriendo, un clic y listo".

**Qué pasa por dentro:** cuando `EDECAN_OLLAMA_AUTOSTART` está activada, `edecan_local.ollama_supervisor.maybe_start_ollama` (dentro del backend local, `edecan_local.runtime.run()`) resuelve el binario (`EDECAN_OLLAMA_BIN`, la ruta al sidecar que el paso de arriba empaquetó — la fija automáticamente `apps/desktop/src-tauri/src/backend.rs` al lanzar el sidecar si lo encuentra — o si no, `ollama` en el `PATH`), evita lanzar un segundo proceso si ya hay uno corriendo, y lo apaga limpio al elegir **Salir completamente** o Cmd+Q (mismo criterio de apagado prolijo que el resto del backend local — ver [`desktop-local.md`](./desktop-local.md) §8). Es de "mejor esfuerzo" en todo momento: cualquier problema (binario roto, puerto ocupado, nunca responde) se resuelve en silencio con un log claro, nunca bloquea el arranque del resto del asistente. Detalle técnico completo en [`desktop-local.md`](./desktop-local.md).

## Ver también

- [`primeros-pasos.md`](./primeros-pasos.md) — wizard de bienvenida y pantalla de Configuración, paso a paso.
- [`desktop-local.md`](./desktop-local.md) — cómo corre el backend por dentro (Postgres embebido, migraciones, colas).
- [`self-hosting.md`](./self-hosting.md) — alternativa para quien prefiere correr Edecán desde el código fuente en vez de instalar el binario.
- [`proveedores-llm.md`](./proveedores-llm.md) — qué proveedor de LLM elegir.
- `ARCHITECTURE.md` §12.f — contrato técnico pinned del runner local que consume esta app.
