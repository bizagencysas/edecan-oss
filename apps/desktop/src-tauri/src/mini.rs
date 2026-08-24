//! Ventana flotante de pregunta rápida (`label = "mini"`).
//!
//! Reusa la misma UI local que `main` (sidecar en 127.0.0.1) con el hash
//! `#edecan_mini=1` para que la web pinte un compositor compacto. Cerrarla
//! solo la oculta: el backend y el tray siguen vivos.

use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};

use crate::backend;

const MINI_LABEL: &str = "mini";

pub fn show_mini(app: &AppHandle) {
    let Some(url) = backend::current_mini_ui_url(app) else {
        eprintln!("[edecan-desktop] la ventana mini espera a que el backend local esté listo");
        return;
    };
    let parsed = match tauri::Url::parse(&url) {
        Ok(value) => value,
        Err(err) => {
            eprintln!("[edecan-desktop] URL inválida para la ventana mini: {err}");
            return;
        }
    };

    if let Some(existing) = app.get_webview_window(MINI_LABEL) {
        let _ = existing.show();
        let _ = existing.unminimize();
        if let Err(err) = existing.set_focus() {
            eprintln!("[edecan-desktop] no se pudo enfocar la ventana mini: {err}");
        }
        return;
    }

    let built = WebviewWindowBuilder::new(app, MINI_LABEL, WebviewUrl::External(parsed))
        .title("Preguntar rápido")
        .user_agent(backend::DESKTOP_USER_AGENT)
        .inner_size(440.0, 180.0)
        .min_inner_size(360.0, 140.0)
        .resizable(true)
        .always_on_top(true)
        .decorations(false)
        .visible(true)
        .center()
        .build();

    match built {
        Ok(window) => {
            if let Err(err) = window.set_focus() {
                eprintln!("[edecan-desktop] no se pudo enfocar la ventana mini: {err}");
            }
        }
        Err(err) => {
            eprintln!("[edecan-desktop] no se pudo crear la ventana mini: {err}");
        }
    }
}

pub fn hide_mini(app: &AppHandle) {
    let Some(window) = app.get_webview_window(MINI_LABEL) else {
        return;
    };
    if let Err(err) = window.hide() {
        eprintln!("[edecan-desktop] no se pudo ocultar la ventana mini: {err}");
    }
}

pub fn toggle_mini(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(MINI_LABEL) {
        match window.is_visible() {
            Ok(true) => hide_mini(app),
            Ok(false) | Err(_) => {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }
        return;
    }
    show_mini(app);
}
