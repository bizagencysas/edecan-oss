// La ventana "main" carga desde una URL EXTERNA (`http://127.0.0.1:<puerto>/`,
// ver backend.rs::show_main_window -- el propio backend Python sirve la web
// estática, Tauri no la empaqueta), no desde el protocolo interno de Tauri.
// Por eso Tauri la trata como origen "remoto" para el sistema de permisos.
// Sin un permiso generado por comando y sin autorizar ese origen en
// `capabilities/default.json`, los invoke() fallan por ACL.
use std::path::{Path, PathBuf};

/// En desarrollo el sidecar congelado todavía no existe: `backend.rs` cae a
/// `uv run --all-packages edecan` desde el código fuente. Tauri validaba
/// `bundle.externalBin` antes de compilar y abortaba `cargo check`/`cargo
/// tauri dev`, por lo que ese fallback era inalcanzable en un clon limpio.
///
/// Solo desactivamos `externalBin` para perfiles no-release y únicamente si
/// falta el archivo del target actual. Una build release conserva el fallo
/// cerrado: nunca puede producir un instalador que olvidó empaquetar su
/// backend. Si Tauri CLI ya suministró un `TAURI_CONFIG`, se preservan todos
/// sus campos y se reemplaza solo `bundle.externalBin`.
fn configure_source_backend_for_dev(manifest_dir: &Path, target: &str, profile: &str) {
    if profile == "release" || sidecar_path(manifest_dir, target).is_file() {
        return;
    }

    let mut config = std::env::var("TAURI_CONFIG")
        .ok()
        .map(|raw| {
            serde_json::from_str::<serde_json::Value>(&raw)
                .expect("TAURI_CONFIG debe ser JSON válido")
        })
        .unwrap_or_else(|| serde_json::json!({}));
    let root = config
        .as_object_mut()
        .expect("TAURI_CONFIG debe ser un objeto JSON");
    let bundle = root
        .entry("bundle")
        .or_insert_with(|| serde_json::json!({}))
        .as_object_mut()
        .expect("TAURI_CONFIG.bundle debe ser un objeto JSON");
    bundle.insert("externalBin".to_string(), serde_json::json!([]));

    // SAFETY: build.rs corre en un proceso de un solo hilo antes de invocar
    // tauri-build; no existe otro hilo que pueda leer el entorno a la vez.
    unsafe {
        std::env::set_var(
            "TAURI_CONFIG",
            serde_json::to_string(&config).expect("no se pudo serializar TAURI_CONFIG"),
        );
    }
    println!(
        "cargo:warning=sidecar congelado ausente; perfil {profile} usará edecan_local desde fuente"
    );
}

fn sidecar_path(manifest_dir: &Path, target: &str) -> PathBuf {
    let suffix = if target.contains("windows") {
        ".exe"
    } else {
        ""
    };
    manifest_dir
        .join("binaries")
        .join(format!("edecan-local-{target}{suffix}"))
}

fn main() {
    let manifest_dir =
        PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR no definido"));
    let target = std::env::var("TARGET").expect("TARGET no definido");
    let profile = std::env::var("PROFILE").expect("PROFILE no definido");
    println!(
        "cargo:rerun-if-changed={}",
        manifest_dir.join("binaries").display()
    );
    configure_source_backend_for_dev(&manifest_dir, &target, &profile);

    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "retry_backend",
            "quit_app",
            "always_listen_get_state",
            "always_listen_record_sample",
            "always_listen_train",
            "always_listen_set_enabled",
            "always_listen_reset_training",
        ]),
    ))
    .expect("error corriendo tauri-build");
}
