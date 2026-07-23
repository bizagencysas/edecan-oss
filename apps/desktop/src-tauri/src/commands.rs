//! Comandos invocables desde la UI mediante `window.__TAURI__.core.invoke`.
//! La splash usa el origen local de Tauri, pero la ventana principal carga
//! desde `http://127.0.0.1:<puerto>` y Tauri v2 la considera remota. Por eso
//! estos comandos se declaran también en `build.rs` y se autorizan de forma
//! explícita en la capability `default`.

use tauri::AppHandle;

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
