//! Helpers cross-platform sin dependencias extra de Tauri.
//!
//! Abren una URL en el navegador por defecto o una carpeta en el explorador
//! de archivos del sistema operativo usando directamente el comando nativo
//! de cada plataforma (`std::process::Command`) en vez de sumar
//! `tauri-plugin-opener` — ese plugin no está en la lista de dependencias
//! de este work package (Cargo.toml solo trae `tauri` + `tauri-plugin-shell`
//! + serde/serde_json + tokio), y estos dos casos de uso son lo bastante
//! simples como para no necesitarlo.

/// Abre `url` con el navegador/aplicación por defecto del sistema.
pub fn open_in_default_browser(url: &str) {
    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("open").arg(url).spawn();
    }
    #[cfg(target_os = "windows")]
    {
        // El "" vacío es el título de ventana que exige `start` cuando el
        // argumento siguiente puede llevar espacios o comillas.
        let _ = std::process::Command::new("cmd")
            .args(["/C", "start", "", url])
            .spawn();
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = std::process::Command::new("xdg-open").arg(url).spawn();
    }
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
