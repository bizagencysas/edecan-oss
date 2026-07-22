//! Centro de permisos del escritorio.
//!
//! La UI no adivina permisos ni abre URLs arbitrarias. Este módulo publica
//! un catálogo acotado por plataforma, consulta los estados que el sistema
//! operativo permite consultar y ejecuta únicamente acciones nativas
//! predefinidas. Apple y Windows no ofrecen un botón universal para
//! conceder todo: algunos permisos muestran un diálogo y otros obligan a
//! abrir la sección exacta de Configuración.

use serde::Serialize;

use crate::listen;

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PermissionStatus {
    Granted,
    NeedsAction,
    Unknown,
    NotRequired,
}

#[derive(Clone, Debug, Serialize)]
pub struct DesktopPermission {
    pub id: &'static str,
    pub title: &'static str,
    pub description: &'static str,
    pub level: &'static str,
    pub status: PermissionStatus,
    pub action_label: Option<&'static str>,
}

#[derive(Debug, Serialize)]
pub struct DesktopPermissionsState {
    pub platform: &'static str,
    pub application_name: &'static str,
    pub application_path: Option<String>,
    pub permissions: Vec<DesktopPermission>,
}

#[derive(Debug, Serialize)]
pub struct PermissionActionResult {
    pub permission_id: String,
    pub status: PermissionStatus,
    pub message: String,
}

pub fn get_state() -> DesktopPermissionsState {
    DesktopPermissionsState {
        platform: platform_name(),
        application_name: "Edecán",
        application_path: current_application_path(),
        permissions: permission_catalog(),
    }
}

pub async fn request(permission_id: String) -> Result<PermissionActionResult, String> {
    match permission_id.as_str() {
        "microphone" => request_microphone().await,
        "accessibility" => request_accessibility(),
        "screen_recording" => request_screen_recording(),
        "notifications" => open_permission_settings("notifications"),
        "full_disk_access" => open_permission_settings("full_disk_access"),
        "automation" => open_permission_settings("automation"),
        "reveal_application" => reveal_application(),
        // En Windows estas capacidades no requieren un consentimiento
        // global; los límites de UAC se solicitan por acción.
        "computer_control" | "files" => Ok(PermissionActionResult {
            permission_id,
            status: PermissionStatus::NotRequired,
            message: "Esta capacidad no necesita un permiso general en este sistema.".into(),
        }),
        _ => Err("Permiso de escritorio desconocido.".into()),
    }
}

async fn request_microphone() -> Result<PermissionActionResult, String> {
    match listen::request_microphone_access().await {
        Ok(()) => Ok(PermissionActionResult {
            permission_id: "microphone".into(),
            status: PermissionStatus::Granted,
            message: "El micrófono está disponible para Edecán.".into(),
        }),
        Err(error) => {
            // Si ya se rechazó antes, macOS/Windows no vuelven a enseñar el
            // diálogo. Abrir la sección exacta evita dejar a la persona con
            // un error técnico sin una salida concreta.
            let _ = open_settings_for("microphone");
            Ok(PermissionActionResult {
                permission_id: "microphone".into(),
                status: PermissionStatus::NeedsAction,
                message: format!(
                    "No se pudo usar el micrófono. Abrimos Configuración para que puedas permitirlo: {error}"
                ),
            })
        }
    }
}

#[cfg(target_os = "macos")]
fn permission_catalog() -> Vec<DesktopPermission> {
    vec![
        DesktopPermission {
            id: "microphone",
            title: "Micrófono",
            description: "Para hablar con Edecán y usar tu palabra clave incluso con la ventana oculta.",
            level: "essential",
            status: PermissionStatus::Unknown,
            action_label: Some("Comprobar y permitir"),
        },
        DesktopPermission {
            id: "accessibility",
            title: "Accesibilidad",
            description: "Permite que el control remoto mueva el mouse, escriba y use tus aplicaciones.",
            level: "essential",
            status: if macos_accessibility_granted() {
                PermissionStatus::Granted
            } else {
                PermissionStatus::NeedsAction
            },
            action_label: Some("Abrir Accesibilidad"),
        },
        DesktopPermission {
            id: "screen_recording",
            title: "Grabación de pantalla",
            description: "Permite ver la pantalla de esta Mac desde el teléfono emparejado.",
            level: "essential",
            status: if macos_screen_recording_granted() {
                PermissionStatus::Granted
            } else {
                PermissionStatus::NeedsAction
            },
            action_label: Some("Solicitar permiso"),
        },
        DesktopPermission {
            id: "notifications",
            title: "Notificaciones",
            description: "Para recordatorios, trabajos terminados y avisos importantes.",
            level: "recommended",
            status: PermissionStatus::Unknown,
            action_label: Some("Abrir Notificaciones"),
        },
        DesktopPermission {
            id: "automation",
            title: "Automatización de apps",
            description: "macOS puede pedir permiso por cada aplicación que Edecán necesite controlar.",
            level: "on_demand",
            status: PermissionStatus::Unknown,
            action_label: Some("Revisar Automatización"),
        },
        DesktopPermission {
            id: "full_disk_access",
            title: "Acceso total al disco",
            description: "Opcional. Úsalo solo si quieres que Edecán trabaje también con carpetas protegidas del sistema.",
            level: "optional",
            status: PermissionStatus::Unknown,
            action_label: Some("Abrir Acceso al disco"),
        },
    ]
}

#[cfg(target_os = "windows")]
fn permission_catalog() -> Vec<DesktopPermission> {
    vec![
        DesktopPermission {
            id: "microphone",
            title: "Micrófono",
            description: "Para hablar con Edecán y usar tu palabra clave en segundo plano.",
            level: "essential",
            status: PermissionStatus::Unknown,
            action_label: Some("Comprobar y permitir"),
        },
        DesktopPermission {
            id: "notifications",
            title: "Notificaciones",
            description: "Para recordatorios, trabajos terminados y avisos importantes.",
            level: "recommended",
            status: PermissionStatus::Unknown,
            action_label: Some("Abrir Notificaciones"),
        },
        DesktopPermission {
            id: "computer_control",
            title: "Mouse, teclado y pantalla",
            description: "Windows no exige un permiso global. Si una acción requiere administrador, mostrará UAC en ese momento.",
            level: "on_demand",
            status: PermissionStatus::NotRequired,
            action_label: None,
        },
        DesktopPermission {
            id: "files",
            title: "Archivos y carpetas",
            description: "Edecán puede trabajar con tus archivos normales; Windows protegerá las carpetas administrativas cuando corresponda.",
            level: "on_demand",
            status: PermissionStatus::NotRequired,
            action_label: None,
        },
    ]
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn permission_catalog() -> Vec<DesktopPermission> {
    vec![
        DesktopPermission {
            id: "microphone",
            title: "Micrófono",
            description: "Para hablar con Edecán y usar tu palabra clave. PipeWire, PulseAudio o ALSA pueden mostrar el consentimiento del escritorio al usarlo.",
            level: "essential",
            status: PermissionStatus::Unknown,
            action_label: Some("Comprobar permiso"),
        },
        DesktopPermission {
            id: "notifications",
            title: "Notificaciones",
            description: "Edecán usa el servicio de notificaciones de tu escritorio cuando está disponible.",
            level: "recommended",
            status: PermissionStatus::NotRequired,
            action_label: None,
        },
        DesktopPermission {
            id: "computer_control",
            title: "Mouse, teclado y pantalla",
            description: "Linux no tiene un permiso global único. X11 usa los permisos de tu sesión; algunos compositores Wayland bloquean captura o control y Edecán lo comprobará al usar la función.",
            level: "on_demand",
            status: PermissionStatus::Unknown,
            action_label: None,
        },
        DesktopPermission {
            id: "files",
            title: "Archivos y carpetas",
            description: "Edecán trabaja con los archivos permitidos para tu usuario; Flatpak, Snap y los portales conservan sus límites propios.",
            level: "on_demand",
            status: PermissionStatus::NotRequired,
            action_label: None,
        },
    ]
}

#[cfg(target_os = "macos")]
fn request_accessibility() -> Result<PermissionActionResult, String> {
    if macos_accessibility_granted() {
        return Ok(PermissionActionResult {
            permission_id: "accessibility".into(),
            status: PermissionStatus::Granted,
            message: "Accesibilidad ya está permitida.".into(),
        });
    }
    // Esta variante de la API no solo consulta: pide a macOS que agregue la
    // aplicación responsable a la lista y muestre el consentimiento nativo.
    // Si sigue pendiente, abrimos además la sección exacta.
    if macos_request_accessibility() {
        return Ok(PermissionActionResult {
            permission_id: "accessibility".into(),
            status: PermissionStatus::Granted,
            message: "Accesibilidad quedó permitida para Edecán.".into(),
        });
    }
    open_settings_for("accessibility")?;
    Ok(PermissionActionResult {
        permission_id: "accessibility".into(),
        status: PermissionStatus::NeedsAction,
        message: "Activa Edecán en Accesibilidad y vuelve a esta pantalla para comprobarlo.".into(),
    })
}

fn current_application_path() -> Option<String> {
    let executable = std::env::current_exe().ok()?;
    #[cfg(target_os = "macos")]
    {
        if let Some(bundle) = executable
            .ancestors()
            .find(|path| path.extension().is_some_and(|extension| extension == "app"))
        {
            return Some(bundle.display().to_string());
        }
    }
    Some(executable.display().to_string())
}

fn reveal_application() -> Result<PermissionActionResult, String> {
    let path = current_application_path()
        .ok_or_else(|| "No se pudo localizar el archivo de Edecán.".to_string())?;
    reveal_path(&path)?;
    Ok(PermissionActionResult {
        permission_id: "reveal_application".into(),
        status: PermissionStatus::NotRequired,
        message: format!("Mostramos el archivo exacto que debes seleccionar: {path}"),
    })
}

#[cfg(target_os = "macos")]
fn reveal_path(path: &str) -> Result<(), String> {
    std::process::Command::new("open")
        .args(["-R", path])
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("No se pudo mostrar Edecán en Finder: {error}"))
}

#[cfg(target_os = "windows")]
fn reveal_path(path: &str) -> Result<(), String> {
    std::process::Command::new("explorer.exe")
        .arg(format!("/select,{path}"))
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("No se pudo mostrar Edecán en el Explorador: {error}"))
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn reveal_path(path: &str) -> Result<(), String> {
    let parent = std::path::Path::new(path)
        .parent()
        .unwrap_or_else(|| std::path::Path::new(path));
    std::process::Command::new("xdg-open")
        .arg(parent)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("No se pudo mostrar la carpeta de Edecán: {error}"))
}

#[cfg(not(target_os = "macos"))]
fn request_accessibility() -> Result<PermissionActionResult, String> {
    Ok(PermissionActionResult {
        permission_id: "accessibility".into(),
        status: PermissionStatus::NotRequired,
        message: "Este sistema no requiere un permiso global de Accesibilidad.".into(),
    })
}

#[cfg(target_os = "macos")]
fn request_screen_recording() -> Result<PermissionActionResult, String> {
    if macos_screen_recording_granted() || macos_request_screen_recording() {
        return Ok(PermissionActionResult {
            permission_id: "screen_recording".into(),
            status: PermissionStatus::Granted,
            message: "Grabación de pantalla está permitida.".into(),
        });
    }
    open_settings_for("screen_recording")?;
    Ok(PermissionActionResult {
        permission_id: "screen_recording".into(),
        status: PermissionStatus::NeedsAction,
        message:
            "Activa Edecán en Grabación de pantalla. macOS puede pedir que reinicies la aplicación."
                .into(),
    })
}

#[cfg(not(target_os = "macos"))]
fn request_screen_recording() -> Result<PermissionActionResult, String> {
    Ok(PermissionActionResult {
        permission_id: "screen_recording".into(),
        status: PermissionStatus::NotRequired,
        message: "Este sistema no requiere un permiso global de captura de pantalla.".into(),
    })
}

fn open_permission_settings(permission_id: &str) -> Result<PermissionActionResult, String> {
    open_settings_for(permission_id)?;
    Ok(PermissionActionResult {
        permission_id: permission_id.to_string(),
        status: PermissionStatus::NeedsAction,
        message: "Abrimos la sección correcta de Configuración. El sistema operativo controla la decisión final.".into(),
    })
}

#[cfg(target_os = "macos")]
fn open_settings_for(permission_id: &str) -> Result<(), String> {
    let url = match permission_id {
        "microphone" => {
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
        }
        "accessibility" => {
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        }
        "screen_recording" => {
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
        }
        "notifications" => "x-apple.systempreferences:com.apple.Notifications-Settings.extension",
        "automation" => {
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
        }
        "full_disk_access" => {
            "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
        }
        _ => return Err("No existe una ruta de Configuración para ese permiso.".into()),
    };
    std::process::Command::new("open")
        .arg(url)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("No se pudo abrir Configuración del Sistema: {error}"))
}

#[cfg(target_os = "windows")]
fn open_settings_for(permission_id: &str) -> Result<(), String> {
    let uri = match permission_id {
        "microphone" => "ms-settings:privacy-microphone",
        "notifications" => "ms-settings:notifications",
        _ => return Err("No existe una ruta de Configuración para ese permiso.".into()),
    };
    std::process::Command::new("explorer.exe")
        .arg(uri)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("No se pudo abrir Configuración de Windows: {error}"))
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn open_settings_for(_permission_id: &str) -> Result<(), String> {
    Err("Este entorno de escritorio no ofrece una ruta universal de permisos.".into())
}

#[cfg(target_os = "macos")]
fn platform_name() -> &'static str {
    "macos"
}

#[cfg(target_os = "windows")]
fn platform_name() -> &'static str {
    "windows"
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn platform_name() -> &'static str {
    "linux"
}

#[cfg(target_os = "macos")]
fn macos_accessibility_granted() -> bool {
    #[link(name = "ApplicationServices", kind = "framework")]
    unsafe extern "C" {
        fn AXIsProcessTrusted() -> bool;
    }
    // SAFETY: función de consulta sin argumentos de la API pública de
    // Accessibility; no transfiere memoria ni conserva punteros.
    unsafe { AXIsProcessTrusted() }
}

#[cfg(target_os = "macos")]
fn macos_request_accessibility() -> bool {
    use core_foundation::base::TCFType;
    use core_foundation::boolean::CFBoolean;
    use core_foundation::dictionary::{CFDictionary, CFDictionaryRef};
    use core_foundation::string::CFString;

    #[link(name = "ApplicationServices", kind = "framework")]
    unsafe extern "C" {
        fn AXIsProcessTrustedWithOptions(options: CFDictionaryRef) -> bool;
    }

    let options: CFDictionary<CFString, CFBoolean> = CFDictionary::from_CFType_pairs(&[(
        CFString::from_static_string("AXTrustedCheckOptionPrompt"),
        CFBoolean::true_value(),
    )]);
    // SAFETY: la API pública solo lee el CFDictionary retenido durante esta
    // llamada y devuelve un booleano. El diccionario permanece vivo.
    unsafe { AXIsProcessTrustedWithOptions(options.as_concrete_TypeRef()) }
}

#[cfg(target_os = "macos")]
fn macos_screen_recording_granted() -> bool {
    #[link(name = "CoreGraphics", kind = "framework")]
    unsafe extern "C" {
        fn CGPreflightScreenCaptureAccess() -> bool;
    }
    // SAFETY: consulta booleana de CoreGraphics, sin argumentos ni punteros.
    unsafe { CGPreflightScreenCaptureAccess() }
}

#[cfg(target_os = "macos")]
fn macos_request_screen_recording() -> bool {
    #[link(name = "CoreGraphics", kind = "framework")]
    unsafe extern "C" {
        fn CGRequestScreenCaptureAccess() -> bool;
    }
    // SAFETY: API pública de CoreGraphics que muestra el consentimiento
    // nativo y devuelve únicamente si la captura quedó autorizada.
    unsafe { CGRequestScreenCaptureAccess() }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn permission_ids_are_unique_and_actions_are_known() {
        let permissions = permission_catalog();
        let ids: std::collections::HashSet<_> = permissions.iter().map(|item| item.id).collect();
        assert_eq!(ids.len(), permissions.len());
        assert!(permissions.iter().all(|item| !item.title.is_empty()));
        assert!(permissions.iter().all(|item| matches!(
            item.level,
            "essential" | "recommended" | "on_demand" | "optional"
        )));
    }

    #[test]
    fn state_reports_current_platform() {
        let state = get_state();
        assert_eq!(state.platform, platform_name());
        assert_eq!(state.application_name, "Edecán");
        assert!(state.application_path.is_some());
        assert!(!state.permissions.is_empty());
    }
}
