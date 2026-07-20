// La ventana "main" carga desde una URL EXTERNA (`http://127.0.0.1:<puerto>/`,
// ver backend.rs::show_main_window -- el propio backend Python sirve la web
// estática, Tauri no la empaqueta), no desde el protocolo interno de Tauri.
// Por eso Tauri la trata como origen "remoto" para el sistema de permisos.
// Sin un permiso generado por comando y sin autorizar ese origen en
// `capabilities/default.json`, los invoke() fallan por ACL.
fn main() {
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
