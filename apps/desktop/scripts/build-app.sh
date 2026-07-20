#!/usr/bin/env bash
# apps/desktop/scripts/build-app.sh
#
# Build de producción completo para macOS. Windows tiene el flujo equivalente
# en `build-app.ps1`. Linux desktop todavía no es una superficie soportada:
# `tauri.conf.json` publica únicamente bundles macOS y Windows.
# Backend empaquetado (scripts/build-backend.sh) seguido del instalador nativo
# (`cargo tauri build` — dmg+app en macOS): punto de entrada reproducible para
# armar un release real.
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

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: build-app.sh genera bundles de macOS y debe correr en macOS." >&2
  echo "       Para Windows usa scripts/build-app.ps1 en una máquina Windows x64." >&2
  exit 1
fi

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
cargo tauri build "${TAURI_BUILD_ARGS[@]}" -- --locked

echo "==> Listo. Instaladores en src-tauri/target/release/bundle/ (ver esa carpeta: dmg/, macos/, etc.)."
