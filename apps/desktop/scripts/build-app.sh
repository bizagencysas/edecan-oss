#!/usr/bin/env bash
# apps/desktop/scripts/build-app.sh
#
# Build de producción completo para ESTA plataforma (macOS o Linux — para
# Windows: build-backend.ps1 y después `cargo tauri build` a mano, ver
# docs/desktop.md): backend empaquetado (scripts/build-backend.sh) seguido
# del instalador nativo (`cargo tauri build` — dmg+app en macOS). Es lo que
# corre quien arma un release real; NO lo corre este work package (ver
# docs/desktop.md, "verificación" — construir el instalador de verdad puede
# tardar varios minutos y requiere las toolchains completas instaladas).
#
# Este script NO firma código ni notariza en macOS — eso es responsabilidad
# de quien empaqueta, con SU PROPIO Developer ID (bring-your-own, ver
# docs/desktop.md "Firma de código"). Sin firmar, el .dmg/.app resultante
# dispara Gatekeeper en Macs distintas a la que lo compiló (clic derecho →
# Abrir la primera vez) — también documentado ahí.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! command -v cargo >/dev/null 2>&1; then
  echo "error: falta 'cargo' en el PATH (instalá Rust: https://rustup.rs)." >&2
  exit 1
fi
if ! cargo tauri --version >/dev/null 2>&1; then
  echo "error: falta el subcomando 'cargo tauri' (instalalo con: cargo install tauri-cli --version '^2.0', ver docs/desktop.md)." >&2
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
# corre por defecto) no lo exija. `tauri-build` (build.rs de este mismo
# crate) lee la variable de entorno `TAURI_CONFIG` como un JSON que se
# mergea sobre `tauri.conf.json` (confirmado empíricamente: `cargo build`
# imprime `cargo:rerun-if-env-changed=TAURI_CONFIG` en su salida).
if [[ "${EDECAN_BUNDLE_OLLAMA:-0}" == "1" ]]; then
  echo "    (EDECAN_BUNDLE_OLLAMA=1: sumando binaries/ollama a externalBin para esta build)"
  export TAURI_CONFIG='{"bundle":{"externalBin":["binaries/edecan-local","binaries/ollama"]}}'
fi
cargo tauri build

echo "==> Listo. Instaladores en src-tauri/target/release/bundle/ (ver esa carpeta: dmg/, macos/, etc.)."
