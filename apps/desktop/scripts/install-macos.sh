#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILT_APP="$DESKTOP_DIR/src-tauri/target/release/bundle/macos/Edecán.app"

# Una sola ruta canónica evita que LaunchServices y TCC presenten varias
# identidades visualmente iguales. /Applications es escribible para las cuentas
# administradoras normales de macOS; el fallback mantiene soportados los equipos
# administrados donde esa carpeta sea de solo lectura.
if [[ -n "${EDECAN_INSTALL_PATH:-}" ]]; then
  TARGET_APP="$EDECAN_INSTALL_PATH"
elif [[ -w /Applications ]]; then
  TARGET_APP="/Applications/Edecán.app"
else
  TARGET_APP="$HOME/Applications/Edecán.app"
fi

STAGING_DIR=""

cleanup_staging() {
  if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
    rm -rf "$STAGING_DIR"
  fi
}

trap cleanup_staging EXIT

codesign_authority() {
  local app_path="$1"
  codesign -dv --verbose=4 "$app_path" 2>&1 \
    | sed -nE 's/^Authority=(Apple Development: .+|Developer ID Application: .+)$/\1/p' \
    | head -n 1
}

available_signing_identity() {
  local requested="${EDECAN_MACOS_CODESIGN_IDENTITY:-}"
  local existing=""

  if [[ -n "$requested" ]]; then
    printf '%s\n' "$requested"
    return 0
  fi
  if [[ -d "$TARGET_APP" ]]; then
    existing="$(codesign_authority "$TARGET_APP" || true)"
    if [[ -n "$existing" ]] \
      && security find-identity -v -p codesigning 2>/dev/null | grep -Fq "\"$existing\""; then
      printf '%s\n' "$existing"
    fi
  fi
}

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

migrate_macos_autostart() {
  local launch_agent="$HOME/Library/LaunchAgents/Edecán.plist"
  local executable="$TARGET_APP/Contents/MacOS/edecan-desktop"
  local gui_domain="gui/$(id -u)"

  [[ -f "$launch_agent" ]] || return 0
  /usr/libexec/PlistBuddy -c "Set :ProgramArguments:0 $executable" "$launch_agent"
  launchctl bootout "$gui_domain" "$launch_agent" >/dev/null 2>&1 || true
  launchctl bootstrap "$gui_domain" "$launch_agent"
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Este instalador es para macOS." >&2
  exit 1
fi

if [[ ! -d "$BUILT_APP" ]]; then
  "$SCRIPT_DIR/build-app.sh"
fi

mkdir -p "$(dirname "$TARGET_APP")"
STAGING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/edecan-install.XXXXXX")"
STAGED_APP="$STAGING_DIR/Edecán.app"
ditto "$BUILT_APP" "$STAGED_APP"

# Una firma ad-hoc genera un requisito designado basado en el hash y macOS la
# considera una app distinta en cada actualización. Si el empaquetador aporta
# una identidad estable, o la instalación anterior ya tenía una disponible en
# el Keychain, se reutiliza para que Micrófono, Accesibilidad y Grabación de
# pantalla sigan perteneciendo al mismo Edecán entre versiones.
SIGNING_IDENTITY="$(available_signing_identity || true)"
if [[ -n "$SIGNING_IDENTITY" ]]; then
  codesign --force --deep --sign "$SIGNING_IDENTITY" --timestamp=none "$STAGED_APP"
fi
codesign --verify --deep --strict "$STAGED_APP"

if [[ -d "$TARGET_APP" ]]; then
  stop_running_edecan
  mkdir -p "$HOME/.Trash"
  BACKUP_ARCHIVE="$HOME/.Trash/Edecán anterior $(date +%Y%m%d-%H%M%S).zip"
  ditto -c -k --sequesterRsrc --keepParent "$TARGET_APP" "$BACKUP_ARCHIVE"
  rm -rf "$TARGET_APP"
  echo "Versión anterior guardada como archivo recuperable en $BACKUP_ARCHIVE"
fi
ditto "$STAGED_APP" "$TARGET_APP"
migrate_macos_autostart
open "$TARGET_APP"

echo "Edecán instalado en $TARGET_APP"
