#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILT_APP="$DESKTOP_DIR/src-tauri/target/release/bundle/macos/Edecán.app"
TARGET_APP="$HOME/Applications/Edecán.app"

collect_process_tree() {
  local parent_pid="$1"
  local child_pid
  for child_pid in $(pgrep -P "$parent_pid" 2>/dev/null || true); do
    collect_process_tree "$child_pid"
  done
  printf '%s\n' "$parent_pid"
}

stop_running_edecan() {
  local desktop_pids
  local process_tree=""
  local pid
  desktop_pids="$(pgrep -x edecan-desktop 2>/dev/null || true)"
  [[ -n "$desktop_pids" ]] || return 0

  for pid in $desktop_pids; do
    process_tree+="$(collect_process_tree "$pid")"$'\n'
  done

  # Primero deja que Tauri cierre el backend, Postgres y sus handles de forma
  # limpia. La ruta por bundle id funciona aunque macOS normalice el acento de
  # Edecán de forma distinta en el path del proceso.
  osascript -e 'tell application id "cc.edecan.desktop" to quit' >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6 7 8; do
    pgrep -x edecan-desktop >/dev/null 2>&1 || return 0
    sleep 1
  done

  # Si una WebView bloqueó el cierre, termina únicamente el árbol que ya
  # pertenecía a Edecán antes de empezar la instalación. Nunca usa killall ni
  # toca procesos de Jarvis, Python u otras aplicaciones.
  for pid in $process_tree; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  for _ in 1 2 3 4 5; do
    pgrep -x edecan-desktop >/dev/null 2>&1 || return 0
    sleep 1
  done

  echo "No se pudo cerrar la versión anterior de Edecán; no se reemplazó la app." >&2
  exit 1
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Este instalador es para macOS." >&2
  exit 1
fi

if [[ ! -d "$BUILT_APP" ]]; then
  "$SCRIPT_DIR/build-app.sh"
fi

mkdir -p "$HOME/Applications"
if [[ -d "$TARGET_APP" ]]; then
  stop_running_edecan
  mkdir -p "$HOME/.Trash"
  BACKUP_APP="$HOME/.Trash/Edecán anterior $(date +%Y%m%d-%H%M%S).app"
  mv "$TARGET_APP" "$BACKUP_APP"
  echo "Versión anterior movida a $BACKUP_APP"
fi
ditto "$BUILT_APP" "$TARGET_APP"
open "$TARGET_APP"

echo "Edecán instalado en $TARGET_APP"
