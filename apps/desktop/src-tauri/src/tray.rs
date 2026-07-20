//! Menú de bandeja mínimo (contrato item (g)): "Abrir en el navegador",
//! "Ver carpeta de datos", "Salir". Sin lógica de negocio acá — cada acción
//! es un one-liner que reusa helpers de `backend`/`util`.
//!
//! El tray se crea ÚNICAMENTE acá (`TrayIconBuilder`), a propósito NO
//! también vía `app.trayIcon` en `tauri.conf.json` — declarar ambos crearía
//! dos íconos de bandeja (uno estático sin el menú custom de abajo, y este).
//! `iconAsTemplate` (look monocromo que se adapta al tema de la barra de
//! menú de macOS) se replica acá con `.icon_as_template(true)` porque ya no
//! viene del JSON.
//!
//! Nota para quien compile esto por primera vez: `tauri::menu`/`tauri::tray`
//! es la superficie de la API de Tauri v2 que más cambió entre betas, y la
//! que este work package NO pudo validar con `cargo check` (no había
//! toolchain de Rust en la máquina donde se escribió — ver README.md). Si
//! `cargo build` se queja en este archivo, empezá por acá: la lógica (qué
//! hace cada item del menú) es simple y no debería tener que cambiar, lo que
//! puede variar son los nombres exactos de algún builder method. Doc
//! oficial: <https://v2.tauri.app/learn/system-tray/> y
//! <https://v2.tauri.app/learn/window-menu/>.

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::AppHandle;

use crate::backend;
use crate::listen;
use crate::util;

pub fn setup_tray(app: &AppHandle) -> tauri::Result<()> {
    let open_browser = MenuItem::with_id(
        app,
        "open_browser",
        "Abrir en el navegador",
        true,
        None::<&str>,
    )?;
    let open_data =
        MenuItem::with_id(app, "open_data", "Ver carpeta de datos", true, None::<&str>)?;
    let stop_listen = MenuItem::with_id(
        app,
        "stop_listen",
        "Detener escucha siempre",
        true,
        None::<&str>,
    )?;
    let quit = MenuItem::with_id(app, "quit", "Salir", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open_browser, &open_data, &stop_listen, &quit])?;

    let icon = app
        .default_window_icon()
        .cloned()
        .expect("falta el ícono por defecto de la app (ver src-tauri/icons/ y tauri.conf.json)");

    let app_for_events = app.clone();
    TrayIconBuilder::new()
        .icon(icon)
        .icon_as_template(true)
        .menu(&menu)
        .tooltip("Edecán")
        .on_menu_event(move |_app, event| {
            let id: &str = event.id().as_ref();
            match id {
                "open_browser" => {
                    let port = backend::current_port(&app_for_events);
                    util::open_in_default_browser(&format!("http://127.0.0.1:{port}/"));
                }
                "open_data" => {
                    let dir = backend::data_dir(&app_for_events);
                    let _ = std::fs::create_dir_all(&dir);
                    util::open_in_file_manager(&dir);
                }
                "stop_listen" => {
                    if let Err(err) = listen::set_enabled(app_for_events.clone(), false) {
                        eprintln!("[edecan-desktop] no se pudo detener la escucha en segundo plano: {err}");
                    }
                }
                "quit" => app_for_events.exit(0),
                _ => {}
            }
        })
        .build(app)?;

    Ok(())
}
