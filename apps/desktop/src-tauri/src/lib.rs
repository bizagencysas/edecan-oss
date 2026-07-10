//! Cascarón Tauri v2 de Edecán. NO reimplementa la UI (reusa apps/web
//! servida por el sidecar) ni el backend (apps/local / `edecan_local`,
//! WP-V3-05) — orquesta: elige puerto, lanza el sidecar, espera a que avise
//! que está listo (mostrando una ventana de splash mientras tanto), abre la
//! ventana principal apuntando a `http://127.0.0.1:<puerto>/`, y lo mata al
//! cerrar. Ver `docs/desktop.md` para el flujo completo y `src/backend.rs`
//! para el ciclo de vida del sidecar.

mod backend;
mod commands;
mod tray;
mod util;

use std::sync::Mutex;

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

use backend::{BackendState, PortState};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(BackendState(Mutex::new(None)))
        .manage(PortState(Mutex::new(0)))
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            commands::retry_backend,
            commands::quit_app,
        ])
        .setup(|app| {
            let handle = app.handle().clone();

            // Ventana de splash: contenido 100% local/embebido (frontendDist
            // = "splash"), se muestra instantáneo, sin depender de red ni
            // del sidecar. Es la ÚNICA ventana que se crea desde
            // `tauri.conf.json`/config estática — "main" se crea recién en
            // `backend::show_main_window` cuando el backend ya está listo.
            WebviewWindowBuilder::new(app, "splash", WebviewUrl::App("index.html".into()))
                .title("Edecán")
                .inner_size(460.0, 380.0)
                .min_inner_size(460.0, 380.0)
                .resizable(false)
                .center()
                .build()?;

            if let Err(err) = tray::setup_tray(&handle) {
                // No es fatal: la app sigue siendo completamente usable sin
                // ícono de bandeja, así que se loguea y se sigue en vez de
                // abortar el arranque por esto.
                eprintln!("[edecan-desktop] no se pudo crear el ícono de bandeja: {err}");
            }

            // Arranca el backend local en segundo plano; la splash se va
            // actualizando vía eventos (`edecan://backend-*`, ver
            // src/backend.rs y src-tauri/splash/index.html).
            tauri::async_runtime::spawn(backend::start_backend(handle));

            Ok(())
        })
        .on_window_event(|window, event| {
            // Esta app solo tiene una ventana visible a la vez (splash XOR
            // main) — cerrarla, la que sea, cierra toda la aplicación (y
            // dispara RunEvent::Exit más abajo, que mata el sidecar).
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                window.app_handle().exit(0);
            }
        })
        .build(tauri::generate_context!())
        .expect("error construyendo la app de Edecán")
        .run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                // Único punto de salida garantizado: cubre Cmd+Q, cerrar la
                // única ventana, "Salir" del tray y "Salir" del panel de
                // error de splash. JAMÁS debe quedar el sidecar huérfano.
                backend::kill_backend(app_handle);
            }
        });
}
