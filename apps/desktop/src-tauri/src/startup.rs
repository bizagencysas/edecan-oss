//! Inicio automático del asistente residente.
//!
//! El primer arranque habilita el inicio con la sesión del sistema. Después
//! se respeta para siempre la elección de la persona, incluida la decisión de
//! desactivarlo desde Ajustes. El argumento `--hidden` evita abrir una ventana
//! al iniciar sesión: quedan vivos el backend, el túnel y el menú de bandeja.

use serde::Serialize;
use std::ffi::OsStr;
use tauri::{AppHandle, Manager};
use tauri_plugin_autostart::ManagerExt;

const INITIALIZED_MARKER: &str = "autostart-initialized";
const AUTOSTART_DEFAULT_ENV: &str = "EDECAN_AUTOSTART_DEFAULT";

#[derive(Debug, Serialize)]
pub struct StartupState {
    pub enabled: bool,
}

fn marker_path(app: &AppHandle) -> Result<std::path::PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|directory| directory.join(INITIALIZED_MARKER))
        .map_err(|error| format!("No se pudo resolver la configuración de Edecán: {error}"))
}

pub fn initialize_default(app: &AppHandle) -> Result<(), String> {
    // `cargo tauri dev` no debe registrar el binario temporal del repositorio
    // como aplicación de inicio. El instalador release sí lo hace una vez.
    if cfg!(debug_assertions) {
        return Ok(());
    }
    let marker = marker_path(app)?;
    if marker.exists() {
        return Ok(());
    }
    if autostart_default_enabled(std::env::var_os(AUTOSTART_DEFAULT_ENV).as_deref()) {
        app.autolaunch()
            .enable()
            .map_err(|error| format!("No se pudo activar el inicio automático: {error}"))?;
    }
    if let Some(parent) = marker.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("No se pudo crear la configuración: {error}"))?;
    }
    std::fs::write(&marker, b"initialized\n")
        .map_err(|error| format!("No se pudo guardar la preferencia: {error}"))
}

/// El producto sigue habilitando el modo residente en el primer arranque.
/// Empaquetadores administrados y smokes nativos pueden desactivar ese
/// *default* sin cambiar el comportamiento de una preferencia ya guardada.
/// Cualquier valor desconocido falla hacia el default histórico (activado).
fn autostart_default_enabled(value: Option<&OsStr>) -> bool {
    !matches!(
        value.and_then(OsStr::to_str).map(str::trim).map(str::to_ascii_lowercase),
        Some(value) if matches!(value.as_str(), "0" | "false" | "off" | "disabled")
    )
}

pub fn get_state(app: &AppHandle) -> Result<StartupState, String> {
    app.autolaunch()
        .is_enabled()
        .map(|enabled| StartupState { enabled })
        .map_err(|error| format!("No se pudo comprobar el inicio automático: {error}"))
}

pub fn set_enabled(app: &AppHandle, enabled: bool) -> Result<StartupState, String> {
    let manager = app.autolaunch();
    if enabled {
        manager
            .enable()
            .map_err(|error| format!("No se pudo activar el inicio automático: {error}"))?;
    } else {
        manager
            .disable()
            .map_err(|error| format!("No se pudo desactivar el inicio automático: {error}"))?;
    }
    let marker = marker_path(app)?;
    if let Some(parent) = marker.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("No se pudo crear la configuración: {error}"))?;
    }
    std::fs::write(&marker, b"initialized\n")
        .map_err(|error| format!("No se pudo guardar la preferencia: {error}"))?;
    get_state(app)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn autostart_is_enabled_by_default_and_unknown_values_fail_closed_to_that_default() {
        assert!(autostart_default_enabled(None));
        assert!(autostart_default_enabled(Some(OsStr::new("enabled"))));
        assert!(autostart_default_enabled(Some(OsStr::new("unexpected"))));
    }

    #[test]
    fn managed_builds_can_disable_only_the_first_run_default() {
        for value in ["0", "false", "OFF", " disabled "] {
            assert!(!autostart_default_enabled(Some(OsStr::new(value))));
        }
    }
}
