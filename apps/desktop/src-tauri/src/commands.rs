//! Comandos invocables desde la UI mediante `window.__TAURI__.core.invoke`.
//! La splash usa el origen local de Tauri, pero la ventana principal carga
//! desde `http://127.0.0.1:<puerto>` y Tauri v2 la considera remota. Por eso
//! estos comandos se declaran también en `build.rs` y se autorizan de forma
//! explícita en la capability `default`.

use tauri::{AppHandle, Emitter};
use tauri_plugin_dialog::DialogExt;

use crate::backend;
use crate::listen;
use crate::permissions;
use crate::startup;
use crate::tray;
use crate::updates;
use crate::util;

/// Botón "Reintentar" del panel de error de splash. Repite exactamente el
/// mismo camino que el arranque inicial (elige puerto, lanza, espera).
#[tauri::command]
pub async fn retry_backend(app: AppHandle) {
    backend::start_backend(app).await;
}

/// Botón "Salir" del panel de error de splash (cuando el backend no
/// arrancó y el usuario prefiere cerrar en vez de reintentar).
#[tauri::command]
pub fn quit_app(app: AppHandle) {
    app.exit(0);
}

/// Abre un portal oficial en el navegador predeterminado.
///
/// La UI principal vive en una WebView remota (`127.0.0.1`) y WebKit no
/// delega de forma confiable `target="_blank"`. Este puente mantiene la
/// navegación fuera de Edecán y aplica la lista cerrada de dominios de
/// `util::validate_external_url`.
#[tauri::command]
pub fn open_external_url(url: String) -> Result<(), String> {
    util::open_in_default_browser(&url)
}

/// Abre el selector de carpetas del sistema operativo asociado a la app.
///
/// Este comando solo devuelve la ruta elegida. La autorización real ocurre
/// después en el backend del IDE, que canonicaliza la ruta y aplica su
/// frontera de workspaces. Así mantenemos una UX nativa sin convertir el
/// WebView en un cliente con acceso general al sistema de archivos.
#[tauri::command]
pub async fn pick_workspace_folder(app: AppHandle) -> Result<Option<String>, String> {
    let selected = app
        .dialog()
        .file()
        .set_title("Elige una carpeta para Edecán")
        .blocking_pick_folder();

    selected
        .map(|file_path| {
            file_path
                .into_path()
                .map(|path| path.to_string_lossy().into_owned())
                .map_err(|error| format!("La carpeta elegida no es una ruta local válida: {error}"))
        })
        .transpose()
}

// --- "Escuchar siempre" (src/listen.rs) -----------------------------------

#[tauri::command]
pub fn always_listen_get_state(app: AppHandle) -> listen::AlwaysListenStateOut {
    listen::get_state(&app)
}

#[tauri::command]
pub async fn always_listen_record_sample(app: AppHandle, index: u8) -> Result<(), String> {
    listen::record_sample(app, index).await
}

#[tauri::command]
pub async fn always_listen_train(app: AppHandle, wake_label: String) -> Result<(), String> {
    let result = listen::train(app.clone(), wake_label).await;
    tray::refresh_listen_state(&app);
    result
}

#[tauri::command]
pub fn always_listen_set_enabled(app: AppHandle, enabled: bool) -> Result<(), String> {
    let result = listen::set_enabled(app.clone(), enabled);
    tray::refresh_listen_state(&app);
    result
}

#[tauri::command]
pub fn always_listen_reset_training(app: AppHandle) -> Result<(), String> {
    let result = listen::reset_training(app.clone());
    tray::refresh_listen_state(&app);
    result
}

// --- Centro de permisos del sistema operativo ---------------------------

#[tauri::command]
pub fn desktop_permissions_get_state() -> permissions::DesktopPermissionsState {
    permissions::get_state()
}

#[tauri::command]
pub async fn desktop_permission_request(
    permission_id: String,
) -> Result<permissions::PermissionActionResult, String> {
    permissions::request(permission_id).await
}

// --- Asistente residente al iniciar sesión -------------------------------

#[tauri::command]
pub fn startup_get_state(app: AppHandle) -> Result<startup::StartupState, String> {
    startup::get_state(&app)
}

#[tauri::command]
pub fn startup_set_enabled(app: AppHandle, enabled: bool) -> Result<startup::StartupState, String> {
    startup::set_enabled(&app, enabled)
}

// --- Actualizaciones firmadas de la app ---------------------------------

#[tauri::command]
pub async fn desktop_update_check(
    app: AppHandle,
    state: tauri::State<'_, updates::DesktopUpdateState>,
    channel: String,
) -> Result<updates::DesktopUpdateCheckResult, String> {
    updates::check(&app, &state, &channel).await
}

#[tauri::command]
pub async fn desktop_update_install(
    app: AppHandle,
    state: tauri::State<'_, updates::DesktopUpdateState>,
    expected_version: String,
    channel: String,
) -> Result<(), String> {
    updates::install(&app, &state, &expected_version, &channel).await
}

/// Lee el texto del portapapeles del sistema (sin overlay de captura) y
/// avisa a la UI con `edecan://ask-with-context`.
#[tauri::command]
pub fn capture_clipboard_context(app: AppHandle) -> Result<String, String> {
    let text = read_clipboard_text()?;
    emit_ask_with_context(&app, &text);
    Ok(text)
}

pub(crate) fn emit_clipboard_context(app: &AppHandle) {
    match read_clipboard_text() {
        Ok(text) => emit_ask_with_context(app, &text),
        Err(err) => {
            eprintln!("[edecan-desktop] no se pudo leer el portapapeles: {err}");
            let _ = app.emit(
                "edecan://ask-with-context",
                serde_json::json!({ "text": "", "error": err }),
            );
        }
    }
}

fn emit_ask_with_context(app: &AppHandle, text: &str) {
    let _ = app.emit(
        "edecan://ask-with-context",
        serde_json::json!({ "text": text }),
    );
}

fn read_clipboard_text() -> Result<String, String> {
    #[cfg(target_os = "macos")]
    {
        return command_stdout("/usr/bin/pbpaste", &[]);
    }
    #[cfg(target_os = "windows")]
    {
        return command_stdout(
            "powershell",
            &["-NoProfile", "-Command", "Get-Clipboard -Raw"],
        );
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        if let Ok(text) = command_stdout("wl-paste", &["--no-newline"]) {
            return Ok(text);
        }
        command_stdout("xclip", &["-selection", "clipboard", "-o"])
    }
}

fn command_stdout(program: &str, args: &[&str]) -> Result<String, String> {
    let output = std::process::Command::new(program)
        .args(args)
        .output()
        .map_err(|err| format!("No se pudo leer el portapapeles: {err}"))?;
    if !output.status.success() {
        return Err("No se pudo leer el portapapeles.".to_string());
    }
    String::from_utf8(output.stdout)
        .map_err(|_| "El portapapeles no contiene texto UTF-8.".to_string())
}
