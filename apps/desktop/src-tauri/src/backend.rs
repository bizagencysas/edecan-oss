//! Ciclo de vida completo del sidecar `edecan-local` (apps/local, WP-V3-05):
//! elegir puerto, lanzarlo (empaquetado o, en dev, vía `EDECAN_LOCAL_DEV_CMD`),
//! esperar la línea `EDECAN_LOCAL_READY` en su stdout (máx. 60s), y matarlo.
//!
//! Contrato del backend local (ver docs/desktop.md y ARCHITECTURE.md §12):
//! `python -m edecan_local --port P --data-dir D` / binario PyInstaller
//! `edecan-local`, imprime `EDECAN_LOCAL_READY port=P` en stdout cuando está
//! sano, expone `GET /healthz`, bindea 127.0.0.1, sirve la web estática en
//! `/`. Este módulo NUNCA hace un GET a `/healthz` — deliberadamente lee
//! stdout en vez de sumar un cliente HTTP (`reqwest`) solo para esto.

use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Handle del proceso del sidecar actualmente vivo (si hay uno). Se limpia
/// SIEMPRE antes de lanzar uno nuevo y al salir de la app — nunca debe
/// quedar un `edecan-local` huérfano corriendo en segundo plano.
pub struct BackendState(pub Mutex<Option<CommandChild>>);

/// Puerto elegido para el arranque actual. Lo lee el menú de bandeja
/// ("Abrir en el navegador") en el momento del click, nunca capturado por
/// valor de antemano — así sigue siendo correcto después de un reintento
/// que haya elegido un puerto distinto.
pub struct PortState(pub Mutex<u16>);

const READY_MARKER: &str = "EDECAN_LOCAL_READY";
const READY_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_LOG_LINES: usize = 50;
const PREFERRED_PORT: u16 = 8765;

/// Elige un puerto libre en 127.0.0.1: primero intenta `preferred`, y si
/// está ocupado deja que el SO asigne uno libre (bind a puerto 0). En
/// ambos casos el listener de prueba se cierra apenas confirma que el
/// puerto estaba libre, dejándolo disponible para que lo tome el sidecar.
pub fn pick_port(preferred: u16) -> u16 {
    if TcpListener::bind(("127.0.0.1", preferred)).is_ok() {
        return preferred;
    }
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .expect("no se pudo reservar ningún puerto TCP libre en 127.0.0.1");
    listener
        .local_addr()
        .expect("listener sin local_addr")
        .port()
}

/// `{app_data_dir}/data` — carpeta de datos del backend local (Postgres
/// embebido, archivos subidos, etc.), separada de la carpeta de
/// configuración propia de Tauri (`app_data_dir` en sí).
pub fn data_dir(app: &AppHandle) -> PathBuf {
    let base = app
        .path()
        .app_data_dir()
        .expect("no se pudo resolver el directorio de datos de la app (Tauri path resolver)");
    base.join("data")
}

/// Puerto del arranque actual (0 si todavía no se lanzó ninguno).
pub fn current_port(app: &AppHandle) -> u16 {
    *app.state::<PortState>().0.lock().unwrap()
}

/// Punto de entrada único para (re)lanzar el backend local. Lo llama tanto
/// `setup()` en el arranque como el comando `retry_backend` — mismo camino,
/// sin duplicar lógica. Nunca hace panic: cualquier fallo termina emitiendo
/// `edecan://backend-error` para que la ventana de splash lo muestre.
pub async fn start_backend(app: AppHandle) {
    // Limpieza defensiva: si había un sidecar vivo de un intento anterior,
    // se mata antes de lanzar uno nuevo. Nunca dos sidecars vivos a la vez.
    kill_backend(&app);

    let port = pick_port(PREFERRED_PORT);
    *app.state::<PortState>().0.lock().unwrap() = port;

    let target_data_dir = data_dir(&app);
    if let Err(err) = std::fs::create_dir_all(&target_data_dir) {
        emit_error(
            &app,
            format!(
                "No se pudo crear la carpeta de datos ({}): {err}",
                target_data_dir.display()
            ),
            Vec::new(),
        );
        return;
    }

    let _ = app.emit("edecan://backend-status", "Arrancando tu asistente…");

    let command = match build_command(&app, port, &target_data_dir) {
        Ok(cmd) => cmd,
        Err(message) => {
            emit_error(&app, message, Vec::new());
            return;
        }
    };

    let (mut rx, child) = match command.spawn() {
        Ok(pair) => pair,
        Err(err) => {
            emit_error(
                &app,
                format!("No se pudo lanzar el backend local: {err}"),
                Vec::new(),
            );
            return;
        }
    };

    *app.state::<BackendState>().0.lock().unwrap() = Some(child);

    let recent_lines: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let app_for_task = app.clone();
    let recent_for_task = recent_lines.clone();

    let outcome = tokio::time::timeout(READY_TIMEOUT, async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let line = String::from_utf8_lossy(&bytes).trim_end().to_string();
                    if line.is_empty() {
                        continue;
                    }
                    push_line(&recent_for_task, &line);
                    let _ = app_for_task.emit("edecan://backend-log", &line);
                    if line.contains(READY_MARKER) {
                        return Ok(parse_ready_port(&line).unwrap_or(port));
                    }
                }
                CommandEvent::Stderr(bytes) => {
                    let line = String::from_utf8_lossy(&bytes).trim_end().to_string();
                    if line.is_empty() {
                        continue;
                    }
                    push_line(&recent_for_task, &line);
                    let _ = app_for_task.emit("edecan://backend-log", &line);
                }
                CommandEvent::Error(err) => {
                    return Err(format!("Error del proceso del backend local: {err}"));
                }
                CommandEvent::Terminated(payload) => {
                    return Err(format!(
                        "El backend local se cerró antes de avisar que estaba listo \
                         (código de salida: {:?}).",
                        payload.code
                    ));
                }
                _ => {}
            }
        }
        Err("El backend local cerró su salida estándar sin avisar que estaba listo.".to_string())
    })
    .await;

    match outcome {
        Ok(Ok(actual_port)) => {
            *app.state::<PortState>().0.lock().unwrap() = actual_port;
            if let Err(err) = show_main_window(&app, actual_port) {
                emit_error(
                    &app,
                    format!("El backend quedó listo, pero no se pudo abrir la ventana: {err}"),
                    read_lines(&recent_lines),
                );
            }
        }
        Ok(Err(message)) => emit_error(&app, message, read_lines(&recent_lines)),
        Err(_elapsed) => emit_error(
            &app,
            "El backend local tardó más de 60 segundos en avisar que estaba listo.".to_string(),
            read_lines(&recent_lines),
        ),
    }
}

/// Arma el `Command` del sidecar. Camino normal: el binario empaquetado por
/// `scripts/build-backend.sh` (`externalBin`, ver tauri.conf.json). Si ese
/// binario no existe todavía (típico en `cargo tauri dev` antes de correr
/// el script de build) cae al comando de desarrollo configurable vía la
/// variable de entorno `EDECAN_LOCAL_DEV_CMD` (default: `"uv run
/// --all-packages python -m edecan_local"`, corrido con cwd = raíz del
/// repo). El default lleva `--all-packages` a propósito (mismo motivo que
/// `scripts/dev.sh`, que exporta esta misma variable explícita ANTES de
/// invocar `cargo tauri dev`: un `uv run` suelto sin ese flag poda en
/// silencio el resto del workspace uv, ver HOTFIXES_PENDIENTES.md "uv sync/
/// uv run sueltos podaban en silencio...") — este default solo se ejercita
/// de verdad si alguien corre `cargo tauri dev` DIRECTO sin pasar por
/// `scripts/dev.sh` (que ya fija la variable antes de llegar acá). En
/// cualquiera de los dos caminos, `with_ollama_env` (abajo) suma al final
/// las dos env vars opcionales de Ollama embebido (WP-V4-09, ver
/// docs/desktop.md "Ollama embebido (opcional)").
fn build_command(
    app: &AppHandle,
    port: u16,
    target_data_dir: &Path,
) -> Result<tauri_plugin_shell::process::Command, String> {
    let port_arg = port.to_string();
    let data_dir_arg = target_data_dir.to_string_lossy().to_string();
    let backend_args = [
        "--port",
        port_arg.as_str(),
        "--data-dir",
        data_dir_arg.as_str(),
    ];

    let cmd = match app.shell().sidecar("edecan-local") {
        Ok(cmd) => cmd.args(backend_args),
        Err(sidecar_err) => {
            let dev_cmd = std::env::var("EDECAN_LOCAL_DEV_CMD")
                .unwrap_or_else(|_| "uv run --all-packages python -m edecan_local".to_string());
            let mut parts = dev_cmd.split_whitespace();
            let program = parts.next().ok_or_else(|| {
                format!(
                    "No hay sidecar empaquetado ({sidecar_err}) y EDECAN_LOCAL_DEV_CMD \
                     está vacío. Corré scripts/build-backend.sh o exportá esa variable \
                     (ver docs/desktop.md)."
                )
            })?;
            let extra_args: Vec<&str> = parts.collect();
            app.shell()
                .command(program)
                .args(extra_args)
                .args(backend_args)
                .current_dir(repo_root_dir())
        }
    };

    Ok(with_expanded_path(with_ollama_env(cmd)))
}

/// Directorios donde suelen vivir CLIs bring-your-own que este backend
/// necesita detectar (`claude`, `codex` — `packages/llm/edecan_llm/
/// detect.py::_detect_cli`, `shutil.which`). Nunca reemplazan al detector
/// de Python como fuente de verdad de "¿está instalado?" — solo amplían
/// dónde busca.
fn extra_cli_search_dirs() -> Vec<String> {
    let home = std::env::var("HOME").unwrap_or_default();
    let mut dirs = vec![
        "/opt/homebrew/bin".to_string(),
        "/opt/homebrew/sbin".to_string(),
        "/usr/local/bin".to_string(),
        "/usr/local/sbin".to_string(),
    ];
    if !home.is_empty() {
        dirs.extend([
            format!("{home}/.local/bin"),
            format!("{home}/.cargo/bin"),
            format!("{home}/.bun/bin"),
            format!("{home}/go/bin"),
            format!("{home}/.npm-global/bin"),
        ]);
    }
    dirs
}

/// Suma `extra_cli_search_dirs()` al `PATH` heredado del proceso Tauri
/// (nunca lo reemplaza, solo lo extiende) antes de lanzar el sidecar.
///
/// Por qué hace falta: una app lanzada por Finder/Launch Services (doble
/// clic, `open`) recibe el PATH mínimo de `launchd`
/// (`/usr/bin:/bin:/usr/sbin:/sbin`), NO el PATH completo que arma la shell
/// del usuario leyendo `~/.zshrc`/`~/.zprofile` — a diferencia de correr
/// `cargo tauri dev`/`build` desde una terminal, que sí lo hereda. El
/// sidecar `edecan-local` hereda el PATH de ESTE proceso Tauri, así que sin
/// este fix `shutil.which("claude")` no encuentra binarios instalados en
/// ubicaciones típicas de usuario (p. ej. `~/.local/bin`, donde caen
/// instalaciones vía `pipx`/`uv tool`) aunque el usuario los tenga
/// perfectamente instalados y funcionando desde su propia terminal — visto
/// en vivo: paso 1 del wizard de bienvenida reportando "No detectamos
/// Claude CLI" en la app empaquetada e instalada desde el .dmg, mientras
/// `which claude` sí lo encuentra en una terminal normal
/// (HOTFIXES_PENDIENTES.md).
fn with_expanded_path(
    cmd: tauri_plugin_shell::process::Command,
) -> tauri_plugin_shell::process::Command {
    let current = std::env::var("PATH").unwrap_or_default();
    let extended = extra_cli_search_dirs().join(":");
    let path = if current.is_empty() {
        extended
    } else {
        format!("{current}:{extended}")
    };
    cmd.env("PATH", path)
}

/// Suma al `Command` del backend local dos env vars OPCIONALES para que
/// `edecan_local.ollama_supervisor` (Python, apps/local) pueda arrancar un
/// Ollama embebido sin que ESTE archivo tenga que orquestar su ciclo de
/// vida — esa lógica vive en Python a propósito, porque ahí sí hay tests
/// (ver docs/desktop-local.md, nota de verificación de este mismo bloque).
/// Nunca falla ni hace panic: si no hay nada que sumar, el `Command` vuelve
/// tal cual.
///
/// `EDECAN_OLLAMA_BIN`: `tauri_plugin_shell::process::Command` (el tipo que
/// devuelve `app.shell().sidecar(...)`, ya usado arriba para
/// "edecan-local") NO expone ningún getter público para la ruta resuelta
/// del binario — solo sirve para lanzarlo, no para preguntarle "¿dónde
/// estás?". Por eso acá la ruta se resuelve a mano con `std` puro
/// (`resolve_ollama_sidecar`), replicando el mismo criterio dev/dos-rutas
/// que ya usa el resto de este archivo para "edecan-local" (sidecar ya
/// copiado junto al ejecutable actual, o binario fuente todavía con el
/// sufijo de target-triple en `binaries/` si nunca se corrió `cargo tauri
/// dev`/`build` después de `scripts/download-ollama.sh`) — sin depender de
/// ninguna API de `tauri_plugin_shell` más allá de la que ya prueba este
/// archivo (`app.shell().sidecar("edecan-local")`, arriba).
///
/// `EDECAN_OLLAMA_AUTOSTART`: se propaga tal cual si quien lanzó la app
/// Tauri la trae fijada en su propio entorno (hoy: uso avanzado/dev: la UI
/// de un clic en Configuración vive del lado del backend local, que la lee
/// de este mismo proceso hijo una vez que arranca).
fn with_ollama_env(
    cmd: tauri_plugin_shell::process::Command,
) -> tauri_plugin_shell::process::Command {
    let cmd = match resolve_ollama_sidecar() {
        Some(bin) => cmd.env("EDECAN_OLLAMA_BIN", bin.to_string_lossy().to_string()),
        None => cmd,
    };
    match std::env::var("EDECAN_OLLAMA_AUTOSTART") {
        Ok(value) => cmd.env("EDECAN_OLLAMA_AUTOSTART", value),
        Err(_) => cmd,
    }
}

/// Intenta resolver la ruta absoluta de un binario `ollama` empaquetado
/// como sidecar (`tauri.conf.json` → `bundle.externalBin`,
/// `scripts/download-ollama.sh`). Devuelve `None` (nunca hace panic) si no
/// encuentra nada en ninguno de los dos lugares donde Tauri puede haberlo
/// dejado:
///
/// 1. **Ya construido** (`cargo tauri build`, o `cargo tauri dev` después
///    de correr `download-ollama.sh`): Tauri copia cada `externalBin` junto
///    al ejecutable de la app, recortando el sufijo de target-triple —
///    mismo directorio que devuelve `std::env::current_exe()`. Se busca el
///    nombre exacto `ollama`/`ollama.exe` ahí.
/// 2. **Recién descargado, todavía sin construir**: `download-ollama.sh`
///    deja el archivo en `apps/desktop/src-tauri/binaries/` CON el sufijo
///    de target-triple puesto (`ollama-aarch64-apple-darwin`, etc.) — acá
///    NO se reconstruye ese sufijo a mano (una fuente más de bugs no
///    verificables sin `cargo build`, ver `docs/desktop-local.md` §8/§9):
///    se busca cualquier archivo que EMPIECE con `ollama-` en esa carpeta.
fn resolve_ollama_sidecar() -> Option<PathBuf> {
    let exe_name = if cfg!(target_os = "windows") {
        "ollama.exe"
    } else {
        "ollama"
    };
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let candidate = dir.join(exe_name);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }

    let source_dir = repo_root_dir().join("apps/desktop/src-tauri/binaries");
    if let Ok(entries) = std::fs::read_dir(&source_dir) {
        for entry in entries.flatten() {
            if entry.file_name().to_string_lossy().starts_with("ollama-") {
                return Some(entry.path());
            }
        }
    }
    None
}

/// Directorio raíz del repo, calculado en tiempo de compilación a partir de
/// `CARGO_MANIFEST_DIR` (= `apps/desktop/src-tauri`). Solo se usa para el
/// fallback de modo dev de `build_command` — en producción no se llama.
fn repo_root_dir() -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let computed = manifest_dir.join("../../..");
    computed.canonicalize().unwrap_or(computed)
}

/// Extrae el puerto real de una línea `EDECAN_LOCAL_READY port=8765`. Si el
/// backend algún día reporta un puerto distinto al pedido (ej. el nuestro
/// quedó ocupado justo entre que lo probamos libre y que el backend lo
/// bindeó), esto evita que la ventana principal navegue al puerto viejo.
fn parse_ready_port(line: &str) -> Option<u16> {
    let after = line.split("port=").nth(1)?;
    let digits: String = after.chars().take_while(|c| c.is_ascii_digit()).collect();
    digits.parse::<u16>().ok()
}

fn push_line(buffer: &Arc<Mutex<Vec<String>>>, line: &str) {
    let mut guard = buffer.lock().unwrap();
    guard.push(line.to_string());
    if guard.len() > MAX_LOG_LINES {
        let excess = guard.len() - MAX_LOG_LINES;
        guard.drain(0..excess);
    }
}

fn read_lines(buffer: &Arc<Mutex<Vec<String>>>) -> Vec<String> {
    buffer.lock().unwrap().clone()
}

fn emit_error(app: &AppHandle, message: String, log: Vec<String>) {
    eprintln!("[edecan-desktop] {message}");
    let payload = serde_json::json!({ "message": message, "log": log });
    let _ = app.emit("edecan://backend-error", payload);
}

/// Crea (si hace falta) y muestra la ventana principal apuntando al backend
/// local ya listo, y cierra la de splash. La web estática la sirve el
/// propio backend en `/` (Next.js exportado, ver scripts/build-backend.sh)
/// — Tauri no empaqueta ni sirve el frontend, solo navega a esa URL.
fn show_main_window(app: &AppHandle, port: u16) -> Result<(), String> {
    let url = tauri::Url::parse(&format!("http://127.0.0.1:{port}/")).map_err(|e| e.to_string())?;

    if let Some(existing) = app.get_webview_window("main") {
        // No debería pasar en el flujo normal (retry_backend solo se llama
        // antes de que "main" exista), pero por las dudas no se duplica la
        // ventana — solo se enfoca la que ya está.
        existing.show().map_err(|e| e.to_string())?;
        existing.set_focus().map_err(|e| e.to_string())?;
    } else {
        let main_window =
            tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::External(url))
                .title("Edecán")
                .inner_size(1280.0, 800.0)
                .min_inner_size(960.0, 600.0)
                .center()
                .build()
                .map_err(|e| e.to_string())?;
        let _ = main_window.set_focus();
    }

    if let Some(splash) = app.get_webview_window("splash") {
        // `destroy()`, NUNCA `close()`: desde Tauri 2.0, `WebviewWindow::close()`
        // dispara un evento `CloseRequested` normal — el mismo que intercepta el
        // `on_window_event` global de lib.rs para salir de toda la app cuando el
        // usuario cierra la única ventana visible. Si acá se usara `close()`, esa
        // transición splash→main dispararía ese mismo handler y mataría la app
        // enterita en el instante en que la ventana principal recién se muestra.
        // `destroy()` fuerza el cierre sin pasar por ese evento (ver changelog de
        // Tauri 2.0: "Changed WebviewWindow::close to trigger a close requested
        // event instead of forcing the window to be closed. Use
        // WebviewWindow::destroy to force close.").
        let _ = splash.destroy();
    }
    Ok(())
}

/// Mata el sidecar actual (si hay uno) y limpia el estado. Se llama al
/// reintentar (antes de lanzar uno nuevo) y en `RunEvent::Exit` (lib.rs) —
/// ese segundo punto es el que garantiza que cerrar la app SIEMPRE mata al
/// backend, sin excepción.
pub fn kill_backend(app: &AppHandle) {
    let child = app.state::<BackendState>().0.lock().unwrap().take();
    let Some(child) = child else { return };

    let pid = child.pid();

    // `CommandChild::kill()` (más abajo) manda SIGKILL en Unix — una señal que
    // NO se puede capturar, así que el `finally: await
    // asyncio.to_thread(pg_handle.cleanup)` de `edecan_local.runtime.run()`
    // (docs/desktop-local.md §5/§8) nunca llegaría a correr, y el Postgres
    // embebido que lanza `pgserver` (proceso hijo real de `edecan-local`, no
    // del sidecar de Tauri) quedaría huérfano. En macOS/Linux, antes de
    // escalar al kill duro, le damos al proceso la oportunidad de apagarse
    // solo: mandamos SIGTERM (que `edecan_local.runtime.run()` sí maneja) y
    // esperamos un margen corto. Si sale solo, no hace falta ningún kill más
    // — Postgres ya quedó apagado limpio por Python mismo. Si no sale a
    // tiempo (proceso colgado), seguimos igual que antes con el kill duro
    // como red de seguridad final, para que cerrar la app nunca se quede
    // esperando indefinidamente.
    #[cfg(not(target_os = "windows"))]
    {
        if send_sigterm_and_wait_for_exit(pid) {
            return;
        }
    }

    if let Err(err) = child.kill() {
        eprintln!("[edecan-desktop] no se pudo matar el backend local (pid {pid}): {err}");
    }

    // `CommandChild::kill()` termina el proceso del sidecar, pero en
    // Windows no siempre se lleva con él a los procesos que ESE proceso
    // haya lanzado (ej. el Postgres embebido de `pgserver`, WP-V3-05).
    // `taskkill /T` mata el árbol completo por PID como red de seguridad
    // extra — nunca debe quedar un proceso huérfano tras cerrar la app.
    #[cfg(target_os = "windows")]
    {
        let _ = std::process::Command::new("taskkill")
            .args(["/F", "/T", "/PID", &pid.to_string()])
            .spawn();
    }
}

/// Manda SIGTERM a `pid` (vía el binario `kill`, sin dependencia nueva de
/// Cargo) y sondea hasta `MAX_WAIT` (cada `POLL_INTERVAL`, con `kill -0`) a
/// que el proceso termine solo. Devuelve `true` si terminó dentro del
/// margen — en ese caso el caller NO debe mandar ningún kill adicional,
/// para dejar que `edecan_local.runtime.run()` haya apagado `pgserver`
/// limpio en su propio `finally`. Devuelve `false` si sigue vivo (o si no
/// se pudo mandar la señal / el binario `kill` no está disponible), y el
/// caller debe escalar al kill duro de siempre como red de seguridad.
/// Bloqueante a propósito (`kill_backend` no es async): el margen máximo es
/// corto (3s) y este camino solo corre al cerrar la app.
#[cfg(not(target_os = "windows"))]
fn send_sigterm_and_wait_for_exit(pid: u32) -> bool {
    let sent = std::process::Command::new("kill")
        .args(["-TERM", &pid.to_string()])
        .status()
        .map(|status| status.success())
        .unwrap_or(false);
    if !sent {
        return false;
    }

    const POLL_INTERVAL: Duration = Duration::from_millis(100);
    const MAX_WAIT: Duration = Duration::from_secs(3);
    let mut waited = Duration::ZERO;
    while waited < MAX_WAIT {
        std::thread::sleep(POLL_INTERVAL);
        waited += POLL_INTERVAL;
        let still_alive = std::process::Command::new("kill")
            .args(["-0", &pid.to_string()])
            .status()
            .map(|status| status.success())
            .unwrap_or(false);
        if !still_alive {
            return true;
        }
    }
    false
}
