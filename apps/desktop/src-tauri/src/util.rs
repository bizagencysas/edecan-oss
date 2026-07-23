//! Helpers cross-platform sin dependencias extra de Tauri.
//!
//! Abren una URL en el navegador por defecto o una carpeta en el explorador
//! de archivos del sistema operativo usando directamente el comando nativo
//! de cada plataforma (`std::process::Command`) en vez de sumar
//! `tauri-plugin-opener` — ese plugin no está en la lista de dependencias
//! de este work package (Cargo.toml solo trae `tauri` + `tauri-plugin-shell`
//! + serde/serde_json + tokio), y estos dos casos de uso son lo bastante
//! simples como para no necesitarlo.

/// Dominios externos que la WebView puede delegar al navegador del sistema.
///
/// Mantener esta lista cerrada evita convertir un eventual XSS en un
/// lanzador de enlaces arbitrarios o de esquemas locales. Los portales son
/// metadatos públicos de onboarding; sumar uno nuevo exige una revisión
/// explícita aquí y en `apps/web/src/lib/connector-guides.ts`.
const OFFICIAL_EXTERNAL_HOSTS: &[&str] = &[
    "adsmanager.facebook.com",
    "api.slack.com",
    "console.cloud.google.com",
    "console.twilio.com",
    "developer.x.com",
    "developers.facebook.com",
    "discord.com",
    "entra.microsoft.com",
    "github.com",
    "learn.microsoft.com",
    "mcp.facebook.com",
    "t.me",
    "www.linkedin.com",
];

pub fn validate_external_url(url: &str) -> Result<(), String> {
    let parsed = tauri::Url::parse(url).map_err(|_| "El enlace no es una URL válida.".to_string())?;
    if parsed.scheme() != "https" {
        return Err("Solo se pueden abrir enlaces HTTPS.".to_string());
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("El enlace no puede contener credenciales.".to_string());
    }
    let host = parsed
        .host_str()
        .ok_or_else(|| "El enlace no tiene un dominio válido.".to_string())?
        .to_ascii_lowercase();
    if !OFFICIAL_EXTERNAL_HOSTS.contains(&host.as_str()) {
        return Err(format!("El dominio externo «{host}» no está autorizado."));
    }
    Ok(())
}

/// Abre `url` con el navegador/aplicación por defecto del sistema.
pub fn open_in_default_browser(url: &str) -> Result<(), String> {
    validate_external_url(url)?;
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("/usr/bin/open")
            .arg(url)
            .spawn()
            .map_err(|err| format!("No se pudo abrir el navegador: {err}"))?;
    }
    #[cfg(target_os = "windows")]
    {
        // El "" vacío es el título de ventana que exige `start` cuando el
        // argumento siguiente puede llevar espacios o comillas.
        std::process::Command::new("cmd")
            .args(["/C", "start", "", url])
            .spawn()
            .map_err(|err| format!("No se pudo abrir el navegador: {err}"))?;
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        std::process::Command::new("xdg-open")
            .arg(url)
            .spawn()
            .map_err(|err| format!("No se pudo abrir el navegador: {err}"))?;
    }
    Ok(())
}

/// Abre `path` (una carpeta) en el explorador de archivos del sistema
/// (Finder/Explorer/lo que corresponda en Linux).
pub fn open_in_file_manager(path: &std::path::Path) {
    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("open").arg(path).spawn();
    }
    #[cfg(target_os = "windows")]
    {
        let _ = std::process::Command::new("explorer").arg(path).spawn();
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = std::process::Command::new("xdg-open").arg(path).spawn();
    }
}

#[cfg(test)]
mod tests {
    use super::validate_external_url;

    #[test]
    fn acepta_portales_oficiales_de_conectores() {
        for url in [
            "https://console.cloud.google.com/apis/credentials",
            "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
            "https://developers.facebook.com/apps/",
            "https://developer.x.com/en/portal/dashboard",
            "https://www.linkedin.com/developers/apps",
            "https://api.slack.com/apps",
            "https://t.me/BotFather",
        ] {
            assert_eq!(validate_external_url(url), Ok(()), "{url}");
        }
    }

    #[test]
    fn rechaza_esquemas_credenciales_y_dominios_arbitrarios() {
        assert!(validate_external_url("http://console.cloud.google.com").is_err());
        assert!(validate_external_url("file:///etc/passwd").is_err());
        assert!(validate_external_url("https://usuario:clave@github.com").is_err());
        assert!(validate_external_url("https://github.com.ejemplo.com").is_err());
    }
}
