#!/usr/bin/env bash
# apps/desktop/scripts/download-ollama.sh (macOS; Windows nativo: ver
# download-ollama.ps1)
#
# Descarga el binario oficial de Ollama (https://ollama.com) para un target
# triple y lo deja en src-tauri/binaries/ con la convención de sidecar de
# Tauri (`ollama-<target-triple>[.exe]`, ver tauri.conf.json ->
# bundle.externalBin) -- mismo lugar donde scripts/build-backend.sh instala
# el sidecar de `edecan-local`. Empaquetar Ollama es 100% OPCIONAL (ver
# docs/desktop.md, "Ollama embebido (opcional)"): sin correr este script, la
# app funciona exactamente igual, solo que sin ofrecer "usar Ollama con un
# clic" salvo que el cliente ya lo tenga instalado aparte.
#
# Adaptación propia (bring-your-own binary, cero llave/servicio compartido
# de la plataforma) del script equivalente de open-jarvis/OpenJarvis
# (Apache-2.0, frontend/src-tauri/scripts/download-ollama.sh) -- ver NOTICE
# para la atribución completa.
#
# Uso:
#   ./download-ollama.sh                      # autodetecta esta máquina
#   ./download-ollama.sh aarch64-apple-darwin
#   ./download-ollama.sh x86_64-apple-darwin
#   ./download-ollama.sh x86_64-pc-windows-msvc
#
# El flujo canónico no invoca este script a mano: usa
# `EDECAN_BUNDLE_OLLAMA=1 scripts/build-app.sh`, que además agrega el sidecar
# al bundle. Esta descarga aislada solo prepara los archivos.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# `cd ... && pwd` canonicaliza la ruta (sin "..") si la carpeta ya existe
# (builds anteriores); en el primerísimo uso todavía no existe, así que cae
# a la ruta literal -- `mkdir -p` de la línea siguiente la crea en cualquier
# caso (mismo patrón que el script de referencia de OpenJarvis, ver NOTICE).
BINARIES_DIR="$(cd "$SCRIPT_DIR/../src-tauri/binaries" 2>/dev/null && pwd || echo "$SCRIPT_DIR/../src-tauri/binaries")"
mkdir -p "$BINARIES_DIR"

SUPPORTED_TARGETS="aarch64-apple-darwin x86_64-apple-darwin x86_64-pc-windows-msvc"

# --- 1) Target triple -------------------------------------------------------
if [[ "${1:-}" != "" ]]; then
  TARGET="$1"
else
  ARCH="$(uname -m)"
  OS="$(uname -s)"
  case "$OS" in
    Darwin)
      case "$ARCH" in
        arm64) TARGET="aarch64-apple-darwin" ;;
        x86_64) TARGET="x86_64-apple-darwin" ;;
        *)
          echo "error: arquitectura de macOS no soportada: $ARCH" >&2
          exit 1
          ;;
      esac
      ;;
    MINGW*|MSYS*|CYGWIN*|Windows_NT)
      # bash de Git Bash/WSL sobre Windows -- en la práctica, para Windows
      # nativo conviene usar download-ollama.ps1 (no depende de tar/curl de
      # Git Bash), pero esto cubre igual el caso de correr este script ahí.
      TARGET="x86_64-pc-windows-msvc"
      ;;
    *)
      echo "error: sistema operativo no soportado por este script: $OS" >&2
      echo "       targets soportados: $SUPPORTED_TARGETS (ver docs/desktop.md)." >&2
      exit 1
      ;;
  esac
fi
echo "==> Target triple: $TARGET"

case " $SUPPORTED_TARGETS " in
  *" $TARGET "*) ;;
  *)
    echo "error: target no soportado: $TARGET" >&2
    echo "       soportados: $SUPPORTED_TARGETS (ver 'Uso' al principio de este script)." >&2
    exit 1
    ;;
esac

SUFFIX=""
case "$TARGET" in
  *windows*) SUFFIX=".exe" ;;
esac
OUT_FILE="$BINARIES_DIR/ollama-${TARGET}${SUFFIX}"
OLLAMA_LIB_DEST="$BINARIES_DIR/ollama-lib"

OLLAMA_VERSION="v0.32.1"
case "$TARGET" in
  aarch64-apple-darwin|x86_64-apple-darwin)
    EXPECTED_SHA256="346d28fe70f3ef3776e42100f5721510aa35fc07f3733f6629dbb117b1cfede9"
    ;;
  x86_64-pc-windows-msvc)
    EXPECTED_SHA256="d5abdc21b64ee928d3c92880ac22da5e5b0a46b8b07179791dd8c711b35f8397"
    ;;
esac

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print tolower($1)}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print tolower($1)}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | awk '{print tolower($NF)}'
  else
    echo "error: falta shasum, sha256sum u openssl para verificar el asset de Ollama." >&2
    return 1
  fi
}

# --- 2) Descargar + verificar + extraer el release oficial ------------------
# Versión y digests fijados desde la metadata oficial del release de GitHub.
# Nunca usamos `latest`: un release reproducible debe descargar siempre los
# mismos bytes y abortar antes de empaquetar si el SHA-256 no coincide.
# "ollama-darwin.tgz" (binario universal arm64+x86_64, mismo archivo para
# los dos targets de Apple) y "ollama-windows-amd64.zip". No hay build
# oficial de Ollama para Linux/Windows en ARM en este mapeo -- los 3 targets
# de arriba son los únicos soportados por este bundle opcional.
RELEASE_URL="https://github.com/ollama/ollama/releases/download/$OLLAMA_VERSION"
case "$TARGET" in
  aarch64-apple-darwin|x86_64-apple-darwin)
    ASSET_URL="$RELEASE_URL/ollama-darwin.tgz"
    ARCHIVE_TYPE="tgz"
    ;;
  x86_64-pc-windows-msvc)
    ASSET_URL="$RELEASE_URL/ollama-windows-amd64.zip"
    ARCHIVE_TYPE="zip"
    ;;
esac

if ! command -v curl >/dev/null 2>&1; then
  echo "error: falta 'curl' en el PATH." >&2
  exit 1
fi
if [[ "$ARCHIVE_TYPE" == "zip" ]] && ! command -v unzip >/dev/null 2>&1; then
  echo "error: falta 'unzip' en el PATH (necesario para extraer el .zip de Windows)." >&2
  exit 1
fi

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "==> Descargando: $ASSET_URL"
ARCHIVE_FILE="$TMPDIR/ollama-archive"
curl -fSL --progress-bar "$ASSET_URL" -o "$ARCHIVE_FILE"

echo "==> Verificando SHA-256 oficial de Ollama $OLLAMA_VERSION..."
ACTUAL_SHA256="$(sha256_file "$ARCHIVE_FILE")"
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "error: digest SHA-256 inválido para $ASSET_URL." >&2
  echo "       esperado: $EXPECTED_SHA256" >&2
  echo "       recibido: $ACTUAL_SHA256" >&2
  exit 1
fi

echo "==> Extrayendo..."
case "$ARCHIVE_TYPE" in
  tgz) tar xzf "$ARCHIVE_FILE" -C "$TMPDIR" ;;
  zip) unzip -q "$ARCHIVE_FILE" -d "$TMPDIR" ;;
esac

# Busca el binario `ollama` dentro de lo extraído -- la estructura interna
# del archivo (suelto en la raíz, o dentro de bin/) puede variar según el
# release, así que se prueban varias ubicaciones conocidas en vez de asumir
# una sola (mismo criterio defensivo que el script de referencia).
OLLAMA_BIN=""
for candidate in "$TMPDIR/ollama" "$TMPDIR/bin/ollama" "$TMPDIR/ollama.exe"; do
  if [[ -f "$candidate" ]]; then
    OLLAMA_BIN="$candidate"
    break
  fi
done

if [[ -z "$OLLAMA_BIN" ]]; then
  echo "error: no se encontró el binario 'ollama' dentro del archivo descargado." >&2
  echo "       Contenido de $TMPDIR:" >&2
  find "$TMPDIR" -type f | head -20 >&2
  exit 1
fi

# El ZIP oficial de Windows no es un ejecutable aislado: Ollama busca sus
# helpers y DLLs en ./lib/ollama relativo a ollama.exe. Perder ese árbol
# produce un bundle que contiene el CLI pero no el runtime nativo. Validamos
# dos archivos nucleares antes de tocar la salida existente.
if [[ "$TARGET" == "x86_64-pc-windows-msvc" ]]; then
  OLLAMA_LIB_SOURCE="$TMPDIR/lib/ollama"
  if [[ ! -f "$OLLAMA_LIB_SOURCE/ggml.dll" || ! -f "$OLLAMA_LIB_SOURCE/libllama.dll" ]]; then
    echo "error: el asset verificado no contiene el runtime esperado en lib/ollama." >&2
    exit 1
  fi
fi

# --- 3) Instalar con la convención de sidecar de Tauri ----------------------
cp "$OLLAMA_BIN" "$OUT_FILE"
chmod +x "$OUT_FILE"

if [[ "$TARGET" == "x86_64-pc-windows-msvc" ]]; then
  rm -rf "$OLLAMA_LIB_DEST"
  mkdir -p "$OLLAMA_LIB_DEST"
  cp -R "$OLLAMA_LIB_SOURCE/." "$OLLAMA_LIB_DEST/"
  echo "==> Runtime nativo de Windows: $OLLAMA_LIB_DEST"
fi

echo "==> Listo: $OUT_FILE"
ls -lh "$OUT_FILE"
