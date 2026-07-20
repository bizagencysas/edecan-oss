//! Comandos invocables desde la UI mediante `window.__TAURI__.core.invoke`.
//! La splash usa el origen local de Tauri, pero la ventana principal carga
//! desde `http://127.0.0.1:<puerto>` y Tauri v2 la considera remota. Por eso
//! estos comandos se declaran también en `build.rs` y se autorizan de forma
//! explícita en la capability `default`.

use tauri::AppHandle;

use crate::backend;
use crate::listen;

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
    listen::train(app, wake_label).await
}

#[tauri::command]
pub fn always_listen_set_enabled(app: AppHandle, enabled: bool) -> Result<(), String> {
    listen::set_enabled(app, enabled)
}

#[tauri::command]
pub fn always_listen_reset_training(app: AppHandle) -> Result<(), String> {
    listen::reset_training(app)
}
