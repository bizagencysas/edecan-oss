#!/usr/bin/env bash
# apps/desktop/scripts/dev.sh
#
# Modo desarrollo del shell nativo: NO corre scripts/build-backend.sh (nada
# de PyInstaller, nada de exportar apps/web) — `cargo tauri dev` arranca la
# app con recarga en caliente del lado Rust, y el backend local corre
# directo desde el código fuente (`uv run python -m edecan_local`, WP-V3-05)
# vía la variable de entorno EDECAN_LOCAL_DEV_CMD que lee
# src-tauri/src/backend.rs::build_command cuando no encuentra el sidecar
# empaquetado en src-tauri/binaries/ — que en este modo, a propósito, nunca
# se generó.
#
# apps/web NO se sirve acá tampoco: para ver la UI real durante desarrollo
# corré aparte `make web` (equivalente a `cd apps/web && npm run dev`,
# puerto 3000 por defecto) y abrí eso en tu navegador normal — este script
# solo levanta el shell nativo (splash + backend local desde fuente), útil
# para iterar en Rust o en el backend Python sin reconstruir el frontend en
# cada cambio. Para probar el flujo COMPLETO tal como lo ve el cliente final
# (shell + web empaquetada + backend congelado) usá scripts/build-app.sh.
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
if ! command -v uv >/dev/null 2>&1; then
  echo "error: falta 'uv' en el PATH (lo necesita EDECAN_LOCAL_DEV_CMD para correr edecan_local desde fuente)." >&2
  exit 1
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
echo "==> Tip: para ver la UI real (no solo la ventana de splash), corré 'make web' aparte."

cd "$DESKTOP_DIR/src-tauri"
exec cargo tauri dev
