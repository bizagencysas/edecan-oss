#!/usr/bin/env bash
# apps/desktop/scripts/build-app.sh
#
# Build de producción completo para macOS y Linux x64. Windows tiene el flujo
# equivalente en `build-app.ps1`. Los targets se separan en los archivos
# `tauri.<plataforma>.conf.json` que Tauri mezcla automáticamente.
# Backend empaquetado (scripts/build-backend.sh) seguido del instalador nativo
# (`cargo tauri build` — app+dmg en macOS, AppImage+deb+rpm en Linux): punto de
# entrada reproducible para armar un release real.
#
# En macOS, si no se aporta un Developer ID, Tauri aplica una firma ad-hoc.
# No identifica a un desarrollador ni elimina el aviso de Gatekeeper, pero sí
# deja el bundle internamente íntegro (incluido el sidecar) y evita distribuir
# una app con un sello de recursos inválido. Firma Developer ID y notarización
# siguen siendo bring-your-own; ver docs/desktop.md "Firma de código".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TAURI_CLI_VERSION="2.11.4"
PLATFORM="$(uname -s)"
ARCH="$(uname -m)"

case "$PLATFORM" in
  Darwin)
    PLATFORM_LABEL="macOS"
    if [[ -n "${EDECAN_MACOS_CODESIGN_IDENTITY:-}" ]]; then
      export APPLE_SIGNING_IDENTITY="$EDECAN_MACOS_CODESIGN_IDENTITY"
    fi
    # Un bundle sin firma exterior hereda la firma del ejecutable y falla
    # `codesign --verify --deep --strict`. `-` es la identidad ad-hoc nativa
    # de codesign. Una identidad real definida por el empaquetador siempre
    # tiene prioridad.
    if [[ -z "${APPLE_SIGNING_IDENTITY:-}" ]]; then
      export APPLE_SIGNING_IDENTITY="-"
      # Una firma ad-hoc no tiene requirement de identidad estable: macOS
      # ancla los permisos TCC (Grabación de pantalla, Accesibilidad) al
      # cdhash EXACTO de esta build, así que CADA rebuild los invalida en
      # silencio — el interruptor sigue encendido en Configuración del
      # Sistema pero tccd lo ignora ("concesión zombi", ver
      # docs/control-remoto.md, sección de solución de problemas).
      echo "aviso: firmando ad-hoc (sin EDECAN_MACOS_CODESIGN_IDENTITY): los permisos de" >&2
      echo "       Grabación de pantalla/Accesibilidad quedarán anclados a ESTA build y" >&2
      echo "       cada rebuild los invalida aunque el interruptor se vea encendido." >&2
      echo "       Para permisos que sobrevivan rebuilds define EDECAN_MACOS_CODESIGN_IDENTITY" >&2
      echo "       con un certificado real (Developer ID o Apple Development)." >&2
    fi
    ;;
  Linux)
    if [[ "$ARCH" != "x86_64" && "$ARCH" != "amd64" ]]; then
      echo "error: el instalador Linux local-first requiere x86_64 (detectado: $ARCH)." >&2
      echo "       En Linux ARM64 usa self-hosting con EDECAN_DATABASE_URL; pgserver no publica ese runtime embebido." >&2
      exit 1
    fi
    PLATFORM_LABEL="Linux x64"
    ;;
  *)
    echo "error: sistema no soportado por build-app.sh: $PLATFORM/$ARCH." >&2
    echo "       Para Windows usa scripts/build-app.ps1 en una máquina Windows x64." >&2
    exit 1
    ;;
esac

if ! command -v cargo >/dev/null 2>&1; then
  echo "error: falta 'cargo' en el PATH (instalá Rust: https://rustup.rs)." >&2
  exit 1
fi
if ! cargo tauri --version >/dev/null 2>&1; then
  echo "error: falta cargo-tauri (instalalo con: cargo install tauri-cli --version '$TAURI_CLI_VERSION' --locked)." >&2
  exit 1
fi
ACTUAL_TAURI_VERSION="$(cargo tauri --version)"
if [[ "$ACTUAL_TAURI_VERSION" != "tauri-cli $TAURI_CLI_VERSION" ]]; then
  echo "error: versión de cargo-tauri no reproducible: $ACTUAL_TAURI_VERSION." >&2
  echo "       Instala la fijada: cargo install tauri-cli --version '$TAURI_CLI_VERSION' --locked --force" >&2
  exit 1
fi

if [[ "$PLATFORM" == "Linux" ]]; then
  # Los paquetes Linux se construyen nativamente: AppImage, Debian y RPM.
  # Fallar acá deja una instrucción concreta en vez de esperar varios minutos
  # para que Rust/Tauri termine con un error críptico de linker o bundler.
  for bin in pkg-config patchelf dpkg-deb rpmbuild; do
    if ! command -v "$bin" >/dev/null 2>&1; then
      echo "error: falta '$bin' para crear los bundles Linux." >&2
      echo "       Instala los requisitos listados en docs/desktop.md §3." >&2
      exit 1
    fi
  done
  # libxdo-dev sigue siendo requisito de compilación de Tauri, pero Ubuntu
  # 22.04 no instala un archivo xdo.pc: no puede validarse con pkg-config.
  for module in webkit2gtk-4.1 alsa ayatana-appindicator3-0.1 librsvg-2.0; do
    if ! pkg-config --exists "$module"; then
      echo "error: falta la dependencia Linux '$module' (pkg-config)." >&2
      echo "       Instala los requisitos listados en docs/desktop.md §3." >&2
      exit 1
    fi
  done
  if [[ "${EDECAN_BUNDLE_OLLAMA:-0}" == "1" ]]; then
    echo "error: Ollama embebido todavía no se incluye en el bundle Linux." >&2
    echo "       El instalador sí detecta Ollama, Codex CLI y Claude CLI ya instalados en el equipo." >&2
    exit 1
  fi
fi

echo "==> Plataforma: $PLATFORM_LABEL"
echo "==> [1/2] Empaquetando el backend (scripts/build-backend.sh)…"
"$SCRIPT_DIR/build-backend.sh"

echo "==> [2/2] cargo tauri build (instalador nativo de esta plataforma)…"
cd "$DESKTOP_DIR/src-tauri"
# `tauri.conf.json` -> `bundle.externalBin` SOLO lista `binaries/edecan-local`
# por defecto. Esta build siempre suma `binaries/fydesign-node` y el recurso
# `../packaging/studio-engine`, ambos creados por build-studio-engine.sh.
# Ollama es opcional (ver `download-ollama.sh`/
# `EDECAN_BUNDLE_OLLAMA` arriba y docs/desktop.md) — Tauri exige que TODOS
# los binarios listados en `externalBin` existan para el target triple de
# esta build, así que si acá se pidió el binario de Ollama (mismo
# EDECAN_BUNDLE_OLLAMA=1 que ya usó build-backend.sh un poco más arriba para
# descargarlo), hay que sumarlo a `externalBin` para ESTA build en
# particular — nunca al archivo base, para que la build sin Ollama (la que
# corre por defecto) no lo exija. El override se pasa explícitamente al CLI
# con `cargo tauri build --config <json>`.
TAURI_EXTERNAL_BIN_JSON='["binaries/edecan-local","binaries/fydesign-node"]'
if [[ "${EDECAN_BUNDLE_OLLAMA:-0}" == "1" ]]; then
  echo "    (EDECAN_BUNDLE_OLLAMA=1: sumando binaries/ollama a externalBin para esta build)"
  TAURI_EXTERNAL_BIN_JSON='["binaries/edecan-local","binaries/fydesign-node","binaries/ollama"]'
fi

# Tauri consume el contenido de la clave en TAURI_SIGNING_PRIVATE_KEY. Para no
# obligar al mantenedor a copiar ese secreto al entorno o al historial del
# shell, Edecán acepta además una ruta local protegida y carga su contenido
# únicamente en el entorno de este proceso.
if [[ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" && -n "${TAURI_SIGNING_PRIVATE_KEY_PATH:-}" ]]; then
  if [[ ! -f "$TAURI_SIGNING_PRIVATE_KEY_PATH" || ! -r "$TAURI_SIGNING_PRIVATE_KEY_PATH" ]]; then
    echo "error: TAURI_SIGNING_PRIVATE_KEY_PATH no apunta a un archivo legible." >&2
    exit 1
  fi
  export TAURI_SIGNING_PRIVATE_KEY
  TAURI_SIGNING_PRIVATE_KEY="$(<"$TAURI_SIGNING_PRIVATE_KEY_PATH")"
  if [[ -z "$TAURI_SIGNING_PRIVATE_KEY" ]]; then
    echo "error: el archivo de firma del updater está vacío." >&2
    exit 1
  fi
fi

# Un build normal/local sigue produciendo instaladores sin exigir la clave
# privada de releases. Cuando CI (o el mantenedor) aporta una clave Tauri,
# se generan además los paquetes del updater y sus firmas minisign.
TAURI_CREATE_UPDATER_ARTIFACTS=false
if [[ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]]; then
  TAURI_CREATE_UPDATER_ARTIFACTS=true
  echo "    (firma de updater detectada: generando artefactos de actualización)"
fi

# Hardened Runtime exige que todas las librerías cargadas compartan el Team ID
# del proceso. El sidecar PyInstaller onefile autoextrae libpython y extensiones
# firmadas ad-hoc (sin Team ID), por lo que activar runtime con identidad `-`
# produce una app que pasa `codesign --verify` pero muere al arrancar. Una firma
# Developer ID real conserva el default endurecido de Tauri y puede notarizarse;
# solo el build local ad-hoc recibe este override explícito y verificable.
if [[ "$PLATFORM" == "Darwin" && "${APPLE_SIGNING_IDENTITY:-}" == "-" ]]; then
  echo "    (firma ad-hoc: Hardened Runtime desactivado para el sidecar PyInstaller local)"
  TAURI_BUNDLE_CONFIG="{\"bundle\":{\"externalBin\":$TAURI_EXTERNAL_BIN_JSON,\"resources\":{\"../packaging/studio-engine\":\"studio-engine\"},\"createUpdaterArtifacts\":$TAURI_CREATE_UPDATER_ARTIFACTS,\"macOS\":{\"hardenedRuntime\":false}}}"
else
  TAURI_BUNDLE_CONFIG="{\"bundle\":{\"externalBin\":$TAURI_EXTERNAL_BIN_JSON,\"resources\":{\"../packaging/studio-engine\":\"studio-engine\"},\"createUpdaterArtifacts\":$TAURI_CREATE_UPDATER_ARTIFACTS}}"
fi
cargo tauri build --config "$TAURI_BUNDLE_CONFIG" -- --locked

echo "==> Listo. Instaladores de $PLATFORM_LABEL en src-tauri/target/release/bundle/."
