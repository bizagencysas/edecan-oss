# apps/desktop — `edecan-desktop` (Tauri)

Cascarón nativo (Rust, [Tauri v2](https://v2.tauri.app)) que empaqueta Edecán como una app de escritorio instalable para macOS y Windows — **el vehículo principal de venta del producto** (`DIRECCION_ACTUAL.md`, "Qué se vende"). No reimplementa nada: reusa la interfaz web ya construida en [`apps/web`](../web) (Next.js, export estático) y el backend local ya definido en [`apps/local`](../local) (`edecan_local`, WP-V3-05) — este directorio solo los orquesta:

1. Al arrancar, elige un puerto libre (preferencia `8765`) y lanza `edecan_local` como *sidecar* (empaquetado con PyInstaller, o desde el código fuente en modo desarrollo).
2. Muestra una ventana de splash mientras espera a que el backend avise `EDECAN_LOCAL_READY` por stdout (máx. 60s), con un panel de error + reintentar si algo falla.
3. Abre la ventana principal apuntando a `http://127.0.0.1:<puerto>/` — el propio backend local sirve ahí tanto la API como la web estática.
4. Al cerrar la app (por cualquier vía: ventana, bandeja, botón "Salir"), mata el proceso del sidecar sin excepción — nunca debe quedar huérfano.

Documentación completa (requisitos, build paso a paso por plataforma, dónde viven los datos, desinstalar, troubleshooting, firma de código): **[`docs/desktop.md`](../../docs/desktop.md)**. Este README es la referencia rápida de quien trabaja *en* este directorio.

## Estructura

```
apps/desktop/
├── src-tauri/          # crate Rust (edecan-desktop)
│   ├── src/
│   │   ├── main.rs     # entry point (boilerplate estándar de Tauri)
│   │   ├── lib.rs       # arma la app: splash, tray, ciclo de vida
│   │   ├── backend.rs   # todo el ciclo de vida del sidecar edecan-local
│   │   ├── tray.rs      # menú de bandeja (Abrir/Ver datos/Salir)
│   │   ├── commands.rs  # comandos invocables desde splash (retry/quit)
│   │   └── util.rs      # abrir URL/carpeta con la app por defecto del SO
│   ├── splash/          # ventana de splash — HTML estático embebido
│   ├── capabilities/    # permisos mínimos (ACL de Tauri v2)
│   ├── icons/           # generados por scripts/make-icons.sh
│   ├── binaries/        # sidecar compilado (gitignored, ver abajo)
│   ├── tauri.conf.json
│   └── Cargo.toml
├── packaging/
│   ├── edecan_local.spec        # spec de PyInstaller para edecan_local
│   ├── edecan_local_entry.py    # entry point mínimo que usa ese spec
│   ├── web/                     # export estático de apps/web (gitignored)
│   └── dist/ · build/           # salida de PyInstaller (gitignored)
├── scripts/
│   ├── build-backend.sh|.ps1    # web estática + PyInstaller -> sidecar
│   ├── download-ollama.sh|.ps1  # OPCIONAL: descarga Ollama -> sidecar (WP-V4-09)
│   ├── dev.sh                   # cargo tauri dev, backend desde fuente
│   ├── build-app.sh             # build-backend + cargo tauri build
│   └── make-icons.sh            # assets/icon-source.png -> src-tauri/icons/
└── assets/
    └── icon-source.png          # placeholder — reemplazalo por el logo real
```

`binaries/`, `packaging/web/`, `packaging/dist/`, `packaging/build/`, `src-tauri/target/` y `src-tauri/gen/` están en `.gitignore` de este directorio — son artefactos de build, nunca se commitean.

## Quick start

```bash
# Desarrollo (recarga en caliente del shell nativo; el backend corre desde
# el código fuente vía `uv run python -m edecan_local`, sin PyInstaller):
./scripts/dev.sh

# Build de producción completo para ESTA plataforma (web estática + backend
# congelado con PyInstaller + instalador nativo):
./scripts/build-app.sh
```

En Windows: `scripts\build-backend.ps1` seguido de `cargo tauri build` a mano (no hay `build-app.ps1` — ver `docs/desktop.md`).

## Requisitos para compilar

- **Rust** estable + `cargo-tauri` (`cargo install tauri-cli --version '^2.0'`).
- **Node.js 20+** y npm (build de `apps/web`).
- **Python 3.12** + [`uv`](https://docs.astral.sh/uv/) (workspace del repo — `edecan_local` y sus paquetes `edecan_*`).
- macOS: Xcode Command Line Tools (`sips`/`iconutil`, usados por `scripts/make-icons.sh`).

Detalle completo, por plataforma, en `docs/desktop.md`.

## Sobre la verificación de este código

Este work package se escribió sin un toolchain de Rust disponible en la máquina (`cargo`/`rustc` no estaban instalados) — no se pudo correr `cargo check` ni `cargo tauri build`. En su lugar, cada API de `tauri`/`tauri-plugin-shell` usada en `src-tauri/src/*.rs` se verificó a mano contra la documentación real de Tauri v2 (docs.rs + v2.tauri.app), incluyendo una corrección real que esa verificación encontró: `WebviewWindow::close()` dispara `WindowEvent::CloseRequested` desde Tauri 2.0 (antes forzaba el cierre) — usar eso en la transición splash→main habría disparado el handler global de "cerrar ventana = salir de la app" apenas se abriera la ventana principal. Se usa `destroy()` en su lugar (ver el comentario en `backend.rs::show_main_window`).

Si al compilar por primera vez `cargo`/`cargo tauri` se quejan de algo en `src/tray.rs` (la superficie de `tauri::menu`/`tauri::tray` es la que más cambió entre betas de Tauri v2), la lógica de ese archivo es simple y no debería tener que cambiar — lo que puede variar es el nombre exacto de algún builder method; el propio archivo tiene un comentario apuntando a la doc oficial correspondiente.

**WP-V4-09 (Ollama embebido)** sumó dos funciones a `backend.rs` (`with_ollama_env`/`resolve_ollama_sidecar`) con el mismo límite y el mismo método de verificación: sin `cargo`/`rustc` disponibles, se escribieron usando SOLO `std` (nada de API nueva de `tauri`/`tauri-plugin-shell` más allá de la que ya usa el resto del archivo), salvo un único método nuevo (`Command::env`) verificado a mano contra el código fuente real de `tauri-plugin-shell` 2.3.5 (docs.rs) — confirmado que `tauri_plugin_shell::process::Command` NO expone ningún getter público para la ruta resuelta de un sidecar, que es justamente por qué esas dos funciones existen (resuelven la ruta con `std::env::current_exe()`/`std::fs::read_dir` en vez de pedírsela a esa API). Detalle completo y checklist de verificación empírica pendiente en [`docs/desktop-local.md`](../../docs/desktop-local.md) §8/§9.

## Tests

Este directorio no tiene una suite de tests propia (es un cascarón de empaquetado, no lógica de negocio — esa vive en `apps/api`/`apps/worker`/`packages/*`, cada uno con la suya). La validación de este WP fue: `cargo check` (no disponible, ver arriba), `bash -n` sobre los `.sh` (incluyendo `download-ollama.sh`, WP-V4-09), y `python -m py_compile` sobre `packaging/edecan_local_entry.py` y `packaging/edecan_local.spec`.
