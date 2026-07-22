//! Cascarón Tauri v2 de Edecán. NO reimplementa la UI (reusa apps/web
//! servida por el sidecar) ni el backend (apps/local / `edecan_local`,
//! WP-V3-05) — orquesta: elige puerto, lanza el sidecar, espera a que avise
//! que está listo (mostrando una ventana de splash mientras tanto), abre la
//! ventana principal apuntando a `http://127.0.0.1:<puerto>/`, y queda
//! residente en la barra del sistema aunque esa ventana se cierre. El
//! sidecar solo se apaga al salir explícitamente. Ver `docs/desktop.md` para
//! el flujo completo y `src/backend.rs` para el ciclo de vida del sidecar.

mod backend;
mod commands;
mod lifecycle;
mod listen;
mod permissions;
mod remote_bridge;
mod startup;
mod tray;
mod util;

use std::sync::Mutex;

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

use backend::{BackendState, DesktopCapabilityState, PortState, StartHiddenState};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let start_hidden = std::env::args().any(|argument| argument == "--hidden");
    // Opción de lifecycle para smokes/entornos administrados. El producto
    // normal es residente en los tres sistemas: el teléfono depende de que
    // el master siga vivo aunque la ventana se cierre.
    let exit_on_close = std::env::args().any(|argument| argument == "--exit-on-close");
    tauri::Builder::default()
        // Debe ser el primer plugin: si Edecán ya está residente, un segundo
        // doble clic recupera la ventana existente en vez de lanzar otro
        // backend, otro puerto y otro túnel.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            tray::show_and_focus_main(app);
        }))
        .manage(BackendState(Mutex::new(None)))
        .manage(PortState(Mutex::new(0)))
        .manage(DesktopCapabilityState(Mutex::new(None)))
        .manage(StartHiddenState(std::sync::atomic::AtomicBool::new(
            start_hidden,
        )))
        .manage(remote_bridge::RemoteBridgeState::default())
        .manage(listen::AlwaysListenRuntime::default())
        .manage(tray::TrayState::default())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--hidden"]),
        ))
        .invoke_handler(tauri::generate_handler![
            commands::retry_backend,
            commands::quit_app,
            commands::always_listen_get_state,
            commands::always_listen_record_sample,
            commands::always_listen_train,
            commands::always_listen_set_enabled,
            commands::always_listen_reset_training,
            commands::desktop_permissions_get_state,
            commands::desktop_permission_request,
            commands::startup_get_state,
            commands::startup_set_enabled,
        ])
        .setup(move |app| {
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
                .visible(!start_hidden)
                .build()?;

            if let Err(err) = startup::initialize_default(&handle) {
                eprintln!("[edecan-desktop] no se pudo configurar el inicio automático: {err}");
            }

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

            // Retoma la escucha nativa si el usuario la dejó activada y el
            // modelo entrenado sigue disponible. Un fallo de micrófono no
            // debe impedir que el resto de la aplicación arranque.
            if let Err(err) = listen::maybe_autostart(app.handle()) {
                eprintln!(
                    "[edecan-desktop] no se pudo autoarrancar la escucha en segundo plano: {err}"
                );
            }
            tray::refresh_listen_state(app.handle());

            Ok(())
        })
        .on_window_event(move |window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                // El cierre lo completa explícitamente este handler. Evitar
                // primero el destroy implícito de GTK impide que el cierre
                // normal y `AppHandle::exit` intenten destruir la misma
                // ventana a la vez (especialmente visible bajo X11).
                api.prevent_close();
                // Misma semántica en macOS, Windows y Linux: cerrar `main`
                // la oculta y conserva backend, relay y acceso móvil. Solo el
                // flag explícito de QA pide que cerrar termine la app.
                let keep_resident = !exit_on_close;
                match lifecycle::close_action(window.label(), keep_resident) {
                    lifecycle::WindowCloseAction::Hide => {
                        // Edecán es residente: cerrar `main` solo guarda la
                        // ventana. El backend, el túnel y la escucha siguen
                        // vivos hasta una salida explícita.
                        if let Err(err) = window.hide() {
                            eprintln!(
                                "[edecan-desktop] no se pudo ocultar la ventana principal: {err}"
                            );
                        }
                    }
                    lifecycle::WindowCloseAction::Exit => window.app_handle().exit(0),
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error construyendo la app de Edecán")
        .run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                // Único punto de salida garantizado: cubre Cmd+Q, "Salir
                // completamente" del tray y "Salir" del panel de error de
                // splash. Cerrar `main` no llega acá: solo la oculta.
                // JAMÁS debe quedar el sidecar huérfano tras una salida real.
                backend::kill_backend(app_handle);
            }
        });
}
