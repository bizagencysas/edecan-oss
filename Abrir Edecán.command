#!/usr/bin/env bash
# Doble clic y listo: abre la aplicación nativa. En un clon de desarrollo sin
# instalar, construye una sola vez y la deja en ~/Applications/Edecán.app.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_APP="$HOME/Applications/Edecán.app"
SYSTEM_APP="/Applications/Edecán.app"
BUILT_APP="$SCRIPT_DIR/apps/desktop/src-tauri/target/release/bundle/macos/Edecán.app"
BUILD_LOG="$HOME/Library/Logs/Edecán/instalacion.log"

open_edecan() {
  local app_path="$1"
  open "$app_path"
}

if [[ -d "$USER_APP" ]]; then
  open_edecan "$USER_APP"
  exit 0
fi

if [[ -d "$SYSTEM_APP" ]]; then
  open_edecan "$SYSTEM_APP"
  exit 0
fi

mkdir -p "$HOME/Applications" "$(dirname "$BUILD_LOG")"

if [[ ! -d "$BUILT_APP" ]]; then
  osascript -e 'display notification "Preparando la app por primera vez. Te avisaré cuando esté lista." with title "Edecán"'
  if ! "$SCRIPT_DIR/apps/desktop/scripts/build-app.sh" >"$BUILD_LOG" 2>&1; then
    osascript -e 'display dialog "No pude preparar Edecán. El detalle quedó en ~/Library/Logs/Edecán/instalacion.log" with title "Edecán" buttons {"Cerrar"} default button 1 with icon stop'
    open -R "$BUILD_LOG"
    exit 1
  fi
fi

ditto "$BUILT_APP" "$USER_APP"
osascript -e 'display notification "Edecán ya está instalado y listo." with title "Edecán"'
open_edecan "$USER_APP"
