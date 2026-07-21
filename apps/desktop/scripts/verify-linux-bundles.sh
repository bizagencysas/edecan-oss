#!/usr/bin/env bash
# Verificación de release Linux x64. Inspecciona los tres paquetes y arranca
# el AppImage en un X virtual, esperando al backend real y cerrando la ventana
# por el protocolo normal del escritorio para comprobar el apagado del sidecar.
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "error: verify-linux-bundles.sh debe ejecutarse en Linux." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUNDLE_DIR="${1:-$DESKTOP_DIR/src-tauri/target/release/bundle}"

for bin in awk curl dbus-run-session dpkg-deb openbox pgrep rpm sed wmctrl xvfb-run; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "error: falta '$bin' para verificar los bundles Linux." >&2
    exit 1
  fi
done

find_one() {
  local directory="$1"
  local pattern="$2"
  local label="$3"
  local matches=()
  while IFS= read -r -d '' candidate; do
    matches+=("$candidate")
  done < <(find "$directory" -maxdepth 1 -type f -name "$pattern" -print0 2>/dev/null)
  if (( ${#matches[@]} != 1 )); then
    echo "error: se esperaba exactamente un $label en $directory; encontrados: ${#matches[@]}." >&2
    return 1
  fi
  printf '%s\n' "${matches[0]}"
}

APPIMAGE="$(find_one "$BUNDLE_DIR/appimage" '*.AppImage' 'AppImage')"
DEB="$(find_one "$BUNDLE_DIR/deb" '*.deb' 'paquete Debian')"
RPM="$(find_one "$BUNDLE_DIR/rpm" '*.rpm' 'paquete RPM')"

echo "==> Inspeccionando metadatos y sidecars…"
dpkg-deb --info "$DEB" >/dev/null
dpkg-deb --contents "$DEB" | grep '/edecan-desktop$' >/dev/null
dpkg-deb --contents "$DEB" | grep '/edecan-local$' >/dev/null
rpm -qip "$RPM" >/dev/null
rpm -qlp "$RPM" | grep '/edecan-desktop$' >/dev/null
rpm -qlp "$RPM" | grep '/edecan-local$' >/dev/null

SMOKE_DIR="$(mktemp -d)"
APP_LOG="$SMOKE_DIR/edecan-linux.log"
DISPLAY_FILE="$SMOKE_DIR/display"
XAUTHORITY_FILE="$SMOKE_DIR/xauthority"
LAUNCHER_PID=""

cleanup() {
  if [[ -n "$LAUNCHER_PID" ]] && kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    kill -TERM "$LAUNCHER_PID" 2>/dev/null || true
    wait "$LAUNCHER_PID" 2>/dev/null || true
  fi
  # Solo alcanza procesos de esta corrida: tanto el sidecar como su Postgres
  # llevan el data-dir efímero y único creado arriba en su línea de comando.
  while IFS= read -r pid; do
    [[ -n "$pid" ]] && kill -TERM "$pid" 2>/dev/null || true
  done < <(pgrep -f "(edecan-local|postgres).*$SMOKE_DIR" 2>/dev/null || true)
  for _cleanup_attempt in $(seq 1 50); do
    if ! pgrep -f "(edecan-local|postgres).*$SMOKE_DIR" >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done
  while IFS= read -r pid; do
    [[ -n "$pid" ]] && kill -KILL "$pid" 2>/dev/null || true
  done < <(pgrep -f "(edecan-local|postgres).*$SMOKE_DIR" 2>/dev/null || true)
  find "$SMOKE_DIR" -depth -delete 2>/dev/null || true
}
trap cleanup EXIT INT TERM

export XDG_CONFIG_HOME="$SMOKE_DIR/config"
export XDG_DATA_HOME="$SMOKE_DIR/data"
export XDG_CACHE_HOME="$SMOKE_DIR/cache"
# `pgserver` usa `platformdirs.user_runtime_path()` para su lock entre
# procesos. Los escritorios Linux normales ya traen XDG_RUNTIME_DIR, pero
# un runner CI sin sesión systemd puede no tener `/run/user/<uid>`. Darle un
# runtime privado replica el contrato XDG real y evita confundir esa carencia
# del runner con un fallo del AppImage.
export XDG_RUNTIME_DIR="$SMOKE_DIR/runtime"
# Solo durante este smoke, el escritorio imprime las últimas líneas del
# sidecar si el backend falla. En instalaciones normales esta variable no
# existe, por lo que los logs potencialmente sensibles siguen sin salir a
# stderr.
export EDECAN_DESKTOP_DIAGNOSTICS=1
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR"
chmod u+x "$APPIMAGE"

echo "==> Arrancando el AppImage y su backend real en Xvfb…"
# `xvfb-run` elige una pantalla libre y solo exporta DISPLAY/XAUTHORITY a su
# proceso hijo. El wrapper escribe ambos valores: compartir solo DISPLAY no
# basta, porque Xvfb rechaza los clientes externos que no presentan la cookie
# del archivo de autoridad efímero creado por xvfb-run.
APPIMAGE_EXTRACT_AND_RUN=1 xvfb-run -a dbus-run-session sh -c '
  printf "%s\n" "$DISPLAY" > "$1"
  printf "%s\n" "$XAUTHORITY" > "$2"

  # Xvfb por sí solo no es un escritorio: no implementa WM_DELETE_WINDOW ni
  # el protocolo EWMH. Openbox hace que el smoke replique el botón X de una
  # sesión Linux real. También se ejecuta dentro de un bus D-Bus privado para
  # que AppIndicator pruebe su ruta normal en vez de degradarse por una
  # carencia artificial del runner.
  openbox --sm-disable &
  for _wm_attempt in $(seq 1 50); do
    wmctrl -m >/dev/null 2>&1 && break
    sleep 0.1
  done
  if ! wmctrl -m >/dev/null 2>&1; then
    echo "error: Openbox no quedó listo en la pantalla virtual." >&2
    exit 1
  fi
  exec "$3"
' _ "$DISPLAY_FILE" "$XAUTHORITY_FILE" "$APPIMAGE" >"$APP_LOG" 2>&1 &
LAUNCHER_PID="$!"

for _attempt in $(seq 1 20); do
  [[ -s "$DISPLAY_FILE" && -s "$XAUTHORITY_FILE" ]] && break
  if ! kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    echo "error: no se pudo crear la pantalla virtual para Edecán." >&2
    sed -n '1,240p' "$APP_LOG" >&2
    exit 1
  fi
  sleep 1
done
if [[ ! -s "$DISPLAY_FILE" || ! -s "$XAUTHORITY_FILE" ]]; then
  echo "error: Xvfb no informó DISPLAY/XAUTHORITY dentro de 20 segundos." >&2
  sed -n '1,240p' "$APP_LOG" >&2
  exit 1
fi
export DISPLAY
DISPLAY="$(tr -d '\r\n' < "$DISPLAY_FILE")"
export XAUTHORITY
XAUTHORITY="$(tr -d '\r\n' < "$XAUTHORITY_FILE")"

# La splash debe existir desde el inicio y es una ventana distinta de la
# principal. `wmctrl -l` devuelve únicamente ventanas de nivel superior que
# administra Openbox; a diferencia de `xdotool search`, nunca confunde el
# webview GTK hijo con la ventana que recibe WM_DELETE_WINDOW. Guardar su ID
# permite comprobar más abajo que Edecán realmente completó la transición de
# arranque, no solo que quedó mostrando "cargando".
SPLASH_WINDOW_ID=""
for _attempt in $(seq 1 20); do
  # `wmctrl -l` devuelve 1 (no 0) cuando el WM ya está listo pero todavía no
  # hay ninguna ventana. Eso es un estado esperado durante el primer segundo,
  # no un error que deba activar `set -e` y volver flakey este bucle.
  SPLASH_WINDOW_ID="$(
    { wmctrl -l 2>/dev/null || true; } |
      awk 'tolower($0) ~ /edec/ { print $1; exit }'
  )"
  [[ -n "$SPLASH_WINDOW_ID" ]] && break
  sleep 1
done
if [[ -z "$SPLASH_WINDOW_ID" ]]; then
  echo "error: Edecán no creó su ventana splash dentro de 20 segundos." >&2
  wmctrl -lx >&2 || true
  sed -n '1,240p' "$APP_LOG" >&2
  exit 1
fi

PORT=""
for _attempt in $(seq 1 120); do
  if ! kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    echo "error: el AppImage terminó antes de dejar listo el backend." >&2
    sed -n '1,240p' "$APP_LOG" >&2
    exit 1
  fi

  BACKEND_LINE="$(
    pgrep -af 'edecan-local.*--port' 2>/dev/null |
      grep -F -- "$XDG_DATA_HOME/cc.edecan.desktop/data" |
      head -1 || true
  )"
  PORT="$(printf '%s' "$BACKEND_LINE" | sed -nE 's/.*--port ([0-9]+).*/\1/p')"
  if [[ -n "$PORT" ]] && curl --fail --silent "http://127.0.0.1:$PORT/healthz" >/dev/null; then
    break
  fi
  sleep 1
done

if [[ -z "$PORT" ]] || ! curl --fail --silent "http://127.0.0.1:$PORT/healthz" >/dev/null; then
  echo "error: el backend empaquetado no respondió /healthz dentro de 120 segundos." >&2
  sed -n '1,240p' "$APP_LOG" >&2
  exit 1
fi

WINDOW_ID=""
for _attempt in $(seq 1 60); do
  while IFS= read -r candidate; do
    if [[ -n "$candidate" && "$candidate" != "$SPLASH_WINDOW_ID" ]]; then
      WINDOW_ID="$candidate"
      break
    fi
  done < <(
    { wmctrl -l 2>/dev/null || true; } |
      awk 'tolower($0) ~ /edec/ { print $1 }'
  )
  [[ -n "$WINDOW_ID" ]] && break
  sleep 1
done
if [[ -z "$WINDOW_ID" ]]; then
  echo "error: el backend quedó sano, pero Edecán no reemplazó la splash por su ventana principal." >&2
  wmctrl -lx >&2 || true
  sed -n '1,240p' "$APP_LOG" >&2
  exit 1
fi

echo "==> Cerrando la ventana y verificando apagado limpio…"
# `xdotool windowclose` sobre el webview hijo destruiría el recurso X11 por
# debajo de GTK y puede provocar BadWindow sin emitir CloseRequested. Este ID
# viene de la lista top-level de Openbox y `wmctrl -ic` le envía
# _NET_CLOSE_WINDOW, equivalente al botón X del usuario.
wmctrl -ic "$WINDOW_ID"
for _attempt in $(seq 1 30); do
  if ! kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done
if kill -0 "$LAUNCHER_PID" 2>/dev/null; then
  echo "error: Edecán no terminó dentro de 30 segundos después de cerrar su ventana." >&2
  wmctrl -lx >&2 || true
  sed -n '1,240p' "$APP_LOG" >&2
  pgrep -af "(edecan-desktop|edecan-local|postgres).*$SMOKE_DIR" >&2 || true
  exit 1
fi
set +e
wait "$LAUNCHER_PID"
LAUNCHER_STATUS="$?"
set -e
LAUNCHER_PID=""
if (( LAUNCHER_STATUS != 0 )); then
  echo "error: el AppImage terminó con código $LAUNCHER_STATUS después del cierre." >&2
  sed -n '1,240p' "$APP_LOG" >&2
  pgrep -af "(edecan-local|postgres).*$SMOKE_DIR" >&2 || true
  exit 1
fi

for _attempt in $(seq 1 10); do
  if ! pgrep -f "(edecan-local|postgres).*$SMOKE_DIR" >/dev/null 2>&1; then
    echo "==> Linux verificado: AppImage + deb + rpm, health real y cero procesos huérfanos."
    exit 0
  fi
  sleep 1
done

echo "error: quedó un sidecar/Postgres huérfano después de cerrar la app." >&2
pgrep -af "(edecan-local|postgres).*$SMOKE_DIR" >&2 || true
exit 1
