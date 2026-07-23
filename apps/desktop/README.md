# apps/desktop — `edecan-desktop` (Tauri)

Cascarón nativo (Rust, [Tauri v2](https://v2.tauri.app)) que empaqueta Edecán como una app de escritorio instalable para macOS, Windows y Linux x64. No reimplementa nada: reusa la interfaz web ya construida en [`apps/web`](../web) (Next.js, export estático) y el backend local ya definido en [`apps/local`](../local) (`edecan_local`, fase v3) — este directorio solo los orquesta:

> **Alcance de la evidencia:** una build o un smoke test ejecutado en macOS
> valida únicamente el bundle macOS. Windows debe compilarse y probarse en
> Windows x64, y Linux en Linux x64. Que sus configuraciones y scripts vivan
> en este repo no equivale a haber validado esos artefactos desde una Mac.

1. Al arrancar, elige un puerto libre (preferencia `8765`) y lanza `edecan_local` como *sidecar* (empaquetado con PyInstaller, o desde el código fuente en modo desarrollo).
2. Muestra una ventana de splash mientras espera a que el backend avise `EDECAN_LOCAL_READY` por stdout (máx. 60s), con un panel de error + reintentar si algo falla.
3. Abre la ventana principal apuntando a `http://127.0.0.1:<puerto>/` — el propio backend local sirve ahí tanto la API como la web estática.
4. En macOS, Windows y Linux, cerrar la ventana principal solo la oculta: Edecán, el backend y el acceso móvil siguen residentes en la barra o bandeja del sistema. **Salir completamente** desde el menú de Edecán apaga el sidecar de forma limpia; en macOS también funciona Cmd+Q. Nunca debe quedar huérfano.

Documentación completa (requisitos, build paso a paso por plataforma, dónde viven los datos, desinstalar, troubleshooting, firma de código): **[`docs/desktop.md`](../../docs/desktop.md)**. Este README es la referencia rápida de quien trabaja *en* este directorio.

## Estructura

```
apps/desktop/
├── src-tauri/          # crate Rust (edecan-desktop)
│   ├── src/
│   │   ├── main.rs     # entry point (boilerplate estándar de Tauri)
│   │   ├── lib.rs       # arma la app: splash, tray, ciclo de vida
│   │   ├── backend.rs   # todo el ciclo de vida del sidecar edecan-local
│   │   ├── lifecycle.rs # decisión pura: ocultar main o salir por completo
│   │   ├── tray.rs      # barra residente (Abrir/navegador/datos/escucha/salir)
│   │   ├── commands.rs  # comandos invocables desde splash (retry/quit)
│   │   └── util.rs      # abrir URL/carpeta con la app por defecto del SO
│   ├── splash/          # ventana de splash — HTML estático embebido
│   ├── capabilities/    # permisos mínimos (ACL de Tauri v2)
│   ├── icons/           # generados por scripts/make-icons.sh
│   ├── binaries/        # sidecar compilado (gitignored, ver abajo)
│   ├── tauri.conf.json
│   ├── tauri.macos.conf.json
│   ├── tauri.windows.conf.json
│   ├── tauri.linux.conf.json
│   └── Cargo.toml
├── packaging/
│   ├── edecan_local.spec        # spec de PyInstaller para edecan_local
│   ├── edecan_local_entry.py    # entry point mínimo que usa ese spec
│   ├── web/                     # export estático de apps/web (gitignored)
│   ├── studio-engine/           # FyDesign + deps + Chromium (gitignored)
│   └── dist/ · build/           # salida de PyInstaller (gitignored)
├── scripts/
│   ├── build-backend.sh|.ps1    # web estática + PyInstaller -> sidecar
│   ├── build-studio-engine.sh|.ps1 # npm ci + Node 22 + Chromium -> Tauri
│   ├── download-ollama.sh|.ps1  # OPCIONAL: descarga Ollama -> sidecar (fase v4)
│   ├── dev.sh                   # cargo tauri dev, backend desde fuente
│   ├── build-app.sh             # build-backend + cargo tauri build
│   ├── verify-windows-bundles.ps1 # instala NSIS, extrae MSI y hace smoke real
│   ├── verify-linux-bundles.sh  # smoke real AppImage + inspección deb/rpm
│   └── make-icons.sh            # assets/icon-source.png -> src-tauri/icons/
└── assets/
    └── icon-source.png          # placeholder — reemplazalo por el logo real
```

`binaries/`, `packaging/web/`, `packaging/studio-engine/`, `packaging/dist/`,
`packaging/build/`, `src-tauri/target/` y `src-tauri/gen/` están en `.gitignore`
de este directorio — son artefactos de build, nunca se commitean.

## Quick start

```bash
# Desarrollo en un comando: prepara/reusa apps/web/out, compila el shell y
# corre el backend desde fuente, sin PyInstaller:
./scripts/dev.sh

# Build de producción completo para ESTA plataforma (web estática + backend
# congelado + Studio autosuficiente + instalador nativo):
./scripts/build-app.sh
```

El build de producción ejecuta `npm ci` contra el lockfile de
`packages/fydesign-engine`, descarga el Chromium fijado por Playwright y un
Node 22 oficial verificado por SHA-256. También incorpora ffmpeg, ffprobe y
yt-dlp con versiones reproducibles y verifica los binarios descargados antes
de empaquetar. Los empaqueta como recurso/sidecar de Tauri: la persona que
instala Edecán no necesita tener Node, npm, Chrome ni esas herramientas
multimedia globales. Sus licencias separadas se documentan en
[`packages/fydesign-engine/NOTICE`](../../packages/fydesign-engine/NOTICE).
Esta fase sí requiere red en la máquina de build y aumenta de forma importante
el tamaño del artefacto; nada de `node_modules` o Chromium entra en Git.

En Windows x64, el equivalente es `scripts\build-app.ps1`; el CI extrae MSI,
instala NSIS y arranca la aplicación instalada con
`scripts\verify-windows-bundles.ps1`. En Linux x64 el mismo `build-app.sh`
produce AppImage, `.deb` y `.rpm`; el CI arranca el AppImage y comprueba que
los tres paquetes contienen FyDesign completo. Ambos gates esperan el backend
real y confirman que cerrar la ventana no deje procesos huérfanos.

`dev.sh` funciona desde un clon sin sidecar precompilado. La primera corrida
instala las dependencias declaradas y genera la UI estática; las siguientes
reusan `apps/web/out`. Usa `EDECAN_REBUILD_WEB=1 ./scripts/dev.sh` tras cambiar
el frontend, o `EDECAN_SKIP_DEV_WEB=1 ./scripts/dev.sh` para iterar únicamente
en Rust/backend.

## Requisitos para compilar

- **Rust** estable + `cargo-tauri` 2.11.4 (`cargo install tauri-cli --version '2.11.4' --locked`).
- **Node.js 22** y **npm 10** (build de `apps/web`).
- **Python 3.12** + [`uv`](https://docs.astral.sh/uv/) (workspace del repo — `edecan_local` y sus paquetes `edecan_*`).
- macOS: Xcode Command Line Tools (`sips`/`iconutil`, usados por `scripts/make-icons.sh`).
- Linux x64: WebKitGTK 4.1, AppIndicator, librsvg, ALSA, libxdo, `patchelf`, herramientas Debian/RPM y `pkg-config`; el smoke de release también usa Xvfb, Openbox y D-Bus (comando exacto en `docs/desktop.md`).

Detalle completo, por plataforma, en `docs/desktop.md`.

## Tests

El crate tiene tests unitarios para el ciclo residente, activación desde la
barra y helpers nativos de audio. Desde `apps/desktop/src-tauri`:

```bash
cargo fmt --check
cargo check --locked
cargo test --locked
```

Los scripts de release se validan además con `bash -n`. Un `cargo check` o
los tests unitarios no sustituyen el build de los instaladores: publica desde
macOS/Linux x64 con `build-app.sh` y desde Windows x64 con `build-app.ps1`, y
prueba el artefacto generado en la plataforma correspondiente. En Windows usa
`scripts/verify-windows-bundles.ps1`; en Linux,
`scripts/verify-linux-bundles.sh`.
