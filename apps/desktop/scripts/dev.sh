#!/usr/bin/env bash
# apps/desktop/scripts/dev.sh
#
# Modo desarrollo del shell nativo: NO corre PyInstaller. Prepara un export
# estático de apps/web y `cargo tauri dev` arranca la app con recarga en
# caliente del lado Rust; el backend local corre
# directo desde el código fuente (`uv run --all-packages python -m edecan_local`)
# vía la variable de entorno EDECAN_LOCAL_DEV_CMD que lee
# src-tauri/src/backend.rs::build_command cuando no encuentra el sidecar
# empaquetado en src-tauri/binaries/ — que en este modo, a propósito, nunca
# se generó.
#
# En un clon limpio genera `apps/web/out` y lo sirve desde el mismo backend
# local, así la ventana Tauri abre una UI utilizable en vez de un 404. Reusa
# ese export en corridas posteriores; usa EDECAN_REBUILD_WEB=1 cuando cambie
# el frontend. Para iterar solo en Rust/backend sin preparar UI, fija
# EDECAN_SKIP_DEV_WEB=1. El flujo de release (backend congelado + instalador)
# sigue viviendo en scripts/build-app.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_DIR/../.." && pwd)"
WEB_DIR="$REPO_ROOT/apps/web"
TAURI_CLI_VERSION="2.11.4"

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
if ! command -v uv >/dev/null 2>&1; then
  echo "error: falta 'uv' en el PATH (lo necesita EDECAN_LOCAL_DEV_CMD para correr edecan_local desde fuente)." >&2
  exit 1
fi

if [[ "${EDECAN_SKIP_DEV_WEB:-0}" != "1" ]]; then
  for bin in node npm; do
    if ! command -v "$bin" >/dev/null 2>&1; then
      echo "error: falta '$bin' en el PATH para preparar la UI local (o usa EDECAN_SKIP_DEV_WEB=1 para iterar solo en Rust/backend)." >&2
      exit 1
    fi
  done


  NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
  NPM_MAJOR="$(npm --version | awk -F. '{print $1}')"
  if [[ "$NODE_MAJOR" != "22" || "$NPM_MAJOR" != "10" ]]; then
    echo "error: apps/web requiere Node 22 y npm 10 (detectados: Node $(node --version), npm $(npm --version))." >&2
    exit 1
  fi

  if [[ "${EDECAN_REBUILD_WEB:-0}" == "1" || ! -f "$WEB_DIR/out/index.html" ]]; then
    echo "==> Preparando UI estática local (apps/web/out)…"
    (
      cd "$WEB_DIR"
      if [[ ! -d node_modules ]]; then
        if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
      fi
      NEXT_OUTPUT=export NEXT_PUBLIC_API_URL='' npm run build
    )
  else
    echo "==> Reusando apps/web/out (EDECAN_REBUILD_WEB=1 fuerza una reconstrucción)."
  fi

  if [[ ! -f "$WEB_DIR/out/index.html" ]]; then
    echo "error: el build no generó $WEB_DIR/out/index.html." >&2
    exit 1
  fi
  export EDECAN_WEB_DIR="$WEB_DIR/out"
else
  echo "==> EDECAN_SKIP_DEV_WEB=1: el backend arrancará sin UI estática."
fi

# Mismo default que YA tiene src-tauri/src/backend.rs cuando esta variable
# no está seteada — se fija igual acá, explícito, para que quede a la vista
# y sea fácil de pisar. Ejemplo si alguna vez hace falta acotar el entorno
# de uv a un solo paquete en vez del workspace completo:
#   EDECAN_LOCAL_DEV_CMD='uv run --package edecan-local python -m edecan_local' scripts/dev.sh
# `--all-packages` explícito: un `uv run` suelto sin este flag poda en silencio el resto del
# workspace uv (ver README.md "Modo desarrollador" / HOTFIXES_PENDIENTES.md).
export EDECAN_LOCAL_DEV_CMD="${EDECAN_LOCAL_DEV_CMD:-uv run --all-packages python -m edecan_local}"

echo "==> EDECAN_LOCAL_DEV_CMD=$EDECAN_LOCAL_DEV_CMD"
if [[ -n "${EDECAN_WEB_DIR:-}" ]]; then
  echo "==> EDECAN_WEB_DIR=$EDECAN_WEB_DIR"
fi

cd "$DESKTOP_DIR/src-tauri"
exec cargo tauri dev
