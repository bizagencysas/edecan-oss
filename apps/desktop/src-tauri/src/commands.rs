//! Comandos invocables desde `splash/index.html`
//! (`window.__TAURI__.core.invoke`). Son comandos de la APP (registrados
//! directo con `tauri::generate_handler!` en `lib.rs`), no de un plugin —
//! el sistema de permisos/capabilities de Tauri v2 gatea comandos
//! *expuestos por plugins*; los que la propia app registra así quedan
//! invocables sin necesitar una entrada extra en `capabilities/default.json`
//! (mismo comportamiento que en v1).

use tauri::AppHandle;

use crate::backend;

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
