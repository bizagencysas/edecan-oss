#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILT_APP="$DESKTOP_DIR/src-tauri/target/release/bundle/macos/Edecán.app"
TARGET_APP="$HOME/Applications/Edecán.app"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Este instalador es para macOS." >&2
  exit 1
fi

if [[ ! -d "$BUILT_APP" ]]; then
  "$SCRIPT_DIR/build-app.sh"
fi

mkdir -p "$HOME/Applications"
if [[ -d "$TARGET_APP" ]]; then
  mkdir -p "$HOME/.Trash"
  BACKUP_APP="$HOME/.Trash/Edecán anterior $(date +%Y%m%d-%H%M%S).app"
  mv "$TARGET_APP" "$BACKUP_APP"
  echo "Versión anterior movida a $BACKUP_APP"
fi
ditto "$BUILT_APP" "$TARGET_APP"
open "$TARGET_APP"

echo "Edecán instalado en $TARGET_APP"
