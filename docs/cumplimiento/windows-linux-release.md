# Windows y Linux: gate de release nativo

Estado del contrato de escritorio de Edecán 0.7.0. Este documento separa lo
que puede comprobarse desde cualquier checkout de la evidencia que solo existe
después de ejecutar el pipeline en el sistema operativo correspondiente.

| Área | Windows x64 | Linux x64 | Gate |
|---|---|---|---|
| Shell | Tauri + WebView2 | Tauri + WebKitGTK 4.1 | Compilación Rust nativa |
| Instaladores | NSIS por usuario + MSI | AppImage + `.deb` + `.rpm` | `build-app.ps1` / `build-app.sh` |
| Backend | `edecan-local.exe` PyInstaller onefile | `edecan-local` PyInstaller onefile | Arranque y `/healthz` reales |
| Base local | PostgreSQL embebido x64 | PostgreSQL embebido x64 | El sidecar debe llegar a READY |
| FyDesign | Node 22, MCP, Chromium, ffmpeg, ffprobe, yt-dlp | Mismo contenido | Inspección de cada paquete y validación al arrancar |
| Cierre | `CloseMainWindow`, sin árbol huérfano | WM_DELETE_WINDOW bajo Xvfb/Openbox, sin huérfanos | Smoke nativo |
| Residente | Plugin de autoinicio + bandeja | Plugin de autoinicio + bandeja | Tests Rust; estado configurable en Ajustes |
| Permisos | Micrófono/Notificaciones + UAC por acción | Audio, X11 y límites Wayland explícitos | Catálogo y textos específicos por plataforma |

## Evidencia que genera CI

- `desktop-windows` corre en `windows-2025`, compila NSIS/MSI, extrae el MSI,
  instala NSIS en una carpeta efímera y ejercita el artefacto instalado.
- `desktop-linux` corre en Ubuntu 22.04, compila los tres paquetes y ejecuta el
  AppImage dentro de Xvfb, D-Bus y Openbox.
- Los artefactos de `main` se guardan durante 14 días para QA, pero el pipeline
  no los presenta como un release firmado.

## Límites que siguen fuera del checkout

- Authenticode/SmartScreen requiere el certificado de quien distribuye.
- APT/RPM firmado requiere una clave y repositorio de distribución.
- El pipeline nativo debe terminar verde después de subir estos cambios. Un
  `cargo test` en macOS no es evidencia de que NSIS, MSI, AppImage, `.deb` o
  `.rpm` hayan corrido.
- Linux ARM64 no incluye PostgreSQL embebido. Usa el modo self-host con una
  base externa hasta que `pgserver` publique ese runtime.
