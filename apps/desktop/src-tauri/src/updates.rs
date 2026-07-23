//! Actualizaciones de escritorio firmadas, sin reclonar el repositorio.
//!
//! El canal solo selecciona un manifiesto HTTPS. El artefacto indicado por
//! ese manifiesto siempre debe pasar la verificación minisign que hace el
//! plugin con la clave pública compilada en `tauri.conf.json`; controlar el
//! JSON o la rama de canales no basta para instalar código.

use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter};
use tauri_plugin_updater::{Update, UpdaterExt};

use crate::backend;

const STABLE_ENDPOINT: &str =
    "https://raw.githubusercontent.com/bizagencysas/edecan-oss/update-channels/stable.json";
const PREVIEW_ENDPOINT: &str =
    "https://raw.githubusercontent.com/bizagencysas/edecan-oss/update-channels/preview.json";

struct PendingDesktopUpdate {
    update: Update,
    version: String,
    channel: String,
}

#[derive(Default)]
pub struct DesktopUpdateState(Mutex<Option<PendingDesktopUpdate>>);

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopUpdateMetadata {
    pub version: String,
    pub current_version: String,
    pub notes: Option<String>,
    pub published_at: Option<String>,
    pub channel: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopUpdateCheckResult {
    pub current_version: String,
    pub update: Option<DesktopUpdateMetadata>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "event", rename_all = "snake_case")]
enum DesktopUpdateProgress {
    Started { content_length: Option<u64> },
    Progress { chunk_length: usize },
    Finished,
}

fn endpoint_for(channel: &str) -> Result<&'static str, String> {
    match channel {
        "stable" => Ok(STABLE_ENDPOINT),
        "preview" => Ok(PREVIEW_ENDPOINT),
        _ => Err("El canal de actualización no es válido.".to_string()),
    }
}

fn friendly_error(action: &str, error: impl std::fmt::Display) -> String {
    let raw = error.to_string();
    if raw.contains("404") || raw.contains("Not Found") {
        return "Este canal todavía no tiene una actualización publicada.".to_string();
    }
    if raw.contains("timed out") || raw.contains("timeout") {
        return format!(
            "No se pudo {action} porque la conexión tardó demasiado. Inténtalo de nuevo."
        );
    }
    format!("No se pudo {action}. Comprueba tu conexión e inténtalo de nuevo. Detalle: {raw}")
}

pub async fn check(
    app: &AppHandle,
    state: &DesktopUpdateState,
    channel: &str,
) -> Result<DesktopUpdateCheckResult, String> {
    let endpoint = endpoint_for(channel)?;
    let endpoint = endpoint
        .parse()
        .map_err(|error| friendly_error("preparar la búsqueda de actualizaciones", error))?;
    let before_exit_app = app.clone();
    let updater = app
        .updater_builder()
        .on_before_exit(move || backend::kill_backend(&before_exit_app))
        .endpoints(vec![endpoint])
        .map_err(|error| friendly_error("preparar la búsqueda de actualizaciones", error))?
        .timeout(Duration::from_secs(20))
        .build()
        .map_err(|error| friendly_error("preparar la búsqueda de actualizaciones", error))?;
    let update = updater
        .check()
        .await
        .map_err(|error| friendly_error("buscar actualizaciones", error))?;
    let metadata = update.as_ref().map(|candidate| DesktopUpdateMetadata {
        version: candidate.version.clone(),
        current_version: candidate.current_version.clone(),
        notes: candidate.body.clone(),
        published_at: candidate.date.map(|date| date.to_string()),
        channel: channel.to_string(),
    });

    *state
        .0
        .lock()
        .map_err(|_| "El administrador de actualizaciones no está disponible.".to_string())? =
        update.map(|candidate| PendingDesktopUpdate {
            version: candidate.version.clone(),
            channel: channel.to_string(),
            update: candidate,
        });
    Ok(DesktopUpdateCheckResult {
        current_version: app.package_info().version.to_string(),
        update: metadata,
    })
}

pub async fn install(
    app: &AppHandle,
    state: &DesktopUpdateState,
    expected_version: &str,
    channel: &str,
) -> Result<(), String> {
    let update = state
        .0
        .lock()
        .map_err(|_| "El administrador de actualizaciones no está disponible.".to_string())?
        .as_ref()
        .filter(|pending| pending.version == expected_version && pending.channel == channel)
        .map(|pending| pending.update.clone())
        .ok_or_else(|| {
            "La actualización disponible cambió. Busca de nuevo antes de instalarla.".to_string()
        })?;

    let progress_app = app.clone();
    let mut started = false;
    update
        .download_and_install(
            move |chunk_length, content_length| {
                if !started {
                    started = true;
                    let _ = progress_app.emit(
                        "edecan://update-progress",
                        DesktopUpdateProgress::Started { content_length },
                    );
                }
                let _ = progress_app.emit(
                    "edecan://update-progress",
                    DesktopUpdateProgress::Progress { chunk_length },
                );
            },
            {
                let progress_app = app.clone();
                move || {
                    let _ = progress_app
                        .emit("edecan://update-progress", DesktopUpdateProgress::Finished);
                }
            },
        )
        .await
        .map_err(|error| friendly_error("descargar e instalar la actualización", error))?;

    // En Windows el instalador puede cerrar la app durante install(). En
    // macOS y Linux llegamos aquí: apagar el sidecar antes de reiniciar evita
    // dejar procesos viejos o archivos bloqueados.
    backend::kill_backend(app);
    app.restart();
}

#[cfg(test)]
mod tests {
    use super::{endpoint_for, friendly_error, PREVIEW_ENDPOINT, STABLE_ENDPOINT};

    #[test]
    fn channels_are_closed_and_https_only() {
        assert_eq!(endpoint_for("stable").unwrap(), STABLE_ENDPOINT);
        assert_eq!(endpoint_for("preview").unwrap(), PREVIEW_ENDPOINT);
        assert!(endpoint_for("nightly").is_err());
        assert!(STABLE_ENDPOINT.starts_with("https://"));
        assert!(PREVIEW_ENDPOINT.starts_with("https://"));
    }

    #[test]
    fn updater_errors_are_human_readable() {
        assert_eq!(
            friendly_error("buscar actualizaciones", "server returned 404 Not Found"),
            "Este canal todavía no tiene una actualización publicada."
        );
        assert!(
            friendly_error("buscar actualizaciones", "request timed out")
                .contains("tardó demasiado")
        );
    }
}
