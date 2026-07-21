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
# Este script NO firma código ni notariza en macOS — eso es responsabilidad
# de quien empaqueta, con SU PROPIO Developer ID (bring-your-own, ver
# docs/desktop.md "Firma de código"). Sin firmar, el .dmg/.app resultante
# dispara Gatekeeper en Macs distintas a la que lo compiló (clic derecho →
# Abrir la primera vez) — también documentado ahí.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TAURI_CLI_VERSION="2.11.4"
PLATFORM="$(uname -s)"
ARCH="$(uname -m)"

case "$PLATFORM" in
  Darwin) PLATFORM_LABEL="macOS" ;;
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
  for module in webkit2gtk-4.1 alsa ayatana-appindicator3-0.1 librsvg-2.0 xdo; do
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
# por defecto (Ollama es opcional, ver `download-ollama.sh`/
# `EDECAN_BUNDLE_OLLAMA` arriba y docs/desktop.md) — Tauri exige que TODOS
# los binarios listados en `externalBin` existan para el target triple de
# esta build, así que si acá se pidió el binario de Ollama (mismo
# EDECAN_BUNDLE_OLLAMA=1 que ya usó build-backend.sh un poco más arriba para
# descargarlo), hay que sumarlo a `externalBin` para ESTA build en
# particular — nunca al archivo base, para que la build sin Ollama (la que
# corre por defecto) no lo exija. El override se pasa explícitamente al CLI
# con `cargo tauri build --config <json>`.
TAURI_BUILD_ARGS=()
if [[ "${EDECAN_BUNDLE_OLLAMA:-0}" == "1" ]]; then
  echo "    (EDECAN_BUNDLE_OLLAMA=1: sumando binaries/ollama a externalBin para esta build)"
  TAURI_BUILD_ARGS+=(
    --config
    '{"bundle":{"externalBin":["binaries/edecan-local","binaries/ollama"]}}'
  )
fi
if (( ${#TAURI_BUILD_ARGS[@]} )); then
  cargo tauri build "${TAURI_BUILD_ARGS[@]}" -- --locked
else
  # Bash 3.2 (incluido por macOS) trata la expansión de un array vacío como
  # variable no definida cuando `set -u` está activo. Ejecutar la variante sin
  # argumentos evita que el instalador de doble clic falle al final del build.
  cargo tauri build -- --locked
fi

echo "==> Listo. Instaladores de $PLATFORM_LABEL en src-tauri/target/release/bundle/."
