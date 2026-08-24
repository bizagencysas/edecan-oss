#!/usr/bin/env bash
# apps/desktop/scripts/build-backend.sh (macOS/Linux)
#
# Arma el "backend local" que el sidecar de Tauri va a lanzar (contrato en
# ARCHITECTURE.md §12.f, docs/desktop.md): construye la web estática de
# apps/web, congela `edecan_local` (apps/local, WP-V3-05) con PyInstaller, y
# deja el binario resultante donde `tauri.conf.json` (`bundle.externalBin`)
# espera encontrarlo. Equivalente Windows: build-backend.ps1 (misma lógica,
# PowerShell). Lo llama `scripts/build-app.sh` antes de `cargo tauri build`;
# también se puede correr suelto para iterar solo sobre el backend.
#
# Requisitos: Node 22 + npm 10 (apps/web), Python 3.12 + uv (workspace del repo),
# Rust (`rustc` en PATH, solo para leer el target triple de esta máquina).
# Ver docs/desktop.md para el detalle de cada uno.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_DIR/../.." && pwd)"
WEB_DIR="$REPO_ROOT/apps/web"
PACKAGING_DIR="$DESKTOP_DIR/packaging"
WEB_DEST_DIR="$PACKAGING_DIR/web"
DIST_DIR="$PACKAGING_DIR/dist"
WORK_DIR="$PACKAGING_DIR/build"
BINARIES_DIR="$DESKTOP_DIR/src-tauri/binaries"

# Finder no carga el perfil interactivo del usuario. Si el `node` visible no
# es el soportado, intenta activar la instalación Node 22 de Homebrew antes de
# fallar. Así `Abrir Edecán.command` funciona por doble clic incluso cuando la
# terminal del usuario tiene otra versión de Node seleccionada.
node_toolchain_is_supported() {
  command -v node >/dev/null 2>&1 &&
    command -v npm >/dev/null 2>&1 &&
    [[ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null)" == "22" ]] &&
    [[ "$(npm --version 2>/dev/null | awk -F. '{print $1}')" == "10" ]]
}

if ! node_toolchain_is_supported && command -v brew >/dev/null 2>&1; then
  NODE22_PREFIX="$(brew --prefix node@22 2>/dev/null || true)"
  if [[ -n "$NODE22_PREFIX" && -x "$NODE22_PREFIX/bin/node" ]]; then
    export PATH="$NODE22_PREFIX/bin:$PATH"
  fi
fi

for bin in node npm uv rustc; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "error: falta '$bin' en el PATH. Ver requisitos en docs/desktop.md." >&2
    exit 1
  fi
done

if ! node_toolchain_is_supported; then
  echo "error: apps/web requiere Node 22 y npm 10 (detectados: Node $(node --version), npm $(npm --version))." >&2
  exit 1
fi

# Integración OPCIONAL: si EDECAN_BUNDLE_OLLAMA=1, descarga Ollama antes de
# armar el sidecar de edecan-local, dejando ollama-$TARGET_TRIPLE en
# src-tauri/binaries/ para que tauri.conf.json -> bundle.externalBin lo
# empaquete junto con el resto (ver docs/desktop.md, "Ollama embebido
# (opcional)"). Sin esta variable (el default), este script no cambia en
# nada respecto de antes.
if [[ "${EDECAN_BUNDLE_OLLAMA:-0}" == "1" ]]; then
  echo "==> [0/5] EDECAN_BUNDLE_OLLAMA=1: descargando Ollama (scripts/download-ollama.sh)…"
  "$SCRIPT_DIR/download-ollama.sh"
fi

# opencode es el MOTOR del IDE (docs/opencode-motor.md), no una integración
# opcional como Ollama: sin este binario dentro del paquete, el IDE de la app
# instalada no puede ejecutar ningún agente en ninguna máquina que no sea la
# de desarrollo. Por eso corre SIEMPRE y sin variable que lo active. El script
# sale de inmediato si el binario ya está y su SHA-256 coincide, así que no
# penaliza las recompilaciones.
echo "==> [0/5] Descargando el motor opencode (scripts/download-opencode.sh)…"
"$SCRIPT_DIR/download-opencode.sh"

echo "==> [1/5] Empaquetando FyDesign Studio (Node 22 + Chromium + npm ci)…"
"$SCRIPT_DIR/build-studio-engine.sh"

echo "==> [2/5] Construyendo la web estática (apps/web, export estático)…"
(
  cd "$WEB_DIR"
  if [[ ! -d node_modules ]]; then
    echo "    (node_modules no existe todavía, instalando dependencias declaradas…)"
    if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
  fi
  # NEXT_OUTPUT=export activa `output: "export"` en next.config.mjs (HTML/CSS/JS
  # estático en out/, sin servidor Next corriendo). NEXT_PUBLIC_API_URL vacío
  # (NO omitido — un vacío explícito) hace que el frontend llame a la API con
  # rutas relativas (same-origin): en la app de escritorio, el backend local
  # sirve la API y esta web estática desde el mismo origen
  # (http://127.0.0.1:<puerto>/), así que no hace falta ni conviene hardcodear
  # un host:puerto fijo acá. Ver docs/primeros-pasos.md §4 para el detalle
  # completo (incluyendo una advertencia ya documentada ahí sobre lib/api.ts).
  NEXT_OUTPUT=export NEXT_PUBLIC_API_URL='' npm run build
)

if [[ ! -d "$WEB_DIR/out" ]]; then
  echo "error: 'npm run build' no generó $WEB_DIR/out (¿next.config.mjs cambió?)." >&2
  exit 1
fi

echo "==> [3/5] Copiando apps/web/out/ -> packaging/web/…"
rm -rf "$WEB_DEST_DIR"
mkdir -p "$WEB_DEST_DIR"
cp -R "$WEB_DIR/out/." "$WEB_DEST_DIR/"

echo "==> [4/5] Congelando edecan_local con PyInstaller (uv run, workspace completo)…"
# PyInstaller vive fijado en el grupo `release` de la raíz y en `uv.lock`.
# `uv run --frozen --group release --all-packages` desde cualquier carpeta
# del workspace resuelve el entorno COMPARTIDO de todos los
# miembros (ver comentario largo al principio de packaging/edecan_local.spec)
# — necesario para que collect_all() encuentre los paquetes de
# `edecan.tools` (EDECAN_TOOL_PACKAGES en ese .spec, 16 a la fecha de v7)
# además de edecan_api/edecan_worker/edecan_db/edecan_core.
# `pgserver` es dependencia directa de `edecan-local`: el mismo lock que
# usa desarrollo alimenta también a PyInstaller, sin flags ocultos.
(
  cd "$DESKTOP_DIR"
  uv run --frozen --all-packages --group release pyinstaller packaging/edecan_local.spec \
    --noconfirm \
    --distpath "$DIST_DIR" \
    --workpath "$WORK_DIR"
)

# `packaging/edecan_local.spec` corre en modo onefile (corregido 2026-07-09,
# ver el docstring del propio .spec): PyInstaller deja UN SOLO ejecutable en
# $DIST_DIR/edecan-local, no una carpeta — el mecanismo de sidecar de Tauri
# (`bundle.externalBin`) solo sabe copiar un archivo por sidecar, así que
# esto es justo lo que necesita (antes, en modo onedir, `cargo build`/`cargo
# run` copiaban solo el ejecutable y dejaban ~90 archivos hermanos atrás —
# el sidecar reventaba con "Failed to load Python shared library" al
# arrancar; ver HOTFIXES_PENDIENTES.md).
FROZEN_FILE="$DIST_DIR/edecan-local"
if [[ ! -f "$FROZEN_FILE" ]]; then
  echo "error: no se encontró $FROZEN_FILE (¿falló pyinstaller arriba?)." >&2
  exit 1
fi

# `pgserver` carga estos módulos por nombre en runtime; no existe un import de
# Python ni un enlace ELF que permita a PyInstaller descubrir el uso. El spec
# los conserva como datos en su ruta `$libdir` exacta y esta inspección del
# onefile final evita gastar varios minutos construyendo AppImage/deb/rpm si
# una futura versión del wheel o de PyInstaller volviera a perderlos.
if [[ "$(uname -s)" == "Linux" ]]; then
  ARCHIVE_LIST="$(uv run --frozen --all-packages --group release pyi-archive_viewer -l "$FROZEN_FILE")"
  for required_entry in \
    'pgserver/pginstall/lib/postgresql/dict_snowball.so' \
    'pgserver/pginstall/lib/postgresql/vector.so'; do
    if ! grep -F -- "$required_entry" <<<"$ARCHIVE_LIST" >/dev/null; then
      echo "error: el sidecar congelado no contiene $required_entry." >&2
      exit 1
    fi
  done
fi

echo "==> [5/5] Instalando el sidecar en src-tauri/binaries/…"
TARGET_TRIPLE="$(rustc -Vv | awk '/^host:/ { print $2 }')"
if [[ -z "$TARGET_TRIPLE" ]]; then
  echo "error: no se pudo determinar el target triple ('rustc -Vv' no imprimió 'host:')." >&2
  exit 1
fi
SIDECAR_NAME="edecan-local-$TARGET_TRIPLE"

mkdir -p "$BINARIES_DIR"
# Limpia sidecars viejos de corridas anteriores (de este u otro target
# triple) antes de copiar — nunca dejar binarios stale mezclados con los
# nuevos.
find "$BINARIES_DIR" -maxdepth 1 -name 'edecan-local-*' -exec rm -rf {} +

cp "$FROZEN_FILE" "$BINARIES_DIR/$SIDECAR_NAME"
# `cp` preserva permisos en macOS/Linux, pero se refuerza el bit ejecutable
# igual — es el archivo exacto que Tauri va a intentar lanzar como sidecar,
# no puede depender de que la copia lo haya preservado bien.
chmod +x "$BINARIES_DIR/$SIDECAR_NAME"

# ---------------------------------------------------------------------------
# Firma de los binarios nativos de `studio-engine` (macOS)
#
# Tauri firma la app y los sidecars de `Contents/MacOS/`, pero NO toca lo que
# viaja dentro de `Contents/Resources/`. `studio-engine` arrastra binarios
# Mach-O de terceros --Chromium de Playwright, ffmpeg, yt-dlp, esbuild,
# fsevents-- que llegan sin firma, sin marca de tiempo y sin Hardened Runtime.
#
# La app se firma igual y arranca igual, así que el fallo es INVISIBLE hasta
# que se intenta notarizar: Apple devuelve `Invalid` con un error por cada
# binario (29 errores sobre 9 archivos, la primera vez que ocurrió). Firmarlos
# aquí, antes de que Tauri empaquete, es lo que hace la build notarizable.
#
# Las .dylib van primero: `chrome-headless-shell` enlaza contra ellas y hay que
# firmar la dependencia antes que a quien la usa.
# ---------------------------------------------------------------------------
if [[ "$(uname -s)" == "Darwin" ]]; then
  FIRMA_ID="${EDECAN_MACOS_CODESIGN_IDENTITY:-${APPLE_SIGNING_IDENTITY:-}}"
  if [[ -n "$FIRMA_ID" && "$FIRMA_ID" != "-" ]]; then
    echo "==> Firmando binarios nativos de studio-engine con '$FIRMA_ID'…"
    MACHOS="$(mktemp)"
    find "$PACKAGING_DIR/studio-engine" -type f \
      \( -perm -u+x -o -name '*.node' -o -name '*.dylib' -o -name '*.so' \) 2>/dev/null \
      | while read -r f; do
          file -b "$f" 2>/dev/null | grep -q 'Mach-O' && echo "$f"
        done > "$MACHOS"
    FIRMADOS=0
    for f in $(grep '\.dylib$' "$MACHOS" || true) $(grep -v '\.dylib$' "$MACHOS" || true); do
      codesign --force --timestamp --options runtime --sign "$FIRMA_ID" "$f"
      FIRMADOS=$((FIRMADOS + 1))
    done
    rm -f "$MACHOS"
    echo "    ($FIRMADOS binarios firmados con marca de tiempo y Hardened Runtime)"
  else
    echo "    (sin identidad de firma real: se omite la firma de studio-engine;" >&2
    echo "     esta build NO será notarizable)" >&2
  fi
fi

echo "==> Listo. Sidecar de un solo archivo: $BINARIES_DIR/$SIDECAR_NAME"
