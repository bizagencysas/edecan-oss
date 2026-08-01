// Evita que se abra una consola extra en Windows en builds release. No
// quitar — es el boilerplate estándar que genera `create-tauri-app` v2.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    edecan_desktop_lib::run();
}
