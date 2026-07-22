//! Punto de entrada del asistente residente. Un clic izquierdo recupera y
//! enfoca la ventana; el menú contextual expone solo acciones operativas
//! cortas y una salida completa explícita.
//!
//! El tray se crea ÚNICAMENTE acá (`TrayIconBuilder`), a propósito NO
//! también vía `app.trayIcon` en `tauri.conf.json` — declarar ambos crearía
//! dos íconos de bandeja (uno estático sin el menú custom de abajo, y este).
//! `iconAsTemplate` (look monocromo que se adapta al tema de la barra de
//! menú de macOS) se replica acá con `.icon_as_template(true)` porque ya no
//! viene del JSON.
//!
use std::sync::Mutex;

use tauri::menu::{CheckMenuItem, Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager};

use crate::backend;
use crate::listen;
use crate::util;

/// Icono monocromático transparente para la barra de menú de macOS.
///
/// El icono principal tiene un recuadro violeta casi opaco. macOS usa el
/// canal alfa de los iconos `template`, por lo que reutilizarlo convertía a
/// Edecán en un cuadrado blanco. Esta máscara dibuja únicamente el robot con
/// audífonos y deja el fondo completamente transparente.
#[cfg(target_os = "macos")]
fn macos_menu_bar_icon() -> tauri::image::Image<'static> {
    const SIZE: u32 = 32;
    let mut rgba = vec![0_u8; (SIZE * SIZE * 4) as usize];
    let mut paint = |x: i32, y: i32| {
        if (0..SIZE as i32).contains(&x) && (0..SIZE as i32).contains(&y) {
            let index = ((y as u32 * SIZE + x as u32) * 4) as usize;
            rgba[index..index + 4].copy_from_slice(&[0, 0, 0, 255]);
        }
    };

    // Diadema circular superior.
    for y in 2..20 {
        for x in 2..30 {
            let dx = x - 16;
            let dy = y - 15;
            let radius_sq = dx * dx + dy * dy;
            if (121..=169).contains(&radius_sq) && y <= 15 {
                paint(x, y);
            }
        }
    }
    // Auriculares.
    for y in 13..23 {
        for x in 3..8 {
            paint(x, y);
            paint(31 - x, y);
        }
    }
    // Cara redondeada, como contorno para que no se convierta en una masa.
    for y in 9..25 {
        for x in 8..24 {
            let border = x <= 10 || x >= 21 || y <= 11 || y >= 22;
            let rounded_corner = !((x <= 9 || x >= 22) && (y <= 10 || y >= 23));
            if border && rounded_corner {
                paint(x, y);
            }
        }
    }
    // Ojos y sonrisa.
    for y in 14..17 {
        for x in 12..15 {
            paint(x, y);
        }
        for x in 18..21 {
            paint(x, y);
        }
    }
    for x in 13..20 {
        paint(x, 20);
    }

    tauri::image::Image::new_owned(rgba, SIZE, SIZE)
}

#[derive(Default)]
pub struct TrayState {
    listen_item: Mutex<Option<CheckMenuItem<tauri::Wry>>>,
}

fn is_primary_activation(button: MouseButton, state: MouseButtonState) -> bool {
    button == MouseButton::Left && state == MouseButtonState::Up
}

pub(crate) fn show_and_focus_main(app: &AppHandle) {
    let Some(window) = app.get_webview_window("main") else {
        // Mientras el sidecar arranca, el splash ya está visible. `main` se
        // crea únicamente después del marcador READY, así que acá no hay
        // una segunda ventana que fabricar ni una URL insegura que adivinar.
        return;
    };

    if let Err(err) = window.show() {
        eprintln!("[edecan-desktop] no se pudo mostrar la ventana principal: {err}");
        return;
    }
    let _ = window.unminimize();
    if let Err(err) = window.set_focus() {
        eprintln!("[edecan-desktop] no se pudo enfocar la ventana principal: {err}");
    }
}

pub(crate) fn refresh_listen_state(app: &AppHandle) {
    let item = app.state::<TrayState>().listen_item.lock().unwrap().clone();
    if let Some(item) = item {
        let _ = item.set_checked(listen::is_enabled(app));
    }
}

pub fn setup_tray(app: &AppHandle) -> tauri::Result<()> {
    let status = MenuItem::with_id(app, "status", "● Edecán activo", false, None::<&str>)?;
    let open_app = MenuItem::with_id(app, "open_app", "Abrir Edecán", true, None::<&str>)?;
    let open_browser = MenuItem::with_id(
        app,
        "open_browser",
        "Abrir en el navegador",
        true,
        None::<&str>,
    )?;
    let open_data =
        MenuItem::with_id(app, "open_data", "Ver carpeta de datos", true, None::<&str>)?;
    let toggle_listen = CheckMenuItem::with_id(
        app,
        "toggle_listen",
        "Escucha siempre",
        true,
        listen::is_enabled(app),
        None::<&str>,
    )?;
    let quit = MenuItem::with_id(app, "quit", "Salir completamente", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &status,
            &open_app,
            &open_browser,
            &open_data,
            &toggle_listen,
            &quit,
        ],
    )?;

    #[cfg(target_os = "macos")]
    let icon = macos_menu_bar_icon();
    #[cfg(not(target_os = "macos"))]
    let icon = app
        .default_window_icon()
        .cloned()
        .expect("falta el ícono por defecto de la app (ver src-tauri/icons/ y tauri.conf.json)");

    TrayIconBuilder::new()
        .icon(icon)
        .icon_as_template(true)
        .menu(&menu)
        .tooltip("Edecán")
        // En macOS/Windows el clic izquierdo abre Edecán; el menú queda en
        // el clic secundario. Linux conserva el comportamiento que permita
        // su implementación de bandeja, donde este flag no está soportado.
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button,
                button_state,
                ..
            } = event
            {
                if is_primary_activation(button, button_state) {
                    show_and_focus_main(tray.app_handle());
                }
            }
        })
        .on_menu_event(move |app, event| {
            let id: &str = event.id().as_ref();
            match id {
                "open_app" => show_and_focus_main(app),
                "open_browser" => {
                    if let Some(url) = backend::current_local_ui_url(app) {
                        util::open_in_default_browser(&url);
                    }
                }
                "open_data" => {
                    let dir = backend::data_dir(app);
                    let _ = std::fs::create_dir_all(&dir);
                    util::open_in_file_manager(&dir);
                }
                "toggle_listen" => {
                    let next = !listen::is_enabled(app);
                    if let Err(err) = listen::set_enabled(app.clone(), next) {
                        eprintln!(
                            "[edecan-desktop] no se pudo cambiar la escucha en segundo plano: {err}"
                        );
                    }
                    refresh_listen_state(app);
                }
                "quit" => app.exit(0),
                _ => {}
            }
        })
        .build(app)?;

    *app.state::<TrayState>().listen_item.lock().unwrap() = Some(toggle_listen);

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_completed_primary_click_restores_the_window() {
        assert!(is_primary_activation(
            MouseButton::Left,
            MouseButtonState::Up
        ));
        assert!(!is_primary_activation(
            MouseButton::Left,
            MouseButtonState::Down
        ));
        assert!(!is_primary_activation(
            MouseButton::Right,
            MouseButtonState::Up
        ));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn menu_bar_icon_has_a_transparent_background_and_visible_glyph() {
        let icon = macos_menu_bar_icon();
        let rgba = icon.rgba();
        assert_eq!(icon.width(), 32);
        assert_eq!(icon.height(), 32);
        assert_eq!(rgba[3], 0, "la esquina debe ser transparente");
        assert!(rgba.chunks_exact(4).any(|pixel| pixel[3] == 255));
        assert!(rgba.chunks_exact(4).any(|pixel| pixel[3] == 0));
    }
}
